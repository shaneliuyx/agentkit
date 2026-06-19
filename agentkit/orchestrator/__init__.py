"""agentkit.orchestrator — autonomous multi-round orchestration.

Composes PURE decision modules (stall / diversity / select) with a durable
state layer and the deterministic context compactor into a crash-resumable,
auditable orchestration loop.
"""

from agentkit.orchestrator.diversity import is_novel, most_similar, similarity
from agentkit.orchestrator.loop import OrchestratorConfig, Spawn, run
from agentkit.orchestrator.select import (
    Dimension,
    Rubric,
    cascade,
    prefilter,
    score_and_rank,
)
from agentkit.orchestrator.stall import (
    StallAssessment,
    assess,
    exceeds_budget,
)
from agentkit.orchestrator.state import (
    Finding,
    ProgressState,
    append_direction,
    append_finding,
    append_iteration_log,
    init_task,
    load_progress,
    log_event,
    read_directions,
    read_findings,
    save_progress,
)

__all__ = [
    # loop
    "run",
    "OrchestratorConfig",
    "Spawn",
    # stall
    "assess",
    "StallAssessment",
    "exceeds_budget",
    # diversity
    "is_novel",
    "similarity",
    "most_similar",
    # select
    "Rubric",
    "Dimension",
    "cascade",
    "prefilter",
    "score_and_rank",
    # state
    "ProgressState",
    "Finding",
    "init_task",
    "log_event",
    "load_progress",
    "save_progress",
    "append_finding",
    "read_findings",
    "read_directions",
    "append_direction",
    "append_iteration_log",
]
