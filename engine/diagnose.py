"""Diagnosis tree. First match wins."""
from __future__ import annotations

from . import config
from .model import ConceptState, Content


def diagnose(content: Content, state: dict, cid: str) -> dict:
    """state: concept_id -> ConceptState. Missing prereqs count as recall 0."""
    s = state[cid]

    # 1. any prereq with recall < 0.7 -> the gap is upstream
    for p in content.concepts[cid]["prereqs"]:
        recall = state[p].mastery["recall"] if p in state else 0.0
        if recall < config.RECALL_OK:
            return {"type": "prereq", "drill": p,
                    "message": f"the gap is upstream; drilling {p}"}

    return diagnose_state_only(s)


def diagnose_state_only(s: ConceptState) -> dict:
    # 2. top misconception >= 0.7 -> re-embed as a distractor
    if s.misconceptions:
        mid, w = max(s.misconceptions.items(), key=lambda kv: kv[1])
        if w >= config.TRAP:
            return {"type": "misconception", "trap": mid,
                    "message": f"re-testing with {mid} embedded as a distractor"}

    # 3. recall >= 0.7 and application < 0.7 -> knows facts, can't apply
    if s.mastery["recall"] >= config.RECALL_OK and s.mastery["application"] < config.RECALL_OK:
        return {"type": "worked_example", "fade": "L2",
                "message": "knows the facts but cannot apply; worked example at L2"}

    # 4. else -> never landed; re-teach
    return {"type": "re_teach", "fade": "L0",
            "message": "never landed; re-teach from the full example"}
