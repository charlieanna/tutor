import sys, pathlib, unittest
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from fixture import (CONCEPTS, MISCONCEPTIONS, QUESTIONS,  # noqa: E402
                     content, content_with_examples)
from engine import config  # noqa: E402
from engine.model import (ConceptState, Option,  # noqa: E402
                          Question, ValidationError, load_content,
                          safe_eval, sim_history_from_json, state_from_json,
                          state_to_json)
from engine.selection import (compose_session, fade_level,  # noqa: E402
                              next_item, pick_question)
from engine.update import on_answer, self_grade  # noqa: E402
from engine.diagnose import diagnose  # noqa: E402

TODAY = date(2026, 9, 14)


def fresh_state(cids):
    return {c: ConceptState() for c in cids}


class TestFadeBoundaries(unittest.TestCase):
    def s(self, recall=0.0, application=0.0):
        s = ConceptState()
        s.mastery["recall"] = recall
        s.mastery["application"] = application
        return s

    def test_boundaries(self):
        self.assertEqual(fade_level(self.s(recall=0.0)), "L0")
        self.assertEqual(fade_level(self.s(recall=0.399)), "L0")
        self.assertEqual(fade_level(self.s(recall=0.4)), "L1")     # boundary
        self.assertEqual(fade_level(self.s(recall=0.699)), "L1")
        self.assertEqual(fade_level(self.s(recall=0.7, application=0.0)), "L2")
        self.assertEqual(fade_level(self.s(recall=0.9, application=0.699)), "L2")
        self.assertEqual(fade_level(self.s(recall=0.9, application=0.7)), "L3")
        self.assertEqual(fade_level(self.s(recall=1.0, application=1.0)), "L3")


class TestCentrality(unittest.TestCase):
    def test_centrality(self):
        c = content()
        self.assertEqual(c.centrality("go:goroutines"), 6)
        self.assertEqual(c.centrality("go:channels-unbuffered"), 5)
        self.assertEqual(c.centrality("go:nil-channels"), 1)

    def test_packs_namespace(self):
        c = content()
        self.assertEqual(c.packs(), {"go"})


class TestSafeEval(unittest.TestCase):
    def test_arithmetic_ok(self):
        self.assertAlmostEqual(safe_eval("100000000 * 0.95 / 86400"),
                               1099.537037037037)
        self.assertEqual(safe_eval("2 + 1"), 3)
        self.assertEqual(safe_eval("(2 + 3) * -1"), -5)

    def test_unsafe_rejected(self):
        for expr in ("__import__('os')", "open('/etc/passwd')",
                     "'a' + 'b'", "len('x')", "2 ** 2 ** 999999999"):
            with self.assertRaises(ValidationError):
                safe_eval(expr)


