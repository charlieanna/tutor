"""Harness tests: seeded examples green via make verify, plus the negative
test — a deliberately corrupted program must make the harness exit nonzero."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFY = ROOT / "app" / "verify.py"


def run_harness(root: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(VERIFY), root],
                          capture_output=True, text=True, timeout=60)


class TestVerifyHarness(unittest.TestCase):
    def test_seed_examples_green(self):
        r = run_harness(str(ROOT / "tests" / "seed" / "verify"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[ok] q101", r.stdout)  # stdout mode
        self.assertIn("[ok] q102", r.stdout)  # deadlock mode
        self.assertIn("[ok] q103", r.stdout)  # panic mode

    def test_corrupted_program_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            # corrupt the stdout example: print the wrong thing
            for qid in ("q101", "q102", "q103"):
                shutil.copytree(ROOT / "tests" / "seed" / "verify" / qid, tmp_path / qid)
            (tmp_path / "q101" / "main.go").write_text(
                "package main\n\nimport \"fmt\"\n\nfunc main() {\n\tfmt.Println(99)\n}\n")
            r = run_harness(tmp)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("FAIL", r.stdout)
            self.assertIn("stdout mismatch", r.stdout)
            # the uncorrupted programs still pass
            self.assertIn("[ok] q102", r.stdout)
            self.assertIn("[ok] q103", r.stdout)

    def test_missing_outcome_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            shutil.copytree(ROOT / "tests" / "seed" / "verify" / "q101", tmp_path / "q101")
            (tmp_path / "q101" / "outcome.json").unlink()
            r = run_harness(tmp)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("missing outcome.json", r.stdout)

    def test_clean_program_fails_deadlock_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            shutil.copytree(ROOT / "tests" / "seed" / "verify" / "q102", tmp_path / "q102")
            (tmp_path / "q102" / "main.go").write_text(
                "package main\n\nfunc main() {}\n")
            r = run_harness(tmp)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("expected deadlock", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
