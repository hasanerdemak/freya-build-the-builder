"""Development aid: score a finished simulation whose runner was lost (platform outage while it was waiting).

    python tools/score_simulation.py <simulation_id> [--brief A|B] [--cases kit/public-test-set.json] [--out runs/x/cases.json] [--wait MINUTES]
    python tools/score_simulation.py list        # the workspace's ten newest simulations with their status

It applies the kit's own ledger scoring and the judge verdicts stored with the simulation. The customer id of
each call is read from the call's own tool calls, the persona of each case from the kit's persona store.
"""
import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
import freya_platform as fp

fp.load_env()
fp.ensure_team_key()
f = fp.freya()
sys.path.insert(0, str(ROOT / "kit" / "tools"))
from score import http as _http, score_ledger


def http(path, body=None):
    return fp.retrying(_http, path, body, tries=6, wait_s=10)

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def customer_of(call):
    for m in call.get("messages", []):
        try:
            ev = json.loads(m.get("message"))
        except Exception:
            continue
        if isinstance(ev, dict) and ev.get("event") == "function_call_start":
            args = ev.get("args") or {}
            cid = (args.get("body") or args).get("customer_id") if isinstance(args.get("body") or args, dict) else None
            if cid:
                return str(cid).strip()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("simulation_id")
    ap.add_argument("--brief")
    ap.add_argument("--cases", default=str(ROOT / "kit" / "public-test-set.json"))
    ap.add_argument("--out")
    ap.add_argument("--wait", type=float, default=0, help="minutes to wait for a running simulation")
    a = ap.parse_args()
    if a.simulation_id == "list":
        found = fp.retrying(f.api, "GET", "/api/v2/simulations", workspaceId=f.workspace_id(), limit=10)
        for s in (found.get("simulations") or found.get("items") or found) if isinstance(found, dict) else found:
            m = s.get("metrics") or {}
            print(s.get("id"), f"{str(s.get('status')):<10}", s.get("createdAt"), s.get("name"), f"runs={m.get('totalRuns')} passed={m.get('passedRuns')} failed={m.get('failedRuns')}")
        return
    cases = {c["id"]: c for c in json.loads(pathlib.Path(a.cases).read_text(encoding="utf-8")) if not a.brief or c["brief"] == a.brief}
    personas = json.loads((ROOT / "kit" / ".personas.json").read_text(encoding="utf-8"))
    case_of = {pid: key.split(":")[0] for key, pid in personas.items()}
    waited = 0
    while True:
        sim = fp.retrying(f.api, "GET", f"/api/v2/simulations/{a.simulation_id}", tries=20, wait_s=15)
        sim = sim.get("simulation") or sim
        if sim.get("status") in ("completed", "failed", "paused") or waited >= a.wait * 60:
            break
        time.sleep(30)
        waited += 30
    its = fp.retrying(f.api, "GET", f"/api/v2/simulations/{a.simulation_id}/iterations", tries=20, wait_s=15)
    its = its.get("iterations") or its.get("items") or its
    runs = fp.retrying(f.get_iteration, a.simulation_id, its[0]["id"])["runs"]
    open_runs = [r for r in runs if r.get("outcome") in (None, "queued", "running")]
    if sim.get("status") != "completed":
        if open_runs or not runs:
            sys.exit(f"simulation status is {sim.get('status')} and {len(open_runs)} of {len(runs)} calls have no verdict: nothing to score yet")
        print(f"note: simulation status is {sim.get('status')}, but every call has a verdict; scoring those\n")
    rows = []
    for r in runs:
        case = cases.get(case_of.get(r.get("personaId"), ""))
        if not case:
            continue
        failed, tools, cid = [], [], None
        if r.get("callId"):
            cid = customer_of(fp.retrying(f.get_call, r["callId"]))
        if cid:
            led = http("/v1/practice/ledger/" + cid)
            failed = score_ledger(case, led)["failed"]
            tools = [x["tool"] for x in led["calls"]]
        else:
            failed.append("ledger not checked: the call made no tool call that names its customer")
        if r.get("outcome") != "pass":
            failed.append("conversation: " + " ".join(str(r.get("reason") or "no verdict").split())[:400])
        rows.append({"id": case["id"], "level": case["level"], "family": case["family"], "pass": not failed, "failed": failed,
                     "call_id": r.get("callId"), "tools": tools, "customer_id": cid})
    rows.sort(key=lambda x: x["id"])
    for row in rows:
        print("PASS" if row["pass"] else "FAIL", row["id"], f"{row['level']:<6}", f"{row['family']:<14}", "|",
              " > ".join(t.replace("verify_identity", "verify").replace("create_promise", "promise") for t in row["tools"]) or "(no tool calls)")
        for line in row["failed"]:
            print("       -", line[:260])
    print(f"\n{sum(r['pass'] for r in rows)} / {len(rows)} passed (bank records and conversation checks), simulation {a.simulation_id}, scored after the fact")
    if a.out:
        out = pathlib.Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"simulation_id": a.simulation_id, "passed": sum(r["pass"] for r in rows), "total": len(rows), "runs": 1, "rows": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
