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

# Optional: the standard OpenAI-compatible adapter (needs the [openai] extra).
# Module import is lazy-guarded, so this re-export only attaches the names; the
# clear "pip install agentkit[openai]" hint surfaces on construction, not here.
try:
    from agentkit.backends.openai_compat import (
        OpenAIChatClient,
        OpenAIEmbedder,
        make_client,
    )

    __all__ += ["OpenAIChatClient", "OpenAIEmbedder", "make_client"]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass

# Optional: the native Claude adapter (needs the [anthropic] extra). Same
# LLMClient seam as the OpenAI adapter — multiple vendors, one interface. The
# clear "pip install agentkit[anthropic]" hint surfaces on construction.
try:
    from agentkit.backends.anthropic_client import AnthropicChatClient

    __all__ += ["AnthropicChatClient"]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass

# Optional: the self-improving layer (re-plan) — config-driven roles, the
# sandbox/gates security spine, the evolve/skills optimizer, planner, codegen,
# and the SelfImprovingAgent facade. See docs/REPLAN-agentkit.md. Submodule
# imports (agentkit.config, agentkit.gates, ...) always work; these are the
# top-level conveniences. Dep-guarded like the blocks above.
try:
    from agentkit.config import dump_role, load_default_roles, load_roles
    from agentkit.gates import Outcome, run_gate
    from agentkit.planner import plan
    from agentkit.sandbox import SubprocessSandbox
    from agentkit.selfimproving import SelfImprovingAgent

    __all__ += [
        "SelfImprovingAgent",
        "load_default_roles",
        "load_roles",
        "dump_role",
        "SubprocessSandbox",
        "run_gate",
        "Outcome",
        "plan",
    ]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass

# Optional: newer public surfaces from the EDP-compliance pass — streaming /
# TTFT (P43), the fan-out cost ceiling (P39), group-relative distillation (P45).
try:
    from agentkit.agent.loop import run_agent_stream
    from agentkit.evolve import distill_group
    from agentkit.orchestrator import BudgetExceeded, FanoutBudget
    from agentkit.topology import assign_topologies, classify_step_topology, run_plan
    from agentkit.types import ChatChunk, stream_chat, supports_streaming

    __all__ += [
        "run_agent_stream",
        "stream_chat",
        "ChatChunk",
        "supports_streaming",
        "FanoutBudget",
        "BudgetExceeded",
        "distill_group",
        "assign_topologies",
        "classify_step_topology",
        "run_plan",
    ]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass
