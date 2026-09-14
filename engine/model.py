"""Dataclasses + JSON (de)serialization + validation. Pure logic, no I/O.

v0.1 schemas:
- concepts are pack-namespaced: "<pack>:<concept>"; prereqs stay within a pack
- question kinds: mcq | numeric | derivation | free_text
- every incorrect MCQ option carries ruled_out_by (which stem fact kills it)
- numeric questions carry answer_key {expr, value, unit}; the validator
  safe-evaluates expr and checks it against value
- free_text questions carry a rubric + word_limit
"""
from __future__ import annotations

import ast
import json
import operator
from dataclasses import dataclass, field
from typing import Any, Optional

from . import config


class ValidationError(Exception):
    pass


# ------------------------------------------------------------------ safe eval

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
           ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_eval(expr: str) -> float:
    """Arithmetic-only evaluation for numeric answer keys. Rejects anything
    that is not a pure arithmetic expression over numeric literals."""
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ValidationError(f"bad arithmetic expr {expr!r}: {e}")

    def walk(node) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            if isinstance(node.op, ast.Pow):
                # reject huge exponents syntactically — evaluating 2**2**1e9
                # would hang; answer keys never need them
                for sub in ast.walk(node.right):
                    if isinstance(sub, ast.Constant) \
                            and isinstance(sub.value, (int, float)) \
                            and abs(sub.value) > 10_000:
                        raise ValidationError(f"exponent too large: {expr!r}")
            return _BINOPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](walk(node.operand))
        raise ValidationError(f"unsafe or non-arithmetic expr: {expr!r}")

    return walk(tree)


# ----------------------------------------------------------------- dataclasses

@dataclass
class Option:
    text: str
    correct: bool = False
    misconception: Optional[str] = None
    ruled_out_by: Optional[str] = None  # required on incorrect options


@dataclass
class Question:
    id: str
    concept: str
    level: str
    kind: str  # mcq | numeric | derivation | free_text
    stem: str
    options: list[Option]
    explanation: str
    derivation_step: Optional[str] = None  # bottleneck | root_cause | fix | pattern
    verify: Optional[dict[str, str]] = None  # {"dir": ..., "backend": ...}
    spec_guarantee: bool = False
    rubric: Optional[dict] = None       # free_text only: {criteria: [...]}
    word_limit: Optional[int] = None    # free_text only
    answer_key: Optional[dict] = None   # numeric only: {expr, value, unit}
    distractor_errors: Optional[list] = None  # numeric only
    last_asked: Optional[str] = None    # ISO date, engine-maintained

    def trap_options(self, misconception_id: str) -> list[Option]:
        return [o for o in self.options if o.misconception == misconception_id]


@dataclass
class Joint:
    question: str
    options: list[Option]
    model_answer: str


@dataclass
class WorkedExample:
    concept: str
    title: str
    joints: dict[str, Joint]


@dataclass
class ExampleStep:
    """Served by next_item when fade ≤ L2 and the example is not in fade_seen."""
    concept: str
    fade: str
    example: WorkedExample
    joint: Optional[str] = None
    kind: str = "example"


@dataclass
class Misconception:
    id: str
    belief: str
    clarification: str
    counterexample: Optional[str] = None


@dataclass
class ConceptState:
    mastery: dict[str, float] = field(
        default_factory=lambda: {lv: 0.0 for lv in config.LEVELS})
    fails: int = 0
    due: Optional[str] = None
    misconceptions: dict[str, float] = field(default_factory=dict)
    asked: int = 0
    fade_seen: list = field(default_factory=list)
    resolves: dict = field(default_factory=dict)

    def frontier(self) -> str:
        for lv in config.LEVELS:
            if self.mastery[lv] < config.MASTERED:
                return lv
        return "reasoning"


class Content:
    """Validated bundle of pack-namespaced concepts, questions, misconceptions."""

    def __init__(self, concepts: dict[str, dict], questions: list[Question],
                 misconceptions: dict[str, Misconception],
                 examples: Optional[dict[str, WorkedExample]] = None):
        self.concepts = concepts
        self.questions = questions
        self.misconceptions = misconceptions
        self.examples = examples or {}
        self.by_id = {q.id: q for q in questions}
        self._centrality: Optional[dict[str, int]] = None

    def packs(self) -> set[str]:
        return {c.split(":", 1)[0] for c in self.concepts}

    def centrality(self, concept_id: str) -> int:
        if self._centrality is None:
            dependents: dict[str, set[str]] = {c: set() for c in self.concepts}
            for cid, data in self.concepts.items():
                for p in data["prereqs"]:
                    dependents[p].add(cid)

            def reach(start: str) -> set[str]:
                seen: set[str] = set()
                stack = list(dependents[start])
                while stack:
                    n = stack.pop()
                    if n in seen:
                        continue
                    seen.add(n)
                    stack.extend(dependents[n])
                return seen

            self._centrality = {c: len(reach(c)) + 1 for c in self.concepts}
        return self._centrality[concept_id]

    def questions_for(self, concept_id: str) -> list[Question]:
        return [q for q in self.questions if q.concept == concept_id]