class TestSelection(unittest.TestCase):
    def test_deficit_times_centrality_ordering(self):
        c = content()
        st = fresh_state(c.concepts)
        cid, q = next_item(c, st, TODAY.isoformat())
        self.assertEqual(cid, "go:goroutines")
        st["go:goroutines"].mastery = {lv: 0.9 for lv in config.LEVELS}
        cid, _ = next_item(c, st, TODAY.isoformat())
        self.assertEqual(cid, "go:channels-unbuffered")

    def test_interleaving_exclusion(self):
        c = content()
        st = fresh_state(c.concepts)
        st["go:goroutines"].mastery = {lv: 0.9 for lv in config.LEVELS}
        cid1, _ = next_item(c, st, TODAY.isoformat())
        cid2, _ = next_item(c, st, TODAY.isoformat(), last_concept=cid1)
        self.assertNotEqual(cid1, cid2)

    def test_fails_multiplier_wins(self):
        c = content()
        st = fresh_state(c.concepts)
        st["go:goroutines"].mastery = {lv: 0.9 for lv in config.LEVELS}
        st["go:channels-unbuffered"].mastery = {lv: 0.9 for lv in config.LEVELS}
        st["go:nil-channels"].fails = 2  # .8*1*3=2.4 beats select's .8*2
        cid, _ = next_item(c, st, TODAY.isoformat())
        self.assertEqual(cid, "go:nil-channels")

    def test_due_multiplier(self):
        c = content()
        st = fresh_state(c.concepts)
        st["go:goroutines"].mastery = {lv: 0.9 for lv in config.LEVELS}
        st["go:channels-unbuffered"].mastery = {lv: 0.9 for lv in config.LEVELS}
        st["go:closing"].due = "2026-09-10"  # overdue: .8*2*2 beats select .8*2
        cid, _ = next_item(c, st, TODAY.isoformat())
        self.assertEqual(cid, "go:closing")

    def test_goal_weighting(self):
        c = content()
        st = fresh_state(c.concepts)
        st["go:goroutines"].mastery = {lv: 0.9 for lv in config.LEVELS}
        goal = {"required": ["go:nil-channels"]}
        cid, _ = next_item(c, st, TODAY.isoformat(), goal=goal)
        self.assertEqual(cid, "go:channels-unbuffered")  # .8*5 beats .8*1*1.5
        for cid_ in ("go:channels-unbuffered", "go:closing", "go:select"):
            st[cid_].mastery = {lv: 0.9 for lv in config.LEVELS}
        cid, _ = next_item(c, st, TODAY.isoformat(), goal=goal)
        self.assertEqual(cid, "go:nil-channels")

    def test_trap_preference(self):
        c = content()
        st = fresh_state(c.concepts)
        s = st["go:channels-buffered"]
        s.mastery["recall"] = 0.9          # frontier = application
        s.misconceptions["buffered-never-blocks"] = 0.9  # >= 0.7
        q = pick_question(c, "go:channels-buffered", s)
        self.assertEqual(q.id, "q006")     # tagged distractor wins at level
        # numeric question also exists at application; trap filter picks q006
        s2 = ConceptState()
        s2.mastery["recall"] = 0.9
        q2 = pick_question(c, "go:channels-buffered", s2)
        self.assertIn(q2.id, {"q006", "qn01"})  # both application-level, LRA order
        # fall back across levels when frontier pool empty
        q3 = pick_question(c, "go:goroutines", ConceptState())
        self.assertIn(q3.id, {"q001", "q002"})

    def test_session_composition(self):
        c = content()
        st = fresh_state(c.concepts)
        st["go:closing"].due = "2026-09-01"
        st["go:select"].due = "2026-09-01"
        plan = compose_session(c, st, TODAY.isoformat(), n=6)
        self.assertLessEqual(len(plan), 6)
        ids = [cid for cid, _ in plan]
        for a, b in zip(ids, ids[1:]):
            self.assertNotEqual(a, b)  # never twice consecutively


