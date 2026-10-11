"""Development aid: play practice cases over the chat route, next to (or instead of) the simulation queue.

    python tools/chat_cases.py <slot> --brief A|B [--only PUB-11,PUB-12] [--style NAME] [--reps N] [--parallel 3] [--turns 30]

The phone agent is the real one (the published workflow on Freya). The customer is played by a model through the
`claude` CLI with the kit's own persona text, and a second model call reads the transcript against the case's
conversation checks. The bank record is scored with the kit's ledger scoring.

This is NOT the official measure: customer and judge are other models than the kit's, and the chat route may skip
the first code step. It finds mistakes; it does not produce a score to report as one.
"""
import argparse
import concurrent.futures
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
import freya_platform as fp

fp.load_env()
fp.ensure_team_key()
f = fp.freya()
sys.path.insert(0, str(ROOT / "kit" / "tools"))
import run_cases as kit_runner
from score import http, score_ledger

HELPER_MODEL = os.environ.get("CHAT_CUSTOMER_MODEL", "claude-sonnet-5-5")


def bank(path, body=None):
    return fp.retrying(http, path, body, tries=6, wait_s=10)


def ask_model(system, prompt):
    cmd = [shutil.which("claude"), "-p", "--model", HELPER_MODEL, "--tools", "", "--no-session-persistence",
           "--setting-sources", "", "--system-prompt", system]
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    out = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, timeout=180, env=env)
    return out.stdout.decode("utf-8", "replace").strip()


def customer_line(persona, transcript):
    lines = "\n".join(f"{'CALLER' if who == 'agent' else 'YOU'}: {text}" for who, text in transcript)
    said = ask_model(persona, f"The phone call so far:\n\n{lines}\n\nWrite only what you say next.")
    return " ".join(said.split()).removeprefix("YOU:").strip()


def judge(case, transcript, calls):
    lines = "\n".join(f"[{n}] {'AGENT' if who == 'agent' else 'CUSTOMER'}: {text}" for n, (who, text) in enumerate(transcript))
    made = "\n".join(f"- {c.get('tool')} {json.dumps(c.get('args') or c.get('body') or {})[:200]} -> {json.dumps(c.get('result') if 'result' in c else c.get('ok'))[:160]}" for c in calls) or "none"
    prompt = (f"Transcript of a phone call:\n\n{lines}\n\nTool calls that reached the bank, in order, with arguments and the bank's answer:\n{made}\n\n"
              "Success criteria, all of which must hold:\n- " + "\n- ".join(case["checks"]) +
              "\n\nAnswer with one line of JSON and nothing else: {\"pass\": true or false, \"reason\": \"one or two sentences naming the criterion and the message number\"}")
    text = ask_model("You check a phone call against given criteria. Judge only what the transcript and the tool list show. Be strict and literal.", prompt)
    try:
        verdict = json.loads(text[text.find("{"):text.rfind("}") + 1])
        return bool(verdict.get("pass")), str(verdict.get("reason"))
    except Exception:
        return None, "no verdict: " + text[:160]


