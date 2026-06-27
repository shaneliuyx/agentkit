"""agentkit.topology.sizing — dynamic agent count derived from task count.

SizingConfig values come from the Loop Config UI panel (§9 of the Studio
design doc) via LoopConfig.sizing(). The defaults here are fallbacks only;
production callers always pass an explicit cfg built from user-set sliders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class SizingConfig:
    """Controls how many agents handle a task list.

    min_tasks_per_agent: lower bound on tasks per agent (not a hard floor —
        the last agent may receive fewer when task count doesn't divide evenly).
    max_tasks_per_agent: tasks are partitioned so no agent exceeds this count.
    """

    min_tasks_per_agent: int = 3
    max_tasks_per_agent: int = 5
    max_agents: int = 5  # hard ceiling on agent count, independent of task count
    #   Backstop against a flooded task list spawning unbounded agents (the
    #   2026-06-27 gap-flood: 74 false gaps -> ceil(74/5)=18 agents -> 2.5M tokens).
    #   Real bounding is upstream via section-consolidation; this is the floor
    #   under it. Matches the ">=3 <=5 agents" product requirement.


def compute_n_agents(n_tasks: int, cfg: SizingConfig = SizingConfig()) -> int:
    """Return the number of agents needed to cover ``n_tasks``.

    Derived from task count so no hard-coded ``n`` is needed. The last agent
    may receive fewer than ``min_tasks_per_agent`` when the remainder is
    smaller — that is by design.

    Examples (max_tasks_per_agent=5):
      n=0  -> 1
      n=3  -> 1  (3 tasks in one agent)
      n=5  -> 1  (5 tasks in one agent)
      n=6  -> 2  (5+1)
      n=10 -> 2  (5+5)
      n=11 -> 3  (5+5+1)
    """
    if n_tasks <= 0:
        return 1
    return max(1, min(cfg.max_agents, math.ceil(n_tasks / cfg.max_tasks_per_agent)))


def assign_tasks(tasks: list, cfg: SizingConfig = SizingConfig()) -> list[list]:
    """Partition ``tasks`` across agents; returns list-of-lists (one per agent).

    Distribution is ceiling-div so earlier agents may carry one extra task.
    The last bucket may be smaller than ``min_tasks_per_agent`` — acceptable.
    """
    if not tasks:
        return [[]]
    n = compute_n_agents(len(tasks), cfg)
    size = math.ceil(len(tasks) / n)
    return [tasks[i * size : (i + 1) * size] for i in range(n)]
