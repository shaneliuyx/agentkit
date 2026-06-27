"""studio.models — shared dataclasses for the Studio API surface.

LoopConfig carries the user-set Loop Config panel values from POST /session
through Session to the runner. The runner calls session.loop_config.sizing()
to get a SizingConfig for compute_n_agents.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentkit.topology.sizing import SizingConfig


@dataclass
class LoopConfig:
    """Loop Config panel settings — sent in POST /session body as loop_config.

    deliverable_path  — explicit path to the artifact to improve (overrides
                        hill-climb history lookup when set).
    auto_improve      — when True and no explicit path, seed from the latest
                        prior run artifact.md.
    min_tasks_per_agent — slider lower bound (not a hard floor for last agent).
    max_tasks_per_agent — slider upper bound; agent count = ceil(n / max).
    max_agents          — slider hard ceiling on agent count per phase, so a
                        flooded task list can never explode the topology
                        (2026-06-27 gap-flood fix). Product spec: 3..5.
    """

    deliverable_path: str | None = None
    auto_improve: bool = True
    min_tasks_per_agent: int = 3
    max_tasks_per_agent: int = 5
    max_agents: int = 5

    def sizing(self) -> SizingConfig:
        """Return SizingConfig derived from UI slider values."""
        return SizingConfig(
            min_tasks_per_agent=self.min_tasks_per_agent,
            max_tasks_per_agent=self.max_tasks_per_agent,
            max_agents=self.max_agents,
        )

    @classmethod
    def from_dict(cls, d: dict) -> "LoopConfig":
        """Parse from the POST /session body loop_config sub-dict."""
        if not d:
            return cls()
        return cls(
            deliverable_path=d.get("deliverable_path") or None,
            auto_improve=bool(d.get("auto_improve", True)),
            min_tasks_per_agent=int(d.get("min_tasks_per_agent", 3)),
            max_tasks_per_agent=int(d.get("max_tasks_per_agent", 5)),
            max_agents=int(d.get("max_agents", 5)),
        )
