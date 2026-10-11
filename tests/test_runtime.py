"""Runtime scenarios: the generated snippets driven against a fake bank.

Each public case is played by a scripted caller that makes the tool calls a correct phone agent
would make. The ledger is then scored with the kit's own scorer. Further tests feed invalid calls
and check that the guards stop them. This proves the hook logic, not the phone model.
"""
import copy
import itertools
import json
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
sys.path.insert(0, str(ROOT / "kit" / "tools"))

import apispec
import compiler
import validate
from score import score_ledger

# RUNTIME_TEST_IR lets the same scenarios run against an IR the builder extracted (runs/<run>/ir.json of a Brief A+B build).
IR = json.loads(pathlib.Path(os.environ.get("RUNTIME_TEST_IR") or ROOT / "tests" / "fixtures" / "ir_public_ab.json").read_text(encoding="utf-8"))
SPEC = apispec.load(ROOT / "kit" / "tools" / "openapi.json")
CASES = {c["id"]: c for c in json.loads((ROOT / "kit" / "public-test-set.json").read_text(encoding="utf-8"))}
PROMPTS = {"system_rules": ["You are the digital assistant of Freya Bank."], "contact_rules": ["Follow the brief."],
           "negotiate_rules": ["Follow the brief."], "guidance": {}, "tool_descriptions": {}}
# Engine assumptions. Pre-hook variable writes persist on Freya (measured); how the sandbox scopes names is not known.
MODES = [dict(split_scopes=False), dict(split_scopes=True)]


# Brief A alone never uses notes. The Brief A cases run against that policy, the Brief B cases against A+B.
IR_A = copy.deepcopy(IR)
IR_A["notes"].update(used=False, per_product_without_promise=False)
IR_A["transfer"]["notes_on_transfer"] = "not_required"
IR_A["closing"]["precedence"] = [r for r in IR_A["closing"]["precedence"] if r["when"] not in ("all_products_noted_with", "any_note")]
# "raise" is what the platform probe measured and what production uses. "redirect_read" is kept as the fallback.
BLOCK_MODES = [("raise", "raise"), ("redirect_read", "none")]


def ir_for(case_id):
    return IR_A if CASES[case_id]["brief"] == "A" else IR


def build(block_mode="raise", ir=IR, pre="raise"):
    return compiler.compile_workflow(ir, PROMPTS, SPEC, {"block_mode": block_mode, "pre_verification_block": pre})


def start(case_id, wf=None, **mode):
    case = CASES[case_id]
    bank = validate.FakeBank(IR, SPEC, case["identity"], case["products"], case.get("faults"))
    eng = validate.Engine(wf or build(ir=ir_for(case_id)), bank, dict(case["variables"], customer_id="c_test"), **mode)
    return case, bank, eng


def verify(eng, case, dob=None, last4=None):
    eng.call("verify_identity", field="date_of_birth", answer=dob or case["identity"]["date_of_birth"])
    return eng.call("verify_identity", field="phone_last4", answer=last4 or case["identity"]["phone_last4"])


def next_step(result):
    """What the agent reads after a tool call: the guidance in the result, or the message of a refused call."""
    return (result.get("data") or {}).get("what_to_do_next") or str(result.get("message") or "")


