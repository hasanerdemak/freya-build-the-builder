"""Write submission.json for one built slot, after checking that the export and the trace belong to the same run.

    python tools/make_submission.py --slot final --team "Team" --member "Name <mail@example.com>" [--member ...] --repo URL --mock-url URL

`commit` is the current HEAD: commit the exported workflow and the trace first, so that the named commit holds both.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
import state as st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", required=True)
    ap.add_argument("--team", required=True)
    ap.add_argument("--member", action="append", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--mock-url", required=True)
    ap.add_argument("--out", default=str(ROOT / "submission.json"))
    a = ap.parse_args()

    export, trace = ROOT / "submission" / f"{a.slot}.workflow.json", ROOT / "traces" / f"{a.slot}.log"
    problems = [f"{p.relative_to(ROOT)} is missing" for p in (export, trace) if not p.exists()]
    if not problems:
        text = trace.read_text(encoding="utf-8", errors="replace")
        if st.sha256_file(export) not in text:
            problems.append("the trace does not record the hash of this export: they are from different runs")
        if "status=COMPLETE" not in text:
            problems.append("the trace does not end with a completed run")
        if "REUSE" in text:
            problems.append("the trace is of a development run that made no model call")
        if a.mock_url.rstrip("/") not in text:
            problems.append("the trace was made with another services address than --mock-url")
        if st.redact(text) != text:
            problems.append("the trace contains something that looks like a credential")
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "builder", "skills", "kit", "mock", str(export.relative_to(ROOT)), str(trace.relative_to(ROOT))],
                           cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        problems.append("uncommitted changes in builder, skills, kit, mock, or in this slot's workflow or trace; commit them first")
    if problems:
        sys.exit("not written:\n- " + "\n- ".join(problems))
    members = []
    for m in a.member:
        found = re.fullmatch(r"\s*(.+?)\s*<([^<>\s]+)>\s*", m)
        if not found:
            sys.exit(f"--member must look like 'Name <mail>': {m}")
        members.append({"name": found.group(1), "email": found.group(2)})
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    data = {"team": a.team, "members": members, "repo": a.repo, "commit": head,
            "run_command": f"python3 builder/build.py {{case_folder}} {{mock_url}} --slot {a.slot}",
            "workflow": export.relative_to(ROOT).as_posix(), "trace": trace.relative_to(ROOT).as_posix(), "mock_url": a.mock_url.rstrip("/")}
    pathlib.Path(a.out).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
