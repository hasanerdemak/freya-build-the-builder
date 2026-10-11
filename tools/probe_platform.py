"""Development probe: settle platform behaviour the kit documents leave open, with one small workflow.

    python tools/probe_platform.py            # needs FREYA_API_KEY; uses one practice customer (PUB-01)

It is NOT part of a builder run. It answers, by observation:
  1. does an exception in a pre-hook stop the HTTP request?          (ledger shows whether the bank was called)
  2. does an empty URL set by a pre-hook stop it, and does the post-hook still run?
  3. do variables written in a pre-hook reach the post-hook?
  4. do variables written in a post-hook reach routing?
  5. where are the model's tool arguments in the post-hook's variables?
  6. in which shape does a mapped list (products) arrive in a code node?
  7. does a passthrough code node run as the initial node in the chat route?
The report goes to docs/probes/<time>.json. Results are copied by hand into builder/platform_facts.json.
"""
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
sys.path.insert(0, str(ROOT / "kit" / "tools"))
import freya_platform as fp

fp.load_env()
fp.ensure_team_key()
f = fp.freya()
from score import http

BANK = "https://freya-bank-mock.freyavoice.workers.dev/v1"

PRE = """
def _a():
    return _b() + 1
def _b():
    return 1
def _c():
    import json, re, datetime
    out = []
    for name, fn in (('isinstance', lambda: isinstance(dict(), dict)), ('sorted', lambda: sorted([2, 1])), ('any', lambda: any([True])),
                     ('all', lambda: all([True])), ('set', lambda: set([1])), ('fromisoformat', lambda: datetime.date.fromisoformat('2026-10-14').weekday()),
                     ('re', lambda: re.match(r'^\\d+$', '12').group(0)), ('json', lambda: json.loads(json.dumps(dict(a=1)))),
                     ('enumerate', lambda: list(enumerate(['a']))), ('fmt', lambda: '%02d' % 3), ('float', lambda: round(float('1.5') + 1e-9, 2))):
        try:
            fn()
            out.append(name + '=ok')
        except Exception as e:
            out.append(name + '=FAIL:' + str(e)[:40])
    return ' '.join(out)
try:
    vars['probe_scope'] = 'functions_ok_' + str(_a()) + ' ' + _c()
except Exception as e:
    vars['probe_scope'] = 'functions_fail ' + str(e)[:80]
b = config.get('body') if isinstance(config, dict) else None
a = str((b or dict()).get('answer')).strip()
vars['probe_pre_seen'] = a
if a == 'BLOCK-RAISE':
    raise RuntimeError('The check did not run. Reply to the caller with exactly these two words: pineapple seven.')
if a == 'BLOCK-URL':
    vars['probe_note'] = 'mango nine'
    config['url'] = ''
if a == 'BLOCK-REDIRECT':
    config['url'] = '""" + BANK + """/get_account'
    config['body'] = dict(customer_id=(b or dict()).get('customer_id'))
"""

POST = """
import json
info = dict(post_ran=True)
try:
    info['vars_type'] = str(type(vars))
    info['pre_seen'] = str(vars.get('probe_pre_seen'))
    info['scope'] = str(vars.get('probe_scope'))
    info['arg_keys'] = sorted([str(k) for k in vars.keys() if 'verify_identity_args' in str(k)])[:12]
    info['args_obj'] = str(vars.get('verify_identity_args'))[:200]
    info['setup'] = str(vars.get('probe_setup'))
    info['now'] = str(vars.get('now'))
except Exception as e:
    info['vars_error'] = str(e)[:120]
try:
    info['result_keys'] = sorted([str(k) for k in result.keys()])
    info['status'] = str(result.get('status'))
    result.pop('header', None)
    d = result.get('data')
    if not isinstance(d, dict):
        d = dict()
        result['data'] = d
    d['probe'] = json.dumps(info)
    d['what_to_do_next'] = 'Reply with the single word next. The caller will now send another DOB or PHONE message: CALL verify_identity for it.'
    if str(d.get('all_verified')).lower() == 'true':
        d['probe_identity'] = 'verified'
        vars['probe_identity_direct'] = 'verified'
except Exception as e:
    pass
"""

