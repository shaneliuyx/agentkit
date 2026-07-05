# PLAN-CONSOLIDATED — Master Requirement / Design / Code Reconciliation

**Date:** 2026-07-05
**Purpose:** the anti-drift ledger. Every requirement ever stated across this repo's ~20 planning
docs, reconciled against the code AS IT EXISTS TODAY, with stale design claims corrected and the
real backlog decomposed. When any other doc's status disagrees with this one, this one wins until
it too is dated — then re-verify against code, never against another doc.
**Method:** full-content sweep of all planning MD files (SPEC.md, DESIGN-v2.md,
WORKLOG entries 1–178, CURRENT task list, PLAN-research-report-skill-package-improvements.md
workstreams A–P, 5 execution PLANs, 5 HANDOFFs, AGENTS.md, README.md) + direct code verification
of every load-bearing claim (file existence, gate conditions, entry points) + the 2026-07-04/05
change wave which post-dates every other doc.

**Authority order for future readers:**
1. The code (verify, don't trust any doc).
2. This file (dated reconciliation).
3. WORKLOG (ground truth for *what landed when*).
4. ARCHITECTURE-doc-generation-pipeline.md (how the pipeline works, re-traced 2026-07-04).
5. SPEC.md / DESIGN-v2.md (design rationale — status claims in both are known-stale, see §3).
6. Individual PLAN-*/HANDOFF-* docs (historical; most are partially superseded, see §5).

---

## 1. Requirement status matrix

Legend: ✅ fulfilled (code-verified) · 🟡 partial · ❌ unfulfilled · ⚰ settled-negative (tested, rejected — do not rebuild)

### 1a. Platform foundations (SPEC)

| Requirement | Status | Evidence |
|---|---|---|
| SSE run API, 19+ event types, ordering contract, cancel | ✅ | `app.py`, `events.py`; M1–M6 MVP done |
| 2D topology graph (React Flow + anime.js), token HUD, stream pane | ✅ | frontend, 43 vitest green |
| 7 panels (Memory/Self-improve/Evolve/Security/DAG/Verify/Router) | ✅ | `studio/panels/*` — now 10 panel modules incl. loopdoctor/hillclimb/dag |
| Token honesty (sticky `~estimated`) | ✅ | `StudioChatClient` + TokenAccounting |
| Backend :8770, graceful degradation on missing local services | ✅ | verified live repeatedly |
| **M7 loop library** (catalog client, seeded planning, Loops panel) | ✅ | `studio/loops.py` (CatalogClient incl. `include_local`), Workstream I slice — **SPEC's "follow-on, never built" is stale** |
| **M8 Loop Doctor** | ✅ | `studio/panels/loopdoctor.py`, `loopdoctor` SSE event in live order |
| **M9 skills + export-run-as-loop** | ✅ | `app.py:345 GET /export/{session_id}` (comment: "M9"), `skills_paths.py`, `run_to_loop` |
| M10 chat panel + continue-run | ✅ | ChatPanel, continue-run lineage (DESIGN §14 Resolved) |
| Goal / Scheduler / Chain endpoints | ✅ | `app.py:423 /session/{id}/goal`, scheduler wiring |

### 1b. Research-report workstreams (big PLAN, A–P)

| WS | Title | Status | Notes |
|---|---|---|---|
| A | Typed evidence matrix | ✅ | `evidence.py`, SSE, export, `evidence_json`. Deferred: separate evidence query table |
| B | Hard-fail publish gate | ✅ **(doc stale)** | Implemented as `report_quality.evaluate_publish_readiness` + `combined_publish_issues` + patch-based publish revision — NOT the specced `publish_gate.py` module name. The PLAN's missing "Implemented slice" marker is a bookkeeping gap, not a code gap |
| C | 100-pt frozen scorecard | 🟡 | Frozen matrix + reducer full-matrix + scorecard→weakness generation live (weakness lists prove it: "Scoring gap: Evidence synthesis scored 2.5/14.7"). Worker relatedness-cropping and `remaining_scoring_matrix` shrink-lifecycle exist (`_prompt_scoring_matrix`); formal `rubric_scorecard_100()` API naming drifted |
| D | ResearchConfig intake | ❌ | No intake object, no LoopConfigPanel controls, no profile routing from intake. **Constraint when building: MUST stay out of task_hash** (lineage guard) |
| E | Research package export | ✅ | `run_to_research_package`, `/export/{id}/research-package`. Deferred: ZIP |
| F | Human review workflow | ✅ as scoped | `build_review_status()`. Deferred by design: blocking manual approval queue |
| G | Diagram/table/code static lints | ✅ | `artifact_lint.py` + static checks. Deferred by design: sandboxed code execution |
| H | Source quality + corroboration | ✅ | source-type inference, weak-status downgrade |
| I | Research loop + skill via seed | ✅ | local loop JSON + domain skill + `/loops`/`/skills` |
| J | Catalog CRUD/sources/UI | 🟡 | Template catalog management EXISTS (audit/quarantine/replace/import/export/approve, entries 143–148, admin-key auth). Loop/skill CRUD + Find/Loops/Skills/Sources tabs + remote sources: NOT built. The task list's "No /catalog/* routes exist" line is stale |
| K | Validation-first observations | 🟡 | Stop report, metrics SSE, trace/checkpoints JSONL, pre_validation checkpoint live. MISSING (verified): `observations.py`, pre-dispatch validation in ToolAugmentedClient, SQLite observation store |
| L | Full enhanced bundle | 🟡 | HTML/JSON/trace/source-notes/demo-code live. MISSING: PDF/PNG rendering |
| M | Run metrics dashboard | 🟡 | `run_metrics.py` + metrics event + export. MISSING: full trace-derived metrics (waits on K), `metrics_json` persistence, per-task-hash aggregation |
| N | Template presets + catalog | ✅ | `report_profiles.py` (10 profiles), TemplateStore + audit + active-only `find_template` (verified: runner DOES call `find_template` — the Matrix §12 claim it doesn't is stale) |
| O | Weak-model hardening | 🟡 **(doc badly stale — says "no slices anywhere")** | VERIFIED BUILT: O1 `model_profiles.py` (+ strong-Claude tier 2026-07-04), O2 profile budgets (searches/fetches/iters wired into ToolAugmentedClient), O6 failure-mode lints, O7 publish-ready gate, O10 `genericity_audit.py`. NOT built (verified missing): O3 JSONL-only finding contract, O5 `report_assembler.py` (no file, no test), O8 methodology-loop seeding beyond scaffolding, O9 regression test (`fixtures/bad_report_duplicate_sections.md` exists; `test_bad_report_quality_gate.py` does NOT) |
| P | Planner-reviewed dynamic sections | ✅ **(doc 1 day stale)** | Designed 2026-07-05 AND implemented same day (`b2f6888`): `requirement_section_decisions` + `apply_section_decisions` in planning.py, skeleton `###` subsection rendering, live-verified (`s_b37252441913` skeleton carries planner-injected "Code Implementation Examples" / "System Architecture Design"). v1 non-goals stand: no mid-phase injection, no new files for subsections, epoch-boundary re-review NOT yet wired (see backlog) |

### 1c. Quality campaign (July PLANs/HANDOFFs + 2026-07-04/05 wave)

| Requirement | Status | Evidence |
|---|---|---|
| Citations must come from live web, not model memory | ✅ | auto-fetch after search (`_auto_fetch_after_search`, profile-driven), grounding forcing turn, `_prefetch_cited` cap 24, evidence/ dossier — live-verified: 9-file dossier, quotes verbatim in fetched pages |
| Full articles for citation, snippets only for triage | ✅ | page slice = `section_window_chars` (6K gemma / 20K strong), not 2.5K |
| Cold-start auto runs get full section machinery | ✅ | skeleton bootstrap un-gated from `use_llm` (2026-07-04) — was the root cause of 42→0 citation stripping |
| Editor/presentation passes actually run | ✅ | two stacked dead-gate bugs fixed (artifact materialization + swallowed `judge_client` NameError) |
| Diagram generated + grounded | ✅ mechanism / 🟡 value | A2 deterministic renderer inserts grounded mermaid; but it is EXTRACTIVE — re-encodes existing prose only. A "design architecture" deliverable needs the Workstream-P synthesis section upstream, now in place; value verification pending |
| Depth/synthesis passes run in auto mode | ✅ (2026-07-05, `468e6b1`) | `_synthesize_analysis` + `expand_underdeveloped_sections` un-gated from `use_llm` — they were the designed solvers for the two stuck rubric rows (Evidence synthesis 2.5/14.7, Analytical depth 1.8/10.5). **Live verification pending** (first post-fix run) |
| LLM timeout survives honest long generations | ✅ | 240s default + `max_retries=0` (retry-multiplication kill), root-caused via py-spy + port-cadence |
| Context compaction on every LLM call | ✅ (`00983b9`) | `compact_messages` in StudioChatClient.chat; pair-safe tool-loop cut; `agentkit.context.compactor` finally has a consumer |
| Hybrid model split (strong plan/judge, weak execute) | ✅ | planner on `judge_client` (default haiku); presentation judging already there |
| Planner never shreds compound noun phrases | ✅ | `(?:,|;)\s+and\s+` clause-boundary split ("Pi and Craft" fix) |
| Per-section presentation ladder (5 forms) | 🟡 | Diagram tier wired (`section_presentation.py` + gate + rollback); `presentation_classifier.py` built NOT wired; table/list generators + deterministic format-fixer NOT built (PLAN-content-and-shallowness Phase 1) |
| Judge-model selector in GUI + judge_llm in POST /session | 🟡 | backend `judge_llm` spec live; frontend selector + presentation action list NOT built |
| TOOLS panel shows live tool calls | ✅ | resolved 2026-07-04: panel was truthful; gemma genuinely made 0 calls pre-auto-fetch. 179–182 tool_call frames stream in verification runs |
| GUI quick-start flow (#1–#4, #6) | ✅ | WORKLOG 178a, live browser-verified. #5 (inline hill-climb toggle) still excluded pending own design |
| Mid-run death persists partial progress | ✅ code / 🟡 live | entry 178b `failed_partial` rows; unit-tested, never exercised live |
| Report depth proven good live | ❌ **the open headline question** | Score ceiling ~0.61 on the Pi/Craft benchmark. Workstream P + depth-pass un-gating are the two fixes aimed at it; the proving run is queued |

---

## 2. Corrected stale designs (doc says X — code says Y, verified 2026-07-05)

1. **SPEC §8 M7–M9 "follow-on after MVP"** → all three shipped (`loops.py`, loopdoctor panel + event, `/export/{session_id}`, skills). SPEC was never updated.
2. **Big-PLAN Workstream O "no implemented slices"** → half of O is live (O1/O2/O6/O7/O10). The plan's markers were simply never back-filled; the WORKLOG (entries 8, 16–18, 48–59) is the truth.
3. **Workstream B "no slice"** → publish gate live under `report_quality.py`, not the specced `publish_gate.py` module name. Module-name drift, not a gap.
4. **Matrix §12 "run path does not call `find_template()`"** → runner calls it (verified grep); N's slice made it active-only + `last_used_at`.
5. **"`_MAX_TOOL_ITERS = 8` global, not model-profile-specific"** (PLAN-perf O2 premise) → `max_tool_iters` is per-profile in `model_profiles.py`; the global is only a fallback.
6. **expand_sections contradiction** (3 docs say "shallowness fix not built", 1 says landed in `683e54b`) → resolved: `expand_sections.py` EXISTS and, as of 2026-07-05 (`468e6b1`), actually RUNS in auto mode. The three "not built" docs were written while it was `use_llm`-gated — dead code read as absent. Both readings were defensible; neither is now.
7. **PLAN-content-and-shallowness "commits 2117299/8f919f4" vs handoffs "parked/uncommitted"** → the wiring landed; `section_presentation.py` is in tree and fires live (gemma sometimes pre-draws the diagram, making the pass an idempotency no-op — that's the pass working, not dead).
8. **Workstream P "the initiator was never built"** → built + live-verified the day after the design was written. The PLAN doc's own §P preamble is already stale.
9. **README score semantics** ("score is the §11.10 weakness-ratio") → recorded score is `adjusted_score(rubric_base, weaknesses)` (README's own D9 section has it right; the persistence section was never reconciled).
10. **README `task_hash = sha256(requirement…)`** → post-D1 it is `task_hash(base_identity(_base_requirement))` — goal/template-invariant.
11. **README Goal/Scheduler curl examples on :8000** → backend is :8770 (:8000 is oMLX). Copy-paste trap.
12. **DESIGN "D5: decompose runner.py (~2.1K lines)"** → runner.py is now ~5.0K lines; the decomposition never happened and the number is stale. `planning.py` extraction was the partial answer.
13. **task list "Deferred: entry-166 run-death (logged, not fixed)"** → fixed in WORKLOG 178b; task list not reconciled past entry ~167.
14. **HANDOFF-citation "do NOT commit without fresh instruction"** → session-scoped constraint that reads as standing; later sessions correctly committed per the user's implement→test→commit workflow.
15. **Per-spoke I/O marked both Partial (D:1069) and Resolved (D:1279) in DESIGN** → Resolved is correct (per-spoke `agent_io.jsonl` rows verified in live workspaces).
16. **Line-number anchors in all pre-07-04 docs** (findings.py:268/327/333, runner.py:790/1128/3843, etc.) → runner.py grew ~2.4K lines since the earliest anchors; treat every line cite in pre-07-04 docs as approximate. ARCHITECTURE §10 (re-verified 2026-07-04) is the only trustworthy line index.

---

## 3. Backlog — decomposed, priority-ordered

### P0 — prove the depth fix (the open headline question)
1. Cold Pi/Craft run on the post-`468e6b1` backend: measure Evidence-synthesis + Analytical-depth rubric rows, code blocks in the injected "Code Implementation Examples" subsection, score vs 0.6083 baseline. (Run queued; this closes or reopens the shallowness thread.)
2. If depth still capped: execute PLAN-content-and-shallowness Phase 3 remainder — relax `findings.py` one-sentence contract (:327 area), floor-aware density cap, sentence-cap reconsideration. The three anchors need re-locating first (see §2.16).
2b. **Strong-model reducer** (Lever 4 from trashed HANDOFF-score-improvement, 2026-06-27 — the only one of its four levers never built; recovered 2026-07-05). The reducer is the synthesis chokepoint; run it on the judge client (same degrade-to-base pattern as hybrid planning, `runner.py:2198`) while spokes stay on the session model. Cost is confined to reduce calls. Directly targets the Evidence-synthesis/Analytical-depth rows; try AFTER P0-1 measures the un-gated passes, so the two levers are attributable separately.

### P1 — finish the presentation ladder (PLAN-content-and-shallowness Phases 1–2)
3. Wire `presentation_classifier.py` (built, unwired).
4. Table + list generators (deterministic-first) + deterministic format-fixer; generalize `plan_presentation` dispatch under the SAME accept gate + rollback (never fork gates).
5. Frontend: judge-model selector + presentation action list (backend `judge_llm` already live).

### P2 — Workstream P v2
6. Epoch-boundary re-review (design §4 — written, not wired; v1 fires at plan time only).
7. Live E2E "diagram survives accept gate end-to-end" (HANDOFF-report-quality next-step 5 — never run; now cheaper since A2 + Workstream P landed).
8. References section: deterministic bibliography from surviving cited URLs + evidence manifest at finalization (kills the π-Wikipedia/refusal-filler junk class; diagnosed 2026-07-04, designed nowhere yet).
8b. **Code-shaped compliance downgrade** (found by run 1529, 2026-07-05): "include example code" was scored SATISFIED (0.97, zero weaknesses) by prose *describing* code — zero fenced blocks — while the evidence dossier held actual `.ts` source. Mirror the diagram-shaped downgrade: `_CODE_SHAPED_RE` (`example code|code example|sample code|snippet`) + fenced-code-block presence gate → NOT_SATISFIED flips → structural retry renders real code from evidence. Same file, same pattern, `requirement_compliance.py`.

### P3 — intake + catalog (the two big unbuilt workstreams)
9. Workstream D: `ResearchConfig` intake (report_type/audience/depth/source_policy…) + LoopConfigPanel controls + profile routing. HARD CONSTRAINT: intake must not enter `task_hash` (lineage).
10. Workstream J remainder: loop/skill CRUD + enable-disable + copy-local + remote sources (TTL/status/egress) + Find/Loops/Skills/Sources tabs. NOT greenfield — extend `CatalogClient` + template-catalog patterns (auth precedent: entry 165 admin key).

### P4 — weak-model hardening remainder (Workstream O open half)
11. O3 JSONL-only finding contract (premise to verify first: `_cited_urls` may not harvest raw JSONL — flagged in PLAN-perf, unverified).
12. O5 deterministic report assembler (flag-gated; biggest token win per PLAN-perf).
13. O9: write `test_bad_report_quality_gate.py` against the existing fixture.

### P5 — observability + persistence depth (K/L/M remainder)
14. K: `observations.py` + pre-dispatch validation callbacks in ToolAugmentedClient + SQLite store.
15. L: PDF/PNG rendering. M: trace-derived metrics + `metrics_json` persistence + per-task-hash aggregation.
16. Persistence columns from Matrix §3: `publish_gate_json`, `metrics_json`, `trace_path`, `checkpoints_path`.

### P6 — hygiene / deferred verifications
17. Perf A/B benchmark (HANDOFF-perf — designed, never run; nothing blocks it).
18. Live exercise of the `failed_partial` persistence path (kill a run mid-flight on purpose).
19. `_verify_prompt` SATISFIED-bar tightening for structural branches (HANDOFF-structural-retry next-step 2 — partially superseded by the compliance downgrade + Workstream P, re-evaluate before doing).
20. GUI quick-start #5 (inline hill-climb toggle) — needs its own design pass (modal state-shadowing).
21. Wall-clock companion cap for count budgets (8 spokes × 12 iters ground for hours before the timeout fix; budgets still have no time ceiling).
22. Per-task web-cache scoping (DESIGN §14 deferred; shared cache pollutes across tasks).
23. Doc hygiene: back-fill "Implemented slice" markers for B and O1/O2/O6/O7/O10 in the big PLAN; reconcile CURRENT task list past entry 167; fix README §2.9–2.11 items.
24. runner.py decomposition (D5) — now ~5K lines; extract postrun/seed/editor modules. Do it AFTER the depth question closes (churn risk).

### Deferred-by-design (do not schedule without new evidence)
- Sandboxed execution of generated code (G) · blocking manual approval queue (F) · ZIP export (E) ·
  separate evidence query table (A) · `optimize_text` owning the epoch loop · DeepEval/RAGAS judge
  (deps not installed) · MESH position-aware fold · shared-library asks list (big PLAN L1804+).

---

## 4. Settled negatives — tested, rejected, do not rebuild

- **Worker-side `search_evidence`**: no gain; model ignores hits (entry 156).
- **Cosine detector for seed contamination**: wrong direction; rejected pre-ship (entry 157).
- **LLM pairwise "which is better" epoch judge**: hedges to TIE even on sonnet; deterministic rubric preference is the gate (DESIGN D4).
- **solved/total score**: punished thoroughness, rewarded empty docs; retired (DESIGN §6.4).
- **Whole-doc LLM echo at scale**: truncates (68KB→36KB); moving-window only (DESIGN §5.3).
- **Weak-model auto report-routing override**: reverted per user design correction (entries 26–34); LLM epic planning stays default.
- **`web_fetch` request dedup/stubbing**: starves the cross-agent reader; cache already serves (DESIGN).
- **F3 anti-truncation section repair**: the motivating "truncation" was miner hallucination (DESIGN §14 Cut).
- **Deterministic regex mermaid repair**: rejected; in-run LLM repair turn won 5/5 (memory: lint-on-seed blindspot).
- **gemma citation-integrity ceiling** on fixture `s_db8f2ec3b920`: genuine model ceiling, not a code bug (entry 156).

**Standing design invariants recovered from trashed handoffs (2026-07-05 dig):**
- **Reducer patch contract stays prose-only** — URL-bearing sentences, never headings/diagrams/
  tables/code. Loosening it is exactly the duplicate-heading bug class entry 162 fixed. Structural
  content belongs to the editor/presentation passes only (HANDOFF-requirement-compliance).
- **Goal-blind spoke invariant** — requirement/relevance/compliance notices go to the goal-aware
  reducer only, never spoke prompts.
- **Two fan-out levers stay distinct** — `max_workers` (concurrency) vs `max_agents` (breadth);
  conflating them hid an 18-spoke/790K-token explosion (HANDOFF-agent-explosion, 2026-06-27).

**Trashed-handoff disposition** (all four dug 2026-07-05; safe to remain deleted):
`HANDOFF-agent-explosion` resolved → DESIGN two-lever doctrine · `HANDOFF-seed-shrink-debug`
resolved same-day per its own header · `HANDOFF-requirement-compliance-diagram-reliability`
absorbed (structural retry = entry 174; clean E2E since run repeatedly) ·
`HANDOFF-score-improvement` levers 1–3 built; lever 4 recovered as backlog P0-2b.

---

## 5. Doc-disposition table (what to trust each file for)

| Doc | Trust for | Do NOT trust for |
|---|---|---|
| ARCHITECTURE-doc-generation-pipeline.md | pipeline mechanics, line index (§10) | pre-07-03 diagram line cites |
| SPEC.md | API/event contracts, MVP design rationale | milestone status (M7–M9 stale) |
| DESIGN-v2.md | invariants, decision rationale, lessons index | §14 backlog labels (self-admittedly decays) |
| WORKLOG | what landed, when, entry-numbered | nothing — it's the changelog, but stops at 178 (2026-07-03); the 07-04/05 wave is in git log + ARCHITECTURE + this file |
| CURRENT task list | pre-entry-167 completion detail | anything after entry 167 |
| Big PLAN (A–P) | workstream specs, priority rationale | status markers (B, O, P all wrong) |
| PLAN-content-and-shallowness | the live execution queue for presentation+depth | its "state at start" snapshot |
| PLAN-per-section-presentation | gate semantics (C1–C9 corrections) | "no code yet" status |
| PLAN-generic-component-assignment | §6–§10 diagnosis chain | §0–§5 (self-superseded), line anchors |
| HANDOFFs (all 5) | commit hashes, settled negatives, live-run evidence | "next steps" (mostly absorbed into later PLANs) |
| README | user manual, capability overview | score semantics, task_hash formula, :8000 examples |

---

## 6. Maintenance rule

This file decays like every other status doc (DESIGN §14 proved it: 5 of 9 "open" items had
already shipped when audited). Rules:

1. **Date every status claim.** A ✅ without a date is a rumor.
2. **When landing work, update: git commit message (always) → WORKLOG entry (substantive) →
   this file's matrix row (if it changes a requirement's status).** Do NOT update the old PLAN
   docs — they are history, and rewriting history is how the B/O marker drift happened.
3. **Re-verify before building on a ❌/🟡**: grep the code first. The three biggest time sinks
   found in this sweep were all "building what exists" or "trusting a stale not-built".
4. **Line numbers go in ARCHITECTURE §10 only** — it has a re-verification discipline; prose docs don't.
