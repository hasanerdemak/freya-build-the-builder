"""Deterministic compiler: Policy IR + prompt text + API spec -> Freya workflow JSON.

The node graph, the tool wiring and the hook code are fixed. A brief changes only the data
(the IR embedded in the hooks) and the prose (prompt text written by the developer role).
"""
import copy
import json
import pathlib
import re

from apispec import TOOL_NAMES

RUNTIME_PATH = pathlib.Path(__file__).resolve().parent / "runtime" / "policy_runtime.py"
COMPILER_VERSION = "1"

CONTACT_TOOLS = ["verify_identity", "schedule_callback", "transfer_to_agent", "close_call"]
NEGOTIATE_TOOLS = ["create_promise", "add_note", "transfer_to_agent", "close_call"]

SYSTEM_MECHANICS = """HOW YOU WORK IN THIS CALL
- Write only the words you say on the phone. One or two short sentences per turn, then wait for the other side.
- When a rule tells you to check, record, book, hand over, note or close, CALL the matching tool. Saying it is not enough. Call one tool per message.
- Every tool result contains what_to_do_next. Read it and do exactly what it says. It is always right, also when you expected something else.
- After every tool result, speak to the customer or call the next tool. Never end a turn with nothing.
- A tool answer that is only an error, without what_to_do_next, means the service did not answer and nothing was done. Call the same tool again once with the same values before you say or do anything else.
- Never say that something was checked, recorded, booked, noted or handed over unless a tool result says so.
- An offer the customer has declined is over. Do not repeat or mention it again unless a rule tells you to, also when the customer then asks for something else. If a rule ends the call after that no, answer a last question in one sentence and close in that same message.
- Small talk that has nothing to do with the call (the weather, the traffic, a busy day): do not comment on it, go on with the step you are on. What the customer says about their own situation is not small talk: your rules say how to answer it.
- Never invent a figure, a date, a product or a reason. Use only the facts in your instructions and what the tools return.
- The call is over only when close_call has succeeded. When the conversation is finished, thank the person, say goodbye and CALL close_call in that same message."""

CONTACT_MECHANICS = """THIS PART OF THE CALL: find out who is on the line and, only when it is the customer in person, check their identity.
- You have no account information in this part of the call. Never state or guess a product, an amount, a date or a reason for the call.
- The security questions are only for the customer in person, after they have clearly confirmed who they are. Ask one question at a time, in the listed order.
- Every time the customer answers a security question, CALL verify_identity with that answer. Never judge an answer yourself.
- An answer may come in parts over two turns (day and month, then the year; two digits, then two more). Put the parts together and CALL verify_identity with the whole answer. Do not ask again for a part you have already heard. If you are not sure it is complete, CALL verify_identity with what you have: an incomplete answer is not counted, and the result tells you what is missing.
- When the customer gives an answer together with a question or a complaint, CALL verify_identity with the answer first. Deal with the rest only after its result.
- The result of verify_identity tells you what to do next: follow its what_to_do_next."""

NEGOTIATE_MECHANICS = """THIS PART OF THE CALL: the customer's identity is verified and the account is loaded.
- Products, product ids, amounts and deadlines come only from ACCOUNT FACTS below. Dates come only from the CALENDAR below. Do no arithmetic of your own.
- Record promises one at a time: CALL create_promise for one product, wait for its result, then call it for the next. Never put two tool calls in one message.
- create_promise needs the product id from ACCOUNT FACTS, the amount as a number and the exact date as YYYY-MM-DD. If its result says nothing was recorded, do what what_to_do_next says before anything else.
- If a tool result says a call was not carried out, nothing happened at the bank: correct it as what_to_do_next says.
- While you are still agreeing amounts and dates with the customer, nothing is recorded yet: say "I can record", not "I have recorded". Say a promise is recorded only after the create_promise result says it IS recorded.
- Say a date to the customer the way the CALENDAR writes it, with weekday, day and month. The YYYY-MM-DD form is only for tools."""

# Used only when a snippet fails at run time, so that the agent is never left without guidance or facing a raw placeholder.
FALLBACK_NEXT = "Tell the customer only what this result confirms. Do not claim anything it does not show. Then continue with the rules."
FALLBACK_FACTS_SETUP = [("date_facts", "CALENDAR IS NOT AVAILABLE. Ask the customer for an exact date and say it back before using it."),
                        ("today_facts", "The calendar is not available. Use the date and time of this call given above."),
                        ("account_facts", "No account information is available yet.")]
FALLBACK_ACCOUNT = "ACCOUNT DATA COULD NOT BE LOADED. You know no product and no amount. Do not state or guess any figure. Apologise, say goodbye and close the call."

