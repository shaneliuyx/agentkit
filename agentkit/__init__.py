"""agentkit — a lean, reusable agent-systems library.

Four modules, one design philosophy:
  - context  — deterministic, zero-LLM conversation compaction (NEW component).
  - memory   — tiered memory: cheap deterministic extraction + a vector store.
  - runtime  — durable DAG execution (graph store, file lock, scheduler).
  - agent    — a dependency-injected ReAct loop + a difficulty router.

Design rules:
  1. Protocol seams (agentkit.types): pluggable deps are Protocols, never
     concrete vendors — inject your own oMLX/Claude/fake.
  2. Deterministic-first tiering: cheap deterministic tiers before LLM tiers.

The context + types + memory re-exports are always available (their only hard
dependency is numpy). The runtime + agent re-exports are wrapped in try/except
so a missing optional dependency never hard-fails ``import agentkit``.
"""

from __future__ import annotations

__version__ = "0.1.0"

# Always-available: types + context (the new component) + memory.
from agentkit.context import CompactResult, compact, merge
from agentkit.memory import MemoryEntry, MemoryStore
from agentkit.types import ChatResult, Embedder, LLMClient, Message

__all__ = [
    "__version__",
    # context
    "compact",
    "merge",
    "CompactResult",
    # memory
    "MemoryStore",
    "MemoryEntry",
    # types
    "Embedder",
    "LLMClient",
    "Message",
    "ChatResult",
]

# Optional: runtime (durable DAG). Wrapped so a missing dep can't break import.
try:
    from agentkit.runtime import GraphStore, Scheduler

    __all__ += ["GraphStore", "Scheduler"]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass

# Optional: agent (ReAct loop + router + batch runner).
try:
    from agentkit.agent import AgentResult, BatchConfig, route, run_agent, run_batch

    __all__ += ["run_agent", "route", "AgentResult", "run_batch", "BatchConfig"]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass

# Optional: agent roles (role specialization over run_agent).
try:
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

    __all__ += [
        "AgentRole",
        "run_role",
        "dispatch",
        "RESEARCHER",
        "REVIEWER",
        "WRITER",
        "VERIFIER",
        "DEFAULT_ROLES",
    ]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass

# Optional: quality (source-grounding / verification pass).
try:
    from agentkit.quality import (
        Claim,
        HttpUrlChecker,
        UrlChecker,
        VerifyFinding,
        extract_claims,
        find_uncited,
        verify,
    )

    __all__ += [
        "verify",
        "VerifyFinding",
        "Claim",
        "extract_claims",
        "find_uncited",
        "HttpUrlChecker",
        "UrlChecker",
    ]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass

# Optional: orchestrator (autonomous multi-round loop + pure decision modules).
try:
    from agentkit.orchestrator import (
        Dimension,
        Finding,
        OrchestratorConfig,
        ProgressState,
        Rubric,
        StallAssessment,
        assess,
        cascade,
        exceeds_budget,
        init_task,
        is_novel,
        log_event,
        run,
        similarity,
    )

    __all__ += [
        "run",
        "OrchestratorConfig",
        "assess",
        "StallAssessment",
        "exceeds_budget",
        "is_novel",
        "similarity",
        "Rubric",
        "Dimension",
        "cascade",
        "ProgressState",
        "Finding",
        "init_task",
        "log_event",
    ]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass

# Optional: backends (concrete LLMClient adapters).
try:
    from agentkit.backends import CliLLMClient

    __all__ += ["CliLLMClient"]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass
