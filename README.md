# Freya "Build the Builder" entry

A builder that reads a bank's material and creates a Freya phone agent, with no manual step. It takes two kinds of input:

```
python builder/build.py <case_folder> <services_url>      a folder of bank documents and the address of the bank's services
python builder/build.py <brief_path> <tool_api_path>      one collections brief and its OpenAPI file
```

The command writes `submission/<slot>.workflow.json` (the workflow exported from the platform) and `traces/<slot>.log` (the trace of the run). `<slot>` is the folder or brief name unless `--slot` is given.

## Case folders

```
python builder/build.py <case_folder> <services_url> --slot final
```

```
case folder (Markdown, text, JSON, PDF, Word, Excel; a zip works too)
  -> reader    (code, builder/ingest.py)          -> every document as text, brief and readme first, long data files outlined
  -> designer  (model, skill service-line)        -> design: tools from the service contract in the folder, rules, instruction text, endings, decisions between sources
  -> checks    (code) + reviewer (model)          -> issues with evidence; at most 1 repair
  -> compiler  (code, builder/casebuild.py)       -> workflow JSON: one conversation step with the tools, one end step per ending
  -> Freya: dry run, save, publish, readback, one chat turn, export
```

- The tools call `<services_url>`: the host of that address plus the path the contract gives each service.
- `builder/runtime/service_runtime.py` is embedded in every tool hook. It reads the design as data: it builds the request body from the field paths, fills call variables and fixed values, accepts an id only when an earlier tool of the call returned it, refuses a change without the customer's yes or before the tool it depends on, refuses a repeat, and adds to every successful result what to do next and how each date in it lies to the day of the call.
- Nothing about a particular bank or case is in the code or in the skill. `tests/test_casebuild.py` drives the path with an invented parcel desk.

This path was added on 2026-10-11 after the final case opened, because the builder until then only handled collections briefs with an OpenAPI file and could not read the final's folder at all. It was written as a general input path; the commit that adds it contains no rule, value, name, announcement or field name of that case.

`mock/` holds the Cloudflare Worker that serves the final case's test customers (`python mock/make_data.py <case_folder>` writes its data, `node mock/check.mjs <case_folder> [url]` compares every answer with the test customers file, `npx wrangler deploy` in `mock/` publishes it). The mock belongs to the case, not to the builder.

## The final build

`submission/final3.workflow.json` is the platform export of the final case's phone agent and `traces/final3.log` is the trace of the builder run that made it; `traces/final3-run/` holds what the model returned in that run. The `source_sha256` on the trace's START line is a hash over the builder source, the skills and `kit/freya.py`. This prints it for a checkout:

```
python -c "import sys; sys.path.insert(0,'builder'); import state; print(state.source_hash())"
```

## Collections briefs

```
brief + tool API
  -> analyst   (model, skill collections-policy)  -> Policy IR: typed rules + cited rule inventory
  -> verifier  (model, skill verify-and-repair)   -> issues with evidence; at most 2 repairs
  -> developer (model, skill freya-workflow)      -> phone-agent prompt text
  -> verifier                                     -> issues; at most 1 repair
  -> compiler  (code)                             -> workflow JSON: fixed 6-step graph, tools, hooks
  -> validator (code)                             -> static checks, local run of the embedded code
  -> Freya: dry run, save, publish, readback, one chat turn, export
```

- The model never writes workflow JSON and never writes code. It extracts data and writes prose.
- A generic policy runtime (`builder/runtime/policy_runtime.py`) is embedded in the workflow's code steps and tool hooks. It reads the IR as data and does the work a small phone model should not do: business days, deadlines, floors, offers per persuasion attempt, answer normalisation, checks before every bank write, and the closing outcome whenever the state of the call decides it. Every tool result carries a `what_to_do_next` sentence for the agent.
- The three skills are in `skills/`. `builder/roles.py` loads one skill per role; the trace lists each skill's files and SHA-256.

Design and data contracts: `docs/IMPLEMENTATION_PLAN.md`. Skill sources: `docs/SKILL_RESEARCH.md`. What was measured: `docs/TEST_REPORT.md`.

## Setup

Requirements: Python 3.10 or newer, and the Claude Code CLI (`claude`) on `PATH`. Developed and run with Python 3.13 and Claude Code 2.1.289 on Windows. Collections briefs need the standard library only. A case folder with PDF, Word or Excel files also needs `pymupdf` (or `pypdf`), `python-docx` and `openpyxl` (`pip install pymupdf python-docx openpyxl`); a file that cannot be read is named in the trace and the build goes on without it.

1. Freya key. Create `.env` in the repository root (it is ignored by git):

   ```
   FREYA_API_KEY=fak_...
   FREYA_BANK_TEAM_KEY=any-secret-of-16-or-more-characters
   ```

   `FREYA_API_URL` defaults to `https://staging-app.freyavoice.ai`. `env.sh` in the kit's format and plain environment variables work too. The team key is only needed for the practice cases; if it is missing, one is generated and appended to `.env`.