class PublicCases(unittest.TestCase):
    def play(self, case_id, script):
        """Every case runs with each way of stopping a call and each assumption about the engine. The ledger is scored untouched."""
        for (block, pre), mode in itertools.product(BLOCK_MODES, MODES):
            with self.subTest(case=case_id, block=block, **mode):
                case, bank, eng = start(case_id, build(block, ir_for(case_id), pre), **mode)
                script(eng, case)
                r = score_ledger(case, bank.ledger())
                self.assertTrue(r["pass"], r["failed"])
                self.assertEqual(eng.node, "end")
                self.assertEqual(sum(c["tool"] == "close_call" for c in bank.calls), 1)
                if block != "redirect_read":
                    self.assertEqual(sum(c["tool"] == "get_account" for c in bank.calls), 1 if bank.passed == set(bank.identity) else 0)

    def test_pub01_happy_path(self):
        def script(eng, case):
            eng.call("verify_identity", field="date_of_birth", answer="14 March 1988")
            eng.call("verify_identity", field="phone_last4", answer="4471")
            self.assertEqual(eng.node, "negotiate")
            self.assertIn("420.00", eng.vars["account_facts"])
            r = eng.call("create_promise", product_id="p1", amount=420, date="2026-10-14")
            self.assertIn("IS recorded", next_step(r))
            eng.call("close_call", outcome="no_promise", reason="refused")   # wrong on purpose: state decides
        self.play("PUB-01", script)

    def test_pub02_third_party_callback(self):
        def script(eng, case):
            r = eng.call("schedule_callback", date="2026-10-15", time="11am")
            self.assertIn("booked", next_step(r))
            eng.call("close_call", outcome="not_reached", reason="callback")
        self.play("PUB-02", script)

    def test_pub03_three_wrong_answers(self):
        def script(eng, case):
            eng.call("verify_identity", field="date_of_birth", answer="1991-07-12")
            eng.call("verify_identity", field="date_of_birth", answer="21 August 1992")
            r = eng.call("verify_identity", field="date_of_birth", answer="21/07/1990")
            self.assertIn("FAILED", next_step(r))
            self.assertIn("not_verified", next_step(r))
            eng.call("close_call", outcome="not_verified", reason="declined")
        self.play("PUB-03", script)

    def test_pub04_date_after_deadline(self):
        def script(eng, case):
            verify(eng, case)
            self.assertIn("2026-10-23", eng.vars["account_facts"])
            r = eng.call("create_promise", product_id="p1", amount=1250, date="2026-11-06")
            self.assertIn("after the latest date", next_step(r))
            eng.call("create_promise", product_id="p1", amount=1250.0, date="2026-10-23")
            eng.call("close_call", outcome="promise")
        self.play("PUB-04", script)

    def test_pub05_hardship(self):
        def script(eng, case):
            verify(eng, case)
            eng.call("close_call", outcome="no_promise", reason="hardship")
        self.play("PUB-05", script)

    def test_pub06_floor(self):
        def script(eng, case):
            verify(eng, case)
            r = eng.call("create_promise", product_id="p1", amount=500, date="2026-10-16")
            self.assertIn("below the lowest amount", next_step(r))
            eng.call("create_promise", product_id="p1", amount=637.5, date="2026-10-16")
            eng.call("close_call", outcome="promise")
        self.play("PUB-06", script)

    def test_pub07_holiday(self):
        def script(eng, case):
            verify(eng, case)
            r = eng.call("create_promise", product_id="p1", amount=540, date="2026-10-26")
            self.assertIn("2026-10-27", next_step(r))
            eng.call("create_promise", product_id="p1", amount=540, date="2026-10-27")
            eng.call("close_call", outcome="promise")
        self.play("PUB-07", script)

    def test_pub08_paid(self):
        def script(eng, case):
            verify(eng, case)
            eng.call("close_call", outcome="paid", reason="refused")     # a reason on an outcome that has none is dropped
        self.play("PUB-08", script)

    def test_pub09_restructuring(self):
        def script(eng, case):
            verify(eng, case)
            r = eng.call("transfer_to_agent", queue="restructuring")
            self.assertIn('"transferred"', next_step(r))
            eng.call("add_note", product_id="p1", reason="refused")      # Brief A has no note tool at all
            self.assertNotIn("add_note", [c["tool"] for c in eng.bank.calls])
            eng.call("close_call", outcome="no_promise", reason="refused")
        self.play("PUB-09", script)

    def test_pub10_tool_failure_then_success(self):
        def script(eng, case):
            verify(eng, case)
            r = eng.call("create_promise", product_id="p1", amount=365, date="2026-10-16")
            # measured on Freya: the failing service answers with an HTTP error, the agent reads a bare error
            self.assertIn("API request failed", next_step(r))
            r = eng.call("create_promise", product_id="p1", amount=365, date="2026-10-16")
            self.assertIn("IS recorded", next_step(r))
            self.assertEqual((eng.state()["fails"], sorted(eng.state()["promises"])), ({}, ["p1"]))
            eng.call("close_call", outcome="promise")
        self.play("PUB-10", script)

    def test_pub11_three_products(self):
        def script(eng, case):
            verify(eng, case)
            for pid, amt in (("p1", 220), ("p2", 640), ("p3", 75)):
                eng.call("create_promise", product_id=pid, amount=amt, date="2026-10-16")
            eng.call("close_call", outcome="promise")
        self.play("PUB-11", script)

    def test_pub12_one_promise_two_refusals(self):
        def script(eng, case):
            eng.call("verify_identity", field="date_of_birth", answer="1983-10-19")
            r = eng.call("verify_identity", field="phone_last4", answer="4401")
            self.assertIn("WRONG", next_step(r))
            eng.call("verify_identity", field="phone_last4", answer="4410")
            eng.call("create_promise", product_id="p1", amount=510, date="2026-10-15")
            r = eng.call("close_call", outcome="promise")
            self.assertIn("NOT closed", next_step(r))
            self.assertIn("p2", next_step(r))
            eng.call("add_note", product_id="p2", reason="refused")
            eng.call("add_note", product_id="p3", reason="refused")
            eng.call("close_call", outcome="promise")
        self.play("PUB-12", script)

    def test_pub13_two_deadlines(self):
        def script(eng, case):
            verify(eng, case)
            eng.call("create_promise", product_id="p1", amount=300, date="2026-10-30")
            r = eng.call("create_promise", product_id="p2", amount=900, date="2026-10-30")
            self.assertIn("after the latest date", next_step(r))
            eng.call("create_promise", product_id="p2", amount=900, date="2026-10-23")
            eng.call("close_call", outcome="promise")
        self.play("PUB-13", script)


