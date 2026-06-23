"""studio.panels.loopdoctor — Loop Doctor audit panel (SPEC §10 M8).

A Studio run (``plan → topology → run``, bounded by ``FanoutBudget``, gated by
``run_gate``, verified by ``quality.verify``) IS a loop-library loop. This panel
audits that run against loop-library's four audit dimensions
(``references/audit.md``) by READING the run's already-collected outcomes — it
composes Studio's existing primitives and adds no new agentkit code:

  - ``bounded``         ⇆ ``FanoutBudget.ceiling`` was set (a token ceiling).
  - ``material_checks`` ⇆ ``quality.verify`` ran and produced verifiable claims.
  - ``safe_actions``    ⇆ no per-phase ``run_gate`` emitted ESCALATE/REJECT.
  - ``clear_stopping``  ⇆ the plan is a finite DAG (resolved ``depends_on``, no
                          cycle) — a finite DAG IS the stop condition.

The builder is PURE: it takes the run's plan + collected events and returns a
``LoopDoctorEvent``. Repairs live in each check's ``fix`` field as SUGGESTIONS;
nothing is auto-applied (loop-library's no-silent-change rule).
"""

from __future__ import annotations

from typing import Any

from studio.events import GateEvent, LoopDoctorEvent, VerifyEvent

#: Gate outcomes that mean a consequential action was NOT auto-allowed.
_UNSAFE_OUTCOMES = frozenset({"escalate", "reject"})


def _check(name: str, status: str, fix: str = "") -> dict[str, Any]:
    """One audit row. ``status`` ∈ pass|warn|fail; ``fix`` is "" on pass."""
    return {"name": name, "status": status, "fix": fix}


def _bounded(budget_ceiling: float | None) -> dict[str, Any]:
    """bounded ⇆ FanoutBudget.ceiling: a token ceiling caps fan-out spend."""
    if budget_ceiling is not None:
        return _check("bounded", "pass")
    return _check(
        "bounded",
        "warn",
        "Set a token ceiling in the Budget panel so fan-out spend is bounded.",
    )


def _material_checks(verify_event: VerifyEvent | None) -> dict[str, Any]:
    """material_checks ⇆ quality.verify: verification ran over real claims.

    PASS when verify() ran AND found at least one verifiable claim (a finding or
    an uncited claim is evidence the output carried checkable assertions). WARN
    when verification produced nothing to check — the output had no verifiable
    claims, so the loop's success gate is not observable.
    """
    if verify_event is None:
        return _check(
            "material_checks",
            "warn",
            "Verification did not run; ensure quality.verify audits the final output.",
        )
    if verify_event.findings or verify_event.uncited:
        return _check("material_checks", "pass")
    return _check(
        "material_checks",
        "warn",
        "No verifiable claims in the output; add a reproducible success check.",
    )


def _safe_actions(gate_events: list[GateEvent]) -> dict[str, Any]:
    """safe_actions ⇆ run_gate outcomes: no phase escalated/rejected.

    FAIL (the strongest signal) when any phase proposal escalated/rejected — a
    consequential action was not auto-allowed and needs an approval boundary.
    The fix names the offending phase(s). PASS when every gate accepted.
    """
    flagged = [g for g in gate_events if g.outcome.lower() in _UNSAFE_OUTCOMES]
    if not flagged:
        return _check("safe_actions", "pass")
    names = ", ".join(g.name for g in flagged)
    return _check(
        "safe_actions",
        "fail",
        f"Gate flagged {names}; add an approval boundary before that action.",
    )


def _clear_stopping(plan_steps: list[dict[str, Any]]) -> dict[str, Any]:
    """clear_stopping ⇆ finite DAG: every depends_on resolves and there is no
    cycle, so the plan terminates. A finite DAG IS the stop condition.

    WARN names the first problem: a dangling dependency (points at no step) or a
    dependency cycle (the plan would never terminate).
    """
    ids = {str(s.get("id")) for s in plan_steps}
    deps: dict[str, list[str]] = {
        str(s.get("id")): [str(d) for d in (s.get("depends_on") or [])]
        for s in plan_steps
    }

    # Dangling dependency: a depends_on id that names no step.
    for sid, ds in deps.items():
        for d in ds:
            if d not in ids:
                return _check(
                    "clear_stopping",
                    "warn",
                    f"Step {sid} depends on unknown step {d}; resolve the dependency "
                    "so the plan is a finite DAG.",
                )

    # Cycle detection (DFS three-colour). A cycle means no terminal state.
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {sid: WHITE for sid in deps}

    def _has_cycle(sid: str) -> bool:
        colour[sid] = GREY
        for d in deps.get(sid, ()):
            if colour.get(d) == GREY:
                return True
            if colour.get(d) == WHITE and _has_cycle(d):
                return True
        colour[sid] = BLACK
        return False

    for sid in deps:
        if colour[sid] == WHITE and _has_cycle(sid):
            return _check(
                "clear_stopping",
                "warn",
                f"Dependency cycle through step {sid}; break it so the plan terminates.",
            )

    return _check("clear_stopping", "pass")


def build_loopdoctor_event(
    plan_steps: list[dict[str, Any]],
    *,
    budget_ceiling: float | None,
    gate_events: list[GateEvent],
    verify_event: VerifyEvent | None,
) -> LoopDoctorEvent:
    """Audit the run against loop-library's four dimensions → ``LoopDoctorEvent``.

    PURE: reads the run's plan steps (the ``PlanEvent.steps`` dict shape:
    ``{id, description, depends_on, role, difficulty}``) plus the collected
    ``gate_events`` + ``verify_event`` + ``budget_ceiling``, and never mutates or
    re-runs anything. Each check's ``fix`` is a suggestion only.
    """
    return LoopDoctorEvent(
        checks=[
            _bounded(budget_ceiling),
            _material_checks(verify_event),
            _safe_actions(list(gate_events)),
            _clear_stopping(list(plan_steps)),
        ]
    )
