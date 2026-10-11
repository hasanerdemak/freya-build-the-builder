"""JSON schemas for the three model roles, and normalisation of the analyst output into the IR."""

STR = {"type": "string"}
INT = {"type": "integer"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}


def S(**props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def A(item):
    return {"type": "array", "items": item}


def E(*values):
    return {"type": "string", "enum": list(values)}


DATE_KINDS = ("today", "business_days_after_today", "calendar_days_after_today", "last_business_day_of_week", "end_of_month", "fixed", "none")
DATE_EXPR = S(kind=E(*DATE_KINDS), n=INT, week_offset=INT, roll_if_today=BOOL,
              adjust=E("none", "next_business_day", "previous_business_day"), date=STR)
OFFER_DATE_EXPR = S(kind=E(*DATE_KINDS, "deadline"), n=INT, week_offset=INT, roll_if_today=BOOL,
                    adjust=E("none", "next_business_day", "previous_business_day"), date=STR)
AMOUNT_EXPR = S(kind=E("field", "percent_of_field", "fixed", "full"), field=STR, percent=NUM, amount=NUM)
PAIRS = A(S(role=STR, field=STR))
TOOL_BINDING = S(roles=PAIRS, response=PAIRS, success=S(field=STR, equals=STR), extra_fields=A(S(name=STR, value=STR)))

GUIDANCE_KEYS = ["id_correct_next", "id_all_verified", "id_wrong_retry", "id_failed", "id_unreadable", "id_unreadable_date",
                 "id_incomplete", "promise_ok", "promise_fail_retry", "promise_fail_final", "promise_duplicate",
                 "amount_below_floor", "date_after_deadline", "date_not_business_day", "callback_ok", "callback_outside_window",
                 "transfer_ok", "notes_pending", "account_unavailable", "farewell", "close_choose", "tool_error_retry"]
TOOLS = ["verify_identity", "get_account", "create_promise", "add_note", "schedule_callback", "transfer_to_agent", "close_call"]

IR_SCHEMA = S(
    meta=S(bank_name=STR, brief_kind=E("clean", "raw"), summary=STR),
    call_variables=S(customer_id=STR, now=STR, others=A(S(name=STR, meaning=STR, example=STR))),
    opening=S(first_line=STR, forbidden_before_verification=A(STR)),
    identity=S(ordered=BOOL, max_wrong_answers=INT,
               questions=A(S(field=STR, ask=STR, format=E("date", "digits", "year", "text"), length=INT,
                             normalize=A(E("strip_spaces", "uppercase", "lowercase", "digits_only")),
                             numeric_date_order=E("dmy", "mdy", "ymd", ""), wire_format=STR)),
               hints=A(S(value=STR, instruction=STR, counts_as_wrong=BOOL))),
    calendar=S(weekend_days=A(STR), holidays=A(STR), week_start=STR,
               this_weekday=E("current_week", "next_occurrence", "unspecified"),
               next_weekday=E("following_week", "next_occurrence", "unspecified")),
    account=S(product_types=A(S(type=STR, label=STR, floor=AMOUNT_EXPR, floor_label=STR))),
    bands=A(S(name=STR, field=STR, min=NUM, max=NUM, deadline=DATE_EXPR, persuasion_attempts=INT)),
    persuasion=A(S(attempt=INT, bands=A(STR), amount=E("full", "floor", "full_or_floor_if_declined"), date=OFFER_DATE_EXPR)),
    promise=S(min_amount=E("floor", "none"), max_amount=E("full", "none"), business_day_required=BOOL, not_after_deadline=BOOL,
              not_before_today=BOOL, one_per_product=BOOL, max_tool_attempts=INT, wire_format=STR, success_sentence=STR,
              confirmation=E("per_product", "batch")),
    callback=S(used=BOOL, allowed_days=A(DATE_EXPR), earliest_time=STR, latest_time=STR, after_now=BOOL, wire_format=STR),
    transfer=S(queues=A(S(queue=STR, close_outcome=STR, close_reason=STR)), notes_on_transfer=E("forbidden", "not_required", "required")),
    notes=S(used=BOOL, per_product_without_promise=BOOL, reasons=A(STR), timing=E("end_of_call", "immediately")),
    closing=S(table=A(S(outcome=STR, reason=STR)),
              precedence=A(S(when=E("transfer_done", "promise_recorded", "callback_scheduled", "identity_failed", "promise_failed",
                                    "all_products_noted_with", "any_note"),
                             value=STR, outcome=STR, reason=STR, reason_source=E("", "transfer_queue", "first_note_not")))),
    tools=S(**{t: TOOL_BINDING for t in TOOLS}),
    rules=A(S(id=STR, section=STR, quote=STR, kind=E("obligation", "prohibition", "exception", "definition", "override"),
              summary=STR, overrides=STR, enforced_by=E("code", "prompt", "both"), stage=E("contact", "negotiate", "both"))),
    unmodeled_rules=A(S(id=STR, reason=STR)),
    open_questions=A(STR),
)

PROMPTS_SCHEMA = S(
    system_rules=A(STR),
    contact_rules=A(S(text=STR, rules=A(STR))),
    negotiate_rules=A(S(text=STR, rules=A(STR))),
    guidance=S(**{k: STR for k in GUIDANCE_KEYS}),
    tool_descriptions=S(**{t: STR for t in TOOLS if t != "get_account"}),
)

ISSUES_SCHEMA = S(
    verdict=E("pass", "repair"),
    checked=A(STR),
    issues=A(S(id=STR, severity=E("critical", "major", "minor"), target=E("ir", "prompts"), path=STR, problem=STR,
               evidence_quote=STR, fix=STR)),
)


def normalize_ir(raw):
    """Turn the role/field pair lists of the model output into maps. Idempotent."""
    ir = dict(raw)
    tools = {}
    for name, binding in (raw.get("tools") or {}).items():
        b = dict(binding)
        for key in ("roles", "response"):
            if isinstance(b.get(key), list):
                b[key] = {p["role"]: p["field"] for p in b[key] if p.get("role") and p.get("field")}
        b["extra_fields"] = [e for e in b.get("extra_fields") or [] if e.get("name")]
        tools[name] = b
    ir["tools"] = tools
    return ir


def blocking_issues(issues):
    return [i for i in issues or [] if i.get("severity") in ("critical", "major")]
