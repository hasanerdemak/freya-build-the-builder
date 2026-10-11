"""Static workflow checks and a local engine that runs the generated snippets against a fake bank.

The local engine executes only the builder's own runtime source (never model-written code). It
models the documented Freya behaviour: baked parameters, pre-hook, HTTP envelope, post-hook,
response mapping, routing on variables, passthrough nodes. It proves the guards and the state
logic. It does not prove what the phone model says.
"""
import copy
import json
import re

from apispec import TOOL_NAMES
from compiler import placeholders

RUNTIME_VARS = {"rt_state", "rt_pre", "rt_identity", "rt_closed", "rt_account", "rt_products_raw",
                "date_facts", "today_facts", "account_facts"}


def _walk(value, path="$"):
    yield path, value
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")


def _snippets(workflow):
    for key, node in (workflow.get("nodes") or {}).items():
        for tool in (node.get("tools") or []) + (node.get("pre_actions") or []):
            name = tool.get("function", {}).get("name")
            for hook in ("pre_process_code", "post_process_code"):
                if tool.get(hook):
                    yield f"{key}.{name}.{hook}", tool[hook]
            code = tool.get("function", {}).get("parameters", {}).get("properties", {}).get("code", {}).get("default")
            if code:
                yield f"{key}.code", code


def check_workflow(workflow, ir, spec):
    """Returns (errors, warnings). Errors stop the build."""
    errors, warnings = [], []
    nodes = workflow.get("nodes") or {}
    for path, value in _walk(workflow):
        if value is None:
            errors.append(f"null value at {path}")
    if workflow.get("initial_node") not in nodes:
        errors.append("initial_node is not a node")
    if not any(n.get("node_type") == "end_call" for n in nodes.values()):
        errors.append("no end_call node")
    call_vars = {v.get("name") for v in (ir.get("call_variables") or {}).get("others") or []}
    call_vars |= {(ir.get("call_variables") or {}).get("customer_id"), (ir.get("call_variables") or {}).get("now")}
    known_vars = RUNTIME_VARS | call_vars
    for key, node in nodes.items():
        ntype = node.get("node_type")
        if node.get("name") != key:
            errors.append(f"node {key}: name differs from key")
        if node.get("tools") and ntype != "conversation":
            errors.append(f"node {key}: tools on a {ntype} node are dropped by the platform")
        if node.get("pre_actions") and ntype not in ("api_request", "code"):
            errors.append(f"node {key}: pre_actions on a {ntype} node are dropped by the platform")
        targets = [t.get("target_node") for t in node.get("transitions") or []]
        for extra in ("passthrough_default_transition", "max_turn_transition"):
            if node.get(extra):
                targets.append(node[extra])
        for target in targets:
            if target not in nodes:
                errors.append(f"node {key}: transition to unknown node {target}")
        if len(set(t.get("target_node") for t in node.get("transitions") or [])) != len(node.get("transitions") or []):
            errors.append(f"node {key}: two transitions share a target and would be merged by the engine")
        if node.get("passthrough"):
            last = (node.get("transitions") or [{}])[-1]
            if (last.get("condition") or {}).get("operator") != "always":
                errors.append(f"node {key}: a passthrough node must end with an always transition")
        if ntype == "conversation" and not any(t.get("function", {}).get("name") == "close_call" for t in node.get("tools") or []):
            errors.append(f"node {key}: a conversation node without close_call cannot end the call")
        for text_key in ("task_message", "loop_prompt"):
            for ph in placeholders(node.get(text_key) or ""):
                if ph not in known_vars:
                    errors.append(f"node {key}: prompt uses unknown variable {ph}")
        for ph in placeholders((node.get("on_enter") or {}).get("text") or ""):
            if ph not in call_vars:
                errors.append(f"node {key}: the opening line uses {ph}, which is not a call variable")
        for tool in (node.get("tools") or []) + (node.get("pre_actions") or []):
            fn = tool.get("function") or {}
            if fn.get("handler") != "__function__:api_call":
                continue
            name = fn.get("name")
            if name not in TOOL_NAMES:
                errors.append(f"node {key}: tool {name} is not one of the bank tools")
                continue
            props = fn.get("parameters", {}).get("properties", {})
            url = props.get("url", {}).get("default") or ""
            if url != spec["tools"][name]["url"]:
                errors.append(f"node {key}: tool {name} points outside the bank tool API ({url})")
            if "const" in json.dumps(props.get("url", {})):
                errors.append(f"node {key}: tool {name} uses const")
            if tool.get("config", {}).get("maxRetries", 0) and name != "get_account":
                errors.append(f"node {key}: write tool {name} has automatic retries")
            spec_fields = {f["name"]: f for f in spec["tools"][name]["request"]}
            body = props.get("body", {}).get("properties", {})
            for missing in set(spec_fields) - set(body):
                if spec_fields[missing].get("required"):
                    errors.append(f"tool {name}: required field {missing} is not sent")
            for fname, prop in body.items():
                if fname not in spec_fields:
                    errors.append(f"tool {name}: field {fname} is not in the tool API")
                    continue
                default = prop.get("default")
                baked_on_purpose = fname == ((ir.get("tools") or {}).get(name, {}).get("roles") or {}).get("customer")
                if isinstance(default, str) and default == "{{" + fname + "}}" and fname in known_vars and not baked_on_purpose:
                    errors.append(f"tool {name}: model-filled field {fname} collides with a variable and would be baked")
                if prop.get("enum") and spec_fields[fname].get("enum") and not set(prop["enum"]) <= set(spec_fields[fname]["enum"]):
                    errors.append(f"tool {name}: field {fname} offers values the tool API does not allow")
    for where, code in _snippets(workflow):
        if "{{" in code or "}}" in code:
            errors.append(f"{where}: double curly braces in code")
        try:
            compile(code, where, "exec")
        except SyntaxError as e:
            errors.append(f"{where}: syntax error {e}")
    for ph in placeholders(workflow.get("system_prompt") or ""):
        errors.append(f"system_prompt uses a variable ({ph})")
    forbidden = [w.lower() for w in (ir.get("opening") or {}).get("forbidden_before_verification") or [] if len(w) > 2]
    contact = nodes.get("contact") or {}
    early_text = " ".join([(contact.get("on_enter") or {}).get("text") or "", workflow.get("system_prompt") or "", contact.get("task_message") or ""]).lower()
    for word in forbidden:
        if re.search(r"\b" + re.escape(word) + r"\b", early_text):
            warnings.append(f"pre-verification text contains the word '{word}', which the brief forbids before verification")
    size = len(json.dumps(workflow))
    if size > 900_000:
        warnings.append(f"workflow is large ({size} bytes)")
    return errors, warnings


