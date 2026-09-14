"""on_answer: mastery updates, scheduling, evidence weights, misconception bumps."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from . import config
from .model import ConceptState, Question


def _today(now: Optional[date] = None) -> date:
    return now or date.today()


def on_answer(state: ConceptState, q: Question, chosen_index: Optional[int],
              correct: bool, today: Optional[date] = None,
              evidence_weight: float = 1.0,
              self_graded: bool = False,
              reasoning_score: Optional[int] = None,
              content=None, all_state: Optional[dict] = None) -> Optional[dict]:
    """Mutates state per the update rules. If the chosen option carries a
    misconception tag, its weight is bumped. Returns a diagnosis directive
    when wrong and fails >= 2 (caller runs diagnose and follows it)."""
    t = _today(today)
    level = q.level if q.level in config.LEVELS else "recall"
    if reasoning_score is not None:
        evidence_weight *= reasoning_score / 3.0

    if correct:
        gain = (config.MASTERED - state.mastery[level]) * config.UP * evidence_weight
        state.mastery[level] += gain
        state.fails = 0
        state.due = (t + timedelta(days=config.REVIEW_DAYS)).isoformat()
    else:
        state.mastery[level] *= config.DOWN
        state.fails += 1
        state.due = (t + timedelta(days=config.FAIL_RETRY_DAYS)).isoformat()
        if chosen_index is not None and 0 <= chosen_index < len(q.options):
            tag = q.options[chosen_index].misconception
            if tag:
                state.misconceptions[tag] = min(
                    1.0, state.misconceptions.get(tag, 0.0) + config.EVIDENCE)

    _update_resolves(state, q, correct, t)
    state.asked += 1

    if not correct and state.fails >= 2:
        if content is not None and all_state is not None:
            from .diagnose import diagnose
            return diagnose(content, all_state, q.concept)
        return diagnose_result(state)


def _python_exec(q: Question) -> bool:
    verify = getattr(q, "verify", None)
    return bool(verify) and verify.get("backend") == "python_exec"


def _update_resolves(state: ConceptState, q: Question, correct: bool, t: date) -> None:
    """M8: python_exec items reappear cold at 3d / 1w / 3w; a miss resets the chain."""
    if not _python_exec(q):
        return
    rec = state.resolves.get(q.id) or {"step": 0}
    step = int(rec.get("step", 0) or 0)
    if correct:
        idx = min(step, len(config.RESOLVE_DAYS) - 1)
        due = (t + timedelta(days=config.RESOLVE_DAYS[idx])).isoformat()
        state.resolves[q.id] = {
            "due": due,
            "step": min(step + 1, len(config.RESOLVE_DAYS) - 1),
        }
    else:
        state.resolves[q.id] = {
            "due": (t + timedelta(days=config.FAIL_RETRY_DAYS)).isoformat(),
            "step": 0,
        }


def diagnose_result(state: ConceptState) -> dict:
    """Diagnosis directive for the concept this state belongs to (prereq check
    is resolved by the caller, who knows the concept graph)."""
    from .diagnose import diagnose_state_only
    return diagnose_state_only(state)


def self_grade(state: ConceptState, q: Question, said_correct: bool,
               today: Optional[date] = None) -> Optional[dict]:
    """Self-graded L2 joint: self-says-correct counts at discounted weight 0.7;
    self-says-wrong is trusted at full weight."""
    return on_answer(state, q, None, said_correct, today,
                     evidence_weight=(config.SELF_GRADE_WEIGHT if said_correct else 1.0),
                     self_graded=True)
