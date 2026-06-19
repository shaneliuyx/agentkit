"""agentkit.orchestrator.stall — PURE stall detection + budget guard.

Generalized from the Deli_AutoResearch orchestrator's stall heuristics. The
thesis of this module is PURITY: it makes the continue / pivot / escalate
decision and the budget check as a function of its INPUTS ONLY. There is NO
time, NO randomness, and NO I/O here. Elapsed time and any metrics are passed
in by the caller (orchestrator/loop.py owns the injected clock).

EXECUTION != EVALUATION: spawned workers do the work; ``assess`` (here) judges
whether progress is real. "Pivot" means change a STRUCTURAL constraint, not
tactics — that intent is encoded in the returned reason text.
"""

from __future__ import annotations

from dataclasses import dataclass

# Decision vocabulary.
CONTINUE = "continue"
PIVOT = "pivot"
ESCALATE = "escalate"


@dataclass(frozen=True)
class StallAssessment:
    """The verdict of a single ``assess`` call.

    action ∈ {"continue", "pivot", "escalate"}. ``stale_count`` is the updated
    run of unproductive rounds. ``reason`` is human-readable rationale.
    """

    action: str
    stale_count: int
    reason: str


def assess(
    new_findings: int,
    stale_count: int,
    metric_prev: float | None = None,
    metric_new: float | None = None,
    pivot_at: int = 2,
    escalate_at: int = 4,
) -> StallAssessment:
    """Decide continue / pivot / escalate from progress signals (PURE).

    Stall rule: a round is unproductive when it yields 0 new findings OR (when
    both metrics are given) the metric regressed (``metric_new < metric_prev``).
    An unproductive round increments ``stale_count``; a productive one resets it
    to 0.

    Escalation ladder on the resulting stale count:
      - ``>= escalate_at`` → escalate (hand off; local search is exhausted)
      - ``>= pivot_at``    → pivot (change a STRUCTURAL constraint, not tactics)
      - otherwise          → continue

    Args:
        new_findings: Count of new findings produced this round.
        stale_count:  Prior run of consecutive unproductive rounds.
        metric_prev:  Optional previous progress metric.
        metric_new:   Optional current progress metric.
        pivot_at:     Stale threshold at which to pivot.
        escalate_at:  Stale threshold at which to escalate.

    Returns:
        A frozen ``StallAssessment``.
    """
    metric_regressed = (
        metric_prev is not None
        and metric_new is not None
        and metric_new < metric_prev
    )
    unproductive = new_findings <= 0 or metric_regressed

    if unproductive:
        new_stale = stale_count + 1
    else:
        new_stale = 0

    if new_stale >= escalate_at:
        return StallAssessment(
            action=ESCALATE,
            stale_count=new_stale,
            reason=(
                f"stale={new_stale} >= escalate_at={escalate_at}: local search "
                "exhausted — escalate / hand off"
            ),
        )
    if new_stale >= pivot_at:
        return StallAssessment(
            action=PIVOT,
            stale_count=new_stale,
            reason=(
                f"stale={new_stale} >= pivot_at={pivot_at}: pivot — change a "
                "structural constraint, not tactics"
            ),
        )
    if unproductive:
        return StallAssessment(
            action=CONTINUE,
            stale_count=new_stale,
            reason=f"unproductive round (stale={new_stale}); keep going",
        )
    return StallAssessment(
        action=CONTINUE,
        stale_count=new_stale,
        reason="productive round; stale reset to 0",
    )


def exceeds_budget(
    rounds: int,
    elapsed_s: float,
    max_rounds: int = 15,
    max_seconds: float = 1800.0,
) -> bool:
    """Return True when either the round or the wall-clock budget is exhausted.

    PURE: ``elapsed_s`` is passed in by the caller (which owns the clock).
    """
    return rounds >= max_rounds or elapsed_s >= max_seconds


if __name__ == "__main__":
    # Reset: a productive round zeroes the stale count and continues.
    a = assess(new_findings=3, stale_count=2)
    assert a.action == CONTINUE and a.stale_count == 0, a

    # One unproductive round below pivot_at → continue, stale increments.
    a = assess(new_findings=0, stale_count=0)
    assert a.action == CONTINUE and a.stale_count == 1, a

    # Pivot transition at stale >= pivot_at (default 2).
    a = assess(new_findings=0, stale_count=1)
    assert a.action == PIVOT and a.stale_count == 2, a
    assert "structural" in a.reason

    # Escalate transition at stale >= escalate_at (default 4).
    a = assess(new_findings=0, stale_count=3)
    assert a.action == ESCALATE and a.stale_count == 4, a

    # Metric regression counts as unproductive even with findings.
    a = assess(new_findings=5, stale_count=0, metric_prev=0.9, metric_new=0.4)
    assert a.action == CONTINUE and a.stale_count == 1, a

    # Metric improvement with findings stays productive (reset).
    a = assess(new_findings=1, stale_count=3, metric_prev=0.4, metric_new=0.9)
    assert a.action == CONTINUE and a.stale_count == 0, a

    # Budget guard.
    assert exceeds_budget(rounds=15, elapsed_s=0.0) is True
    assert exceeds_budget(rounds=0, elapsed_s=1800.0) is True
    assert exceeds_budget(rounds=3, elapsed_s=10.0) is False

    print("stall self-check OK")
