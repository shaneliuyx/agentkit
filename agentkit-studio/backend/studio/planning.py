"""studio.planning — epic/plan parsing, plan dedup, topology expansion, the
render graph, hub-assignment parsing, and per-run epoch status.

Extracted from ``studio.runner`` (SRP): these are stateless plan/graph/topology
transforms plus the :class:`EpochResult` DTO. ``studio.runner`` re-exports every
name here so existing ``from studio.runner import _plan_from_epics`` (etc.)
imports keep working.
"""

from __future__ import annotations

import json as _json
import re as _re
from dataclasses import dataclass, replace
from typing import Any

from agentkit.planner.core import Plan, PlanStep, plan
from agentkit.topology.core import MAP, MESH, PIPELINE, SINGLE, STAR

from studio.events import GraphEvent
from studio.findings import _parse_patches_from_output
from studio.prompts import _build_planner_cot_prompt


def _parse_epic_plan(text: str) -> list[dict]:
    """Extract epics list from an EPIC_PLAN JSON block in LLM planner output.

    Returns empty list on missing block or invalid JSON so the caller can
    fall through to the standard agentkit plan() path.
    """
    m = _re.search(r"EPIC_PLAN:\s*```json\s*(\{.*?\})\s*```", text, _re.DOTALL)
    if not m:
        # Unfenced: LLMs may emit compact single-line JSON; greedy without DOTALL
        # so `.` stops at newline boundaries and captures the full line-level object.
        m = _re.search(r"EPIC_PLAN:\s*(\{.*\})", text)
    if not m:
        return []
    try:
        data = _json.loads(m.group(1))
        return data.get("epics", [])
    except (ValueError, KeyError):
        return []


def _plan_from_epics(
    requirement: str,
    client,
    weaknesses_block: str = "",
    artifact_summary: str = "",
) -> Plan:
    """Epic-based planner (DESIGN §2.3) — replaces the flat cold decomposer.

    The planner LLM is prompted with the CoT planner prompt and must emit an
    EPIC_PLAN JSON block. Each epic becomes one phase (`PlanStep` with STAR
    fan-out); `depends_on` sequences the phases. Falls back to the deterministic
    `plan()` only when the LLM returns no parseable epics, so a malformed plan
    never breaks a run.
    """
    prompt = _build_planner_cot_prompt(
        goal=requirement,
        artifact_path="artifact.md",
        artifact_summary=artifact_summary,
        weaknesses_block=weaknesses_block,
    )
    try:
        resp = client.chat([{"role": "user", "content": prompt}])
        epics = _parse_epic_plan(getattr(resp, "text", "") or "")
    except Exception:  # noqa: BLE001 — any planner failure → deterministic fallback
        epics = []
    if not epics:
        return plan(requirement)

    epic_ids = {str(e.get("id")) for e in epics if e.get("id")}
    steps = tuple(
        PlanStep(
            id=str(e["id"]),
            description=str(e.get("description") or e.get("title") or e["id"]),
            # keep only deps that resolve to a sibling epic (no self/dangling deps)
            depends_on=tuple(
                str(d) for d in e.get("depends_on", ())
                if str(d) in epic_ids and str(d) != str(e["id"])
            ),
            topology=STAR,
        )
        for e in epics
        if e.get("id")
    )
    if not steps:
        return plan(requirement)
    try:
        return Plan(task=requirement, steps=steps)
    except Exception:  # noqa: BLE001 — bad DAG → deterministic fallback
        return plan(requirement)