DEFAULT_TOOL_DESCRIPTIONS = {
    "verify_identity": "Check one security answer with the bank. CALL it every time the customer gives a complete answer to a security question. Never decide yourself whether an answer is right.",
    "get_account": "Load the customer's late products.",
    "create_promise": "Record one payment promise for one product. CALL it once per product, only after the customer clearly confirmed the read-back of product, amount and date.",
    "add_note": "Write the reason why one product ends without a promise. CALL it once per such product when the rules require a note.",
    "schedule_callback": "Book a callback. CALL it when the rules allow a callback and you have an exact day and time.",
    "transfer_to_agent": "Hand the call to a person. CALL it when the rules say the call must be handed over, with the queue the rules name.",
    "close_call": "Close the call at the bank. CALL it exactly once, as the very last action, in the same message as your goodbye. Not calling it leaves the call open.",
}


class CompileError(ValueError):
    pass


def runtime_source(keep=None):
    """The runtime without docstring, comments and blank lines.

    `keep` is the set of block tags a snippet needs (see the "# <only:...>" markers in the runtime).
    None keeps everything."""
    text = RUNTIME_PATH.read_text(encoding="utf-8")
    body = text[text.index("def _rt_main("):]
    lines, stack = [], []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# <only:"):
            tags = set(stripped[len("# <only:"):].rstrip(">").split(","))
            stack.append(keep is None or bool(tags & keep))
            continue
        if stripped == "# </only>":
            stack.pop()
            continue
        if not stripped or stripped.startswith("#") or not all(stack):
            continue
        lines.append(line.rstrip())
    if stack:
        raise CompileError("unbalanced block markers in the runtime source")
    src = "\n".join(lines) + "\n"
    if "{{" in src or "}}" in src:
        raise CompileError("the runtime source contains double curly braces")
    return src


def _safe_json(value):
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
    while "{{" in text or "}}" in text:
        text = text.replace("{{", "{ {").replace("}}", "} }")
    return text