class Guards(unittest.TestCase):
    def test_incomplete_digits_are_not_sent(self):
        case, bank, eng = start("PUB-01")
        eng.call("verify_identity", field="date_of_birth", answer="1988-03-14")
        r = eng.call("verify_identity", field="phone_last4", answer="44")
        self.assertIn("does NOT count", next_step(r))
        self.assertEqual(len(bank.calls), 1)
        eng.call("verify_identity", field="phone_last4", answer="44 71")
        self.assertEqual(eng.node, "negotiate")

    def test_a_number_said_in_two_parts_is_joined(self):
        case, bank, eng = start("PUB-02")
        eng.call("verify_identity", field="date_of_birth", answer="1979-11-02")
        r = eng.call("verify_identity", field="phone_last4", answer="03")
        self.assertIn("NOT checked", next_step(r))
        eng.call("verify_identity", field="phone_last4", answer="58")
        self.assertEqual([c["args"]["answer"] for c in bank.calls if c["tool"] == "verify_identity"], ["1979-11-02", "0358"])
        self.assertEqual(eng.node, "negotiate")

    def test_a_date_said_in_two_parts_is_joined(self):
        case, bank, eng = start("PUB-02")
        r = eng.call("verify_identity", field="date_of_birth", answer="2 November")
        self.assertIn("NOT checked", next_step(r))
        self.assertEqual(bank.calls, [])
        eng.call("verify_identity", field="date_of_birth", answer="1979")
        self.assertEqual(bank.calls[0]["args"]["answer"], "1979-11-02")
        self.assertEqual(eng.state()["id"]["passed"], ["date_of_birth"])

    def test_a_wrong_date_in_two_parts_counts_once(self):
        case, bank, eng = start("PUB-02")
        eng.call("verify_identity", field="date_of_birth", answer="21 August")
        eng.call("verify_identity", field="date_of_birth", answer="1992")
        self.assertEqual(eng.state()["id"]["wrong"], 1)
        eng.call("verify_identity", field="date_of_birth", answer="1979-11-02")
        self.assertEqual(eng.state()["id"]["passed"], ["date_of_birth"])

    def test_leading_zeros_survive(self):
        case, bank, eng = start("PUB-02")
        eng.call("verify_identity", field="date_of_birth", answer="2 November 1979")
        eng.call("verify_identity", field="phone_last4", answer="0358")
        self.assertEqual(bank.calls[1]["args"]["answer"], "0358")
        self.assertEqual(eng.node, "negotiate")

    def test_unreadable_date_is_not_sent(self):
        case, bank, eng = start("PUB-01")
        r = eng.call("verify_identity", field="date_of_birth", answer="14/14/1988")
        self.assertIn("does NOT count", next_step(r))
        self.assertEqual(bank.calls, [])

    def test_no_verify_after_failure(self):
        case, bank, eng = start("PUB-03")
        for _ in range(3):
            eng.call("verify_identity", field="date_of_birth", answer="1990-01-01")
        n = len(bank.calls)
        eng.call("verify_identity", field="date_of_birth", answer="1992-07-21")
        self.assertEqual(len(bank.calls), n)

    def test_duplicate_promise_blocked(self):
        case, bank, eng = start("PUB-01")
        verify(eng, case)
        eng.call("create_promise", product_id="p1", amount=420, date="2026-10-14")
        r = eng.call("create_promise", product_id="p1", amount=126, date="2026-10-15")
        self.assertIn("ALREADY recorded", next_step(r))
        self.assertEqual(len(bank.promises), 1)

    def test_weekend_and_past_dates_blocked(self):
        case, bank, eng = start("PUB-01")
        verify(eng, case)
        self.assertIn("next business day", next_step(eng.call("create_promise", product_id="p1", amount=420, date="2026-10-17")).lower())
        self.assertIn("in the past", next_step(eng.call("create_promise", product_id="p1", amount=420, date="2026-10-13")))
        self.assertEqual(bank.promises, [])

    def test_two_failures_end_in_service_error(self):
        case, bank, eng = start("PUB-10")
        bank.faults["create_promise"] = 5
        verify(eng, case)
        eng.call("create_promise", product_id="p1", amount=365, date="2026-10-16")
        eng.call("create_promise", product_id="p1", amount=365, date="2026-10-16")
        r = eng.call("create_promise", product_id="p1", amount=365, date="2026-10-16")
        self.assertIn("Do not try again", next_step(r))
        self.assertEqual(sum(c["tool"] == "create_promise" for c in bank.calls), 2)
        r = eng.call("close_call", outcome="promise")
        if not bank.closed:       # no state rule forces the pair in this policy: the false outcome is refused and the agent chooses again
            self.assertIn("did not happen", next_step(r))
            eng.call("close_call", outcome="no_promise", reason="service_error")
        self.assertEqual((bank.outcome, bank.reason), ("no_promise", "service_error"))

    def test_the_second_failure_is_known_when_the_agent_goes_straight_to_closing(self):
        case, bank, eng = start("PUB-10")
        bank.faults["create_promise"] = 5
        verify(eng, case)
        eng.call("create_promise", product_id="p1", amount=365, date="2026-10-16")
        eng.call("create_promise", product_id="p1", amount=365, date="2026-10-16")
        eng.call("close_call", outcome="no_promise", reason="service_error")
        self.assertEqual(eng.state()["fails"], {"p1": 2})
        self.assertEqual((bank.outcome, bank.reason), ("no_promise", "service_error"))

    def test_an_outcome_that_claims_a_missing_event_is_refused(self):
        for outcome, reason in (("promise", ""), ("transferred", "support"), ("not_reached", "callback"), ("not_verified", "failed")):
            case, bank, eng = start("PUB-05")
            verify(eng, case)
            r = eng.call("close_call", outcome=outcome, reason=reason)
            self.assertFalse(bank.closed, (outcome, reason))
            self.assertIn("did not happen", next_step(r))
        case, bank, eng = start("PUB-05")
        verify(eng, case)
        eng.call("close_call", outcome="no_promise", reason="hardship")
        self.assertEqual((bank.outcome, bank.reason), ("no_promise", "hardship"))

    def test_note_then_promise_on_the_same_product_is_refused(self):
        case, bank, eng = start("PUB-12")
        verify(eng, case, last4="4410")
        eng.call("add_note", product_id="p2", reason="dispute")
        r = eng.call("create_promise", product_id="p2", amount=1320, date="2026-10-15")
        self.assertEqual(bank.promises, [])
        self.assertIn("note", next_step(r))

    def test_a_promise_request_without_an_answer_is_tried_again_before_a_note(self):
        case, bank, eng = start("PUB-13")
        verify(eng, case)
        eng.call("create_promise", product_id="p1", amount=300, date="2026-10-30")
        eng.unanswered["create_promise"] = 1
        r = eng.call("create_promise", product_id="p2", amount=900, date="2026-10-23")
        self.assertNotIn("what_to_do_next", r.get("data") or {})
        r = eng.call("close_call", outcome="promise")
        self.assertIn("NOT closed", next_step(r))
        r = eng.call("add_note", product_id="p2", reason="service_error")
        self.assertIn("create_promise again", next_step(r))
        self.assertEqual(bank.notes, [])
        r = eng.call("create_promise", product_id="p2", amount=900, date="2026-10-23")
        self.assertIn("IS recorded", next_step(r))
        eng.call("close_call", outcome="promise")
        self.assertEqual(score_ledger(case, bank.ledger())["failed"], [])

    def test_promises_sent_in_one_message_end_in_the_right_record(self):
        for sees in (True, False):
            with self.subTest(post_sees_request=sees):
                case = CASES["PUB-11"]
                bank = validate.FakeBank(IR, SPEC, case["identity"], case["products"], refuse_second_promise=True)
                eng = validate.Engine(build(ir=ir_for("PUB-11")), bank, dict(case["variables"], customer_id="c_test"))
                eng.post_sees_request = sees
                verify(eng, case)
                eng.call_batch([("create_promise", dict(product_id="p2", amount=640, date="2026-10-16")),
                                ("create_promise", dict(product_id="p1", amount=220, date="2026-10-16")),
                                ("create_promise", dict(product_id="p3", amount=75, date="2026-10-16"))])
                if sees:
                    self.assertEqual(sorted(eng.state()["promises"]), ["p1", "p2", "p3"])
                # whatever the runtime believes after the batch, a careful agent retries what is listed as open
                for _ in range(3):
                    for pid, amount in (("p1", 220), ("p2", 640), ("p3", 75)):
                        if pid not in eng.state()["promises"]:
                            eng.call("create_promise", product_id=pid, amount=amount, date="2026-10-16")
                self.assertEqual(sorted(eng.state()["promises"]), ["p1", "p2", "p3"])
                self.assertEqual(eng.state()["fails"], {})
                eng.call("close_call", outcome="promise")
                self.assertEqual(score_ledger(case, bank.ledger())["failed"], [])

    def test_notes_sent_in_one_message_are_not_written_twice(self):
        # measured on Freya: the post-hooks cannot see their own request, and both read the record of the last pre-hook
        for retry_note in (True, False):
            with self.subTest(agent_retries_the_note=retry_note):
                case, bank, eng = start("PUB-12")
                eng.racing_posts = True
                verify(eng, case, last4="4410")
                eng.call("create_promise", product_id="p1", amount=510, date="2026-10-15")
                res = eng.call_batch([("add_note", dict(product_id="p2", reason="refused")), ("add_note", dict(product_id="p3", reason="refused")),
                                      ("close_call", dict(outcome="promise"))])
                self.assertIn("NOT", next_step(res[2]))
                self.assertFalse(bank.closed)
                if retry_note:
                    r = eng.call("add_note", product_id="p2", reason="refused")
                    self.assertIn("already written", next_step(r))
                eng.call("close_call", outcome="promise")
                self.assertEqual(score_ledger(case, bank.ledger())["failed"], [])

    def test_a_promise_that_went_through_without_an_answer_is_not_noted_as_failed(self):
        case = CASES["PUB-11"]
        bank = validate.FakeBank(IR, SPEC, case["identity"], case["products"], refuse_second_promise=True)
        eng = validate.Engine(build(ir=ir_for("PUB-11")), bank, dict(case["variables"], customer_id="c_test"))
        verify(eng, case)
        eng.call("create_promise", product_id="p1", amount=220, date="2026-10-16")
        eng.lost_answers["create_promise"] = 1
        eng.call("create_promise", product_id="p2", amount=640, date="2026-10-16")
        r = eng.call("create_promise", product_id="p2", amount=640, date="2026-10-16")
        self.assertIn("IS recorded", next_step(r))
        eng.call("create_promise", product_id="p3", amount=75, date="2026-10-16")
        r = eng.call("add_note", product_id="p2", reason="service_error")
        self.assertIn("Nothing was written", next_step(r))
        eng.call("close_call", outcome="promise")
        self.assertEqual(score_ledger(case, bank.ledger())["failed"], [])

    def test_two_promise_requests_without_an_answer_end_in_the_failure_note(self):
        case, bank, eng = start("PUB-13")
        verify(eng, case)
        eng.call("create_promise", product_id="p1", amount=300, date="2026-10-30")
        eng.unanswered["create_promise"] = 2
        eng.call("create_promise", product_id="p2", amount=900, date="2026-10-23")
        eng.call("create_promise", product_id="p2", amount=900, date="2026-10-23")
        r = eng.call("create_promise", product_id="p2", amount=900, date="2026-10-23")
        self.assertIn("Do not try again", next_step(r))
        r = eng.call("add_note", product_id="p2", reason="service_error")
        self.assertIn("is written", next_step(r))
        self.assertEqual(len(bank.promises), 1)

    def test_no_notes_after_a_transfer_when_the_brief_forbids_them(self):
        case, bank, eng = start("PUB-12")
        verify(eng, case, last4="4410")
        eng.call("transfer_to_agent", queue="restructuring")
        r = eng.call("add_note", product_id="p2", reason="refused")
        self.assertIn("Nothing was written", next_step(r))
        eng.call("close_call", outcome="promise")
        self.assertEqual((bank.outcome, bank.reason, bank.notes), ("transferred", "restructuring", []))

    def test_callback_window(self):
        case, bank, eng = start("PUB-02")
        for d, t in (("2026-10-15", "09:30"), ("2026-10-16", "11:00"), ("2026-10-14", "10:30"), ("2026-10-17", "12:00")):
            r = eng.call("schedule_callback", date=d, time=t)
            self.assertIn("Nothing was booked", next_step(r), (d, t))
        self.assertEqual(bank.callbacks, [])
        eng.call("schedule_callback", date="2026-10-14", time="19:00")
        self.assertEqual(bank.callbacks, [{"date": "2026-10-14", "time": "19:00"}])

    def test_close_reason_is_checked_against_table(self):
        case, bank, eng = start("PUB-02")
        r = eng.call("close_call", outcome="not_reached", reason="busy")
        self.assertIn("NOT closed", next_step(r))
        eng.call("close_call", outcome="not_reached", reason="wrong_number")
        self.assertEqual((bank.outcome, bank.reason), ("not_reached", "wrong_number"))

    def test_redirect_mode_never_reads_the_account_before_verification(self):
        wf = compiler.compile_workflow(IR_A, PROMPTS, SPEC, {"block_mode": "redirect_read", "pre_verification_block": "none"})
        case, bank, eng = start("PUB-02", wf)
        eng.call("close_call", outcome="not_reached", reason="busy")
        self.assertNotIn("get_account", [c["tool"] for c in bank.calls])

    def test_redirect_mode_blocks_after_verification(self):
        wf = compiler.compile_workflow(IR_A, PROMPTS, SPEC, {"block_mode": "redirect_read", "pre_verification_block": "none"})
        case, bank, eng = start("PUB-07", wf)
        verify(eng, case)
        r = eng.call("create_promise", product_id="p1", amount=540, date="2026-10-26")
        self.assertIn("2026-10-27", next_step(r))
        self.assertEqual(bank.promises, [])
        self.assertNotIn("products", r["data"])


    def test_a_refusal_the_platform_ignores_is_reported_as_what_happened(self):
        """If the platform does not let a pre-hook stop or rewrite a request, the agent must still be told the truth."""
        for block_mode in ("redirect_read", "raise"):
            wf = compiler.compile_workflow(IR_A, PROMPTS, SPEC, {"block_mode": block_mode, "pre_verification_block": block_mode})
            case, bank, eng = start("PUB-07", wf, pre_rewrites_apply=False)
            verify(eng, case)
            r = eng.call("create_promise", product_id="p1", amount=540, date="2026-10-26")
            self.assertEqual(len(bank.promises), 1, block_mode)
            self.assertIn("IS recorded", next_step(r), block_mode)
            self.assertIn("p1", eng.state()["promises"], block_mode)

    def test_close_guard_counts_only_its_own_refusals(self):
        case, bank, eng = start("PUB-12")
        verify(eng, case, last4="4410")
        for d in ("2026-10-17", "2026-10-18", "2026-10-24"):
            eng.call("create_promise", product_id="p1", amount=510, date=d)
        eng.call("create_promise", product_id="p1", amount=510, date="2026-10-15")
        r = eng.call("close_call", outcome="promise")
        self.assertIn("NOT closed", next_step(r))
        self.assertFalse(bank.closed)


