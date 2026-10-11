"""Build a Freya phone agent from a bank brief and that bank's tool API. One command, no manual step.

    python builder/build.py <case_folder> <services_url>      a folder of bank documents and the address of the bank's services (see casebuild.py)
    python builder/build.py <brief_path> <tool_api_path>     one collections brief and its OpenAPI file
        [--base-brief PATH ...]     briefs the main brief builds on, in order (public Brief B: --base-brief briefs/brief-a.md)
        [--slot NAME]               workflow slot, default: the brief file name. One slot is one workflow, updated in place
        [--out DIR]                 where the exported workflow goes, default: submission/
        [--cases FILE --brief-tag X [--reps N] [--style S] [--only IDS]]   run practice cases after publishing (public briefs only)
        [--offline]                 stop after compile and local validation; touch nothing on Freya
        [--strict]                  fail when verifier issues remain after the repair budget
        [--budget-min 28]           time budget of the run in minutes (the event limit is 30)
        [--resume RUN_ID]           continue a stopped run with its original deadline
        [--reuse-policy RUN_ID]     development only: take the IR and prompt text of an earlier run and make no model call

Needs FREYA_API_KEY (environment, .env or env.sh) and the `claude` CLI with a login or ANTHROPIC_API_KEY.
Writes submission/<slot>.workflow.json (platform export) and traces/<slot>.log (the trace of the run).
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys
import threading
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import apispec
import compiler
import freya_platform as fp
import ir as irmod
import llm
import roles
import state as st
import validate

MAX_IR_REPAIRS = 2
MAX_PROMPT_REPAIRS = 1
MAX_CREATE_ATTEMPTS = 3     # an unanswered create is sent again at most twice
EXPORT_RESERVE_S = 120      # always kept for publish checks and export
MODEL_ROUND_S = 240         # a model round is not started with less than this (plus the reserve) left


class BuildError(RuntimeError):
    pass


def _heading_label(text):
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].split(":")[0].strip()
    return ""


def referenced_briefs(brief_path, _seen=None):
    """Briefs in the same folder that this brief says it builds on, base first.

    The official command passes one brief. A brief that only lists changes names its base by heading label or
    file name; two briefs that name each other are not treated as base and change."""
    seen = _seen or {brief_path}
    text = brief_path.read_text(encoding="utf-8", errors="replace")
    own = _heading_label(text)
    found = []
    for other in sorted(brief_path.parent.glob("*.md")):
        other = other.resolve()
        if other in seen or other.stem.lower() == "readme":
            continue
        other_text = other.read_text(encoding="utf-8", errors="replace")
        label = _heading_label(other_text)
        names = [n for n in (label if len(label.split()) >= 2 else "", other.name) if n and n != own]
        mentioned = any(re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w.-])", text) for n in names)
        if mentioned and not (own and own in other_text) and brief_path.name not in other_text:
            found.append(other)
    ordered = []
    for base in found:
        for p in referenced_briefs(base, seen | set(found)) + [base]:
            if p not in ordered:
                ordered.append(p)
    return ordered


def _issue(severity, target, path, problem, fix=""):
    return {"id": "det-" + hashlib.sha1((path + problem).encode()).hexdigest()[:6], "severity": severity, "target": target, "path": path,
            "problem": problem, "evidence_quote": "", "fix": fix or "Correct this so that it matches the tool API and the brief."}


def _role(run, fn, what, *args, **kwargs):
    """Call a model role. The attempt is traced before it starts, so a failed or timed-out call is still on record."""
    if not run.deadline.allow(60, EXPORT_RESERVE_S):
        raise BuildError(f"no time left in the run budget for the model call: {what}")
    for attempt in (1, 2):
        run.log("CALLING", f"model={llm.model_name()} role={fn.__name__[4:]} purpose=\"{what}\" timeout_s={int(_model_timeout(run))}")
        try:
            data, info = fn(*args, _model_timeout(run), **kwargs)
            break
        except Exception as e:
            run.log("MODELERR", f"role={fn.__name__[4:]} purpose=\"{what}\" failed: {type(e).__name__}: {str(e)[:300]}")
            if attempt == 2 or not _can_start_model_round(run):
                raise
            run.log("RETRY", f"model call repeated once: {what}")
    run.model_call(info)
    return data


def _optional_role(run, fn, what, *args, **kwargs):
    """A model call the build can finish without (verification, repair). Returns None when it fails."""
    try:
        return _role(run, fn, what, *args, **kwargs)
    except BuildError:
        raise
    except Exception:
        run.log("WARN", f"continuing without: {what}")
        return None


def _bounded(run, what, fn, *args, safe_to_repeat=True, max_tries=None, **kwargs):
    """Run a platform step in a worker thread, wait out transient platform failures, and give up when the run deadline passes.

    safe_to_repeat=False is for the step that may create a workflow: it is repeated only when the platform says it
    did not process the request. max_tries is for a step the run can do without: it must not use up the deadline."""
    box = {}

    def note(attempt, error):
        run.log("RETRY", f"{what}: transient platform failure, attempt {attempt}: {str(error)[:160]}")

    def work():
        try:
            limit = {"tries": max_tries} if max_tries else {"remaining": run.deadline.remaining}
            box["value"] = fp.retrying(fn, *args, only_rejected=not safe_to_repeat, on_retry=note, **limit, **kwargs)
        except BaseException as e:
            box["error"] = e

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(max(5.0, run.deadline.remaining()))
    if thread.is_alive():
        raise BuildError(f"the run deadline passed during: {what}")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _model_timeout(run):
    return max(60, min(600, run.deadline.remaining() - EXPORT_RESERVE_S))


def _can_start_model_round(run):
    return run.deadline.allow(MODEL_ROUND_S, EXPORT_RESERVE_S)


def stage_policy(run, briefs, spec, raw_api, brief_text, strict):
    """EXTRACT and VERIFY_POLICY with a bounded repair loop. Returns the IR."""
    run.stage("EXTRACT")
    ir = _role(run, roles.run_analyst, "extract the Policy IR from the brief and tool API", briefs, spec, raw_api)
    earlier = None
    for round_no in range(1, MAX_IR_REPAIRS + 2):
        run.artifact(f"ir.round{round_no}.json", ir)
        run.stage("VERIFY_POLICY")
        errors, warnings = validate.check_ir(ir, spec, brief_text)
        issues = [_issue("critical", "ir", e.split(":")[0], e) for e in errors]
        for w in warnings:
            run.log("WARN", "IR check: " + w)
        verdict = "skipped"
        if _can_start_model_round(run):
            report = _optional_role(run, roles.run_verifier, f"verify the Policy IR against the original inputs, round {round_no}", briefs, spec, raw_api, ir, round_no=round_no, earlier=earlier) or {"verdict": "not verified (model call failed)"}
            verdict = report.get("verdict")
            issues += [i for i in report.get("issues") or [] if i.get("target", "ir") == "ir"]
            run.log("VERIFY", f"policy round {round_no}: verdict={verdict}, issues={len(report.get('issues') or [])}; checked: " + "; ".join(report.get("checked") or [])[:400])
        else:
            run.log("VERIFY", f"policy round {round_no}: model verification skipped, not enough time left")
        run.artifact(f"issues.ir.round{round_no}.json", issues)
        for i in issues:
            run.log("ISSUE", f"[{i.get('severity')}] {i.get('path')}: {i.get('problem')} | evidence: {str(i.get('evidence_quote'))[:160]} | fix: {str(i.get('fix'))[:200]}")
        blocking = irmod.blocking_issues(issues)
        if not blocking:
            break
        if round_no > MAX_IR_REPAIRS or not _can_start_model_round(run):
            if errors:
                raise BuildError("the Policy IR still fails deterministic checks after the repair budget: " + "; ".join(errors[:5]))
            run.state["degraded"].append({"stage": "VERIFY_POLICY", "unresolved": blocking})
            run.log("DEGRADED", f"{len(blocking)} verifier issue(s) on the IR remain after the repair budget; continuing with the last IR")
            if strict:
                raise BuildError("--strict: unresolved verifier issues on the Policy IR")
            break
        run.state["repairs"]["ir"] += 1
        run.stage("REPAIR_POLICY")
        run.log("REPAIR", f"IR repair {run.state['repairs']['ir']} of {MAX_IR_REPAIRS}: {len(blocking)} issue(s) sent back to the analyst")
        repaired = _optional_role(run, roles.run_analyst, "repair the Policy IR", briefs, spec, raw_api, previous=ir, issues=blocking)
        if repaired is None:
            if errors:
                raise BuildError("the Policy IR fails deterministic checks and the repair call failed: " + "; ".join(errors[:5]))
            run.state["degraded"].append({"stage": "REPAIR_POLICY", "unresolved": blocking})
            run.log("DEGRADED", f"the IR repair call failed; continuing with the last IR and {len(blocking)} unresolved verifier issue(s)")
            break
        ir = repaired
        earlier = blocking
    for rule in ir.get("unmodeled_rules") or []:
        run.log("UNMODEL", f"rule {rule.get('id')} is carried by prompt text only: {rule.get('reason')}")
    for q in ir.get("open_questions") or []:
        run.log("QUESTION", q)
    run.artifact("ir.json", ir)
    return ir


def _prompt_lint(prompts, ir):
    issues = []
    text_fields = [("system_rules", prompts.get("system_rules") or [])] + \
                  [(k, [i.get("text", "") for i in prompts.get(k) or []]) for k in ("contact_rules", "negotiate_rules")] + \
                  [("guidance." + k, [v]) for k, v in (prompts.get("guidance") or {}).items()] + \
                  [("tool_descriptions." + k, [v]) for k, v in (prompts.get("tool_descriptions") or {}).items()]
    for where, texts in text_fields:
        for n, text in enumerate(texts):
            if "{{" in str(text) or "}}" in str(text):
                issues.append(_issue("critical", "prompts", f"{where}[{n}]", "contains double curly braces", "Remove the double curly braces."))
    forbidden = [w for w in (ir.get("opening") or {}).get("forbidden_before_verification") or [] if len(w) > 2]
    early = [("system_rules", prompts.get("system_rules") or []), ("contact_rules", [i.get("text", "") for i in prompts.get("contact_rules") or []])]
    early += [("guidance." + k, [v]) for k, v in (prompts.get("guidance") or {}).items() if k.startswith(("id_", "callback_"))]
    for where, texts in early:
        for n, text in enumerate(texts):
            for word in forbidden:
                if re.search(r"\b" + re.escape(word.lower()) + r"\b", str(text).lower()):
                    issues.append(_issue("major", "prompts", f"{where}[{n}]", f"uses the word '{word}', which the brief forbids before verification",
                                         "Reword the item so that the word does not appear, for example by saying what the agent may say instead."))
    for key in ("contact_rules", "negotiate_rules"):
        if not prompts.get(key):
            issues.append(_issue("critical", "prompts", key, "is empty", "Write the rules for this step."))
    covered = {r for k in ("contact_rules", "negotiate_rules") for i in prompts.get(k) or [] for r in i.get("rules") or []}
    missing = [r["id"] for r in ir.get("rules") or [] if r.get("enforced_by") in ("prompt", "both") and r.get("kind") != "definition" and r["id"] not in covered]
    if missing:
        issues.append(_issue("major", "prompts", "contact_rules/negotiate_rules", "these rule ids are not carried by any item: " + ", ".join(missing),
                             "Add an item for each of these rules in the step given by its stage, or add the id to the item that already carries it."))
    return issues


def stage_prompts(run, briefs, spec, ir, strict):
    """AUTHOR and VERIFY_PROMPTS with one bounded repair. Returns the prompt text."""
    run.stage("AUTHOR")
    prompts = _role(run, roles.run_developer, "write the phone-agent prompt text", briefs, spec, ir)
    earlier = None
    for round_no in range(1, MAX_PROMPT_REPAIRS + 2):
        run.artifact(f"prompts.round{round_no}.json", prompts)
        run.stage("VERIFY_PROMPTS")
        issues = _prompt_lint(prompts, ir)
        if _can_start_model_round(run):
            report = _optional_role(run, roles.run_verifier, f"verify the prompt text against the original inputs, round {round_no}", briefs, spec, None, ir, prompts=prompts, round_no=round_no, earlier=earlier) or {"verdict": "not verified (model call failed)"}
            issues += [dict(i, target="prompts") for i in report.get("issues") or []]
            run.log("VERIFY", f"prompts round {round_no}: verdict={report.get('verdict')}, issues={len(report.get('issues') or [])}; checked: " + "; ".join(report.get("checked") or [])[:400])
        else:
            run.log("VERIFY", f"prompts round {round_no}: model verification skipped, not enough time left")
        run.artifact(f"issues.prompts.round{round_no}.json", issues)
        for i in issues:
            run.log("ISSUE", f"[{i.get('severity')}] {i.get('path')}: {i.get('problem')} | fix: {str(i.get('fix'))[:200]}")
        blocking = irmod.blocking_issues(issues)
        if not blocking:
            break
        if round_no > MAX_PROMPT_REPAIRS or not _can_start_model_round(run):
            run.state["degraded"].append({"stage": "VERIFY_PROMPTS", "unresolved": blocking})
            run.log("DEGRADED", f"{len(blocking)} issue(s) on the prompt text remain after the repair budget; continuing with the last text")
            if strict:
                raise BuildError("--strict: unresolved verifier issues on the prompt text")
            break
        run.state["repairs"]["prompts"] += 1
        run.stage("REPAIR_PROMPTS")
        run.log("REPAIR", f"prompt repair {run.state['repairs']['prompts']} of {MAX_PROMPT_REPAIRS}: {len(blocking)} issue(s) sent back to the developer")
        repaired = _optional_role(run, roles.run_developer, "repair the phone-agent prompt text", briefs, spec, ir, previous=prompts, issues=blocking)
        if repaired is None:
            run.state["degraded"].append({"stage": "REPAIR_PROMPTS", "unresolved": blocking})
            run.log("DEGRADED", f"the prompt repair call failed; continuing with the last text and {len(blocking)} unresolved issue(s)")
            break
        prompts = repaired
        earlier = blocking
    run.artifact("prompts.json", prompts)
    return prompts


def local_scenario(workflow, ir, spec):
    """Run the generated snippets once against a fake bank built from the same IR: setup, a full identity pass, plan.

    It uses neutral invented customer data. It shows that the embedded code runs and produces its facts for this brief."""
    answers, digits = {}, "0123456789"
    for q in (ir.get("identity") or {}).get("questions") or []:
        fmt = q.get("format")
        if fmt == "date":
            answers[q["field"]] = "1980-01-02" if (q.get("wire_format") or "YYYY-MM-DD") == "YYYY-MM-DD" else None
        elif fmt in ("digits", "year"):
            n = int(q.get("length") or 4)
            answers[q["field"]] = "1980" if fmt == "year" else (digits * 3)[1:1 + n]
        else:
            # digits survive every normalisation the runtime can apply (case, spaces, digits only), so the fake
            # bank and the runtime agree on the value whatever the brief asks for
            answers[q["field"]] = "4711"
    if any(v is None for v in answers.values()):
        return ["local scenario skipped: the identity date format is not ISO"]
    acc = ir["tools"]["get_account"]["response"]
    plist = next((f for f in spec["tools"]["get_account"]["response"] if f["name"] == acc.get("products")), {})
    product = {}
    for f in plist.get("items") or []:
        if f["name"] == acc.get("id"):
            continue
        if f.get("enum"):
            product[f["name"]] = f["enum"][0]
        elif f["type"] in ("number", "integer"):
            product[f["name"]] = 30 if f["name"] == acc.get("late") else 100.0
        else:
            product[f["name"]] = "1234"
    band_fields = {b.get("field") for b in ir.get("bands") or [] if b.get("field")}
    for b in (ir.get("bands") or [])[:1]:
        if b.get("field"):
            product[b["field"]] = b.get("min") if b.get("min") is not None else 1
    cv = ir.get("call_variables") or {}
    variables = {cv.get("customer_id") or "customer_id": "c_local", cv.get("now") or "now": "2030-01-08T10:00"}
    bank = validate.FakeBank(ir, spec, answers, [product])
    eng = validate.Engine(workflow, bank, variables)
    notes = []
    if "CALENDAR" not in str(eng.vars.get("date_facts")):
        raise BuildError("local scenario: the setup snippet did not produce the calendar facts")
    for q in (ir.get("identity") or {}).get("questions") or []:
        eng.call("verify_identity", **{ir["tools"]["verify_identity"]["roles"]["field"]: q["field"], ir["tools"]["verify_identity"]["roles"]["answer"]: answers[q["field"]]})
    if eng.node != "negotiate":
        raise BuildError(f"local scenario: a full identity pass did not reach the negotiate step (stopped in {eng.node})")
    products = eng.state().get("products") or []
    if not products:
        raise BuildError("local scenario: the plan snippet built no product from a get_account response")
    p = products[0]
    notes.append(f"local scenario ok: identity pass -> negotiate; product facts built (band={p.get('band') or 'none'}, deadline={p.get('deadline') or 'none'}, floor={p.get('floor')}, offers={len(p.get('offers') or [])})")
    if band_fields and not p.get("band"):
        notes.append("local scenario warning: the sample product matched no band")
    return notes


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2 and re.match(r"https?://", args[1]) and (pathlib.Path(args[0]).is_dir() or args[0].lower().endswith(".zip")):
        # A case folder with the address of the bank's services, in place of one brief with an OpenAPI file.
        import casebuild
        return casebuild.main(args)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brief")
    ap.add_argument("tool_api")
    ap.add_argument("--base-brief", action="append", default=[])
    ap.add_argument("--slot")
    ap.add_argument("--out", default=str(ROOT / "submission"))
    ap.add_argument("--cases")
    ap.add_argument("--brief-tag")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--style")
    ap.add_argument("--only")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--budget-min", type=float, default=28.0)
    ap.add_argument("--resume")
    ap.add_argument("--reuse-policy")
    a = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):     # a console code page must not be able to stop a run
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    fp.load_env()
    brief_path, api_path = pathlib.Path(a.brief).resolve(), pathlib.Path(a.tool_api).resolve()
    slot = a.slot or brief_path.stem
    trace_path = ROOT / "traces" / f"{slot}.log"
    if a.resume:
        old = st.read_json(ROOT / "runs" / a.resume / "state.json")
        run = st.Run(a.resume, slot, st.Deadline(old["budget_s"], old["started_epoch"]), trace_path, resumed=True)
        run.state.update({k: old[k] for k in ("model_calls", "repairs", "tests", "degraded") if k in old})
    else:
        run_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + slot
        run = st.Run(run_id, slot, st.Deadline(a.budget_min * 60), trace_path)
    exit_code = 1
    try:
        run.stage("READ_INPUTS")
        found_bases = [] if a.base_brief else referenced_briefs(brief_path)
        paths = [pathlib.Path(p).resolve() for p in a.base_brief] + found_bases + [brief_path]
        briefs = [{"name": p.name, "text": p.read_text(encoding="utf-8")} for p in paths]
        raw_api = api_path.read_text(encoding="utf-8")
        inputs = {"briefs": {p.name: st.sha256_file(p) for p in paths}, "tool_api": {api_path.name: st.sha256_file(api_path)}}
        hashes = {"inputs": inputs, "source": st.source_hash(), "skills": {s["name"]: s["sha256"] for s in roles.skill_manifest()}}
        if a.resume and {k: old.get(k) for k in ("inputs", "source", "skills")} != hashes:
            raise BuildError("resume refused: the inputs, the builder source or the skills changed since the run started")
        run.state.update(hashes, git_commit=st.git_commit(), builder_model=llm.model_name(), command=" ".join(sys.argv))
        run.log("START", f"run={run.state['run_id']} slot={slot} budget_s={int(run.deadline.budget_s)} builder_model={llm.model_name()} "
                         f"source_commit={run.state['git_commit']} source_sha256={hashes['source'][:16]}" + (" (resumed, original deadline kept)" if a.resume else ""))
        for name, h in list(inputs["briefs"].items()) + list(inputs["tool_api"].items()):
            run.log("INPUT", f"{name} sha256={h}")
        if found_bases:
            run.log("INPUT", "the brief names another brief in its folder as its base; read first: " + ", ".join(p.name for p in found_bases))
        for s in roles.skill_manifest():
            run.log("SKILL", f"role={s['role']} skill={s['name']} sha256={s['sha256'][:16]} files={','.join(s['files'])}")

        run.stage("PARSE_API")
        spec = apispec.parse(json.loads(raw_api))
        run.log("API", f"server={spec['server']} tools=" + ", ".join(f"{t}({len(spec['tools'][t]['request'])} fields)" for t in apispec.TOOL_NAMES))
        brief_text = "\n\n".join(b["text"] for b in briefs)

        if not a.offline:
            # Before any model call: a run that cannot publish should not spend the model budget.
            run.stage("PREFLIGHT")
            if not fp.has_key():
                raise BuildError("FREYA_API_KEY is not set; use --offline to build without publishing")
            try:
                fp.retrying(fp.health, tries=3, wait_s=10)
            except Exception as e:
                raise BuildError(f"the Freya API does not answer (platform outage or invalid key), no model call was made: {str(e)[:200]}") from None
            run.log("FREYA", "preflight: the API answers and the key is accepted")

        cached = lambda name: (run.dir / name) if a.resume and (run.dir / name).exists() else None
        if a.reuse_policy:
            src = ROOT / "runs" / a.reuse_policy
            old_inputs = st.read_json(src / "state.json").get("inputs")
            if old_inputs != inputs:
                raise BuildError("--reuse-policy refused: that run was made from other input files")
            run.log("REUSE", f"development run: IR and prompt text taken from run {a.reuse_policy}; no model call is made in this run")
            run.artifact("ir.json", st.read_json(src / "ir.json"))
            run.artifact("prompts.json", st.read_json(src / "prompts.json"))
            cached = lambda name: run.dir / name
        ir = st.read_json(cached("ir.json")) if cached("ir.json") else stage_policy(run, briefs, spec, raw_api, brief_text, a.strict)
        prompts = st.read_json(cached("prompts.json")) if cached("prompts.json") else stage_prompts(run, briefs, spec, ir, a.strict)

        run.stage("COMPILE")
        facts = fp.facts()
        options = {"block_mode": facts.get("block_mode", "none"), "pre_verification_block": facts.get("pre_verification_block", "none")}
        workflow = compiler.compile_workflow(ir, prompts, spec, options)
        stats = compiler.stats(workflow)
        run.artifact("workflow.compiled.json", workflow)
        run.log("COMPILE", f"nodes={stats['nodes']} tools={stats['tools']} bytes={stats['bytes']} guard_block_mode={options['block_mode']} "
                           f"pre_verification_block={options['pre_verification_block']} compiler_version={compiler.COMPILER_VERSION}")

        run.stage("VALIDATE")
        errors, warnings = validate.check_workflow(workflow, ir, spec)
        for w in warnings:
            run.log("WARN", "workflow check: " + w)
        if errors:
            raise BuildError("the compiled workflow fails static checks: " + "; ".join(errors[:8]))
        try:
            for note in local_scenario(workflow, ir, spec):
                run.log("TEST", note)
            run.state["tests"].append({"name": "static checks and local scenario", "result": "pass"})
        except Exception as e:
            # The scenario is the builder's own rehearsal with invented data. When it fails, the cause can be the
            # rehearsal as well as the workflow, and a build without an agent is the worse outcome.
            run.log("WARN", f"local scenario did not pass, continuing: {type(e).__name__}: {str(e)[:300]}")
            run.state["tests"].append({"name": "static checks and local scenario", "result": "scenario failed", "error": str(e)[:300]})
            run.state["degraded"].append({"stage": "VALIDATE", "unresolved": [{"problem": "local scenario failed: " + str(e)[:300]}]})

        if a.offline:
            run.log("OFFLINE", "stopping before any Freya call, as requested")
            run.state["status"] = "OFFLINE_COMPLETE"
            exit_code = 0
            return exit_code
        if not fp.has_key():
            raise BuildError("FREYA_API_KEY is not set; the workflow was compiled and validated locally but not published")

        run.stage("DRYRUN")
        _bounded(run, "dry run", fp.dry_run, slot, workflow)
        run.log("FREYA", "dry run accepted the workflow")

        run.stage("PUBLISH")
        new_slot = not fp.slot_exists(slot)
        for create_attempt in range(1, MAX_CREATE_ATTEMPTS + 1):
            try:
                pub = _bounded(run, "save and publish", fp.publish, slot, workflow, safe_to_repeat=fp.slot_exists(slot), on_created=lambda e: run.log("FREYA", f"created workflow_id={e['workflow_id']} agent_id={e['agent_id']} (not yet published)"))
                break
            except RuntimeError as e:
                if isinstance(e, BuildError) or not fp.transient(e) or create_attempt == MAX_CREATE_ATTEMPTS or run.deadline.remaining() < EXPORT_RESERVE_S:
                    raise
                if fp.slot_exists(slot):
                    run.log("RETRY", f"save and publish: the workflow exists, a later step got no answer; updating it in place: {str(e)[:160]}")
                else:
                    # No documented route lists workflows, so an unanswered create cannot be checked. A second
                    # create may leave one unused workflow in the workspace; a failed build leaves no agent at all.
                    run.log("RETRY", f"save and publish: the create request got no answer ({str(e)[:120]}); sending it again. "
                                     "If the first request was processed after all, one unused workflow stays in the workspace")
                time.sleep(15)
        pub["created"] = new_slot
        run.state.update(workflow_id=pub["workflow_id"], agent_id=pub["agent_id"], published_version=pub["version"])
        run.log("FREYA", f"{'created' if pub['created'] else 'updated'} workflow_id={pub['workflow_id']} agent_id={pub['agent_id']} "
                         f"published_version={pub['version']} steps={stats['nodes']} tools={stats['tools']} agent_model={fp.freya().AGENT_LLM['model']}")

        run.stage("READBACK")
        stored = _bounded(run, "readback", fp.readback, pub["workflow_id"])
        run.artifact("workflow.readback.json", stored)
        problems = fp.diff(workflow, stored["workflow"])
        versions = _bounded(run, "version check", fp.published_version, pub["workflow_id"])
        run.log("FREYA", f"readback: published_version={versions['published']} newest_version={versions['newest']} differences={len(problems)}")
        for p in problems:
            run.log("WARN", "readback: " + p)
        if any("dropped" in p or "lost" in p or "missing" in p for p in problems):
            raise BuildError("the platform dropped part of the workflow: " + "; ".join(problems[:6]))
        if versions["published"] != versions["newest"]:
            raise BuildError("the newest workflow version is not the published one")

        run.stage("SMOKE")
        try:
            variables = dict(workflow.get("test_variables") or {})
            variables[(ir.get("call_variables") or {}).get("customer_id") or "customer_id"] = "c_smoke_no_customer"
            reply = _bounded(run, "chat smoke turn", fp.smoke, pub["workflow_id"], variables, max_tries=3)
            ok = bool(reply.get("response")) and "{{" not in str(reply.get("response"))
            run.state["tests"].append({"name": "one chat turn (can the call start)", "result": "pass" if ok else "fail", "response": reply.get("response")})
            run.log("TEST", f"chat smoke turn: {'pass' if ok else 'FAIL'}; agent said: {str(reply.get('response'))[:200]!r}")
        except Exception as e:
            run.state["tests"].append({"name": "one chat turn (can the call start)", "result": "error", "error": str(e)[:300]})
            run.log("TEST", f"chat smoke turn: ERROR (infrastructure or workflow start): {str(e)[:300]}")

        if a.cases:
            run.stage("CASES")
            budget = run.deadline.remaining() - EXPORT_RESERVE_S
            if budget < 240:
                run.log("TEST", "practice cases skipped: not enough time left in the run budget")
            else:
                out = run.dir / "cases.json"
                code, text = fp.run_cases(pub["agent_id"], a.brief_tag, pathlib.Path(a.cases).resolve(), budget, out, a.reps, a.style, a.only)
                run.artifact("cases.log", text)
                summary = [l for l in text.splitlines() if l.startswith(("PASS", "FAIL", "       -")) or " passed " in l or "slowest" in l or "WARNING" in l]
                for line in summary:
                    run.log("TEST", "practice: " + line[:300])
                if code != 0:
                    last = next((l for l in reversed(text.splitlines()) if l.strip()), "no output")
                    run.log("TEST", f"practice run did not finish (exit {code}): {last[:300]}")
                run.state["tests"].append({"name": f"practice cases brief={a.brief_tag} reps={a.reps} style={a.style}", "exit": code,
                                           "result": next((l for l in text.splitlines() if " passed " in l), "no result line")})

        run.stage("EXPORT")
        out_path = pathlib.Path(a.out) / f"{slot}.workflow.json"
        _bounded(run, "export", fp.export, pub["workflow_id"], out_path)
        run.state.update(export_path=str(out_path.relative_to(ROOT)) if out_path.is_relative_to(ROOT) else str(out_path), export_sha256=st.sha256_file(out_path))
        run.log("EXPORT", f"{run.state['export_path']} sha256={run.state['export_sha256']} (taken from the platform after publish)")
        run.state["status"] = "COMPLETE_DEGRADED" if run.state["degraded"] else "COMPLETE"
        exit_code = 0
    except Exception as e:
        run.state["status"] = "FAILED"
        run.state["error"] = f"{type(e).__name__}: {e}"
        run.log("ERROR", f"{type(e).__name__}: {str(e)[:1500]}")
        if not isinstance(e, (BuildError, llm.ModelError, apispec.ApiSpecError, compiler.CompileError, fp.PlatformError)):
            run.artifact("traceback.txt", traceback.format_exc())
    finally:
        run.state["elapsed_s"] = round(run.deadline.elapsed(), 1)
        run.state["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cost = sum(c.get("cost_usd") or 0 for c in run.state["model_calls"])
        run.save()
        run.log("END", f"status={run.state['status']} stage={run.state['stage']} elapsed_s={run.state['elapsed_s']} model_calls={len(run.state['model_calls'])} "
                       f"model_cost_usd={cost:.2f} repairs_ir={run.state['repairs']['ir']} repairs_prompts={run.state['repairs']['prompts']} "
                       f"workflow_id={run.state.get('workflow_id')} agent_id={run.state.get('agent_id')}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
