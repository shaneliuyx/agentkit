"""studio.prompts — stateless CoT prompt builders and prompt fragments.

Extracted from ``studio.runner`` (SRP): these functions are pure string
constructors with no runner state. They carry the load-bearing DESIGN/PLAN refs
in their docstrings. ``studio.runner`` re-exports every name here so existing
``from studio.runner import _build_*`` imports keep working.
"""

from __future__ import annotations


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
        "  genuinely fit. Derive phase names from the goal itself.\n"
        "  Each phase MUST be DISTINCT — never emit the same phase twice. A goal that\n"
        "  lists several sub-tasks is ONE set of phases, not repeated per sub-task.\n\n"
        "  If the deliverable is a research report, choose the plan depth from the\n"
        "  requested report size, evidence burden, risk, and profile:\n"
        "    - Compact report / brief / under ~1,000 words / low-risk summary: use 1–2\n"
        "      epics, usually evidence gathering followed by concise synthesis.\n"
        "    - Standard report with multiple required sections or source validation:\n"
        "      use 3–4 epics following the methodology: intake/profile, source plan\n"
        "      + retrieval, evidence validation/matrix, assembly + lint/publish.\n"
        "    - Large, high-impact, disputed, regulated, or literature-review report:\n"
        "      use 4–5 epics and include explicit verification, limitations, revision,\n"
        "      and human-review/publish-gate work.\n"
        "  These are planning heuristics, not hardcoded stages: adapt them to the goal.\n\n"
        "Step 3 — For each epic enumerate 6–15 parallel branches.\n"
        "  Branches within an epic run in parallel — no inter-branch dependencies.\n"
        "  Each branch must be completable by one agent with available tools.\n"
        "  For compact or weak-tool report work, fewer branches are acceptable when\n"
        "  extra branches would duplicate searches or exceed the requested report size.\n\n"
        "Step 4 — Account for existing deliverable and weaknesses.\n"
        "  If a deliverable exists: branches must address gaps only, not reconstruct.\n"
        "  If no deliverable: Epic 1 branches should establish the initial structure.\n\n"
        "Step 5 — Declare each epic's TOPOLOGY INTENT (how its branches coordinate):\n"
        "  Choose from the same design principles the runtime selector will use:\n"
        "  - 'single'        : one agent can do the work; task is small, fuzzy, or ordered.\n"
        "  - 'star'          : independent branches can run in parallel, then reduce.\n"
        "  - 'map'           : one operation per item over an upstream list ('each'/'every').\n"
        "  - 'mesh'          : branches must DEBATE / compare / critique alternatives.\n"
        "  - 'pipeline'      : strictly ordered stages where each consumes the prior one.\n"
        "  - 'gateway'       : route among identities/tools/permissions before work starts.\n"
        "  - 'durable_board' : restart recovery, cross-session state, queueing, or human review.\n"
        "  - 'tree'          : hierarchical manager-to-leaf decomposition.\n"
        "  This is an INTENT. A later LLM topology selector will make the final choice\n"
        "  and explain the rationale for each phase.\n\n"
        "Step 6 — Emit the plan:\n\n"
        "EPIC_PLAN:\n"
        "```json\n"
        '{"epics": [{"id": "epic-1", "title": "...", "description": "...",\n'
        '  "topology": "star", "depends_on": [],\n'
        '  "branches": [{"id": "b-1a", "description": "..."}]}]}\n'
        "```\n"
    )


def _build_executor_prompt(goal: str, artifact_text: str, weaknesses_block: str) -> str:
    """STAR-spoke EXECUTOR prompt (§11.10 + PLAN P2 — goal-blind worker).

    The spokes were given the HUB planning prompt ("you are the planning hub …
    assign work … emit TASK_LIST/ASSIGNED"), so they PLANNED instead of fetching:
    the reducer received analysis, the artifact never gained sourced content, and
    the score stalled. This frames each spoke as a research EXECUTOR — fetch real
    pages and emit RESEARCH_FINDING blocks WITH their URLs — never a planner.

    Research workers are goal-bounded, not goal-blind: they need the task goal to
    form relevant search queries and judge whether a fetched page belongs in the
    report, but they still execute only the assigned focus appended by the caller.
    """
    art = (artifact_text or "").strip()
    art_block = (
        f"CURRENT DELIVERABLE (improve it; do not restate a plan):\n{art[:3000]}\n\n"
        if art else ""
    )
    return (
        _today_note()
        + "You are a RESEARCH EXECUTOR, not a planner. DO the research NOW — do NOT "
        "emit TASK_LIST, ASSIGNED, or any plan/assignment. Execute ONLY your assigned "
        "task below (do not broaden it).\n\n"
        f"TASK GOAL (use only for search relevance and WHY; do not rewrite it as a report):\n"
        f"{(goal or '').strip()}\n\n"
        f"{art_block}"
        f"WEAKNESSES TO FIX (concrete gaps):\n{weaknesses_block or '(none)'}\n\n"
        "Step 1 — Build each search query from TASK GOAL + your assigned focus + the "
        "target section. Do not search an ambiguous fragment by itself.\n"
        "Step 2 — If weaknesses are listed, fix those; if weaknesses are '(none)', "
        "fill pending assigned sections with relevant sourced evidence for the assigned "
        "focus. Run web_search THEN web_fetch to read the ACTUAL article content "
        "(not just the snippet).\n"
        "Step 3 — Emit one RESEARCH_FINDING per page you fetched, exactly:\n"
        "  RESEARCH_FINDING:\n"
        "  ARTICLE_TITLE: <title>\n"
        "  URL: <the EXACT url you fetched — REQUIRED, never omit>\n"
        "  POPULARITY: <metric if stated, else n/a>\n"
        "  PATCH_TARGET: <the '## Section' heading this improves>\n"
        "  QUOTE: <COPY-PASTE one sentence EXACTLY from the fetched page — character "
        "for character, no paraphrase, no edits, no ellipsis. This verbatim text IS "
        "the evidence; do not summarize or restate it.>\n"
        "  WHY: <your OWN synthesis of what this quote means for the GOAL — go beyond "
        "restating the quote; interpret it, one to two sentences in your words>\n\n"
        "Citing without substantiating is the #1 failure: a bare URL is NOT enough. "
        "COPY the QUOTE verbatim from the page — do NOT restate the article in your own "
        "words, PASTE its words. The quote is checked against the fetched page (a "
        "fabricated one is dropped). WHY is different from the quote: never just "
        "restate what it already says — add your interpretation, so what, or "
        "implication for the GOAL. If this quote appears to CONTRADICT another claim "
        "already in the document, say so in WHY instead of leaving the disagreement "
        "unaddressed.\n"
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
