"""M6 sim e2e: piped stdin, five dimension scores, sim_history persisted."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "app" / "cli.py"


def run(args, stdin_text="", state_path=None, events_path=None):
    env = dict(os.environ)
    env.pop("TUTOR_LLM_KEY", None)
    if state_path:
        env["TUTOR_STATE"] = str(state_path)
    if events_path:
        env["TUTOR_EVENTS"] = str(events_path)
    return subprocess.run([sys.executable, str(CLI), *args],
                          input=stdin_text, capture_output=True, text=True,
                          cwd=ROOT, env=env, timeout=120)


class TestSimE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "state.json"
        self.events = pathlib.Path(self.tmp.name) / "events.jsonl"
        self.addCleanup(self.tmp.cleanup)

    def test_full_sim_records_five_dimensions(self):
        stdin = (
            "SLOs missing; peak ratio unknown; close-incident under partition.\n.\n"
            "A\n"
            "Partition incidents by id; downtown hospital is a hot key.\n.\n"
            "Even key plus hot-hospital mitigation.\n.\n"
            "Stale map reads, fail-closed close.\n.\n"
            "Leader death loses the GPS lag window.\n.\n"
        )
        r = run(["sim"], stdin_text=stdin, state_path=self.state,
                events_path=self.events)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("dimension scores", r.stdout)
        self.assertIn("constraint_extraction", r.stdout)
        self.assertIn("sim recorded", r.stdout)
        raw = json.loads(self.state.read_text())
        hist = raw.get("sim_history") or []
        self.assertTrue(hist)
        scores = hist[-1]["dimension_scores"]
        for dim in ("constraint_extraction", "estimation", "technique_selection",
                    "tradeoff_defense", "failure_reasoning"):
            self.assertIn(dim, scores)
            self.assertGreaterEqual(scores[dim], 1)
            self.assertLessEqual(scores[dim], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