def _dedupe_plan_steps(plan_obj: Plan) -> Plan:
    """Collapse phases with an identical normalized description — PATH-AGNOSTIC.

    Applied to the FINAL plan, after the seeded / LLM-epic / deterministic planner has
    run, because any of them can emit the same phase twice (the Pi/Craft run showed
    "Craft agent…" and "create a research report" duplicated under the seeded planner —
    `_plan_from_epics`'s id-dedup never saw it). Keeps the first step per normalized
    description and remaps a dropped duplicate's id into its `depends_on` users so the DAG
    stays connected. No-op (returns the original) when there are no duplicates.
    """
    seen: dict[str, str] = {}      # normalized description → kept step id
    remap: dict[str, str] = {}     # dropped duplicate id → kept id
    kept: list = []
    for s in plan_obj.steps:
        key = " ".join((s.description or "").lower().split())
        if key and key in seen:
            remap[s.id] = seen[key]
            continue
        if key:
            seen[key] = s.id
        kept.append(s)
    if not remap:
        return plan_obj
    kept_ids = {s.id for s in kept}
    new_steps = tuple(
        replace(
            s,
            depends_on=tuple(
                dict.fromkeys(
                    remap.get(d, d) for d in s.depends_on
                    if remap.get(d, d) in kept_ids and remap.get(d, d) != s.id
                )
            ),
        )
        for s in kept
    )
    try:
        return replace(plan_obj, steps=new_steps)
    except Exception:  # noqa: BLE001 — bad DAG → leave the plan as-is
        return plan_obj


def _epoch_status(
    epoch_idx: int, delta: float, min_delta: float, max_epochs: int
) -> str:
    """Displayed hill-climb status from the PER-RUN epoch index (PLAN item 8).

    Both labels are per-run, never cumulative:
      * "converged" — THIS run's in-process epoch loop reached its budget
        (``epoch_idx >= max_epochs``). Keying it on the cumulative all-time ``version``
        (``next_version`` = MAX(version)+1 over every prior run) showed "converged" on the
        FIRST epoch of any task with history.
      * "plateau" — only from the run's SECOND epoch onward (``epoch_idx > 1``); the first
        epoch has no prior epoch in this run, so its delta against unrelated seeded history is
        meaningless and must not stop the loop (this is also the run()-loop break signal).

    ``epoch_idx == 0`` is a single manual pass (no epoch loop) → always "improving".
    """
    if epoch_idx and epoch_idx >= max_epochs:
        return "converged"
    if epoch_idx > 1 and delta < min_delta:
        return "plateau"
    return "improving"


def _parse_assigned(text: str) -> dict[str, list[str]]:
    """Extract the hub's ASSIGNED block: agent → [section/branch ids] (DESIGN §3.3).

    Returns {} when no parseable block is present (the validation then no-ops).
    """
    m = _re.search(r'ASSIGNED:\s*```json\s*(\{.*?\})\s*```', text, _re.DOTALL)
    if not m:
        m = _re.search(r'ASSIGNED:\s*(\{.*?\})', text, _re.DOTALL)
    if not m:
        return {}
    try:
        data = _json.loads(m.group(1))
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(agent): [str(s) for s in sections]
        for agent, sections in data.items()
        if isinstance(sections, list)
    }


def _dedupe_assignment(
    assigned: dict[str, list[str]],
) -> tuple[dict[str, list[str]], list[str]]:
    """Deterministically resolve overlapping section assignments (R2 enforcement).

    The hub CoT prompt mandates non-overlapping, one-section-per-agent assignment,
    but that is LLM-enforced. This validates the emitted ASSIGNED block in code:
    the FIRST agent (in assignment order) to claim a section keeps it; any later
    agent claiming the same section loses the duplicate. Returns
    ``(clean_assignment, overlapping_ids)`` — overlapping_ids is empty on a clean
    partition. Within-agent repeats are also collapsed.
    """
    seen: set[str] = set()
    clean: dict[str, list[str]] = {}
    overlaps: list[str] = []
    for agent, sections in assigned.items():
        kept: list[str] = []
        for s in sections:
            if s in seen:
                if s not in overlaps:
                    overlaps.append(s)
                continue  # claimed by an earlier agent (or earlier in this list)
            seen.add(s)
            kept.append(s)
        clean[agent] = kept
    return clean, overlaps


def _phase_search_failed(outputs: list[str]) -> bool:
    """True iff every worker reported SEARCH: error and none produced findings (§14.2).

    A phase where the search tool itself failed for ALL workers must HALT with a
    visible notice — not silently no-op (which would look like "doc is already
    perfect") and not write failure-narration. Returns False if any worker found
    content (RESEARCH_FINDING / PATCHES) or reported SEARCH: ok, or if there are
    no worker outputs to judge.
    """
    if not outputs:
        return False
    saw_error = False
    for o in outputs:
        low = o.lower()
        # Real work: a RESEARCH_FINDING block, a NON-empty PATCHES array, or an
        # explicit SEARCH: ok. (An empty `PATCHES: []` is no work, not evidence.)
        if ("research_finding" in low
                or _re.search(r"search:\s*ok", low)
                or _parse_patches_from_output(o)):
            return False
        if _re.search(r"search:\s*error", low):
            saw_error = True
    return saw_error


def _render_graph(plan_obj: Plan) -> GraphEvent:
    """Derive the render graph (SPEC §6): a phase node per step, expanded into
    intra-phase agent nodes per topology, plus inter-phase ``depends_on`` edges.

    Node kinds: ``phase`` (the step) + ``agent``/``hub``/``reduce``/``stage`` for
    the topology expansion. The runtime ``n_agents`` (from ``phase_done``)
    reconciles spoke counts later on the frontend.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    peers = 3  # default fan-out breadth (mirrors dynamic._DEFAULT_PEERS)

    for step in plan_obj.steps:
        phase_id = step.id
        nodes.append(
            {
                "id": phase_id,
                "kind": "phase",
                "phase": phase_id,
                "label": step.description[:80],
                "state": "pending",
            }
        )
        topo = step.topology or SINGLE
        _expand_topology(nodes, edges, phase_id, topo, peers)
        for dep in step.depends_on:
            edges.append({"from": dep, "to": phase_id, "kind": "depends"})

    return GraphEvent(nodes=nodes, edges=edges)


def _expand_topology(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    phase_id: str,
    topo: str,
    peers: int,
) -> None:
    """Append intra-phase agent nodes/edges for one phase's topology."""

    def agent(idx: int, kind: str = "agent") -> str:
        nid = f"{phase_id}:{kind}{idx}"
        nodes.append(
            {"id": nid, "kind": kind, "phase": phase_id, "label": kind, "state": "pending"}
        )
        return nid

    if topo == SINGLE:
        a = agent(0)
        edges.append({"from": phase_id, "to": a, "kind": "intra"})
    elif topo == STAR:
        spokes = [agent(i) for i in range(peers)]
        reduce_id = agent(0, "reduce")
        for s in spokes:
            edges.append({"from": phase_id, "to": s, "kind": "intra"})
            edges.append({"from": s, "to": reduce_id, "kind": "reduce"})
    elif topo == MESH:
        ps = [agent(i) for i in range(peers)]
        for i, a in enumerate(ps):
            for b in ps[i + 1 :]:
                edges.append({"from": a, "to": b, "kind": "mesh"})
        reduce_id = agent(0, "reduce")
        for a in ps:
            edges.append({"from": a, "to": reduce_id, "kind": "reduce"})
    elif topo == MAP:
        # MAP fan-out: N workers (one per upstream item), then reduce.
        # peers is a best-effort count — actual count depends on upstream list.
        workers = [agent(i) for i in range(peers)]
        reduce_id = agent(0, "reduce")
        for w in workers:
            edges.append({"from": phase_id, "to": w, "kind": "intra"})
            edges.append({"from": w, "to": reduce_id, "kind": "reduce"})
    elif topo == PIPELINE:
        stages = [agent(i, "stage") for i in range(3)]  # mirrors _PIPELINE_STAGES
        edges.append({"from": phase_id, "to": stages[0], "kind": "intra"})
        for a, b in zip(stages, stages[1:]):
            edges.append({"from": a, "to": b, "kind": "pipeline"})


def _with_upstream(description: str, upstream: str) -> str:
    """Fold upstream outputs into a step description — byte-identical to
    ``agentkit.topology.dynamic._with_upstream`` so the Studio-driven single-step
    sub-plan produces the same prompts a full ``run_plan`` would."""
    if upstream:
        return f"{description}\n\nContext from prior steps:\n{upstream}"
    return description


@dataclass(frozen=True)
class EpochResult:
    """Per-epoch outcome returned by :meth:`Runner._run_inner` (DESIGN §14.4).

    ``run()`` consumes it to decide whether the epoch loop continues. ``status``
    is one of ``improving`` / ``plateau`` / ``converged`` — the same value the
    pass's :class:`HillClimbEvent` carries for the frontend timeline.
    """

    version: int
    score: float
    delta: float
    status: str