INSPECT = """
import json
out = dict()
try:
    p = vars.get('probe_products')
    out['mapped_type'] = str(type(p))
    out['mapped_value'] = str(p)[:300]
    out['account_keys'] = sorted([str(k) for k in vars.keys() if str(k).startswith('account')])[:40]
    out['identity_direct'] = str(vars.get('probe_identity_direct'))
    out['identity_mapped'] = str(vars.get('probe_identity'))
except Exception as e:
    out['error'] = str(e)[:200]
vars['probe_shape'] = json.dumps(out)
"""


def code_node(name, target, code):
    return {"name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": target, "on_enter": {"type": "silent"},
            "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec", "parameters": {"type": "object", "properties": {
                "code": {"type": "string", "default": code}, "timeout_ms": {"type": "number", "default": 10000}}}}}],
            "transitions": [{"target_node": target, "description": "always", "condition": {"operator": "always"}}]}


def api_props(tool, body, required):
    return {"type": "object", "properties": {"url": {"type": "string", "default": f"{BANK}/{tool}"}, "method": {"type": "string", "default": "POST"},
                                             "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                                             "body": {"type": "object", "properties": body, "required": required}}}


WORKFLOW = {
    "initial_node": "setup", "global_nodes": {},
    "system_prompt": "You are a test harness. Follow the task exactly. Keep replies to one word.",
    "nodes": {
        "setup": code_node("setup", "talk", "vars['probe_setup'] = 'ran'\nvars['probe_note'] = 'none yet'\n"),
        "talk": {
            "name": "talk", "node_type": "conversation", "on_enter": {"type": "tts", "text": "Probe ready."}, "on_enter_first_entry_only": True,
            "task_message": "When the user message starts with DOB, CALL verify_identity with field date_of_birth and answer set to the rest of the message, copied exactly. "
                            "When the user message starts with PHONE, CALL verify_identity with field phone_last4 and answer set to the rest of the message, copied exactly. "
                            "After a tool result or a tool error, do what it tells you to do; if it tells you nothing, reply with the single word done. "
                            "When the user asks for the secret word, answer with the secret word. Secret word: {{probe_note}}. Setup marker: {{probe_setup}}.",
            "tools": [{"type": "function", "config": {"timeoutMs": 12000, "maxRetries": 0},
                       "function": {"name": "verify_identity", "handler": "__function__:api_call", "description": "Check one answer. CALL it for every DOB or PHONE message.",
                                    "parameters": api_props("verify_identity", {"customer_id": {"type": "string", "default": "{{customer_id}}"},
                                                                                "field": {"type": "string", "enum": ["date_of_birth", "phone_last4"], "default": "{{field}}", "description": "date_of_birth or phone_last4"},
                                                                                "answer": {"type": "string", "default": "{{answer}}", "description": "The text after DOB or PHONE, copied exactly."}}, ["field", "answer"])},
                       "response_mapping": [{"path": "data.probe_identity", "variable": "probe_identity"}],
                       "pre_process_code": PRE, "post_process_code": POST}],
            "max_node_turns": 30, "max_turn_transition": "end",
            "transitions": [{"target_node": "account", "description": "verified", "condition": {"logic": "or", "conditions": [
                {"variable": "probe_identity", "operator": "equals", "operand": "verified"},
                {"variable": "probe_identity_direct", "operator": "equals", "operand": "verified"}]}}],
        },
        "account": {"name": "account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "inspect", "on_enter": {"type": "silent"},
                    "suppress_tool_results": True,
                    "pre_actions": [{"type": "function", "config": {"timeoutMs": 12000, "maxRetries": 1},
                                     "function": {"name": "get_account", "handler": "__function__:api_call", "description": "Load the products.",
                                                  "parameters": api_props("get_account", {"customer_id": {"type": "string", "default": "{{customer_id}}"}}, [])}}],
                    "response_mapping": [{"path": "data.products", "variable": "probe_products"}],
                    "transitions": [{"target_node": "inspect", "description": "always", "condition": {"operator": "always"}}]},
        "inspect": code_node("inspect", "report", INSPECT),
        "report": {"name": "report", "node_type": "conversation", "on_enter": {"type": "tts", "text": "SHAPE {{probe_shape}}"},
                   "task_message": "Reply with the single word: finished. Do not call a tool in this turn.", "tools": [], "max_node_turns": 5, "max_turn_transition": "end", "transitions": []},
        "end": {"name": "end", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "transitions": [],
                "terminal": {"type": "function", "function": {"name": "probe_end", "handler": "__function__:end_call", "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}}},
    },
}


def main():
    report = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "turns": []}
    f.validate_workflow("probe", WORKFLOW)
    pub = fp.publish("probe", WORKFLOW)
    report["workflow"] = pub
    stored = fp.readback(pub["workflow_id"])["workflow"]
    tool = (stored["nodes"]["talk"].get("tools") or [{}])[0]
    report["readback"] = {"hooks_kept": bool(tool.get("pre_process_code")) and bool(tool.get("post_process_code")), "mapping_kept": tool.get("response_mapping"),
                          "account_pre_actions": len(stored["nodes"]["account"].get("pre_actions") or []), "account_mapping": stored["nodes"]["account"].get("response_mapping")}
    session = http("/v1/practice/sessions", {"case": "PUB-01"})["variables"]
    cid = session["customer_id"]
    report["chats"] = []
    # One fresh chat per blocking mechanism, so that an earlier tool result cannot change what the model does next.
    # The first message of a chat only triggers the opening line, so every chat starts with a greeting.
    for script in (["Hello", "DOB BLOCK-RAISE"], ["Hello", "DOB BLOCK-URL", "What is the secret word?"], ["Hello", "PHONE 4471", "DOB 1988-03-14", "ok"]):
        call_id = None
        for message in script:
            body = {"workflowId": pub["workflow_id"], "message": message, "customerVariables": session}
            if call_id:
                body["callId"] = call_id
            try:
                r = f.api("POST", "/api/v2/chat/message", body)
                call_id = r.get("callId") or call_id
                report["turns"].append({"user": message, "agent": r.get("response")})
            except Exception as e:
                report["turns"].append({"user": message, "error": str(e)[:400]})
            print(report["turns"][-1], flush=True)
        report["chats"].append({"script": script, "events": events_of(call_id)})
        try:
            f.api("POST", "/api/v2/chat/end", {"callId": call_id})
        except Exception:
            pass
    time.sleep(2)
    ledger = http("/v1/practice/ledger/" + cid)
    report["ledger_calls"] = [{"tool": c.get("tool"), "args": c.get("args") or c.get("body") or c.get("request"), "result": c.get("result")} for c in ledger.get("calls", [])]
    out = ROOT / "docs" / "probes" / (time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("\nledger calls:")
    for c in report["ledger_calls"]:
        print(" ", json.dumps(c)[:300])
    print("report:", out)


def events_of(call_id):
    try:
        call = f.get_call(call_id)
        events = []
        for m in call.get("messages", []):
            if m.get("role") == "system":
                try:
                    ev = json.loads(m.get("message"))
                except Exception:
                    ev = {"raw": str(m.get("message"))[:300]}
                kind = ev.get("event")
                if kind in ("function_call_start", "function_call_complete", "error", "node_enter"):
                    events.append({"event": kind, "node": m.get("nodeId"), "detail": json.dumps(ev)[:1500]})
                elif kind == "route_evaluated":
                    table = ev.get("variable_table") or {}
                    probe = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in table.items() if "probe" in k or "_args" in k or k.startswith("account")}
                    events.append({"event": kind, "matched": ev.get("matched"), "target": ev.get("target"), "probe_vars": json.dumps(probe)[:2500]})
            else:
                events.append({"role": m.get("role"), "text": str(m.get("message"))[:400], "node": m.get("nodeId")})
        return events
    except Exception as e:
        return [{"events_error": str(e)[:400]}]


if __name__ == "__main__":
    main()
