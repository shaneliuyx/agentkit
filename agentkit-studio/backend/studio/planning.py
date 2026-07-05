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
from agentkit.topology.core import (
    DURABLE_BOARD,
    GATEWAY,
    MAP,
    MESH,
    PIPELINE,
    SINGLE,
    STAR,
    TREE,
    TopologyChoice,
)

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
    _VALID_TOPO = {SINGLE, PIPELINE, STAR, MAP, MESH}
    steps = tuple(
        PlanStep(
            id=str(e["id"]),
            description=str(e.get("description") or e.get("title") or e["id"]),
            # keep only deps that resolve to a sibling epic (no self/dangling deps)
            depends_on=tuple(
                str(d) for d in e.get("depends_on", ())
                if str(d) in epic_ids and str(d) != str(e["id"])
            ),
            # E4: honor the planner's per-epic TOPOLOGY INTENT when it names a valid
            # topology; else leave it None so the task-driven selector
            # (assign_topologies_with_choices) fills it. The runner reconciles an explicit
            # intent against the selector's verdict.
            topology=(
                str(e.get("topology")).strip().lower()
                if str(e.get("topology")).strip().lower() in _VALID_TOPO
                else None
            ),
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


_LLM_TOPOLOGIES = {SINGLE, STAR, MAP, MESH, PIPELINE, GATEWAY, DURABLE_BOARD, TREE}


def _parse_topology_choice(text: str) -> dict[str, Any]:
    """Parse the LLM topology selector's first JSON object."""
    dec = _json.JSONDecoder()
    for i, ch in enumerate(text or ""):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(text[i:])
        except _json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def _build_topology_choice_prompt(step: PlanStep) -> str:
    intent = (step.topology or "").strip().lower()
    intent_line = f"Planner topology intent: {intent}\n" if intent else ""
    return (
        "You select the coordination topology for one agent phase.\n"
        "Choose the topology yourself. Use the principles below as guidance, not as "
        "a hardcoded checklist.\n\n"
        "Topology options:\n"
        "- single: one agent can do the work; task is small, fuzzy, or strongly ordered.\n"
        "- star: independent subtasks can run in parallel, then a reducer combines them.\n"
        "- map: repeat the same operation over each item in an upstream list.\n"
        "- mesh: peers should challenge, critique, compare, debate, or reconcile alternatives.\n"
        "- pipeline: ordered stages where each stage consumes the previous stage's output.\n"
        "- gateway: route among different entry points, identities, tools, or permissions before work starts.\n"
        "- durable_board: work needs restart recovery, cross-session state, queueing, or human-in-loop durability.\n"
        "- tree: hierarchical manager-to-leaf decomposition is needed.\n\n"
        "Decision principles from the Studio design:\n"
        "1. Prefer single until there is a real coordination reason.\n"
        "2. Routing/permissions are gateway-level concerns before worker topology.\n"
        "3. Restart, cross-session, queue, or human-in-loop needs durable_board.\n"
        "4. Debate/critique/comparison needs mesh.\n"
        "5. Independent parallel work needs star; per-item repeated work needs map.\n"
        "6. Ordered stage-by-stage work needs pipeline.\n"
        "7. Hierarchical decomposition with manager/leaves needs tree.\n\n"
        "Return ONLY JSON with this shape:\n"
        '{"topology":"star","rationale":"why this topology fits this phase",'
        '"questions_fired":["independent-parallel"]}\n\n'
        f"{intent_line}"
        f"Phase id: {step.id}\n"
        f"Phase description:\n{step.description}\n"
    )


def select_topologies_by_llm(plan_obj: Plan, client) -> tuple[Plan, dict[str, TopologyChoice]]:
    """Ask the LLM to choose topology+rationale per phase.

    Code only validates and falls back on unusable output; the selection rationale
    comes from the model-facing design principles.
    """
    new_steps: list[PlanStep] = []
    choices: dict[str, TopologyChoice] = {}
    for step in plan_obj.steps:
        prompt = _build_topology_choice_prompt(step)
        try:
            resp = client.chat([{"role": "user", "content": prompt}])
            data = _parse_topology_choice(getattr(resp, "text", "") or "")
        except Exception:  # noqa: BLE001
            data = {}
        raw_top = str(data.get("topology") or "").strip().lower()
        if raw_top not in _LLM_TOPOLOGIES:
            raw_top = (step.topology or SINGLE)
        rationale = str(data.get("rationale") or "LLM topology selection").strip()
        questions = data.get("questions_fired") or ()
        if not isinstance(questions, (list, tuple)):
            questions = ()
        choice = TopologyChoice(
            topology=raw_top,
            trigger="llm",
            concurrency=1,
            rationale=rationale,
            questions_fired=tuple(str(q) for q in questions),
        )
        choices[step.id] = choice
        new_steps.append(replace(step, topology=raw_top))
    return replace(plan_obj, steps=tuple(new_steps)), choices


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
    # Two shapes (G4): legacy ``{agent: [sections]}`` and the new
    # ``{agent: {"sections": [...], "create": [...]}}`` where ``create`` names sections the
    # agent must CREATE (not yet in the doc). Both flatten to the agent's full section list;
    # a create job is just a section the reducer's coverage check (G3) verifies got populated.
    out: dict[str, list[str]] = {}
    for agent, val in data.items():
        if isinstance(val, list):
            out[str(agent)] = [str(s) for s in val]
        elif isinstance(val, dict):
            secs = [str(s) for s in val.get("sections", []) if isinstance(val.get("sections"), list)]
            crea = [str(s) for s in val.get("create", []) if isinstance(val.get("create"), list)]
            out[str(agent)] = secs + crea
    return out


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


def verify_assignment_coverage(
    assigned: dict[str, list[str]], doc_after: str
) -> list[str]:
    """G3: which hub-assigned sections were NOT addressed in the assembled doc.

    Closes the loop the user asked for: the hub assigns bounded jobs BY SECTION; after the
    reducer assembles, deterministically verify each assigned section is actually present and
    populated. A section that is ABSENT or still an empty placeholder was not delivered — it
    becomes a next-epoch weakness (returned as ``[<section>] …`` strings, the same shape the
    miner emits, so the existing handoff routes it back to the owning section's agent).

    Concept-matched to the assembled headings (``_content_tokens``) so a slightly-renamed
    section still resolves. Returns [] on a clean, fully-covered assignment."""
    sections: list[str] = []
    for _agent, secs in (assigned or {}).items():
        for s in secs:
            if s and s not in sections:
                sections.append(s)
    if not sections:
        return []
    from agentkit.artifacts.sections import split_sections
    from studio.rubric import _content_tokens, mask_fenced_code
    after = split_sections(mask_fenced_code(doc_after or ""))

    def _find(name: str) -> tuple[str, str] | None:
        want = _content_tokens(name)
        for h, b in after:
            if name.lower() in h.lower() or (want and want & _content_tokens(h)):
                return h, b
        return None

    unmet: list[str] = []
    for name in sections:
        hit = _find(name)
        if hit is None:
            unmet.append(f"[{name}] assigned this phase but absent from the assembled document")
            continue
        _h, body = hit
        bl = (body or "").strip().lower()
        if not bl or "_(to be completed)_" in bl or "_(pending" in bl:
            unmet.append(f"[{name}] assigned this phase but still empty/placeholder (not addressed)")
    return unmet


def build_section_worker_foci(
    sections: list[str] | tuple[str, ...],
    weaknesses: list[str] | tuple[str, ...],
    *,
    max_sections_per_agent: int = 1,
    section_files: dict[str, str] | None = None,
    scoring_matrix: list[dict[str, object]] | tuple[dict[str, object], ...] | None = None,
) -> tuple[str, ...]:
    """Build explicit section-scoped worker assignments for fan-out phases.

    Each focus is a small contract: these exact sections are the worker's scope,
    relevant weaknesses are guidance, and absent/pending sections are create jobs.
    """
    clean_sections = [_normalize_section_heading(s) for s in sections if str(s).strip()]
    if not clean_sections:
        return ()
    group_size = max(1, int(max_sections_per_agent or 1))
    groups = [
        clean_sections[i:i + group_size]
        for i in range(0, len(clean_sections), group_size)
    ]
    return tuple(_section_focus_text(g, weaknesses, section_files or {}, scoring_matrix=scoring_matrix) for g in groups)


def build_section_assignment_queue(
    sections: list[str] | tuple[str, ...],
    weaknesses: list[str] | tuple[str, ...],
    *,
    section_files: dict[str, str] | None = None,
    agent_slots: int | None = None,
    scoring_matrix: list[dict[str, object]] | tuple[dict[str, object], ...] | None = None,
) -> tuple[str, ...]:
    """Build one-file worker foci for the section assignment queue."""
    rows = build_section_assignment_rows(
        sections,
        weaknesses,
        section_files=section_files,
        agent_slots=agent_slots,
        scoring_matrix=scoring_matrix,
    )
    return tuple(row["assignment"] for row in rows)


def build_section_assignment_rows(
    sections: list[str] | tuple[str, ...],
    weaknesses: list[str] | tuple[str, ...] = (),
    *,
    section_files: dict[str, str] | None = None,
    agent_slots: int | None = None,
    scoring_matrix: list[dict[str, object]] | tuple[dict[str, object], ...] | None = None,
) -> tuple[dict[str, str], ...]:
    """Build atomic queue rows: one agent slot, one section, one file."""
    clean_sections = [_normalize_section_heading(s) for s in sections if str(s).strip()]
    if not clean_sections:
        return ()
    slots = max(1, int(agent_slots or len(clean_sections)))
    files = section_files or {}
    rows: list[dict[str, str]] = []
    for i, section in enumerate(clean_sections):
        agent_id = f"agent-{(i % slots) + 1:03d}"
        rows.append(
            {
                "agent_id": agent_id,
                "section": section,
                "file": files.get(section.removeprefix("## ").strip(), "(section file pending)"),
                "status": "queued",
                "assignment": _section_focus_text(
                    [section],
                    weaknesses,
                    files,
                    agent_id=agent_id,
                    scoring_matrix=scoring_matrix,
                ),
            }
        )
    return tuple(rows)


def _normalize_section_heading(section: str) -> str:
    sec = str(section).strip()
    if sec.startswith("#"):
        return sec
    return f"## {sec}"


def _section_focus_text(
    sections: list[str],
    weaknesses: list[str] | tuple[str, ...],
    section_files: dict[str, str],
    agent_id: str | None = None,
    scoring_matrix: list[dict[str, object]] | tuple[dict[str, object], ...] | None = None,
) -> str:
    from studio.rubric import _content_tokens
    from studio.rubric import format_scoring_rules

    owned_tokens = set()
    for sec in sections:
        owned_tokens |= _content_tokens(sec)
    relevant: list[str] = []
    for weakness in weaknesses or ():
        w = str(weakness).strip()
        if not w:
            continue
        wl = w.lower()
        if "[document]" in wl or (owned_tokens and owned_tokens & _content_tokens(w)):
            relevant.append(w)
    section_lines = "\n".join(f"- {s}" for s in sections)
    file_lines = "\n".join(
        f"- {s} -> {section_files.get(s.removeprefix('## ').strip(), '(section file pending)')}"
        for s in sections
    )
    weakness_lines = "\n".join(f"- {w}" for w in relevant) if relevant else "- (none)"
    scoring_lines = format_scoring_rules(scoring_matrix, sections=sections)
    return (
        (f"AGENT ID: {agent_id}\n\n" if agent_id else "")
        + "ASSIGNMENT QUEUE FETCH:\n"
        "- This worker call may fetch exactly one queued section file in normal runtime.\n"
        "- Process the current assigned file target only, then return section-local findings/patches.\n\n"
        "ASSIGNED SECTIONS:\n"
        f"{section_lines}\n\n"
        "ASSIGNED SECTION FILES:\n"
        f"{file_lines}\n\n"
        "TASK GUIDANCE:\n"
        "- Work only on the assigned sections above.\n"
        "- If an assigned section is absent or still pending, create and populate it.\n"
        "- Keep each section's updates in its matching section file; do not combine multiple assigned sections into one file.\n"
        "- For RESEARCH_FINDING blocks, use PATCH_TARGET values that exactly match one assigned heading.\n"
        "- Do not patch or write content for sections assigned to other agents.\n\n"
        "WORKER OUTPUT CONTRACT:\n"
        "- Return only grounded RESEARCH_FINDING blocks or a PATCHES JSON block that the reducer can apply.\n"
        "- For RESEARCH_FINDING, include ARTICLE_TITLE, URL, PATCH_TARGET, QUOTE, and WHY.\n"
        "- URL must be the exact http(s) page fetched for the finding; never invent bibliography entries.\n"
        "- PATCH_TARGET must exactly match one assigned heading above.\n"
        "- For PATCHES, use exactly this reducer-applicable JSON shape:\n"
        "  PATCHES:\n"
        "  ```json\n"
        "  [{\"op\":\"insert_after\",\"anchor\":\"## Exact Assigned Heading\",\"content\":\"One short grounded sentence with the exact source URL.\"}]\n"
        "  ```\n"
        "- PATCHES objects use op/anchor/content only; do not use legacy PATCH_TARGET/CONTENT patch objects.\n"
        "- PATCHES content must include an exact http(s) source URL and must not contain markdown headings or full-section prose.\n"
        "- Do not return plain markdown section prose, report text, headings, or a References section; the reducer writes the report from grounded findings/patches.\n"
        "- If no real fetched source supports the assigned section, return no finding for it.\n\n"
        "SCORING REQUIREMENTS FOR THIS SCOPE:\n"
        f"{scoring_lines}\n\n"
        "WEAKNESSES TO FIX IN THIS SCOPE:\n"
        f"{weakness_lines}"
    )


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


# ---------------------------------------------------------------------------
# Workstream P — planner-reviewed requirement-driven dynamic sections.
# The template is only the STARTING outline; the planner reviews it against the
# requirement's deliverable-shaped asks and may add a section or a sub-section.
# Scoring stays frozen (rc["scoring_template"]/["scoring_matrix"] are never
# touched here) — additions land on rc["active_template"] only, the live-outline
# store that assignment/publish/export already read.
# ---------------------------------------------------------------------------

#: Form/structure deliverables a requirement can literally ask for. Deliberately
#: keyword-narrow (like _DIAGRAM_SHAPED_RE): a match means the user asked for a
#: CONTENT FORM the outline may need a home for — never a count/length
#: constraint ("under 800 words", "at least 3 sources").
_FORM_DELIVERABLE_RE = _re.compile(
    r"(?i)\b("
    r"example code|code example|sample code|design architecture|architecture design|"
    r"solution architecture|reference architecture|diagram|comparison table|"
    r"flow ?chart|checklist|timeline|roadmap|glossary|appendix|case stud(?:y|ies)|"
    r"benchmark"
    r")\b"
)

#: Hard cap on planner-injected top-level sections per run (outline-bloat guard).
_MAX_DYNAMIC_SECTIONS = 3

_VALID_SECTION_ACTIONS = ("existing", "new_section", "subsection")


def deliverable_candidates(requirement: str) -> list[str]:
    """Deterministic deliverable-shaped phrases literally present in *requirement*.

    No LLM: this is the fallback extractor AND the trigger gate — when it finds
    nothing, the planner review is skipped entirely (0 extra LLM calls)."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _FORM_DELIVERABLE_RE.finditer(requirement or ""):
        phrase = m.group(1).lower()
        if phrase not in seen:
            seen.add(phrase)
            out.append(phrase)
    return out


def _fallback_section_decisions(
    outline: list[str], candidates: list[str]
) -> list[dict[str, Any]]:
    """No-LLM decisions: concept-token match against the outline → existing,
    else a new top-level section titled from the deliverable phrase."""
    from studio.rubric import _content_tokens

    outline_tokens = [(_content_tokens(t), t) for t in outline]
    decisions: list[dict[str, Any]] = []
    for cand in candidates:
        toks = _content_tokens(cand)
        hit = next((t for o_toks, t in outline_tokens if toks & o_toks), None)
        if hit is not None:
            decisions.append(
                {"deliverable": cand, "action": "existing", "title": hit, "parent": None}
            )
        else:
            decisions.append(
                {"deliverable": cand, "action": "new_section",
                 "title": cand.title(), "parent": None}
            )
    return decisions


def _section_review_prompt(
    outline: list[str], requirement: str, candidates: list[str],
    weaknesses: list[str] | tuple[str, ...],
) -> str:
    outline_block = "\n".join(f"- {t}" for t in outline) or "- (empty)"
    weak_block = "\n".join(f"- {w}" for w in list(weaknesses)[:6])
    return (
        "You review a report OUTLINE against the deliverables the task explicitly "
        "asks for, and decide where each deliverable should live.\n\n"
        f"TASK:\n{(requirement or '').strip()[:2000]}\n\n"
        f"CURRENT OUTLINE (top-level sections):\n{outline_block}\n\n"
        + (f"KNOWN WEAKNESSES:\n{weak_block}\n\n" if weak_block else "")
        + "DELIVERABLES THE TASK ASKS FOR:\n"
        + "\n".join(f"- {c}" for c in candidates)
        + "\n\nFor EACH deliverable decide ONE of:\n"
        '- "existing": an existing outline section already is the natural home '
        '(set "title" to that EXACT section title)\n'
        '- "new_section": the outline needs a NEW top-level section (set "title" '
        "to a short task-specific section title)\n"
        '- "subsection": a sub-section under an existing section is enough (set '
        '"title" to the sub-section title and "parent" to the EXACT existing '
        "section title)\n\n"
        "Reply with ONLY a JSON array, one object per deliverable:\n"
        '[{"deliverable": "...", "action": "existing|new_section|subsection", '
        '"title": "...", "parent": null or "..."}]'
    )


def requirement_section_decisions(
    client: Any,
    outline: list[str] | tuple[str, ...],
    requirement: str,
    weaknesses: list[str] | tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Planner review (Workstream P): where should each deliverable live?

    ONE LLM call on the planner/judge client; every failure path (no client, LLM
    error, unparseable/invalid JSON) falls back to the deterministic
    concept-token decisions. Returns [] when the requirement asks for no
    form-shaped deliverable — the common case, costing 0 LLM calls."""
    outline_list = [str(t).strip() for t in outline if str(t).strip()]
    candidates = deliverable_candidates(requirement)
    if not candidates:
        return []
    if client is None:
        return _fallback_section_decisions(outline_list, candidates)
    try:
        reply = client.chat([{
            "role": "user",
            "content": _section_review_prompt(outline_list, requirement, candidates, weaknesses),
        }])
        text = str(getattr(reply, "text", "") or "")
        m = _re.search(r"\[.*\]", text, _re.DOTALL)
        raw = _json.loads(m.group(0)) if m else None
        decisions: list[dict[str, Any]] = []
        for item in raw or []:
            action = str(item.get("action") or "").strip().lower()
            title = str(item.get("title") or "").strip().lstrip("#").strip()
            parent = item.get("parent")
            if action not in _VALID_SECTION_ACTIONS or not title:
                raise ValueError(f"invalid decision: {item!r}")
            if action == "subsection" and not str(parent or "").strip():
                raise ValueError(f"subsection without parent: {item!r}")
            decisions.append({
                "deliverable": str(item.get("deliverable") or title),
                "action": action,
                "title": title,
                "parent": str(parent).strip() if parent else None,
            })
        if not decisions:
            raise ValueError("empty decision list")
        return decisions
    except Exception:  # noqa: BLE001 — review is an optimization, never a blocker
        return _fallback_section_decisions(outline_list, candidates)


def apply_section_decisions(
    rubric_config: dict[str, Any],
    decisions: list[dict[str, Any]],
    *,
    max_new: int = _MAX_DYNAMIC_SECTIONS,
) -> tuple[list[str], dict[str, list[str]]]:
    """Apply planner decisions to the LIVE outline; never touch frozen scoring.

    Grows ``rubric_config["active_template"]`` with accepted new sections
    (additive-only, capped, concept-deduped against the current outline) and
    returns ``(added_titles, subsections)`` where *subsections* maps a parent
    section title to its ``###`` placeholder titles (consumed by the cold-start
    skeleton builder; seeded runs get no subsection injection in v1).
    ``scoring_template`` / ``scoring_matrix`` / ``template`` are read-only here —
    the no-moving-target contract."""
    from studio.rubric import _content_tokens

    outline = [
        str(t) for t in (
            rubric_config.get("active_template") or rubric_config.get("template") or []
        )
    ]
    outline_tokens = [_content_tokens(t) for t in outline]
    added: list[str] = []
    subsections: dict[str, list[str]] = {}
    for d in decisions:
        action = d.get("action")
        title = str(d.get("title") or "").strip()
        if not title:
            continue
        if action == "new_section" and len(added) < max_new:
            toks = _content_tokens(title)
            if any(toks & o for o in outline_tokens):
                continue  # concept already present — planner said new, dedupe says no
            outline.append(title)
            outline_tokens.append(toks)
            added.append(title)
        elif action == "subsection":
            parent = str(d.get("parent") or "").strip()
            if parent:
                subsections.setdefault(parent, []).append(title)
    if added:
        rubric_config["active_template"] = outline
    return added, subsections
