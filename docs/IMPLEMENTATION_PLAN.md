# Implementation plan

Single current specification for the Freya "Build the Builder" entry. Written 2026-10-11 around 08:30 UTC, after the first Codex consultation (`docs/consultations/01-*`). Supersedes the architecture sections of `FREYA_WINNING_PLAN_EN.md`. Status lines say what is measured and what is not; this file is updated when measurements arrive.

## 1. System

```
brief.md (+ optional base brief) + bank.openapi.json
        |
        v
READ_INPUTS -> PARSE_API -> EXTRACT -> VERIFY_POLICY (<= 2 repairs) -> AUTHOR -> VERIFY_PROMPTS (<= 1 repair)
   -> COMPILE -> VALIDATE -> DRYRUN -> PUBLISH -> READBACK -> SMOKE -> [CASES] -> EXPORT -> COMPLETE
                                                                      any stage -> FAILED
```

| Component | File | Responsibility |
|---|---|---|
| Controller / CLI | `builder/build.py` | Stage machine, monotonic deadline, checkpoints, trace, exit code |
| API parser | `builder/apispec.py` | Deterministic parse of the supplied OpenAPI: per tool URL, request fields (type, enum, required, description), response fields |
| Model adapter | `builder/llm.py` | `claude -p --output-format json --json-schema` subprocess. Works with a Claude login or `ANTHROPIC_API_KEY`. No other SDK |
| Roles | `builder/roles.py` | Three LLM roles, each loading exactly one skill: analyst (`collections-policy`), developer (`freya-workflow`), verifier (`verify-and-repair`) |
| IR | `builder/ir.py` | JSON schemas for the model, deterministic IR validation (enum subsets, quote provenance, DSL well-formedness) |
| Policy runtime | `builder/runtime/policy_runtime.py` | Generic Python that runs INSIDE the Freya workflow (code nodes, pre/post hooks). Identical for every brief. Reads the IR as data |
| Compiler | `builder/compiler.py` | IR + API spec + runtime source -> workflow JSON. Deterministic |
| Validator | `builder/validate.py` | Static workflow checks and a local scenario run of the embedded runtime against a fake bank |
| Platform | `builder/platform.py` | Dry run, save, publish, readback diff, chat smoke, practice cases, export. Uses the kit's unmodified `freya.py` |
| State / trace | `builder/state.py` | Atomic `runs/<run-id>/state.json`, append-only trace, hashes |

The LLM never writes workflow JSON and never writes code. It produces data (typed IR) and prose (phone-agent prompt text). Code that runs locally and code that runs in the workflow is ours.

## 2. Why this shape

- Hidden briefs come with no practice customers, so a test-and-fix loop cannot correct structure during a hidden run. The node graph, the tool wiring and the hooks are therefore fixed and tested on the 13 public cases; a brief changes only data and prose.
- A typed IR alone cannot express unseen rule kinds; free prose alone cannot be checked. The IR is hybrid: typed where code computes or checks, cited prose where the phone model must behave.
- All routing in the workflow is on variables written by hooks. There is no `extraction_schema` and no `matches_intent`, so a turn makes one model call and the routing cannot drift.

## 3. Phone-agent workflow skeleton

| Node | Type | Purpose |
|---|---|---|
| `setup` | code, initial, passthrough | From `now` and the IR: calendar table, relative-weekday table, callback window, initial state. Writes `date_facts` and `today_facts`. Also runs on demand in the first hook, because the chat route skips an initial code node |
| `contact` | conversation | Fixed opening (`on_enter` tts). Right-party check, third party, callback, identity questions. Tools: `verify_identity`, `schedule_callback`, `transfer_to_agent`, `close_call`. The prompt does not contain the purpose of the call |
| `account` | api_request, passthrough | `get_account` as `pre_actions`, node-level `response_mapping`, results suppressed for the model |
| `plan` | code, passthrough | Per product: spoken label, band, deadline, floor, the dates that can be recorded, persuasion offers. Writes `account_facts` and state. Products are rebuilt from the flattened result keys, since a mapped list does not arrive |
| `negotiate` | conversation | Disclosure, negotiation, confirmation, recording, notes, transfer, closing. Tools: `create_promise`, `add_note` (only when the brief uses it), `transfer_to_agent`, `close_call` |
| `end` | end_call | `message_mode: none`. Reached when a `close_call` post-hook reports a successful close |

Decisions taken after consultation 01:

