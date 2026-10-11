"""Freya platform steps: dry run, save and publish, readback, smoke turn, practice cases, export.

Uses the kit's `freya.py` unchanged. The phone-agent model (`AGENT_LLM`) is set there and is never touched here.
"""
import json
import os
import pathlib
import secrets
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
FACTS_PATH = pathlib.Path(__file__).resolve().parent / "platform_facts.json"
_freya = None


class PlatformError(RuntimeError):
    pass


def load_env():
    """Read KEY=VALUE lines from .env or env.sh at the repository root. Existing environment variables win."""
    for name in (".env", "env.sh"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.split(" #", 1)[0].strip().strip('"').strip("'")
            if key.strip() and value and key.strip() not in os.environ:
                os.environ[key.strip()] = value


def has_key():
    return bool(os.environ.get("FREYA_API_KEY"))


def ensure_team_key():
    """The practice bank needs a team key the team makes up once. Create and store it when it is missing."""
    if os.environ.get("FREYA_BANK_TEAM_KEY"):
        return
    key = "team-" + secrets.token_hex(16)
    os.environ["FREYA_BANK_TEAM_KEY"] = key
    with (ROOT / ".env").open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"\nFREYA_BANK_TEAM_KEY={key}\n")


def freya():
    global _freya
    if _freya is None:
        if not has_key():
            raise PlatformError("FREYA_API_KEY is not set (put it in .env or the environment)")
        sys.path.insert(0, str(KIT))
        import freya as module
        _freya = module
    return _freya


def transient(error):
    """True for a platform answer that says the request was not processed and may be sent again."""
    text = str(error)
    return any(mark in text for mark in ("-> 502", "-> 503", "-> 504", "temporarily unavailable"))


def retrying(fn, *args, tries=4, wait_s=10, only_rejected=False, remaining=None, on_retry=None, **kwargs):
    """Repeat a platform call after a transient failure (502, 503, 504).

    only_rejected: repeat only when the platform says it never processed the request (its API key check was
    unavailable). That is the safe case for a call that creates something; an ambiguous failure is not repeated.
    remaining: a function returning the seconds left in the run. When given, retries continue while more than
    90 seconds are left, so an outage is waited out inside the run deadline and never beyond it."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn(*args, **kwargs)
        except (RuntimeError, OSError) as e:
            safe = "API key validation is temporarily unavailable" in str(e) if only_rejected else (transient(e) or isinstance(e, OSError))
            more = remaining() > 90 + wait_s if remaining else attempt < tries
            if not safe or not more:
                raise
            if on_retry:
                on_retry(attempt, e)
            time.sleep(wait_s)


def health():
    """One cheap authenticated read. Raises when the platform or the key does not answer."""
    return bool(freya().api("GET", "/api/v2/workspaces"))


def facts():
    """Platform behaviour measured by probes (see docs/PLATFORM_PROBES.md). Decides how guards block a call."""
    try:
        return json.loads(FACTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"block_mode": "none", "pre_verification_block": "none"}


def state_file():
    ws = str(freya().workspace_id())
    return ROOT / f".freya-agents.{ws[:12]}.json"


def slot_exists(slot):
    """True when this workspace already has a workflow for the slot, so that saving it is an update in place."""
    try:
        path = state_file()
        return path.exists() and slot in json.loads(path.read_text())
    except Exception:
        return False


def dry_run(name, workflow):
    return freya().validate_workflow(name, workflow)


def publish(slot, workflow, on_created=None):
    """Create the slot's workflow once, update it in place afterwards, and publish. Returns ids and the version."""
    f = freya()
    path = state_file()
    known = json.loads(path.read_text()) if path.exists() else {}
    created = slot not in known
    entry = f.save_workflow(slot, workflow, state_file=str(path))
    if created:
        if on_created:
            on_created(entry)
        # create_workflow does not run the helper's model correction and publish steps; one update does.
        entry["version"] = f.update_workflow(entry["workflow_id"], workflow)
        known = json.loads(path.read_text())
        known[slot] = entry
        path.write_text(json.dumps(known, indent=1))
    return {"workflow_id": entry["workflow_id"], "agent_id": entry["agent_id"], "version": entry.get("version"), "created": created}


def readback(workflow_id):
    f = freya()
    return f.api("GET", f"/api/v2/workflow/{workflow_id}", workspaceId=f.workspace_id())["workflow"]


