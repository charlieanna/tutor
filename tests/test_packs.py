"""Pack launch-slice sizes for M5 / M8 / M9."""
import sys
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.cli import load_content_packs  # noqa: E402


class TestPackSizes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = load_content_packs()

    def _qs(self, prefix):
        return [q for q in self.c.questions if q.concept.startswith(prefix)]

    def test_system_design_slice(self):
        concepts = [c for c in self.c.concepts if c.startswith("sd:")]
        self.assertGreaterEqual(len(concepts), 6)
        qs = self._qs("sd:")
        mcq = [q for q in qs if q.kind in ("mcq", "numeric")]
        ft = [q for q in qs if q.kind == "free_text"]
        self.assertGreaterEqual(len(mcq), 45)
        self.assertGreaterEqual(len(ft), 12)
        misc = [m for m in self.c.misconceptions
                if m in (
                    "replicas-fix-everything", "cap-means-pick-two",
                    "eventual-latest", "cache-always-helps",
                    "shard-by-user-is-fine", "2pc-safe",
                    "linearizable-serializable", "async-makes-fast",
                    "local-topk-additive")]
        self.assertEqual(len(misc), 9)
        self.assertGreaterEqual(len(self.c.misconceptions), 15)
        examples = [c for c in self.c.examples if c.startswith("sd:")]
        self.assertGreaterEqual(len(examples), 6)

    def test_dsa_slice(self):
        concepts = [c for c in self.c.concepts if c.startswith("dsa:")]
        self.assertGreaterEqual(len(concepts), 12)
        self.assertGreaterEqual(len(self._qs("dsa:")), 60)
        self.assertTrue(any(q.verify and q.verify.get("backend") == "python_exec"
                            for q in self._qs("dsa:")))

    def test_go_slice(self):
        concepts = [c for c in self.c.concepts if c.startswith("go:")]
        self.assertEqual(len(concepts), 12)
        self.assertGreaterEqual(len(self._qs("go:")), 40)
        import json
        go_misc = json.loads(
            (ROOT / "content" / "packs" / "go" / "misconceptions.json").read_text())
        self.assertGreaterEqual(len(go_misc), 22)
        for seed in ("go-runs-inline", "goroutine-always-runs",
                     "unbuffered-buffers-one", "rendezvous-instantaneous",
                     "channels-make-everything-safe", "buffered-never-blocks",
                     "close-then-send-fine", "close-twice-fine",
                     "receive-after-close-blocks", "nil-channel-skips-select",
                     "waitgroup-add-inside-goroutine",
                     "loop-var-fresh-per-iteration", "mutex-copied-by-value",
                     "write-before-go-is-enough", "race-only-if-printing"):
            self.assertIn(seed, go_misc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
