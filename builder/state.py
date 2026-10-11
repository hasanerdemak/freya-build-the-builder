"""Run state, checkpoints, deadline and trace. Files only."""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def sha256_file(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def source_hash():
    """One hash over everything that decides what the builder produces."""
    digest = hashlib.sha256()
    files = sorted(list((ROOT / "builder").rglob("*.py")) + list((ROOT / "builder").glob("*.json"))
                   + [p for p in (ROOT / "skills").rglob("*") if p.is_file()] + [ROOT / "kit" / "freya.py"])
    for f in files:
        if "__pycache__" in f.parts:
            continue
        digest.update(f.relative_to(ROOT).as_posix().encode() + b"\0" + f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def git_commit():
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "builder", "skills", "kit"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        return (head or "no-commit") + ("+dirty" if dirty else "")
    except Exception:
        return "unknown"


SECRET_ENV = ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
SECRET_PATTERNS = re.compile(r"fak_[A-Za-z0-9_\-]{6,}|sk-ant-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]{8,}|(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")


def redact(text):
    """Remove credentials from anything that is written to a trace or a run directory."""
    for name in SECRET_ENV:
        value = os.environ.get(name)
        if value and len(value) >= 8:
            text = text.replace(value, "[redacted]")
    return SECRET_PATTERNS.sub("[redacted]", text)


def write_atomic(path, text):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def write_json(path, value):
    write_atomic(path, json.dumps(value, indent=1, ensure_ascii=False))


def read_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


class Deadline:
    """The budget of one builder run. A resumed run keeps the original start, so it never gains time."""

    def __init__(self, budget_s, started_epoch=None):
        self.budget_s = budget_s
        self.started_epoch = started_epoch or time.time()
        self._base = time.monotonic() - (time.time() - self.started_epoch)

    def elapsed(self):
        return time.monotonic() - self._base

    def remaining(self):
        return self.budget_s - self.elapsed()

    def allow(self, need_s, reserve_s=0):
        return self.remaining() - reserve_s >= need_s


class Run:
    def __init__(self, run_id, slot, deadline, trace_path, resumed=False):
        self.dir = ROOT / "runs" / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.deadline = deadline
        self.trace_path = pathlib.Path(trace_path)
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = {"run_id": run_id, "slot": slot, "stage": "INIT", "status": "RUNNING",
                      "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(deadline.started_epoch)),
                      "started_epoch": deadline.started_epoch, "budget_s": deadline.budget_s, "model_calls": [],
                      "repairs": {"ir": 0, "prompts": 0}, "tests": [], "degraded": [], "resumed": resumed}
        if not resumed:
            self.trace_path.write_text("", encoding="utf-8")

    def log(self, kind, text, **data):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        line = redact(f"[{stamp} +{self.deadline.elapsed():6.1f}s] {kind:<8} {text}")
        print(line, flush=True)
        # traces/<slot>.log is the trace to submit; runs/<run-id>/trace.log is the same text and is never overwritten.
        for path in (self.trace_path, self.dir / "trace.log"):
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
        with (self.dir / "events.jsonl").open("a", encoding="utf-8", newline="\n") as f:
            f.write(redact(json.dumps({"at": stamp, "elapsed_s": round(self.deadline.elapsed(), 1), "kind": kind, "text": text, **data}, ensure_ascii=False, default=str)) + "\n")

    def stage(self, name):
        self.state["stage"] = name
        self.state["elapsed_s"] = round(self.deadline.elapsed(), 1)
        self.save()
        self.log("STAGE", name)

    def model_call(self, info):
        self.state["model_calls"].append(info)
        cost = f" cost_usd={info['cost_usd']:.3f}" if isinstance(info.get("cost_usd"), (int, float)) else ""
        self.log("MODEL", f"role={info.get('role')} model={info.get('model')} skill={info.get('skill')}@{str(info.get('skill_sha256'))[:12]} "
                          f"seconds={info.get('seconds')} out_tokens={info.get('output_tokens')}{cost} purpose=\"{info.get('purpose')}\"", **info)

    def save(self):
        write_atomic(self.dir / "state.json", redact(json.dumps(self.state, indent=1, ensure_ascii=False)))

    def artifact(self, name, value):
        path = self.dir / name
        write_atomic(path, redact(value if isinstance(value, str) else json.dumps(value, indent=1, ensure_ascii=False)))
        return path
