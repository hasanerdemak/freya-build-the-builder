"""Development aid: one chat call against a published workflow of a slot, with call variables.

    python tools/chat_final.py <slot> --var name=value [--var ...] --say "first customer line" [--say ...]
    python tools/chat_final.py <slot> --var name=value [--var ...] --persona "who the customer is and what they want" [--turns 12]

With --say the customer lines are fixed. With --persona a model plays the customer in the language of the persona text.
The call's transcript with tool calls and results is printed at the end. Not a score: it finds mistakes.
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
sys.path.insert(0, str(ROOT / "tools"))
import freya_platform as fp

fp.load_env()
f = fp.freya()
HELPER_MODEL = os.environ.get("CHAT_CUSTOMER_MODEL", "claude-sonnet-5-5")


def ask_model(system, prompt):
    cmd = [shutil.which("claude"), "-p", "--model", HELPER_MODEL, "--tools", "", "--no-session-persistence", "--setting-sources", "", "--system-prompt", system]
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    out = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, timeout=180, env=env)
    return " ".join(out.stdout.decode("utf-8", "replace").split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slot")
    ap.add_argument("--var", action="append", default=[])
    ap.add_argument("--say", action="append", default=[])
    ap.add_argument("--persona")
    ap.add_argument("--turns", type=int, default=12)
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    workflow_id = json.loads(fp.state_file().read_text())[a.slot]["workflow_id"]
    variables = dict(v.split("=", 1) for v in a.var)
    lines, call_id, transcript = list(a.say), None, []
    said = lines.pop(0) if lines else "Alo?"
    for _ in range(a.turns):
        body = {"workflowId": workflow_id, "message": said, "customerVariables": variables}
        if call_id:
            body["callId"] = call_id
        r = fp.retrying(f.api, "POST", "/api/v2/chat/message", body, tries=2, wait_s=5)
        call_id = r.get("callId") or call_id
        reply = r.get("response")
        transcript += [("customer", said), ("agent", reply)]
        print(f"CUSTOMER: {said}\nAGENT:    {reply}\n", flush=True)
        if r.get("ended") or r.get("callEnded") or reply in (None, ""):
            break
        if a.persona:
            history = "\n".join(f"{'ASSISTANT' if who == 'agent' else 'YOU'}: {text}" for who, text in transcript)
            said = ask_model("You play a bank customer on the phone. Stay in character, speak only the customer's words, one or two short sentences, "
                             "in the language of the persona. When your matter is settled, say thanks and goodbye, and after the assistant's goodbye answer only: [END]\n\n" + a.persona,
                             f"The call so far:\n\n{history}\n\nWrite only what you say next.")
            if "[END]" in said:
                break
        elif lines:
            said = lines.pop(0)
        else:
            break
    try:
        f.api("POST", "/api/v2/chat/end", {"callId": call_id})
    except Exception:
        pass
    import show_call
    print()
    show_call.show(call_id)


if __name__ == "__main__":
    main()
