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
        "Step 5 — Emit the plan:\n\n"
        "EPIC_PLAN:\n"
        "```json\n"
        '{"epics": [{"id": "epic-1", "title": "...", "description": "...",\n'
        '  "depends_on": [], "branches": [{"id": "b-1a", "description": "..."}]}]}\n'
        "```\n"
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


def _build_skeleton(goal: str, client=None, embedder=None) -> str:
    """Build the initial document skeleton — a FIXED high-level, topic-agnostic ToC
    (DESIGN §14.1 / §14.2).

    Deliberately GENERIC: the same standard research-report sections (``DEFAULT_TEMPLATE``)
    for every goal, so the report's topic is generated by its CONTENT — the workers'
    grounded findings — not named by the template. The title is a placeholder the reducer
    fills from those findings, NEVER the goal text. The old behavior derived goal-specific
    headings and, via semantic template-reuse, seeded a prior topic's headings (e.g.
    loop-engineering) into an unrelated report — the drift this removes. Headings +
    placeholder bodies only: NO search/LLM needed, so it is robust to an outage, and
    workers fill each section additively (create == improve). ``goal``/``client``/
    ``embedder`` are accepted for call-site compatibility; only the structure is used.
    """
    from studio.rubric import DEFAULT_TEMPLATE

    body = "".join(
        f"## {section}\n_(pending — needs sourced content)_\n\n"
        for section in DEFAULT_TEMPLATE
    )
    return "# _(report title — generated from the findings below)_\n\n" + body.rstrip() + "\n"
