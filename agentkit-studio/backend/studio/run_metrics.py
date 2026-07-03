"""Small deterministic run metrics derived from existing Studio signals."""

from __future__ import annotations

from typing import Any


def build_stop_report(
    *,
    reason: str,
    tool_calls: int = 0,
    failed_validations: int = 0,
    checkpoints: int = 0,
    wall_s: float = 0.0,
    token_cost: int = 0,
) -> dict[str, Any]:
    return {
        "reason": reason,
        "tool_calls": tool_calls,
        "failed_validations": failed_validations,
        "checkpoints": checkpoints,
        "wall_s": wall_s,
        "token_cost": token_cost,
    }


def build_run_metrics(
    *,
    stop_report: dict[str, Any],
    evidence_count: int = 0,
    weak_evidence_count: int = 0,
    tool_calls: int = 0,
    tool_failures: int = 0,
    review: dict[str, Any] | None = None,
    scorecard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return metrics with stable zero-denominator behavior."""
    verified_evidence = max(0, evidence_count - weak_evidence_count)
    return {
        "task_success": stop_report.get("reason") == "validation_passed"
        and not (review or {}).get("required", False),
        "tool_call_accuracy": _ratio(tool_calls - tool_failures, tool_calls),
        "citation_accuracy": _ratio(verified_evidence, evidence_count),
        "human_intervention_required": bool((review or {}).get("required", False)),
        "score": _score(scorecard),
        "stop_reason": stop_report.get("reason", ""),
        "stop_report": stop_report,
    }


def _ratio(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return max(0.0, min(1.0, float(num) / float(den)))


def _score(scorecard: dict[str, Any] | None) -> float | None:
    if not scorecard:
        return None
    value = scorecard.get("score", scorecard.get("total"))
    return float(value) if isinstance(value, (int, float)) else None