def play(case, workflow_id, style, max_turns):
    log = [f"--- {case['id']} {case['title']}" + (f" [{style}]" if style else "")]
    session = bank("/v1/practice/sessions", {"case": case["id"]})["variables"]
    now = datetime.datetime.fromisoformat(session["now"])
    # The kit's customer has no clock either; this one must not fall back on the real date.
    persona = kit_runner.persona_for(case, {"identity": case["identity"]}, style)["prompt"] + (
        f"\n\nTHE DAY OF THIS CALL: {now.strftime('%A %d %B %Y')}. 'Today', 'tomorrow' and weekday names are counted from that day. "
        "Words in brackets like (say it in words, like ...) are examples of a format, never your own answer.")
    transcript, call_id, said, problem = [], None, "Hello?", None
    for _ in range(max_turns):
        body = {"workflowId": workflow_id, "message": said, "customerVariables": session}
        if call_id:
            body["callId"] = call_id
        started = time.monotonic()
        try:
            r = f.api("POST", "/api/v2/chat/message", body)
        except Exception as e:
            problem = f"infrastructure: chat turn failed: {str(e)[:160]}"
            break
        call_id = r.get("callId") or call_id
        reply = " ".join(str(r.get("response") or "").split())
        transcript.append(("agent", reply))
        log.append(f"   AGENT ({time.monotonic() - started:4.1f}s): {reply}")
        if bank("/v1/practice/ledger/" + session["customer_id"]).get("outcome"):
            break
        said = customer_line(persona, transcript)
        transcript.append(("customer", said))
        log.append(f"   CUSTOMER: {said}")
    else:
        problem = f"no close_call within {max_turns} turns"
    if call_id:
        try:
            f.api("POST", "/api/v2/chat/end", {"callId": call_id})
        except Exception:
            pass
    ledger = bank("/v1/practice/ledger/" + session["customer_id"])
    tools = [x["tool"] for x in ledger["calls"]]
    failed = score_ledger(case, ledger)["failed"] + ([problem] if problem else [])
    verdict, reason = (None, "not judged: the call did not finish") if problem else judge(case, transcript, ledger["calls"])
    if verdict is False:
        failed.append("conversation: " + reason)
    kind = "infra" if problem and problem.startswith("infrastructure") else ("pass" if not failed else "fail")
    log.append(f"{kind.upper():<5} {case['id']} | " + (" > ".join(tools) or "(no tool calls)"))
    log += ["       - " + line[:320] for line in failed]
    if verdict is not False:
        log.append("       judge: " + reason[:300])
    return {"id": case["id"], "level": case["level"], "family": case["family"], "pass": kind == "pass", "kind": kind, "failed": failed, "call_id": call_id,
            "tools": tools, "customer_id": session["customer_id"], "transcript": transcript, "judge": reason, "style": style}, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slot")
    ap.add_argument("--brief")
    ap.add_argument("--cases", default=str(ROOT / "kit" / "public-test-set.json"))
    ap.add_argument("--only")
    ap.add_argument("--style")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--turns", type=int, default=30)
    ap.add_argument("--quiet", action="store_true", help="print transcripts of failed calls only")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    entry = json.loads(fp.state_file().read_text())[a.slot]
    cases = [c for c in json.loads(pathlib.Path(a.cases).read_text(encoding="utf-8"))
             if (not a.brief or c["brief"] == a.brief) and (not a.only or c["id"] in a.only.split(","))]
    jobs = [c for _ in range(a.reps) for c in cases]

    def one(case):
        try:
            return play(case, entry["workflow_id"], a.style, a.turns)
        except Exception as e:
            return ({"id": case["id"], "level": case["level"], "family": case["family"], "pass": False, "kind": "infra",
                     "failed": [f"infrastructure: {type(e).__name__}: {str(e)[:200]}"], "tools": [], "transcript": [], "style": a.style},
                    [f"--- {case['id']} {case['title']}", f"INFRA {case['id']} | {type(e).__name__}: {str(e)[:200]}"])

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.parallel)) as pool:
        for row, log in pool.map(one, jobs):
            rows.append(row)
            print("\n".join(log if not (a.quiet and row["kind"] == "pass") else log[:1] + log[-2:]) + "\n", flush=True)
    counted = [r for r in rows if r["kind"] != "infra"]
    print(f"{sum(r['pass'] for r in counted)} / {len(counted)} calls as expected (bank record and model-judged conversation), "
          f"{len(rows) - len(counted)} stopped by infrastructure. Chat route, slot {a.slot}, style {a.style}. Not an official score.")
    by_case = {}
    for r in counted:
        by_case.setdefault(r["id"], []).append("P" if r["pass"] else "F")
    print("per case:", "  ".join(f"{k[4:]}:{''.join(v)}" for k, v in sorted(by_case.items())))
    out = ROOT / "runs" / (time.strftime("chat-%Y%m%d-%H%M%S", time.gmtime()) + "-" + a.slot + (f"-{a.style}" if a.style else ""))
    out.mkdir(parents=True)
    (out / "cases.json").write_text(json.dumps({"route": "chat", "helper_model": HELPER_MODEL, "style": a.style, "rows": rows}, indent=1), encoding="utf-8")
    print("saved", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