class TestUpdate(unittest.TestCase):
    def q(self, level="recall"):
        return Question("qx", "go:goroutines", level, "mcq", "?",
                        [Option("a", correct=True), Option("b", misconception="m1")], "")

    def test_correct_update_math(self):
        s = ConceptState()
        q = self.q()
        res = on_answer(s, q, 0, True, today=TODAY)
        self.assertAlmostEqual(s.mastery["recall"], 0.8 * 0.3)
        self.assertEqual(s.fails, 0)
        self.assertEqual(s.due, "2026-09-17")  # +3 days
        self.assertIsNone(res)

    def test_evidence_weight_discounts_gain(self):
        s = ConceptState()
        on_answer(s, self.q(), 0, True, today=TODAY, evidence_weight=0.7)
        self.assertAlmostEqual(s.mastery["recall"], 0.8 * 0.3 * 0.7)

    def test_wrong_update_math_and_misconception_bump(self):
        s = ConceptState()
        s.mastery["recall"] = 0.6
        on_answer(s, self.q(), 1, False, today=TODAY)  # chose the m1-tagged option
        self.assertAlmostEqual(s.mastery["recall"], 0.6 * 0.6)
        self.assertEqual(s.fails, 1)
        self.assertEqual(s.due, "2026-09-15")  # +1 day
        self.assertAlmostEqual(s.misconceptions["m1"], 0.5)
        res = on_answer(s, self.q(), 1, False, today=TODAY)
        self.assertEqual(s.fails, 2)
        self.assertAlmostEqual(s.misconceptions["m1"], 1.0)
        self.assertIsNotNone(res)

    def test_self_grade_weights(self):
        s = ConceptState()
        q = self.q("application")
        self_grade(s, q, True, today=TODAY)
        self.assertAlmostEqual(s.mastery["application"], 0.8 * 0.3 * 0.7)
        s2 = ConceptState()
        self_grade(s2, q, False, today=TODAY)
        self.assertEqual(s2.mastery["application"], 0.0)  # *0.6 of 0

    def test_python_exec_resolve_chain_and_reset(self):
        s = ConceptState()
        q = Question("px", "go:goroutines", "application", "mcq", "?",
                     [Option("a", correct=True), Option("b")], "",
                     verify={"dir": "x", "backend": "python_exec"})
        on_answer(s, q, 0, True, today=TODAY)
        self.assertEqual(s.resolves["px"]["due"], "2026-09-17")
        self.assertEqual(s.resolves["px"]["step"], 1)
        on_answer(s, q, 0, True, today=date(2026, 9, 17))
        self.assertEqual(s.resolves["px"]["due"], "2026-09-24")  # +7d
        self.assertEqual(s.resolves["px"]["step"], 2)
        on_answer(s, q, 1, False, today=date(2026, 9, 24))
        self.assertEqual(s.resolves["px"]["step"], 0)
        self.assertEqual(s.resolves["px"]["due"], "2026-09-25")

    def test_non_python_exec_has_no_resolve(self):
        s = ConceptState()
        on_answer(s, self.q(), 0, True, today=TODAY)
        self.assertEqual(s.resolves, {})


class TestDiagnose(unittest.TestCase):
    def setUp(self):
        self.c = content()

    def test_prereq_branch(self):
        st = fresh_state(self.c.concepts)
        st["go:channels-buffered"].fails = 3
        d = diagnose(self.c, st, "go:channels-buffered")
        self.assertEqual(d["type"], "prereq")
        self.assertEqual(d["drill"], "go:channels-unbuffered")

    def test_misconception_branch(self):
        st = fresh_state(self.c.concepts)
        for p in self.c.concepts["go:channels-buffered"]["prereqs"]:
            st[p].mastery["recall"] = 0.8
        s = st["go:channels-buffered"]
        s.mastery["recall"] = 0.8
        s.misconceptions["buffered-never-blocks"] = 0.7
        d = diagnose(self.c, st, "go:channels-buffered")
        self.assertEqual(d["type"], "misconception")
        self.assertEqual(d["trap"], "buffered-never-blocks")

    def test_worked_example_branch(self):
        st = fresh_state(self.c.concepts)
        for p in self.c.concepts["go:channels-buffered"]["prereqs"]:
            st[p].mastery["recall"] = 0.8
        s = st["go:channels-buffered"]
        s.mastery["recall"] = 0.75
        s.mastery["application"] = 0.3
        d = diagnose(self.c, st, "go:channels-buffered")
        self.assertEqual(d["type"], "worked_example")
        self.assertEqual(d["fade"], "L2")

    def test_re_teach_branch(self):
        st = fresh_state(self.c.concepts)
        for p in self.c.concepts["go:channels-buffered"]["prereqs"]:
            st[p].mastery["recall"] = 0.8
        d = diagnose(self.c, st, "go:channels-buffered")
        self.assertEqual(d["type"], "re_teach")
        self.assertEqual(d["fade"], "L0")


