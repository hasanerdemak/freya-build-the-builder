import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))

import casebuild
import ingest

# An invented service line (a parcel desk), unrelated to any bank case.
DESIGN = {
    "language": "English", "summary": "Parcel desk.",
    "call_variables": [{"name": "memberId", "role": "customer_reference", "meaning": "member", "example": "m-1"},
                       {"name": "now", "role": "current_time", "meaning": "time of the call", "example": "2030-03-04T09:00:00+00:00"}],
    "source_decisions": [], "unresolved": [], "opening_line": "", "system_rules": ["You are the parcel desk."],
    "tools": [
        {"name": "list_parcels", "service": "parcels.list", "path": "/desk/v2/parcels.list", "method": "POST", "description": "CALL it first.",
         "fields": [{"path": "query.member", "source": "call_variable", "value": "memberId", "type": "string", "description": "", "allowed": [], "required": True},
                    {"path": "meta.channel", "source": "fixed", "value": "phone", "type": "string", "description": "", "allowed": [], "required": True}],
         "changes_something": False, "needs_customer_yes": False, "only_after": [], "timeout_seconds": 15, "after_success": "Name the parcels.",
         "result_facts": [{"when": "count(items) == 0", "say": "No parcel."},
                          {"when": "count(items) >= 2 and items.0.fee > items.1.fee", "say": "The first parcel costs more."},
                          {"when": "present(items.0.fee) and (items.0.fee <= 0 or items.0.state == \"FREE\")", "say": "The first parcel is free."},
                          {"when": "missing(note) and not missing(items)", "say": "No note."},
                          {"when": "items.0.fee > nothing.here", "say": "never"}]},
        {"name": "preview_move", "service": "parcels.preview", "path": "/desk/v2/parcels.preview", "method": "POST", "description": "CALL it before a move.",
         "fields": [{"path": "query.parcelRef", "source": "earlier_result", "value": "list_parcels.parcelRef", "type": "string", "description": "", "allowed": [], "required": True},
                    {"path": "query.slot", "source": "agent", "value": "", "type": "integer", "description": "The slot.", "allowed": ["1", "2", "3"], "required": True}],
         "changes_something": False, "needs_customer_yes": False, "only_after": ["list_parcels"], "timeout_seconds": 15, "after_success": "Read the new day back."},
        {"name": "move_parcel", "service": "parcels.move", "path": "/desk/v2/parcels.move", "method": "POST", "description": "CALL it after a yes.",
         "fields": [{"path": "query.parcelRef", "source": "earlier_result", "value": "list_parcels.parcelRef", "type": "string", "description": "", "allowed": [], "required": True},
                    {"path": "query.slot", "source": "agent", "value": "", "type": "integer", "description": "The slot.", "allowed": ["1", "2", "3"], "required": True}],
         "changes_something": True, "needs_customer_yes": True, "times_allowed": 2, "only_after": ["preview_move"], "timeout_seconds": 15, "after_success": "Say it is moved."},
    ],
    "task": "1. CALL list_parcels.", "endings": [{"name": "done", "when": "The customer has nothing more.", "closing_sentence": "Goodbye."}],
}


class Hooks:
    def __init__(self):
        self.workflow = casebuild.compile_workflow(DESIGN, "https://services.example/base")
        self.tools = {t["function"]["name"]: t for t in self.workflow["nodes"]["conversation"]["tools"]}
        self.vars = {"memberId": "m-1", "now": "2030-03-04T09:00:00+00:00"}

    def call(self, name, answer=None, **args):
        config = {"url": "x", "method": "POST", "headers": {}, "body": dict(args)}
        exec(self.tools[name]["pre_process_code"], {"vars": self.vars, "config": config})
        result = {"status": "success", "status_code": 200, "data": answer if answer is not None else {}}
        exec(self.tools[name]["post_process_code"], {"vars": self.vars, "result": result})
        return config["body"], result["data"]


