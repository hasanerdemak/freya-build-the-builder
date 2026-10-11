"""Write submission/manifest.json in the form the organisers ask for, from the last completed run of each slot.

    python tools/make_manifest.py hidden-1 hidden-2 [--team NAME] [--repo URL]

It checks that both runs completed, came from the same builder source, and that each exported file still has the
hash its run recorded. It fills in nothing it cannot verify: team and repo stay as given on the command line.
"""
import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
import state as st


def last_run(slot):
    runs = sorted((ROOT / "runs").glob(f"*-{slot}"))
    for run in reversed(runs):
        if not (run / "state.json").exists():      # practice and chat result folders share the name pattern
            continue
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        if state.get("status", "").startswith("COMPLETE"):
            return state
    sys.exit(f"no completed run for slot {slot}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slots", nargs="+")
    ap.add_argument("--team", default="FILL-IN-TEAM-NAME")
    ap.add_argument("--repo", default="FILL-IN-REPOSITORY-URL")
    a = ap.parse_args()
    states = {slot: last_run(slot) for slot in a.slots}
    problems = []
    if len({s["source"] for s in states.values()}) != 1:
        problems.append("the runs were made from different builder sources: rebuild both from the same commit")
    for slot, s in states.items():
        path = ROOT / s["export_path"]
        if not path.exists() or st.sha256_file(path) != s["export_sha256"]:
            problems.append(f"{slot}: the exported workflow file is missing or differs from what the run exported")
        if "+dirty" in s.get("git_commit", ""):
            problems.append(f"{slot}: the run was made from uncommitted source ({s['git_commit']})")
        if "--reuse-policy" in s.get("command", ""):
            problems.append(f"{slot}: the last completed run reused the model output of an earlier run (development run); build it fresh")
        if s.get("degraded"):
            print(f"note: {slot} completed with unresolved verifier issues (see its trace)")
        trace = ROOT / "traces" / f"{slot}.log"
        if not trace.exists():
            problems.append(f"{slot}: traces/{slot}.log is missing")
        elif f"run={s['run_id']}" not in trace.read_text(encoding="utf-8", errors="replace"):
            # every run of a slot rewrites its trace, so a later failed run leaves a trace that is not the export's
            problems.append(f"{slot}: traces/{slot}.log is not the trace of run {s['run_id']} (a later run rewrote it); copy runs/{s['run_id']}/trace.log over it")
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    manifest = {"team": a.team, "repo": a.repo, "commit": head,
                "run_command": "python3 builder/build.py {brief_path} {tool_api_path}",
                "workflows": {slot: s["export_path"].replace("\\", "/") for slot, s in states.items()},
                "traces": {slot: f"traces/{slot}.log" for slot in states}}
    out = ROOT / "submission" / "manifest.json"
    out.write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(manifest, indent=1))
    for slot, s in states.items():
        print(f"{slot}: run {s['run_id']} source_commit={s.get('git_commit')} elapsed_s={s.get('elapsed_s')} workflow_id={s.get('workflow_id')}")
    print("the manifest commit is the commit that will contain the exports and traces; the builder source commit of each run is listed above")
    for p in problems:
        print("PROBLEM:", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
