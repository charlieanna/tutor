"""Shared synthetic v0.1 content shaped like the Go spine (tiny, namespaced)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from engine.model import load_content  # noqa: E402

CONCEPTS = {
    "go:goroutines": {"prereqs": [], "blurb": "", "canonical": []},
    "go:channels-unbuffered": {"prereqs": ["go:goroutines"], "blurb": "", "canonical": []},
    "go:channels-buffered": {"prereqs": ["go:channels-unbuffered"], "blurb": "", "canonical": []},
    "go:closing": {"prereqs": ["go:channels-unbuffered"], "blurb": "", "canonical": []},
    "go:select": {"prereqs": ["go:channels-unbuffered"], "blurb": "", "canonical": []},
    "go:nil-channels": {"prereqs": ["go:select"], "blurb": "", "canonical": []},
}

MISCONCEPTIONS = {
    "buffered-never-blocks": {"belief": "b", "clarification": "c",
                              "counterexample": "second send on cap-1 blocks"},
    "close-then-send-fine": {"belief": "b", "clarification": "c",
                             "counterexample": "send after close panics"},
    "select-no-default-spins": {"belief": "b", "clarification": "c",
                                "counterexample": "ready case with no nil spins"},
    "m1": {"belief": "b", "clarification": "c",
           "counterexample": "tagged trap option"},
}


def _mcq(qid, concept, level, stem, correct_text, wrong, misconception=None,
         spec_guarantee=False):
    """wrong: list of texts; the first wrong may carry the misconception tag."""
    opts = [{"text": correct_text, "correct": True}]
    for i, w in enumerate(wrong):
        o = {"text": w, "ruled_out_by": "eliminated by a stem fact"}
        if i == 0 and misconception:
            o["misconception"] = misconception
        opts.append(o)
    d = {"id": qid, "concept": concept, "level": level, "kind": "mcq",
         "stem": stem, "options": opts, "explanation": ""}
    if spec_guarantee:
        d["spec_guarantee"] = True
    return d


QUESTIONS = [
    _mcq("q001", "go:goroutines", "recall", "What is a goroutine?", "a", "b"),
    _mcq("q002", "go:goroutines", "application", "Launch one.", "a", "b"),
    _mcq("q003", "go:channels-unbuffered", "recall", "Unbuffered send semantics?", "a", "b"),
    _mcq("q004", "go:channels-unbuffered", "application", "Pass a value.", "a", "b"),
    _mcq("q005", "go:channels-buffered", "recall", "Buffered send blocks when?", "a", "b"),
    _mcq("q006", "go:channels-buffered", "application",
         "ch := make(chan int, 1); send twice - what happens?",
         "second send blocks", "nothing, it never blocks",
         misconception="buffered-never-blocks", spec_guarantee=True),
    _mcq("q007", "go:closing", "recall", "Who may close?", "a", "b"),
    _mcq("q008", "go:closing", "application", "Send after close?",
         "panics", "fine", misconception="close-then-send-fine",
         spec_guarantee=True),
    _mcq("q009", "go:select", "recall", "What does select do?", "a", "b"),
    _mcq("q010", "go:nil-channels", "recall", "Nil channel in select?", "a", "b"),
    # numeric: options document their error; answer_key must self-check
    {"id": "qn01", "concept": "go:channels-buffered", "level": "application",
     "kind": "numeric",
     "stem": "A buffered channel has capacity 3. How many sends complete without blocking (no receiver)?",
     "options": [{"text": "3", "correct": True},
                 {"text": "10", "ruled_out_by": "capacity caps non-blocking sends",
                  "misconception": "buffered-never-blocks"},
                 {"text": "4", "ruled_out_by": "capacity 3 admits exactly 3 sends"}],
     "answer_key": {"expr": "2 + 1", "value": 3, "unit": "sends"},
     "distractor_errors": ["assumed buffered channels never block",
                            "off-by-one on capacity"],
     "explanation": "Capacity 3 admits exactly 3 sends."},
    # free_text: rubric-graded long form
    {"id": "ft01", "concept": "go:select", "level": "reasoning", "kind": "free_text",
     "stem": "Explain when a select without default can spin, and the nil-channel idiom that stops it.",
     "word_limit": 150,
     "rubric": {"criteria": [
         {"id": "spin-cause", "points": 2, "model_answer": "A busy ready case loops hot"},
         {"id": "nil-idiom", "points": 2, "model_answer": "Setting a case channel to nil disables it"}],
         "misconceptions": {"select-no-default-spins": "belief text"}},
     "explanation": ""},
    {"id": "ft02", "concept": "go:nil-channels", "level": "reasoning",
     "kind": "free_text",
     "stem": "When do you nil a channel inside a select loop, and what does it cost?",
     "word_limit": 150,
     "rubric": {"criteria": [
         {"id": "disable-case", "points": 2,
          "model_answer": "A nil channel disables that case; the loop can still block on the rest"}],
         "misconceptions": {}},
     "explanation": ""},
]

# One worked example so next_item can serve fade ≤ L2 steps. Joints follow
# the naive-then-break skeleton; one joint is enough for engine tests.
EXAMPLES = {
    "go:goroutines": {
        "title": "A goroutine is cheap until the run queue is not",
        "joints": {
            "bottleneck": {
                "question": "What saturates first as goroutines pile up?",
                "options": [
                    {"text": "the scheduler run queue", "correct": True},
                    {"text": "the garbage collector",
                     "ruled_out_by": "the setup names runnable-goroutine growth, not heap growth"},
                ],
                "model_answer": "the scheduler run queue",
            }
        },
    }
}


def content():
    return load_content(CONCEPTS, QUESTIONS, MISCONCEPTIONS)


def content_with_examples():
    return load_content(CONCEPTS, QUESTIONS, MISCONCEPTIONS, EXAMPLES)
