"""Concept selection, frontier/fade computation, next_item."""
from __future__ import annotations

from typing import Optional

from . import config
from .model import ConceptState, Content, ExampleStep, Question


def fade_level(state: ConceptState) -> str:
    if state.mastery["recall"] < config.FADE_L1:
        return "L0"
    if state.mastery["recall"] < config.FADE_L2:
        return "L1"
    if state.mastery["application"] < config.FADE_L2:
        return "L2"
    return "L3"


def concept_weight(content: Content, cid: str, state: ConceptState,
                   today: str, goal: Optional[dict],
                   last_concept: Optional[str]) -> float:
    if cid == last_concept:
        return 0.0  # interleaving: never twice in a row
    w = max(0.0, (config.MASTERED - state.mastery[state.frontier()])) * content.centrality(cid)
    if w == 0.0:
        return 0.0
    if state.fails >= 2:
        w *= 3
    if state.due is not None and state.due <= today:
        w *= 2
    if goal and cid in goal.get("required", []):
        w *= config.GOAL_WEIGHT
    return w


def top_misconception(state: ConceptState) -> Optional[str]:
    if not state.misconceptions:
        return None
    mid, w = max(state.misconceptions.items(), key=lambda kv: kv[1])
    return mid if w >= config.TRAP else None


def resolve_overdue(state: ConceptState, today: str) -> bool:
    return any(isinstance(rec, dict) and rec.get("due") and rec["due"] <= today
               for rec in (state.resolves or {}).values())


def pick_item(content: Content, cid: str, state: ConceptState,
              session_type: str = "quick_drill",
              today: Optional[str] = None) -> Optional[Question | ExampleStep]:
    """Question, or a worked-example step when fade ≤ L2 and not recently shown."""
    if session_type == "quick_drill":
        fade = fade_level(state)
        seen = getattr(state, "fade_seen", []) or []
        if fade in ("L0", "L1", "L2") and fade not in seen:
            ex = content.examples.get(cid)
            if ex is not None:
                joint = next(iter(ex.joints), None) if fade == "L1" else None
                return ExampleStep(concept=cid, fade=fade, example=ex, joint=joint)
    return pick_question(content, cid, state, session_type, today=today)


def pick_question(content: Content, cid: str, state: ConceptState,
                  session_type: str = "quick_drill",
                  today: Optional[str] = None) -> Optional[Question]:
    """Within the concept at its frontier level: trap questions first
    (option tagged with the user's top misconception, weight >= 0.7),
    then least-recently-asked; fall back to any level.
    quick_drill serves MCQ-style items; design_session serves design_defense."""
    qs = content.questions_for(cid)
    if session_type == "design_session":
        qs = [q for q in qs if q.kind == "free_text"]
    elif session_type == "quick_drill":
        qs = [q for q in qs if q.kind != "free_text"]
    if not qs:
        return None
    if today:
        due_qs = [cand for cand in qs
                  if isinstance(state.resolves.get(cand.id), dict)
                  and state.resolves[cand.id].get("due")
                  and state.resolves[cand.id]["due"] <= today]
        if due_qs:
            due_qs.sort(key=lambda q: (q.last_asked is not None, q.last_asked or ""))
            return due_qs[0]
    level = state.frontier()
    at_level = [q for q in qs if q.level == level]
    pool = at_level or qs
    trap = top_misconception(state)
    if trap is not None:
        traps = [q for q in pool if q.trap_options(trap)]
        if traps:
            pool = traps
    pool.sort(key=lambda q: (q.last_asked is not None, q.last_asked or ""))
    return pool[0]


def next_item(content: Content, state: dict[str, ConceptState], today: str,
              goal: Optional[dict] = None,
              last_concept: Optional[str] = None) -> Optional[tuple[str, Question]]:
    """Returns (concept_id, question) for the highest-weighted concept."""
    best_cid, best_w = None, 0.0
    for cid in content.concepts:
        s = state.get(cid)
        if s is None:
            continue
        w = concept_weight(content, cid, s, today, goal, last_concept)
        if w > best_w:
            best_cid, best_w = cid, w
    if best_cid is None:
        return None
    q = pick_item(content, best_cid, state[best_cid], today=today)
    if q is None:
        return None
    return best_cid, q