class CaseBuild(unittest.TestCase):
    def test_the_design_compiles_and_rehearses(self):
        self.assertEqual(casebuild.check_design(DESIGN), [])
        workflow = casebuild.compile_workflow(DESIGN, "https://services.example/base")
        self.assertEqual(casebuild.check_workflow(workflow, DESIGN), [])
        self.assertEqual(len(casebuild.rehearse(workflow, DESIGN)), 3)
        tool = workflow["nodes"]["conversation"]["tools"][0]["function"]["parameters"]["properties"]
        self.assertEqual(tool["url"]["default"], "https://services.example/desk/v2/parcels.list")
        self.assertEqual([t["target_node"] for t in workflow["nodes"]["conversation"]["transitions"]], ["done"])
        self.assertEqual(workflow["initial_node"], "setup")
        self.assertIn("- preview_move: {{sv_mem_preview_move}}", workflow["nodes"]["conversation"]["task_message"])
        started = {}
        exec(workflow["nodes"]["setup"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"], {"vars": started})
        self.assertEqual(started, {"sv_mem_list_parcels": "(not called yet)", "sv_mem_preview_move": "(not called yet)", "sv_mem_move_parcel": "(not called yet)"})
        self.assertNotIn("{{", json.dumps(workflow["nodes"]["conversation"]["tools"][0]["pre_process_code"]))

    def test_request_bodies_follow_the_field_paths(self):
        h = Hooks()
        body, data = h.call("list_parcels", {"items": [{"parcelRef": "PX**-0042", "due": "06.03.2030"}, {"parcelRef": "PX**-0077"}]}, member="{{memberId}}", channel="phone")
        self.assertEqual(body, {"query": {"member": "m-1"}, "meta": {"channel": "phone"}})
        self.assertEqual(data["what_to_do_next"], "Name the parcels.")
        _, shelved = h.call("list_parcels", {"items": [{"parcelRef": "PX**-0042", "shelf": "0420"}]})
        self.assertEqual(shelved["digit_notes"], ["shelf 0420 digit by digit: 0 4 2 0"])
        self.assertIn("2 day(s) after", " ".join(data["date_notes"]))

    def test_a_value_from_an_earlier_result_is_repaired_or_refused(self):
        h = Hooks()
        with self.assertRaises(Exception) as first:
            h.call("preview_move", parcelRef="0042", slot="2")
        self.assertIn("CALL list_parcels first", str(first.exception))
        h.call("list_parcels", {"items": [{"parcelRef": "PX**-0042"}, {"parcelRef": "PX**-0077"}]})
        body, _ = h.call("preview_move", parcelRef=" 0042", slot="2")
        self.assertEqual(body, {"query": {"parcelRef": "PX**-0042", "slot": 2}})
        single = Hooks()
        single.call("list_parcels", {"items": [{"parcelRef": "PX**-0042"}]})
        body, _ = single.call("preview_move", parcelRef="", slot="1")       # one value was returned: it is the one
        self.assertEqual(body["query"]["parcelRef"], "PX**-0042")
        body, _ = h.call("preview_move", parcelRef="PX11-0077", slot="2")      # the agent filled in the masked part: same ending, one match
        self.assertEqual(body["query"]["parcelRef"], "PX**-0077")
        with self.assertRaises(Exception) as unknown:
            h.call("preview_move", parcelRef="9999", slot="2")
        self.assertIn("PX**-0042, PX**-0077", str(unknown.exception))
        self.assertTrue(str(unknown.exception).startswith("The call was NOT carried out"))
        with self.assertRaises(Exception) as bad_slot:
            h.call("preview_move", parcelRef="PX**-0042", slot="7")
        self.assertIn("must be one of", str(bad_slot.exception))

    def test_a_change_needs_its_order_a_yes_and_happens_once(self):
        h = Hooks()
        h.call("list_parcels", {"items": [{"parcelRef": "PX**-0042"}]})
        with self.assertRaises(Exception) as early:
            h.call("move_parcel", parcelRef="PX**-0042", slot="2", customer_said_yes="yes")
        self.assertIn("CALL preview_move first", str(early.exception))
        h.call("preview_move", parcelRef="PX**-0042", slot="2")
        with self.assertRaises(Exception) as no_yes:
            h.call("move_parcel", parcelRef="PX**-0042", slot="2", customer_said_yes="no")
        self.assertIn("clear yes", str(no_yes.exception))
        body, _ = h.call("move_parcel", parcelRef="PX**-0042", slot="2", customer_said_yes="yes")
        self.assertEqual(body, {"query": {"parcelRef": "PX**-0042", "slot": 2}})
        h.call("move_parcel", parcelRef="PX**-0042", slot="2", customer_said_yes="yes")     # allowed twice in this design
        with self.assertRaises(Exception) as again:
            h.call("move_parcel", parcelRef="PX**-0042", slot="2", customer_said_yes="yes")
        self.assertIn("already done", str(again.exception))

    def test_facts_are_worked_out_from_the_answer(self):
        h = Hooks()
        _, data = h.call("list_parcels", {"body": {"items": [{"parcelRef": "PX**-0042", "fee": 12.5}, {"parcelRef": "PX**-0077", "fee": 3}]}, "note": "x"})
        self.assertEqual(data["facts"], ["The first parcel costs more."])
        _, data = h.call("list_parcels", {"items": [{"parcelRef": "PX**-0042", "fee": 0, "state": "PAID"}]})
        self.assertEqual(data["facts"], ["The first parcel is free.", "No note."])
        _, data = h.call("list_parcels", {"items": []})
        self.assertEqual(data["facts"], ["No parcel."])

    def test_results_are_kept_for_later_turns(self):
        h = Hooks()
        h.call("list_parcels", {"items": [{"parcelRef": "PX**-0042", "due": "06.03.2030"}], "note": ""})
        self.assertEqual(h.vars["sv_mem_preview_move"], "(not called yet)")
        self.assertIn("items.0.parcelRef=PX**-0042; items.0.due=06.03.2030", h.vars["sv_mem_list_parcels"])
        h.call("preview_move", {"day": "07.03.2030"}, parcelRef="PX**-0042", slot="2")
        h.call("preview_move", {"day": "08.03.2030"}, parcelRef="PX**-0042", slot="3")
        h.call("preview_move", {"day": "09.03.2030"}, parcelRef="PX**-0042", slot="1")
        kept = h.vars["sv_mem_preview_move"]
        self.assertNotIn("07.03.2030", kept)
        self.assertIn("[asked with parcelRef=PX**-0042|slot=3] day=08.03.2030", kept)
        self.assertIn("[asked with parcelRef=PX**-0042|slot=1] day=09.03.2030", kept)
        self.assertNotIn("{{", kept + h.vars["sv_mem_list_parcels"])

    def test_a_result_reminds_of_the_change_that_has_not_been_called(self):
        h = Hooks()
        _, listed = h.call("list_parcels", {"items": [{"parcelRef": "PX**-0042"}]})
        self.assertNotIn("not_done_yet", listed)
        _, previewed = h.call("preview_move", parcelRef="PX**-0042", slot="2")
        self.assertIn("move_parcel", previewed["not_done_yet"])
        h.call("move_parcel", parcelRef="PX**-0042", slot="2", customer_said_yes="yes")
        _, again = h.call("preview_move", parcelRef="PX**-0042", slot="3")
        self.assertNotIn("not_done_yet", again)

    def test_structural_faults_of_a_design_are_reported(self):
        broken = json.loads(json.dumps(DESIGN))
        broken["tools"][1]["fields"][0]["value"] = "nothing.parcelRef"
        broken["tools"][0]["path"] = "parcels.list"
        broken["endings"] = []
        found = " ".join(i["problem"] for i in casebuild.check_design(broken))
        for part in ("earlier_result", "must start with /", "no ending"):
            self.assertIn(part, found)


class Ingest(unittest.TestCase):
    def test_a_folder_is_read_brief_first_and_long_json_is_outlined(self):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "notes").mkdir()
        (d / "notes" / "a.txt").write_text("alpha", encoding="utf-8")
        (d / "README.md").write_text("readme", encoding="utf-8")
        (d / "BRIEF.md").write_text("brief", encoding="utf-8")
        (d / "big.json").write_text(json.dumps({"rows": [{"id": i, "text": "x" * 50} for i in range(2000)]}), encoding="utf-8")
        (d / "picture.bin").write_bytes(b"\x00\x01")
        docs = ingest.read_folder(d)
        self.assertEqual([x["path"] for x in docs][:2], ["BRIEF.md", "README.md"])
        big = next(x for x in docs if x["path"] == "big.json")
        self.assertIn("2000 items", big["text"])
        self.assertLess(len(big["text"]), 20000)
        self.assertEqual(next(x for x in docs if x["path"] == "picture.bin")["note"], "file type not read")


if __name__ == "__main__":
    unittest.main()
