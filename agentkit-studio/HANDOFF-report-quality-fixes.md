# HANDOFF — research-report quality fixes (Bug B shrink + Bug A diagram) + presentation follow-ups

**Date:** 2026-07-04
**Branch:** `build-research-report-generator-plan` (single checkout `/Users/yuxinliu/code/agentkit`, studio backend at `agentkit-studio/backend`)
**Authoritative design spec:** `PLAN-generic-component-assignment.md` §6–§10 (§0–§5 are superseded history; §9 = codex-reviewed fix spec; §10 = structural-content/presentation design).

Read the plan §6–§10 first. This doc is "what happened / what's left / how to resume."

---

## TL;DR

Two damaging bugs in the hill-climb research-report generator were diagnosed (against real run
evidence, after two wrong theories each corrected by data), fixed, tested, and committed. Both are
verified. A larger per-section "right presentation for the content" initiative (diagram/table/list)
is designed + de-risked but only the diagram GENERATION core is landed. Banked at >$1000 session cost.

| Commit | What | Status |
|---|---|---|
| `683e54b` | Bug B: patch-based publish-revision + shrink/de-cite guard + guarded expansion | ✅ live-proven |
| `914af43` | Observability: log full traceback + exc type on mid-run failure | ✅ tested |
| `ace9cff` | Bug A: A2 deterministic diagram generation (code-rendered + code-inserted) | ✅ generation proven live |
| `6212601` | test: adapt structural-retry tests to A2 (ace9cff was red on its own — fixed) | ✅ HEAD green |

Full suite on **clean HEAD `6212601`**: **710 passed / 4 deselected** (verified against a clean checkout —
`ace9cff` alone was red because its `test_runner.py` adaptations were dirty at commit time; `6212601`
committed them: 3 loop tests retargeted to a non-diagram "comparison table" opportunity + 3 A2
integration tests). No uncommitted tracked changes except scratch scripts (below).

---

## Bug B — report collapse (FIXED, the dominant bug)

**Symptom:** a good 21.4KB report was served as 9.5KB with citations stripped (48 URL-lines→17), score
0.97→0.75. **Root cause (verified from on-disk io files):** the epoch gate correctly accepted the
21.4KB text; the shrink happened POST-gate in the **publish-revision** (`runner.py:3843-3902`), which
was a WHOLE-DOCUMENT LLM rewrite accepted on publish-readiness checks alone — no shrink/de-cite/score
guard. Weak gemma condensed it.

**Fix (`683e54b`):**
- `_publish_revision_regressed` guard at the accept point — reject a revision that shrinks / drops
  verified URLs / drops distinct sources / regresses `rubric_score`; **fail-CLOSED** on gate error.
- `studio/publish_patch.py` — replaced the whole-doc rewrite with **patch-based** revision
  (fragment+anchor patches, fuzzy anchor resolve with 0.80 floor + uniqueness/tie-margin, whole-doc
  reject >30%, aggregate budget 12 patches / 50% span, conflict-skip, one bounded retry). Applies via
  `agentkit.artifacts.patcher.reduce_patches`.
- `studio/expand_sections.py` — guarded expand-underdeveloped-sections (unused grounded findings, exact
  source URL required, anti-citation-wall ratio, score-regress guard; no-ops with zero LLM calls on
  healthy reports).
- Codex review REPRODUCED + we fixed 3 P1 (conflict-orphan append, anchor ambiguity, fabricated-URL
  passthrough) + 2 P2 (aggregate budget, silent gate-swallow) — all regression-tested.

