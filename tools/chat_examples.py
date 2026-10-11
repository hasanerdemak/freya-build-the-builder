"""Development aid: play the example conversations of a case folder against a published slot over the chat route.

    python tools/chat_examples.py <slot> <case_folder> [--only 1,4] [--parallel 3] [--turns 14] [--out DIR]

Reads `example-scenarios.md` of the folder at run time: each example's call variables, its situation (played by a model as
the customer) and its expected behaviour (read against the transcript by a second model call). Not the official measure:
customer and judge are other models than the organisers'. It finds mistakes.
"""
import argparse
import concurrent.futures
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
sys.path.insert(0, str(ROOT / "tools"))
import freya_platform as fp
from chat_final import ask_model

fp.load_env()
f = fp.freya()


def examples(folder):
    text = (pathlib.Path(folder) / "example-scenarios.md").read_text(encoding="utf-8")
    out = []
    for block in re.split(r"\n## ", text)[1:]:
        title = block.splitlines()[0].strip()
        variables = dict(re.findall(r"\*\*([A-Za-z ]+?):\*\*\s*`([^`]+)`", block))
        situation = re.search(r"\*\*Situation:\*\*\s*(.+?)\n\s*\n", block, re.S)
        expected = re.search(r"\*\*Expected behaviou?r:\*\*\s*(.+?)(?:\n---|\Z)", block, re.S)
        named = {("customerNumber" if "customer" in k.lower() else k.strip()): v for k, v in variables.items()}
        out.append({"title": title, "variables": named, "situation": situation.group(1).strip() if situation else "",
                    "expected": expected.group(1).strip() if expected else ""})
    return out


def events(call_id):
    call = fp.retrying(f.get_call, call_id)
    lines = []
    for m in call.get("messages", []):
        if m.get("role") in ("assistant", "user"):
            lines.append(f"{'AGENT' if m['role'] == 'assistant' else 'CUSTOMER'}: {m.get('message')}")
            continue
        try:
            ev = json.loads(m.get("message"))
        except Exception:
            continue
        kind = ev.get("event")
        if kind == "function_call_start":
            args = ev.get("args") or {}
            lines.append(f"  [tool call {ev.get('function')} {json.dumps(args.get('body', args), ensure_ascii=False)[:300]}]")
        elif kind == "function_call_complete":
            lines.append(f"  [tool result {ev.get('function')} {json.dumps(ev.get('result'), ensure_ascii=False)[:1500]}]")
        elif kind == "function_call_error":
            lines.append(f"  [tool error {ev.get('function')} {str(ev.get('error'))[:500]}]")
        elif kind == "tool_call_routing":
            lines.append(f"  [call ended through {ev.get('tool_name')}]")
    return lines, call.get("endedReason")


def play(example, workflow_id, max_turns):
    persona = ("You play a bank customer on a phone call with the bank's voice assistant. You speak Turkish, one or two short natural sentences per turn, "
               "only the customer's words. Follow this situation exactly and in its order; do not add requests it does not contain:\n\n" + example["situation"] +
               "\n\nThe assistant speaks first. Do not help the assistant and do not correct it. Bring up one thing per turn, in the order of the situation; do not name a choice before the assistant has asked for it unless the situation says you do. When everything in your situation is done and the assistant asks whether you want anything else or offers something more, say no thanks and goodbye. Answer only [END] after the assistant has said it is sending you to the menu or to a person, or has closed the call.")
    transcript, call_id, said = [], None, "Alo"
    for _ in range(max_turns):
        body = {"workflowId": workflow_id, "message": said, "customerVariables": example["variables"]}
        if call_id:
            body["callId"] = call_id
        try:
            r = fp.retrying(f.api, "POST", "/api/v2/chat/message", body, tries=3, wait_s=8)
        except Exception as e:
            transcript.append(("agent", f"[platform error: {str(e)[:160]}]"))
            break
        call_id = r.get("callId") or call_id
        transcript += [("customer", said), ("agent", r.get("response"))]
        history = "\n".join(f"{'ASSISTANT' if who == 'agent' else 'YOU'}: {text}" for who, text in transcript[1:])
        said = ask_model(persona, f"The call so far:\n\n{history}\n\nWrite only what you say next.")
        if "[END]" in said or not said:
            break
    try:
        f.api("POST", "/api/v2/chat/end", {"callId": call_id})
    except Exception:
        pass
    lines, ended = events(call_id) if call_id else ([], None)
    verdict = ask_model("You check a phone call against given expectations. Judge only what the transcript and the tool lines show. Be strict and literal. "
                        "The first customer line 'Alo' only opens the call.",
                        "Transcript with tool calls:\n\n" + "\n".join(lines) + "\n\nExpected behaviour, every item must hold:\n" + example["expected"] +
                        "\n\nAnswer with one line of JSON and nothing else: {\"pass\": true or false, \"failed\": [\"each expectation that does not hold, with the reason in a few words\"]}")
    try:
        got = json.loads(verdict[verdict.find("{"):verdict.rfind("}") + 1])
    except Exception:
        got = {"pass": None, "failed": ["no verdict: " + verdict[:200]]}
    return {"title": example["title"], "call_id": call_id, "ended": ended, "lines": lines, "pass": got.get("pass"), "failed": got.get("failed") or []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slot")
    ap.add_argument("case_folder")
    ap.add_argument("--only")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--turns", type=int, default=14)
    ap.add_argument("--out")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    workflow_id = json.loads(fp.state_file().read_text())[a.slot]["workflow_id"]
    todo = examples(a.case_folder)
    # A simulation runs the workflow's first code step, the chat route does not: its starting values are passed instead.
    compiled = sorted((ROOT / "runs").glob(f"*-{a.slot}/workflow.compiled.json"))
    defaults = json.loads(compiled[-1].read_text(encoding="utf-8")).get("test_variables", {}) if compiled else {}
    for e in todo:
        e["variables"] = {**{k: v for k, v in defaults.items() if k.startswith("sv_")}, **e["variables"]}
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        todo = [e for i, e in enumerate(todo, 1) if i in keep]
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.parallel) as pool:
        results = list(pool.map(lambda e: play(e, workflow_id, a.turns), todo))
    for r in results:
        print(f"\n===== {r['title']}: {'PASS' if r['pass'] else 'FAIL' if r['pass'] is False else 'NO VERDICT'}  (call {r['call_id']}, ended {r['ended']})")
        for line in r["lines"]:
            print("   " + line[:700])
        for why in r["failed"]:
            print("   !! " + str(why)[:400])
    print("\n" + ", ".join(f"{r['title'].split(':')[0]}: {'pass' if r['pass'] else 'FAIL'}" for r in results))
    if a.out:
        out = pathlib.Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"examples-{a.slot}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