class TestValidator(unittest.TestCase):
    def load(self, concepts=None, questions=None, miscons=None):
        return load_content(concepts or CONCEPTS, questions or QUESTIONS,
                            miscons or MISCONCEPTIONS)

    def test_valid_fixture_loads(self):
        self.load()

    def test_missing_ruled_out_by_rejected(self):
        qs = [dict(QUESTIONS[0], options=[{"text": "a", "correct": True},
                                          {"text": "b"}])]
        with self.assertRaises(ValidationError) as e:
            self.load(questions=qs)
        self.assertIn("ruled_out_by", str(e.exception))

    def test_two_correct_options_rejected(self):
        qs = [dict(QUESTIONS[0], options=[{"text": "a", "correct": True},
                                          {"text": "b", "ruled_out_by": "x"},
                                          {"text": "c", "correct": True,
                                           "ruled_out_by": "y"}])]
        with self.assertRaises(ValidationError) as e:
            self.load(questions=qs)
        self.assertIn("exactly 1 correct", str(e.exception))

    def test_unknown_misconception_tag(self):
        qs = [dict(QUESTIONS[0], options=[{"text": "a", "correct": True},
                                          {"text": "b", "ruled_out_by": "x",
                                           "misconception": "nope"}])]
        with self.assertRaises(ValidationError) as e:
            self.load(questions=qs)
        self.assertIn("unknown misconception tag nope", str(e.exception))

    def test_unknown_concept(self):
        qs = [dict(QUESTIONS[0], concept="ghost:x")]
        with self.assertRaises(ValidationError) as e:
            self.load(questions=qs)
        self.assertIn("unknown concept ghost:x", str(e.exception))

    def test_unknown_prereq(self):
        cs = {**CONCEPTS, "go:ghost": {"prereqs": ["go:phantom"],
                                        "blurb": "", "canonical": []}}
        with self.assertRaises(ValidationError) as e:
            self.load(concepts=cs)
        self.assertIn("unknown prereq go:phantom", str(e.exception))

    def test_cross_pack_prereq_rejected(self):
        cs = dict(CONCEPTS)
        cs["dsa:two-pointers"] = {"prereqs": ["go:goroutines"], "blurb": "", "canonical": []}
        with self.assertRaises(ValidationError) as e:
            self.load(concepts=cs)
        self.assertIn("cross-pack prereq", str(e.exception))

    def test_non_namespaced_concept_rejected(self):
        cs = {"bare": {"prereqs": [], "blurb": "", "canonical": []}}
        with self.assertRaises(ValidationError) as e:
            self.load(concepts=cs)
        self.assertIn("pack-namespaced", str(e.exception))

    def test_cycle_rejected(self):
        cs = {k: dict(v) for k, v in CONCEPTS.items()}
        cs["go:goroutines"]["prereqs"] = ["go:nil-channels"]
        with self.assertRaises(ValidationError) as e:
            self.load(concepts=cs)
        self.assertIn("cycle", str(e.exception))

    def test_code_without_verify_rejected(self):
        qs = [dict(QUESTIONS[0], stem="x := make(chan int)")]
        with self.assertRaises(ValidationError) as e:
            self.load(questions=qs)
        self.assertIn("no verify entry", str(e.exception))

    def test_code_with_spec_guarantee_ok(self):
        qs = [dict(QUESTIONS[0], stem="x := make(chan int)", spec_guarantee=True)]
        self.load(questions=qs)

    def test_numeric_value_mismatch_rejected(self):
        qn = dict(QUESTIONS[10])
        qn = {**qn, "answer_key": {**qn["answer_key"], "expr": "2 + 2", "value": 3}}
        with self.assertRaises(ValidationError) as e:
            self.load(questions=[qn])
        self.assertIn("declared value", str(e.exception))

    def test_numeric_unsafe_expr_rejected(self):
        qn = {**QUESTIONS[10],
              "answer_key": {**QUESTIONS[10]["answer_key"], "expr": "__import__('os')"}}
        with self.assertRaises(ValidationError) as e:
            self.load(questions=[qn])
        self.assertIn("non-arithmetic", str(e.exception))

    def test_numeric_needs_distractor_errors(self):
        qn = {**QUESTIONS[10], "distractor_errors": []}
        with self.assertRaises(ValidationError) as e:
            self.load(questions=[qn])
        self.assertIn("distractor_errors", str(e.exception))

    def test_free_text_without_word_limit_rejected(self):
        ft = {**QUESTIONS[11], "word_limit": None}
        with self.assertRaises(ValidationError) as e:
            self.load(questions=[ft])
        self.assertIn("word_limit", str(e.exception))

    def test_free_text_without_rubric_rejected(self):
        ft = {**QUESTIONS[11], "rubric": None}
        with self.assertRaises(ValidationError) as e:
            self.load(questions=[ft])
        self.assertIn("needs a rubric", str(e.exception))

    def test_rubric_criterion_missing_model_answer(self):
        ft = {**QUESTIONS[11],
              "rubric": {"criteria": [{"id": "c1", "points": 2}]}}
        with self.assertRaises(ValidationError) as e:
            self.load(questions=[ft])
        self.assertIn("model_answer", str(e.exception))

    def test_rubric_unknown_misconception(self):
        ft = {**QUESTIONS[11],
              "rubric": {"criteria": [{"id": "c1", "points": 2,
                                       "model_answer": "a"}],
                          "misconceptions": {"ghost": "b"}}}
        with self.assertRaises(ValidationError) as e:
            self.load(questions=[ft])
        self.assertIn("unknown rubric misconception ghost", str(e.exception))

    def test_bad_derivation_step(self):
        qs = [dict(QUESTIONS[0], kind="derivation", derivation_step="nope")]
        with self.assertRaises(ValidationError) as e:
            self.load(questions=qs)
        self.assertIn("derivation_step", str(e.exception))

    def test_bad_verify_backend(self):
        qs = [dict(QUESTIONS[0], verify={"dir": "x", "backend": "psychic"})]
        with self.assertRaises(ValidationError) as e:
            self.load(questions=qs)
        self.assertIn("bad verify backend", str(e.exception))