def published_version(workflow_id):
    f = freya()
    versions = f.api("GET", f"/api/v2/versions/workflow/{workflow_id}", workspaceId=f.workspace_id())["versions"]
    pub = [v for v in versions if v.get("isPublished")]
    newest = max(versions, key=lambda v: v["versionNumber"])
    return {"published": max((v["versionNumber"] for v in pub), default=None), "newest": newest["versionNumber"]}


def _mapping(entries):
    """Mapping entries as (path, variable); the platform adds an empty description to each."""
    return [(m.get("path"), m.get("variable")) for m in entries or []]


def _tools_of(node):
    return {t.get("function", {}).get("name"): t for t in (node.get("tools") or []) + (node.get("pre_actions") or [])}


def diff(sent, stored):
    """What the platform dropped or changed, compared with what was sent. Platform additions are ignored."""
    problems = []
    for key in ("initial_node", "system_prompt"):
        if sent.get(key) != stored.get(key):
            problems.append(f"{key} differs")
    for key, node in sent["nodes"].items():
        got = (stored.get("nodes") or {}).get(key)
        if got is None:
            problems.append(f"node {key} is missing")
            continue
        for field in ("node_type", "task_message", "passthrough", "passthrough_default_transition", "suppress_tool_results",
                      "on_enter", "max_turn_transition"):
            if field in node and node[field] != got.get(field):
                problems.append(f"node {key}: {field} differs")
        if _mapping(node.get("response_mapping")) != _mapping(got.get("response_mapping")):
            problems.append(f"node {key}: lost or changed response_mapping")
        if [(t["target_node"], t["condition"]) for t in node.get("transitions") or []] != \
                [(t.get("target_node"), {k: v for k, v in (t.get("condition") or {}).items()}) for t in got.get("transitions") or []]:
            want = [t["target_node"] for t in node.get("transitions") or []]
            have = [t.get("target_node") for t in got.get("transitions") or []]
            if want != have:
                problems.append(f"node {key}: transitions differ ({want} sent, {have} stored)")
        want_tools, got_tools = _tools_of(node), _tools_of(got)
        for name, tool in want_tools.items():
            if name not in got_tools:
                problems.append(f"node {key}: tool {name} was dropped")
                continue
            for hook in ("pre_process_code", "post_process_code"):
                if tool.get(hook) and tool.get(hook) != got_tools[name].get(hook):
                    problems.append(f"node {key}: tool {name} lost or changed {hook}")
            if _mapping(tool.get("response_mapping")) != _mapping(got_tools[name].get("response_mapping")):
                problems.append(f"node {key}: tool {name} lost or changed response_mapping")
            a = tool["function"]["parameters"]["properties"]
            b = got_tools[name].get("function", {}).get("parameters", {}).get("properties", {})
            if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
                problems.append(f"node {key}: tool {name} parameters differ")
    return problems


def smoke(workflow_id, variables, message="Hello?"):
    """One chat turn against the published workflow, then end the chat. Shows whether a call can start."""
    f = freya()
    r = f.api("POST", "/api/v2/chat/message", {"workflowId": workflow_id, "message": message, "customerVariables": variables})
    call_id = r.get("callId")
    try:
        if call_id:
            f.api("POST", "/api/v2/chat/end", {"callId": call_id})
    except Exception:
        pass
    return {"response": r.get("response"), "call_id": call_id}


def export(workflow_id, path):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    freya().export_workflow(workflow_id, str(path))
    # The kit writes in text mode, which gives CRLF on Windows; git stores LF. Fixed line ends keep the file's
    # hash equal to the one the trace records, on every checkout.
    data = pathlib.Path(path).read_bytes()
    pathlib.Path(path).write_bytes(data.replace(b"\r\n", b"\n"))
    return path


def run_cases(agent_id, brief_tag, cases_file, timeout_s, out_path, reps=1, style=None, only=None):
    """Run the kit's practice runner (through cases_runner, which only adds retries). Returns (exit code, output text)."""
    ensure_team_key()
    cmd = [sys.executable, str(pathlib.Path(__file__).with_name("cases_runner.py")), agent_id, "--cases", str(cases_file), "--out", str(out_path), "--reps", str(reps)]
    if brief_tag:
        cmd += ["--brief", brief_tag]
    if style:
        cmd += ["--style", style]
    if only:
        cmd += ["--only", only]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        proc = subprocess.run(cmd, cwd=KIT, capture_output=True, timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or b"").decode("utf-8", "replace") + "\n[builder] the practice run exceeded the remaining time budget and was stopped"
    return proc.returncode, proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