- `contact` and `identity` are one node. A split joined by an `is_tool_call` transition adds a model-triggered step that can be skipped (`docs/workflow-format.md:449`).
- One closing mechanism: the model-called `close_call` tool plus a variable transition to `end`. No lifecycle `close_call` unless a blocking pre-hook is proven live, because two mechanisms can produce two close attempts.
- Model-filled tool fields use `"default": "{{<field>}}"` exactly as the working builder does; runtime variables are prefixed `rt_` or are named facts, and the validator rejects a collision between a variable name and a tool field name.
- Notes are deferred to the end of the call when the brief forbids notes on a transfer, since a note written early cannot be undone.

### 3.1 What code enforces and what the prompt carries

Post-hooks (run before the model sees a result):

- remove headers, update state, and write one `what_to_do_next` sentence into `result["data"]` chosen by deterministic state from brief-derived guidance strings;
- write routing variables (`rt_identity`, `rt_closed`) both through `vars` and through a hook-injected field mapped by `response_mapping`.

Pre-hooks (run before the HTTP request):

- force `customer_id`, normalise arguments (dates to the wire format, digit answers with leading zeros kept, amounts to 2 decimals);
- `verify_identity`: reject incomplete or unreadable answers before they can count as wrong;
- `create_promise`: known product, not already promised, amount inside the brief's range, date not before today, a business day, not after that product's deadline, tool-failure retry limit;
- `schedule_callback`: allowed day, time window, later than now;
- `add_note`: allowed reason, no promise on that product, not after a transfer when the brief forbids it;
- `close_call`: outcome and reason corrected from state where state decides (transfer, recorded promise, callback, failed identity, note-derived reasons); otherwise the model's choice is checked against the brief's table; missing per-product notes block the close.

Blocking an invalid call is not documented in the kit, so it was measured (`docs/PLATFORM_PROBES.md`): an exception raised in a pre-hook stops the HTTP request, and the exception message is what the phone agent reads. `builder/platform_facts.json` therefore sets `block_mode: raise` before and after verification. A refused call raises with the reason and the next step as its message; the refusal is counted in the pre-hook because the post-hook does not run. The other modes (`redirect_read`, `blank_url`, `none`) remain in the runtime for a platform that behaves differently, and the post-hook still treats a call as stopped only when the result proves it.

Carried by prompts only (speech-only events that no hook observes): right-party confirmation, refusal counters, persuasion attempt counting, read-back and explicit confirmation, sympathy sentences, vague-date re-asks. Code supplies every number and date these steps need (per-attempt offers, deadlines, next business days), so the model does no arithmetic. This limit is stated, not hidden.

## 4. Data contracts

### 4.1 Policy IR (typed part)

`meta`, `call_variables`, `opening {first_line, forbidden_before_verification[]}`, `identity {questions[{field, ask, format: date|digits|year|text, length, normalize[], numeric_date_order, wire_format}], max_wrong_answers, hints[{value, instruction, counts_as_wrong}]}`, `calendar {weekend_days[], holidays[], week_start}`, `account {product_types[{type, label, floor: AmountExpr}]}`, `bands[{name, field, min, max, deadline: DateExpr, persuasion_attempts}]`, `persuasion[{attempt, bands[], amount: full|floor|full_or_floor_if_declined, date: DateExpr}]`, `promise {min_amount, max_amount, business_day_required, not_after_deadline, max_tool_attempts, success_sentence, confirmation: per_product|batch}`, `callback {used, allowed_days: DateExpr[], earliest_time, latest_time, after_now}`, `transfer {queues[{queue, close_outcome, close_reason}], notes_on_transfer: forbidden|not_required|required}`, `notes {used, reasons[], per_product_without_promise, timing}`, `closing {table[{outcome, reason}], precedence[{when, outcome, reason | reason_source}]}`, `tools {<tool>: {roles{role: field}, response{role: field}, success{field, equals}, extra_fields[]}}`.

- `DateExpr`: `{kind: today | business_days_after_today | calendar_days_after_today | last_business_day_of_week | end_of_month | fixed, n, week_offset, roll_if_today, adjust: none|next_business_day|previous_business_day, date}`.
- `AmountExpr`: `{kind: field | percent_of_field | fixed | full, field, percent, amount}`.
- Closing predicates: `transfer_done`, `promise_recorded`, `callback_scheduled`, `identity_failed`, `all_products_noted_with`, `any_note`. `reason_source`: `transfer_queue`, `first_note_not`.

### 4.2 Provenance, overrides, gaps

Every rule the analyst lists has an id, a verbatim quote, the section it came from and, when it replaces another rule, `overrides`. The deterministic validator checks that each quote occurs in the supplied brief text. A rule the typed part cannot express goes to `unmodeled_rules` with its quote; it is still carried in prose and it is printed in the trace. Missing or contradictory rules are recorded as `open_questions`; the builder never fills them from the public briefs or from general banking practice.

