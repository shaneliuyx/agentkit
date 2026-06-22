"""agentkit.gates — the LEARN admission gate (REPLAN §4–§5).

Deterministic, LLM-non-overridable pipeline: syntax -> containment -> sandbox
execute -> regression -> safety (optional LLM veto) -> delta, yielding
``ACCEPT | REJECT | ESCALATE``. The injected safety LLM can only add a
rejection, never grant an acceptance.
"""

from agentkit.gates.core import (
    DEFAULT_EXEC_TIMEOUT,
    DEFAULT_MIN_DELTA,
    Evaluator,
    Gate,
    Outcome,
    Verdict,
    run_gate,
)

__all__ = [
    "Gate",
    "Verdict",
    "Outcome",
    "Evaluator",
    "run_gate",
    "DEFAULT_MIN_DELTA",
    "DEFAULT_EXEC_TIMEOUT",
]