def check_ir(ir, spec, brief_text=None):
    """Deterministic IR checks against the supplied tool API (and the brief for quotes)."""
    errors, warnings = [], []
    tools = ir.get("tools") or {}
    for t in TOOL_NAMES:
        fields = {f["name"]: f for f in spec["tools"][t]["request"]}
        resp = {f["name"]: f for f in spec["tools"][t]["response"]}
        tool = tools.get(t)
        if not tool:
            errors.append(f"tools.{t}: missing binding")
            continue
        for role, name in (tool.get("roles") or {}).items():
            if name and name not in fields:
                errors.append(f"tools.{t}.roles.{role}: field {name!r} is not in the request schema")
        suc = (tool.get("success") or {}).get("field")
        if suc and resp and suc not in resp:
            errors.append(f"tools.{t}.success.field: {suc!r} is not in the response schema")
        if not resp:
            warnings.append(f"tools.{t}: the tool API documents no response fields; bindings for its result cannot be checked")
        for e in tool.get("extra_fields") or []:
            if e.get("name") and e["name"] not in fields:
                errors.append(f"tools.{t}.extra_fields: {e['name']!r} is not in the request schema")
        roles = set((tool.get("roles") or {}).values())
        for name, f in fields.items():
            if f.get("required") and name not in roles and name not in {e.get("name") for e in tool.get("extra_fields") or []}:
                warnings.append(f"tools.{t}: required field {name!r} has no role; the phone model fills it from its description")

    def need_roles(tool, names):
        have = (tools.get(tool) or {}).get("roles") or {}
        for n in names:
            if not have.get(n):
                errors.append(f"tools.{tool}.roles.{n}: missing")

    need_roles("verify_identity", ["field", "answer"])
    need_roles("create_promise", ["product", "amount", "date"])
    need_roles("close_call", ["outcome"])
    for t in TOOL_NAMES:
        need_roles(t, ["customer"])

    def enum_of(tool, role):
        name = ((tools.get(tool) or {}).get("roles") or {}).get(role)
        for f in spec["tools"][tool]["request"]:
            if f["name"] == name:
                return f.get("enum")
        return None

    vf = enum_of("verify_identity", "field")
    questions = (ir.get("identity") or {}).get("questions") or []
    if not questions:
        errors.append("identity.questions: empty")
    for q in questions:
        if vf and q.get("field") not in vf:
            errors.append(f"identity.questions: field {q.get('field')!r} is not an allowed value of verify_identity")
        if q.get("format") not in ("date", "digits", "year", "text"):
            errors.append(f"identity.questions: unknown format {q.get('format')!r}")
    outs, reasons = enum_of("close_call", "outcome"), enum_of("close_call", "reason")
    table = (ir.get("closing") or {}).get("table") or []
    if not table:
        errors.append("closing.table: empty")
    pairs = {(r.get("outcome"), r.get("reason") or "") for r in table}
    for row in table:
        if outs and row.get("outcome") not in outs:
            errors.append(f"closing.table: outcome {row.get('outcome')!r} is not allowed by close_call")
        if reasons and row.get("reason") and row.get("reason") not in reasons:
            errors.append(f"closing.table: reason {row.get('reason')!r} is not allowed by close_call")
    queues = enum_of("transfer_to_agent", "queue")
    for q in (ir.get("transfer") or {}).get("queues") or []:
        if queues and q.get("queue") not in queues:
            errors.append(f"transfer.queues: {q.get('queue')!r} is not allowed by transfer_to_agent")
        if (q.get("close_outcome"), q.get("close_reason") or "") not in pairs:
            errors.append(f"transfer.queues: closing pair for {q.get('queue')!r} is not in the closing table")
    note_reasons = enum_of("add_note", "reason")
    for r in (ir.get("notes") or {}).get("reasons") or []:
        if note_reasons and r not in note_reasons:
            errors.append(f"notes.reasons: {r!r} is not allowed by add_note")
    known_when = {"transfer_done", "promise_recorded", "callback_scheduled", "identity_failed", "promise_failed",
                  "all_products_noted_with", "any_note"}
    for rule in (ir.get("closing") or {}).get("precedence") or []:
        if rule.get("when") not in known_when:
            errors.append(f"closing.precedence: unknown predicate {rule.get('when')!r}")
        src = rule.get("reason_source") or ""
        if src not in ("", "transfer_queue", "first_note_not"):
            errors.append(f"closing.precedence: unknown reason_source {src!r}")
        if not src and (rule.get("outcome"), rule.get("reason") or "") not in pairs:
            errors.append(f"closing.precedence: pair {rule.get('outcome')!r}/{rule.get('reason')!r} is not in the closing table")
    date_kinds = {"today", "business_days_after_today", "calendar_days_after_today", "last_business_day_of_week", "end_of_month", "fixed", "none"}
    for b in ir.get("bands") or []:
        if (b.get("deadline") or {}).get("kind") not in date_kinds:
            errors.append(f"bands.{b.get('name')}: unknown deadline kind {(b.get('deadline') or {}).get('kind')!r}")
    for a in ir.get("persuasion") or []:
        if (a.get("date") or {}).get("kind") not in date_kinds | {"deadline"}:
            errors.append(f"persuasion attempt {a.get('attempt')}: unknown date kind")
        if a.get("amount") not in ("full", "floor", "full_or_floor_if_declined"):
            errors.append(f"persuasion attempt {a.get('attempt')}: unknown amount kind {a.get('amount')!r}")
    for e in (ir.get("callback") or {}).get("allowed_days") or []:
        if e.get("kind") not in date_kinds:
            errors.append("callback.allowed_days: unknown date kind")
    for h in (ir.get("calendar") or {}).get("holidays") or []:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(h)):
            errors.append(f"calendar.holidays: {h!r} is not an ISO date")
    acc = (tools.get("get_account") or {}).get("response") or {}
    plist = next((f for f in spec["tools"]["get_account"]["response"] if f["name"] == acc.get("products")), None)
    if not plist:
        errors.append("tools.get_account.response.products: not a field of the get_account response")
    else:
        item_fields = {f["name"] for f in plist.get("items") or []}
        for role in ("id", "type", "due", "late"):
            if acc.get(role) and item_fields and acc[role] not in item_fields:
                errors.append(f"tools.get_account.response.{role}: {acc[role]!r} is not a product field")
        type_enum = next((f.get("enum") for f in plist.get("items") or [] if f["name"] == acc.get("type")), None)
        for pt in (ir.get("account") or {}).get("product_types") or []:
            if type_enum and pt.get("type") not in type_enum and pt.get("type") not in ("*", "default", "any"):
                errors.append(f"account.product_types: {pt.get('type')!r} is not a product type of the tool API")
            fl = pt.get("floor") or {}
            if fl.get("kind") in ("field", "percent_of_field") and item_fields and fl.get("field") not in item_fields:
                errors.append(f"account.product_types.{pt.get('type')}: floor field {fl.get('field')!r} is not a product field")
            for ph in re.findall(r"\{(\w+)\}", pt.get("label") or ""):
                if item_fields and ph not in item_fields:
                    errors.append(f"account.product_types.{pt.get('type')}: label uses unknown product field {ph!r}")
        for b in ir.get("bands") or []:
            if b.get("field") and item_fields and b["field"] not in item_fields:
                errors.append(f"bands.{b.get('name')}: field {b['field']!r} is not a product field")
    if brief_text is not None:
        flat = " ".join(brief_text.split()).lower()
        for rule in ir.get("rules") or []:
            quote = " ".join(str(rule.get("quote") or "").split()).lower().strip(" .\"'")
            if quote and quote not in flat:
                warnings.append(f"rule {rule.get('id')}: quote not found verbatim in the brief")
    return errors, warnings