### 4.3 Prompt content (developer role)

`system_prompt`, `playbook {contact[], negotiate[]}` (short imperative rules, each with the rule ids it implements), `guidance {event: sentence}` for the fixed event vocabulary of the runtime. The verifier checks coverage (every rule id is enforced by the typed part or present in a playbook) and that pre-verification text does not name what the brief forbids before verification.

### 4.4 RunState and verifier issues

`RunState`: `run_id, slot, stage, started_utc, deadline_monotonic_budget_s, elapsed_s, input_hashes, source_hash, skill_hashes, model_calls[], repairs, ir_path, workflow_path, workflow_id, agent_id, published_version, export_sha256, tests[], status`. Written atomically after every stage. `--resume <run-id>` checks input, source and skill hashes and keeps the original deadline; a stage whose side effect is uncertain (publish) is re-read from the platform, not blindly repeated.

`Issue`: `{id, severity: critical|major|minor, target: ir|prompts, path, problem, evidence_quote, fix}`.

## 5. CLI and outputs

```
python builder/build.py <brief_path> <tool_api_path> [--base-brief PATH ...] [--slot NAME] [--out DIR]
                        [--cases FILE --brief-tag A|B [--reps N]] [--offline] [--resume RUN_ID] [--budget-min 28]
```

- Official form `python3 builder/build.py {brief_path} {tool_api_path}` works with no flags. Slot defaults to the brief file stem; each hidden brief gets its own slot and its own workflow, updated in place.
- Public Brief B is built as `brief-b.md` with `--base-brief brief-a.md`, or with no flag: a brief that names another brief of its folder as its base (heading label or file name) is read together with it, base first, and the trace says so. Hidden briefs are expected to be single files; the detection covers the case where one is not.
- Outputs: `submission/<slot>.workflow.json` (platform export), `traces/<slot>.log` (plain text), `runs/<run-id>/` (state, IR, prompts, issues, compiled workflow, readback, JSONL events).

## 6. Control flow, repair, deadline

- A preflight read of the Freya API runs before the first model call; when the platform does not answer, the run stops without spending model calls.
- Platform steps wait out transient failures (502, 503, 504) while more than 90 seconds of the budget are left. A step that may create a workflow is repeated only when the platform says it did not process the request.
- One monotonic deadline per run (default 28 of the 30 minutes). Every model call, HTTP call and simulation receives the remaining time as its timeout. With under 4 minutes left no new model round or test starts; export is always reserved.
- Repairs: at most 2 analyst rounds and 1 developer round. An unresolved structural error fails the run. An unresolved semantic issue after the repair budget is recorded as `degraded` in the trace and the run continues, because an imperfect agent scores more than none. `--strict` turns that into a failure.
- Model output cannot declare completion; only the controller sets `COMPLETE`, after export and hash recording.

## 7. Publish, readback, export

`freya.save_workflow(slot, workflow)` then, on first creation, one `freya.update_workflow` so the model correction and publish steps of the helper run. Readback compares, per node, the tools, hooks, mappings and transitions that were sent with what the platform stored, and fails on a silent drop. The trace records workflow id, agent id, published version number, node and tool counts, and the SHA-256 of the export file. The export is taken from the platform, never from the local compile.

## 8. Tests

| Layer | What | Proves | Does not prove |
|---|---|---|---|
| Unit (local) | API parser, date/amount DSL, IR validation | Deterministic parts | Platform behaviour |
| Runtime scenarios (local) | Hooks driven against a fake bank for the public expectations, plus invalid writes | Guards and state logic | That the phone model follows guidance |
| Holdouts (local) | Mutated API (renamed fields and enums), another calendar, clean and raw synthetic briefs through extract and compile | Parser, IR and compiler generalisation | Hidden score; no bank customers exist for these |
| Platform probes (live) | Hook blocking, hook variable persistence, product list shape, close ordering | Platform assumptions | Policy correctness |
| Practice cases (live) | 13 public cases, then `--reps 3`, then styles | Ledger, policy, honesty on public rules | Other calendars and APIs |

Success criterion for the freeze: public cases measured at 3 of 3 where time allows, with failures listed by cause. Nothing is reported as passing that was not run.

## 9. Changes from the earlier proposal

- IR, skeleton and guard contract are now concrete (they were described, not specified).
- Three LLM roles map one to one onto the three skills; the compiler stays deterministic.
- No separate framework, no API SDK: the Claude CLI is the only model dependency.
- Unit Tests on the platform, the raw HTTPS adapter and style matrices are cut first.