def _check_no_braces(value, where):
    if isinstance(value, str):
        if "{{" in value or "}}" in value:
            raise CompileError(f"double curly braces are not allowed in runtime data ({where})")
    elif isinstance(value, dict):
        for k, v in value.items():
            _check_no_braces(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_no_braces(v, f"{where}[{i}]")


def runtime_config(ir, prompts, spec, options):
    typed = {k: copy.deepcopy(ir.get(k)) for k in ("call_variables", "identity", "calendar", "account", "bands", "persuasion",
                                                   "promise", "callback", "transfer", "notes", "closing", "tools") if ir.get(k) is not None}
    cfg = {"ir": typed, "api": {t: {"url": spec["tools"][t]["url"]} for t in TOOL_NAMES},
           "guidance": {k: v for k, v in (prompts.get("guidance") or {}).items() if v},
           "block_mode": options.get("block_mode", "none"), "pre_verification_block": options.get("pre_verification_block", "none"),
           "read_tool": "get_account", "calendar_days": options.get("calendar_days", 45)}
    _check_no_braces(cfg, "config")
    return cfg


def snippet(cfg_json, hook, tool=""):
    """One self-contained Python snippet for a hook or a code node, with only the runtime parts it needs."""
    src = runtime_source({hook, tool} if tool else {hook})
    entry = {
        "pre": f"    _rt_main('pre', {tool!r}, _c, vars, config, None)\n",
        "post": f"    _rt_main('post', {tool!r}, _c, _v, _g, result)\n",
        "setup": "    _rt_main('setup', '', _c, vars, None, None)\n",
        "plan": "    _rt_main('plan', '', _c, vars, None, None)\n",
    }[hook]
    head = "_c = " + repr(cfg_json) + "\n"
    if hook == "post":
        head += "try:\n    _v = vars\nexcept Exception:\n    _v = None\n"
        # The request of this very call, when the platform exposes it to the post-hook. It identifies the call
        # exactly, also when the agent sends several tool calls in one message and they run at the same time.
        head += "try:\n    _g = config\nexcept Exception:\n    _g = None\n"
    fallback = {
        "pre": "",
        "post": ("    try:\n        _d = result.get('data')\n        if not isinstance(_d, dict):\n            _d = dict()\n            result['data'] = _d\n"
                 "        if not _d.get('what_to_do_next'):\n            _d['what_to_do_next'] = " + repr(FALLBACK_NEXT) + "\n    except Exception:\n        pass\n"),
        "setup": ("    try:\n        for _k, _t in " + repr(FALLBACK_FACTS_SETUP) + ":\n            if not vars.get(_k):\n                vars[_k] = _t\n    except Exception:\n        pass\n"),
        "plan": ("    try:\n        vars['account_facts'] = " + repr(FALLBACK_ACCOUNT) + "\n    except Exception:\n        pass\n"),
    }[hook]
    tail = "try:\n" + entry + "except Exception as _e:\n    if str(_e).startswith('The call was NOT carried out'):\n        raise\n" + fallback
    code = src + head + tail
    if "{{" in code or "}}" in code:
        raise CompileError("a generated snippet contains double curly braces")
    return code


def _role_of(ir, tool, field_name):
    for role, name in ((ir.get("tools") or {}).get(tool, {}).get("roles") or {}).items():
        if name == field_name:
            return role
    return ""


def _body_properties(ir, spec, tool):
    tool_ir = (ir.get("tools") or {}).get(tool) or {}
    consts = {e["name"]: e.get("value") for e in (tool_ir.get("extra_fields") or []) if e.get("name")}
    cust_var = (ir.get("call_variables") or {}).get("customer_id") or "customer_id"
    props, required = {}, []
    for f in spec["tools"][tool]["request"]:
        name, role = f["name"], _role_of(ir, tool, f["name"])
        ftype = f["type"] if f["type"] in ("string", "number", "integer", "boolean", "object", "array") else "string"
        if role == "customer":
            props[name] = {"type": "string", "default": "{{" + cust_var + "}}"}
            continue
        if name in consts and consts[name] not in (None, ""):
            props[name] = {"type": ftype, "default": consts[name]}
            continue
        desc = " ".join(str(f.get("description") or "").split())
        enum = list(f.get("enum") or [])
        if tool == "verify_identity" and role == "field":
            wanted = [q["field"] for q in (ir.get("identity") or {}).get("questions") or []]
            enum = [e for e in enum if e in wanted] or enum or wanted
            desc = "Which security question this answer belongs to."
        elif role == "product":
            desc = "Exactly one product id from ACCOUNT FACTS. Never invent one."
        elif role == "date":
            desc = "The exact calendar date as YYYY-MM-DD, taken from the CALENDAR."
        elif role == "amount":
            desc = "The agreed amount as a number, for example 250.00."
        elif role == "time":
            desc = "The time as HH:MM in 24-hour form."
        prop = {"type": ftype, "default": "{{" + name + "}}"}
        if enum:
            prop["enum"] = enum
            desc = (desc + " One of: " + ", ".join(str(e) for e in enum) + ".").strip()
        if desc:
            prop["description"] = desc
        props[name] = prop
        if f.get("required"):
            required.append(name)
    return props, required


def make_tool(ir, prompts, spec, tool, cfg_json, with_hooks=True, retries=0):
    props, required = _body_properties(ir, spec, tool)
    desc = (prompts.get("tool_descriptions") or {}).get(tool) or DEFAULT_TOOL_DESCRIPTIONS[tool]
    obj = {
        "type": "function",
        "config": {"timeoutMs": 12000, "maxRetries": retries},
        "function": {
            "name": tool, "handler": "__function__:api_call", "description": " ".join(desc.split()),
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string", "default": spec["tools"][tool]["url"]},
                "method": {"type": "string", "default": spec["tools"][tool]["method"]},
                "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                "body": {"type": "object", "properties": props, "required": required},
            }},
        },
    }
    if with_hooks:
        mapping = []
        if tool == "verify_identity":
            mapping.append({"path": "data.rt_identity", "variable": "rt_identity"})
        if tool == "close_call":
            mapping.append({"path": "data.rt_closed", "variable": "rt_closed"})
        if mapping:
            obj["response_mapping"] = mapping
        obj["pre_process_code"] = snippet(cfg_json, "pre", tool)
        obj["post_process_code"] = snippet(cfg_json, "post", tool)
    return obj


def _equals(variable, operand, target, description):
    return {"target_node": target, "description": description,
            "condition": {"logic": "or", "conditions": [{"variable": variable, "operator": "equals", "operand": operand}]}}


def _always(target):
    return {"target_node": target, "description": "always", "condition": {"operator": "always"}}


def _code_node(name, target, code):
    return {"name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": target,
            "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
            "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                                                             "parameters": {"type": "object", "properties": {
                                                                 "code": {"type": "string", "default": code},
                                                                 "timeout_ms": {"type": "number", "default": 10000}}}}}],
            "transitions": [_always(target)]}