class TestStateRoundtrip(unittest.TestCase):
    def test_json_roundtrip(self):
        st = fresh_state(CONCEPTS)
        st["go:goroutines"].mastery["recall"] = 0.55
        st["go:goroutines"].fails = 2
        st["go:goroutines"].due = "2026-09-20"
        st["go:goroutines"].misconceptions = {"m1": 0.5}
        st["go:goroutines"].fade_seen = ["L0", "L1"]
        out = state_from_json(state_to_json(st))
        self.assertEqual(out["go:goroutines"].mastery["recall"], 0.55)
        self.assertEqual(out["go:goroutines"].fails, 2)
        self.assertEqual(out["go:goroutines"].due, "2026-09-20")
        self.assertEqual(out["go:goroutines"].misconceptions, {"m1": 0.5})
        self.assertEqual(out["go:goroutines"].fade_seen, ["L0", "L1"])

    def test_sim_history_roundtrip(self):
        st = fresh_state(CONCEPTS)
        hist = [{"scenario": "ambulance-dispatch", "date": "2026-09-14",
                 "dimension_scores": {"estimation": 2, "tradeoff_defense": 1}}]
        raw = state_to_json(st, sim_history=hist)
        out = state_from_json(raw)
        self.assertEqual(out["go:goroutines"].fails, 0)
        self.assertEqual(sim_history_from_json(raw), hist)

    def test_legacy_flat_state_json_still_loads(self):
        raw = ('{"go:goroutines": {"mastery": {"recall": 0.4, "application": 0.0,'
               ' "reasoning": 0.0}, "fails": 1, "due": null, "misconceptions": {},'
               ' "asked": 2, "fade_seen": []}}')
        out = state_from_json(raw)
        self.assertAlmostEqual(out["go:goroutines"].mastery["recall"], 0.4)
        self.assertEqual(out["go:goroutines"].asked, 2)
        self.assertEqual(sim_history_from_json(raw), [])


