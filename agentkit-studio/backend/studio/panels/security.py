"""studio.panels.security — security-spine panel (SPEC §5.5 #4).

Any tool/codegen step is admitted through the LEARN gate
(``gates.run_gate`` over a ``SubprocessSandbox``). The gate is deterministic and
LLM-non-overridable: syntax → containment → sandbox-execute → regression →
safety → delta, yielding ACCEPT | REJECT | ESCALATE. Studio emits one ``gate``
frame per proposal carrying the ``Outcome`` + the stage that produced it +
whether it actually ran sandboxed.

The panel evaluates a proposal with a trivial pass-through evaluator (the GUI
demonstrates the gate's *containment/execute* discipline, not a real eval
metric), so a benign ``print('ok')`` is ACCEPTed and an ``import subprocess``
proposal is ESCALATEd — visibly, on the panel.
"""

from __future__ import annotations

from typing import Any

from agentkit.gates.core import Outcome, run_gate
from agentkit.sandbox.core import SubprocessSandbox

from studio.events import GateEvent


def run_gate_event(name: str, proposal: dict[str, Any], *, cwd: str) -> GateEvent:
    """Run one proposal through the LEARN gate → ``GateEvent`` (never raises).

    ``proposal`` is a dict (e.g. ``{"type": "tool", "code": "..."}``). The
    evaluator returns 1.0 so a runnable, contained, improving proposal reaches
    ACCEPT; containment/syntax/execute failures short-circuit deterministically.
    """
    try:
        verdict = run_gate(
            proposal,
            baseline_score=0.0,
            sandbox=SubprocessSandbox(),
            evaluator=lambda _p: 1.0,
            cwd=cwd,
        )
        sandboxed = "execute" in verdict.details
        return GateEvent(
            name=name,
            outcome=verdict.status.value,
            detail=f"{verdict.stage}: {verdict.reason}",
            sandboxed=sandboxed,
        )
    except Exception as exc:  # noqa: BLE001 - a gate failure must not break the run
        return GateEvent(
            name=name,
            outcome=Outcome.ESCALATE.value,
            detail=f"gate error: {exc}",
            sandboxed=False,
        )
