"""M6 sim runner: brief → extract → capacity → sketch → weighted probes → failure.

Probes are chosen by applying next_item weighting to the scenario's probe bank
against the user's weakness map. Five dimension scores update mastery and
append sim_history.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import config
from engine.model import ConceptState
from engine.selection import concept_weight
from engine.update import on_answer

from app.cli import (LETTERS, load_content_packs, load_state, log_event,
                     read_choice, save_state)

PACKS_DIR = ROOT / "content" / "packs"
DEFAULT_DIMS = ("constraint_extraction", "estimation", "technique_selection",
                "tradeoff_defense", "failure_reasoning")
_STEP_OWNED = frozenset({
    "constraint_extraction", "estimation", "technique_selection", "failure_reasoning",
})


def load_scenarios() -> list[dict]:
    out = []
    for pack_dir in sorted(p for p in PACKS_DIR.iterdir() if p.is_dir()):
        simd = pack_dir / "sims"
        if not simd.is_dir():
            continue
        for p in sorted(simd.glob("*.json")):
            out.append(json.loads(p.read_text()))
    return out


def _require_concepts(sc: dict) -> list[str]:
    concepts = sc.get("concepts")
    if not concepts:
        raise ValueError(f"scenario {sc.get('id')!r} missing concepts")
    return concepts


def _concept_at(sc: dict, role: str) -> str:
    concepts = _require_concepts(sc)
    if role == "first":
        return concepts[0]
    if role == "last":
        return concepts[-1]
    raise ValueError(f"unknown concept role {role!r}")


def _score_dimensions(sc: dict) -> tuple[str, ...]:
    dims = sc.get("dimensions")
    if dims:
        return tuple(dims)
    return DEFAULT_DIMS


def _probe_uncovered(sc: dict) -> tuple[str, ...]:
    return tuple(d for d in _score_dimensions(sc) if d not in _STEP_OWNED)


def _step_rubric(sc: dict, dimension: str) -> dict:
    entry = (sc.get("step_rubrics") or {}).get(dimension)
    if not entry or not entry.get("rubric"):
        raise ValueError(
            f"scenario {sc.get('id')!r} missing step_rubrics[{dimension!r}]")
    return entry["rubric"]


def _step_concept(sc: dict, dimension: str) -> str:
    entry = (sc.get("step_rubrics") or {}).get(dimension) or {}
    role = entry.get("concept")
    if role in ("first", "last"):
        return _concept_at(sc, role)
    if isinstance(role, str) and role:
        return role
    return _concept_at(sc, "first" if dimension == "constraint_extraction" else "last")


def pick_probes(scenario: dict, content, state: dict, today: str, n: int = 2,
                uncovered: tuple[str, ...] | None = None) -> list:
    """Weakness-weighted, but cover leftover sim dimensions first."""
    if uncovered is None:
        uncovered = _probe_uncovered(scenario)
    bank = list(scenario.get("probes") or [])
    if not bank:
        return []
    scored = []
    for probe in bank:
        cid = probe["concept"]
        s = state.get(cid) or ConceptState()
        w = concept_weight(content, cid, s, today, None, None)
        scored.append((w, probe))
    scored.sort(key=lambda t: -t[0])
    picked: list = []
    seen: set[int] = set()
    for dim in uncovered:
        for _, probe in scored:
            if id(probe) in seen:
                continue
            if probe.get("dimension") != dim:
                continue
            picked.append(probe)
            seen.add(id(probe))
            if len(picked) == n:
                return picked
            break
    for _, probe in scored:
        if id(probe) in seen:
            continue
        picked.append(probe)
        seen.add(id(probe))
        if len(picked) == n:
            break
    return picked


def _read_until_dot(stdin, stdout) -> str | None:
    print("Type your answer. End with a line containing only '.' (or Q):",
          file=stdout)
    lines = []
    while True:
        line = stdin.readline()
        if not line or line.strip().upper() == "Q":
            return None
        if line.strip() == ".":
            break
        lines.append(line.rstrip("\n"))
    return "\n".join(lines)


def _grade_free(content, state, cid, stem, rubric, text, session_type="sim"):
    """Self-grade via rubric presence: nonempty answer scores partial credit.
    Real grading is M4 when TUTOR_LLM_KEY is set."""
    q = type("Q", (), {"level": "reasoning", "options": [], "id": cid})()
    key = os.environ.get("TUTOR_LLM_KEY")
    if key:
        try:
            from grader.rubric_grader import grade
            result = grade({"id": cid, "stem": stem}, rubric, text, key=key)
            correct = result["verdict"] == "correct"
            on_answer(state[cid], q, None, correct,
                      reasoning_score=result["reasoning_score"],
                      content=content, all_state=state)
            log_event(cid, cid, "reasoning", correct,
                      result.get("misconception"), session_type)
            return 3 if result["verdict"] == "correct" else (
                2 if result["verdict"] == "partial" else 1)
        except Exception:
            pass
    covered = 1 if text.strip() else 0
    on_answer(state[cid], q, None, bool(text.strip()),
              evidence_weight=config.SELF_GRADE_WEIGHT if text.strip() else 1.0,
              content=content, all_state=state)
    log_event(cid, cid, "reasoning", bool(text.strip()), None, session_type)
    return 1 + 2 * covered


def _grade_numeric(content, state, q, chosen, today):
    correct = q.options[chosen].correct if q.options else False
    on_answer(state[q.concept], q, chosen, correct,
              today=date.fromisoformat(today),
              content=content, all_state=state)
    log_event(q.id, q.concept, q.level, correct,
              q.options[chosen].misconception if q.options else None, "sim")
    return 3 if correct else 1


def apply_sim_answers(content, state, sc, today, *, constraints, sketch, failure,
                      capacity_choice=None, probe_answers=None) -> dict:
    """Score a completed sim from structured answers (web + tests)."""
    dims = _score_dimensions(sc)
    scores = {d: 1 for d in dims}
    cid = _step_concept(sc, "constraint_extraction")
    state.setdefault(cid, ConceptState())
    scores["constraint_extraction"] = _grade_free(
        content, state, cid, sc["brief"],
        _step_rubric(sc, "constraint_extraction"), constraints or "")
    numeric_qs = [q for q in content.questions
                  if q.kind == "numeric" and q.concept in _require_concepts(sc)]
    if numeric_qs and capacity_choice is not None:
        q = numeric_qs[0]
        if 0 <= capacity_choice < len(q.options):
            scores["estimation"] = _grade_numeric(
                content, state, q, capacity_choice, today)
    cid2 = _step_concept(sc, "technique_selection")
    state.setdefault(cid2, ConceptState())
    scores["technique_selection"] = _grade_free(
        content, state, cid2, "sketch",
        _step_rubric(sc, "technique_selection"), sketch or "")
    answers = list(probe_answers or [])
    for i, probe in enumerate(pick_probes(sc, content, state, today, n=2)):
        text = answers[i] if i < len(answers) else ""
        pcid = probe["concept"]
        state.setdefault(pcid, ConceptState())
        dim = probe.get("dimension")
        if not dim:
            continue
        score = _grade_free(
            content, state, pcid, probe["prompt"],
            probe.get("rubric") or {"criteria": [
                {"id": "p", "points": 2, "model_answer": probe.get("prompt", "")}]},
            text)
        if scores.get(dim, 1) <= 1:
            scores[dim] = score
    fail_cid = _step_concept(sc, "failure_reasoning")
    state.setdefault(fail_cid, ConceptState())
    scores["failure_reasoning"] = _grade_free(
        content, state, fail_cid, "failure modes",
        _step_rubric(sc, "failure_reasoning"), failure or "")
    return scores


def run_sim(stdin=sys.stdin, stdout=sys.stdout) -> int:
    scenarios = load_scenarios()
    if not scenarios:
        print("No sim scenarios authored yet.", file=stdout)
        return 1
    content = load_content_packs()
    state, sim_history = load_state(list(content.concepts))
    today = date.today().isoformat()
    sc = scenarios[0]
    dims = _score_dimensions(sc)
    print(f"# {sc['title']}\n", file=stdout)
    print(sc["brief"], file=stdout)

    scores = {d: 1 for d in dims}

    print("\n— 1. constraints (what did the brief leave unspecified?) —", file=stdout)
    text = _read_until_dot(stdin, stdout)
    if text is None:
        print("Session ended.", file=stdout)
        return 0
    cid = _step_concept(sc, "constraint_extraction")
    state.setdefault(cid, ConceptState())
    scores["constraint_extraction"] = _grade_free(
        content, state, cid, sc["brief"],
        _step_rubric(sc, "constraint_extraction"), text)

    print("\n— 2. capacity math —", file=stdout)
    numeric_qs = [q for q in content.questions
                  if q.kind == "numeric" and q.concept in _require_concepts(sc)]
    if numeric_qs:
        q = numeric_qs[0]
        print(q.stem, file=stdout)
        for j, opt in enumerate(q.options):
            print(f"  {LETTERS[j]}) {opt.text}", file=stdout)
        choice = read_choice(len(q.options), stdin, stdout)
        if choice is None:
            print("Session ended.", file=stdout)
            return 0
        scores["estimation"] = _grade_numeric(content, state, q, choice, today)
    else:
        print("(no numeric item in this scenario's concepts)", file=stdout)

    print("\n— 3. sketch the design (one decision + its cost) —", file=stdout)
    text = _read_until_dot(stdin, stdout)
    if text is None:
        print("Session ended.", file=stdout)
        return 0
    cid2 = _step_concept(sc, "technique_selection")
    state.setdefault(cid2, ConceptState())
    scores["technique_selection"] = _grade_free(
        content, state, cid2, "sketch",
        _step_rubric(sc, "technique_selection"), text)

    print("\n— 4. deep-dive (selected from your weakness map) —", file=stdout)
    for probe in pick_probes(sc, content, state, today, n=2):
        print(probe["prompt"], file=stdout)
        text = _read_until_dot(stdin, stdout)
        if text is None:
            print("Session ended.", file=stdout)
            return 0
        pcid = probe["concept"]
        state.setdefault(pcid, ConceptState())
        dim = probe.get("dimension")
        if not dim:
            continue
        score = _grade_free(
            content, state, pcid, probe["prompt"],
            probe.get("rubric") or {"criteria": [
                {"id": "p", "points": 2, "model_answer": probe.get("prompt", "")}]},
            text)
        if scores.get(dim, 1) <= 1:
            scores[dim] = score

    print("\n— 5. failure modes —", file=stdout)
    text = _read_until_dot(stdin, stdout)
    if text is None:
        print("Session ended.", file=stdout)
        return 0
    fail_cid = _step_concept(sc, "failure_reasoning")
    state.setdefault(fail_cid, ConceptState())
    scores["failure_reasoning"] = _grade_free(
        content, state, fail_cid, "failure modes",
        _step_rubric(sc, "failure_reasoning"), text)

    sim_history.append({"scenario": sc["id"], "date": today,
                        "dimension_scores": scores})
    save_state(state, sim_history)
    print("\n— dimension scores —", file=stdout)
    for d in dims:
        print(f"  {d}: {scores[d]}/3", file=stdout)
    print("sim recorded.", file=stdout)
    return 0
