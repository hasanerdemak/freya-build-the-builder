"""Development aid: run practice cases against the workflow a slot already has, without building or publishing.

    python tools/run_practice.py <slot> --brief A|B [--reps N] [--style NAME] [--only PUB-01,PUB-02] [--minutes 40]

Results go to runs/practice-<time>-<slot>/ (cases.json for tools/show_call.py, cases.log with the runner output).
"""
import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
import freya_platform as fp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slot")
    ap.add_argument("--brief")
    ap.add_argument("--cases", default=str(ROOT / "kit" / "public-test-set.json"))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--style")
    ap.add_argument("--only")
    ap.add_argument("--minutes", type=float, default=40)
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    fp.load_env()
    entry = json.loads(fp.state_file().read_text())[a.slot]
    out = ROOT / "runs" / (time.strftime("practice-%Y%m%d-%H%M%S", time.gmtime()) + "-" + a.slot)
    out.mkdir(parents=True)
    print(f"slot={a.slot} agent_id={entry['agent_id']} workflow_id={entry['workflow_id']} version={entry.get('version')} out={out.relative_to(ROOT)}", flush=True)
    code, text = fp.run_cases(entry["agent_id"], a.brief, pathlib.Path(a.cases).resolve(), a.minutes * 60, out / "cases.json", a.reps, a.style, a.only)
    (out / "cases.log").write_text(text, encoding="utf-8")
    keep = [l for l in text.splitlines() if l.strip() and "another simulation is running" not in l]
    print("\n".join(l[:400] for l in keep[-60:]))
    sys.exit(code)


if __name__ == "__main__":
    main()
