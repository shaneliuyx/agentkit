"""studio.runner — the step-loop driver that emits the SSE sequence (SPEC §5.2).

``agentkit.topology.dynamic.run_plan`` is synchronous and emits nothing mid-run,
so Studio drives the phase loop itself and delegates each step to ``run_plan``
on a SINGLE-STEP sub-plan, folding upstream outputs into the description exactly
as agentkit's own ``_with_upstream`` does. The real STAR/MESH/PIPELINE fan-out
happens inside that per-step ``run_plan`` call; Studio adds observability around
it.

Event ordering (SPEC §4):
  session → plan → topology → graph → (per phase: phase_start, [router],
  [memory], [token…], [agent_event], phase_done, [dag], [selfimprove],
  [evolve], [gate]) → budget → verify → done

Token frames fire *during* a phase via the ``on_usage`` callback closed over the
current step (StudioChatClient calls it per LLM call). The runner runs in a
worker thread; events cross to the SSE generator through a queue (app.py owns the
asyncio bridge). Here the runner just calls an injected ``emit(event)`` sink.
"""

from __future__ import annotations

import json as _json
import re as _re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from agentkit.orchestrator.fanout import BudgetExceeded, FanoutBudget
from agentkit.planner.core import Plan, PlanStep, plan
from agentkit.topology.core import MAP, MESH, PIPELINE, SINGLE, STAR
from agentkit.topology.dynamic import assign_topologies, run_plan
from agentkit.types import LLMClient

from studio.backends import build_chat_client, build_embedder, resolve_backend
from studio.events import (
    BudgetEvent,
    DoneEvent,
    ErrorEvent,
    GateEvent,
    GoalMetEvent,
    GraphEvent,
    HillClimbEvent,
    LoopSeedEvent,
    PhaseDoneEvent,
    PhaseStartEvent,
    PlanEvent,
    SessionEvent,
    StudioEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    TopologyEvent,
)
from studio.loops import make_seeded_decomposer
from studio.panels.dag import DagTracker
from studio.panels.evolve import build_evolve_event
from studio.panels.loopdoctor import build_loopdoctor_event
from studio.panels.memory import MemoryTracker
from studio.panels.router import build_router_event
from studio.panels.security import run_gate_event
from studio.panels.selfimprove import SelfImproveTracker
from studio.panels.verify import build_verify_event
from studio.session import RunSnapshot, Session
from studio.shared_bridge import TokenAccounting, UsageReport
from studio.tools import ToolAugmentedClient, web_toolkit_available
from studio.workspace import Workspace

# ---------------------------------------------------------------------------
# M8 / M9 — Epic plan parsing and CoT prompt builders (DESIGN §3, §5)
# ---------------------------------------------------------------------------

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


def _build_planner_cot_prompt(
    goal: str,
    artifact_path: str,
    artifact_summary: str,
    weaknesses_block: str,
) -> str:
    """Strategic planner CoT prompt (DESIGN §3 — Planner CoT Prompt).

    Returned string is passed as the sole user message to the LLM; the model
    must emit an EPIC_PLAN JSON block that _parse_epic_plan() can extract.
    """
    return (
        "You are the strategic planner for a multi-phase agent system.\n"
        "The goal can be any type of task — research, writing, analysis, design,\n"
        "code generation, data processing, or a mix. Do not assume a specific domain.\n"
        "Think step by step.\n\n"
        f"GOAL: {goal}\n"
        f"DELIVERABLE PATH: {artifact_path}\n"
        f"EXISTING DELIVERABLE: {artifact_summary or 'none'}\n"
        f"ACCUMULATED WEAKNESSES:\n{weaknesses_block or '(none)'}\n\n"
        "Step 1 — Understand the goal and the form of its deliverable.\n\n"
        "Step 2 — Identify 2–5 major work phases (epics).\n"
        "  Do not default to Research→Analysis→Writing unless those phases\n"
        "  genuinely fit. Derive phase names from the goal itself.\n\n"
        "Step 3 — For each epic enumerate 6–15 parallel branches.\n"
        "  Branches within an epic run in parallel — no inter-branch dependencies.\n"
        "  Each branch must be completable by one agent with available tools.\n\n"
        "Step 4 — Account for existing deliverable and weaknesses.\n"
        "  If a deliverable exists: branches must address gaps only, not reconstruct.\n"
        "  If no deliverable: Epic 1 branches should establish the initial structure.\n\n"
        "Step 5 — Emit the plan:\n\n"
        "EPIC_PLAN:\n"
        "```json\n"
        '{"epics": [{"id": "epic-1", "title": "...", "description": "...",\n'
        '  "depends_on": [], "branches": [{"id": "b-1a", "description": "..."}]}]}\n'
        "```\n"
    )


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


def _today_note() -> str:
    """Current-date context line for agent prompts (DESIGN §11.4).

    Agents are otherwise date-blind and wrongly flag current-year sources as
    'future-dated' credibility problems. A tool would force a round-trip per
    agent for a value constant across the run; injecting it is cheaper and
    guaranteed-seen.
    """
    import datetime
    today = datetime.date.today()
    return (
        f"Today's date is {today.isoformat()}. Treat any date on or before today "
        f"as current or past — do NOT flag dates in {today.year} or earlier as "
        "'future-dated' or a credibility concern.\n\n"
    )


def _build_hub_cot_prompt(
    goal: str,
    artifact_path: str,
    ledger_block: str,
    weaknesses_block: str,
    artifact_text: str,
    max_tasks_per_agent: int,
) -> str:
    """Hub planning CoT prompt for one epic phase (DESIGN §5.1 / §5.2).

    Injected as the step description so the hub LLM sees it as its task.
    """
    if artifact_text:
        step1 = (
            "Step 1 — Read the existing deliverable structure.\n"
            "  Identify sections, coverage depth, and citation quality.\n"
            "  List what is present and what is thin or missing.\n"
        )
    else:
        step1 = (
            "Step 1 — No existing deliverable found.\n"
            "  Define the document structure: sections, purpose, and\n"
            "  information needed to populate each section.\n"
            f"  Deliverable will be created at: {artifact_path}\n"
        )
    return (
        _today_note() +
        "You are the planning hub for a multi-phase agent system.\n"
        "Think through each step carefully before acting.\n\n"
        "CONTEXT:\n"
        f"  Goal: {goal}\n"
        f"  Deliverable: {artifact_path}\n"
        f"  {ledger_block}\n"
        f"  Accumulated weaknesses:\n{weaknesses_block or '(none)'}\n\n"
        f"{step1}\n"
        "Step 2 — Compare against the goal. State gaps specifically.\n\n"
        "Step 3 — Generalize weaknesses into universal requirements.\n\n"
        "Step 4 — Define this phase's work items (additive/corrective only;\n"
        "  no items from COMPLETED TASKS).\n\n"
        "Step 5 — Assign work items to agents BY DOCUMENT SECTION.\n"
        "  Rules:\n"
        "    - Each agent owns a non-overlapping set of sections (e.g. \"## Results\",\n"
        "      \"## Analysis\"). Assign by section heading, NOT by topic —\n"
        "      \"improve Section X\" not \"cover Topic Y\". Section-scoped assignment\n"
        "      guarantees non-overlapping anchors so worker PATCHES commute.\n"
        f"    - Max {max_tasks_per_agent} sections per agent; last agent may receive fewer.\n"
        "    - No section assigned to more than one agent.\n"
        "    - Tell each agent its exact section headings (verbatim from the document)\n"
        "      so its PATCHES anchors are unambiguous.\n"
        "  Emit TASK_LIST, ASSIGNED, and DONE blocks (JSON).\n\n"
        f"Step 6 — Emit DELIVERABLE_PATH: {artifact_path}\n"
        "  Workers write PATCHES blocks targeting only their assigned sections —\n"
        "  no direct file writes.\n"
    )


def _build_worker_cot_prompt(
    task_list_for_agent: str,
    artifact_current_text: str,
) -> str:
    """Worker (stateless suggester) CoT prompt (DESIGN §5.3).

    Workers emit PATCHES suggestions only — they never write to disk. The anchor
    rule is the crux: copy the assigned section heading VERBATIM from the current
    deliverable so the Reducer can locate it unambiguously.
    """
    return (
        _today_note() +
        "You are a worker agent. You will suggest changes to a shared document.\n"
        "Do NOT write to any file — emit patch suggestions only. Think step by step.\n\n"
        f"TASK ASSIGNMENTS:\n{task_list_for_agent}\n\n"
        f"CURRENT DELIVERABLE CONTENT:\n{artifact_current_text}\n\n"
        "Step 1 — For each assigned task, state what you need to find or verify.\n\n"
        "Step 2 — Execute: use web_search and web_fetch to gather evidence.\n"
        "  For each source: note the URL, title, and key facts extracted.\n\n"
        "Step 3 — Assess completeness. One more search if any task is thin.\n\n"
        "Step 4 — Draft your patch suggestions (patch-or-silent, DESIGN §11.2).\n"
        "  Rules:\n"
        "    - Emit a PATCH for a section ONLY if you found sourced content (real URL)\n"
        "      that improves it. Found nothing → emit NO patch for that section.\n"
        "    - NEVER write prose explaining why you couldn't (no 'search unavailable',\n"
        "      no 'I could not find'). Silence = no change; the reducer keeps the doc.\n"
        "    - Use the exact section heading string as your anchor (e.g. \"## Results\").\n"
        "      Copy the heading verbatim from CURRENT DELIVERABLE CONTENT — do not paraphrase.\n"
        "    - Each patch targets ONLY sections you were assigned.\n"
        "    - Do NOT write patches for sections assigned to other agents.\n"
        "    - Prior weaknesses are labeled by section. Fix a \"[## Section]\" weakness\n"
        "      ONLY if that section is one you were assigned — you cannot patch a\n"
        "      section you do not own. A \"[document]\" weakness has NO single owner, so\n"
        "      EVERY agent must address it within its OWN assigned sections (apply the\n"
        "      global fix — e.g. grounding, no truncation, consistent terminology — to\n"
        "      each section you hold). Never edit a section outside your set. (§11.4)\n"
        "    - Prefer insert_after/append over replace — additive patches on distinct\n"
        "      anchors commute; replace patches on the same anchor conflict.\n"
        "    - Use the PATCHES JSON format exactly (see the patch schema).\n\n"
        "Step 5 — Emit DONE markers: DONE: [\"task-id-1\", \"task-id-2\"]\n\n"
        "Step 6 — Emit your PATCHES block (empty [] if you found nothing), then ONE\n"
        "  status line: 'SEARCH: ok' or 'SEARCH: error' (error iff the search tool\n"
        "  itself failed). The Reducer applies patches ADDITIVELY — it never rewrites\n"
        "  or shortens the document (DESIGN §11.3).\n"
    )


