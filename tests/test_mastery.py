"""Tests for the BKT mastery model (pure functions, no Neo4j needed)."""

from __future__ import annotations

from rb_neo.mastery import (
    apply_decay,
    bkt_update,
    compute_mastery,
    is_mastered,
    params_for,
)
from rb_neo.models import Attempt


def test_correct_increases_known() -> None:
    p = params_for("letter")
    assert bkt_update(0.3, correct=True, params=p) > 0.3


def test_incorrect_decreases_evidence() -> None:
    p = params_for("letter")
    # An incorrect answer should yield lower mastery than a correct one.
    assert bkt_update(0.5, correct=False, params=p) < bkt_update(0.5, correct=True, params=p)


def test_correct_streak_reaches_mastery() -> None:
    p = params_for("letter")
    prob = p.p_l0
    for _ in range(8):
        prob = bkt_update(prob, correct=True, params=p)
    assert is_mastered(prob)


def test_decay_pulls_toward_uncertainty() -> None:
    # A confident estimate erodes toward 0.5 over idle time, never below it.
    decayed = apply_decay(0.95, days_idle=30)
    assert 0.5 < decayed < 0.95


def test_decay_noop_for_no_gap() -> None:
    assert apply_decay(0.9, days_idle=0) == 0.9


def test_compute_mastery_multiskill() -> None:
    # Reading "cat" and "cot" correctly should master shared graphemes c and t.
    word_graphemes = {
        "cat": [("c", "letter"), ("a", "letter"), ("t", "letter")],
        "cot": [("c", "letter"), ("o", "letter"), ("t", "letter")],
    }
    attempts = [Attempt(word="cat", correct=True, ts=1000 + i * 86400) for i in range(5)] + [
        Attempt(word="cot", correct=True, ts=1000 + (5 + i) * 86400) for i in range(5)
    ]

    estimates = {e.skill: e for e in compute_mastery(attempts, word_graphemes)}
    assert {"c", "a", "t", "o"} <= set(estimates)
    # 'c' and 't' appear in every attempt -> highest mastery and most attempts.
    assert estimates["c"].attempts == 10
    assert is_mastered(estimates["c"].p)
    assert is_mastered(estimates["t"].p)
