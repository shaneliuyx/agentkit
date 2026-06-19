"""agentkit.agent — dependency-injected ReAct loop + difficulty router."""

from agentkit.agent.batch import BatchConfig, run_batch
from agentkit.agent.loop import (
    AgentResult,
    DictToolRegistry,
    ToolRegistry,
    TrajectoryStep,
    quarantine,
    run_agent,
)
from agentkit.agent.roles import (
    DEFAULT_ROLES,
    REVIEWER,
    RESEARCHER,
    VERIFIER,
    WRITER,
    AgentRole,
    dispatch,
    run_role,
)
from agentkit.agent.router import RouteDecision, route

__all__ = [
    "run_agent",
    "AgentResult",
    "TrajectoryStep",
    "ToolRegistry",
    "DictToolRegistry",
    "quarantine",
    "route",
    "RouteDecision",
    "run_batch",
    "BatchConfig",
    "AgentRole",
    "run_role",
    "dispatch",
    "RESEARCHER",
    "REVIEWER",
    "WRITER",
    "VERIFIER",
    "DEFAULT_ROLES",
]