def _build_executor_prompt(goal: str, artifact_text: str, weaknesses_block: str) -> str:
    """STAR-spoke EXECUTOR prompt (§11.10 — the score-ceiling fix).

    The spokes were given the HUB planning prompt ("you are the planning hub …
    assign work … emit TASK_LIST/ASSIGNED"), so they PLANNED instead of fetching:
    the reducer received analysis, the artifact never gained sourced content, and
    the score stalled. This frames each spoke as a research EXECUTOR — fetch real
    pages and emit RESEARCH_FINDING blocks WITH their URLs — never a planner.
    """
    art = (artifact_text or "").strip()
    art_block = (
        f"CURRENT DELIVERABLE (improve it; do not restate a plan):\n{art[:3000]}\n\n"
        if art else ""
    )
    return (
        _today_note()
        + "You are a RESEARCH EXECUTOR, not a planner. DO the research NOW — do NOT "
        "emit TASK_LIST, ASSIGNED, or any plan/assignment.\n\n"
        f"GOAL: {goal}\n\n"
        f"{art_block}"
        f"WEAKNESSES TO FIX (concrete gaps):\n{weaknesses_block or '(none)'}\n\n"
        "Step 1 — For each weakness in your focus, run web_search THEN web_fetch to "
        "read the ACTUAL article content (not just the snippet).\n"
        "Step 2 — Emit one RESEARCH_FINDING per page you fetched, exactly:\n"
        "  RESEARCH_FINDING:\n"
        "  ARTICLE_TITLE: <title>\n"
        "  URL: <the EXACT url you fetched — REQUIRED, never omit>\n"
        "  POPULARITY: <metric if stated, else n/a>\n"
        "  PATCH_TARGET: <the '## Section' heading this improves>\n"
        "  QUOTE: <COPY-PASTE one sentence EXACTLY from the fetched page — character "
        "for character, no paraphrase, no edits, no ellipsis. This verbatim text IS "
        "the evidence; do not summarize or restate it.>\n"
        "  WHY: <why this quote matters to the GOAL, one sentence in your words>\n\n"
        "Citing without substantiating is the #1 failure: a bare URL is NOT enough. "
        "COPY the QUOTE verbatim from the page — do NOT restate the article in your own "
        "words, PASTE its words. The quote is checked against the fetched page (a "
        "fabricated one is dropped); WHY only frames the quote's relevance.\n"
        "Rules: emit a RESEARCH_FINDING ONLY for a URL you actually fetched (so it "
        "is real and verifiable); every finding MUST carry its URL AND a verbatim "
        "QUOTE; found nothing for a weakness → emit nothing for it (no narration, "
        "no plan).\n"
        "OUTPUT FORMAT (critical): your ENTIRE response is RESEARCH_FINDING blocks and "
        "nothing else. Do NOT write a report, executive summary, section prose, or any "
        "'#'/'##' markdown headings — the reducer assembles the report FROM your "
        "findings. A response that is prose instead of RESEARCH_FINDING blocks is "
        "discarded and the document gains nothing.\n"
    )


