"""studio.events — THE SSE event contract (SPEC §4).

Every frame on the wire is ``{type, session_id, ts, payload}``. One event type
per GUI concern so the frontend reducer stays a flat switch. Each event is a
frozen dataclass carrying ONLY its payload fields; ``session_id`` and ``ts`` are
stamped by the serializer at emit time (the runner owns the clock), so the
event dataclasses themselves are pure values with no ambient time baked in.

Design rules:
  - Immutable: every event is ``@dataclass(frozen=True)`` — events are values,
    never mutated after construction.
  - Exact field names: the payload keys mirror SPEC §4 verbatim so the TS
    ``api/types.ts`` union can be a 1:1 mirror.
  - ``to_sse(session_id, ts)`` returns the full envelope dict; ``sse_data``
    returns the JSON string an ``EventSourceResponse`` puts in the ``data:``
    field. The ``EVENT_TYPE`` class attribute is the discriminator.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StudioEvent:
    """Base for every SSE frame. Subclasses set ``EVENT_TYPE`` + payload fields.

    The envelope (``type``/``session_id``/``ts``) is assembled in ``to_sse``; the
    payload is every dataclass field of the subclass.
    """

    #: Discriminator written to the ``type`` field; overridden per subclass.
    EVENT_TYPE: str = field(default="event", init=False, repr=False)

    def payload(self) -> dict[str, Any]:
        """The payload dict = all dataclass fields (EVENT_TYPE is excluded as it
        is a non-init class-level field)."""
        data = asdict(self)
        data.pop("EVENT_TYPE", None)
        return data

    def to_sse(self, session_id: str, ts: float) -> dict[str, Any]:
        """Full SSE envelope: ``{type, session_id, ts, payload}``."""
        return {
            "type": self.EVENT_TYPE,
            "session_id": session_id,
            "ts": ts,
            "payload": self.payload(),
        }

    def sse_data(self, session_id: str, ts: float) -> str:
        """JSON string for the ``data:`` field of an SSE frame."""
        return json.dumps(self.to_sse(session_id, ts), default=str)


# ---------------------------------------------------------------------------
# Lifecycle / structure
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionEvent(StudioEvent):
    """``session`` — header. ``llm``/``embed`` are ``{label, model}`` dicts."""

    EVENT_TYPE: str = field(default="session", init=False, repr=False)
    llm: dict[str, Any] = field(default_factory=dict)
    embed: dict[str, Any] = field(default_factory=dict)
    mode: str = "auto"


@dataclass(frozen=True)
class PlanEvent(StudioEvent):
    """``plan`` — phases. ``steps`` is a list of
    ``{id, description, depends_on, role, difficulty}``."""

    EVENT_TYPE: str = field(default="plan", init=False, repr=False)
    task: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TopologyEvent(StudioEvent):
    """``topology`` — graph shapes. ``steps`` is ``[{id, topology}]`` post
    ``assign_topologies``."""

    EVENT_TYPE: str = field(default="topology", init=False, repr=False)
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class GraphEvent(StudioEvent):
    """``graph`` — derived render graph.

    ``nodes`` = ``[{id, kind, phase, label, state}]``;
    ``edges`` = ``[{from, to, kind}]`` (``from`` is a reserved word in Python, so
    the dicts are built by the caller — we never use it as an identifier here).
    """

    EVENT_TYPE: str = field(default="graph", init=False, repr=False)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-phase
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhaseStartEvent(StudioEvent):
    """``phase_start`` — node → running (pulse)."""

    EVENT_TYPE: str = field(default="phase_start", init=False, repr=False)
    step_id: str = ""


@dataclass(frozen=True)
class AgentEventEvent(StudioEvent):
    """``agent_event`` — forwarded ``orchestrator.log_event`` record."""

    EVENT_TYPE: str = field(default="agent_event", init=False, repr=False)
    step_id: str = ""
    name: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TokenEvent(StudioEvent):
    """``token`` — token HUD update for one call + the running cumulative."""

    EVENT_TYPE: str = field(default="token", init=False, repr=False)
    step_id: str = ""
    input: int = 0
    output: int = 0
    total: int = 0
    estimated: bool = False
    cumulative: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextEvent(StudioEvent):
    """``text`` — streamed ``ChatChunk.text`` delta for the stream pane."""

    EVENT_TYPE: str = field(default="text", init=False, repr=False)
    step_id: str = ""
    delta: str = ""


@dataclass(frozen=True)
class PhaseDoneEvent(StudioEvent):
    """``phase_done`` — node → done. Fields mirror ``StepRun``."""

    EVENT_TYPE: str = field(default="phase_done", init=False, repr=False)
    step_id: str = ""
    topology: str = ""
    n_agents: int = 0
    tokens: int = 0
    wall_s: float = 0.0
    output: str = ""


@dataclass(frozen=True)
class BudgetEvent(StudioEvent):
    """``budget`` — budget gauge."""

    EVENT_TYPE: str = field(default="budget", init=False, repr=False)
    spent: int = 0
    ceiling: float | None = None
    exceeded: bool = False


@dataclass(frozen=True)
class RouterEvent(StudioEvent):
    """``router`` — router panel. ``tier`` is the routed backend label."""

    EVENT_TYPE: str = field(default="router", init=False, repr=False)
    step_id: str = ""
    difficulty: str = ""
    tier: str = ""


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MemoryEvent(StudioEvent):
    """``memory`` — ``entries`` = ``[{id, text, tier, score}]``."""

    EVENT_TYPE: str = field(default="memory", init=False, repr=False)
    entries: list[dict[str, Any]] = field(default_factory=list)
    notice: str = ""


@dataclass(frozen=True)
class SelfImproveEvent(StudioEvent):
    """``selfimprove`` — from ``assess`` / ``StallAssessment``."""

    EVENT_TYPE: str = field(default="selfimprove", init=False, repr=False)
    round: int = 0
    stalled: bool = False
    assessment: str = ""
    action: str = ""


@dataclass(frozen=True)
class EvolveEvent(StudioEvent):
    """``evolve`` — one distillation/optimization round."""

    EVENT_TYPE: str = field(default="evolve", init=False, repr=False)
    round: int = 0
    score: float = 0.0
    delta: float = 0.0
    variant: str = ""


@dataclass(frozen=True)
class GateEvent(StudioEvent):
    """``gate`` — security panel. From ``run_gate`` / ``Outcome``."""

    EVENT_TYPE: str = field(default="gate", init=False, repr=False)
    name: str = ""
    outcome: str = ""
    detail: str = ""
    sandboxed: bool = False


@dataclass(frozen=True)
class DagEvent(StudioEvent):
    """``dag`` — from ``GraphStore``. ``nodes`` = ``[{id, status}]``,
    ``edges`` = ``[[from, to], ...]``."""

    EVENT_TYPE: str = field(default="dag", init=False, repr=False)
    graph_id: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[list[str]] = field(default_factory=list)


@dataclass(frozen=True)
class LoopsEvent(StudioEvent):
    """``loops`` — loop-library catalog matches for a requirement.

    ``matches`` = ``[{id, title, summary, url, trigger, keywords, score}]``.
    Field names align to the REAL catalog.json (schemaVersion 2): a catalog loop
    has no ``id``/``summary``/``trigger`` field, so loops.py maps ``id←slug``,
    ``summary←description``, ``trigger←useWhen`` and adds ``keywords``/``score``.
    """

    EVENT_TYPE: str = field(default="loops", init=False, repr=False)
    matches: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class LoopSeedEvent(StudioEvent):
    """``loop_seed`` — the chosen loop's adapted seed steps.

    ``steps`` = ``[{id, description, depends_on, role}]`` — a linear DAG
    synthesized from the loop's flat ``steps`` list (mirrors planner._linear_steps).
    """

    EVENT_TYPE: str = field(default="loop_seed", init=False, repr=False)
    loop_id: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ToolCallEvent(StudioEvent):
    """``tool_call`` — an agent tool invocation (e.g. web_search)."""

    EVENT_TYPE: str = field(default="tool_call", init=False, repr=False)
    step_id: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultEvent(StudioEvent):
    """``tool_result`` — the result of an agent tool invocation."""

    EVENT_TYPE: str = field(default="tool_result", init=False, repr=False)
    step_id: str = ""
    tool: str = ""
    summary: str = ""
    n_results: int = 0
    notice: str = ""
    #: True when the jail refused the op (path escape) — drives the warning style.
    rejected: bool = False


@dataclass(frozen=True)
class VerifyEvent(StudioEvent):
    """``verify`` — from ``quality.verify``. ``findings`` =
    ``[{claim, supported, sources}]``; ``uncited`` = claim strings."""

    EVENT_TYPE: str = field(default="verify", init=False, repr=False)
    findings: list[dict[str, Any]] = field(default_factory=list)
    uncited: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LoopDoctorEvent(StudioEvent):
    """``loopdoctor`` — the run audited against loop-library's checklist (M8).

    Maps loop-library's four audit dimensions onto Studio's EXISTING primitives:
    ``bounded`` ⇆ ``FanoutBudget.ceiling``; ``material_checks`` ⇆ ``quality.verify``;
    ``safe_actions`` ⇆ the per-phase ``gates.run_gate`` outcomes; ``clear_stopping``
    ⇆ the plan being a finite DAG. ``checks`` = ``[{name, status, fix}]`` where
    ``status`` ∈ ``"pass"|"warn"|"fail"`` and ``fix`` is a SUGGESTION string (empty
    when ``pass``) — never auto-applied (matches loop-library's no-silent-change rule).
    """

    EVENT_TYPE: str = field(default="loopdoctor", init=False, repr=False)
    checks: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DoneEvent(StudioEvent):
    """``done`` — end state."""

    EVENT_TYPE: str = field(default="done", init=False, repr=False)
    total_tokens: int = 0
    input: int = 0
    output: int = 0
    estimated: bool = False
    wall_s: float = 0.0
    result: str = ""
    cancelled: bool = False
    #: Absolute path the final result was saved to (in the session workspace), or
    #: "" if there was nothing to save / the write failed.
    result_path: str = ""


@dataclass(frozen=True)
class ErrorEvent(StudioEvent):
    """``error`` — error toast."""

    EVENT_TYPE: str = field(default="error", init=False, repr=False)
    message: str = ""
    where: str = ""
