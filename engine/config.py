# All engine constants. Do not duplicate these elsewhere.
MASTERED = 0.8            # mastery at or above this = level passed
RECALL_OK = 0.7           # recall threshold used by diagnosis
TRAP = 0.7                # misconception weight: trap embed, report, diagnose
UP = 0.3                  # correct-answer gain factor
DOWN = 0.6                # wrong-answer decay factor
EVIDENCE = 0.5            # misconception weight increment per tagged wrong answer
REVIEW_DAYS = 3           # due date offset after a correct answer
FAIL_RETRY_DAYS = 1       # due date offset after a wrong answer
SELF_GRADE_WEIGHT = 0.7   # evidence weight for self-graded-correct
GOAL_WEIGHT = 1.5         # multiplier for concepts in the goal pack
PROBE_RATE = 0.25         # "why?" probe rate after correct reasoning MCQs (M4)
FADE_L1 = 0.4             # recall threshold for L0 -> L1
FADE_L2 = 0.7             # recall threshold for L1 -> L2 (application for L2 -> L3)
# M8 spaced re-solve of python_exec items: 3 days, 1 week, 3 weeks
RESOLVE_DAYS = (3, 7, 21)

LEVELS = ("recall", "application", "reasoning")
