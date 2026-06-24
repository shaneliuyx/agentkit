"""studio.panels.hillclimb — Hill Climbing Dashboard panel.

Builds HillClimbEvent frames from OptimizeResult archives. Fires only when
the runner performs actual prompt evolution via
agentkit.loop.hill_climb.hill_climb_from_traces(). The existing EvolveEvent
(GRPO distillation rounds) remains the primary evolve signal; HillClimbEvent
is the richer epoch-by-epoch trace from the DGM optimization loop.
"""

from __future__ import annotations

from agentkit.evolve.core import OptimizeResult

from studio.events import HillClimbEvent


def build_hill_climb_events(
    result: OptimizeResult,
    weaknesses: list[str] | None = None,
) -> list[HillClimbEvent]:
    """Convert an OptimizeResult archive into per-epoch HillClimbEvent frames.

    Args:
        result:     The OptimizeResult returned by evolve_prompt() /
                    hill_climb_from_traces().
        weaknesses: The TraceWeakness.pattern strings that steered the proposer
                    (may be None when called without weakness targeting).

    Returns:
        One HillClimbEvent per accepted variant in result.archive. If the
        archive is empty, returns a single event summarizing the baseline run.
    """
    w_list = list(weaknesses or [])

    if not result.archive:
        return [
            HillClimbEvent(
                epoch=0,
                score=result.baseline_score,
                delta=0.0,
                status="reject",
                note="no variants improved over baseline",
                weaknesses=w_list,
            )
        ]

    return [
        HillClimbEvent(
            epoch=variant.epoch,
            score=round(variant.score, 4),
            delta=round(variant.score - result.baseline_score, 4),
            status=variant.status,
            note=variant.note[:200],
            weaknesses=w_list,
        )
        for variant in result.archive
    ]
