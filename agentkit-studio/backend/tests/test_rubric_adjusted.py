"""Tests for adjusted_score — couples the structural rubric to remaining weaknesses.

The deterministic rubric measures QUANTITY (sections, URLs, words) and saturates at
1.0 while real defects remain; it scored 1.0 on a report with a malformed mermaid,
fabricated URLs, and no inline citations (DESIGN §14.7). adjusted_score enforces the
invariant: any open weakness ⇒ score < 1.0, with severity- and count-graded penalty.
"""
from __future__ import annotations

from studio.rubric import adjusted_score


def test_no_weaknesses_returns_base() -> None:
    assert adjusted_score(1.0, []) == 1.0
    assert adjusted_score(0.8, None) == 0.8


def test_any_weakness_breaks_perfect_score() -> None:
    """The user's invariant: a single open weakness means the score is not 1.0."""
    assert adjusted_score(1.0, ["any minor nit"]) < 1.0


def test_more_weaknesses_score_lower() -> None:
    """Monotonic: an improving doc with fewer weaknesses scores strictly higher."""
    many = [f"weakness {i}" for i in range(8)]
    few = many[:3]
    assert adjusted_score(1.0, many) < adjusted_score(1.0, few) < adjusted_score(1.0, [])


def test_hard_defects_cost_more_than_soft() -> None:
    """A deterministic, objective defect (malformed mermaid) is penalised harder than an
    equal count of soft, LLM-opinion weaknesses."""
    hard = adjusted_score(1.0, ["[Diagram] Malformed mermaid edge — node glued to label"])
    soft = adjusted_score(1.0, ["the tone could be more formal"])
    assert hard < soft


def test_real_failing_case_collapses() -> None:
    """The recorded 1.0 / 8-weakness report (1 hard mermaid) must drop well below 1.0."""
    ws = ["[Diagram] Malformed mermaid edge — ..."] + [f"w{i}" for i in range(7)]
    score = adjusted_score(1.0, ws)
    assert 0.5 <= score <= 0.7  # ~0.62 with current calibration


def test_penalty_is_floored() -> None:
    """Even a flood of weaknesses keeps some structural credit (no negative/zero score)."""
    flood = [f"w{i}" for i in range(100)]
    assert adjusted_score(1.0, flood) >= 1.0 * (1 - 0.6) - 0.001
