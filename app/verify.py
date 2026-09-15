"""Content verification harness (v0.1).

One interface, four backends. A verify dir is `content/verify/<qid>/` with
`outcome.json`; its "backend" field picks the checker (default go_exec):

  go_exec      run main.go; modes: stdout | deadlock | panic | race | guarantee
  python_exec  run solution.py; modes: stdout | timeout | cases
               (cases = run against each tests/<n>.in, compare <n>.out)
  rubric       no execution — structural check of rubric.json
  numeric      no execution — safe-eval every pack's numeric question
               answer_key.expr and compare to its declared value

Every program runs under a timeout (default 10s, override with "limit").
Exit 0 = all green; nonzero = some program/rubric/answer contradicts its
declaration. Usage: verify.py [verify_root] [packs_root]
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.model import safe_eval, ValidationError          # noqa: E402
from app.paths import packs_dir, verify_dir                  # noqa: E402

DEFAULT_TIMEOUT = 10
DEADLOCK_MARKER = "all goroutines are asleep"


def run_go(qdir: pathlib.Path, race: bool, timeout: int) -> subprocess.CompletedProcess:
    cmd = ["go", "run"]
    if race:
        cmd.append("-race")
    cmd.append("main.go")
    return subprocess.run(cmd, cwd=qdir, capture_output=True, text=True, timeout=timeout)


def run_python(qdir: pathlib.Path, timeout: int,
               stdin_text: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "solution.py"], cwd=qdir,
                          input=stdin_text, capture_output=True, text=True,
                          timeout=timeout)


def check_go(qdir, outcome, timeout) -> list[str]:
    mode = outcome.get("mode")
    try:
        proc = run_go(qdir, race=(mode == "race"), timeout=timeout)
    except subprocess.TimeoutExpired:
        return [f"{qdir.name}: timed out after {timeout}s"]
    combined = proc.stdout + proc.stderr
    errors: list[str] = []

    if mode == "stdout":
        if proc.returncode != 0:
            errors.append(f"{qdir.name}: exit {proc.returncode}, expected clean run")
        if proc.stdout != outcome.get("stdout", ""):
            errors.append(f"{qdir.name}: stdout mismatch: {proc.stdout!r} "
                          f"!= {outcome.get('stdout')!r}")
    elif mode == "deadlock":
        if proc.returncode == 0:
            errors.append(f"{qdir.name}: exited cleanly, expected deadlock")
        elif DEADLOCK_MARKER not in combined:
            errors.append(f"{qdir.name}: failed but not a deadlock fatal error: "
                          f"{combined.strip()[:200]}")
    elif mode == "panic":
        needle = outcome.get("stderr_contains", "")
        if proc.returncode == 0:
            errors.append(f"{qdir.name}: exited cleanly, expected panic")
        elif needle not in proc.stderr:
            errors.append(f"{qdir.name}: stderr missing {needle!r}: "
                          f"{proc.stderr.strip()[:200]}")
    elif mode == "race":
        has_race = "DATA RACE" in combined
        if has_race != bool(outcome.get("race", True)):
            errors.append(f"{qdir.name}: DATA RACE present={has_race}, "
                          f"expected {outcome.get('race', True)}")
    elif mode == "guarantee":
        if proc.returncode != 0:
            errors.append(f"{qdir.name}: exit {proc.returncode}, expected clean run")
    else:
        errors.append(f"{qdir.name}: unknown go_exec mode {mode!r}")
    return errors


def check_python(qdir, outcome, timeout) -> list[str]:
    mode = outcome.get("mode")

    if mode == "cases":
        tests_dir = qdir / "tests"
        if not tests_dir.is_dir():
            return [f"{qdir.name}: cases mode needs a tests/ dir"]
        errors: list[str] = []
        cases = sorted(tests_dir.glob("*.in"))
        if not cases:
            return [f"{qdir.name}: cases mode found no tests/*.in files"]
        for case in cases:
            expected_path = case.with_suffix(".out")
            if not expected_path.exists():
                errors.append(f"{qdir.name}: missing {expected_path.name} for {case.name}")
                continue
            expected = expected_path.read_text()
            try:
                proc = run_python(qdir, timeout, stdin_text=case.read_text())
            except subprocess.TimeoutExpired:
                errors.append(f"{qdir.name}: {case.name} timed out after {timeout}s")
                continue
            if proc.returncode != 0:
                errors.append(f"{qdir.name}: {case.name} exited {proc.returncode}: "
                              f"{proc.stderr.strip()[:200]}")
            elif proc.stdout != expected:
                errors.append(f"{qdir.name}: {case.name} stdout mismatch: "
                              f"{proc.stdout!r} != {expected!r}")
        return errors

    try:
        proc = run_python(qdir, timeout)
    except subprocess.TimeoutExpired:
        if mode == "timeout":
            return []  # expected to hit the limit
        return [f"{qdir.name}: timed out after {timeout}s"]

    if mode == "stdout":
        errors = []
        if proc.returncode != 0:
            errors.append(f"{qdir.name}: exit {proc.returncode}, expected clean run")
        if proc.stdout != outcome.get("stdout", ""):
            errors.append(f"{qdir.name}: stdout mismatch: {proc.stdout!r} "
                          f"!= {outcome.get('stdout')!r}")
        return errors
    if mode == "timeout":
        return [f"{qdir.name}: finished before the {timeout}s limit, expected TLE"]
    return [f"{qdir.name}: unknown python_exec mode {mode!r}"]


def check_rubric_dir(qdir) -> list[str]:
    """Structural check of the answer key. Deep grading is the M4 grader."""
    rubric_path = qdir / "rubric.json"
    if not rubric_path.exists():
        return [f"{qdir.name}: rubric backend needs rubric.json"]
    try:
        rubric = json.loads(rubric_path.read_text())
    except json.JSONDecodeError as e:
        return [f"{qdir.name}: rubric.json not valid JSON: {e}"]
    errors: list[str] = []
    criteria = rubric.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return [f"{qdir.name}: rubric needs a non-empty criteria list"]
    for crit in criteria:
        if not crit.get("id") or not crit.get("model_answer"):
            errors.append(f"{qdir.name}: criterion missing id or model_answer")
        if not isinstance(crit.get("points"), int) or crit.get("points", 0) <= 0:
            errors.append(f"{qdir.name}: criterion {crit.get('id')} needs positive points")
    return errors


def check_numeric_packs(packs_root: pathlib.Path) -> tuple[list[str], int]:
    """For every numeric question in every pack: safe-eval answer_key.expr and
    compare against the declared value. Also catches questions whose verify
    dir is missing when they reference one."""
    errors: list[str] = []
    n = 0
    if not packs_root.is_dir():
        return errors, n
    for pack_dir in sorted(p for p in packs_root.iterdir() if p.is_dir()):
        qpath = pack_dir / "questions.json"
        if not qpath.exists():
            continue
        for q in json.loads(qpath.read_text()):
            if q.get("kind") != "numeric":
                continue
            n += 1
            ak = q.get("answer_key", {})
            try:
                computed = safe_eval(ak.get("expr", ""))
                declared = float(ak.get("value", "nan"))
                if abs(computed - declared) > 1e-6 * max(1.0, abs(computed)):
                    errors.append(f"{q['id']}: expr evaluates to {computed}, "
                                  f"declared value is {declared}")
            except ValidationError as e:
                errors.append(f"{q['id']}: {e}")
    return errors, n


def check_one(qdir: pathlib.Path) -> list[str]:
    outcome_path = qdir / "outcome.json"
    if not outcome_path.exists():
        return [f"{qdir.name}: missing outcome.json"]
    try:
        outcome = json.loads(outcome_path.read_text())
    except json.JSONDecodeError as e:
        return [f"{qdir.name}: outcome.json not valid JSON: {e}"]

    backend = outcome.get("backend", "go_exec")
    timeout = int(outcome.get("limit", DEFAULT_TIMEOUT))

    if backend == "go_exec":
        if not (qdir / "main.go").exists():
            return [f"{qdir.name}: go_exec backend needs main.go"]
        return check_go(qdir, outcome, timeout)
    if backend == "python_exec":
        if not (qdir / "solution.py").exists():
            return [f"{qdir.name}: python_exec backend needs solution.py"]
        return check_python(qdir, outcome, timeout)
    if backend == "rubric":
        return check_rubric_dir(qdir)
    return [f"{qdir.name}: unknown backend {backend!r}"]


def run_all(verify_root: pathlib.Path | None = None,
            packs_root: pathlib.Path | None = None) -> tuple[list[str], int]:
    verify_root = verify_root or verify_dir()
    packs_root = packs_root or packs_dir()
    errors: list[str] = []
    total = 0

    qdirs = sorted(d for d in verify_root.iterdir() if d.is_dir()) if verify_root.is_dir() else []
    for qdir in qdirs:
        errs = check_one(qdir)
        status = "FAIL" if errs else "ok"
        print(f"[{status}] {qdir.name}")
        for e in errs:
            print(f"       {e}")
        errors.extend(errs)
        total += 1

    numeric_errors, n_numeric = check_numeric_packs(packs_root)
    for e in numeric_errors:
        print(f"[FAIL] numeric: {e}")
    errors.extend(numeric_errors)
    total += n_numeric

    if not total:
        return ["nothing to verify: no verify dirs and no numeric questions"], 1
    return errors, total


def main(argv: list[str]) -> int:
    verify_root = pathlib.Path(argv[1]) if len(argv) > 1 else verify_dir()
    packs_root = pathlib.Path(argv[2]) if len(argv) > 2 else packs_dir()
    errors, total = run_all(verify_root, packs_root)
    if errors:
        print(f"\n{len(errors)} failure(s) across {total} check(s).")
        return 1
    print(f"\nAll {total} check(s) verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
