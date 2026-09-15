"""v0.1 multi-pack tests: free_text rubric validation, session types,
harness backends (go/python/rubric/numeric — positive + negative), e2e design."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fixture import content, CONCEPTS, QUESTIONS, MISCONCEPTIONS  # noqa: E402
from engine.model import ConceptState, ValidationError, load_content  # noqa: E402
from engine.selection import compose_session, pick_question  # noqa: E402
from app.paths import has_authored_packs  # noqa: E402

SEED_VERIFY = ROOT / "tests" / "seed" / "verify"
SEED_PACKS = ROOT / "tests" / "seed" / "packs"

FT_Q = {
    "id": "ft99", "concept": "go:closing", "level": "reasoning",
    "kind": "free_text", "stem": "Explain a thing.", "word_limit": 150,
    "options": [], "explanation": "",
    "rubric": {"criteria": [
        {"id": "c1", "points": 3, "model_answer": "a"},
        {"id": "c2", "points": 2, "model_answer": "b"}],
        "misconceptions": {"m1": "belief"}},
}
CLI = ROOT / "app" / "cli.py"
VERIFY = ROOT / "app" / "verify.py"


def run_cmd(cmd, stdin_text="", state_path=None, events_path=None):
    env = dict(os.environ)
    if state_path:
        env["TUTOR_STATE"] = str(state_path)
    if events_path:
        env["TUTOR_EVENTS"] = str(events_path)
    return subprocess.run(cmd, input=stdin_text, capture_output=True, text=True,
                          cwd=ROOT, env=env, timeout=120)


class TestFreeTextValidation(unittest.TestCase):
    def load(self, questions):
        return load_content(CONCEPTS, questions, MISCONCEPTIONS)

    def test_valid_free_text(self):
        self.load([FT_Q] + QUESTIONS)

    def test_free_text_requires_rubric(self):
        with self.assertRaises(ValidationError) as e:
            self.load([{**FT_Q, "rubric": None}])
        self.assertIn("needs a rubric", str(e.exception))

    def test_empty_criteria_rejected(self):
        with self.assertRaises(ValidationError) as e:
            self.load([{**FT_Q, "rubric": {"criteria": []}}])
        self.assertIn("non-empty criteria", str(e.exception))

    def test_criterion_missing_model_answer(self):
        rub = {"criteria": [{"id": "c1", "points": 2}]}
        with self.assertRaises(ValidationError) as e:
            self.load([{**FT_Q, "rubric": rub}])
        self.assertIn("model_answer", str(e.exception))

    def test_nonpositive_points_rejected(self):
        rub = {"criteria": [{"id": "c1", "points": 0, "model_answer": "a"}]}
        with self.assertRaises(ValidationError) as e:
            self.load([{**FT_Q, "rubric": rub}])
        self.assertIn("positive integer points", str(e.exception))

    def test_duplicate_criterion_ids_rejected(self):
        rub = {"criteria": [{"id": "c1", "points": 1, "model_answer": "a"},
                             {"id": "c1", "points": 1, "model_answer": "b"}]}
        with self.assertRaises(ValidationError) as e:
            self.load([{**FT_Q, "rubric": rub}])
        self.assertIn("duplicate rubric criterion", str(e.exception))

    def test_unknown_rubric_misconception_rejected(self):
        rub = {"criteria": [{"id": "c1", "points": 1, "model_answer": "a"}],
               "misconceptions": {"ghost": "b"}}
        with self.assertRaises(ValidationError) as e:
            self.load([{**FT_Q, "rubric": rub}])
        self.assertIn("unknown rubric misconception ghost", str(e.exception))

    def test_word_limit_required(self):
        with self.assertRaises(ValidationError) as e:
            self.load([{**FT_Q, "word_limit": None}])
        self.assertIn("word_limit", str(e.exception))

    def test_bad_verify_backend_rejected(self):
        with self.assertRaises(ValidationError) as e:
            self.load([{**FT_Q, "verify": {"dir": "x", "backend": "psychic"}}])
        self.assertIn("bad verify backend", str(e.exception))

    def test_free_text_exempt_from_option_rules(self):
        self.load([FT_Q])  # no options at all


class TestSessionTypes(unittest.TestCase):
    def test_quick_drill_excludes_free_text(self):
        c = content()
        st = {cid: ConceptState() for cid in c.concepts}
        plan = compose_session(c, st, "2026-09-14", n=6)
        self.assertTrue(plan)
        self.assertTrue(all(q.kind != "free_text" for _, q in plan))

    def test_design_session_only_free_text_and_capped(self):
        c = content()
        st = {cid: ConceptState() for cid in c.concepts}
        plan = compose_session(c, st, "2026-09-14", n=9,
                               session_type="design_session")
        self.assertLessEqual(len(plan), 3)  # long-form rounds are capped
        self.assertTrue(all(q.kind == "free_text" for _, q in plan))
        self.assertEqual([q.id for _, q in plan], ["ft01", "ft02"])

    def test_pick_question_filters_by_session_type(self):
        c = content()
        s = ConceptState()
        q_quick = pick_question(c, "go:select", s, "quick_drill")
        self.assertIsNotNone(q_quick)
        self.assertNotEqual(q_quick.kind, "free_text")
        # ft01 lives on go:select: design mode finds it, quick mode never serves it
        q_design = pick_question(c, "go:select", s, "design_session")
        self.assertEqual(q_design.id, "ft01")
        # a concept with no free_text serves nothing in design mode
        self.assertIsNone(pick_question(c, "go:goroutines", s, "design_session"))


class TestHarnessBackends(unittest.TestCase):
    def test_all_seed_backends_green(self):
        r = run_cmd([sys.executable, str(VERIFY), str(SEED_VERIFY), str(SEED_PACKS)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for name in ("q101", "q102", "q103", "py_stdout", "py_timeout",
                     "py_cases", "rubric_ok"):
            self.assertIn(f"[ok] {name}", r.stdout)
        self.assertRegex(r.stdout, r"All \d+ check\(s\) verified")

    def test_corrupted_python_output_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            for d in ("py_stdout", "py_cases", "rubric_ok"):
                shutil.copytree(SEED_VERIFY / d, tmp_path / d)
            (tmp_path / "py_stdout" / "solution.py").write_text("print(42)\n")
            r = run_cmd([sys.executable, str(VERIFY), tmp_path])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("stdout mismatch", r.stdout)
            self.assertIn("[ok] py_cases", r.stdout)

    def test_fast_program_fails_timeout_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            shutil.copytree(SEED_VERIFY / "py_timeout", tmp_path / "py_timeout")
            (tmp_path / "py_timeout" / "solution.py").write_text("print('fast')\n")
            r = run_cmd([sys.executable, str(VERIFY), tmp_path])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("expected TLE", r.stdout)

    def test_python_cases_mode_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            shutil.copytree(SEED_VERIFY / "py_cases", tmp_path / "py_cases")
            r = run_cmd([sys.executable, str(VERIFY), tmp_path])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("[ok] py_cases", r.stdout)

    def test_python_cases_wrong_answer_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            shutil.copytree(SEED_VERIFY / "py_cases", tmp_path / "py_cases")
            (tmp_path / "py_cases" / "solution.py").write_text(
                "import sys\nprint(len(sys.stdin.read()))\n")
            r = run_cmd([sys.executable, str(VERIFY), tmp_path])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("stdout mismatch", r.stdout)

    def test_rubric_without_rubric_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            shutil.copytree(SEED_VERIFY / "rubric_ok", tmp_path / "rubric_ok")
            (tmp_path / "rubric_ok" / "rubric.json").unlink()
            r = run_cmd([sys.executable, str(VERIFY), tmp_path])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("needs rubric.json", r.stdout)

    def test_numeric_bad_value_fails(self):
        """Corrupt a pack's numeric answer key in a temp packs root."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            src = SEED_PACKS / "go"
            shutil.copytree(src, tmp_path / "go")
            qpath = tmp_path / "go" / "questions.json"
            qs = json.loads(qpath.read_text())
            found = False
            for q in qs:
                if q.get("kind") == "numeric":
                    q["answer_key"]["value"] = 42
                    found = True
            self.assertTrue(found, "seed go pack needs a numeric question")
            qpath.write_text(json.dumps(qs))
            r = run_cmd([sys.executable, str(VERIFY),
                         str(SEED_VERIFY), str(tmp_path)])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("declared value", r.stdout)


class TestDesignSessionE2E(unittest.TestCase):
    @unittest.skipUnless(has_authored_packs(), "authored packs not mounted")
    def test_design_session_self_grade_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp) / "state.json"
            stdin_text = ("my design answer line 1\nmore detail\n.\n"  # answer
                          "y\nn\nn\n")                                  # self-grades
            r = run_cmd([sys.executable, str(CLI), "drill", "3", "--design"],
                        stdin_text=stdin_text, state_path=state)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("design defense", r.stdout)
            self.assertIn("rubric", r.stdout)
            self.assertIn("recorded", r.stdout)
            raw = json.loads(state.read_text())
            concepts = raw["concepts"] if isinstance(raw.get("concepts"), dict) else raw
            self.assertGreaterEqual(concepts["sd:partitioning"]["asked"], 0)
            self.assertTrue(any(v.get("asked", 0) >= 1 for v in concepts.values()))

    def test_quick_drill_untouched_by_free_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp) / "state.json"
            r = run_cmd([sys.executable, str(CLI), "drill", "2"],
                        stdin_text="A\nA\n", state_path=state)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("design defense", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