class TestExampleServing(unittest.TestCase):
    def test_l0_serves_example_when_not_recently_shown(self):
        c = content_with_examples()
        st = fresh_state(c.concepts)
        cid, item = next_item(c, st, TODAY.isoformat())
        self.assertEqual(cid, "go:goroutines")
        self.assertEqual(item.kind, "example")
        self.assertEqual(item.fade, "L0")
        self.assertEqual(item.example.concept, "go:goroutines")

    def test_skips_example_once_fade_seen(self):
        c = content_with_examples()
        st = fresh_state(c.concepts)
        st["go:goroutines"].fade_seen = ["L0"]
        cid, item = next_item(c, st, TODAY.isoformat())
        self.assertEqual(cid, "go:goroutines")
        self.assertNotEqual(item.kind, "example")

    def test_l3_never_serves_example(self):
        c = content_with_examples()
        st = fresh_state(c.concepts)
        st["go:goroutines"].mastery["recall"] = 0.9
        st["go:goroutines"].mastery["application"] = 0.9
        cid, item = next_item(c, st, TODAY.isoformat())
        self.assertEqual(cid, "go:goroutines")
        self.assertNotEqual(item.kind, "example")

    def test_l1_serves_joint_mcq(self):
        c = content_with_examples()
        st = fresh_state(c.concepts)
        for cid in c.concepts:
            st[cid].mastery = {lv: 0.9 for lv in config.LEVELS}
        st["go:goroutines"].mastery["recall"] = 0.5  # L1, still the heaviest deficit
        st["go:goroutines"].mastery["application"] = 0.0
        st["go:goroutines"].mastery["reasoning"] = 0.0
        cid, item = next_item(c, st, TODAY.isoformat())
        self.assertEqual(cid, "go:goroutines")
        self.assertEqual(item.kind, "example")
        self.assertEqual(item.fade, "L1")
        self.assertEqual(item.joint, "bottleneck")

    def test_pick_prefers_overdue_python_exec_resolve(self):
        c = content()
        s = ConceptState()
        s.resolves["q002"] = {"due": "2026-09-01", "step": 1}
        # q002 is application; give it a verify backend in the loaded object
        for q in c.questions:
            if q.id == "q002":
                q.verify = {"backend": "python_exec"}
        got = pick_question(c, "go:goroutines", s, today=TODAY.isoformat())
        self.assertEqual(got.id, "q002")


class TestSessionQuotas(unittest.TestCase):
    def test_quick_drill_meets_due_and_weakest_floors(self):
        c = content()
        st = fresh_state(c.concepts)
        for cid in c.concepts:
            st[cid].mastery["recall"] = 0.5
        st["go:closing"].due = "2026-09-01"
        st["go:select"].due = "2026-09-01"
        st["go:nil-channels"].mastery["recall"] = 0.0
        st["go:channels-buffered"].mastery["recall"] = 0.0
        plan = compose_session(c, st, TODAY.isoformat(), n=6)
        ids = [cid for cid, _ in plan]
        self.assertLessEqual(len(ids), 6)
        self.assertGreaterEqual(sum(1 for i in ids if i in {"go:closing", "go:select"}), 2)
        self.assertGreaterEqual(
            sum(1 for i in ids if i in {"go:nil-channels", "go:channels-buffered"}), 2)
        for a, b in zip(ids, ids[1:]):
            self.assertNotEqual(a, b)

    def test_design_session_serves_two_to_three_free_text(self):
        c = content()
        st = fresh_state(c.concepts)
        plan = compose_session(c, st, TODAY.isoformat(), n=9,
                               session_type="design_session")
        self.assertGreaterEqual(len(plan), 2)
        self.assertLessEqual(len(plan), 3)
        self.assertTrue(all(q.kind == "free_text" for _, q in plan))
        self.assertEqual([q.id for _, q in plan], ["ft01", "ft02"])