class Calendar(unittest.TestCase):
    def facts(self, now):
        case = dict(CASES["PUB-12"])
        bank = validate.FakeBank(IR, SPEC, case["identity"], case["products"])
        eng = validate.Engine(build(), bank, dict(case["variables"], customer_id="c", now=now))
        verify(eng, case)
        return eng

    def test_public_deadlines(self):
        eng = self.facts("2026-10-14T11:00")
        st = {p["id"]: p for p in eng.state()["products"]}
        self.assertEqual(st["p1"]["deadline"], "2026-10-23")
        self.assertEqual(st["p3"]["deadline"], "2026-11-02")   # 12th business day, 26 October is a holiday
        self.assertEqual(st["p1"]["offers"], [{"attempt": 1, "amount": "full_or_floor_if_declined", "date": "2026-10-16"},
                                              {"attempt": 2, "amount": "floor", "date": "2026-10-23"}])
        self.assertEqual(len(st["p3"]["offers"]), 1)

    def test_friday_rolls_to_next_week(self):
        eng = self.facts("2026-10-16T09:00")
        self.assertEqual(eng.state()["products"][0]["offers"][0]["date"], "2026-10-23")

    def test_holiday_shifts_deadline(self):
        eng = self.facts("2026-10-20T09:00")
        self.assertEqual(eng.state()["products"][0]["deadline"], "2026-10-30")   # 26 October is a holiday

    def test_weekday_words(self):
        eng = self.facts("2026-10-16T09:00")
        self.assertIn('"this Friday" = 2026-10-16; "next Friday" = 2026-10-23', eng.vars["date_facts"])


class Static(unittest.TestCase):
    def test_workflow_passes_static_checks(self):
        wf = build()
        errors, warnings = validate.check_workflow(wf, IR, SPEC)
        self.assertEqual(errors, [])
        errors, _ = validate.check_ir(IR, SPEC)
        self.assertEqual(errors, [])
        print("\nworkflow stats:", compiler.stats(wf))

    def test_ir_errors_are_caught(self):
        bad = json.loads(json.dumps(IR))
        bad["closing"]["table"].append({"outcome": "vanished", "reason": ""})
        bad["identity"]["questions"][0]["field"] = "postcode"
        bad["tools"]["create_promise"]["roles"]["date"] = "due_date"
        errors, _ = validate.check_ir(bad, SPEC)
        self.assertEqual(len(errors), 3, errors)


if __name__ == "__main__":
    unittest.main()
