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
    pass_economics: dict[str, Any] | None = None,
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
        "pass_economics": pass_economics or {},
    }


def _ratio(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return max(0.0, min(1.0, float(num) / float(den)))


def _per(num: float, den: float) -> float | None:
    return (num / den) if den > 0 else None


def build_pass_economics(
    *, pass_ledger: dict[str, dict[str, int]] | None, token_cost: int = 0
) -> dict[str, Any]:
    """Per-run acceptance economics (L5). Feeds S5: passes with ~0% acceptance
    over a nonzero run of attempts are removal/redesign candidates."""
    ledger = pass_ledger or {}
    total_accepted = sum(int(e.get("accepted", 0)) for e in ledger.values())
    passes = {
        name: {
            "ran": int(e.get("ran", 0)),
            "accepted": int(e.get("accepted", 0)),
            "acceptance_rate": _ratio(int(e.get("accepted", 0)), int(e.get("ran", 0))),
        }
        for name, e in sorted(ledger.items())
    }
    return {
        "passes": passes,
        "total_accepted": total_accepted,
        "tokens_per_accepted": _per(float(token_cost), float(total_accepted)),
    }


def _score(scorecard: dict[str, Any] | None) -> float | None:
    if not scorecard:
        return None
    value = scorecard.get("score", scorecard.get("total"))
    return float(value) if isinstance(value, (int, float)) else None
