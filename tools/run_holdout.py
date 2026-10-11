"""Generalisation holdout: build an invented bank's brief offline and check the result against hand-written expectations.

    python tools/run_holdout.py clean [--reuse RUN_ID]

The expectations below were written by hand from the holdout brief before any build. The bank that the generated
hook code talks to is a fake built from hand-written API bindings, not from the builder's own IR, so a wrong
binding in the IR shows up as a failure. This tests the analyst, the compiler and the runtime on rules and an API
the builder has never seen. It creates no bank customer and says nothing about the phone model.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
import apispec
import compiler
import validate

BINDINGS = {
    "clean": {
        "identity": {"max_wrong_answers": 2},
        "tools": {
            "verify_identity": {"roles": {"customer": "customer_id", "field": "question", "answer": "value"}, "response": {"verified": "correct", "all_verified": "check_passed", "wrong_left": "attempts_left"}, "success": {"field": "ok", "equals": "true"}},
            "get_account": {"roles": {"customer": "customer_id"}, "response": {"products": "accounts", "id": "account_ref"}, "success": {"field": "ok", "equals": "true"}},
            "create_promise": {"roles": {"customer": "customer_id", "product": "account_ref", "amount": "pay_amount", "date": "pay_date"}, "success": {"field": "ok", "equals": "true"}},
            "add_note": {"roles": {"customer": "customer_id", "product": "account_ref", "reason": "code"}, "success": {"field": "ok", "equals": "true"}},
            "schedule_callback": {"roles": {"customer": "customer_id", "date": "callback_date", "time": "callback_time"}, "success": {"field": "ok", "equals": "true"}},
            "transfer_to_agent": {"roles": {"customer": "customer_id", "queue": "team"}, "success": {"field": "ok", "equals": "true"}},
            "close_call": {"roles": {"customer": "customer_id", "outcome": "result", "reason": "detail"}, "success": {"field": "ok", "equals": "true"}},
        },
    },
    "raw": {
        "identity": {"max_wrong_answers": 3},
        "tools": {
            "verify_identity": {"roles": {"customer": "customer_id", "field": "check", "answer": "answer"}, "response": {"verified": "match", "all_verified": "all_passed", "wrong_left": "wrong_remaining"}, "success": {"field": "status", "equals": "ok"}},
            "get_account": {"roles": {"customer": "customer_id"}, "response": {"products": "products", "id": "product_id"}, "success": {"field": "status", "equals": "ok"}},
            "create_promise": {"roles": {"customer": "customer_id", "product": "product_id", "amount": "amount", "date": "due_on"}, "success": {"field": "status", "equals": "ok"}},
            "add_note": {"roles": {"customer": "customer_id", "product": "product_id", "reason": "category"}, "success": {"field": "status", "equals": "ok"}},
            "schedule_callback": {"roles": {"customer": "customer_id", "date": "day", "time": "time"}, "success": {"field": "status", "equals": "ok"}},
            "transfer_to_agent": {"roles": {"customer": "customer_id", "queue": "team"}, "success": {"field": "status", "equals": "ok"}},
            "close_call": {"roles": {"customer": "customer_id", "outcome": "outcome", "reason": "reason"}, "success": {"field": "status", "equals": "ok"}},
        },
    },
}
RAW_CUSTOMER = {"identity": {"company_number": "00123456", "founded_year": "1998"},
                "products": [{"product_id": "L1", "product_type": "business_loan", "amount_past_due": 2400.0, "days_past_due": 30, "min_instalment": 600.0},
                             {"product_id": "C2", "product_type": "business_card", "card_last4": "5521", "amount_past_due": 900.0, "days_past_due": 75, "min_instalment": 150.0}],
                "variables": {"customer_id": "ng_test", "contact_name": "Tom Okafor", "company_name": "Okafor Fittings Ltd", "now": "2028-06-05T09:40"}}
CUSTOMER = {"identity": {"postcode": "AB12CD", "birth_year": "1975"},
            "products": [{"account_ref": "a1", "kind": "overdraft", "overdue_amount": 480.0, "days_overdue": 12},
                         {"account_ref": "a2", "kind": "store_card", "card_last4": "9911", "overdue_amount": 300.0, "days_overdue": 45, "minimum_due": 60.0}],
            "variables": {"customer_id": "hb_test", "full_name": "Irene Vasquez", "now": "2027-03-15T14:20"}}

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(("ok   " if condition else "FAIL ") + name + ("" if condition else f"   [{detail}]"))


def nxt(r):
    return str((r.get("data") or {}).get("what_to_do_next") or r.get("message") or "")


def fresh(wf, spec, faults=None, identity=None):
    bank = validate.FakeBank(BINDINGS["clean"], spec, identity or CUSTOMER["identity"], CUSTOMER["products"], faults)
    return bank, validate.Engine(wf, bank, CUSTOMER["variables"])


def verify(eng):
    eng.call("verify_identity", question="postcode", value="ab1 2cd")
    eng.call("verify_identity", question="birth_year", value="1975")


def clean(run_dir):
    spec = apispec.load(ROOT / "tests" / "holdout" / "clean" / "openapi.json")
    ir, prompts = json.loads((run_dir / "ir.json").read_text(encoding="utf-8")), json.loads((run_dir / "prompts.json").read_text(encoding="utf-8"))
    wf = compiler.compile_workflow(ir, prompts, spec, {"block_mode": "raise", "pre_verification_block": "raise"})
    errors, _ = validate.check_workflow(wf, ir, spec)
    check("workflow passes static checks", not errors, errors[:3])
    check("no callback tool when the brief has no callback", "schedule_callback" not in [t["function"]["name"] for t in wf["nodes"]["contact"]["tools"]])
    check("opening line is the brief's sentence", "Good day, this is Harbor Bank's automated assistant. May I speak with {{full_name}}?" == wf["nodes"]["contact"]["on_enter"]["text"], wf["nodes"]["contact"]["on_enter"]["text"])
    early = (wf["system_prompt"] + wf["nodes"]["contact"]["task_message"]).lower()
    check("no forbidden word in pre-verification text", not any(w in early for w in ("arrears", "overdue", "balance")))

    bank, eng = fresh(wf, spec)
    verify(eng)
    check("postcode is sent in capitals without spaces", bank.calls and bank.calls[0]["args"].get("value") == "AB12CD", bank.calls[:1])
    check("identity pass reaches negotiation", eng.node == "negotiate", eng.node)
    ps = {p["id"]: p for p in eng.state().get("products", [])}
    check("overdraft deadline is 10 calendar days: 2027-03-25", ps.get("a1", {}).get("deadline") == "2027-03-25", ps.get("a1"))
    check("store card deadline is the 5th open day, Saturday open, 17 March closed: 2027-03-22", ps.get("a2", {}).get("deadline") == "2027-03-22", ps.get("a2"))
    check("overdraft floor is 25 percent: 120.00", ps.get("a1", {}).get("floor") == 120.0, ps.get("a1"))
    check("store card floor is minimum_due: 60.00", ps.get("a2", {}).get("floor") == 60.0, ps.get("a2"))
    check("overdraft has one extra offer: floor on 2027-03-16", [(o["amount"], o["date"]) for o in ps.get("a1", {}).get("offers", [])] == [("floor", "2027-03-16")], ps.get("a1", {}).get("offers"))
    check("store card (31+ days) has no extra offer", ps.get("a2", {}).get("offers") == [], ps.get("a2", {}).get("offers"))
    check("store card label names the last four digits", "9911" in ps.get("a2", {}).get("label", ""), ps.get("a2", {}).get("label"))
    r = eng.call("create_promise", account_ref="a1", pay_amount=100, pay_date="2027-03-16")
    check("amount under the floor is not recorded", not bank.promises and "120.00" in nxt(r), nxt(r))
    r = eng.call("create_promise", account_ref="a1", pay_amount=120, pay_date="2027-03-17")
    check("closed date is not recorded and the next open day is offered", not bank.promises and "2027-03-18" in nxt(r), nxt(r))
    r = eng.call("create_promise", account_ref="a1", pay_amount=120, pay_date="2027-03-26")
    check("date after the last allowed date is not recorded", not bank.promises and "2027-03-25" in nxt(r), nxt(r))
    eng.call("create_promise", account_ref="a1", pay_amount=120, pay_date="2027-03-20")
    check("a Saturday inside the limit is recorded", bank.promises == [{"product_id": "a1", "amount": 120.0, "date": "2027-03-20"}], bank.promises)
    r = eng.call("close_call", result="ptp")
    check("closing waits for the note of the other account", not bank.closed and "a2" in nxt(r), nxt(r))
    eng.call("add_note", account_ref="a2", code="declined")
    eng.call("close_call", result="no_ptp", detail="declined")
    check("closing is ptp without detail when a promise was recorded", (bank.outcome, bank.reason) == ("ptp", None), (bank.outcome, bank.reason))

    bank, eng = fresh(wf, spec, faults=[{"tool": "create_promise", "fail_first_n": 9}])
    verify(eng)
    for _ in range(4):
        r = eng.call("create_promise", account_ref="a1", pay_amount=480, pay_date="2027-03-16")
    check("create_promise is tried three times in total, not four", sum(c["tool"] == "create_promise" for c in bank.calls) == 3, sum(c["tool"] == "create_promise" for c in bank.calls))
    eng.call("add_note", account_ref="a1", code="system_fault")
    eng.call("add_note", account_ref="a2", code="settled")
    eng.call("close_call", result="ptp")
    check("closing is no_ptp with the first non-settled note code", (bank.outcome, bank.reason) == ("no_ptp", "system_fault"), (bank.outcome, bank.reason))

    bank, eng = fresh(wf, spec)
    eng.call("verify_identity", question="postcode", value="ZZ99ZZ")
    r = eng.call("verify_identity", question="postcode", value="ZZ98ZZ")
    check("two wrong answers end the check", "FAILED" in nxt(r) and "id_failed" in nxt(r), nxt(r))
    eng.call("close_call", result="no_contact", detail="unidentified")
    check("closing is id_failed / wrong_answers", (bank.outcome, bank.reason) == ("id_failed", "wrong_answers"), (bank.outcome, bank.reason))

    bank, eng = fresh(wf, spec)
    verify(eng)
    eng.call("add_note", account_ref="a1", code="hardship")
    eng.call("transfer_to_agent", team="care")
    eng.call("close_call", result="no_ptp", detail="hardship")
    check("a transfer closes as handed_over / care and does not wait for the other note", (bank.outcome, bank.reason) == ("handed_over", "care") and len(bank.notes) == 1, (bank.outcome, bank.reason, bank.notes))

    bank, eng = fresh(wf, spec)
    verify(eng)
    eng.call("add_note", account_ref="a1", code="settled")
    eng.call("add_note", account_ref="a2", code="settled")
    eng.call("close_call", result="no_ptp", detail="declined")
    check("every account settled closes as settled", (bank.outcome, bank.reason) == ("settled", None), (bank.outcome, bank.reason))

    bank, eng = fresh(wf, spec)
    eng.call("close_call", result="no_contact", detail="third_party")
    check("a third-party call closes with the agent's pair and calls nothing else", [c["tool"] for c in bank.calls] == ["close_call"] and (bank.outcome, bank.reason) == ("no_contact", "third_party"), bank.calls)


def raw(run_dir):
    spec = apispec.load(ROOT / "tests" / "holdout" / "raw" / "openapi.json")
    ir, prompts = json.loads((run_dir / "ir.json").read_text(encoding="utf-8")), json.loads((run_dir / "prompts.json").read_text(encoding="utf-8"))
    wf = compiler.compile_workflow(ir, prompts, spec, {"block_mode": "raise", "pre_verification_block": "raise"})
    errors, _ = validate.check_workflow(wf, ir, spec)
    check("workflow passes static checks", not errors, errors[:3])
    opening = "Hello, this is the virtual assistant of Northgate Savings, calling for {{company_name}}. Am I speaking with {{contact_name}}?"
    check("opening line is the mail's sentence", wf["nodes"]["contact"]["on_enter"]["text"] == opening, wf["nodes"]["contact"]["on_enter"]["text"])
    check("no note tool when the bank does not use notes", "add_note" not in [t["function"]["name"] for t in wf["nodes"]["negotiate"]["tools"]])
    table = {(r["outcome"], r["reason"]) for r in ir["closing"]["table"]}
    check("the retired reason no_answer is not in the closing table", not any(r == "no_answer" for _, r in table), sorted(table))
    check("the closing table has the 12 pairs of the code list", len(table) == 12, sorted(table))
    check("the memo's extra closed day is in the calendar", sorted(ir["calendar"]["holidays"]) == ["2028-06-06", "2028-06-12", "2028-07-03", "2028-12-25"], ir["calendar"]["holidays"])

    def fresh_raw(faults=None):
        bank = validate.FakeBank(BINDINGS["raw"], spec, RAW_CUSTOMER["identity"], RAW_CUSTOMER["products"], faults)
        return bank, validate.Engine(wf, bank, RAW_CUSTOMER["variables"])

    def verify_raw(eng):
        eng.call("verify_identity", check="company_number", answer="00123456")
        eng.call("verify_identity", check="founded_year", answer="1998")

    bank, eng = fresh_raw()
    r = eng.call("verify_identity", check="company_number", answer="0012 34")
    check("an incomplete registration number is not sent", not bank.calls and "NOT" in nxt(r), nxt(r))
    verify_raw(eng)
    check("leading zeros of the registration number are kept", bank.calls and bank.calls[0]["args"].get("answer") == "00123456", bank.calls[:1])
    check("identity pass reaches negotiation (success flag is status=ok)", eng.node == "negotiate", eng.node)
    ps = {p["id"]: p for p in eng.state().get("products", [])}
    check("30 days past due: latest date is the 8th working day, 2028-06-19", ps.get("L1", {}).get("deadline") == "2028-06-19", ps.get("L1"))
    check("75 days past due: memo gives the 3rd working day, 2028-06-09", ps.get("C2", {}).get("deadline") == "2028-06-09", ps.get("C2"))
    check("lowest amount is min_instalment for both products", (ps.get("L1", {}).get("floor"), ps.get("C2", {}).get("floor")) == (600.0, 150.0), (ps.get("L1"), ps.get("C2")))
    check("30 days: one further proposal, lowest amount on the latest date", [(o["amount"], o["date"]) for o in ps.get("L1", {}).get("offers", [])] == [("floor", "2028-06-19")], ps.get("L1", {}).get("offers"))
    check("75 days: no further proposal (memo c)", ps.get("C2", {}).get("offers") == [], ps.get("C2", {}).get("offers"))
    r = eng.call("create_promise", product_id="L1", amount=2400, due_on="2028-06-06")
    check("the memo's closed day 6 June is refused, next working day 7 June offered", not bank.promises and "2028-06-07" in nxt(r), nxt(r))
    r = eng.call("create_promise", product_id="C2", amount=900, due_on="2028-06-13")
    check("card over 60 days: a date after 9 June is refused", not bank.promises and "2028-06-09" in nxt(r), nxt(r))
    eng.call("create_promise", product_id="L1", amount=600, due_on="2028-06-19")
    eng.call("close_call", outcome="no_commitment", reason="declined")
    check("closing is promise_taken with no reason, without any note", (bank.outcome, bank.reason, bank.notes) == ("promise_taken", None, []), (bank.outcome, bank.reason, bank.notes))

    bank, eng = fresh_raw()
    verify_raw(eng)
    eng.call("create_promise", product_id="L1", amount=2400, due_on="2028-06-07")
    eng.call("transfer_to_agent", team="restructure")
    eng.call("close_call", outcome="promise_taken")
    check("a transfer wins over a recorded promise: handed_off / restructure", (bank.outcome, bank.reason) == ("handed_off", "restructure"), (bank.outcome, bank.reason))

    bank, eng = fresh_raw(faults=[{"tool": "create_promise", "fail_first_n": 9}])
    verify_raw(eng)
    for _ in range(3):
        eng.call("create_promise", product_id="L1", amount=2400, due_on="2028-06-07")
    check("the promise tool is tried twice, not three times", sum(c["tool"] == "create_promise" for c in bank.calls) == 2, sum(c["tool"] == "create_promise" for c in bank.calls))
    eng.call("close_call", outcome="no_commitment", reason="declined")
    check("closing is no_commitment / record_failed", (bank.outcome, bank.reason) == ("no_commitment", "record_failed"), (bank.outcome, bank.reason))

    bank, eng = fresh_raw()
    for day, time_, ok in (("2028-06-06", "10:00", False), ("2028-06-07", "15:45", False), ("2028-06-05", "09:00", False), ("2028-06-07", "15:30", True)):
        eng.call("schedule_callback", day=day, time=time_)
        check(f"callback {day} {time_} is {'booked' if ok else 'refused'}", bool(bank.callbacks) == ok, bank.callbacks)
    eng.call("close_call", outcome="unreached", reason="no_slot")
    check("closing is unreached / callback_booked after a booked callback", (bank.outcome, bank.reason) == ("unreached", "callback_booked"), (bank.outcome, bank.reason))

    bank, eng = fresh_raw()
    r = eng.call("close_call", outcome="unreached", reason="no_answer")
    check("the retired reason is refused at closing", not bank.closed, nxt(r))
    eng.call("close_call", outcome="unreached", reason="not_our_customer")
    check("a valid pair closes", (bank.outcome, bank.reason) == ("unreached", "not_our_customer"), (bank.outcome, bank.reason))


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "clean"
    base = ROOT / "tests" / "holdout" / which
    if "--reuse" in sys.argv:
        run_dir = ROOT / "runs" / sys.argv[sys.argv.index("--reuse") + 1]
    else:
        proc = subprocess.run([sys.executable, str(ROOT / "builder" / "build.py"), str(base / "brief.md"), str(base / "openapi.json"), "--offline", "--slot", "holdout-" + which])
        if proc.returncode != 0:
            sys.exit("the offline build failed")
        run_dir = sorted((ROOT / "runs").glob(f"*-holdout-{which}"))[-1]
    print("run:", run_dir.name)
    {"clean": clean, "raw": raw}[which](run_dir)
    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed} / {len(results)} holdout checks passed")
    (run_dir / "holdout_checks.json").write_text(json.dumps([{"check": n, "pass": ok, "detail": str(d)[:400]} for n, ok, d in results], indent=1), encoding="utf-8")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