# ---------------------------------------------------------------- serialization

def state_to_json(state: dict[str, ConceptState],
                  sim_history: Optional[list] = None) -> str:
    return json.dumps(
        {"concepts": {cid: vars(s) for cid, s in state.items()},
         "sim_history": sim_history or []},
        indent=2, sort_keys=True)


def _parse_state_payload(data: dict) -> tuple[dict, list]:
    """Envelope {concepts, sim_history} or legacy flat {concept_id: state}."""
    if (set(data.keys()) <= {"concepts", "sim_history"}
            and isinstance(data.get("concepts"), dict)):
        return data["concepts"], list(data.get("sim_history") or [])
    concepts = {k: v for k, v in data.items() if k != "sim_history"}
    hist = data["sim_history"] if isinstance(data.get("sim_history"), list) else []
    return concepts, hist


def state_from_json(raw: str) -> dict[str, ConceptState]:
    concepts_raw, _ = _parse_state_payload(json.loads(raw))
    out: dict[str, ConceptState] = {}
    for cid, d in concepts_raw.items():
        s = ConceptState()
        s.mastery.update(d.get("mastery", {}))
        s.fails = d.get("fails", 0)
        s.due = d.get("due")
        s.misconceptions = d.get("misconceptions", {})
        s.asked = d.get("asked", 0)
        s.fade_seen = d.get("fade_seen", [])
        s.resolves = d.get("resolves", {})
        out[cid] = s
    return out


def sim_history_from_json(raw: str) -> list:
    _, hist = _parse_state_payload(json.loads(raw))
    return hist


# ------------------------------------------------------------------- loading

def _question_from_dict(d: dict[str, Any]) -> Question:
    opts = [Option(**o) for o in d.get("options", [])]
    return Question(
        id=d["id"], concept=d["concept"], level=d["level"],
        kind=d.get("kind", "mcq"), stem=d["stem"], options=opts,
        explanation=d.get("explanation", ""),
        derivation_step=d.get("derivation_step"), verify=d.get("verify"),
        spec_guarantee=d.get("spec_guarantee", False), rubric=d.get("rubric"),
        word_limit=d.get("word_limit"), answer_key=d.get("answer_key"),
        distractor_errors=d.get("distractor_errors"))


def _example_from_dict(cid: str, d: dict[str, Any]) -> WorkedExample:
    joints: dict[str, Joint] = {}
    for name, j in (d.get("joints") or {}).items():
        opts = [Option(**o) for o in j.get("options", [])]
        joints[name] = Joint(question=j.get("question", ""), options=opts,
                             model_answer=j.get("model_answer", ""))
    return WorkedExample(concept=cid, title=d.get("title", ""), joints=joints)


def load_content(concepts_raw: dict, questions_raw: list[dict],
                 misconceptions_raw: dict,
                 examples_raw: Optional[dict] = None) -> Content:
    concepts = {
        cid: {"prereqs": d.get("prereqs", []), "blurb": d.get("blurb", ""),
              "canonical": d.get("canonical", [])}
        for cid, d in concepts_raw.items()
    }
    questions = [_question_from_dict(d) for d in questions_raw]
    miscons = {
        mid: Misconception(id=mid, belief=d["belief"],
                           clarification=d["clarification"],
                           counterexample=d.get("counterexample"))
        for mid, d in misconceptions_raw.items()
    }
    content = Content(concepts, questions, miscons,
                      {_cid: _example_from_dict(_cid, d)
                       for _cid, d in (examples_raw or {}).items()})
    validate_content(content)
    return content


_CODE_MARKERS = ("```", "func ", "def ", ":=", "go func", "chan ", "make(")


def _stem_has_code(stem: str) -> bool:
    return any(marker in stem for marker in _CODE_MARKERS)


VALID_BACKENDS = ("go_exec", "python_exec", "rubric", "numeric")
KINDS = ("mcq", "numeric", "derivation", "free_text")


def _validate_rubric(q: Question, misconceptions: dict) -> list[str]:
    errors: list[str] = []
    rub = q.rubric
    if not isinstance(rub, dict) or not isinstance(rub.get("criteria"), list) \
            or not rub["criteria"]:
        return [f"{q.id}: free_text needs a rubric with a non-empty criteria list"]
    seen: set[str] = set()
    for crit in rub["criteria"]:
        cid = crit.get("id")
        if not cid:
            errors.append(f"{q.id}: rubric criterion missing id")
        elif cid in seen:
            errors.append(f"{q.id}: duplicate rubric criterion id {cid}")
        else:
            seen.add(cid)
        if not isinstance(crit.get("points"), int) or crit["points"] <= 0:
            errors.append(f"{q.id}: rubric criterion {cid} needs positive integer points")
        if not crit.get("model_answer"):
            errors.append(f"{q.id}: rubric criterion {cid} needs a model_answer")
    for mid in rub.get("misconceptions", {}):
        if mid not in misconceptions:
            errors.append(f"{q.id}: unknown rubric misconception {mid}")
    if not isinstance(q.word_limit, int) or q.word_limit <= 0:
        errors.append(f"{q.id}: free_text needs a positive word_limit")
    return errors


