"""Scripted e2e (v0.1): piped stdin drill session, state survives a process
restart, report prints the weakness map, reset wipes it, and every answered
question appends a line to the events.jsonl calibration log."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "app" / "cli.py"


def concept_map(raw):
    return raw["concepts"] if isinstance(raw.get("concepts"), dict) else raw


def run(args, stdin_text="", state_path=None, events_path=None):
    env = dict(os.environ)
    if state_path:
        env["TUTOR_STATE"] = str(state_path)
    if events_path:
        env["TUTOR_EVENTS"] = str(events_path)
    return subprocess.run([sys.executable, str(CLI), *args],
                          input=stdin_text, capture_output=True, text=True,
                          cwd=ROOT, env=env, timeout=120)


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "state.json"
        self.events = pathlib.Path(self.tmp.name) / "events.jsonl"
        self.addCleanup(self.tmp.cleanup)

    def test_drill_report_reset_cycle(self):
        # session 1: answer everything correctly (examples consume no stdin)
        r = run(["drill", "3"], stdin_text="A\nA\nA\nA\nA\nA\n",
                state_path=self.state, events_path=self.events)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Session complete", r.stdout)

        # state persisted to disk
        self.assertTrue(self.state.exists())
        raw = json.loads(self.state.read_text())
        asked = sum(v["asked"] for v in concept_map(raw).values())
        progress = asked + sum(len(v.get("fade_seen") or [])
                               for v in concept_map(raw).values())
        self.assertGreater(progress, 0)
        lines = [l for l in self.events.read_text().strip().splitlines() if l]
        self.assertEqual(len(lines), asked)
        if asked:
            ev = json.loads(lines[0])
            for key in ("ts", "user", "qid", "concept", "level", "correct",
                        "misconception", "session_type"):
                self.assertIn(key, ev)

        # session 2: fresh process — state and events continue, not reset
        r2 = run(["drill", "2"], stdin_text="A\nA\nA\nA\n",
                 state_path=self.state, events_path=self.events)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        raw2 = json.loads(self.state.read_text())
        asked2 = sum(v["asked"] for v in concept_map(raw2).values())
        self.assertGreaterEqual(asked2, asked)
        self.assertEqual(len(self.events.read_text().strip().splitlines()), asked2)

        # report prints the weakness map over persisted state
        r3 = run(["report"], state_path=self.state)
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertIn("frontier", r3.stdout)
        self.assertIn("go:goroutines", r3.stdout)

        # reset wipes state (events log is calibration data; it stays)
        r4 = run(["reset", "--yes"], state_path=self.state)
        self.assertEqual(r4.returncode, 0, r4.stderr)
        self.assertFalse(self.state.exists())
        self.assertTrue(self.events.exists())

    def test_drill_wrong_answer_and_quit(self):
        r = run(["drill", "6"], stdin_text="B\nB\nB\nB\nB\nB\nQ\n",
                state_path=self.state, events_path=self.events)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wrong", r.stdout)
        raw = json.loads(self.state.read_text())
        self.assertTrue(any(v["fails"] >= 1 for v in concept_map(raw).values()))
        evs = [json.loads(l) for l in self.events.read_text().splitlines() if l]
        self.assertTrue(evs)
        self.assertFalse(evs[0]["correct"])

    def test_invalid_command(self):
        r = run(["bogus"], state_path=self.state)
        self.assertEqual(r.returncode, 2)

    def test_sim_history_survives_drill(self):
        hist = [{"scenario": "ambulance-dispatch", "date": "2026-09-14",
                 "dimension_scores": {"estimation": 1}}]
        self.state.write_text(json.dumps({
            "concepts": {},
            "sim_history": hist,
        }))
        r = run(["drill", "1"], stdin_text="A\nA\nA\n",
                state_path=self.state, events_path=self.events)
        self.assertEqual(r.returncode, 0, r.stderr)
        raw = json.loads(self.state.read_text())
        self.assertEqual(raw.get("sim_history"), hist)

    def test_event_session_type_is_quick_drill(self):
        r = run(["drill", "6"], stdin_text="A\nA\nA\nA\nA\nA\nA\nA\n",
                state_path=self.state, events_path=self.events)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = self.events.read_text().strip().splitlines()
        self.assertTrue(lines)
        ev = json.loads(lines[0])
        self.assertEqual(ev["session_type"], "quick_drill")
        self.assertEqual(ev["user"], "local")


if __name__ == "__main__":
    unittest.main(verbosity=2)