**Live proof:** re-run (session `s_2b186fac503d`, gemma, 2 epochs, seed=v9) → v4 = **19.8KB / 0.7896 /
10 URLs** vs damaged v2 **9.5KB / 0.7531 / 9**. Report no longer collapses. (The first re-run attempt
died `failed_partial` on a transient oMLX connection drop — NOT the fix; that's what motivated `914af43`.)

---

## Bug A — architecture diagram never generated (GENERATION fixed; per-section/table/list = follow-ups)

**Symptom:** 0 mermaid across 6 live structural-retry attempts, despite a CFP "Mermaid Safe Mode"
prompt. **Root cause:** `_editor_structural_retry` (`runner.py:~1128`) drives a TOOL-AUGMENTED editor —
the model must BOTH write valid mermaid AND issue a `patch_artifact` tool call to insert it; weak gemma
fails both. The MVP had tested CFP as a bare completion (harness-vs-production fidelity gap).

**Fix (`ace9cff`) — A2 deterministic path (`studio/diagram_render.py`, wired into `_editor_structural_retry`):**
For a diagram-shaped opportunity, bypass the tool call entirely: the BARE `base_client` emits plain
`COMPONENT: id | label` / `EDGE: a -> b | label` lines (weak models DO this reliably) →
`render_grounded_diagram` deterministically renders a guaranteed-valid mermaid flowchart →
`insert_diagram_block` code-inserts a fenced block → existing accept gate (unchanged). Tool-augmented
loop kept as fallback (tables/code).

**Proven live end-to-end** on gemma-4-26B + real v9 "Background and Context" section: 8 grounded
components / 6 edges of the real layered Pi architecture (OpenClaw→pi-coding-agent→pi-agent-core→pi-ai)
→ valid fenced ```mermaid block landed (+340 chars). This is the LANDING that tool-call gave 0/6.

### ⚠️ KNOWN WEAKNESS in the committed grounding (fix first when resuming)

`ace9cff` grounds via **embedding cosine** (BGE-M3, `min_cosine=0.40`). Empirically measured: real
nodes 0.70–0.79 vs **same-domain fabrications** ("Blockchain Ledger" 0.58, "Kubernetes Cluster" 0.62) —
only **0.03 separation**, so the threshold **rejects nothing**. Embeddings can't separate real from
plausible-fake here. NOT a regression (gemma extracted only real verbatim components; the diagram is
correct) — but the fabrication GUARD is ineffective.

**DECISION (A) — literal-token grounding (empirically settled, adopt on resume):**
Per-section data was decisive: embedding cosine can't separate — a fabrication ("Stripe Billing API"
0.540) TIES a real component ("Agentic logic" 0.540) and outscores others; BGE-M3 scores short entity
labels by DOMAIN, not presence. Literal-token presence separates cleanly (real 4/4 tokens in the
section, fabricated 0/3).
1. **Grounding = literal-token presence**: keep a component iff ≥1 significant token (len≥4,
   case-insensitive) of its label appears in the SECTION prose; else drop. Deterministic, zero-cost, the
   only signal that separates. This is NOT the brittle keyword-match rejected earlier — the generator
   extracts the list FROM the section prose, so real nodes are **verbatim by construction**; the
   implicit/paraphrase risk does not materialize (proven on real data).
2. **DROP the embedding-cosine guard** (`min_cosine` in `diagram_render.py`) — it rejects nothing useful.
3. **(escape hatch, add only if a real non-literal node is ever observed):** one cheap LLM-verify per
   zero-token-overlap residual node. Currently that residual == the fabrications, so it'd add cost for
   nothing — do NOT add it preemptively.
4. Keep fall-open + accept-gate as backstop.

**Also: the per-section DETECTION prompt is TOO LOOSE** (measured): it false-positived YES|architecture
on Executive Summary + Scope (narrative/meta sections) and echoed the literal `<architecture|process|
dataflow>` placeholder on Key Findings/Evidence. Fix when wiring the per-section loop: add the text-first
floor ("would a diagram MATERIALLY beat prose? ≥3 distinct components with REAL relationships, not a
narrative/meta summary") + a cleaner (non-placeholder-echoing) output format.

---

## Follow-ups (tasks #21, #23 — nothing lost, resume cheap)

1. ✅ **DONE (2026-07-04, not yet committed)** — Diagram grounding refinement: replaced the inert
   embedding-cosine guard in `diagram_render.py` with **literal-token presence** (DECISION A). `_ground`
   now keeps a component iff ≥1 significant token (len≥4, word-start match so plurals/inflections count)
   of its label appears in the report prose; un-checkable short-token labels fall open. Dropped
   `_GROUND_COSINE_MIN`/`_cosine`/`_StubEmbedder`/`embedder` param; caller (`runner.py:1212`) + both test
   files updated (`_AxisEmbedder`/`_Boom` doubles deleted). LLM-verify residual escape hatch NOT added
   (deferred per DECISION A #3 — residual == fabrications today). **710 passed / 4 deselected**; module
   self-check green. **Committed `7a7f049`.** Follow-on codex-review fix **C2 (`843fa44`)**: reject an
   edgeless component list in `render_grounded_diagram` (N boxes / 0 edges is not a diagram) — `_MIN_EDGES`.
   Follow-up #2 (per-section presentation loop) designed + codex-reviewed in `PLAN-per-section-presentation.md`
   §10 (build order: C2✓ → live prompt-test → local-debt gate + one-diagram wiring → tests → #4 list, #3 table).
2. **Per-section detection loop** — current wiring runs on the WHOLE `scored_text` and produces ONE
   diagram. User wants PER-SECTION: iterate H2 sections, decide per section, generate for qualifying
   sections only. Idempotent (skip a section already having a mermaid/table), text-first threshold
   (≲5-6 items stays prose), caption each.
3. **Table** presentation layer — same pattern for "multiple components + COMPARISON → markdown table"
   (deterministic render, more reliable than mermaid). v9 already has an architecture table → idempotency.
4. **List/paragraph** reformatting layer — per-section "prose vs bulleted/numbered list vs list-type
   paragraph" with quality rules (parallelism, colon intro, 2-7 items, no multilevel, don't over-list).
5. **Final live-E2E** — run a full editor pass live and confirm the diagram SURVIVES the accept gate
   (generation+insertion proven; accept-gate unchanged/proven; but not yet run through the whole retry
   live end to end). Low risk.
6. **TOOLS panel bug (#21)** — TOOLS tab shows "No tool calls yet" during runs (other tabs populate).
   Not a tools-off issue (prefetch fires). Likely gemma fenced-JSON tool-call emit not firing the
   `ToolCallEvent`, or a `runStore` shape mismatch. Backend `ToolCallEvent` (events.py:283) / frontend
   `runStore.ts` → `ToolsPanel.tsx`. See task #21.

### The unifying design (PLAN §10): per-section "right presentation for the content"
Per section, pick by content shape (cheapest-tier-that's-correct): PARAGRAPH (flowing logic / few
items) · BULLETED/NUMBERED LIST (≥3-4 parallel items) · TABLE (comparison, or list >8-10) · DIAGRAM
(components + relationships/flow). Gates: text-first threshold, no-duplication, highlight-main-findings,
caption every block. Best-practice sources baked in: APA, Turabian ch.11, Canada CCDR, CSE Science
Editor, Google Tech Writing, Microsoft Style Guide, Cornell CHEC, UK DfE, WSDOT.

---

## How to resume (environment + gotchas)

- **Services:** oMLX gemma `:8000` (model `gemma-4-26B-A4B-it-heretic-4bit`), SearXNG `:8080`, backend
  `:8770`, frontend `:5173`. Restart backend after code changes: `agentkit-studio/./restart.sh backend`
  (uvicorn does NOT hot-reload). Verify `GET :8770/backends` = 200.
- **oMLX is flaky under heavy load** — a run drops with "Connection error." (transient; recovers). The
  `914af43` traceback logging now captures the real error in `backend/tmp/uvicorn.log`.
- **Tests:** `cd agentkit-studio/backend && .venv/bin/python -m pytest tests -q` → expect 710 passed.
- **Lineage / baselines** (`backend/tmp/task_runs.db`, task_hash `b9d55be4f6d6` = the "and" phrasing):
  v1 0.97 · v2 0.7531 (damaged, pre-fix) · v3 failed_partial (oMLX drop) · v4 0.7896 (post-fix, proven).
  Seed = v9 artifact `backend/tmp/studio-workspaces/s_791e2db70e88/artifact.md` (19,672 chars; has a
  table, no diagram — the Bug A test fixture). The `39ee3efddbd9` lineage = the "or" phrasing (note:
  `task_hash = sha256(base_identity(bare_requirement).lower())[:12]` — the STORED requirement is the
  expanded form and re-hashes differently; the bare typed input is what hashes to the lineage id).
- **Driving a live run via the GUI** (chrome-devtools MCP, not ghost-os): select gemma → Connect session
  → inject requirement via native-setter+input event on `textarea[aria-label="New message"]` (fill() is
  unreliable for long text) → set hill-climb config by DIRECT `POST /api/session/{id}/hill-climb`
  (the form's seed_path field has a React onChange-staleness bug; the direct POST is reliable) with
  `max_epochs:2, auto_improve:true, seed_path:<abs v9 path>` → Send. Do NOT attach a goal (forks the
  task_hash). See prior session memory `agentkit-studio-gui-test-quirks`.
- **Scratch to clean up** (uncommitted, in `backend/`): `a2_prompt_check.py`, `a2_live_validate.py`,
  `a2_tune_threshold.py`, `e2e_demo_fix*.py`, `calib_*.py`, `editor_*.py`, `test_t1_reachability_scratch`
  — diagram-fixer's validation scratch; delete or ignore, do not commit.
- **Reuse constraint (PLAN §7):** extend existing seams — `rubric_score`/`scoring_matrix`, `lint_artifact`,
  `requirement_compliance`, the deliverable template, `_is_structural_opportunity`, `epoch_gate`,
  `agentkit.artifacts.patcher` — never fork a parallel scorer/detector.

## Method note (why the fixes are trustworthy)
Three diagnoses were wrong before the evidence corrected them (Bug A: hard_issue-routing → actually the
retry fires; Bug B: epoch-gate → actually post-gate publish-revision; Bug A grounding: embedding →
actually doesn't separate). Each was caught by reading real run artifacts / running the real path /
independent codex review that REPRODUCED bugs the unit tests missed. Verify against reality, not theory.