def _build_reducer_refine_prompt(goal: str, artifact_path: str) -> str:
    """Reducer Phase-2 editorial refinement prompt (DESIGN §2.2 Step 5).

    Phase 1 (structural merge) is done mechanically by ``reduce_patches``; this
    prompt drives the Phase-2 full-document polish. The merged text is appended
    by the caller after this header (the ``{merged_text}`` slot)."""
    return (
        "You are the Reducer for this phase. All worker patches have been merged\n"
        "into the document below. Your job is the SECOND phase: a full editorial\n"
        "pass — not a mechanical merge. Think through each step in order.\n\n"
        f"GOAL: {goal}\n"
        f"DELIVERABLE PATH: {artifact_path}\n\n"
        "Step 1 — Read the full merged document below as a document editor,\n"
        "  forming an overall sense of its structure and intent.\n\n"
        "Step 2 — Assess coherence: does the document flow logically section to\n"
        "  section? Note every place the flow breaks.\n\n"
        "Step 3 — Find and fix gaps: missing transitions, incomplete sentences,\n"
        "  orphaned headings with no body.\n\n"
        "Step 4 — Resolve every `<!-- conflict -->` marker by integrating or\n"
        "  removing the marked content cleanly; no conflict markers may remain.\n\n"
        "Step 5 — Remove redundancy: deduplicate content multiple workers inserted\n"
        "  identically.\n\n"
        "Step 6 — Enforce consistency: uniform terminology, citation style, and\n"
        "  heading hierarchy throughout.\n\n"
        "Step 7 — Improve quality: tighten prose, correct factual inconsistencies,\n"
        "  improve clarity.\n\n"
        "Step 8 — Emit the best possible COMPLETE document. Output the full refined\n"
        "  document only — every section, no truncation, no commentary. Your output\n"
        "  length must be >= the merged input length.\n\n"
        "--- BEGIN MERGED DOCUMENT ---\n"
    )


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
    """True iff every worker reported SEARCH: error and none produced findings (§11.6).

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


def _build_skeleton(goal: str, client, embedder=None) -> str:
    """Build an initial document skeleton from the goal (DESIGN §11.5).

    Template reuse (1st-document creation): if a prior research report's skeleton semantically
    matches this goal (cosine over the embedder, via TemplateStore), reuse its proven STRUCTURE
    instead of LLM-generating one — faster, consistent, grounded in a report that worked. Falls
    back to LLM generation when no template matches or no embedder is wired.

    Headings + placeholder bodies only — NO search needed, so it is robust to a
    search outage. Workers then fill each section additively, the SAME pipeline as
    improving an existing doc (create == improve). Falls back to a generic skeleton
    if the LLM is unavailable or returns nothing usable.
    """
    if embedder is not None:
        try:
            from studio.templates import TemplateStore
            tmpl = TemplateStore(embedder=embedder).find_template(goal)
            if tmpl:
                return tmpl
        except Exception:  # noqa: BLE001 — template store optional; fall back to LLM
            pass
    prompt = (
        "Set up a document SKELETON for the goal below. Output ONLY markdown:\n"
        "  - one title line (#)\n"
        "  - 4-8 section headings (##) that the goal genuinely requires (derive them\n"
        "    from the goal — not a generic template)\n"
        "  - under each heading exactly this placeholder line:\n"
        "      _(pending — needs sourced content)_\n"
        "Do NOT write real content or invent facts. Headings + placeholders only.\n\n"
        f"GOAL: {goal}\n"
    )
    try:
        res = client.chat([{"role": "user", "content": prompt}])
        out = (getattr(res, "text", "") or "").strip()
        if out.count("##") >= 2:
            return out
    except Exception:  # noqa: BLE001 — fall back to a generic skeleton
        pass
    return (
        f"# {goal.strip()[:100]}\n\n"
        "## Overview\n_(pending — needs sourced content)_\n\n"
        "## Key Findings\n_(pending — needs sourced content)_\n\n"
        "## Details\n_(pending — needs sourced content)_\n\n"
        "## Sources\n_(pending — needs sourced content)_\n\n"
        "## References\n_(pending — needs sourced content)_\n"
    )


def _detect_gaps(artifact_text: str) -> list[tuple[str, str]]:
    """Detect gaps in the merged deliverable (DESIGN §11.4).

    A gap is a section that is **empty or a placeholder**. Each gap is tagged with
    its nearest **top-level** section (h1/h2) so the caller can consolidate by
    section before sizing agents — a report has a bounded number of top-level
    sections, so the worklist (and thus agent count) is structurally bounded.

    Returns (top_level_section, message) tuples. Routed by the caller: non-last
    phase → consolidated to distinct sections, handed to the next phase via the
    ledger; last phase → messages carried to the next run as weaknesses.

    NOTE (2026-06-27 fix): the old "substantive prose but no inline http → gap"
    rule mis-flagged every well-formed section of a properly-cited report (whose
    citations live in a References section, not inline) — ~74 false gaps that
    exploded agent sizing. Removed: prose with content is NOT a gap; only
    empty/placeholder sections are.
    """
    gaps: list[tuple[str, str]] = []
    parts = _re.split(r'(?m)^(#{1,6}\s+.+)$', artifact_text)
    # parts = [pre, heading1, body1, heading2, body2, ...]
    it = iter(parts[1:])
    top = "(document root)"
    for heading in it:
        body = next(it, '')
        h, b = heading.strip(), body.strip()
        level = len(h) - len(h.lstrip('#'))
        if level <= 2:
            top = h  # nearest h1/h2 owns the sub-sections beneath it
        low = b.lower()
        if not b or '_(pending' in low or 'placeholder' in low:
            gaps.append((top, f"{h}: empty/placeholder — needs sourced content"))
    return gaps


def _gap_sections(gaps: list[tuple[str, str]]) -> list[str]:
    """Consolidate gaps to distinct top-level sections (DESIGN §11.4).

    Consolidation shrinks the LEDGER input (sections, not raw gap count) that
    drives ``_max_workers`` (concurrency) and the hub prompt's ledger block.
    It does NOT by itself bound the spoke COUNT: the fan-out derives breadth
    from ``_facets`` (STAR/MESH) and the upstream item count (MAP), which read
    prose/lists, not section count. The hard breadth cap is ``run_plan``'s
    ``max_agents`` arg (the 2026-06-27 gap-flood fix). Order-preserving.
    """
    seen: set[str] = set()
    out: list[str] = []
    for top, _ in gaps:
        if top not in seen:
            seen.add(top)
            out.append(top)
    return out


def _unresolved_block(weaknesses: list[str], repeat_failed: set[str], limit: int) -> str:
    """User-facing 'known unresolved issues' block (DESIGN §11.4 — surface, don't hide).

    A repeat-failure (recorded in >= ``limit`` prior runs) still present in this
    run's weaknesses was attempted again — including the last phase — and remains
    open. It is appended below the result shown in the chat window so the user
    knows what could not be resolved, instead of being silently dropped. Returns
    '' when nothing is still open.
    """
    from studio.task_runs import _norm_weakness
    still_open = [w for w in weaknesses if _norm_weakness(w) in repeat_failed]
    if not still_open:
        return ""
    return (
        "\n\n---\n\n## ⚠️ Known unresolved issues\n\n"
        f"_Attempted across {limit}+ runs (including this run's final phase) and "
        "still open — surfaced, not hidden:_\n\n"
        + "\n".join(f"- {w}" for w in still_open)
    )


def _strip_preamble(text: str) -> str:
    """Strip any non-document preamble before the artifact's first markdown
    heading (DESIGN §11.4). A reducer occasionally prepends review commentary
    ('The artifact is complete... Weaknesses addressed: ✅... Remaining concern:')
    instead of emitting the document. That commentary belongs in the chat (the
    surfaced _unresolved_block), NEVER in the artifact — and the grow-only ratchet
    would otherwise LOCK it into the seed forever (a clean-up that shortens the doc
    is rejected as a regression). Applied at every artifact boundary (seed, reducer
    read, write-back) so inherited corruption is sanitized and cannot propagate.

    Strips everything before the first line beginning with '#'. No heading found =>
    return unchanged (never destroy a genuinely heading-less document).

    Also removes inherited '<!-- conflict(...): anchor not found -->' markers that
    reduce_patches emitted on a missing anchor in an EARLIER version (before the
    anchor-demotion fix) and that the additive merge then froze into the seed forever.
    Anchor-demotion prevents NEW markers; this strips the old ones (the content beneath
    a marker is kept — only the noise comment line is removed).
    """
    import re
    text = re.sub(r"[ \t]*<!--\s*conflict.*?-->[ \t]*\n?", "", text)
    m = re.search(r"^#", text, flags=re.MULTILINE)
    return text[m.start():] if m else text


def _weakness_score(
    prior_weaknesses: list[str],
    open_weaknesses: list[str],
    embedder=None,
    threshold: float = 0.85,
) -> float:
    """Hill-climb score = solved / total over the weakness set (§11.4).

    A prior weakness is SOLVED only if NO still-open weakness is SEMANTICALLY
    similar to it. Matching must be semantic, not string: the LLM miner re-words
    the same issue every run ("no comparative metrics" -> "no systematic ranking"),
    so exact/normalized-string matching counted a re-worded-but-unsolved weakness as
    'solved' and inflated the score on an UNCHANGED artifact. total = prior + open
    issues with no prior match (genuinely new). No weakness anywhere => 1.0.

    Falls back to normalized-string matching when no embedder is available.
    """
    from studio.task_runs import _cosine, _norm_weakness
    prior = [w for w in (prior_weaknesses or []) if w and w.strip()]
    open_ = [w for w in (open_weaknesses or []) if w and w.strip()]
    if not prior and not open_:
        return 1.0

    if embedder is not None:
        try:
            pe = embedder.embed(prior) if prior else []
            oe = embedder.embed(open_) if open_ else []
            def _hit(vec, others) -> bool:
                return any(_cosine(vec, o) >= threshold for o in others)
            solved = sum(1 for pv in pe if not _hit(pv, oe))
            new_open = sum(1 for ov in oe if not _hit(ov, pe))
            total = len(prior) + new_open
            return round(solved / total, 2) if total else 1.0
        except Exception:  # noqa: BLE001 — embedding unavailable → string fallback
            pass

    pn = {_norm_weakness(w) for w in prior}
    on = {_norm_weakness(w) for w in open_}
    total_set = pn | on
    return 1.0 if not total_set else round(len(total_set - on) / len(total_set), 2)


#: Max cited URLs to prefetch per reduce phase (bounds added fetch latency/cost).
_PREFETCH_LIMIT = 8


def _prefetch_cited(drafts: list[str], limit: int = _PREFETCH_LIMIT) -> int:
    """Fetch cited-but-uncached URLs from the worker drafts so genuine sources pass the
    grounding guard (the fetch-density fix). No-op when the fetch cache is empty — that
    means no grounding drop happens (cache_active is False), so there is nothing to fix,
    and tests stay offline. Bounded by ``limit`` to cap latency. Returns how many cited
    URLs are now cached."""
    from studio.tools import _fetch_cache, prefetch_url
    if not _fetch_cache:
        return 0
    seen: list[str] = []
    for d in drafts:
        for m in _re.finditer(r'URL:\s*(https?://\S+)', d):
            u = m.group(1).strip().rstrip('.,)')
            if u not in seen:
                seen.append(u)
    fetched = sum(1 for u in seen[:limit] if prefetch_url(u))
    _dbg(f"prefetch cited={len(seen)} fetched_ok={fetched} (cap {limit})")
    return fetched


def _dbg(msg: str) -> None:
    """Append a throughput-diagnostic line to the file named by OMC_THROUGHPUT_DEBUG.

    A no-op unless that env var is set, so production and tests write nothing. Used to
    localize where findings are lost between the spokes and the artifact (raw findings →
    grounded floor patches → patches actually applied → grow-only writeback)."""
    import os
    path = os.environ.get("OMC_THROUGHPUT_DEBUG")
    if not path:
        return
    try:
        with open(path, "a") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


def _apply_ranking(doc: str, findings: list) -> str:
    """F4/F5: replace the source-selection section with the honest split ranking table.

    Ranks EVERY source cited in the doc (this phase's rich findings supply title/popularity;
    others are added bare from the doc's URLs for completeness). Best-effort: no findings / no
    target section / a metric failure → doc unchanged. The S2/GitHub lookups go through
    ``fetch_metrics`` (ONE S2 batch, cached in .web_cache.json under metric:<url>, degrade to
    None) so they never block or crash a run."""
    if not findings:
        return doc
    import json as _json
    import os as _os
    from agentkit.artifacts.metrics import fetch_metrics
    from agentkit.artifacts.patcher import DocPatch, reduce_patches
    from agentkit.artifacts.ranking import synthesize_ranking_table
    from agentkit.artifacts.sections import split_sections
    from agentkit.artifacts.types import Finding

    target = next((h for h, _b in split_sections(doc)
                   if 'source selection' in h.lower()
                   or h.lower().strip().endswith('sources')
                   or 'popularity' in h.lower()), None)
    if target is None:
        return doc
    body = dict(split_sections(doc)).get(target, '')
    if not body:
        return doc
    # rank ALL sources cited in the doc, enriched by this phase's findings
    rich = {f.url: f for f in findings}
    all_findings = [rich.get(u) or Finding(url=u)
                    for u in {x.rstrip('.,)') for x in _re.findall(r'https?://\S+', doc)}]
    if not all_findings:
        return doc
    from agentkit.artifacts.metrics import source_kind
    # Only touch the network/cache file when a source actually HAS a fetchable metric
    # (arxiv/github). A blog-only doc (e.g. offline tests) skips file I/O entirely → all
    # sources are 'reported', no network, no .web_cache.json read/write.
    if not any(source_kind(f.url)[0] for f in all_findings):
        metrics = {f.url: None for f in all_findings}
    else:
        cache: dict = {}
        try:
            if _os.path.exists('.web_cache.json'):
                with open('.web_cache.json') as _cf:
                    cache = _json.load(_cf)
        except Exception:  # noqa: BLE001
            cache = {}
        try:
            metrics = fetch_metrics([f.url for f in all_findings],
                                    s2_key=_os.environ.get('SEMANTIC_SCHOLAR_API_KEY'), cache=cache)
            with open('.web_cache.json', 'w') as _cf:   # persist metric:<url> entries
                _json.dump(cache, _cf)
        except Exception:  # noqa: BLE001 — metrics best-effort; never block
            metrics = {}
    table = synthesize_ranking_table(all_findings, metrics)
    return reduce_patches(
        doc, [[DocPatch(op='replace', anchor=body, content=f"{target}\n\n{table}\n",
                        source='ranking')]]
    ).text


def _make_section_reducer(client, artifact_text: str, weaknesses: list[str], embedder=None):
    """Build the section-aware STAR reducer closure (DESIGN §4.5; Lever 3).

    Returns ``run_plan``'s reducer hook ``(worker_drafts) -> (merged_text, tokens)``.

    PATCH-BASED (Lever 3): instead of re-emitting the full ~38K document — whose
    output a completion cap (``max_tokens``) truncates mid-section (the v29
    truncation/incomplete weaknesses) — the reducer asks the model for a SMALL list
    of section PATCHES and applies them MECHANICALLY via ``reduce_patches``. The
    model never re-emits the document, so truncation is impossible and output tokens
    drop ~10x. As a deterministic floor, the workers' own RESEARCH_FINDING blocks are
    converted to additive patches too — so a phase always makes grounded progress even
    if the model emits no usable PATCHES. Additive only; the runner's grow-only
    writeback ratchet still rejects any shrink. Sections are the artifact's ``##``
    headings — the structure lives in the markdown + weakness tags, no Section type.
    """
    wk_block = "\n".join(f"- {w}" for w in (weaknesses or [])) or "(none)"
    art_block = artifact_text.strip()

    def reduce(drafts: list[str]) -> tuple[str, int]:
        workers = "\n\n".join(f"[worker {i + 1}]\n{d}" for i, d in enumerate(drafts))
        prompt = (
            _today_note() +
            "You are the section-aware reducer of a multi-worker research phase.\n"
            "Do NOT re-emit the document. Emit a SMALL JSON list of PATCHES that fold "
            "each worker's SOURCED finding into the CURRENT ARTIFACT, section by "
            "section (sections are the '##' headings).\n\n"
            "Each patch is one object:\n"
            '  {"op": "insert_after", "anchor": "## <exact section heading from the '
            'artifact>", "content": "<a substantiating SENTENCE woven from the '
            'finding: its central claim + a short verbatim quote + the source URL>"}\n'
            '  - op is "insert_after" (add prose under a heading) or "replace" (swap a '
            "placeholder line for grounded prose).\n"
            "  - anchor MUST be text that already exists in the CURRENT ARTIFACT.\n"
            "  - content ADDS grounded prose and keeps every source URL.\n\n"
            "RULES — violating these REGRESSES the deliverable:\n"
            "  - Additive only: a patch may ADD substance, never delete or shorten "
            "existing sourced content.\n"
            "  - Every added claim keeps its source URL from the worker's "
            "RESEARCH_FINDING.\n"
            "  - A weakness below resolved by a worker (with a real URL) → weave it in "
            "as a sentence, not a bare citation line.\n"
            "  - No worker content for a section → emit no patch for it.\n\n"
            f"SECTION WEAKNESSES (review checklist):\n{wk_block}\n\n"
            f"CURRENT ARTIFACT:\n--- BEGIN ---\n{art_block or '(empty)'}\n--- END ---\n\n"
            f"WORKER OUTPUTS:\n{workers}\n\n"
            "Output ONLY:\nPATCHES:\n```json\n[ ... ]\n```\n"
            "Nothing else — no document, no preamble, no commentary."
        )
        res = client.chat([{"role": "user", "content": prompt}])
        tokens = int(getattr(res, "total_tokens", 0) or 0)
        llm_patches = _parse_patches_from_output(res.text or "")
        # Deterministic floor: convert the workers' own RESEARCH_FINDING blocks to
        # additive patches. Guarantees grounded progress when the model emits no
        # usable PATCHES, and folds in any finding it skipped. The reduce_patches
        # duplicate-guard makes the overlap idempotent.
        # Fetch-density fix: spokes cite ~12 URLs/phase but fetch ~1, so the grounding
        # guard dropped 80-100% of real findings. Fetch the cited-but-uncached URLs now
        # so genuine sources survive grounding (a 404/fabricated URL still drops).
        _prefetch_cited(drafts)
        findings: list = []
        raw_findings = 0
        for d in drafts:
            raw_findings += len(_re.findall(r'#{0,6}\s*RESEARCH_FINDING', d))
            findings += _parse_findings(d)
        # F1: collapse near-duplicate findings (STRUM merge) BEFORE they become patches, so
        # the additive merge stops dumping ~26 repetitive citations as an unordered block.
        from agentkit.artifacts.dedup import dedupe_findings
        findings, n_dedup = dedupe_findings(findings, embedder)
        floor_patches = _findings_to_patches(findings)
        patches = llm_patches + floor_patches
        if not patches:
            _dbg(f"reduce drafts={len(drafts)} raw_findings={raw_findings} "
                 f"llm={len(llm_patches)} floor=0 dedup={n_dedup} → NO PATCHES (no findings survived)")
            return art_block, tokens  # nothing to add → unchanged (no truncation)
        # Resolve anchors before merging: a finding's PATCH_TARGET that is not a real
        # heading in the doc would otherwise become a '<!-- conflict -->' marker that
        # pollutes the artifact (the throughput fix surfaced 13 such markers in one
        # phase). Demote any insert_after with a missing anchor to a clean append.
        for p in patches:
            if getattr(p, "op", "") == "insert_after" and p.anchor and p.anchor not in art_block:
                p.op, p.anchor = "append", None
        from agentkit.artifacts.patcher import reduce_patches
        rr = reduce_patches(art_block, [patches])
        # F4/F5: replace the source-selection section with the honest split ranking table.
        merged = _apply_ranking(rr.text, findings)
        _dbg(f"reduce drafts={len(drafts)} raw_findings={raw_findings} "
             f"llm={len(llm_patches)} floor={len(floor_patches)} dedup={n_dedup} "
             f"applied_delta={len(rr.text) - len(art_block)} conflicts={len(rr.conflicts)} "
             f"ranked_delta={len(merged) - len(rr.text)}")
        return merged.strip(), tokens

    return reduce


def _parse_findings(text: str) -> list:
    """Parse RESEARCH_FINDING blocks → grounded ``agentkit.artifacts.types.Finding`` objects.

    Bare ``RESEARCH_FINDING:`` and ``## RESEARCH_FINDING`` both parse. Dual grounding oracle
    (Lever 1): keep a finding iff its URL is http(s) AND — when the fetch cache holds pages —
    its URL was fetched OR its verbatim quote appears on a fetched page; else drop (a true
    fabrication: invented URL AND invented quote). ``quote_verified`` gates whether the
    verbatim quote is later woven. CLAIM/CONTENT/KEY_INSIGHT fold into ``why`` (the schema
    dropped the rephrased CLAIM — the verbatim QUOTE is the evidence)."""
    from agentkit.artifacts.types import Finding
    from studio.tools import _fetch_cache, _quote_in_cache, _url_in_cache

    cache_active = bool(_fetch_cache)
    out: list = []
    for m in _re.finditer(
        r'#{0,6}\s*RESEARCH_FINDING(.*?)(?=#{0,6}\s*RESEARCH_FINDING|\Z)', text, _re.DOTALL
    ):
        block = m.group(1)

        def _f(name: str) -> str:
            fm = _re.search(rf'{name}:\s*(.+)', block)
            return fm.group(1).strip() if fm else ''

        url = _f('URL')
        if not url.lower().startswith('http'):
            continue  # not a sourced finding → not content
        quote = _f('QUOTE').strip().strip('"')
        quote_verified = bool(quote) and _quote_in_cache(quote)
        grounded = (not cache_active) or _url_in_cache(url) or quote_verified
        if cache_active and not grounded:
            continue
        out.append(Finding(
            url=url,
            title=_f('ARTICLE_TITLE'),
            quote=quote,
            why=_f('WHY') or _f('CLAIM') or _f('CONTENT') or _f('KEY_INSIGHT'),
            popularity=_f('POPULARITY'),
            patch_target=_f('PATCH_TARGET'),
            quote_verified=quote_verified,
            grounded=grounded,
        ))
    return out


def _findings_to_patches(findings: list) -> list:
    """Grounded ``Finding`` objects → additive, WOVEN DocPatches (Lever 2: verbatim copy-paste
    evidence). The verbatim QUOTE is the evidence when verified; ``why`` frames it. Each becomes
    an ``insert_after`` its PATCH_TARGET (or ``append`` if none). Additive only."""
    from agentkit.artifacts.patcher import DocPatch

    patches: list = []
    for f in findings:
        cite = f"[{f.title or f.url}]({f.url})"
        has_pop = bool(f.popularity and f.popularity.lower() != 'n/a')
        pop_clause = f", {f.popularity}" if has_pop else ""
        lead = f"{f.why.rstrip('.')}: " if f.why else ""
        if f.quote_verified:  # COPY-PASTE: the verbatim source excerpt IS the evidence
            content = f'\n\n{lead}"{f.quote}" ({cite}{pop_clause}).\n'
        elif f.why:           # no verifiable quote → grounded by URL; framing + citation
            content = f"\n\n{f.why.rstrip('.')} ({cite}{pop_clause}).\n"
        else:                 # nothing to weave → bare citation line
            content = f"\n- {cite}{(' (' + f.popularity + ')') if has_pop else ''}\n"
        if f.patch_target:
            patches.append(DocPatch(op="insert_after", anchor=f.patch_target, content=content, source="finding"))
        else:
            patches.append(DocPatch(op="append", anchor=None, content=content, source="finding"))
    return patches


def _research_findings_to_patches(text: str) -> list:
    """Back-compat one-shot: parse + ground + weave (the post-loop patch path). The reducer
    uses _parse_findings + dedupe_findings + _findings_to_patches separately so it can collapse
    near-duplicate findings before they become patches (F1)."""
    return _findings_to_patches(_parse_findings(text))


def _parse_patches_from_output(text: str) -> list:
    """Extract DocPatch list from a worker's PATCHES JSON block (DESIGN §2.2).

    Returns empty list when no block is present — the caller falls through to
    the RESEARCH_FINDING reducer path unchanged.
    """
    from agentkit.artifacts.patcher import DocPatch

    m = _re.search(r'PATCHES:\s*```json\s*(\[.*?\])\s*```', text, _re.DOTALL)
    if not m:
        m = _re.search(r'"patches"\s*:\s*(\[.*?\])', text, _re.DOTALL)
    if not m:
        return []
    try:
        items = _json.loads(m.group(1))
        return [
            DocPatch(
                op=item.get("op", "append"),
                anchor=item.get("anchor"),
                content=item.get("content", ""),
                source=item.get("source", ""),
            )
            for item in items
            if isinstance(item, dict)
        ]
    except (ValueError, TypeError):
        return []


# ---------------------------------------------------------------------------

#: Emit sink: the runner calls this for every event; app.py wires it to a queue.
Emit = Callable[[StudioEvent], None]


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


#: A document is "complete" if its last non-space char closes a sentence/structure.
#: Used to reject a truncated artifact in favor of a complete synthesis (see the
#: result_output selection below). Markdown reports legitimately end on a period,
#: list/table row, fence, blockquote, or heading underline — so the set is permissive;
#: a bare cutoff mid-word/URL (the truncation symptom) fails it.
_CLEAN_END_CHARS = frozenset(".!?)]\"'`|>*-_")


def _ends_cleanly(text: str) -> bool:
    """True if ``text`` ends at a sentence/structure boundary (not truncated mid-line).

    Research reports end with reference lines like "- Author. 'Title.' https://url"
    where the last WORD is a URL, not the line itself. Check last word for URL prefix.
    """
    stripped = text.rstrip()
    if not stripped:
        return False
    last_line = stripped.split("\n")[-1].strip()
    last_word = last_line.split()[-1] if last_line.split() else ""
    if last_word.startswith("http://") or last_word.startswith("https://"):
        return True
    return stripped[-1] in _CLEAN_END_CHARS


class Runner:
    """Drives one studio run end-to-end, emitting the ordered SSE sequence.

    Constructed per run from a ``Session`` + an ``Emit`` sink + an injected
    ``LLMClient`` factory (so tests pass a fake client with no network). The
    factory takes the ``on_usage`` callback and returns a client.
    """

    def __init__(
        self,
        session: Session,
        emit: Emit,
        *,
        client_factory: Callable[[Callable[[UsageReport], None]], LLMClient] | None = None,
        embedder: Any = None,
        sandbox_cwd: str = ".",
        search_fn: Callable[..., list[Any]] | None = None,
        fetch_fn: Callable[..., Any] | None = None,
        workspace_root: Any = None,
    ) -> None:
        self._session = session
        self._emit = emit
        self._client_factory = client_factory
        self._embedder = embedder
        self._sandbox_cwd = sandbox_cwd
        #: Injected web_search fn for the tool loop (tests pass a stub → no net).
        self._search_fn = search_fn
        #: Injected web_fetch fn for the tool loop (tests pass a stub → no net).
        self._fetch_fn = fetch_fn
        #: Workspace root override for the file-tool jail (tests pass a tmp dir).
        self._workspace_root = workspace_root
        self._acc = TokenAccounting()
        self._current_step_id = ""
        #: Wall-clock start of the run, stamped in run(); the done frame reports
        #: real elapsed time (per-phase wall_s lives on phase_done).
        self._t0: float | None = None
        #: Tokens captured via on_usage for the current phase (used to reconcile
        #: against run_plan's StepRun.tokens for non-StudioChatClient backends).
        self._phase_captured = 0

    # -- token plumbing ----------------------------------------------------

    def _on_usage(self, usage: UsageReport) -> None:
        """Per-call usage sink: feed accounting + push a ``token`` frame.

        Closes over ``_current_step_id`` so each frame is attributed to the phase
        running when the LLM call fired.
        """
        self._acc.add(usage)
        self._phase_captured += usage.input_tokens + usage.output_tokens
        self._emit(
            TokenEvent(
                step_id=self._current_step_id,
                input=usage.input_tokens,
                output=usage.output_tokens,
                total=usage.input_tokens + usage.output_tokens,
                estimated=usage.estimated,
                cumulative={
                    "input": self._acc.total_input_tokens,
                    "output": self._acc.total_output_tokens,
                    "total": self._acc.total_tokens,
                    "estimated": self._acc.tokens_estimated,
                },
            )
        )

    def _reconcile_phase_tokens(self, step_id: str, step_tokens: int) -> None:
        """Emit a ``token`` frame for tokens ``run_plan`` counted but ``on_usage``
        did not capture (the raw/CLI-client case, where no UsageReport fires).

        Such tokens carry no in/out split and no usage telemetry, so they are
        booked as ``estimated`` output tokens — flipping the run's sticky ``~``,
        which is the honest signal for "this backend did not report a split".
        Keeps the HUD reconciled to ``DynamicPlanResult.total_tokens`` (SPEC §8).
        """
        remainder = step_tokens - self._phase_captured
        if remainder <= 0:
            return
        self._on_usage(
            UsageReport(input_tokens=0, output_tokens=remainder, estimated=True)
        )

    # -- the run -----------------------------------------------------------

    def run(self, requirement: str) -> None:
        """Execute the full pipeline for ``requirement``, emitting every event."""
        self._t0 = time.perf_counter()
        try:
            self._run_inner(requirement)
        except Exception as exc:  # noqa: BLE001 - any failure becomes an error frame
            self._emit(ErrorEvent(message=str(exc), where="runner"))
            # Still emit a terminal done so the frontend leaves the running state.
            self._emit(self._done_event("", cancelled=False))

    def _run_inner(self, requirement: str) -> None:
        session = self._session

        # Inject goal end_state and constraints into requirement so the agent
        # sees them during planning — not just during post-phase verification.
        _goal = getattr(session, "goal", None)
        if _goal is not None:
            _parts: list[str] = []
            if getattr(_goal, "end_state", None):
                _parts.append(f"Goal: {_goal.end_state}")
            _constraints = getattr(_goal, "constraints", None) or []
            if _constraints:
                _parts.append("Constraints:\n" + "\n".join(f"- {c}" for c in _constraints))
            if _parts:
                requirement = "\n".join(_parts) + "\n\n" + requirement

        # Stash the original requirement so task_hash is stable across iterations
        # (the seeder may rewrite requirement with "ITERATION N —..." prefix).
        _original_requirement = requirement

        # Hill climb: if auto_improve is on and a prior run exists for this task,
        # copy its artifact into the current workspace and prefix the requirement
        # with the prior score + weaknesses so the agent edits rather than regenerates.
        _hc_cfg = getattr(session, "hill_climb_config", None) or {}
        _artifact_copied = False
        _eff_ws2 = None
        _weaknesses_block = ""  # prior-run lessons → planner/hub constraints
        _seed_len = 0           # length of the seeded prior artifact (anti-regression)
        # §11.4 loop-closure check: normalized weaknesses recorded in >= REPEAT_LIMIT
        # prior runs of this task were injected and never fixed. The reducer drops
        # them from its handoff (below) instead of grinding on them forever. Empty
        # when not hill-climbing.
        _repeat_failed: set[str] = set()
        if _hc_cfg.get("auto_improve"):
            import shutil
            from studio.task_runs import TaskRunStore, task_hash as _task_hash
            _thash = _task_hash(requirement)
            # Pass the embedder so each run's requirement is embedded for R10
            # cross-task similarity retrieval (no-op when embedder is None).
            _store = TaskRunStore(embedder=self._embedder)
            _repeat_failed = _store.repeat_failures(_thash)
            # Use latest run with actual artifact content — LLM self-eval scores
            # are noisy; the most recent non-empty artifact has accumulated the
            # most incremental work and is the best hill-climb seed.
            from studio.workspace import workspace_root as _ws_root_fn3
            _eff_ws2 = self._workspace_root or _ws_root_fn3()
            _prior = _store.latest_with_content(_thash, ws_root=_eff_ws2)
            if _prior:
                _prior_art = _eff_ws2 / _prior.session_id / "artifact.md"
                _artifact_copied = False
                if _prior_art.exists():
                    _curr_ws = Workspace(session.session_id, root=_eff_ws2)
                    shutil.copy(_prior_art, _curr_ws.root / "artifact.md")
                    _artifact_copied = True
                    # Record the seed size: a run must never write back a SHORTER
                    # artifact than it started from. Guarantees "worst case = no
                    # improvement" — a failed/thin run keeps the prior good doc
                    # instead of overwriting it (the regression that stranded the
                    # good 28KB report behind thin "search unavailable" output).
                    # §11.4: SANITIZE inherited corruption first — an artifact a
                    # prior reducer poisoned with a commentary preamble would
                    # otherwise be locked in by the grow-only ratchet forever (a
                    # clean-up that shortens it reads as a regression). Strip on seed
                    # so _seed_len is the CLEAN baseline the run grows from.
                    try:
                        _seed_path = _curr_ws.root / "artifact.md"
                        _seed_clean = _strip_preamble(_seed_path.read_text())
                        _seed_path.write_text(_seed_clean)
                        _seed_len = len(_seed_clean)
                    except OSError:
                        _seed_len = 0
                # Accumulate weaknesses from this task's prior runs AND from
                # semantically SIMILAR prior tasks (R10) — every failure lesson,
                # including cross-task ones, carries forward. Deduplicated by
                # exact string; exact-task lessons rank first. Degrades to
                # exact-task-only when no embedder is available.
                # Cap to the top-N most relevant lessons (exact-task first). The
                # full accumulated set across many prior runs can be dozens of
                # items; injecting all of them bloats the requirement and makes
                # the planner explode each lesson into its own phase. 10 is plenty
                # of signal without overwhelming the plan.
                _MAX_INJECTED_WEAKNESSES = 10
                _all_weaknesses = _store.accumulated_weaknesses(
                    requirement, _thash, embedder=self._embedder,
                )[:_MAX_INJECTED_WEAKNESSES]
                # Weaknesses are CONSTRAINTS for the planner/hub (quality bar to
                # meet), NOT tasks to decompose — threaded via _weaknesses_block
                # into _plan_from_epics so epic planning treats them correctly,
                # and onto session.weaknesses so each phase hub sees them too.
                _weaknesses_block = "\n".join(f"- {w}" for w in _all_weaknesses)
                session.weaknesses = _all_weaknesses  # hub reads getattr(session,"weaknesses")
                _fix_items = "\n".join(
                    f"  {i+1}. {w}" for i, w in enumerate(_all_weaknesses)
                )
                # Workers use web_search/web_fetch only — no write_file tool. Multiple
                # workers run concurrently; writing a shared artifact.md would cause
                # conflicts. Each worker's TEXT OUTPUT is its "temp file": the runner
                # collects outputs[step.id] = sr.output and the reducer receives all
                # of them via upstream context. RESEARCH_FINDING blocks let the reducer
                # apply each finding independently.
                #
                # Prompt structure: imperative tool-call instruction FIRST, schema
                # SECOND. "FIND AND OUTPUT" framing causes narration (model says "I'll
                # search" but never calls the tool). "Use web_search tool right now"
                # triggers actual tool_call responses the loop can execute.
                if _fix_items:
                    _finding_schema = (
                        "## RESEARCH_FINDING\n"
                        "ARTICLE_TITLE: <exact title>\n"
                        "URL: https://<exact URL — required>\n"
                        "POPULARITY: <verifiable signal: top-N result, N shares, N citations>\n"
                        "PUBLICATION: <date or unknown>\n"
                        "KEY_INSIGHT: <one sentence relevant to the task>\n"
                        "PATCH_TARGET: <exact article name or section heading in the artifact>\n"
                    )
                    requirement = (
                        f"{requirement}\n\n"
                        f"Use the web_search tool right now to find the following missing data:\n"
                        f"{_fix_items}\n\n"
                        f"For each item found, output a RESEARCH_FINDING block:\n"
                        f"{_finding_schema}\n"
                        f"Call web_search immediately.\n\n"
                        f"WORKER CONTRACT (DESIGN §11.2) — patch-or-silent:\n"
                        f"  - Found sourced content (with a real URL) → output a RESEARCH_FINDING.\n"
                        f"  - Found nothing → output NOTHING. Do NOT write a sentence explaining\n"
                        f"    why (no 'web search unavailable', no 'I could not find...'). Silence\n"
                        f"    means 'no change' — the reducer keeps the existing doc as-is.\n"
                        f"  - End with exactly ONE status line:\n"
                        f"      SEARCH: ok      (the search tool worked, whatever it returned)\n"
                        f"      SEARCH: error   (the search tool itself failed — quota/timeout/down)\n"
                        f"  URL is required in every RESEARCH_FINDING. Failure-narration is forbidden."
                    )

        # session frame
        self._emit(
            SessionEvent(llm=session.llm_info, embed=session.embed_info, mode=session.mode)
        )

        # build the usage-capturing client (injected factory in tests)
        base_client = self._build_client()
        # Wrap in a web_search tool loop when tools are enabled (run_plan stays
        # unchanged — it sees a plain LLMClient that happens to run a tool loop).
        # When a prior artifact was seeded, also offer read_artifact/patch_artifact
        # so concurrent workers can apply OCC patches directly to artifact.md.
        _art_for_tools = (
            _eff_ws2 / session.session_id / "artifact.md"
            if _artifact_copied and _eff_ws2 is not None
            else None
        )
        client = self._maybe_tool_augment(base_client, artifact_path=_art_for_tools)

        # plan → emit plan. Three paths:
        #   1. Seeded session  → pre-seed decomposition from a loop-library loop.
        #   2. LLM mode        → EPIC-BASED planning (DESIGN §2.3): the planner
        #                        LLM emits an EPIC_PLAN; each epic is one phase.
        #   3. Offline/auto    → deterministic plan() (no LLM available; tests).
        seed_steps = session.seed_steps
        use_llm = session.mode == "llm"
        if seed_steps:
            plan_obj = plan(requirement, decomposer=make_seeded_decomposer(seed_steps))
            self._emit(LoopSeedEvent(loop_id=session.seed_loop_id, steps=seed_steps))
        elif use_llm:
            # Planner runs on base_client (no tool loop — planning needs no web).
            # Plan from the CLEAN task (not the weakness/research-finding-bloated
            # `requirement`) and pass prior-run lessons as a constraints block, so
            # epic planning treats them as a quality bar — never decomposing each
            # weakness into its own phase.
            plan_obj = _plan_from_epics(
                _original_requirement, base_client, weaknesses_block=_weaknesses_block
            )
        else:
            plan_obj = plan(_original_requirement)
        # Capture the plan-as-dicts once: the PlanEvent payload AND the input the
        # Loop Doctor audits (its clear_stopping check walks this DAG at run end).
        plan_step_dicts = [
            {
                "id": s.id,
                "description": s.description,
                "depends_on": list(s.depends_on),
                "role": s.role,
                "difficulty": s.difficulty,
            }
            for s in plan_obj.steps
        ]
        self._emit(PlanEvent(task=plan_obj.task, steps=plan_step_dicts))

        # assign topologies (auto; llm path only when mode=='llm' AND client given).
        # use_llm already computed above for the epic-planning branch.
        plan_obj = assign_topologies(
            plan_obj, mode="auto", client=client, llm=use_llm
        )
        # Hill-climb REQUIRES STAR on every phase (DESIGN §11.4): only STAR's
        # reducer does the section-aware merge/refine/review of each worker's
        # per-section output against that section's weakness list, producing the
        # section-keyed {document, weaknesses} handoff that the next phase (and
        # next epoch) accumulates. MESH/PIPELINE/SINGLE have no such reducer, so
        # auto-derived topology would silently break the improvement loop. The
        # breadth cap (run_plan max_agents) keeps the forced STAR from exploding.
        if _hc_cfg.get("auto_improve"):
            plan_obj = replace(
                plan_obj,
                steps=tuple(replace(s, topology=STAR) for s in plan_obj.steps),
            )
        topology_map = {s.id: (s.topology or SINGLE) for s in plan_obj.steps}
        self._emit(
            TopologyEvent(
                steps=[{"id": sid, "topology": topo} for sid, topo in topology_map.items()]
            )
        )

        # derived render graph
        self._emit(_render_graph(plan_obj))

        # panel trackers
        dag = DagTracker(plan_obj)
        self._emit(dag.snapshot())
        mem = MemoryTracker(self._embedder)
        selfimp = SelfImproveTracker()
        budget = (
            FanoutBudget(ceiling=session.budget_ceiling)
            if session.budget_ceiling is not None
            else None
        )

        # M8: cross-phase TaskLedger and dynamic sizing (DESIGN §3, §5)
        from agentkit.orchestrator.ledger import TaskRecord, TaskLedger
        _ledger = TaskLedger()
        # Seed the ledger with every planned phase UP FRONT (DESIGN §2.3 / §3.2).
        # Without this, all_tasks stayed empty and remaining() was structurally
        # always empty — the REMAINING block printed "(none)" and worker sizing
        # saw max(1,0)=1 every phase. Seeding makes remaining() reflect real
        # pending work; mark_done() (end of loop) moves each finished phase to
        # completed, so later hubs see an accurate COMPLETED-vs-REMAINING split
        # and never re-assign prior-phase work.
        for _s in plan_obj.steps:
            _ledger.add_task(TaskRecord(id=_s.id, description=_s.description[:120]))
        _lc = getattr(session, "loop_config", None)
        _sizing_cfg = _lc.sizing() if _lc is not None else None

        outputs: dict[str, str] = {}
        _reducer_gaps: list[str] = []   # §11.4 last-phase gaps → next-run weaknesses

        # §11.5: create == improve. When improving but NO prior doc exists yet,
        # bootstrap a skeleton (headings + placeholders from the goal, no search) so
        # the phase loop fills it ADDITIVELY — the same pipeline as improving an
        # existing doc, instead of asking one LLM to author the whole report.
        if (_hc_cfg.get("auto_improve") and not _artifact_copied
                and _eff_ws2 is not None and use_llm):
            _skel = _build_skeleton(plan_obj.task or requirement, base_client,
                                    embedder=self._embedder)
            if _skel:
                _skel_file = _eff_ws2 / session.session_id / "artifact.md"
                _skel_file.parent.mkdir(parents=True, exist_ok=True)
                _skel_file.write_text(_skel)
                _artifact_copied = True       # additive pipeline now has a base
                _seed_len = len(_skel)        # may grow from here, never shrink below
        cancelled = False
        final_output = ""
        #: Gate outcomes collected across phases — the Loop Doctor's safe_actions
        #: check reads these at run end (no re-running of any gate).
        gate_events: list[GateEvent] = []

        for step in plan_obj.steps:
            if session.cancel_requested:
                cancelled = True
                break

            self._current_step_id = step.id
            self._phase_captured = 0
            # Planned fan-out (sizing cap + 1 reduce) so the DAG shows the agents
            # as RUNNING up front, not a default guess corrected only at phase_done.
            _planned_n = (_sizing_cfg.max_agents + 1) if _sizing_cfg is not None else None
            self._emit(PhaseStartEvent(step_id=step.id, n_agents=_planned_n))

            # router panel
            self._emit(build_router_event(step))

            # memory recall before the phase (what prior lessons apply)
            self._emit(mem.recall(step.description))

            # fold upstream outputs, then run the single-step sub-plan
            upstream = "\n\n".join(
                f"[{dep}] {outputs[dep]}" for dep in step.depends_on if outputs.get(dep)
            )
            is_last = step is plan_obj.steps[-1]
            desc = step.description
            # Inject the top-level task into every step whose description does not
            # already contain it.  This matters especially for downstream phases
            # (e.g. "create a research report") that are too terse to be meaningful
            # without the original goal, and for PIPELINE stages (previously STAR)
            # where the hub description never contained the full task text.
            topo = step.topology or SINGLE
            if plan_obj.task and plan_obj.task not in desc:
                desc = f"TASK: {plan_obj.task}\n\n{desc}"
            # On the final step, if there is upstream content, prefix with an
            # explicit instruction to output the artifact rather than asking for
            # more context. Loop catalog "stop" steps are written for humans; the
            # LLM needs an imperative framing to produce the artifact, not a
            # meta-decision about whether to continue.
            if is_last and upstream:
                if _artifact_copied:
                    # Prior artifact seeded into workspace. The reducer has no read_file
                    # tool, so inject the seeded content directly into the prompt.
                    # Workers produced RESEARCH_FINDING blocks in their text output;
                    # the reducer applies each block to the artifact independently.
                    # After the step runs we write sr.output back to artifact.md.
                    _seed_text = ""
                    if _eff_ws2 is not None:
                        try:
                            _seed_text = (_eff_ws2 / session.session_id / "artifact.md").read_text()
                        except OSError:
                            pass
                    _art_ctx = (
                        f"CURRENT ARTIFACT (from prior run — base to improve):\n"
                        f"--- BEGIN ARTIFACT ---\n{_seed_text}\n--- END ARTIFACT ---\n\n"
                        if _seed_text else ""
                    )
                    desc = (
                        f"You are the reducer in a multi-worker research pipeline.\n"
                        f"You are an ADDITIVE MERGER, never a rewriter (DESIGN §11.3).\n\n"
                        f"Workers searched the web and produced RESEARCH_FINDING blocks above "
                        f"(each has ARTICLE_TITLE / URL / POPULARITY / PATCH_TARGET fields).\n\n"
                        f"ABSOLUTE RULES — violating these REGRESSES the deliverable:\n"
                        f"  - PRESERVE every existing section of the CURRENT ARTIFACT VERBATIM.\n"
                        f"    Do NOT summarize, shorten, condense, re-word, or remove anything.\n"
                        f"  - You may ONLY ADD content that comes from a worker's RESEARCH_FINDING\n"
                        f"    (with its URL). No finding for a section → leave that section exactly\n"
                        f"    as-is.\n"
                        f"  - If workers found NOTHING (no RESEARCH_FINDING blocks above), output\n"
                        f"    the CURRENT ARTIFACT completely unchanged. Never write a 'blocker' or\n"
                        f"    'search unavailable' report — that is failure-narration, not content.\n\n"
                        f"How to apply each RESEARCH_FINDING:\n"
                        f"  1. Find PATCH_TARGET in the artifact.\n"
                        f"  2. URL missing inline → add it next to the citation.\n"
                        f"  3. POPULARITY missing → add it in parentheses.\n"
                        f"  4. New article → add a summary paragraph + a References entry with the URL.\n\n"
                        f"Output: the CURRENT ARTIFACT with additions applied — every original\n"
                        f"section intact, output length STRICTLY >= the input length (a shorter\n"
                        f"output is rejected and the prior good doc is kept).\n\n"
                        f"{_art_ctx}"
                        f"Workflow instruction: {desc}"
                    )
                else:
                    desc = (
                        f"You are the final step of a multi-step agent workflow. "
                        f"The prior steps have already produced the following output. "
                        f"Your job: return the complete, final artifact exactly as produced "
                        f"by the prior steps (optionally refining it). "
                        f"Do NOT ask for more context or input — all necessary work is already done.\n\n"
                        f"Workflow instruction: {desc}"
                    )
            sub_step = replace(
                step, description=_with_upstream(desc, upstream), depends_on=()
            )
            sub_plan = Plan(task=plan_obj.task, steps=(sub_step,))

            # M9: inject hub CoT prompt when loop_config active and phase fans out.
            # The step description becomes the hub's system prompt inside run_plan.
            # STAR/MAP ONLY — by design: these are the section-partition fan-outs
            # the hub plans an ASSIGNED block for. MESH (debate) and PIPELINE
            # (ordered stages) have no section partition, so they skip the hub
            # CoT (no sizing/assignment features). Their breadth is still bounded
            # — run_plan's max_agents caps _facets/PIPELINE stages regardless.
            if _lc is not None and topo in (STAR, MAP):
                _hub_art_text = ""
                if _eff_ws2 is not None:
                    _hub_art_file = _eff_ws2 / session.session_id / "artifact.md"
                    if _hub_art_file.exists():
                        _hub_art_text = _hub_art_file.read_text()[:3000]
                _hub_wk_lines = "\n".join(
                    f"- {w}" for w in getattr(session, "weaknesses", []) or []
                )
                # §11.10: frame the STAR spokes as EXECUTORS, not planning hubs.
                # The old _build_hub_cot_prompt made every spoke "the planning hub"
                # → they emitted TASK_LIST/ASSIGNED (plans) instead of fetching, so
                # the reducer got analysis, the artifact gained no sources, and the
                # score stalled. Execute-and-emit-RESEARCH_FINDING framing instead.
                _executor_desc = _build_executor_prompt(
                    goal=plan_obj.task or requirement,
                    artifact_text=_hub_art_text,
                    weaknesses_block=_hub_wk_lines,
                )
                sub_step = replace(
                    sub_step,
                    description=_executor_desc + "\n\n" + sub_step.description,
                )
                sub_plan = replace(sub_plan, steps=(sub_step,))

            # M8: dynamic worker count from LoopConfig sizing (DESIGN §3).
            # TWO DISTINCT LEVERS (the 2026-06-27 gap-flood fix):
            #   _max_workers  → concurrency (how many spokes run at once),
            #                   derived from the remaining-task count.
            #   _max_agents   → breadth   (how many spokes EXIST), the raw
            #                   max_agents slider. Passing only _max_workers
            #                   capped concurrency while STAR/MAP still spawned
            #                   18 spokes; run_plan now clamps the COUNT too.
            _max_workers = 4
            _max_agents = None
            if _sizing_cfg is not None:
                from agentkit.topology.sizing import compute_n_agents
                _n_remaining = max(1, len(_ledger.remaining()))
                _max_workers = compute_n_agents(_n_remaining, _sizing_cfg)
                _max_agents = _sizing_cfg.max_agents

            # Collision guard (DESIGN §3.1): mark this phase in-flight before it
            # runs so remaining() excludes it; mark_done() clears it after. Keeps
            # the same task from appearing as "remaining" while it is executing.
            _ledger.mark_in_flight(step.id)

            # §4.5: under hill-climb, inject the section-aware reducer so the STAR
            # reduce step merges/refines/reviews worker output into the current
            # artifact (vs the generic synthesis). Read the running artifact fresh
            # each phase — it is the section-keyed handoff from the prior phase.
            _reducer = None
            if _artifact_copied and _eff_ws2 is not None:
                _cur_art = ""
                try:
                    _cur_art = _strip_preamble(
                        (_eff_ws2 / session.session_id / "artifact.md").read_text()
                    )
                except OSError:
                    pass
                _reducer = _make_section_reducer(
                    client, _cur_art, getattr(session, "weaknesses", []) or [],
                    embedder=self._embedder,   # F1: dedup near-duplicate findings
                )

            try:
                result = run_plan(
                    sub_plan, client, budget=budget,
                    max_workers=_max_workers, max_agents=_max_agents,
                    reducer=_reducer,
                )
            except BudgetExceeded as exc:
                self._emit(
                    BudgetEvent(spent=exc.spent, ceiling=session.budget_ceiling, exceeded=True)
                )
                cancelled = True
                break

            sr = result.runs[0]
            outputs[step.id] = sr.output
            final_output = sr.output

            # §11.6: if the search tool itself failed for this whole phase, surface
            # it as a visible gate check — a broken-search run must not masquerade
            # as a finished one (and the anti-regression guard keeps the seed).
            if _phase_search_failed([sr.output]):
                _sf_gate = GateEvent(
                    name="search-availability",
                    outcome="fail",
                    detail="Search tool failed for this phase (SEARCH: error, no findings); "
                           "deliverable left unchanged (no regression).",
                )
                gate_events.append(_sf_gate)
                self._emit(_sf_gate)

            # R2 enforcement: validate the hub's ASSIGNED block in CODE (not just
            # prompt). Parse agent→sections, detect any section claimed by >1
            # agent, and deterministically reassign (first-claim-wins). Surface
            # the result as a gate check so violations are visible, not silent.
            _assigned = _parse_assigned(sr.output)
            if _assigned:
                _clean, _overlaps = _dedupe_assignment(_assigned)
                if _overlaps:
                    _gate = GateEvent(
                        name="worker-assignment",
                        outcome="warn",
                        detail=(
                            f"{len(_overlaps)} section(s) assigned to >1 agent: "
                            f"{', '.join(_overlaps[:8])}"
                            f"{'…' if len(_overlaps) > 8 else ''}. "
                            f"Deterministically reassigned first-claim-wins."
                        ),
                    )
                else:
                    _gate = GateEvent(
                        name="worker-assignment",
                        outcome="pass",
                        detail="Section partition non-overlapping (1 section ≤ 1 agent).",
                    )
                gate_events.append(_gate)
                self._emit(_gate)

            # When the reducer received the seeded artifact as context and produced
            # an improved version, write it back to artifact.md so the scorer and
            # the next epoch both see the improved content. Anti-regression guard:
            # only overwrite if the new output is >5000 chars AND not shorter than
            # the seed — a thin/failed run must keep the prior good doc, never
            # shrink it (worst case = no improvement, never regression).
            # §4.5: write EVERY phase's reduced output back (was last-phase only),
            # so artifact.md is the running section-keyed handoff to the next
            # phase. Grow-only ratchet: only overwrite if not shorter than the
            # current artifact, then raise _seed_len — the doc grows monotonically
            # across phases AND epochs, never regresses (a thin/failed reduce keeps
            # the prior good doc).
            _clean_out = _strip_preamble(sr.output)  # never persist reducer commentary
            # F2: per-section ratchet. The old whole-doc grow-only rule (len >= _seed_len)
            # rejected ANY shrink, blocking dedup/replace/repair. accept_rewrite allows a
            # rewrite (even shorter) as long as no section that had CONTENT is deleted or
            # gutted — preserving anti-regression at section granularity.
            _art_path = (_eff_ws2 / session.session_id / "artifact.md") if _eff_ws2 is not None else None
            _old_art = ""
            if _art_path is not None:
                try:
                    _old_art = _art_path.read_text()
                except OSError:
                    pass
            from agentkit.artifacts.sections import accept_rewrite
            if (_artifact_copied and _art_path is not None
                    and len(_clean_out.strip()) > 0
                    and accept_rewrite(_old_art, _clean_out)):
                try:
                    _art_path.write_text(_clean_out)
                    _dbg(f"writeback ACCEPT step={step.id} {len(_old_art)}→{len(_clean_out)}")
                    _seed_len = len(_clean_out)   # track current length for the next phase
                except OSError:
                    pass
            elif _artifact_copied and _art_path is not None:
                _dbg(f"writeback REJECT step={step.id} clean_len={len(_clean_out)} "
                     f"(accept_rewrite: a sourced section was deleted/gutted)")

            # Reconcile tokens run_plan counted that on_usage did not capture.
            # A StudioChatClient fires on_usage per call (with the in/out split);
            # a raw/CLI client does not, so its tokens only surface in
            # StepRun.tokens. Emit a per-phase frame for any remainder so the HUD
            # always reconciles to DynamicPlanResult.total_tokens (SPEC §8 M3).
            self._reconcile_phase_tokens(step.id, sr.tokens)

            self._emit(
                PhaseDoneEvent(
                    step_id=step.id,
                    topology=sr.topology,
                    n_agents=sr.n_agents,
                    tokens=sr.tokens,
                    wall_s=sr.wall_s,
                    output=sr.output,
                )
            )

            # M8: record this phase as completed (all_tasks was seeded up front,
            # so mark_done moves it from remaining/in-flight to completed).
            _ledger.mark_done(step.id)

            # post-phase panels
            mem.record(step.id, sr.output)
            dag.mark_done(step.id, tokens=sr.tokens)
            self._emit(dag.snapshot())
            self._emit(
                selfimp.assess_phase(
                    produced_output=bool(sr.output.strip()), metric=float(sr.tokens)
                )
            )
            self._emit(build_evolve_event(len(outputs), list(outputs.values())))
            gate_event = self._gate_event_for(step.id, sr.output)
            gate_events.append(gate_event)
            self._emit(gate_event)

            # Goal check: if session has a LoopGoal, verify after each phase.
            if getattr(session, 'goal', None) is not None:
                try:
                    from agentkit.loop.goal import check_goal
                    _verdict = check_goal(session.goal, cwd=self._sandbox_cwd)
                    if _verdict.met:
                        self._emit(GoalMetEvent(
                            end_state=session.goal.end_state,
                            evidence=_verdict.evidence,
                            reason=_verdict.reason,
                            step_id=step.id,
                        ))
                        break
                except Exception:  # noqa: BLE001
                    pass  # agentkit.loop not installed → skip silently

        # M8: if any worker output contained PATCHES blocks, apply them atomically
        # via reduce_patches() + write_artifact() (DESIGN §2.2).  Workers that
        # emit RESEARCH_FINDING text instead produce no patches — the existing
        # reducer prompt path runs unchanged for those phases.
        if _eff_ws2 is not None and outputs:
            _patch_groups: list[list] = []
            for _out in outputs.values():
                # §11.3: PATCHES from workers, plus RESEARCH_FINDING blocks
                # converted to additive patches — both merge programmatically so
                # the document is never re-emitted (and never shortened) by an LLM.
                _patches = _parse_patches_from_output(_out) + _research_findings_to_patches(_out)
                if _patches:
                    _patch_groups.append(_patches)
            if _patch_groups:
                from agentkit.artifacts.patcher import reduce_patches, write_artifact
                _art_file = _eff_ws2 / session.session_id / "artifact.md"
                _cur_text = _art_file.read_text() if _art_file.exists() else ""
                # Phase 2 (DESIGN §2.2 Step 5): a full-document editorial refine pass
                # over the structurally-merged text. Only with a real LLM (mode=='llm');
                # offline/canned backends would corrupt the artifact, so refine is None.
                # The closure guards output length so a short/confused response cannot
                # clobber a clean merge (mirrors the >5000-char guard above).
                _refine_fn = None
                if use_llm:
                    _refine_goal = plan_obj.task or requirement
                    _refine_path = str(_art_file)

                    def _refine_fn(merged_text: str) -> str:
                        prompt = (
                            _build_reducer_refine_prompt(_refine_goal, _refine_path)
                            + merged_text
                            + "\n--- END MERGED DOCUMENT ---\n"
                        )
                        res = base_client.chat([{"role": "user", "content": prompt}])
                        out = (getattr(res, "text", "") or "").strip()
                        # Reject truncated/empty refinements: keep the clean merge.
                        if len(out) >= int(len(merged_text) * 0.8):
                            return out
                        return merged_text

                _rr = reduce_patches(_cur_text, _patch_groups, llm_refine_fn=_refine_fn)
                # Anti-regression guard: never replace the seed with a shorter
                # merged doc (worst case = no improvement, never regression).
                if _rr.text and len(_rr.text) >= _seed_len:
                    write_artifact(_art_file, _rr.text)
                # §11.4 gap routing: detect empty/placeholder sections, then
                # CONSOLIDATE by top-level section before routing so a noisy gap
                # list can't inflate agent sizing (2026-06-27 fix). Non-last phase
                # → hand the distinct sections to the next phase via the ledger
                # (one bounded task per section); last phase → carry gap messages
                # to the next run as weaknesses.
                _gaps = _detect_gaps(_rr.text or "")
                # §11.4 closure: a repeat-failure (recorded in >= REPEAT_LIMIT prior
                # runs) is NEVER dropped or hidden. It flows normally through handoff
                # so the LAST phase gets a final attempt; whatever is still open after
                # the run is surfaced to the user below the result (see run end). We
                # only TRACK them here for that report — _repeat_failed is computed at
                # run start and consumed at run end.
                if _gaps and not is_last:
                    for _si, _sec in enumerate(_gap_sections(_gaps)):
                        _ledger.add_task(TaskRecord(
                            id=f"gap-{step.id}-{_si}",
                            description=f"Revise/source section: {_sec}",
                        ))
                elif _gaps:  # last phase → carry to next run as SECTION-bound weaknesses
                    # Keep the section label so next run routes each weakness to the
                    # agent that owns that section (an agent can't patch unassigned
                    # sections — DESIGN §11.4).
                    _reducer_gaps.extend(f"[{_sec}] {_m}" for _sec, _m in _gaps)

        # budget gauge
        if budget is not None:
            self._emit(
                BudgetEvent(
                    spent=budget.spent_total,
                    ceiling=session.budget_ceiling,
                    exceeded=False,
                )
            )

        # If the final step produced less than its direct predecessor, fall back
        # to the predecessor's output. In research loops, the last step is a
        # meta "stop/continue" decision — the real artifact lives in the step it
        # depends on (its direct predecessor in the DAG).
        last_step = plan_obj.steps[-1] if plan_obj.steps else None
        predecessor_id = (
            last_step.depends_on[-1] if (last_step and last_step.depends_on) else None
        )
        predecessor_output = outputs.get(predecessor_id, "") if predecessor_id else ""
        result_output = (
            predecessor_output
            if predecessor_output and len(predecessor_output) > len(final_output)
            else final_output
        )

        # Steps that write their artifact to artifact.md produce content in a file
        # rather than the LLM text response, so prefer the file — BUT only when it is
        # complete. Auto-improve copies the prior best artifact into the workspace as a
        # seed; if the agent doesn't overwrite it, the file is a STALE (and here,
        # truncated) seed. The old "prefer the longest text" rule then re-kept that
        # truncated seed over the agent's fresh, complete-but-shorter synthesis — every
        # iteration re-scored the same truncated text and the score could never climb
        # past the "not truncated" criterion. Fix: only prefer the file when it is longer
        # AND ends cleanly; a truncated file loses to the agent's actual final output.
        ws_artifact = self._read_workspace_artifact()
        if ws_artifact and len(ws_artifact) > len(result_output) and _ends_cleanly(ws_artifact):
            result_output = ws_artifact

        # §11.10: strip any reducer commentary preamble from the DISPLAYED/stored
        # result too — not just artifact.md. A reducer that narrated ("The artifact
        # is complete… Weaknesses addressed: ✅… Remaining concern: future-dated…")
        # leaves that in result_output even when artifact.md was sanitized, since
        # the preamble version is longer and wins the length check above. That
        # commentary belongs in the surfaced _unresolved_block (chat), never in the
        # deliverable shown to the user.
        result_output = _strip_preamble(result_output)

        # verification (pure tier, always runs)
        verify_event = build_verify_event(result_output)
        self._emit(verify_event)

        # Loop Doctor (M8): audit the finished run against loop-library's
        # checklist, composed from the run's collected gate/verify outcomes +
        # the budget ceiling + the plan DAG. Suggestions only — never applied.
        loopdoctor_event = build_loopdoctor_event(
            plan_step_dicts,
            budget_ceiling=session.budget_ceiling,
            gate_events=gate_events,
            verify_event=verify_event,
        )
        self._emit(loopdoctor_event)

        # Record the finished run so GET /export can serialize it to a loop (M9).
        session.record_run(
            RunSnapshot(
                requirement=requirement,
                plan_steps=plan_step_dicts,
                topology=topology_map,
                loopdoctor_checks=loopdoctor_event.checks,
                budget_ceiling=session.budget_ceiling,
                result=result_output,
                cancelled=cancelled,
            )
        )

        # Hill climb post-run: score output, mine weaknesses, record, emit HillClimbEvent.
        # Runs regardless of hill_climb_config so task_hash-based lookup always has data.
        try:
            from studio.task_runs import (
                TaskRun,
                TaskRunStore,
                mine_weaknesses_from_outputs,
                score_result,
                task_hash as _task_hash,
            )
            # Embedder wired so this run's requirement is embedded on record()
            # → future runs can find it via similar_runs() (R10).
            _store = TaskRunStore(embedder=self._embedder)
            _thash = _task_hash(_original_requirement)
            from studio.workspace import workspace_root as _ws_root_fn
            _effective_ws_root = self._workspace_root or _ws_root_fn()
            _art_file = _effective_ws_root / session.session_id / "artifact.md"
            _art_path = str(_art_file)
            # Score the PERSISTED artifact, not the loose result_output. The final phase's
            # returned text and the artifact.md it wrote to disk can diverge (a phase may
            # return a short status string while the full report lives in the file). Since
            # auto-improve seeds the NEXT run from artifact.md, scoring anything else means
            # scoring one text and carrying forward another — the cause of phantom scores
            # (e.g. a recorded 0.50 on a report that re-scores 0.80). Prefer the file when
            # it is at least as substantial as the return; fall back to result_output.
            _scored_text = result_output
            try:
                if _art_file.exists():
                    _file_text = _art_file.read_text()
                    if len(_file_text.strip()) >= len((result_output or "").strip()):
                        _scored_text = _file_text
            except Exception:  # noqa: BLE001 - a read failure must not break recording
                pass
            # Scoring and weakness mining must use the RAW client (base_client), not the
            # ToolAugmentedClient. When the scorer has web_search available, it calls it
            # to verify citations — fabricated or paywalled articles score 0.0 even when
            # the output quality is genuinely good. The scorer is an LLM judge, not a
            # research agent; it must not make live web calls.
            _judge_client = base_client
            # Check scored text URLs against web cache — real (cached) URLs get marked
            # as verified so the judge doesn't penalise genuine citations as fabricated.
            _verified_urls: list[str] = []
            try:
                import json as _json
                import os as _os
                from studio.task_runs import verified_urls_in_cache
                if _os.path.exists(".web_cache.json"):
                    with open(".web_cache.json") as _cf:
                        _verified_urls = verified_urls_in_cache(
                            _json.load(_cf), _scored_text or ""
                        )
            except Exception:  # noqa: BLE001
                pass
            _score, _scorer_feedback = score_result(
                _scored_text, _original_requirement, _judge_client,
                verified_urls=_verified_urls or None,
            )
            # Mine against the full artifact so the miner sees real content (URLs,
            # citations, conclusions). Pass result_output in the outputs dict to let
            # the miner still catch synthesis failures like "workers returned status
            # only". Without this, the miner saw the 3K reducer response instead of
            # the 28K artifact and reported "no URLs" when 8 real URLs already existed.
            _mine_outputs = {k: v for k, v in outputs.items()}
            if result_output:
                _mine_outputs["reducer_response"] = result_output
            _weaknesses = mine_weaknesses_from_outputs(
                _mine_outputs,
                _scored_text,
                _original_requirement,
                _judge_client,
                scorer_feedback=_scorer_feedback,
                verified_urls=_verified_urls or None,  # cache-as-oracle (§11.10)
            )
            # §11.4: prepend the reducer's last-phase gaps — they are concrete and
            # grounded ("§Results: no source URL"), so they make the next run's
            # constraints specific. Dedup against the LLM-mined set.
            if _reducer_gaps:
                _seen_w = set(_weaknesses)
                _weaknesses = [g for g in _reducer_gaps if g not in _seen_w] + _weaknesses
            # §11.4 SCORE = solved / total over the weakness set (deterministic;
            # overrides the noisy LLM self-eval, which emitted impossible values
            # like 0.1 and rated good output low). total = prior accumulated UNION
            # still-open; solved = total - still-open. No weakness anywhere => nothing
            # to fix => 1.0. score_result still runs above, but only for the
            # scorer_feedback that mining consumes — its score is discarded here.
            _score = _weakness_score(
                getattr(session, "weaknesses", []) or [], _weaknesses,
                embedder=self._embedder,
            )
            # §11.4 surface (never hide): a repeat-failure (>= REPEAT_LIMIT prior runs)
            # that is STILL recorded this run was attempted again — including the last
            # phase — and remains open. Append it visibly below the result shown in the
            # chat window so the user knows what could not be resolved, instead of
            # silently dropping it.
            if _repeat_failed:
                from studio.task_runs import REPEAT_LIMIT
                result_output = (result_output or "").rstrip() + _unresolved_block(
                    _weaknesses, _repeat_failed, REPEAT_LIMIT
                )
            _version = _store.next_version(_thash)
            _store.record(
                TaskRun(
                    task_hash=_thash,
                    session_id=session.session_id,
                    version=_version,
                    score=_score,
                    weaknesses=_weaknesses,
                    artifact_path=_art_path,
                    requirement=_original_requirement,
                    result_text=result_output,
                )
            )
            # Template reuse: save a decent report's heading SKELETON so the next
            # semantically-similar research can seed its first document from it (best-effort;
            # dedups identical skeletons; needs an embedder to be searchable).
            if _score >= 0.6 and self._embedder is not None:
                try:
                    from studio.templates import TemplateStore, extract_skeleton
                    TemplateStore(embedder=self._embedder).save_template(
                        _original_requirement, extract_skeleton(result_output))
                except Exception:  # noqa: BLE001 — template save is non-critical
                    pass
            _prev_score = 0.0
            if _version > 1:
                _prev = _store.all_runs(_thash)
                if len(_prev) >= 2:
                    _prev_score = _prev[-2].score
            _delta = _score - _prev_score
            _hc_cfg2 = getattr(session, "hill_climb_config", None) or {}
            _min_delta = float(_hc_cfg2.get("min_improvement", 0.02))
            _max_epochs = int(_hc_cfg2.get("max_epochs", 5))
            if _version >= _max_epochs:
                _status = "converged"
            elif _version > 1 and _delta < _min_delta:
                _status = "plateau"
            else:
                _status = "improving"
            self._emit(
                HillClimbEvent(
                    epoch=_version,
                    score=_score,
                    delta=_delta,
                    status=_status,
                    note=f"v{_version} score={_score:.2f}",
                    weaknesses=_weaknesses,
                    task_hash=_thash,
                )
            )
        except Exception:  # noqa: BLE001 — scoring failure must never crash the run
            pass

        # done
        self._emit(self._done_event(result_output, cancelled=cancelled))

    # -- helpers -----------------------------------------------------------

    def _build_client(self) -> LLMClient:
        """Build the run's LLMClient — injected factory in tests, else from spec."""
        if self._client_factory is not None:
            return self._client_factory(self._on_usage)
        backend = resolve_backend(self._session.llm_spec)
        # session info may be filled lazily; ensure label/model present
        return build_chat_client(backend, self._on_usage)

    def _maybe_tool_augment(
        self, client: LLMClient, *, artifact_path: Path | None = None
    ) -> LLMClient:
        """Wrap ``client`` in the tool loop (web_search + jailed file tools) when
        tools are enabled.

        Gated on ``session.tools_enabled`` AND web_toolkit being importable; in
        tests an injected ``search_fn`` (set via ``self._search_fn``) bypasses the
        import so no network is hit. The file tools are confined to a per-session
        :class:`~studio.workspace.Workspace` (realpath jail). When ``artifact_path``
        is provided, the artifact OCC tools (read_artifact / patch_artifact) are
        also offered. Returns the bare client when tools are off.
        """
        enabled = self._session.tools_enabled and (
            self._search_fn is not None or web_toolkit_available()
        )
        if not enabled:
            return client
        workspace = Workspace(self._session.session_id, root=self._workspace_root)
        return ToolAugmentedClient(
            client,
            on_tool_call=lambda sid, tool, args: self._emit(
                ToolCallEvent(step_id=sid, tool=tool, args=args)
            ),
            on_tool_result=lambda sid, tool, summary, n, notice, rejected: self._emit(
                ToolResultEvent(
                    step_id=sid,
                    tool=tool,
                    summary=summary,
                    n_results=n,
                    notice=notice,
                    rejected=rejected,
                )
            ),
            step_id_getter=lambda: self._current_step_id,
            search_fn=self._search_fn,
            fetch_fn=self._fetch_fn,
            workspace=workspace,
            artifact_path=artifact_path,
        )

    def _gate_event_for(self, step_id: str, output: str) -> GateEvent:
        """Run the phase output through the security gate as a text proposal."""
        proposal = {"type": "phase_output", "content": output, "description": output[:200]}
        return run_gate_event(f"phase:{step_id}", proposal, cwd=self._sandbox_cwd)

    def _done_event(self, final_output: str, *, cancelled: bool) -> DoneEvent:
        elapsed = time.perf_counter() - self._t0 if self._t0 is not None else 0.0
        return DoneEvent(
            total_tokens=self._acc.total_tokens,
            input=self._acc.total_input_tokens,
            output=self._acc.total_output_tokens,
            estimated=self._acc.tokens_estimated,
            wall_s=elapsed,
            result=final_output,
            cancelled=cancelled,
            result_path=self._write_result(final_output),
        )

    def _read_workspace_artifact(self) -> str:
        """Return content of artifact.md from the workspace if it exists."""
        try:
            ws = Workspace(self._session.session_id, root=self._workspace_root)
            artifact = ws.root / "artifact.md"
            if artifact.exists():
                return artifact.read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _write_result(self, final_output: str) -> str:
        """Save the final result to the session workspace → its absolute path.

        Best-effort: a write failure returns "" (the result still rides in the
        ``done`` event), and an empty result is not written.
        """
        if not final_output.strip():
            return ""
        try:
            ws = Workspace(self._session.session_id, root=self._workspace_root)
            ws.write("result.md", final_output)
            return str(ws.root / "result.md")
        except Exception:  # noqa: BLE001 - saving is auxiliary; never break `done`
            return ""
