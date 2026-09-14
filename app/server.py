"""M7 thin web UI: FastAPI + one page. Auth = unguessable URL token.

State lives in data/users/<token>/ so two tokens cannot see each other.
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets
import sys
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.model import (  # noqa: E402
    ConceptState, state_from_json, state_to_json, sim_history_from_json)
from engine.selection import compose_session, report  # noqa: E402
from engine.update import on_answer, self_grade  # noqa: E402
from app.cli import load_content_packs, log_event  # noqa: E402

STATIC = ROOT / "static"
app = FastAPI(title="Tutor")
if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


class AnswerIn(BaseModel):
    qid: str
    choice: int | None = None
    text: str | None = None
    self_correct: bool | None = None
    session_type: str = "quick_drill"


class SimIn(BaseModel):
    constraints: str = ""
    sketch: str = ""
    failure: str = ""
    capacity_choice: int | None = None
    probe_answers: list[str] = []


def _data_root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("TUTOR_DATA", ROOT / "data" / "users"))


def _paths(token: str) -> tuple[pathlib.Path, pathlib.Path]:
    if not token or "/" in token or ".." in token or len(token) < 12:
        raise HTTPException(400, "bad token")
    d = _data_root() / token
    return d / "state.json", d / "events.jsonl"


def _load(token: str, content):
    sp, _ = _paths(token)
    if sp.exists():
        raw = sp.read_text()
        state = state_from_json(raw)
        hist = sim_history_from_json(raw)
    else:
        state, hist = {}, []
    for cid in content.concepts:
        state.setdefault(cid, ConceptState())
    return state, hist


def _save(token: str, state, hist):
    sp, _ = _paths(token)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(state_to_json(state, sim_history=hist))


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/t/{token}")
def user_page(token: str):
    _paths(token)
    return FileResponse(STATIC / "index.html")


@app.post("/session")
def new_session():
    token = secrets.token_urlsafe(18)
    _data_root().joinpath(token).mkdir(parents=True, exist_ok=True)
    return {"token": token, "url": f"/t/{token}"}


@app.get("/api/{token}/report")
def api_report(token: str):
    content = load_content_packs()
    state, _ = _load(token, content)
    rows = report(content, state, date.today().isoformat())
    return {"rows": rows}


@app.post("/api/{token}/drill")
def api_drill(token: str, n: int = 6, session_type: str = "quick_drill"):
    content = load_content_packs()
    state, hist = _load(token, content)
    today = date.today().isoformat()
    if session_type == "design_session":
        n = min(n, 3)
    plan = compose_session(content, state, today, n=n, session_type=session_type)
    items = []
    for cid, q in plan:
        if getattr(q, "kind", None) == "example":
            items.append({"kind": "example", "concept": cid, "fade": q.fade,
                          "title": q.example.title, "joint": q.joint})
            if q.fade not in state[cid].fade_seen:
                state[cid].fade_seen.append(q.fade)
            continue
        items.append({
            "kind": q.kind, "id": q.id, "concept": cid, "level": q.level,
            "stem": q.stem, "word_limit": q.word_limit,
            "options": [{"text": o.text} for o in q.options],
            "rubric": q.rubric if q.kind == "free_text" else None,
        })
    _save(token, state, hist)
    return {"items": items}


@app.post("/api/{token}/answer")
def api_answer(token: str, body: AnswerIn):
    content = load_content_packs()
    state, hist = _load(token, content)
    q = content.by_id.get(body.qid)
    if q is None:
        raise HTTPException(404, "unknown question")
    s = state[q.concept]
    if q.kind == "free_text":
        correct = bool(body.self_correct)
        self_grade(s, q, correct)
        tag = None
    else:
        if body.choice is None or not (0 <= body.choice < len(q.options)):
            raise HTTPException(400, "choice required")
        opt = q.options[body.choice]
        correct = opt.correct
        tag = opt.misconception
        on_answer(s, q, body.choice, correct, content=content, all_state=state)
    log_event(q.id, q.concept, q.level, correct, tag, body.session_type)
    # log_event uses process-wide EVENTS_PATH; also append per-user
    _, ev = _paths(token)
    ev.parent.mkdir(parents=True, exist_ok=True)
    with ev.open("a") as f:
        f.write(json.dumps({
            "qid": q.id, "concept": q.concept, "correct": correct,
            "session_type": body.session_type}) + "\n")
    _save(token, state, hist)
    out = {"correct": correct, "explanation": q.explanation}
    if not correct and body.choice is not None and q.options:
        opt = q.options[body.choice]
        if opt.ruled_out_by:
            out["ruled_out_by"] = opt.ruled_out_by
        if opt.misconception and opt.misconception in content.misconceptions:
            m = content.misconceptions[opt.misconception]
            out["misconception"] = m.belief
            out["clarification"] = m.clarification
    return out


@app.get("/api/{token}/sim")
def api_sim_brief(token: str):
    _paths(token)
    from app.sim import load_scenarios, pick_probes
    scs = load_scenarios()
    if not scs:
        return JSONResponse({"error": "no scenarios"}, 404)
    sc = scs[0]
    content = load_content_packs()
    state, _ = _load(token, content)
    today = date.today().isoformat()
    numeric_qs = [q for q in content.questions
                  if q.kind == "numeric" and q.concept in sc.get("concepts", [])]
    numeric = None
    if numeric_qs:
        q = numeric_qs[0]
        numeric = {"id": q.id, "stem": q.stem,
                   "options": [{"text": o.text} for o in q.options]}
    probes = [{"concept": p["concept"], "prompt": p["prompt"],
               "dimension": p.get("dimension")}
              for p in pick_probes(sc, content, state, today, n=2)]
    return {"id": sc["id"], "title": sc["title"], "brief": sc["brief"],
            "concepts": sc.get("concepts", []), "numeric": numeric,
            "probes": probes}


@app.post("/api/{token}/sim")
def api_sim_submit(token: str, body: SimIn):
    from app.sim import apply_sim_answers, load_scenarios
    scs = load_scenarios()
    if not scs:
        raise HTTPException(404, "no scenarios")
    sc = scs[0]
    content = load_content_packs()
    state, hist = _load(token, content)
    today = date.today().isoformat()
    scores = apply_sim_answers(
        content, state, sc, today,
        constraints=body.constraints, sketch=body.sketch,
        failure=body.failure, capacity_choice=body.capacity_choice,
        probe_answers=body.probe_answers)
    hist.append({"scenario": sc["id"], "date": today, "dimension_scores": scores})
    _save(token, state, hist)
    return {"dimension_scores": scores, "scenario": sc["id"]}


def main():
    import uvicorn
    uvicorn.run("app.server:app", host="127.0.0.1", port=int(
        os.environ.get("TUTOR_PORT", "8000")), reload=False)


if __name__ == "__main__":
    main()