# ---------------- local engine ----------------

class FakeBank:
    """A stand-in for the bank that follows the supplied tool API. It enforces nothing from the brief."""

    def __init__(self, ir, spec, identity, products, faults=None, refuse_second_promise=False):
        self.ir, self.spec = ir, spec
        self.refuse_second_promise = refuse_second_promise      # the practice bank does; a stand-in need not
        self.identity, self.products = dict(identity), copy.deepcopy(products)
        self.faults = {f["tool"]: f["fail_first_n"] for f in faults or []}
        self.passed, self.wrong, self.closed = set(), 0, False
        self.calls, self.promises, self.notes, self.callbacks, self.transfers = [], [], [], [], []
        self.outcome = self.reason = None
        acc = ir["tools"]["get_account"]["response"]
        for i, p in enumerate(self.products):
            p.setdefault(acc.get("id") or "product_id", f"p{i + 1}")

    def _role(self, tool, role):
        return (self.ir["tools"][tool].get("roles") or {}).get(role) or role

    def _ok(self, tool, extra=None, ok=True):
        suc = self.ir["tools"][tool].get("success") or {}
        want = str(suc.get("equals") or "true")
        out = {suc.get("field") or "ok": ok if want.lower() == "true" else (want if ok else "error")}
        out.update(extra or {})
        return out

    def handle(self, tool, body):
        status, data = self._handle(tool, body)
        self.calls.append({"tool": tool, "args": copy.deepcopy(body), "result": data, "status": status})
        return status, data

    def _handle(self, tool, body):
        if self.closed:
            return 200, self._ok(tool, {"error": "call closed"}, ok=False)
        if self.faults.get(tool, 0) > 0:
            self.faults[tool] -= 1
            return 500, {"error": "service failed"}
        vr = self.ir["tools"]["verify_identity"].get("response") or {}
        max_wrong = int((self.ir.get("identity") or {}).get("max_wrong_answers") or 3)
        if tool == "verify_identity":
            field, answer = body.get(self._role(tool, "field")), str(body.get(self._role(tool, "answer")))
            if field not in self.identity:
                return 200, self._ok(tool, {"error": "unknown field"}, ok=False)
            if self.wrong >= max_wrong:
                return 200, self._ok(tool, {"error": "check ended"}, ok=False)
            good = answer == str(self.identity[field])
            if good:
                self.passed.add(field)
            else:
                self.wrong += 1
            return 200, self._ok(tool, {vr.get("verified") or "verified": good, vr.get("all_verified") or "all_verified": self.passed == set(self.identity),
                                         vr.get("wrong_left") or "wrong_answers_left": max_wrong - self.wrong})
        verified = self.passed == set(self.identity)
        if tool == "get_account":
            if not verified:
                return 200, self._ok(tool, {"error": "identity not verified"}, ok=False)
            return 200, self._ok(tool, {self.ir["tools"][tool]["response"].get("products") or "products": copy.deepcopy(self.products)})
        if tool == "create_promise":
            if not verified:
                return 200, self._ok(tool, {"error": "identity not verified"}, ok=False)
            pid = body.get(self._role(tool, "product"))
            if pid not in [p[self.ir["tools"]["get_account"]["response"].get("id") or "product_id"] for p in self.products]:
                return 200, self._ok(tool, {"error": "unknown product_id"}, ok=False)
            if self.refuse_second_promise and any(x["product_id"] == pid for x in self.promises):
                return 200, self._ok(tool, {"error": "this product already has a promise"}, ok=False)
            self.promises.append({"product_id": pid, "amount": body.get(self._role(tool, "amount")), "date": body.get(self._role(tool, "date"))})
            return 200, self._ok(tool, {"promise_id": f"pr{len(self.promises)}"})
        if tool == "add_note":
            self.notes.append({"product_id": body.get(self._role(tool, "product")), "reason": body.get(self._role(tool, "reason"))})
        elif tool == "schedule_callback":
            self.callbacks.append({"date": body.get(self._role(tool, "date")), "time": body.get(self._role(tool, "time"))})
        elif tool == "transfer_to_agent":
            self.transfers.append({"queue": body.get(self._role(tool, "queue"))})
        elif tool == "close_call":
            self.closed = True
            self.outcome, self.reason = body.get(self._role(tool, "outcome")), body.get(self._role(tool, "reason"))
        return 200, self._ok(tool)

    def ledger(self):
        return {"outcome": self.outcome, "reason": self.reason, "promises": self.promises, "notes": self.notes,
                "callbacks": self.callbacks, "transfers": self.transfers,
                "calls": [{"tool": c["tool"], "result": c["result"] if isinstance(c["result"], dict) else {}} for c in self.calls]}


