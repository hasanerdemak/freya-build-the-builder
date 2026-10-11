import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))

import apispec


class ApiSpec(unittest.TestCase):
    def test_public_api(self):
        spec = apispec.load(ROOT / "kit" / "tools" / "openapi.json")
        self.assertEqual(list(spec["tools"]), apispec.TOOL_NAMES)
        self.assertEqual(spec["tools"]["close_call"]["url"], "https://freya-bank-mock.freyavoice.workers.dev/v1/close_call")
        self.assertEqual(apispec.field(spec, "verify_identity", "field")["enum"], ["date_of_birth", "phone_last4"])
        self.assertTrue(apispec.field(spec, "create_promise", "amount")["required"])
        self.assertFalse(apispec.field(spec, "close_call", "reason")["required"])
        products = apispec.field(spec, "get_account", "products", "response")
        self.assertIn("minimum_payment", [f["name"] for f in products["items"]])

    def test_refs_and_allof_are_resolved(self):
        spec = apispec.load(ROOT / "tests" / "holdout" / "clean" / "openapi.json")
        names = [f["name"] for f in spec["tools"]["verify_identity"]["response"]]
        self.assertEqual(sorted(names), ["attempts_left", "check_passed", "correct", "error", "ok"])
        self.assertEqual([f["name"] for f in spec["tools"]["create_promise"]["response"]], ["ok", "error"])
        self.assertEqual(apispec.field(spec, "close_call", "result")["enum"][0], "ptp")

    def test_missing_tool_is_an_error(self):
        doc = json.loads((ROOT / "kit" / "tools" / "openapi.json").read_text(encoding="utf-8"))
        del doc["paths"]["/add_note"]
        with self.assertRaises(apispec.ApiSpecError):
            apispec.parse(doc)


if __name__ == "__main__":
    unittest.main()
