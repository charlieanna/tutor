"""Tutor CLI: drill [n] [--design] | report | reset.

All engine calls are pure; this module owns the only I/O: pack content,
state file, events log, stdin/stdout.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import date, datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.diagnose import diagnose                        # noqa: E402
from engine.model import (ConceptState, ValidationError,    # noqa: E402
                          load_content, sim_history_from_json,
                          state_from_json, state_to_json)
from engine.selection import compose_session, report        # noqa: E402
from engine.update import on_answer, self_grade             # noqa: E402
from app.paths import packs_dir                             # noqa: E402

STATE_PATH = pathlib.Path(os.environ.get("TUTOR_STATE", ROOT / "state.json"))
EVENTS_PATH = pathlib.Path(os.environ.get("TUTOR_EVENTS", ROOT / "events.jsonl"))
LETTERS = "ABCDEFGH"
FREE_TEXT_KIND = "free_text"


# ------------------------------------------------------------------- loading

def _pack_namespace(pack_concepts: dict, pack_dir: pathlib.Path) -> str:
    if pack_concepts:
        first = next(iter(pack_concepts))
        if ":" in first:
            return first.split(":", 1)[0]
    return pack_dir.name


def load_packs() -> tuple[dict, list[dict], dict, dict]:
    """Merge all packs under content/packs/ (schemas are namespaced; merging
    is safe — cross-pack prereqs are rejected by the validator)."""
    concepts: dict = {}
    questions: list[dict] = []
    misconceptions: dict = {}
    examples: dict = {}
    packs_root = packs_dir()
    if not packs_root.is_dir():
        return concepts, questions, misconceptions, examples
    for pack_dir in sorted(p for p in packs_root.iterdir() if p.is_dir()):
        pack_concepts = json.loads((pack_dir / "concepts.json").read_text())
        concepts.update(pack_concepts)
        questions.extend(json.loads((pack_dir / "questions.json").read_text()))
        misconceptions.update(json.loads((pack_dir / "misconceptions.json").read_text()))
        exdir = pack_dir / "worked_examples"
        if exdir.is_dir():
            ns = _pack_namespace(pack_concepts, pack_dir)
            for jf in exdir.glob("*.json"):
                data = json.loads(jf.read_text())
                cid = data.get("concept") or f"{ns}:{jf.stem}"
                examples[cid] = data
    return concepts, questions, misconceptions, examples


def load_content_packs():
    return load_content(*load_packs())


def load_state(all_concepts: list[str]) -> tuple[dict[str, ConceptState], list]:
    sim_history: list = []
    if STATE_PATH.exists():
        raw = STATE_PATH.read_text()
        state = state_from_json(raw)
        sim_history = sim_history_from_json(raw)
    else:
        state = {}
    for cid in all_concepts:
        state.setdefault(cid, ConceptState())
    return state, sim_history


def save_state(state: dict[str, ConceptState], sim_history: list | None = None) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(state_to_json(state, sim_history=sim_history or []))


def load_goal() -> dict | None:
    goal_path = packs_dir().parent / "goals.json"
    if goal_path.exists():
        return json.loads(goal_path.read_text())
    return None


def log_event(qid: str, concept: str, level: str, correct: bool,
              misconception: str | None, session_type: str) -> None:
    """Append-only calibration log. One line per answer."""
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": datetime.now(timezone.utc).isoformat(),
             "user": os.environ.get("TUTOR_USER", "local"),
             "qid": qid, "concept": concept, "level": level,
             "correct": correct, "misconception": misconception,
             "session_type": session_type}
    with EVENTS_PATH.open("a") as f:
        f.write(json.dumps(event) + "\n")


# ------------------------------------------------------------------- drill

def run_drill(n: int, stdin=sys.stdin, stdout=sys.stdout,
              session_type: str = "quick_drill") -> None:
    content = load_content_packs()
    state, sim_history = load_state(list(content.concepts))
    goal = load_goal()
    today = date.today().isoformat()

    plan = compose_session(content, state, today, n=n, goal=goal,
                           session_type=session_type)
    if not plan:
        print("No questions available.", file=stdout)
        return

    for i, (cid, q) in enumerate(plan, 1):
        if getattr(q, "kind", None) == "example":
            print(f"\n[{i}/{len(plan)}] {cid} worked example ({q.fade})",
                  file=stdout)
            print(q.example.title, file=stdout)
            if q.fade not in state[cid].fade_seen:
                state[cid].fade_seen.append(q.fade)
            save_state(state, sim_history)
            continue
        if q.kind == FREE_TEXT_KIND:
            if not run_free_text(content, state, cid, q, i, len(plan),
                                 session_type, stdin, stdout):
                break
            save_state(state, sim_history)
            continue
        s = state[cid]
        print(f"\n[{i}/{len(plan)}] {cid} ({q.level})", file=stdout)
        print(q.stem, file=stdout)
        for j, opt in enumerate(q.options):
            print(f"  {LETTERS[j]}) {opt.text}", file=stdout)

        choice = read_choice(len(q.options), stdin, stdout)
        if choice is None:
            print("Session ended.", file=stdout)
            break
        correct = q.options[choice].correct
        tag = q.options[choice].misconception

        directive = on_answer(s, q, choice, correct,
                              content=content, all_state=state)
        q.last_asked = today
        log_event(q.id, cid, q.level, correct, tag, session_type)
        if correct:
            print("✓ correct", file=stdout)
            _maybe_probe(q, stdin, stdout)
        else:
            print(f"✗ wrong — {q.explanation or 'see explanation'}", file=stdout)
            if tag:
                m = content.misconceptions[tag]
                print(f"  Misconception: {m.belief}", file=stdout)
                print(f"  Clarification: {m.clarification}", file=stdout)
            # ruled_out_by: why this option was impossible given the stem
            if not correct and q.options[choice].ruled_out_by:
                print(f"  Ruled out: {q.options[choice].ruled_out_by}", file=stdout)

        if directive is None and not correct and s.fails >= 2:
            directive = diagnose(content, state, cid)
        if directive:
            print(f"  → diagnosis: {directive['message']}", file=stdout)
            if directive["type"] == "prereq":
                drill_prereq(content, state, directive["drill"], stdin, stdout)

        save_state(state, sim_history)

    print("\nSession complete. State saved.", file=stdout)


def run_free_text(content, state, cid, q, i, total, session_type,
                  stdin, stdout) -> bool:
    """Long-form design round: answer, reveal rubric, self-grade per criterion.
    All criteria covered -> correct at self-grade weight 0.7; any miss -> wrong
    at full weight. The M4 rubric grader replaces the self-grade step behind
    TUTOR_LLM_KEY."""
    print(f"\n[{i}/{total}] {cid} ({q.level}) — design defense", file=stdout)
    print(f"{q.stem} (≤ {q.word_limit} words)", file=stdout)
    print("\nType your answer. End with a line containing only '.' "
          "(or 'Q' to quit):", file=stdout)
    lines = []
    while True:
        line = stdin.readline()
        if not line or line.strip().upper() == "Q":
            print("Session ended.", file=stdout)
            return False
        if line.strip() == ".":
            break
        lines.append(line.rstrip("\n"))
    answer = "\n".join(lines)
    if not answer.strip():
        print("(no answer given)", file=stdout)

    key = os.environ.get("TUTOR_LLM_KEY")
    if key:
        try:
            from grader.rubric_grader import GradeError, grade
            result = grade({"id": q.id, "stem": q.stem}, q.rubric, answer, key=key)
            correct = result["verdict"] == "correct"
            on_answer(state[cid], q, None, correct,
                      reasoning_score=result["reasoning_score"],
                      content=content, all_state=state)
            q.last_asked = date.today().isoformat()
            log_event(q.id, cid, q.level, correct, result.get("misconception"),
                      session_type)
            print(f"✓ grader: {result['verdict']} "
                  f"(reasoning {result['reasoning_score']}/3)", file=stdout)
            return True
        except Exception as e:
            print(f"(grader unavailable: {e}; self-grade)", file=stdout)

    print("\n— rubric —", file=stdout)
    all_yes = True
    for crit in q.rubric["criteria"]:
        print(f"  [{crit['id']}] ({crit['points']} pts) {crit['model_answer']}",
              file=stdout)
        while True:
            resp = stdin.readline()
            if not resp:
                return False
            resp = resp.strip().lower()
            if resp in ("y", "n"):
                break
            print("  y or n:", file=stdout)
        if resp != "y":
            all_yes = False
    for mid, belief in q.rubric.get("misconceptions", {}).items():
        print(f"  misconception check — {mid}: {belief}", file=stdout)

    self_grade(state[cid], q, all_yes)
    q.last_asked = date.today().isoformat()
    log_event(q.id, cid, q.level, all_yes, None, session_type)
    print("✓ recorded (self-graded, weight 0.7)" if all_yes
          else "✗ recorded — gaps above; re-test scheduled tomorrow", file=stdout)
    return True


def drill_prereq(content, state, prereq_id, stdin, stdout) -> None:
    """Follow a prereq diagnosis: one immediate question on the upstream gap."""
    qs = ([q for q in content.questions_for(prereq_id)
           if q.level == state[prereq_id].frontier()]
          or content.questions_for(prereq_id))
    if not qs:
        return
    q = qs[0]
    print(f"\n— prereq drill: {prereq_id} —", file=stdout)
    print(q.stem, file=stdout)
    for j, opt in enumerate(q.options):
        print(f"  {LETTERS[j]}) {opt.text}", file=stdout)
    choice = read_choice(len(q.options), stdin, stdout)
    if choice is None:
        return
    correct = q.options[choice].correct
    on_answer(state[prereq_id], q, choice, correct)
    log_event(q.id, prereq_id, q.level, correct,
              q.options[choice].misconception, "prereq_drill")
    print("✓ correct" if correct else "✗ wrong", file=stdout)


def read_choice(n_options: int, stdin, stdout) -> int | None:
    while True:
        line = stdin.readline()
        if not line:
            return None
        line = line.strip().upper()
        if line == "Q":
            return None
        if len(line) == 1 and line in LETTERS[:n_options]:
            return LETTERS.index(line)
        print(f"Pick A–{LETTERS[n_options - 1]} (or Q to quit):", file=stdout)


WHY_RUBRIC = {"criteria": [
    {"id": "why", "points": 2,
     "model_answer": "cites the stem fact that uniquely forces the chosen option"}]}


def _maybe_probe(q, stdin, stdout) -> None:
    from grader.rubric_grader import GradeError, grade, should_probe
    if not os.environ.get("TUTOR_LLM_KEY"):
        return
    if not should_probe(q.level, True):
        return
    print("why? (one sentence, then Enter)", file=stdout)
    line = stdin.readline()
    if not line or not os.environ.get("TUTOR_LLM_KEY"):
        return
    try:
        grade({"id": q.id, "stem": q.stem}, WHY_RUBRIC, line.strip(),
              key=os.environ.get("TUTOR_LLM_KEY"))
    except GradeError:
        pass


# ------------------------------------------------------------------- report

def run_report(stdout=sys.stdout) -> None:
    content = load_content_packs()
    state, _ = load_state(list(content.concepts))
    today = date.today().isoformat()

    print(f"{'concept':<30} {'frontier':<12} {'fade':<4} {'fails':<6} "
          f"{'misconceptions':<28} overdue", file=stdout)
    for row in report(content, state, today):
        miscon = ",".join(row["misconceptions"]) or "-"
        overdue = "yes" if row["overdue"] else "no"
        print(f"{row['concept']:<30} {row['frontier']:<12} {row['fade']:<4} "
              f"{row['fails']:<6} {miscon:<28} {overdue}", file=stdout)


# ------------------------------------------------------------------- reset

def run_reset(force: bool = False) -> None:
    if STATE_PATH.exists():
        if not force and input("Delete all progress? [y/N] ").strip().lower() != "y":
            print("Aborted.")
            return
        STATE_PATH.unlink()
    print("State reset.")


def main(argv: list[str]) -> int:
    cmds = ("drill", "design", "sim", "report", "verify", "reset")
    if len(argv) < 2 or argv[1] not in cmds:
        print("usage: cli.py drill [n] | design [n] | sim | report | verify | reset",
              file=sys.stderr)
        return 2
    try:
        if argv[1] == "drill":
            session_type = "design_session" if "--design" in argv else "quick_drill"
            args = [a for a in argv[2:] if a != "--design"]
            n = int(args[0]) if args else (3 if session_type == "design_session" else 6)
            run_drill(n, session_type=session_type)
        elif argv[1] == "design":
            n = int(argv[2]) if len(argv) > 2 else 3
            run_drill(n, session_type="design_session")
        elif argv[1] == "sim":
            from app.sim import run_sim
            return run_sim()
        elif argv[1] == "report":
            run_report()
        elif argv[1] == "verify":
            from app.verify import main as verify_main
            return verify_main(["verify", *argv[2:]])
        else:
            run_reset(force="--yes" in argv or "-f" in argv)
    except ValidationError as e:
        print(f"content invalid: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