def compose_session(content: Content, state: dict[str, ConceptState], today: str,
                    n: int = 6, goal: Optional[dict] = None,
                    last_concept: Optional[str] = None,
                    session_type: str = "quick_drill") -> list[tuple[str, Question]]:
    """quick_drill N items: ≥ N/3 spaced-due, ≥ N/3 weakest deficits, rest
    frontier; never the same concept twice consecutively. design_session
    (N=2–3) serves free-text on the weakest concepts."""
    if session_type == "design_session":
        n = min(n, 3)
        candidates = [cid for cid in content.concepts
                      if any(q.kind == "free_text"
                             for q in content.questions_for(cid))]
        weighted = sorted(
            candidates,
            key=lambda c: -concept_weight(content, c, state.get(c, ConceptState()),
                                          today, goal, last_concept))
        out: list[tuple[str, Question]] = []
        last = last_concept
        for cid in weighted:
            if len(out) >= n:
                break
            if cid == last:
                continue
            q = pick_item(content, cid, state.get(cid, ConceptState()),
                          session_type, today=today)
            if q:
                out.append((cid, q))
                last = cid
        return out
    due = []
    for cid, s in state.items():
        concept_due = (s.due is not None and s.due <= today
                       and s.mastery[s.frontier()] < config.MASTERED)
        if concept_due or resolve_overdue(s, today):
            due.append(cid)
    weakest = sorted(
        state,
        key=lambda c: state[c].mastery[state[c].frontier()] / content.centrality(c))
    plan: list[str] = []
    last = last_concept
    remaining = n

    def take(candidates: list[str], limit: int) -> None:
        nonlocal last, remaining
        for cid in candidates:
            if limit <= 0 or remaining <= 0:
                return
            if cid == last or cid in plan or not content.questions_for(cid):
                continue
            plan.append(cid)
            last = cid
            remaining -= 1
            limit -= 1

    take(due, n // 3)
    take(weakest, (n + 2) // 3 - len(plan) + n // 3)  # at least N/3 deficit items overall
    # remainder: general frontier order by weight
    weighted = sorted(
        (c for c in content.concepts if c in state),
        key=lambda c: -concept_weight(content, c, state[c], today, goal, last))
    take(weighted, remaining)
    while remaining > 0:  # final fallback if interleave constraints starved us
        for cid in content.concepts:
            if remaining <= 0:
                break
            if cid in plan or not content.questions_for(cid):
                continue
            if cid == plan[-1] if plan else False:
                continue
            plan.append(cid)
            remaining -= 1
        else:
            break

    out: list[tuple[str, Question]] = []
    for cid in plan:
        q = pick_item(content, cid, state.get(cid, ConceptState()),
                      session_type, today=today)
        if q is None:
            continue
        out.append((cid, q))
    return out


def report(content: Content, state: dict[str, ConceptState], today: str) -> list[dict]:
    """Weakness map sorted by deficit × centrality. Each row: concept, frontier,
    fade, fails, active misconceptions (≥ 0.7), overdue."""
    rows: list[dict] = []
    for cid in content.concepts:
        s = state.get(cid) or ConceptState()
        deficit = max(0.0, config.MASTERED - s.mastery[s.frontier()])
        rows.append({
            "concept": cid,
            "frontier": s.frontier(),
            "fade": fade_level(s),
            "fails": s.fails,
            "misconceptions": [m for m, w in s.misconceptions.items() if w >= config.TRAP],
            "overdue": bool(s.due and s.due <= today),
            "score": deficit * content.centrality(cid),
        })
    rows.sort(key=lambda r: -r["score"])
    return rows
