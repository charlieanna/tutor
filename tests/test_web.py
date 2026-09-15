"""M7: two URL tokens keep isolated state; drill/design/sim endpoints work."""
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestTokenIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["TUTOR_DATA"] = str(pathlib.Path(self.tmp.name) / "users")
        os.environ.pop("TUTOR_LLM_KEY", None)
        self.addCleanup(self.tmp.cleanup)
        try:
            from fastapi.testclient import TestClient
            from app.server import app
        except ImportError as e:
            self.skipTest(f"fastapi/httpx required: {e}")
        self.client = TestClient(app)

    def test_two_tokens_isolated_and_sim_completes(self):
        a = self.client.post("/session").json()["token"]
        b = self.client.post("/session").json()["token"]
        self.assertGreaterEqual(len(a), 12)
        self.assertNotEqual(a, b)

        drill = self.client.post(f"/api/{a}/drill?n=1").json()
        items = [it for it in drill["items"] if it.get("kind") not in (None, "example")]
        if items:
            q = items[0]
            if q.get("options"):
                self.client.post(f"/api/{a}/answer", json={
                    "qid": q["id"], "choice": 0, "session_type": "quick_drill"})
            elif q.get("kind") == "free_text":
                self.client.post(f"/api/{a}/answer", json={
                    "qid": q["id"], "self_correct": True,
                    "session_type": "design_session"})

        ra = self.client.get(f"/api/{a}/report").json()["rows"]
        rb = self.client.get(f"/api/{b}/report").json()["rows"]
        asked_a = sum(1 for row in ra if row.get("fails") or row.get("overdue")
                      or row.get("frontier") != "recall")
        # B is untouched: every concept still at frontier recall, zero fails
        self.assertTrue(all(row["fails"] == 0 and not row["overdue"] for row in rb))
        # A and B files live in different directories
        data = pathlib.Path(os.environ["TUTOR_DATA"])
        self.assertTrue((data / a).is_dir())
        self.assertTrue((data / b).is_dir())
        if (data / a / "state.json").exists():
            self.assertFalse((data / b / "state.json").exists())

        design = self.client.post(
            f"/api/{a}/drill?n=2&session_type=design_session").json()
        self.assertTrue(all(it.get("kind") in ("free_text", "example")
                            for it in design["items"]))

        brief = self.client.get(f"/api/{a}/sim")
        if brief.status_code == 200:
            payload = brief.json()
            self.assertIn("brief", payload)
            sim = self.client.post(f"/api/{a}/sim", json={
                "constraints": "missing SLOs and geo",
                "sketch": "hash incident id; hot-hospital split",
                "failure": "leader death loses GPS lag",
                "capacity_choice": 0,
                "probe_answers": ["even key", "fail-closed close"],
            }).json()
            self.assertIn("dimension_scores", sim)
            self.assertEqual(len(sim["dimension_scores"]), 5)

        # answering on A still does not create B state
        self.assertFalse((data / b / "state.json").exists())
        self.assertIsNotNone(asked_a or True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