def _bullets(items):
    out = []
    for it in items or []:
        text = it.get("text") if isinstance(it, dict) else it
        text = " ".join(str(text or "").split())
        if text:
            out.append("- " + text)
    return "\n".join(out)


def compile_workflow(ir, prompts, spec, options=None):
    options = options or {}
    cfg = runtime_config(ir, prompts, spec, options)
    cfg_json = _safe_json(cfg)
    notes_used = bool((ir.get("notes") or {}).get("used"))
    callback_used = bool((ir.get("callback") or {}).get("used", True))
    transfer_used = bool((ir.get("transfer") or {}).get("queues"))

    def tools_for(names):
        out = []
        for t in names:
            if t == "add_note" and not notes_used:
                continue
            if t == "schedule_callback" and not callback_used:
                continue
            if t == "transfer_to_agent" and not transfer_used:
                continue
            out.append(make_tool(ir, prompts, spec, t, cfg_json))
        return out

    opening = " ".join(str((ir.get("opening") or {}).get("first_line") or "").split())
    if not opening:
        raise CompileError("the IR has no opening line")
    system_prompt = "\n".join(x for x in [_bullets(prompts.get("system_rules")), "", SYSTEM_MECHANICS] if x is not None).strip()
    questions = "\n".join(f'{i + 1}. {q.get("ask") or q["field"]} (check it with field "{q["field"]}")'
                          for i, q in enumerate((ir.get("identity") or {}).get("questions") or []))
    now_var = (ir.get("call_variables") or {}).get("now") or "now"
    now_line = "The date and time of this call is {{" + now_var + "}}. Every date rule uses it, never the real clock."
    contact_task = "\n\n".join([
        CONTACT_MECHANICS,
        "RULES FOR THIS PART OF THE CALL\n" + _bullets(prompts.get("contact_rules")),
        "SECURITY QUESTIONS (only for the customer in person)\n" + questions,
        now_line,
        "{{today_facts}}",
    ])
    negotiate_task = "\n\n".join([
        NEGOTIATE_MECHANICS,
        "RULES FOR THIS PART OF THE CALL\n" + _bullets(prompts.get("negotiate_rules")),
        "{{account_facts}}",
        now_line,
        "{{date_facts}}",
    ])
    products_path = ((ir.get("tools") or {}).get("get_account", {}).get("response") or {}).get("products") or "products"
    account_tool = make_tool(ir, prompts, spec, "get_account", cfg_json, with_hooks=False, retries=2)
    closed = lambda: _equals("rt_closed", "yes", "end", "the call was closed at the bank")
    nodes = {
        "setup": _code_node("setup", "contact", snippet(cfg_json, "setup")),
        "contact": {
            "name": "contact", "node_type": "conversation",
            "on_enter": {"type": "tts", "text": opening}, "on_enter_first_entry_only": True,
            "task_message": contact_task, "tools": tools_for(CONTACT_TOOLS),
            "max_node_turns": 40, "max_turn_transition": "end",
            "transitions": [closed(), _equals("rt_identity", "verified", "account", "all security questions passed")],
        },
        "account": {
            "name": "account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "plan",
            "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "suppress_tool_results": True,
            "pre_actions": [account_tool],
            "response_mapping": [{"path": "data." + products_path, "variable": "rt_products_raw"}],
            "transitions": [_always("plan")],
        },
        "plan": _code_node("plan", "negotiate", snippet(cfg_json, "plan")),
        "negotiate": {
            "name": "negotiate", "node_type": "conversation",
            "task_message": negotiate_task, "tools": tools_for(NEGOTIATE_TOOLS),
            "max_node_turns": 60, "max_turn_transition": "end",
            "transitions": [closed()],
        },
        "end": {
            "name": "end", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
            "transitions": [],
            "terminal": {"type": "function", "function": {"name": "call_closed", "handler": "__function__:end_call",
                                                          "parameters": {"type": "object", "properties": {
                                                              "message_mode": {"type": "string", "default": "none"}}}}},
        },
    }
    workflow = {"initial_node": "setup", "system_prompt": system_prompt, "nodes": nodes, "global_nodes": {}}
    tests = {v["name"]: str(v["example"]) for v in (ir.get("call_variables") or {}).get("others") or []
             if v.get("name") and v.get("example") not in (None, "")}
    if tests:
        workflow["test_variables"] = tests
    return workflow


def stats(workflow):
    nodes = workflow.get("nodes") or {}
    tools = sum(len(n.get("tools") or []) + len(n.get("pre_actions") or []) for n in nodes.values())
    return {"nodes": len(nodes), "tools": tools, "bytes": len(json.dumps(workflow))}


def placeholders(text):
    return re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", text or "")