def validate_content(content: Content) -> None:
    errors: list[str] = []

    # concepts: namespaced ids, prereqs resolve within the same pack, DAG
    for cid, data in content.concepts.items():
        if ":" not in cid:
            errors.append(f"concept {cid}: id must be pack-namespaced '<pack>:<concept>'")
        for p in data["prereqs"]:
            if p not in content.concepts:
                errors.append(f"concept {cid}: unknown prereq {p}")
            elif p.split(":", 1)[0] != cid.split(":", 1)[0]:
                errors.append(f"concept {cid}: cross-pack prereq {p}")

    WHITE, GREY, BLACK = 0, 1, 2
    color = {c: WHITE for c in content.concepts}

    def visit(c: str, path: list[str]) -> None:
        if color[c] == GREY:
            errors.append("prereq graph has a cycle: " + " -> ".join(path + [c]))
            return
        if color[c] == BLACK:
            return
        color[c] = GREY
        for p in content.concepts[c]["prereqs"]:
            if p in content.concepts:  # unknown prereqs reported separately
                visit(p, path + [c])
        color[c] = BLACK

    for c in content.concepts:
        visit(c, [])

    seen_qids: set[str] = set()
    for q in content.questions:
        if q.id in seen_qids:
            errors.append(f"duplicate question id {q.id}")
        seen_qids.add(q.id)

        if q.concept not in content.concepts:
            errors.append(f"{q.id}: unknown concept {q.concept}")
        if q.level not in config.LEVELS:
            errors.append(f"{q.id}: bad level {q.level}")
        if q.kind not in KINDS:
            errors.append(f"{q.id}: bad kind {q.kind}")
            continue

        if q.kind in ("mcq", "numeric"):
            if len(q.options) < 2:
                errors.append(f"{q.id}: needs at least 2 options")
                continue
            n_correct = sum(1 for o in q.options if o.correct)
            if n_correct != 1:
                errors.append(f"{q.id}: expected exactly 1 correct option, found {n_correct}")
            for o in q.options:
                if not o.correct and not o.ruled_out_by:
                    errors.append(f"{q.id}: incorrect option needs ruled_out_by: {o.text!r}")
                if o.misconception is not None and o.misconception not in content.misconceptions:
                    errors.append(f"{q.id}: unknown misconception tag {o.misconception}")

        if q.kind == "numeric":
            ak = q.answer_key
            if not ak or "expr" not in ak or "value" not in ak:
                errors.append(f"{q.id}: numeric needs answer_key with expr and value")
            else:
                try:
                    computed = safe_eval(ak["expr"])
                    if abs(computed - float(ak["value"])) > 1e-6 * max(1.0, abs(computed)):
                        errors.append(f"{q.id}: answer_key expr evaluates to {computed}, "
                                      f"declared value is {ak['value']}")
                except ValidationError as e:
                    errors.append(str(e))
            if not isinstance(q.distractor_errors, list) or not q.distractor_errors:
                errors.append(f"{q.id}: numeric needs distractor_errors documenting "
                              f"each wrong option's error")

        if q.kind == "derivation" and q.derivation_step not in (
                "bottleneck", "root_cause", "fix", "pattern"):
            errors.append(f"{q.id}: derivation kind needs a valid derivation_step")

        if q.kind == "free_text":
            errors.extend(_validate_rubric(q, content.misconceptions))

        if q.verify is not None:
            backend = q.verify.get("backend", "go_exec")
            if backend not in VALID_BACKENDS:
                errors.append(f"{q.id}: bad verify backend {backend!r}")

        if (q.kind in ("mcq", "numeric", "derivation") and _stem_has_code(q.stem)
                and q.verify is None and not q.spec_guarantee):
            errors.append(
                f"{q.id}: code in stem but no verify entry and no spec_guarantee flag")

    for mid, m in content.misconceptions.items():
        if not m.counterexample:
            errors.append(f"misconception {mid}: needs a counterexample")

    for cid, ex in content.examples.items():
        if cid not in content.concepts:
            errors.append(f"example {cid}: unknown concept {cid}")
            continue
        for jname, joint in ex.joints.items():
            if not joint.options:
                continue
            n_correct = sum(1 for o in joint.options if o.correct)
            if n_correct != 1:
                errors.append(f"{cid} joint {jname}: expected exactly 1 correct option, "
                              f"found {n_correct}")
            for o in joint.options:
                if not o.correct and not o.ruled_out_by:
                    errors.append(f"{cid} joint {jname}: incorrect option needs "
                                  f"ruled_out_by: {o.text!r}")
                if o.misconception is not None \
                        and o.misconception not in content.misconceptions:
                    errors.append(f"{cid} joint {jname}: unknown misconception "
                                  f"tag {o.misconception}")

    if errors:
        raise ValidationError("; ".join(errors))
