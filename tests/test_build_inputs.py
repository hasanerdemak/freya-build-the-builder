import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))

import build


def _folder(files):
    d = pathlib.Path(tempfile.mkdtemp())
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d.resolve()


class ReferencedBriefs(unittest.TestCase):
    def test_public_b_finds_a_and_a_finds_nothing(self):
        briefs = ROOT / "kit" / "briefs"
        self.assertEqual(build.referenced_briefs((briefs / "brief-b.md").resolve()), [(briefs / "brief-a.md").resolve()])
        self.assertEqual(build.referenced_briefs((briefs / "brief-a.md").resolve()), [])

    def test_standalone_briefs_have_no_base(self):
        for name in ("clean", "raw"):
            self.assertEqual(build.referenced_briefs((ROOT / "tests" / "holdout" / name / "brief.md").resolve()), [])

    def test_chain_is_ordered_base_first(self):
        d = _folder({"one.md": "# Plan One: cards\nrules", "two.md": "# Plan Two: loans\nPlan One applies.",
                     "three.md": "# Plan Three: all\nAs in two.md, with changes.", "README.md": "# Plan One and more\nPlan Three"})
        self.assertEqual([p.name for p in build.referenced_briefs(d / "three.md")], ["one.md", "two.md"])

    def test_briefs_naming_each_other_are_left_alone(self):
        d = _folder({"x.md": "# Desk North: a\nUnlike Desk South, no fees.", "y.md": "# Desk South: b\nUnlike Desk North, fees."})
        self.assertEqual(build.referenced_briefs(d / "x.md"), [])


class LocalScenario(unittest.TestCase):
    def test_the_rehearsal_passes_for_any_answer_normalisation(self):
        import copy
        import json
        import apispec
        import compiler
        ir0 = json.loads((ROOT / "tests" / "fixtures" / "ir_public_ab.json").read_text(encoding="utf-8"))
        spec = apispec.load(ROOT / "kit" / "tools" / "openapi.json")
        for ops in (["lowercase"], ["digits_only"], ["uppercase", "strip_spaces"], []):
            with self.subTest(normalize=ops):
                ir = copy.deepcopy(ir0)
                q = ir["identity"]["questions"][1]
                q.update(format="text", normalize=ops, length=0)
                prompts = {"system_rules": [], "contact_rules": [], "negotiate_rules": [], "guidance": {}, "tool_descriptions": {}}
                workflow = compiler.compile_workflow(ir, prompts, spec, {"block_mode": "raise", "pre_verification_block": "raise"})
                notes = build.local_scenario(workflow, ir, spec)
                self.assertIn("local scenario ok", notes[0])


class _Run:
    def __init__(self):
        self.lines, self.calls = [], 0
        self.deadline = build.st.Deadline(1680)

    def log(self, kind, text):
        self.lines.append(kind)

    def model_call(self, info):
        self.calls += 1


class ModelCalls(unittest.TestCase):
    def test_a_failed_model_call_is_repeated_once(self):
        tries = []

        def run_analyst(timeout_s):
            tries.append(timeout_s)
            if len(tries) == 1:
                raise build.llm.ModelError("overloaded")
            return {"ok": 1}, {}

        run = _Run()
        self.assertEqual(build._role(run, run_analyst, "extract"), {"ok": 1})
        self.assertEqual((len(tries), run.calls), (2, 1))
        self.assertIn("MODELERR", run.lines)

    def test_a_verifier_that_keeps_failing_does_not_stop_the_build(self):
        def run_verifier(timeout_s):
            raise build.llm.ModelError("no answer")

        run = _Run()
        self.assertIsNone(build._optional_role(run, run_verifier, "verify"))
        with self.assertRaises(build.llm.ModelError):
            build._role(run, run_verifier, "verify")


if __name__ == "__main__":
    unittest.main()