2. Builder model. The builder calls `claude -p` and nothing else. Either log in once (`claude`, then `/login`) or set `ANTHROPIC_API_KEY`. The model is `claude-opus-5-5`; set `BUILDER_MODEL` to use another one. The phone agent's model is the one in `kit/freya.py` (`AGENT_LLM`) and is not changed.

3. Check the model access:

   ```
   python -c "import sys; sys.path.insert(0,'builder'); import llm; print(llm.call('Return ok=true.', {'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}, 120, 'access check'))"
   ```

## Running

Hidden or any single-file brief:

```
python builder/build.py briefs/hidden-1.md briefs/hidden-1.openapi.json
python builder/build.py briefs/hidden-2.md briefs/hidden-2.openapi.json
```

Each brief gets its own slot and its own workflow on Freya, updated in place on later runs. Workflow ids are remembered per workspace in `.freya-agents.<workspace>.json` (ignored by git); a fresh clone creates new workflows.

A brief that only lists changes to another brief in the same folder (it names that brief by its heading label or file name) is read together with it: the builder finds the base on its own and writes the fact to the trace. `--base-brief` states the base explicitly instead.

Public briefs (Brief B builds on Brief A):

```
python builder/build.py kit/briefs/brief-a.md kit/tools/openapi.json --slot public-a --cases kit/public-test-set.json --brief-tag A
python builder/build.py kit/briefs/brief-b.md kit/tools/openapi.json --base-brief kit/briefs/brief-a.md --slot public-b --cases kit/public-test-set.json --brief-tag B
```

Other options: `--offline` (stop after local validation, no Freya call), `--strict` (fail when verifier issues remain), `--budget-min N` (default 28; the event limit is 30), `--resume RUN_ID` (continue a stopped run inside its original deadline), `--reps`, `--style`, `--only` (passed to the kit's practice runner). `--reuse-policy RUN_ID` is a development aid that republishes the IR and prompt text of an earlier run without any model call; it is not used for a submission.

A run first checks that the Freya API answers and stops before any model call when it does not. During the platform steps it waits out short outages inside its time budget.

A model call that fails is repeated once; when a verification or repair call still fails, the run continues with the last checked output and says so in the trace. A workflow create request that gets no answer is sent again at most twice (no documented route lists workflows, so its outcome cannot be checked).

A run exits 0 when a workflow was exported. The trace ends with `END status=COMPLETE`, `COMPLETE_DEGRADED` (verifier issues remained after the repair budget and are listed) or `FAILED` with the reason.

## Tests

```
python -m unittest discover -s tests -v
```

Local tests need no key: the API parser, the calendar and amount logic, and the generated hook code driven against a fake bank for the thirteen public expectations and for invalid writes. They prove the code paths, not what the phone model says. Results of practice runs on Freya are in `docs/TEST_REPORT.md`.

## Layout

| Path | Content |
|---|---|
| `builder/build.py` | Controller and CLI |
| `builder/apispec.py`, `ir.py`, `roles.py`, `llm.py` | Tool API parser, schemas, model roles, model adapter |
| `builder/compiler.py`, `builder/runtime/policy_runtime.py` | Workflow compiler and the code that runs inside the workflow (collections briefs) |
| `builder/ingest.py`, `builder/casebuild.py`, `builder/runtime/service_runtime.py` | Folder reader, design schema, compiler and hook code for case folders |
| `mock/` | Cloudflare Worker with the final case's test customers, its data generator and its check |
| `builder/validate.py`, `builder/freya_platform.py`, `builder/state.py` | Checks and local engine, platform steps, run state and trace |
| `builder/platform_facts.json` | Platform behaviour measured by `tools/probe_platform.py` (results in `docs/PLATFORM_PROBES.md`) |
| `tools/` | Development aids: platform probe, holdout runner, call viewer, platform wait, practice runs against a slot (`run_practice.py`), scoring of a simulation whose runner was lost (`score_simulation.py`), practice cases over the chat route (`chat_cases.py`), manifest (`make_manifest.py`) |
| `builder/cases_runner.py` | Runs the kit's practice runner unchanged, with retries for platform and bank reads |
| `skills/` | The builder skills with their reference files: three for collections briefs, `service-line` for case folders |
| `kit/` | The organisers' starter kit v3 (`freya.py`, runner, scorer, public briefs). Files are unchanged; its `.claude/` folder is not tracked here, so `kit/examples/agent_builder.py`, which reads a skill from it, does not run from this repository |
| `tests/` | Local tests and holdout briefs for other banks |
| `submission/`, `traces/` | Exported workflows and run traces |
