# HANDOFF — Dynamic sections, auto-mode depth passes, synthesis guards

**Date:** 2026-07-05
**Branch:** `build-research-report-generator-plan`, **~37 commits ahead of origin, NOT pushed.**
**Tests:** 815 backend passed, 4 deselected (full suite green at `684c040`).
**One run IN FLIGHT at handoff time** — see "The in-flight measurement run" below.
Companion docs: `PLAN-CONSOLIDATED.md` (master requirement/design/code reconciliation + backlog,
written this session — read it before trusting any other doc's status claims) and
`ARCHITECTURE-doc-generation-pipeline.md` §3/§5/§7/§9.5 (re-traced 2026-07-04).

---

## What landed this session (chronological, all committed)

1. **`2e2979f` — LLM timeout 90s→240s + `max_retries=0` + hybrid planning.**
   Root-caused a "stalled" haiku run to retry multiplication: openai-python's internal 2 retries ×
   our 7-attempt ladder = 21 × 90s socket attempts per call, while honest full-budget generations
   (~73s decode at measured 112 tok/s + prefill) legitimately cross 90s. Diagnosis chain worth
   reusing: proxy-connection port-cadence (~90s per source port proved the timeout WAS firing) →
   `sudo py-spy dump` → silent-socket repro. Planning now runs on the judge client
   (`judge_llm` spec, default haiku) — `runner.py:2198`.

2. **`00983b9` — deterministic context compaction on every studio LLM call.**
   `compact_messages` at the top of `StudioChatClient.chat` (the single choke point). Uses
   `agentkit.context.compactor` (existed, previously imported by NOTHING). The compactor's own cut
   keys on user turns and no-ops on tool loops, so the wrapper does its own pair-safe cut: system +
   first user (assignment) verbatim, 50K verbatim tail (recent fetched pages stay quotable), head
   summarized. Thresholds: `STUDIO_COMPACT_MAX_CHARS` 100K / `STUDIO_COMPACT_TAIL_CHARS` 50K.

3. **`8d7f89e` + `b2f6888` — Workstream P: planner-reviewed requirement-driven dynamic sections**
   (design in the big PLAN, implementation same day). The template is only the STARTING outline:
   `requirement_section_decisions` (planning.py) extracts deliverable-shaped asks deterministically
   (`_FORM_DELIVERABLE_RE` — 0 LLM calls when none), one planner review call decides
   existing/new_section/subsection per deliverable (strict JSON, concept-token fallback, fail-open),
   `apply_section_decisions` grows `rc["active_template"]` ONLY (scoring_template/matrix untouched —
   the no-moving-target contract, frozen-baseline regression test included). Subsections render as
   `###` placeholders in the parent's skeleton body. **Live-verified**: planner routed
   'example code' → `### Code Implementation Examples` and 'design architecture' →
   `### System Architecture Design` under Evidence and Analysis; run 1529 scored 0.97 with the
   injected sections filled (grounded, cited).

4. **`468e6b1` — depth passes un-gated from `use_llm`** (third/fourth instances of the mode-gate
   disease). `_synthesize_analysis` and `expand_underdeveloped_sections` — the designed solvers for
   the two stuck rubric rows (Evidence synthesis 2.5/14.7, Analytical depth 1.8/10.5 on every
   auto-mode run) — were dead in `mode="auto"`.

5. **`976a694` + `8406996` — `PLAN-CONSOLIDATED.md`.** Full sweep of all ~20 planning docs (4
   parallel extraction agents) + code verification: requirement status matrix (M7–M9 are BUILT
   despite SPEC; Workstream O is half-built despite "no slices"; B is live under
   `report_quality.py`), 16 corrected stale claims, prioritized backlog P0–P6, settled-negatives
   list, doc-disposition table. Also reconciled 4 trashed `~/.Trash/HANDOFF-*` files — one live
   recovery: **strong-model reducer** (backlog P0-2b) was never built.

6. **`3b0c300` — report-title word cap fix.** The 14-word cap chopped "…create a research report"
   to "…create a research" AFTER the stop-anchor had produced a clean 15-word topic. Cap now 18 +
   a cut never ends on a dangling connective. (An old test literally asserted the truncation as
   expected — updated.)

7. **`c5e6030` — pending `###` sub-sections now have a responsible worker.** Live finding:
   planner-injected sub-sections appeared in ZERO spoke inputs (assignment rows carry section
   TITLES; the `###` placeholders live in file BODIES) — run 1529's fills were the reducer folding
   findings under those anchors by accident. `section_workspace.pending_subsections()` +
   `build_section_assignment_rows(subsections=...)` + runner wiring: the owning row's assignment
   now names each pending sub-heading as REQUIRED SUB-SECTIONS. Generic (any future `###` source).

8. **`684c040` — synthesis pass accepts only structural rewrites, never meta-prose.** Run 1530
   (FIRST run with the un-gated synthesize pass) collapsed 0.97 → 0.087: gemma answered ABOUT the
   task ("Once you provide the text… I will immediately transform it…"), the reply QUOTED heading
   names, the URL/length guards were vacuous on a citation-free window (∅ == ∅), and the quoted
   headings became fake sections after References → 15 lints + compliance fail + every downstream
   pass rejecting its candidates against the poisoned baseline. Fix: two STRUCTURAL guards in
   `_synthesize_block` — (a) a rewrite must never invent a heading line; (b) a rewrite must retain
   ≥30% of the draft's content vocabulary (`_content_word_overlap`, headings excluded from credit).
   **A refusal-phrase regex was explicitly rejected by the user (no-hardcoding principle)** — do
   not reintroduce one; structural properties only.

## The in-flight measurement run (P0-1)

Launched at handoff: cold Pi/Craft run on backend17 (all fixes above live), background task
`bccj5txi7`, experiment driver `scratchpad/experiment_pi_craft.py`, diag at `scratchpad/diag17.log`.
This is the REAL depth measurement — run 1530's attempt measured an unguarded synthesis pass.

**Score ladder to compare against** (all cold starts, same requirement, task_hash `492bae60177b`):

| Run | Config delta | Score | Notes |
|---|---|---|---|
| 1527 | through auto-fetch | 0.6083 | 1106 words, 1 mermaid, 0 code |
| 1528 | + hybrid haiku planning | 0.582 | proves deliverable gap was structural, not model |
| 1529 | + Workstream P sections | 0.97 | sections filled BUT prose-about-code, 0 fences (inflated) |
| 1530 | + depth passes UNGUARDED | 0.087 | refusal-prose corruption (now fixed) |
| next | + synthesis guards etc. | ? | ← the number that matters |

**Readouts:** Evidence-synthesis / Analytical-depth rubric rows (were 2.5/14.7 and 1.8/10.5);
fenced code in `### Code Implementation Examples`; mermaid survival; References stays last;
H1 says "…research report" (title fix); whether workers (not just the reducer) touch the
sub-sections (grep spoke `io/*.in.md` for "REQUIRED SUB-SECTIONS").

**Lineage hygiene:** runs 1526–1530 rows were backed up then DELETED for cold-start comparability —
backups in scratchpad (`task_runs_backup_492bae60177b.json`, `_1529.json`, `_1530.json`). The
in-flight run will re-record; delete its row too (after backup) if another cold start is needed.
NEVER leave a corrupted row as the latest — `latest_with_content` will seed the next run from it.

## Next steps (priority order — mirrors PLAN-CONSOLIDATED §3)

1. **Read the in-flight run's result** (task `bccj5txi7` output, per-run stats printed by driver).
   If depth rows still stuck → P0-2 (relax findings.py one-sentence contract) and/or P0-2b
   (strong-model reducer on the judge client — same pattern as `runner.py:2198`).
2. **Code-shaped compliance downgrade** (backlog P2-8b): "include example code" scored SATISFIED on
   prose describing code, zero fences, while evidence/ held actual `.ts` source. Mirror
   `_DIAGRAM_SHAPED_RE`/`_MERMAID_BLOCK_RE` in `requirement_compliance.py` for code-shaped branches.
3. **Codex review** of this session's ~10 commits (user's standing workflow loop — not yet done).
4. **Push** — 37 commits ahead; user's call.
5. Presentation ladder Phases 1–2 (classifier wiring, table/list generators, frontend judge
   selector) — PLAN-content-and-shallowness-execution.