class Engine:
    """Minimal model of the documented workflow engine, enough to run hooks, mappings and routing."""

    def __init__(self, workflow, bank, call_vars, pre_vars_persist=True, flatten_args=True, split_scopes=False, pre_rewrites_apply=True):
        self.wf, self.bank = workflow, bank
        self.vars = dict(call_vars)
        self.pre_vars_persist, self.flatten_args, self.split_scopes = pre_vars_persist, flatten_args, split_scopes
        self.pre_rewrites_apply = pre_rewrites_apply      # False models a platform that ignores what a pre-hook does to the request
        self.unanswered = {}                              # tool name -> how many of its next requests get no usable answer
        self.lost_answers = {}                            # tool name -> how many of its next requests are carried out but their answer is lost
        self.post_sees_request = False                    # whether the post-hook can read the request of its own call (not documented)
        self.racing_posts = False                         # measured: post-hooks of one message can each save the state they started from
        self.trace = []
        self.node = workflow["initial_node"]
        self._enter(self.node)

    def _exec(self, code, names):
        if self.split_scopes:
            exec(code, {"__builtins__": __builtins__}, names)
        else:
            exec(code, names)

    def _flatten(self, prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                self._flatten(f"{prefix}.{k}", v)
        elif isinstance(value, bool):
            self.vars[prefix] = "true" if value else "false"
        elif isinstance(value, list):
            for i, v in enumerate(value):
                self._flatten(f"{prefix}.{i}", v)
        else:
            self.vars[prefix] = value

    @staticmethod
    def _path(result, path):
        cur = result
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    def _resolve(self, default):
        if isinstance(default, str):
            m = re.fullmatch(r"\{\{(\w+)\}\}", default)
            if m:
                return self.vars.get(m.group(1))
        return default

    def _http(self, tool, config):
        if not config.get("url"):
            return {"status": "error", "message": "URL is required", "data": None}
        name = config["url"].rstrip("/").split("/")[-1]
        status, data = self.bank.handle(name, config.get("body") or {})
        return {"status": "success" if status < 400 else "error", "data": data, "message": "Successfully executed POST request" if status < 400 else "Request failed",
                "status_code": status, "header": {"Content-Type": "application/json"}}

    def _enter(self, key):
        hops = 0
        while True:
            hops += 1
            if hops > 20:
                raise RuntimeError("passthrough loop")
            self.node = key
            node = self.wf["nodes"][key]
            self.trace.append(("enter", key))
            if node["node_type"] == "code":
                code = node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
                self._exec(code, {"vars": self.vars})
            elif node["node_type"] == "api_request":
                for action in node.get("pre_actions") or []:
                    props = action["function"]["parameters"]["properties"]
                    body = {k: self._resolve(p.get("default")) for k, p in props["body"]["properties"].items()}
                    body = {k: v for k, v in body.items() if v is not None}
                    result = self._http(action["function"]["name"], {"url": props["url"]["default"], "body": body})
                    self._flatten(f"{key}_response", result)
                    for m in node.get("response_mapping") or []:
                        v = self._path(result, m["path"])
                        if v is not None and not isinstance(v, (list, dict)):      # measured: a mapped list does not arrive
                            self.vars[m["variable"]] = v
            if not node.get("passthrough"):
                return
            key = self._route(node) or node.get("passthrough_default_transition")

    def _cond(self, cond):
        if cond.get("operator") == "always":
            return True
        if "conditions" in cond:
            results = [self._cond(c) for c in cond["conditions"]]
            return all(results) if cond.get("logic") == "and" else any(results)
        value = self.vars.get(cond.get("variable"))
        if cond.get("operator") == "equals":
            return str(value) == str(cond.get("operand"))
        if cond.get("operator") == "is_set":
            return value is not None
        return False

    def _route(self, node):
        for t in node.get("transitions") or []:
            if self._cond(t["condition"]):
                return t["target_node"]
        return None

    def tool_names(self):
        return [t["function"]["name"] for t in self.wf["nodes"][self.node].get("tools") or []]

    def call(self, name, **args):
        """What happens when the dialogue model calls a tool. Returns the result the model would see."""
        node = self.wf["nodes"][self.node]
        tool = next((t for t in node.get("tools") or [] if t["function"]["name"] == name), None)
        if tool is None:
            return {"status": "error", "message": f"The function `{name}` is not currently available."}
        props = tool["function"]["parameters"]["properties"]
        body = {}
        for k, p in props["body"]["properties"].items():
            baked = self._resolve(p.get("default"))
            if baked is not None and not (isinstance(p.get("default"), str) and p["default"] == "{{" + k + "}}" and k not in self.vars):
                body[k] = baked
        body.update(args)
        config = {"url": props["url"]["default"], "method": "POST", "headers": {"Content-Type": "application/json"}, "body": body}
        pre_vars = self.vars if self.pre_vars_persist else dict(self.vars)
        raised, original = None, copy.deepcopy(config)
        try:
            self._exec(tool["pre_process_code"], {"vars": pre_vars, "config": config})
        except Exception as e:
            raised = e
        if not self.pre_rewrites_apply:
            config, raised = original, None
        # Measured on Freya (docs/PLATFORM_PROBES.md): after an exception or an empty URL in the pre-hook no request is
        # sent and the post-hook does not run; the agent reads the exception message. Tool arguments appear in the
        # variable table only after the post-hook, so inside it they are those of the previous call.
        if raised or not config.get("url"):
            result = {"status": "error", "message": str(raised) if raised else "URL is required", "data": None}
        elif self.unanswered.get(name):
            self.unanswered[name] -= 1
            result = {"status": "error", "message": "API request failed: <!DOCTYPE html>", "data": None}
        elif self.lost_answers.get(name):
            # Measured on Freya: the request reached the bank, then the sandbox for the post-hook could not be reached.
            self.lost_answers[name] -= 1
            self._http(name, config)
            result = {"status": "error", "message": "Failed to reach snekbox: [Errno 104] Connection reset by peer", "data": None}
        else:
            result = self._http(name, config)
            if (result.get("status_code") or 200) >= 400:
                # Measured on Freya (PUB-10 and a gateway error page): a bank answer with an HTTP error status reaches
                # the agent as a bare error. The post-hook does not run and nothing is mapped.
                result = {"status": "error", "message": f"API request failed: {result.get('data')}", "data": None}
            else:
                names = {"vars": self.vars, "result": result}
                if self.post_sees_request:
                    names["config"] = config
                self._exec(tool["post_process_code"], names)
                for m in tool.get("response_mapping") or []:
                    v = self._path(result, m["path"])
                    if v is not None:
                        self.vars[m["variable"]] = v
        if self.flatten_args:
            for k, v in args.items():
                self.vars[f"{name}_args.{k}"] = v
        self.trace.append(("tool", name, copy.deepcopy(config.get("body")), copy.deepcopy(result)))
        target = self._route(node)
        if target and target != self.node:
            self._enter(target)
        return result

    def call_batch(self, calls):
        """Several tool calls in one message, as measured on Freya: every pre-hook runs before any answer comes back."""
        node = self.wf["nodes"][self.node]
        started = []
        for name, args in calls:
            tool = next(t for t in node.get("tools") or [] if t["function"]["name"] == name)
            props = tool["function"]["parameters"]["properties"]
            body = {}
            for k, p in props["body"]["properties"].items():
                baked = self._resolve(p.get("default"))
                if baked is not None and not (isinstance(p.get("default"), str) and p["default"] == "{{" + k + "}}" and k not in self.vars):
                    body[k] = baked
            body.update(args)
            config = {"url": props["url"]["default"], "method": "POST", "headers": {"Content-Type": "application/json"}, "body": body}
            try:
                self._exec(tool["pre_process_code"], {"vars": self.vars, "config": config})
                started.append((name, tool, config, None))
            except Exception as e:
                started.append((name, tool, config, e))
        results = []
        snapshot, merged = dict(self.vars), {}      # measured: every post-hook of the batch sees the variables as they were after the pre-hooks
        for name, tool, config, raised in started:
            if raised:
                results.append({"status": "error", "message": str(raised), "data": None})
                continue
            result = self._http(name, config)
            seen = dict(snapshot) if self.racing_posts else self.vars
            names = {"vars": seen, "result": result}
            if self.post_sees_request:
                names["config"] = config
            self._exec(tool["post_process_code"], names)
            if self.racing_posts:
                merged.update({k: v for k, v in seen.items() if snapshot.get(k) != v})
            results.append(result)
        if self.racing_posts:
            self.vars.update(merged)
        target = self._route(node)
        if target and target != self.node:
            self._enter(target)
        return results

    def state(self):
        try:
            return json.loads(self.vars.get("rt_state") or "{}")
        except Exception:
            return {}
