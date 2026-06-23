"""studio.panels.router — difficulty-router panel (SPEC §5.5 #7).

``agentkit.agent.router.route(step_difficulty)`` maps a difficulty LABEL to a
``RouteDecision(backend, model, rationale)``. Studio emits one ``router`` frame
per step: the step's difficulty (defaulting to "medium" when the planner left it
unset) and the routed backend as the "tier".
"""

from __future__ import annotations

import warnings

from agentkit.agent.router import route
from agentkit.planner.core import PlanStep

from studio.events import RouterEvent

#: Difficulty the router uses when a plan step carries none.
_DEFAULT_DIFFICULTY = "medium"


def build_router_event(step: PlanStep) -> RouterEvent:
    """Route one plan step → ``RouterEvent``.

    The router warns on an unknown difficulty (falls back to medium); we suppress
    that warning here since the panel already shows the resolved tier.
    """
    difficulty = (step.difficulty or _DEFAULT_DIFFICULTY).strip().lower()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        decision = route(difficulty)
    return RouterEvent(
        step_id=step.id,
        difficulty=difficulty,
        tier=decision.backend,
    )
