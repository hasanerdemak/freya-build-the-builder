"""Development aid: print one call's transcript with tool calls, results and routing, compactly.

    python tools/show_call.py <call_id> [--vars]          # one call
    python tools/show_call.py --run runs/<run>/cases.json [--failed] [--vars]   # every call of a practice run
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
import freya_platform as fp

fp.load_env()
f = fp.freya()
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def show(call_id, with_vars=False):
    call = fp.retrying(f.get_call, call_id)
    print(f"--- call {call_id} ended={call.get('endedReason')} duration={call.get('duration')}")
    for m in call.get("messages", []):
        t = m.get("secondsFromStart")
        stamp = f"{t:6.1f}s" if isinstance(t, (int, float)) else "       "
        role = m.get("role")
        if role in ("assistant", "user"):
            print(f"{stamp} {role.upper():<9} [{m.get('nodeId')}] {m.get('message')}")
            continue
        try:
            ev = json.loads(m.get("message"))
        except Exception:
            print(f"{stamp} system    {str(m.get('message'))[:300]}")
            continue
        kind = ev.get("event")
        if kind == "function_call_start":
            args = ev.get("args") or {}
            print(f"{stamp}   CALL    {ev.get('function')} {json.dumps(args.get('body', args))[:400]} url={str(args.get('url'))[-40:]}")
        elif kind == "function_call_complete":
            print(f"{stamp}   RESULT  {ev.get('function')} {json.dumps(ev.get('result'))[:900]}")
        elif kind == "node_enter":
            print(f"{stamp}   NODE    {ev.get('from_node')} -> {ev.get('node')} ({ev.get('reason')})")
        elif kind == "error":
            print(f"{stamp}   ERROR   {json.dumps(ev)[:600]}")
        elif kind == "route_evaluated" and with_vars:
            table = ev.get("variable_table") or {}
            keep = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in table.items() if k.startswith("rt_") and k != "rt_state"}
            print(f"{stamp}   ROUTE   matched={ev.get('matched')} target={ev.get('target')} {json.dumps(keep)[:400]}")
            if "rt_state" in table:
                v = table["rt_state"]
                print(f"{stamp}   STATE   {str(v.get('value') if isinstance(v, dict) else v)[:700]}")
        elif kind not in ("route_evaluated",):
            print(f"{stamp}   {str(kind):<7} {json.dumps(ev)[:300]}")


def main():
    args = sys.argv[1:]
    with_vars = "--vars" in args
    if "--run" in args:
        data = json.loads(pathlib.Path(args[args.index("--run") + 1]).read_text(encoding="utf-8"))
        for row in data.get("rows", []):
            if "--failed" in args and row.get("pass"):
                continue
            print(f"\n===== {row['id']} {'PASS' if row.get('pass') else 'FAIL'} {row.get('family')} tools={row.get('tools')}")
            for reason in row.get("failed", []):
                print("  why:", reason[:500])
            if row.get("call_id"):
                show(row["call_id"], with_vars)
    else:
        show(args[0], with_vars)


if __name__ == "__main__":
    main()
