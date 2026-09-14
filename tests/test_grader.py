"""M4: mocked rubric grader. Zero network. Engine runs with no key."""
import json
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from grader.rubric_grader import GradeError, grade, should_probe, validate_grade  # noqa: E402


QUESTION = {
    "id": "ft01", "stem": "Defend the cache.",
    "level": "reasoning", "kind": "free_text",
}
RUBRIC = {
    "criteria": [
        {"id": "hit", "points": 2, "model_answer": "name the hit path"},
        {"id": "cost", "points": 1, "model_answer": "name the invalidation cost"},
    ]
}
VALID = {
    "verdict": "partial",
    "misconception": None,
    "criteria_scores": {"hit": 2, "cost": 0},
    "reasoning_score": 2,
}


class TestValidateGrade(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_grade(VALID, RUBRIC)["verdict"], "partial")

    def test_bad_verdict(self):
        with self.assertRaises(GradeError):
            validate_grade({**VALID, "verdict": "maybe"}, RUBRIC)

    def test_missing_criterion(self):
        with self.assertRaises(GradeError):
            validate_grade({**VALID, "criteria_scores": {"hit": 2}}, RUBRIC)

    def test_score_over_points(self):
        with self.assertRaises(GradeError):
            validate_grade({**VALID, "criteria_scores": {"hit": 9, "cost": 0}}, RUBRIC)

    def test_reasoning_score_range(self):
        with self.assertRaises(GradeError):
            validate_grade({**VALID, "reasoning_score": 4}, RUBRIC)


class TestGradeRetry(unittest.TestCase):
    def test_retries_once_then_accepts(self):
        calls = {"n": 0}

        def transport(_prompt):
            calls["n"] += 1
            if calls["n"] == 1:
                return "not-json"
            return json.dumps(VALID)

        out = grade(QUESTION, RUBRIC, "cache the hot path",
                    key="fake", transport=transport)
        self.assertEqual(out["verdict"], "partial")
        self.assertEqual(calls["n"], 2)

    def test_two_invalid_raises(self):
        def transport(_prompt):
            return "still not json"

        with self.assertRaises(GradeError) as e:
            grade(QUESTION, RUBRIC, "x", key="fake", transport=transport)
        self.assertIn("after retry", str(e.exception).lower())

    def test_no_key_raises_skip(self):
        env = os.environ.pop("TUTOR_LLM_KEY", None)
        try:
            with self.assertRaises(GradeError) as e:
                grade(QUESTION, RUBRIC, "x", key="")
            self.assertIn("TUTOR_LLM_KEY", str(e.exception))
        finally:
            if env is not None:
                os.environ["TUTOR_LLM_KEY"] = env

    def test_transport_is_the_only_io(self):
        """A transport that would network-fail must not be called if we inject one."""
        out = grade(QUESTION, RUBRIC, "x", key="fake",
                    transport=lambda p: json.dumps(VALID))
        self.assertEqual(out["reasoning_score"], 2)


class TestProbeRate(unittest.TestCase):
    def test_only_correct_reasoning(self):
        self.assertTrue(should_probe("reasoning", True, rng=lambda: 0.0))
        self.assertFalse(should_probe("reasoning", True, rng=lambda: 0.9))
        self.assertFalse(should_probe("reasoning", False, rng=lambda: 0.0))
        self.assertFalse(should_probe("recall", True, rng=lambda: 0.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