## 10. Consultation 01 dispositions

| Recommendation | Disposition | Reason |
|---|---|---|
| Probe hook blocking before relying on it | Accepted | Undocumented (`docs/tools-and-code.md:414`) |
| Fallback: validate before an engine action node | Adapted | Kept as `redirect_read` first because it needs no extraction; engine-run writes need extraction with its own silent failures |
| Combine contact and identity | Accepted | Removes a model-triggered transition |
| One closing mechanism | Accepted | Two can produce two close attempts |
| Buffer notes until transfer is ruled out | Accepted | Brief B rule 10 forbids notes before and after a transfer |
| Typed contact and refusal counters, offer steps | Adapted | Offer steps are typed and computed; counters stay in prompts because no hook observes speech |
| Unsupported critical rules are build errors | Adapted | Recorded as `degraded`; failing the hidden run scores zero |
| Cut raw HTTPS adapter, checklist parsing, elaborate resume | Accepted | Time |
| Every conversation node needs a closing route | Accepted | Both conversation nodes route to `end` |
| `create_workflow` does not correct the model | Accepted | Update once after create |

### Consultation 02 (final design critique)

| Recommendation | Disposition | Reason |
|---|---|---|
| `redirect_read` must never run before verification | Accepted | A read of the account before verification fails the scorer; covered by a test |
| Product ids in the account facts | Accepted | The agent needs them as write arguments |
| `max_tool_attempts` per product, first try included | Accepted | Removes the ambiguity |
| Typed speech-event state through extraction | Rejected | Extraction discards low-confidence values silently and variables go stale; revisit only on measured failures |
| Rerun bootstrap contract | Accepted | README setup; state file named by workspace |
| Exercise the exact emitted snippets, scan them for braces | Accepted | Local engine runs the generated code; brace scan on every snippet |

### Consultation 03 (review of the implementation)

| Finding | Disposition | Change |
|---|---|---|
| With `block_mode: none` every rejection is unenforced and guidance can be false | Adapted | Default is `redirect_read`; the post-hook treats a call as stopped only when the result proves it, otherwise it reports what happened |
| A promise could follow a note on the same product | Accepted | Refused in `judge()` with a test |
| Notes written before a later transfer | Adapted | Cannot be undone by code; notes are held to the end by instruction and refused after a transfer |
| Unrelated refusals disabled the close guard | Accepted | Close-specific counter (also found by the holdout) |
| Deadline not enforced on platform steps and first model calls | Accepted | `_bounded` worker for every platform step, time check before every model call |
| Malformed state or `now` silently removes guidance | Accepted | Tolerant state and date parsing; every snippet has a fallback that still writes guidance or fact blocks |
| Missing arguments could store a promise under a made-up key | Accepted | Unattributable successes are counted, not keyed |
| Brief A cases tested with the A+B policy and edited ledgers | Accepted | Separate policies, untouched ledgers |
| Tests assumed one blocking mechanism | Accepted | Cases run in both modes, plus an engine that ignores pre-hook rewrites |
| Hidden API shapes (operation servers, object fields, missing response schema) | Adapted | Operation-level servers, object and array types kept, missing response schema is a warning. GET parameters are not supported; the event keeps the seven POST tools |
| Trace lacks failed model calls and early ids; slot log overwritten | Accepted | `CALLING` and `MODELERR` lines, id logged at creation, a copy of the trace in every run directory |
| No secret redaction | Accepted | Key values and key-shaped strings are removed from traces, events, state and artifacts |
| Real clock when `now` is missing, non-finite amounts | Adapted | Non-finite and non-positive amounts refused. The clock fallback stays, since stopping the call is worse |
| Skill text against schema (`ymd`, unset deadline) | Accepted | Year-first parsing, date kind `none` |
| Sandbox builtins and function-local imports | Open | Added to the probe |

## 11. Schedule (Europe/Istanbul, 2026-10-11)

| Window | Work |
|---|---|
| 11:30 to 13:30 | Parser, runtime, compiler, validator, local scenarios; skills and roles |
| 13:30 to 15:00 | First live publish, platform probes, public A cases, fixes |
| 15:00 to 17:00 | Public B, repetitions, risk styles, holdouts |
| 17:00 to 19:00 | Fresh-run reproducibility, README, traces, review, freeze commit |
| 20:00 to 21:30 | Hidden runs, one per brief, same commit |
| 21:30 to 22:30 | Artifact check, manifest, submission by the user |

Live windows depend on the Freya key being available.
