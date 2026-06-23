"""studio.panels.selfimprove — self-improve / re-plan panel (SPEC §5.5 #2).

The autonomous loop's PURE assessor ``orchestrator.stall.assess`` judges whether
a round was productive and decides continue / pivot / escalate. Studio drives a
lightweight per-round assessment over the running phase outputs: a phase that
produced output is a "productive" round (1 finding); an empty phase is
unproductive, driving the stall ladder. Each call yields a ``selfimprove`` frame
mirroring a ``StallAssessment``.

This is the deterministic, model-free spine of ``orchestrator.run`` surfaced as
GUI telemetry — the same ``assess`` the real loop uses, without committing to a
full crash-resumable on-disk run for a single GUI session.
"""

from __future__ import annotations

from agentkit.orchestrator.stall import ESCALATE, assess

from studio.events import SelfImproveEvent


class SelfImproveTracker:
    """Runs ``assess`` across phases, carrying the stall count between rounds."""

    def __init__(self) -> None:
        self._stale = 0
        self._round = 0
        self._prev_metric: float | None = None

    def assess_phase(self, *, produced_output: bool, metric: float) -> SelfImproveEvent:
        """Assess one phase as an orchestration round → ``SelfImproveEvent``."""
        self._round += 1
        verdict = assess(
            new_findings=1 if produced_output else 0,
            stale_count=self._stale,
            metric_prev=self._prev_metric,
            metric_new=metric,
        )
        self._stale = verdict.stale_count
        self._prev_metric = metric
        return SelfImproveEvent(
            round=self._round,
            stalled=verdict.action != "continue",
            assessment=verdict.reason,
            action=verdict.action,
        )

    @property
    def escalated(self) -> bool:
        """True once the stall ladder reached escalate (local search exhausted)."""
        return self._stale >= 4  # mirrors stall.assess default escalate_at
