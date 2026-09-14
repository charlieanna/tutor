"""M4 — the only LLM module. Env-gated. Structured JSON with one retry.

Tests inject `transport`; production uses urllib against TUTOR_LLM_URL.
The engine and CLI run fully with no key set (callers catch GradeError).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from engine import config

VERDICTS = ("correct", "partial", "wrong")
TRANSPORT = Callable[[str], str]


class GradeError(Exception):
    pass


def should_probe(level: str, correct: bool, rng=None) -> bool:
    """After a correct reasoning-level MCQ, probe 'why?' at PROBE_RATE."""
    if not correct or level != "reasoning":
        return False
    draw = rng() if rng is not None else __import__("random").random()
    return draw < config.PROBE_RATE


def validate_grade(obj: Any, rubric: dict) -> dict:
    if not isinstance(obj, dict):
        raise GradeError("grader output is not a JSON object")
    if obj.get("verdict") not in VERDICTS:
        raise GradeError(f"bad verdict {obj.get('verdict')!r}")
    if "misconception" not in obj:
        raise GradeError("missing misconception")
    mid = obj["misconception"]
    if mid is not None and not isinstance(mid, str):
        raise GradeError("misconception must be a string or null")
    scores = obj.get("criteria_scores")
    if not isinstance(scores, dict):
        raise GradeError("criteria_scores must be an object")
    wanted = [c["id"] for c in rubric.get("criteria", [])]
    for cid in wanted:
        if cid not in scores:
            raise GradeError(f"missing criteria_scores[{cid}]")
        pts = next(c["points"] for c in rubric["criteria"] if c["id"] == cid)
        try:
            val = int(scores[cid])
        except (TypeError, ValueError) as e:
            raise GradeError(f"criteria_scores[{cid}] not an int") from e
        if val < 0 or val > pts:
            raise GradeError(f"criteria_scores[{cid}]={val} outside 0..{pts}")
        scores[cid] = val
    rs = obj.get("reasoning_score")
    if rs not in (1, 2, 3):
        raise GradeError("reasoning_score must be 1, 2, or 3")
    return {
        "verdict": obj["verdict"],
        "misconception": mid,
        "criteria_scores": {k: scores[k] for k in wanted},
        "reasoning_score": int(rs),
    }


def _prompt(question: dict, rubric: dict, user_text: str) -> str:
    return (
        "Grade the answer. Return JSON only with keys verdict "
        "(correct|partial|wrong), misconception (id or null), "
        "criteria_scores {id: 0..points}, reasoning_score (1-3).\n"
        f"QUESTION: {question.get('stem', '')}\n"
        f"RUBRIC: {json.dumps(rubric)}\n"
        f"ANSWER: {user_text}\n"
    )


def _http(key: str, prompt: str) -> str:
    url = os.environ.get("TUTOR_LLM_URL",
                         "https://api.openai.com/v1/chat/completions")
    model = os.environ.get("TUTOR_LLM_MODEL", "gpt-4o-mini")
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Return only the grading JSON."},
            {"role": "user", "content": prompt},
        ],
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise GradeError(f"grader HTTP failed: {e}") from e
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise GradeError(f"unexpected grader envelope: {e}") from e


def _parse(raw: str, rubric: dict) -> dict:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise GradeError(f"invalid JSON: {e}") from e
    return validate_grade(obj, rubric)


def grade(question: dict, rubric: dict, user_text: str, *,
          key: Optional[str] = None, transport: Optional[TRANSPORT] = None) -> dict:
    """One retry on invalid JSON, then raise GradeError loudly."""
    key = os.environ.get("TUTOR_LLM_KEY", "") if key is None else key
    if not key:
        raise GradeError("TUTOR_LLM_KEY not set")
    prompt = _prompt(question, rubric, user_text)
    call = transport or (lambda p: _http(key, p))
    try:
        return _parse(call(prompt), rubric)
    except GradeError:
        try:
            return _parse(call(prompt), rubric)
        except GradeError as e:
            raise GradeError(f"invalid grader output after retry: {e}") from e