## Gotchas that cost time this session (avoid repeating)

- **Restart the backend after every backend edit** — uvicorn has no --reload here. Every fix wave
  used a new `OMC_THROUGHPUT_DEBUG` diag file (`diag12…diag17.log` in scratchpad). Without that env
  var, `_dbg()` is a no-op and stage-level diagnosis is blind.
- **`ls` is hook-rewritten to `rtk ls`** — its tree output breaks `$(ls -t … | head -1)` command
  substitution. Use absolute paths / `find`.
- **cwd drifts across tool calls** (git runs from repo root, backend cmds from backend/) — always
  `cd` explicitly or use absolute paths; `tmp/task_runs.db` relative from the wrong cwd silently
  creates an empty DB.
- **Drain SSE fully** in any driver script or the run never records (established; still true).
- **A 0.00s stage timer = swallowed exception, not a fast stage** (fail-open `except` disease).
  `uvx ruff check --select F821` finds extraction-scope NameErrors cheaply.
- **The experiment driver queries `max(id)`** in task_runs.db — concurrent runs would misattribute.
- **VibeProxy (haiku) is on `:8317`, oMLX on `:8000`, backend on `:8770`, SearXNG on `:8080`.**

## Standing invariants (do not relax)

- Scoring baseline frozen per run (`scoring_template`/`scoring_matrix`); dynamic sections land on
  `active_template` only — no moving target.
- Reducer patch contract stays prose-only (structural content = editor/presentation passes only).
- Goal-blind spokes: requirement/compliance notices go to the reducer, never spoke prompts.
  (Workstream P's REQUIRED SUB-SECTIONS clause names section-local work, not the goal — compatible.)
- No hardcoded phrase lists in production guards — structural properties only (user rule,
  reaffirmed this session; recorded in memory + `feedback_no_demo_gaming`).
- `max_workers` (concurrency) vs `max_agents` (breadth) stay distinct.