class TestGraderWeight(unittest.TestCase):
    def test_reasoning_score_scales_evidence_weight(self):
        s = ConceptState()
        q = Question("qx", "go:goroutines", "reasoning", "free_text", "?",
                     [], "")
        on_answer(s, q, None, True, today=TODAY, reasoning_score=2)
        self.assertAlmostEqual(s.mastery["reasoning"], 0.8 * 0.3 * (2 / 3))

    def test_wrong_twice_diagnoses_prereq_when_content_given(self):
        c = content()
        st = fresh_state(c.concepts)
        q = c.by_id["q005"]
        s = st["go:channels-buffered"]
        on_answer(s, q, 1, False, today=TODAY, content=c, all_state=st)
        res = on_answer(s, q, 1, False, today=TODAY, content=c, all_state=st)
        self.assertEqual(res["type"], "prereq")
        self.assertEqual(res["drill"], "go:channels-unbuffered")


class TestValidatorGaps(unittest.TestCase):
    def test_missing_counterexample_rejected(self):
        mis = {**MISCONCEPTIONS,
               "m1": {"belief": "b", "clarification": "c"}}
        with self.assertRaises(ValidationError) as e:
            load_content(CONCEPTS, QUESTIONS, mis)
        self.assertIn("counterexample", str(e.exception))

    def test_example_joint_missing_ruled_out_by_rejected(self):
        bad = {"go:goroutines": {
            "title": "x",
            "joints": {"bottleneck": {
                "question": "q",
                "options": [{"text": "a", "correct": True}, {"text": "b"}],
                "model_answer": "a"}}}}
        with self.assertRaises(ValidationError) as e:
            load_content(CONCEPTS, QUESTIONS, MISCONCEPTIONS, bad)
        self.assertIn("ruled_out_by", str(e.exception))

    def test_example_unknown_concept_rejected(self):
        bad = {"ghost:x": {"title": "x", "joints": {}}}
        with self.assertRaises(ValidationError) as e:
            load_content(CONCEPTS, QUESTIONS, MISCONCEPTIONS, bad)
        self.assertIn("unknown concept", str(e.exception))


class TestReport(unittest.TestCase):
    def test_sorted_by_deficit_times_centrality(self):
        from engine.selection import report
        c = content()
        st = fresh_state(c.concepts)
        for cid in c.concepts:
            st[cid].mastery = {lv: 0.9 for lv in config.LEVELS}
        st["go:select"].mastery["recall"] = 0.0       # 0.8 × 2 = 1.6
        st["go:nil-channels"].mastery["recall"] = 0.0  # 0.8 × 1 = 0.8
        rows = report(c, st, TODAY.isoformat())
        self.assertEqual([r["concept"] for r in rows[:2]],
                         ["go:select", "go:nil-channels"])

    def test_row_fields_frontier_fade_fails_misconceptions_overdue(self):
        from engine.selection import report
        c = content()
        st = fresh_state(c.concepts)
        s = st["go:goroutines"]
        s.fails = 3
        s.due = "2026-09-01"
        s.misconceptions = {"m1": 0.7, "buffered-never-blocks": 0.2}
        row = next(r for r in report(c, st, TODAY.isoformat())
                   if r["concept"] == "go:goroutines")
        self.assertEqual(row["frontier"], "recall")
        self.assertEqual(row["fade"], "L0")
        self.assertEqual(row["fails"], 3)
        self.assertEqual(row["misconceptions"], ["m1"])
        self.assertTrue(row["overdue"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
