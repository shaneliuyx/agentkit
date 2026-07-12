# PLAN-CONSOLIDATED — research-report quality & codebase simplification (SINGLE SOURCE OF TRUTH)

## Part 0 — How to read this doc

### 0.1 Single-plan banner

> **⭐ SINGLE SOURCE OF TRUTH (2026-07-08):** THIS file (`PLAN-CONSOLIDATED.md`) is the one authoritative plan. It absorbed and RETIRES `PLAN-codebase-simplification.md` (its full Parts 0–7 are the body below) and `PLAN-research-first-recovery-addendum.md` (folded into **Part 8**). Those two files are now retirement stubs pointing here — do not edit them. When any other doc, handoff, or memory disagrees, this file wins; within it the unified backlog **Part 1.2** is authoritative over any other list.
>
> *(History: an earlier 2026-07-05 pass merged CONSOLIDATED's §17 material INTO the simplification plan as Parts 1.2/7; on 2026-07-08 the direction was reversed at the user's request — the simplification plan's full content was promoted back into this file as the canonical home. The internal `§N` cross-references below are byte-verbatim from that history; resolve them via the Part 0.4 map.)*

**Date:** 2026-07-05
**Trigger (user):** "we always patch original code — review the whole codebase, reduce
hierarchy/layers, improve efficiency, do not downgrade original features."
**Status:** (origin banner, 2026-07-05) PLANNED — audit done (evidence below); execution starts AFTER
the attempt-4 cold run records. **SUPERSEDED 2026-07-09: the program executed through ACCEPTANCE**
(research_first rebuild, deterministic 0.92–1.0); live axis status = Part 1.4-GS4.

### 0.2 Authority order

1. **Part 1.2 (unified backlog, old §17.3)** is authoritative over any other backlog
   or priority list in this document. Where Part 2 (Active tracks), Part 3 (Execution
   log), or Part 4 (Findings) name a priority or "next" item that conflicts with
   Part 1.2, Part 1.2 wins.
2. Within Part 6 (Simplification program), the Unified Execution Order (§6.5, old
   §12) supersedes the historical sequencing drafts kept for record in §6.6 (old §4,
   old §7).
3. For which EXTERNAL doc to trust for what, see Part 7.4 (old §17.5, doc disposition).

### 0.3 Maintenance rules

(carried over from old §17.6, amended for the new part numbers)

1. Date every status claim — an undated ✅ is a rumor.
2. Landing work updates: commit message → §13 row (now Part 3.1, with measured
   result) → §17.1 matrix row (now Part 7.1) if a requirement's status changed. Old
   PLAN docs are history — never rewrite them.
3. Re-verify before building on a ❌/🟡: grep the code first.
4. Line numbers live in ARCHITECTURE §10 only.
5. **One backlog** — §17.3 (now Part 1.2). New items go there, not into new sections
   or new files.

### 0.4 Old → New section map

Internal cross-references inside Parts 2–7 below were left as their original `§N`
citations (byte-verbatim copy) rather than rewritten in place — use this table to
resolve any of them, and to resolve `§N` citations from commit messages, handoffs,
or memory files written before this restructure.

| Old section | New location |
|---|---|
| Banner / Date / Trigger / Status (unnumbered header) | Part 0.1 |
| STATUS BY CHAPTER (unnumbered table) | Part 1.1 |
| §0 Objective & success metric | Part 6.1 |
| §1 Audit evidence | Part 6.2 |
| §2 Workstreams S1–S5 (+ explicit non-goals) | Part 6.3 |
| §3 Verification protocol | Part 6.4 |
| §4 Sequencing (old) | Part 6.6 (historical, superseded) |
| §5 Loop-health analysis (5.1 RC1–5, 5.2 calibration) | Part 4.1 |
| §6 Loop-health workstreams (L1–L6) | Part 5.5 |
| §7 Combined sequencing (supersedes §4) | Part 6.6 (historical, superseded) |
| §8 Structural-content root cause / L0 (8.1, 8.2) | Part 5.4 |
| §9 Reference-process gap analysis (9.1–9.3, P1–P4) | Part 5.1 |
| §10 Unified pipeline + editorial gate (10.1–10.5, E1–E11) | Part 5.2 |
| §11 First-principles reasoning trace (11.1–11.4, D-A–D-G, D1–D5) | Part 5.3 |
| §12 Unified execution order | Part 6.5 |
| §13 Execution log + lineage scoreboard | Part 3.1 / Part 3.2 |
| §14 Attempt-8 results + attempt-9 slate | narrative → Part 4.2/4.3; ordered slate → Part 2.3 |
| §15 Outcome-first re-prioritization | body → Part 2.1; success-check tail → Part 1.3 |
| §16 Rebuild track: research_first.py | Part 2.2 |
| §17 (intro) Master ledger | Part 7 (intro) |
| §17.1 Requirement status matrix delta | Part 7.1 |
| §17.2 Corrected stale designs | Part 7.2 |
| §17.3 Unified backlog | Part 1.2 |
| §17.4 Settled negatives | Part 7.3 |
| §17.5 Doc disposition | Part 7.4 |
| §17.6 Maintenance rule | Part 0.3 |

## Part 1 — NOW

### 1.1 Status dashboard (STATUS BY CHAPTER, re-keyed)

| Part | Topic | Status |
|-----|-------|--------|
| 6.2 | Audit evidence | ✅ done — drove S1–S5 |
| 6.3 | S-workstreams | S1 ✅ · S2 ✅ · S3 ✅ `601a831` (guards.py, 4 fail-open composites) · S4 ✅ no-op · S5 ✅ MEASURED NULL (live diag: rubric 3×/run, cache 16 entries — memo/index would save microseconds; research_first killed the "hot loop" premise) |
| 6.4 | Verification protocol | ✅ in force — suite 836→890, reviewer pass per slice, live E2E per behavior change |
| 6.6 | Sequencing (old) | superseded by §12 (now Part 6.5) |
| 4.1 | Loop-health diagnosis RC1–5 | ✅ complete — every RC now has live evidence + a workstream |
| 5.5 | L-workstreams | L1 ✅ `371861f` (editorial gate E1–E11 unifier + verdict router + never-record-unverified) · L2 ✅ · L3 ✅ · L4 ✅ MEASURED-NULL `69bdcf8` (repeat-weakness escalation needs a hot iterative loop; research_first is cold-start by design → nothing to escalate over; codex-concurred) · L5 ◐ (verdict/offtopic memo ✅; economics remainder open) · L6 ✅ adopted |
| 5.4 | L0 structural producer | ✅ committed — code path verified live (v3 0.657); diagram veto attributed, prose fix in review round |
| 5.1 | Writer reference process P1–P4 | P1 ✅ `0f72b1d` (coverage ledger; codex HIGH×2 body-cited fix `e8e3da7`) · P2 ✅ DONE-in-research_first `0b4fc47` (question-first planning realized as answer-contracts: `_extract_question_contracts` in FRAME emits per-question `{question,subject,answer_form,min_evidence}` BEFORE research; resolution is per-question via `_resolve_question_contracts`; the ⬜ spec below is stale hub/spoke framing) · P3 ✅ REUSE `0f72b1d` · P4 ✅ `0f72b1d` |
| 5.2 | Editorial gate E1–E11 | ◐ **9/11 rows compute (E1–E7, E9, E11)**; fail-open never records a pass; verdict router + seed-eligibility gated. E7 (deterministic: section promises code/diagram/comparison → must carry the block) + E9 (deterministic: same noun phrase with conflicting counts across sections) added as ADVISORY rows `<this commit>` — codex-scoped: the SPEC's LLM half is over-specced for the cold-start pipeline (advisory weakness never consumed; hard-reject = weak-model false-reject risk on prevention-hardened output), so only the cheap deterministic value was built. **E8/E10 deliberately SKIPPED** — failure modes already prevented by construction (E8←D4 summary-from-body, E10←P4 `_limitations_note`); an LLM judge would re-judge a prevention path. E7/E9 fail → stays "completed" (advisory, not E3/E4 hard-reject); verified PASS on the canonical 6/6 artifact (no false-positive). 6 new tests. |
| 5.3 | Design workstreams D1–D5 | D2 ✅ `0f72b1d` · D3 ✅ `0f72b1d` · D1 ✅ ~90% realized-in-research_first (claims.jsonl grow-only substrate + synthesis-from-claims + coverage-as-query all live; residual = per-claim confidence + claim-IDs, both D5 feeders) · D4 ✅ realized-by-construction (`_write_summary` runs LAST from the final body, `research_first.py:3556` after the section loop — kills overclaiming-summary as specced) · D5 ◐ ONLY open D-item: (a) next-epoch confidence-budget = MEASURED-NULL (cold-start, no epochs, same class as L4) · (b) per-claim confidence + failed-producer-raises-a-research-question = buildable, low-value now (belief-state north-star, "hardest, do last") |
| 6.5 | Unified execution order | ACTIVE tracker — slices 1–4 committed; current work = §14 slate (now Part 2.3) |
| 3.1 | Execution log | living log — one row per committed fix with measured result |
| 2.3 (slate) / 4.2–4.3 (narrative) | Attempt-8 results + attempt-9 slate | items 1–4 ✅ committed (item 1 = BOTH erasers: A `27beff6` synth fence guard, B `d4e84a9` duplicate-section birth fix + fold-merges; 925 passed) · items 5–8 queued behind §15 re-prioritization (now Part 2.1) |
| 2.1 (body) / 1.3 (success check) | Outcome-first re-prioritization | ✅ SUCCESS CHECK MET via the research_first rebuild (row 2.2): deterministic acceptance 0.92–1.0, Craft cited, fuller sections, grounded Pi+Craft integration diagram (`686d7b0`). The old hub/spoke P1-core/depth/8b items are moot (superseded by §16). Now closing GS4 gold-parity axes. |
| 2.2 | Rebuild track: research_first.py | 🟢 ACCEPTANCE REACHED — live deterministic runs hold 0.92–1.0 (task_hash `492bae60177b`). GS3 gold-parity axes (Part 1.4-GS4) ALL CLOSED: Code ✅ `0bf8c69` · Diagram edge ✅ `686d7b0` (client-provenance RCA) · References A titled ✅ `030ffba` + B inline `[N]` ✅ `4fb0240` · Sections heading-leak ✅ `5009d8a` + H3 ✅ `bf32bbe` · Detail (relations table + pi-tui roster + dep-order) ✅ `728e524`/`353a95e`/`f807903` · diagram_render hygiene ✅ `1575cc2` · G3 aggregator/mirror de-rank ✅ `0d034d4`. **ALL gold-parity CLOSED — zero open items** |
| 1.4-GS4 | Gold-parity axis tracker | ✅ COMPLETE — all 5 axes (Code/Diagram/References/Sections/Detail) live-verified per artifact (not score); see §Part 1.4-GS4 table. G3 aggregator/mirror de-rank also ✅ `0d034d4`. **Zero open gold-parity items.** |
| 1.2 (backlog) / 7.1–7.4 (matrix/negatives/disposition) | Master ledger (merged PLAN-CONSOLIDATED) | ✅ merged 2026-07-05 night — §17.3 (now Part 1.2) = THE unified backlog · matrix delta §17.1 (now Part 7.1) · +4 settled negatives (Part 7.3) · CONSOLIDATED now a stub |

### 1.1b PENDING TASKS (open, as of 2026-07-10)

Acceptance (Part 1.3 a–e) already MET live on `492bae60177b` (0.92–1.0); all
gold-parity axes CLOSED. Everything below is *durability / depth hardening*, not
an acceptance blocker. Branch is 153 commits ahead of origin, unpushed.

**GATE — ✅ CLEARED 2026-07-11 (`49563af`):**
- ✅ **Codex branch audit** — exhaustive pre-push review of all 26 changed `backend/studio/*.py` (run `bitpog13x`; two prior runs hung on stdin-open + monorepo-path-prefix bugs, both fixed). Result: 24 CLEAN, 2 prior fixes re-confirmed, **2 real HIGH found + fixed** (`49563af`): (a) `_page_for_url` substring→exact match (evidence misattribution); (b) E3 coverage-fail→rejected per user ruling. +3 regression tests, suite 1170, ruff clean.

**Chain (`start the L1 workflow, then P2, L4`):**
1. ✅ **L1** editorial gate — DONE `371861f` (+ codex HIGH-1 hardening `49563af`).
2b. ✅ **P2.5 — answer contracts + closed-loop recovery** — DONE `554f0f8` (A contracts + B contract-fulfillment measurement, replacing P2's lexical false-negative-prone flag) · `d9a85d3` (C targeted recovery + honest question-gap) · `b696c83` (reconcile traces vs final resolution — live-caught self-contradiction fix). Codex-designed (answer-contracts over type-classifier). Live-verified v93 (s_4c021146c082): 6/6 resolved via real artifacts+grounded claims (0/141 ungrounded), design/comparison/code Qs credited by their artifacts (lexical P2 couldn't). Spec `scratchpad/P2.5-answer-contracts-design.md`. **FOLLOW-UP CLEARED** (gap-forcing live run s_46054c956ff4, benchmark/cost requirement): recovery FIRED on 5 under-evidenced questions, searched + honestly traced the unfillable ones (short_by>0, real queries, one fired the fill path adding 45 real claims), and the 5 traces rendered correctly INSIDE Limitations before References with contradiction-check=0 — the reconciliation + append paths (both unexercised by the canonical 6/6 run) proven live. Orthogonal-not-P2.5: disambiguation degraded on the reworded requirement (1/6); recovery for __joint__ extracts single-subject-tagged claims so joint-fill is imperfect but degrades gracefully to honest tracing. Acceptance {honest measurement · no contradiction · no fabrication · recovery fires+traces} MET. 20 P2.5 tests, suite 1184.
2. ✅ **P2 — question-first planning** — DONE `85e7af9` (spec+approach-A) + `<this commit>` (lexical-floor caveat). FRAME `_extract_questions` decomposes the requirement into per-subject research questions → `_subject_queries` derives search from them (fail-open → subject-generic, byte-identical) → ASSEMBLE records a per-question `__questions__` coverage dimension. **Live-verified** v92 (s_3455ed3e225f, 0.646 completed): 5 questions extracted + drove the searches; run clean, E3/E4 pass. Coverage `answered` flag is a LEXICAL lower-bound (proven 1 false-negative; ~4/5 true) — embedding-cosine upgrade deferred until an E-Q editorial row gates on it (YAGNI). 6 unit tests, suite 1176.
3. ✅ **L4 — repeat-weakness escalation** — **MEASURED-NULL for research_first** (2026-07-11, codex-concurred). DECISION RECORD: L4's mechanism needs a hot iterative loop where the SAME weakness survives K epochs so a lever can escalate. research_first has none of the four prerequisites — evidence: (a) the repeat-failure detector is gated OFF (`runner.py:2811` `if auto_improve and NOT use_research_first`); (b) the structural-editor lever is in `_CONTENT_MUTATING_PASSES` → skipped for `rebuild_generated`; (c) no execute-contract cap exists in research_first; (d) cold-start-by-design → no lever retried across epochs. L4's INTENT ("notice your own walls, don't spin") is already met per-run by P2.5 recovery→honest-Limitation. Building L4 here would be dead code or secretly re-legacy-fy research_first. **Non-goal:** no cross-run coupling in the default cold-start pipeline (codex: only justified as opt-in "campaign memory"). Regression-locked: `test_repeat_failure_machinery_not_invoked_under_research_first` (repeat_failures never called under research_first). L4 applies to the legacy fallback only (which already has the `_repeat_failed`+`_unresolved_block` substrate). **SUCCESSOR (native, building next):** codex's **Answerability Gate** — per-run wall classification (answerable/recoverable/structurally-unanswerable/out-of-scope); skip recovery churn on the unanswerable, reasoned Limitations. Motivated by the gap-run's 5 recovery searches, ~4 on structurally-unanswerable asks (benchmark/cost). Native to cold-start-linear (evidence topology, not epoch history).

**Other open workstreams (not in the active chain):**
- ✅ **D5 CLOSED** (2026-07-12, code-verified) — belief/uncertainty state resolves into three parts, none genuinely-open-and-buildable: **(1+2) epoch confidence-budget = DEAD-BY-COLD-START** — `grep confidence|epoch|belief|uncertain research_first.py` → 0 matches; "per-subject confidence in the ledger" + "next-epoch budget to lowest confidence" have no consumer (no next epoch — same MEASURED-NULL class as L4/advisory-weaknesses); building them = speculative dead code (YAGNI). **(3) failed-producer→research-signal (D5-F) = ✅ REALIZED-BY-CONSTRUCTION** (earlier "low-value-now/pending" was wrong): `_recover_underevidenced` is a closed-loop in-run recovery — an under-evidenced contract triggers a targeted recovery search NOW, and a STILL-short contract emits a failed-recovery TRACE → declared question-limitation (never empty content), with `_classify_answerability` routing structurally-unanswerable asks to reasoned Limitations. Inline-recovery is the cold-start-correct shape of "defer the question to a next epoch." **Residual per-claim `confidence` field left unbuilt** — its only live consumer would be claim-ranking, already solved by subject-grounded fan-out ranking. **D1 ✅ ~90% + D4 ✅ realized-by-construction**; D2/D3 ✅ `0f72b1d`.
- ◐ **L5 remainder** — acceptance economics (per-run verdict cost); memoization half already ✅.
- ✅ **G1-noise** (semantic joint-claim guard) — DONE: `_verify_joint_claim` + `_filter_joint_noise` judge each JOINT claim (>=2 subjects) "genuine relationship vs coincidental co-mention?" and drop the coincidental ones (fail-open, re-persists). Option (b) — verbatim-quote compound-noun check — was ALREADY live in `_subject_supported`; this closes the residual bare-name co-mention case (option (a)). Verified: keeps all 4 real joint claims of the canonical 6/6 run.
- **quarantined, do NOT reopen unless old pipeline outlives it:** buggy `dedupe_sections` fence-masking at `artifact_text.py:662` (reachable only from old-pipeline `normalize_artifact`; research_first is dedupe-free by construction).

**Decision owed to user (not a task):** whether to PUSH the 153-commit branch, and if so squashed/split or as-is. Codex audit is the pre-push gate; push only on explicit word.

### 1.2 Unified backlog

(old §17.3, verbatim — "the single queue; outcome-first per §15" → §15 is now Part 2.1)

> **RESUME POINTER (2026-07-06):** session handoff at
> `.omc/handoffs/2026-07-06-research-first-rebuild-handoff.md` — in-flight teammate state,
> builder's remaining 4-item list, reviewer/commit/attempt-11 sequence, environment gotchas.

**NOW — visible-outcome track (Pi/Craft artifact must change character):**
1. ~~**Attempt 10 readout**~~ ✅ DONE (run 1540, v7 0.5923) — erasers VERIFIED
   dead: fences=4 constant through finalize, typescript code block + mermaid
   diagram both in the recorded artifact (first time ever). Remaining gaps are
   coverage, not erasure: 0 Craft URLs, Pi-only diagram (see Part 3.1 row).
2. **P1-core coverage actor** (#37) — zero-sources check per covers-X subject →
   IMPERATIVE search directive into tool-loop/worker prompts. Craft searches
   need an actor, not another verdict.
3. **Depth relaxation** (#38, absorbs old P0-2) — findings.py 2–3 grounded
   sentences + density-cap revisit. MUST first read f6f6c65/9d30b13/ccabed3
   (prior attempt under-delivered — REBUILD-LESSONS).
4. **8b requirement-conditioned diagram** — build_components_prompt receives
   requirement branch + subject list (full value only after Craft content exists).
5. **Rebuild track (§16)** — ✅ ACCEPTED. USER DECIDED: REPLACE hub/spoke. The
   attempt-11 recovery RCAs (whole-task subject extraction, source-selection leakage,
   marker leaks, filler diagram labels) are all resolved; deterministic acceptance
   reached (0.92–1.0). No rerun pending.
6. **Success check (Part 1.3 a–e)** — ✅ MET live. GS4 gold-parity axes ALL CLOSED:
   Code ✅ `0bf8c69` · Diagram edge ✅ `686d7b0` · References-A ✅ `030ffba` · References-B `[N]` ✅ `4fb0240`
   · G4 cap ✅ `3807144` · G6 heading-leak ✅ `5009d8a` · H3 ✅ `bf32bbe` · detail residue (relations table +
   pi-tui roster) ✅ `728e524`/`353a95e`/`f807903` · diagram_render hygiene ✅ `1575cc2` · G3 aggregator/mirror de-rank ✅ `0d034d4`. **ALL gold-parity axes CLOSED — zero open items.**

**FROZEN until the report visibly changes** (internal-quality, from §14/§12):
verdict memoization per text-hash · AST swallowed-content lint (markdown-it-py) ·
L1 editorial pass + crashed/unscored status rule · S3 guard consolidation ·
S5 remainder · echoed-H1 + em-dash rf residuals (§16).

**CARRY-OVER from CONSOLIDATED (priorities unchanged, deleted items removed):**
- P1 presentation ladder: wire `presentation_classifier.py` · table/list
  generators + deterministic format-fixer under the SAME accept gate ·
  frontend judge-model selector + presentation action list.
- P2: epoch-boundary re-review (Workstream P v2). ~~P2-7 live diagram E2E~~
  (attempt 10 IS that run) · ~~P2-8 references~~ · ~~P2-8b code-shaped~~ (both ✅).
- P3: Workstream D ResearchConfig intake (HARD CONSTRAINT: stays out of
  task_hash) · Workstream J remainder (loop/skill CRUD, tabs, remote sources).
- P4: O3 JSONL finding contract (verify `_cited_urls` premise first) · O5
  deterministic assembler (⚠ overlaps §16 rebuild WRITE/ASSEMBLE — reconcile
  before building either) · O9 `test_bad_report_quality_gate.py`.
- P5: K observations.py + pre-dispatch validation + SQLite store · L PDF/PNG ·
  M trace-derived metrics + `metrics_json` + per-task-hash aggregation ·
  persistence columns (`publish_gate_json`, `metrics_json`, `trace_path`,
  `checkpoints_path`).
- P6 hygiene: perf A/B benchmark · live `failed_partial` exercise ·
  `_verify_prompt` SATISFIED-bar re-evaluation · GUI quick-start #5 ·
  wall-clock cap for count budgets · per-task web-cache scoping · doc marker
  back-fill · runner.py decomposition (S2 already took −869 lines; continue
  via S-workstreams, ~~"after depth closes"~~ now interleaved).
- ~~P0-1 cold run~~ (ran 6×, answer: depth still capped → item 3 above) ·
  ~~P0-2b~~ ✅ · Deferred-by-design list unchanged (sandboxed exec, approval
  queue, ZIP, evidence query table, DeepEval/RAGAS, MESH fold, shared-lib asks).

### 1.3 Attempt-11 success check

(old §15 tail)

Success check for the pivot: attempt 11 artifact must contain (a) ≥1 Craft-specific
cited source, (b) sections averaging materially more substance, (c) **THREE
diagrams** (user refinement 2026-07-05 late, twice-stated): a Pi architecture
diagram, a Craft architecture diagram, THEN an integration diagram — the
integration one with a cluster per subject and ≥1 labeled cross-edge, each
cross-edge grounded in a joint claim or a documented interface on each side;
a single-subject diagram alone, or two disconnected islands, fails. Generic
form: one architecture diagram per subject + one integration diagram.
(d) **SAMPLE CODE follows the same pattern** (user generalization, same night):
one code example per subject + one INTEGRATION example composing them —
integration code may be synthesized (compile-gated, uses only claim-documented
interfaces, captioned as proposed usage, never presented as quoted source).
(e) **Relationship as cross-cutting dimension**: for N≥2 subjects, the skeleton
includes an integration section by construction; multi-subject sections draw
from __joint__/relationship claims; the summary states the relationship
conclusion. User principle (quote): "if 2 or more objects mentioned in task,
all the flow in the article need to consider their relationships."
Anything less = pivot not achieved, rediagnose.

### 1.4 Prioritised implementation schedule (2026-07-08)

The remaining backlog (Part 1.2 FROZEN + CARRY-OVER, Part 5 writer/editorial/design,
Part 8 recovery) ordered into six sequential phases. Rule: **visible artifact quality
gates everything** — no internal-quality, infra, or UX work counts until a bare Pi/Craft
run clears Part 1.3 a–e *consistently* (not one lucky run). Within a phase, items are
dependency-ordered; a phase completes when its Done-when holds, then the next begins.
Effort: S ≤ ½ day · M ≈ 1–2 days · L ≈ 3–5 days. Every landed item gets a Part 3.1 row.

**Phase 0 — Measure the ceiling. ✅ DONE (2026-07-08, harness `scratchpad/phase0_variance.py`).**
- 0.1 Ran the bare Pi/Craft acceptance **5×** on post-MVP-9 code. **FINDING (inverts the "variance"
  hypothesis): output is fully DETERMINISTIC** — all 5 runs byte-identical (score 0.5797, 1546
  words, 2 diagrams). gemma greedy-decodes deterministically + the web cache is now stable, so
  there is no run-to-run variance to average out; there is a fixed CEILING.
- **Deterministic a–e result: 5/6 every run.** Pass 5/5: a (craft-agents-oss cited), b (1546 >
  1468 words), d (2 code blocks), e (relationship section "Integrated Agentic Workflow"),
  fmt (References last, ⊆ claims). **Sole failure: c (three diagrams) — only 2 render.** *(Superseded: 3 diagrams incl. a grounded integration diagram now render live; c passes.)*
- **Root cause (claims.jsonl, run 1): research COVERAGE imbalance, not depth-of-prose or model.**
  14 claims total = **Pi 2 · Craft 12 · joint 0**. ZERO joint claims → the integration diagram
  cannot ground (correct no-fabricate) → that IS the entire `c` miss. Pi under-covered (2 vs 12)
  → its subject diagram degrades to glue-word labels ("Designed/Harness/Users/Adapt") despite the
  naming prompt, because thin claims give the component extractor nothing better. Words/model/
  determinism are NOT the lever; **per-subject + joint-claim coverage is.**
- **⇒ Phase 1 re-scope:** promote 1.2 (coverage gate) to the PRIMARY item; the gate must cover
  both a per-subject claim floor AND joint-claim coverage (0 joint claims must trigger recovery,
  not silently drop the integration diagram). Word-count depth (old 1.1) is deprioritised — 1546
  words already clears the bar.

### Part 1.4-GS — Gold-standard gap analysis (2026-07-09)

Method (user-directed, AI-leveraged): a human researcher (Claude) did the SAME task by hand, wrote a
reference report (`scratchpad/gold_report_pi_craft.md`), and gap-analysed studio's run `s_7c3236cf4ad0`
against it. Target: **studio produces reports of similar quality to the hand-researched gold standard.**
This is the eval that drives quality parity — re-run it whenever the pipeline changes.

Gaps found (studio vs gold), each mapped to a work item and status:

| # | Gap | Evidence (studio run) | Fix / plan item | Status |
|---|-----|-----------------------|-----------------|--------|
| G1 | **Joint grounding** — no integration diagram; relationship section ungrounded | joint=0; only 2 subject diagrams | Anchor-confirmed page identity + within-page collision guard (`_subject_present`/`_page_subjects`/`_tag_claim`/`_mentions_subject`) + targeted relationship extraction (`_extract_relationship_claim`) | **◐ anti-fabrication DONE, real-integration OPEN.** 4 live iterations closed 4 fabrication paths (primary/recovery/targeted/same-page-collision); `inflection`=0, no false integration diagram. But real Pi↔Craft joint STILL 0 — see G1-deep. **✅ SUPERSEDED by G1-fetch (joint 7 live) + diagram fix `686d7b0`: real integration diagram now grounds `Craft→Pi "utilizes"`.** |
| G1-deep | **Was mis-diagnosed as reader-model recall — VERIFIED (script, not inference) to be CONTENT-WINDOWING + FETCH QUALITY.** `verify_extraction.py` ran the pipeline's own extraction on the real source-007 (121,671 chars) with gemma AND haiku: the old `content[:8000]` window was pure GitHub nav chrome (chars 0–8k); the architecture sentence "uses the Pi SDK side by side" is at char 93,139. gemma extracted "6.5k stars" — because that chrome was all it saw. **haiku extracted the same chrome → reader model is NOT the constraint.** | `verify_extraction.py` on `s_fa2816b1e000/source-007` | **PARTIALLY DONE:** `_extraction_window` (relevance-scored paragraph selection over the WHOLE file, honouring the "moving window / never head-truncate" principle) replaces the blind head cut. Post-fix, gemma + haiku both now extract a REAL `['Craft','Pi']` joint claim (no Inflection). Live run would ground a real integration diagram. | ◐ window fix DONE |
| G1-fetch | **Root cause was head-truncation of a long page (not fetch quality per se).** `_CLAIM_SOURCE_CHARS=8000` on a 121k-char rendered page = extraction saw only nav chrome; the architecture sentence is at char ~93k (para 39 of 229). | `verify_extraction.py` + para-index diagnostic | **✅ DONE — READ THE FULL FILE (user directive).** `_extraction_window` (relevance select) → then `_content_windows` **moving window** over the WHOLE boilerplate-stripped file (overlapping, bounded, dbg-logged cap). Live proof run `s_dec853a62198`: **joint 7 (was 0), 3 diagrams incl. a real integration diagram**, authoritative claim landed: *"utilizes both the Claude Agent SDK and the Pi SDK simultaneously."* + `_strip_boilerplate` drops nav chrome generically. | ✅ DONE + live-verified |
| G1-noise | **Residual collision when the LLM paraphrases the qualifier away.** Moving-window now reads provider-list sections too; the LLM rephrases "Inflection Pi" → bare "Pi is one of the compatible models", so `_mentions_subject` (which keys on the compound proper noun) can't catch it → a few noisy joint claims + `inflection`=5 in the artifact. | run `s_dec853a62198`: 7 joint incl. "Pi is one of the many compatible models…" | **NEW — semantic guard**: the collision guard is lexical (compound-noun); a paraphrase defeats it. Options: (a) judge-verify each JOINT claim ("does this assert a real A–B relationship, or a coincidental co-mention?") on the strong reader; (b) drop joint claims whose quote's subject token is a compound-proper-noun even if the claim paraphrases it (check the QUOTE, which is verbatim, not just the claim). | ✅ DONE: (b) already live in `_subject_supported`; (a) added as `_verify_joint_claim`/`_filter_joint_noise` (post-CLAIMS, pre-coverage, fail-open) — keeps all 4 real joint claims of the 6/6 run |
| G-pdf | **PDF sources were unreadable** — `web_fetch` returns binary garbage for a PDF; no PDF lib installed → a PDF source produced no usable text. | user-flagged; confirmed no PDF path in `tools.py`/`web_toolkit` | **✅ DONE — `_fetch_pdf_text`** (pypdf) in `studio/tools.py::_fetch_page`: `.pdf` suffix OR arXiv `/pdf/<id>` path → download (25 MB cap), verify `%PDF` magic, join per-page text; falls through to HTML if not actually a PDF; fail-open on any error. Then the moving window reads it like a README. Verified live on a real arXiv PDF (39,625 chars) + 4 unit tests. | ✅ DONE + verified |
| G2 | **Diagram labels are prose glue** | `Pi→Designed/Harness/Users/Adapt` | Identifier-shaped component harvest (hyphenated/dotted/CamelCase + all-caps acronyms), drop bare prose words; stop rejecting real sub-components that contain the subject token (`pi-ai`) | ✅ DONE 2026-07-09 (`_subject_feature_labels`) |
| G3 | **Source authority** — cites an aggregator (`hotools.com`) + the WRONG product (`www.craft.do` doc editor, not the agent app); misses every primary repo/README/PR | 7 refs, ~3 low-value/wrong | **NEW — source-selection authority pass**: prefer official repos/READMEs/vendor docs; de-rank aggregators + same-name-wrong-product; ground selection in disambiguation anchors | ✅ DONE — primary-content selection CLOSED by query-directed extraction (GS2); titled refs `030ffba`; **aggregator/mirror de-rank ✅ `0d034d4`** — `_classify_source_authority` (LLM primary/secondary, no domain literal) + `_order_claims_primary_first` (shared reorder feeds refs + `[N]`, order-only so no dangling marker). Live `s_fda2c420bd59`: 5 primaries #1-5, deepwiki #6 + hotools #7 (were interleaved #3/#6) |
| G4 | **Citation stuffing** — same URL repeated after every sentence | `pi.dev/docs/latest` ×N | **NEW — citation diversity/dedup**: distinct source per claim; cap repeats of one URL | ✅ DONE — raw-URL stuffing eliminated by References-B `[N]` render (`4fb0240`) + per-paragraph repeat cap `_dedup_markers_per_paragraph` (`3807144`, valid-ref-guarded so a non-citation `[2024]` survives). Live v84: 0 paragraphs with a repeated marker |
| G5 | **Architectural depth** — generic prose, no package/layer structure | "minimal harness, extensible" vs gold's pi-ai→pi-agent-core→pi-coding-agent table | Ties to known **shallowness root cause** (findings.py one-sentence contract); component/layer extraction from claims | ✅ DONE — full package taxonomy via query-directed extraction + roster enumeration (`f807903`, pi-tui now named, was 3+summary); dependency order + integration relationships surface in the `relations` fan-out table (`728e524`+`353a95e`): live `Pi SDK → pi-ai/pi-coding-agent/pi-agent-core`, `pi-agent-core sits between pi-ai/pi-coding-agent`, `Craft Agents → REST APIs/MCP servers/local filesystem`. Live-verified `s_b320f6fb3670` |
| G6 | **Heading leak** — content bullets promoted to H2, breaks ToC | 5 leaked H2s ("Initialize the open-source interface…") | **Phase 2.1 AST swallowed-content lint** (markdown-it-py) | ✅ DONE `5009d8a` — per-section `_sanitize_section_headings` (demotes body `#`/`##`→`###`) + artifact-wide `_demote_stray_h1` backstop; live v83 = 1 H1 |
| G7 | **Example code is generic**, not grounded in the real API surface | 2 generic blocks vs gold's real `Agent` hooks/SDK/RPC | **Phase 3 writer** — code grounded in fetched SDK docs, not invented | ✅ DONE `0bf8c69` — example code grounded in real evidence + invented-SDK guard; live 0.92, fabricated `from craft_agents import` gone |

**⇒ Phase 1.5 — quality-parity pass (UPDATED 2026-07-10).** Acceptance closed (0.92–1.0 live).
DONE: G3 primary-content selection (extraction), G4 per-URL cap + References-B inline `[N]` (`4fb0240`+`3807144`),
G5 architectural depth (relations table + roster `f807903`), G6 heading-leak lint (`5009d8a`), G7 code
grounding (`0bf8c69`), diagram edge (`686d7b0`), References-A titled entries (`030ffba`), detail residue
(dependency order + pi-tui + provider/relations table — tasks 1/2/3, live-verified), G3 aggregator/mirror de-rank
(`0d034d4`, live `s_fda2c420bd59`). **ALL Phase 1.5 gold-parity items CLOSED — nothing un-done.** See Part 1.4-GS4 tracker.

### Part 1.4-GS2 — Refreshed gap analysis (2026-07-09, POST synthesis + refs-last fix)

Re-ran the gold eval against the CURRENT pipeline (run `s_9f3534785ca6`, index rotation, 2152 words,
3 diagrams, 3 code) after the section-synthesis fix (byte-identical section openers → real synthesis,
0.4438 → 0.5706/0.6552 deterministic) and the assembler references-last fix. Gold baseline re-verified
2026-07-09 against the **primary READMEs** (`earendil-works/pi` — MIT, 4 packages pi-ai→pi-agent-core→
pi-coding-agent→pi-tui; `lukilabs/craft-agents-oss` — Apache-2.0, Electron/Bun, *"uses the Claude Agent
SDK and the Pi SDK side by side"*, Pi SDK routes Google/Codex/Copilot/OpenAI, Claude Agent SDK routes
Anthropic + third-party). The four axes the user named:

| Axis | Studio (current) | Gold | Verdict |
|------|------------------|------|---------|
| **Detail level** | Generic prose — "a stateful loop", "the `AgentHarness`" (deepwiki vocabulary); no package taxonomy, no layer stack, no lifecycle hooks | Names all 4 packages + responsibilities, dependency order, `beforeToolCall`/`afterToolCall` hooks, tree sessions | **GAP — G5.** Depth ceiling still real; content reads as a *paraphrase of a secondary wiki*, not the primary structure. |
| **Sections** | 9 flat H2 (Exec/Scope/Background/Findings/Evidence/Implications/Limitations/Refs/Integration); distinct now (synthesis fix landed) | Same skeleton **+ H3 sub-sections** (F1–F4, per-architecture, per-example) | **NARROWED + small new gap G8.** Section coverage now matches; gold's H3 granularity aids depth/readability. |
| **Reference docs** | 7 refs incl. **`deepwiki.com/badlogic/pi-mono`** (secondary mirror), **`hotools.com`** (aggregator), `pi.dev/packages/pi-agents` (unverified); MISSES every primary README + the "two backends" source | 8 refs, all primary/authoritative (repo READMEs, SDK docs, Craft OSS README, PR) | **GAP — G3, now the #1 lever.** Studio's *deep content came from deepwiki, not the README* → this is the master cause of the detail + diagram gaps below. |
| **Diagram quality** | 3 mermaid, but: (1) Craft diagram has a **`Inflection Pi`** node (wrong-Pi collision leaked into label harvest); (2) integration diagram draws a **fabricated `AgentHarness --MCP--> Craft MCP servers` bridge** — NOT the real "Craft runs the Pi SDK as one of two backends"; (3) Pi diagram uses deepwiki's `AgentHarness` vocab, not the 4-package stack | 3 correct purpose-built: Pi layered dependency, Craft two-backend selector, Craft-embeds-Pi provider routing | **GAP — G1-diagram + G2-residual.** Joint claims now exist, but the diagram *edges* are invented, and the integration is mis-drawn as an MCP link. |

> **⚠ SUPERSEDED (2026-07-09 PM) — the GS2 "Refreshed gap analysis" below is historical.** Its
> master-finding (G3 source-authority as the root lever) and 4-item priority were largely resolved by
> the query-directed **extraction fix** (Part 1.4-GS2 result: 0.5706→0.92, names the 3 primary packages
> from the README not deepwiki) and the **diagram fix** (`686d7b0`, live v80). Kept for history; the live
> axis status is Part 1.4-GS4.

**Master finding (new): source authority (G3) is the root lever, not a peer gap.** Studio grounded its
deep content in `deepwiki` (a secondary Pi-mono mirror) and `hotools.com` (an aggregator). That single
choice cascades into three of the four axes: the *detail* is deepwiki's "AgentHarness/stateful loop"
framing instead of the README's package taxonomy; the *diagram vocab* inherits the same secondary terms;
and the *integration diagram* invents an MCP bridge because the real "two backends side by side"
statement (README line, primary) was never fetched/cited. **Fix G3 first — prefer primary
repo/README/vendor-doc sources, de-rank aggregators + secondary mirrors, ground selection in the
disambiguation anchors — and G5/diagram-vocab/G1-diagram all move with it.** The synthesis fix proved
the WRITE stage can now integrate what it's given; the binding constraint has shifted upstream to WHAT
it's given (source selection), exactly as the "shallowness root cause" memory predicts once the
one-sentence-contract is relaxed.

Refreshed priority after Phase 1 closes (2026-07-10: **ALL items done, zero open**):
1. ~~**G3 source authority**~~ ✅ DONE — primary-source selection (extraction) + aggregator/mirror de-rank `0d034d4` (LLM classify + primary-first reorder; live `s_fda2c420bd59`: deepwiki/hotools sunk to #6/#7).
2. ~~**G1-diagram grounding**~~ ✅ DONE `686d7b0` — integration edge from the joint claim's directional triple (`Craft→Pi "utilizes"`); wrong-Pi collision guard covers label harvest.
3. ~~**G5 architectural depth**~~ ✅ DONE — package/layer + dependency order via query-directed extraction + roster (`f807903`) + relations table (`728e524`+`353a95e`).
4. ~~**G8 (minor)** — H3 sub-section granularity~~ ✅ DONE `bf32bbe` — `_write_section` licenses `###` sub-topics; live 7 subheadings.

Newly-DONE since the last gap pass (do not re-open): section-synthesis (distinct openers + analysis;
the `_claims_for_section` per-section rotation + the `_write_section` synthesis directive), assembler
references-last (`assemble_artifact_from_sections`).

### Part 1.4-GS3 — Thorough 4-axis comparison vs the CURRENT 0.92 report (2026-07-09)

Redone against `s_dd5d05c27ae6` (score 0.92, the post-extraction-fix output), on the four axes the
user named. Gold baseline = primary-README-sourced reference report.

> **STATUS (2026-07-09 PM): the parity table below is the STARTING point; live status is Part 1.4-GS4.**
> Code axis DONE `0bf8c69`; Diagrams edge DONE `686d7b0` (live v80, no longer "~40%/WEAKEST"); References
> A (titled numbered) DONE `030ffba` (no longer bare-URL "~60%"). Ranked items 1–2 below are COMPLETE.

| Axis | Studio 0.92 | Gap to gold | Parity |
|------|-------------|-------------|--------|
| **Detail** | Names pi-ai/pi-agent-core/pi-coding-agent + the correct "Claude Agent SDK and Pi SDK side by side" integration (gold-parity on core facts) | Misses layered DEPENDENCY order, omits pi-tui, no provider-routing table; theme repetition across 4 sections | ~80% |
| **Sections** | 9 sections match gold's skeleton | 2 HEADING LEAKS (H1 inside body: `# To run the project…` L47, `# Minimal example…` L77) break ToC (G6); flatter (no H3) | ~90% |
| **References** | 7 refs | Still cites deepwiki (secondary), hotools.com (aggregator), dead `pi.dev/packages/*`; citation stuffing (`agents.craft.do` ×13, `earendil-works/pi` ×9) (G3-residual + G4) | ~60% |
| **Diagrams** | 3 present | WEAKEST. D1 deepwiki vocab (AgentHarness/AgentLoop/AgentInstance), only 2 packages; D2 circular+empty (`Craft→Craft Agents`), misses two-backend; D3 integration EDGE wrong (`coding-agent-CLI → "specialized task-execution layer" → Craft`), Craft subgraph empty (G1-diagram + G2-residual) | ~40% |

**NEW serious finding — fabricated example code (G7). ✅ RESOLVED `0bf8c69`** (example code grounded in
real fetched evidence; a synthesized programming-language block that imports a package is dropped as an
unverified SDK; live: `from craft_agents import` gone, Craft shown via its real NL interface). Original
finding kept below for context. The Craft code block invents a Python SDK that
exists in NO fetched source: `from craft_agents import Agent`, `agent.connect_model()`,
`agent.navigate_to()`. Craft is an Electron/Bun app driven by natural language + `craft-cli` — no Python
API. This is fabrication in the deliverable: the anti-fabrication contract (verbatim quote per CLAIM)
does NOT cover generated CODE, so `_splice_code`/`_splice_integration_code` can hallucinate an API.

**Ranked remaining work to gold-parity (updates the priority list above):**
1. ~~**Diagram grounding (G1-diagram / G2-residual)**~~ ✅ DONE `686d7b0` — integration edge grounded from
   the directional triple (`Craft→Pi "utilizes"`), live v80; endpoint-pick nit remains.
2. ~~**Code grounding (G7)**~~ ✅ DONE `0bf8c69` — example code grounded in real evidence; invented-SDK guard.
3. ~~**Reference quality (G3-residual + G4)**~~ ✅ DONE — titled numbered entries `030ffba`; inline `[N]` (B)
   `4fb0240`; grounding residual (strip same-domain non-claim URL) `5009d8a`; G4 per-paragraph cap `3807144`.
4. ~~**Heading-leak lint (G6)**~~ ✅ DONE `5009d8a` — `_demote_stray_h1` artifact-wide backstop (1 H1, fence-aware).
5. ~~**Detail residue**~~ ✅ DONE — `relations:[{head,rel,tail}]` field + subject-grounded fan-out table
   (`728e524`+`353a95e`, reviewer MEDIUMs fixed `f841c51`); pi-tui roster enumeration `f807903`; dep-order lands
   via relations (`pi-agent-core sits between pi-ai/pi-coding-agent`). All live-verified. See Part 1.4-GS4 tracker.

### Part 1.4-GS4 — Axis status (2026-07-09 PM, live-verified)

Working the GS3 list one axis at a time; acceptance = live-run artifact, not score.

| Axis | Status | Evidence |
|------|--------|----------|
| **Code (G7 fabrication)** | ✅ DONE | commit `0bf8c69` — example code grounded in real evidence, invented-SDK guard; live 0.92, fabricated import gone |
| **Diagrams (G1/G2 edge)** | ✅ DONE | commit `686d7b0` — integration edge was `Pi.LLM APIs →"craft agents"→ Craft` (reversed + node-name label); now `Craft.Claude Agent SDK →"utilizes"→ Pi` (grounded directional triple). **RCA reversal:** the wrong edge shipped from `research_first` (subgraph fingerprint), NOT `diagram_render` — the "consolidate to fix wrong module" premise was false; real cause was **client provenance** (tool-augmented client degraded the deterministic triple sub-prompt; fixed via `tools.base_client()` unwrap). Live v80, edge correct, score 1.0. Endpoint pick still imperfect (ideal `Craft Agents → Pi SDK`) = open nit. |
| **References (G3/G4)** | ✅ A+B DONE | **A (titled numbered entries): ✅ `030ffba`** live-verified — `N. [page-title](url)`. **B (inline `[N]` citations): ✅ `4fb0240`** live-verified (v82/v83, 32 markers) — deterministic `_apply_citation_markers` renders each inline URL the writer already cited → its reference number `[N]` (better than codex's writer-proposes C: the writer emits explicit URLs, so the marker is a *render* of a declared citation, zero false-attribution; also de-stuffs G4 raw-URL clutter). **Grounding residual: ✅ `5009d8a`** — a same-domain non-claim URL (hallucinated `pi.dev/packages/pi-agent-workflows`) is now STRIPPED (fake citation removed, domain-grounded sentence kept), never a guessed marker. **G4 per-paragraph cap: ✅ `3807144`**. |
| **Diagrams — hygiene** | ✅ DONE `1575cc2` | `diagram_render` is now the single mermaid-construction authority: moved the cluster/triple helpers + `build_cluster_components_prompt` + new `render_subject_cluster_diagram` entry; `research_first` keeps only orchestration (`Relationship` stays out via a passed `integration_label`). ALL shims removed, call sites qualified, zero dead/duplicate diagram code (ruff clean); 1101 tests pass; offline diagram edge identical (`Craft→Pi "utilizes"`). |
| **Sections (G6 heading leaks)** | ✅ DONE `5009d8a` | `_demote_stray_h1` artifact-wide backstop: exactly one H1 (title), a leaked body `# ` demotes to `### `, fence-aware. Live v83: 1 H1. Complements per-section `_sanitize_section_headings`. |
| **Sections — H3 sub-structure** | ✅ DONE `bf32bbe` (task 3/3) | `_write_section` dropped the "(no headings)" ban and licenses `###` level-3 sub-topics for distinct aspects. Verified the G6 guards (`_sanitize_section_headings`+`_demote_stray_h1`) PRESERVE intentional `###` while still demoting leaked `#`/`##` — compatible by construction, so the prompt is not silently defeated (test `test_sanitize_section_headings_preserves_h3_subheadings`). Non-deterministic (model chooses); machinery now allows it. 153 tests. **Live-confirmed** (`s_a4a244ff795d`): 7 substantive `###` subheadings, no leaked `#`/`##`. |
| **Detail — relations/relationship table** | ✅ MECHANISM DONE `728e524`+`353a95e` (task 1/3) | Claim schema gained `relations:[{head,rel,tail}]` — the LLM emits triples, code keeps only those whose BOTH endpoints are WHOLE-TOKEN grounded in the verbatim quote (reviewer MEDIUM fix: word-boundary, not substring, so `Pi`/`Go` can't ground on `shipping`/`Google`). `_splice_relationship_table` renders a **subject-grounded, fan-out-ranked** table (v1 shipped junk — alphabetical+cap surfaced `AppMenu`/`It` noise, truncated the real fan-out; construction fix filters to subject/anchor-grounded sources, fan-out-only rows, ranked by fan-out size). Replay on REAL live claims (`s_8dc0951732bc`) leads with Pi architecture, Craft integration surface, and the **pi-agent-core dependency order** — genuine G5 depth. Generic (no domain/provider literal). **Live-confirmed via the real pipeline** (`s_a4a244ff795d`): table renders `Pi SDK → uses pi-ai/pi-coding-agent/pi-agent-core` (the roster), `pi-agent-core → sits between pi-ai/pi-coding-agent` (dep-order), `Craft Agents → REST APIs/MCP servers/local filesystem`; no structural junk. |
| **Detail — dep-order + pi-tui** | ✅ DONE (prompt fix, live-verified) | **Root cause (verified on evidence, not fetch gap):** source-001 has BOTH a complete 4-row package TABLE (incl. `pi-tui`) AND a prose sentence "the three Pi packages (pi-ai, pi-coding-agent, pi-agent-core)"; the extractor grabbed the tidy summary sentence and skipped pi-tui's table row. Fix = a pointed one-sentence extraction directive: "if the source lists components in a TABLE/list, emit ONE claim per ROW — never collapse a roster into a count; the full table is the authority, capture every row the summarising sentence omits." **Live-verified** (`s_b320f6fb3670`): pi-tui now a claim (`@earendil-works/pi-tui is a Terminal UI library…`) AND in the artifact (diagram node + prose). Patch attempt 1 sufficed — deterministic roster parser (designed, quote-grounding proven) NOT needed. dep-order already lands via relations (`pi-agent-core sits between pi-ai/pi-coding-agent`). |

### Part 1.4-GS2 result

**RESULT — query-directed extraction closed G3+G5 (2026-07-09, deterministic).** The "app reads all
evidence but not equally" question (user) traced to extraction, not fetch: the Pi README's 4-package
table produced 0 package claims (3 compounding causes — priority-blind `extract 3-6 facts` prompt;
package names trapped in `[name](url)` markdown; `_strip_boilerplate` deleting the link-dense table as
nav chrome). Fix = query-directed + structure-aware extraction + `_plainer_markdown` + per-line
nav-vs-content boilerplate rule (all in `_extract_claims_from_source` / `_strip_boilerplate`).
Direct evidence-replay: Pi README 0/4 → 3/4 named, 4/4 by role. Live (deterministic, confirmed ×2):
**score 0.5706 → 0.92, a-e 6/6**, report now names pi-ai/pi-agent-core/pi-coding-agent + the correct
`Claude Agent SDK` + `Pi SDK` two-backend integration (gold-standard content). G5 (architectural
depth) and most of G3 (source authority — primary content now dominates the vocabulary) are CLOSED by
this. Remaining minor: G1-diagram edge grounding, G2-residual (`Inflection Pi` label harvest), G4
citation dedup, G6 heading-leak lint. The one-sentence contract was NOT relaxed (verified unnecessary:
the depth came from reading the source better, not from longer claims — the contract stays load-bearing
for anti-fabrication).

**Phase 1 — Close acceptance (visible outcome). [M–L] — ✅ DONE (2026-07-09).**
Acceptance reached: deterministic live runs hold 0.92–1.0, a–e clears (3 diagrams incl. a grounded
integration diagram, real labels). The Phase-0 `c` failure (0 joint claims + Pi under-coverage) was
fixed by the query-directed extraction + moving-window fetch (joint 0→7, G1-fetch DONE) and the diagram
edge fix `686d7b0`. Items 1.1/1.2 satisfied; 1.3 summary-grounding is the only minor residual. Phase 2
(+ the GS4 gold-parity axes) is now the active work.
Scope set by the Phase-0 readout: the deterministic failure was `c` (integration diagram absent)
caused by **0 joint claims + Pi under-coverage (2 vs 12)**. Attack coverage, not word-depth.
- 1.1 **Coverage gate before WRITE — per-subject floor AND joint-claim coverage** (Part 8 #1 /
  #37 P1-core, was PARTIAL; now PRIMARY). Before WRITE, require each subject ≥ a small claim
  floor AND ≥1 joint claim for an N≥2 relationship task. On a miss, fire ONE generic recovery
  action — an imperative directed search/fetch (per subject for the thin one; a joint
  `"<A> <B> <mechanism>"` query for the missing joint evidence) — then re-check; still empty →
  `failed_partial` diagnostic, never silently drop the integration diagram or ship glue labels.
  Generic, fail-visible. [M]
- 1.2 **Joint-research strengthening** — the FRAME relationship hypothesis already derives joint
  queries; this run produced 0 joint claims, so either the joint fetch is too narrow or its
  results get anchor-gated out. Widen/relax the joint fetch (still grounded) so an integration
  task reliably yields joint claims → the integration diagram grounds → `c` passes. [M]
- 1.3 **summary_mechanism_grounding** — resolve the persistent harness FAIL: real gap vs
  over-strict checker (it flags claim-present words like "agentcontext"/"api"). Either tighten
  `_write_summary` grounding or relax the check to domain-level, with a canary either way. [S]
- **Done-when:** the (deterministic) bare Pi/Craft run passes Part 1.3 **a–e in full** (3 diagrams
  incl. a grounded integration diagram; real component labels, not glue) — one run suffices since
  output is deterministic — recorded + separate-lane review. Re-run Phase 0's 5× only to confirm
  determinism still holds after the change.

**Phase 2 — Internal quality + loop-health (unfreeze; structure is now clean enough). [M]**
The Part 1.2 FROZEN list + the remaining L-/S-workstreams (Part 5.5 / 6.3) — was gated on
"report visibly changes", now unblocked by Phase 1. (Already ✅, do NOT re-do: L2, L3, L6,
S1, S2, S4, and verdict/offtopic memoization — the DONE half of L5/S5.)
- 2.1 **AST swallowed-content lint** via markdown-it-py (already a transitive dep) — catches the
  giant-fence-swallows-headings class generically. [M]
- 2.2 **L1 — Verify hardening (RC1)**: the §10.2 editorial checklist + crashed/unscored status
  rule — this is the E1–E11 unifier and completes E3/E5 (Part 5.2). [M]
- 2.3 **S3 — guard primitives** (`studio/guards.py`) — consolidate the scattered ad-hoc guards
  into one module (MEDIUM risk); prerequisite for L4. [M]
- 2.4 **L4 — Repeat-weakness escalation (RC4)**: uses the S2 pass ledger + L1 row evidence to
  detect weaknesses recorded in ≥ REPEAT_LIMIT prior runs and drop/escalate them instead of
  grinding forever (builds on S3). [M]
- 2.5 **L5 remainder — acceptance economics** (verdict memoization already ✅): the per-run
  postrun cost block — per-pass attempted/accepted counts + tokens per accepted change
  (data already flows through `timing_sink`/`_dbg`; aggregate it). Feeds → **S5 measured
  efficiency wins** (rubric memo, cache index) — numbers-gated, ship ONLY with before/after
  timing. [M]
- 2.6 **Residuals**: echoed-H1 + em-dash research_first residuals (§16). [S]
- **Done-when:** no regression in Phase-1 acceptance; each guard has a canary; S5 wins carry
  measured before/after numbers.

**Phase 3 — Writer & design process (Part 5.1/5.3). [L]**
- 3.1 Writer reference process P1–P4 (Part 5.1) — the P1 bundle. [L]
- 3.2 Question-first planning (P2), disambiguation probe (P3, rides on P1), not-found contract
  (P4, rides on P1). [M]
- 3.3 Design workstreams D1–D5 (Part 5.3). [L]
- **Done-when:** measured artifact-quality delta vs Phase-1 baseline (else defer as gold-plating).

**Phase 4 — Presentation ladder + R3 follow-ups. [M–L]**
- 4.1 Wire `presentation_classifier.py`; table/list generators + deterministic format-fixer under
  the SAME accept gate; frontend judge-model selector + presentation action list. [L]
- 4.2 R3 follow-ups: comparison-table richness beyond axes+pros/cons; live-exercise the
  competes/independent branches (currently unit-tested only); N≥3-subject relationship
  (`n_subject_relationship_evidence`). [M]
- **Done-when:** presentation shapes ship under the accept gate; a compete-shaped and an N≥3 task
  each produce a correct artifact live.

**Phase 5 — Persistence & observability (Part 4/5 infra). [L]**
- 5.1 O3 JSONL finding contract (verify `_cited_urls` premise first) · O5 deterministic assembler
  (reconcile with the rebuild WRITE/ASSEMBLE — do not double-build) · O9 `test_bad_report_quality_gate.py`. [M]
- 5.2 K observations.py + pre-dispatch validation + SQLite store · M trace-derived metrics +
  `metrics_json` + per-task-hash aggregation · persistence columns (`publish_gate_json`,
  `metrics_json`, `trace_path`, `checkpoints_path`) · L PDF/PNG export. [L]
- **Done-when:** every run persists structured metrics + publish-gate JSON for drift comparison.

**Phase 6 — Config, UX & hygiene (Part 3/6). [M]**
- 6.1 Workstream D ResearchConfig intake (HARD CONSTRAINT: stays OUT of task_hash) · Workstream J
  remainder (loop/skill CRUD, tabs, remote sources) · GUI quick-start #5. [M]
- 6.2 Hygiene: perf A/B benchmark · live `failed_partial` exercise · `_verify_prompt` SATISFIED-bar
  re-evaluation · wall-clock cap for count budgets · per-task web-cache scoping · doc marker
  back-fill · runner.py decomposition (continue the S2 −869-line trend via S-workstreams). [M]
- **Done-when:** config is user-editable without lineage rotation; runner.py under the file-size
  target; perf baseline recorded.

**Cross-cutting invariants (hold in every phase):** no hardcoded task/subject strings or domain
vocab in production logic · fail-open with a dbg-log on every LLM/IO path · author and review in
separate passes (no self-approval) · verify against a live run, not a green suite · restart the
no-reload server + confirm process start-time > commit before trusting any live result
([[reference_stale_server_verification_trap]]).

## Part 2 — Active tracks

### 2.1 Outcome-first pivot rationale

(old §15 body; OUTCOME-FIRST RE-PRIORITIZATION, 2026-07-05 night, user-directed:
"need to see different outcome")

Six runs of honest scores (0.145→0.30→0.66→0.43→0.41→0.49) said the machinery half
was fixed but the READ REPORT hadn't changed character: no Craft, shallow sections,
generic diagram. *(Dated 2026-07-05 — no longer true: the research_first rebuild reached
acceptance 0.92–1.0 with Craft cited, fuller sections, and a grounded integration diagram.)*
Re-ranked by visible outcome delta:

1. **P1-core coverage actor** (was slice 5, now immediate): deterministic
   zero-sources check per covers-X subject (evidence/ + cache scan for subject
   tokens); when zero, inject an IMPERATIVE search directive into the tool-loop/
   worker prompts ("no sources exist for <subject>: issue web_search '<subject> …'
   and web_fetch the top result BEFORE writing") — not another reducer notice.
   Verdicts without an actor produced 0 Craft searches across 3 runs.
2. **Depth contract relaxation** (NEW slice — root cause documented since §5 but
   never scheduled): loosen findings.py one-sentence contract to 2–3 grounded
   sentences per finding + revisit the density cap. The anti-quote-wall guards that
   motivated the over-correction now exist independently (junk floor, citation
   guards, salvage) — the ceiling is no longer needed at this severity.
3. **8b requirement-conditioned diagram**: build_components_prompt receives the
   requirement branch + subject list; diagram targets what was asked (full value
   only after #1 supplies Craft content).
4. A+B eraser bundle — ✅ COMMITTED (A `27beff6`, B `d4e84a9`; 925 passed, ruff clean; protects everything above).
5. Deferred: verdict memoization, AST lint, L1 editorial pass, S3 guards — internal;
   resume after the report visibly changes.

(Success check for this pivot: Part 1.3.)

### 2.2 Rebuild track: `studio/research_first.py`

(old §16, in full; ◐ IN BUILD, 2026-07-05 night; user pre-approved re-architecture)

**Why a rebuild, not another patch:** six honest runs oscillated
(0.145→0.30→0.66→0.43→0.41→0.49) because the hub/spoke generation core produces
the wrong thing by construction — coverage, grounding, and structure are all
enforced AFTER generation by guards, and guards only detect. The rebuild makes
them properties of construction (§9's writer process P1–P4 turned into code).
The verified machinery (task_runs lineage, junk pipeline, finalize repairs,
compliance extraction) is KEPT as the shell.

**Pipeline (one linear pass, no hub/spoke):**
1. **FRAME** — extract subjects/sections from the requirement; per-subject
   LLM disambiguation → `(descriptor, anchor_terms)` (probe 1 researched the
   π constant + dictionary "craft" — disambiguation is FIRST-ORDER, §9 step 0
   validated); THEN relationship resolution (user design direction 2026-07-05
   late: "disambiguate the subjects first, then consider the relationship of
   these 2") — one LLM call over the resolved descriptors + requirement →
   hypothesized integration mechanism/direction, fail-open to
   "composed side-by-side, mechanism unknown"; drives joint queries and the
   integration diagram's cross-edge candidates (hypothesis directs search,
   never becomes ungrounded content). Both identity AND relationship are
   explicit FRAME outputs surfaced in the Scope Assumptions block; emits a
   Scope Assumptions block into the artifact.
2. **RESEARCH** — unconditional per-subject search fanout + joint
   integration queries (coverage by construction, not by verdict); quoted-descriptor
   queries + anchor-density source selection (round 2); topical floor on the CLEAN
   base task text (template boilerplate split off).
3. **CLAIMS** — every claim carries a verbatim source substring
   (grounding by construction); `claims.jsonl` audit trail; escape-flood normalize.
4. **WRITE** — sections generated FROM claims (2–4 paragraphs each), code +
   diagram spliced structurally, summary written LAST.
5. **ASSEMBLE** — dedupe → rebuild references → deterministic repairs →
   mdformat (formatter-after-repairs invariant; Flowmark/PyMarkdown rejected —
   they launder broken fences into valid garbage).

**Binding constraints:** `REBUILD-LESSONS.md` (7 lesson categories mined from
194 commits + git-history second sweep; every rf-builder report must carry a
LESSONS COMPLIANCE section). No hardcoding — subject handling is generic
(user directive; test-enforced).

**Status log:**
| Step | Status | Evidence |
|---|---|---|
| Skeleton + FRAME/RESEARCH/CLAIMS/WRITE/ASSEMBLE | ✅ built (uncommitted) | first probe ran end-to-end in 83s, coverage-by-construction proven (both subjects searched unconditionally) |
| Probe 1 defect: wrong referents (π constant, dictionary craft) | ✅ fixed | `_disambiguate_subject` + `_base_task_text` topical floor; Pi now resolves to earendil-works/pi exact |
| Craft referent ambiguity (craft.do vs Craft Agents) | ✅ resolved by user | **Craft = Craft Agents OSS (craft-agents-oss, Luki Labs)** — acceptance target for round 2 |
| Round 2: anchor-density source selection + quoted-descriptor queries + anchor-mismatch drop | ✅ ACCEPTANCE MET (probe v8, independently spot-checked by team-lead) | Craft: 19 claims, craft-agents-oss cited in claims AND body ✓; Pi: earendil-works/pi + pi.dev ✓; zero definition/constant sources ✓ (π-Wikipedia dropped by offtopic floor); 25 claims / 8 sources / 1393 words / mermaid present. Builder SELF-CAUGHT a phrase-vs-token anchor-matching bug via the live probe (exact-phrase anchors dropped the real craft-agents-oss README; token-level matching fixed + regression test). Em-dash residual root-caused: `_fenced_code_compiles()` stdlib compile() gate on the LLM-fallback code path only — 0 syntax lints on latest run. tests/test_research_first.py 18 passed; full suite 927; ruff clean |
| Round 3 (final): subject-tag clamp | ✅ built + builder-verified | Probe found 3 pypi.org/agent-framework claims (Microsoft's unrelated framework) LLM-mistagged as "Craft" — wrong-referent leak class. Clamp in `_extract_claims_from_source(loop_subject=...)`: `tags != [loop_subject]` → reset to the fetch-loop's own subject. Builder's first draft (`not in tags`) would have MISSED the real case — cached pypi source was tagged `['Pi','Craft']` (both present); tightened after re-verifying against real cached data (a single-subject loop vets sources against only its own anchors — anything but exactly that subject is unvetted). 3 regression tests (absent-tag / spurious-extra-tag / no-clamp joint+matching); re-extraction on cached v8 source: `['Pi','Craft']`→`['Pi']` on all 3 claims. Suite 930, ruff clean |
| Reviewer pass (separate lane, no self-approval) | ✅ verdict: APPROVE-WITH-FIXES → fix round ◐ | 1 HIGH: `_claims_for_section` first-N cap + subject-ordered `_build_claims` starves subject-2 at WRITE ("names two subjects, covers one" reborn one stage later) — fix ordered: round-robin subjects into cap + regression test. 1 MEDIUM (`_search` excepts unlogged) + 1 LOW (evidence-write OSError silent) — log lines ordered per fail-open-WITH-a-log standard. Verified non-findings: hardcoding prohibition HOLDS (subject names only in comments explaining structural checks); no re-implementation of textutil/artifact_text; anchor token-matching, clamp both-shapes, compile()-gate scoping all confirmed correct; suite 930 re-run by reviewer independently |
| Reviewer confirm round | ✅ APPROVE | HIGH resolved: round-robin by first subject tag, generic keys, stable order, no self-starvation (zero-claim subjects absent from keys; while-guard prevents infinite loop; __joint__ rides first tag); regression test pins 10+2/cap-6 → 4+2 (first-N gave 6+0). MEDIUM/LOW log lines confirmed applied. One non-blocking residual: claims.jsonl OSError (:429) still unlogged |
| Commit `research_first.py` + tests | ✅ `c85bbc6` | 2 files, +1001 lines; 22 module tests; suite 931; ruff F821 clean |
| **Wire-in decision** | ✅ USER DECIDED (2026-07-05 night): **REPLACE hub/spoke now** | Options presented: mode-switch A/B (recommended) / replace now / hold-and-patch-old. User chose replace. Wiring dispatched to rf-builder — shell preserved (SSE, task_hash UNCHANGED → lineage 492bae60177b, recording/scoring tail), S2 content-mutating finalize passes must not double-process ASSEMBLE output, live E2E through real SSE path = attempt 11 vs Part 1.3 acceptance |
| Wiring-verification run v8 (run 1541, 0.5804) — NOT the acceptance run | ✅ wiring proven / ✗ content failed | research_first drove a live in-app run end-to-end (correct disambiguation, 3+3+2 fetches, 23 claims/8 sources, recorded in lineage). User screenshot triage, defects A–F: **A** π-constant sources (piday/wiki-Pi) back IN-APP though standalone probe dropped them — hypothesis: JOINT loop bypasses anchor gate (loop_subject=None) + joint claims LLM-tagged 'Pi' unclamped; fix = joint loop vets vs union of subject anchors + relationship terms, joint claims clamped __joint__ · **B** raw page dump as ```text fence (LaTeX/SVG markup) — quote length cap + non-prose-density reject + blockquote rule · **C** refusal-prose in artifact ("results are insufficient…") — WRITE never emits search-quality meta-commentary · **D** integration diagram = 2 boxes "integrates with" (user: "too simple, no value") — 3-diagram spec stands · **E** Microsoft agent-framework claim alive as Pi-tagged — zero-anchor sources drop at selection; clamp is for mistags, not wrong-referent sources · **F** References scraped from rendered body (LaTeX link-texts, "source", SVG links from the dump) — References BUILT from claims.jsonl exactly (both directions), never re-harvested from text |
| Wiring DELIVERED (builder report, v9 run 1542 s_b634b4076dcf 0.5903) | ✅ built, review in flight | `Session.use_research_first` set ONLY in POST /session (tests via registry default False); env kill-switch STUDIO_DISABLE_RESEARCH_FIRST; exception → fail-open fallback to _run_phase_loop; finalize `rebuild_generated` marker skips 9 content passes, 8-pass recording tail runs (dbg-confirmed live); SSE phase_start per stage; task_hash untouched. TWO SELF-CAUGHT BUGS: (1) rebuild calls web_toolkit DIRECTLY (not model-gated) → 30 tests hung on real network — old pipeline's test-safety was an ACCIDENTAL byproduct of tool-gating; session-flag design earns the contract explicitly (2 wrong routing signals rejected via suite before landing) · (2) evidence-file/claims-ledger divergence: offtopic-rejected page's raw file stayed on disk → `_find_evidence_code` spliced LaTeX; `_discard_evidence_file` at both rejection points. Suite 939. v9 readout: both referents correct (agents.craft.do + pi.dev — craft-agents-oss in claims but not cited this run), Exec-Summary π-drift from gemma memory persists |
| LEAD DECISIONS on v9 (2026-07-06) | queued as R1→R2 | (1) partial-citation resolved MECHANICALLY by defect-F fix (References=claims URLs → craft-agents-oss appears by construction), no round-robin biasing · (2) π-drift NOT accepted as "weak-model limitation": new invariant **artifact URLs ⊆ claims.jsonl** — post-WRITE deterministic drop of model-memory citations · (3) scope split: R1 correctness (defects A/B/C/E/F + URL⊆claims) → R2 features (relationship step, 3 diagrams, code set, integration section) → ONE acceptance run vs full Part 1.3 · (4) wiring commits independently after reviewer pass (in flight) |
| Quote-neutralize addendum (defect B, WRITE-side) | ✅ built + mechanically verified (v10 run, s_a55f2a9f7b3d, 0.5903, same lineage) | `_needs_blockquote` (multi-line or line starting `#`/`*`/`-`/`digit.`/`>`/fence) + `_neutralize_embedded_quotes` (structure-bearing → `> ` per line; duplicate/nested repeat of an earlier claim's quote → dropped, substring-containment whitespace-normalized), applied right after `_write_section`'s LLM call. 2 tests (blockquote-not-heading via artifact_lint zero-issues; shared quote embedded exactly once). Mechanical acceptance: artifact heading count 9 == 1 title + 8 skeleton sections EXACT, zero leaked headings; lint_artifact = only pre-existing placeholder note. Suite 941 |
| Wiring review verdict (rf-reviewer, separate lane) | ✅ APPROVE-WITH-FIXES → HIGH fix dispatched, **commit blocked on HIGH** | **HIGH** runner.py:2593 — on auto-improve continuations (THE hill-climb path) the seeded stale artifact.md wins the post-gen ws_artifact preference (longer + ends_cleanly + lints≤) because the rebuild never writes artifact.md during generation → fresh rebuild output silently replaced by pre-rebuild bloat, re-recorded into lineage; the :2585 guard assumed the generator writes artifact.md (true for phase loop, FALSE for rebuild). Code-traced, not live-observed → builder ordered: repro on a continuation E2E FIRST, then fix by construction (preferred: write final_output to artifact.md immediately after rebuild returns → override becomes no-op, downstream/seed consistency; check interplay with finalize materialization) + continuation-shaped regression test. **MEDIUM** neither safety contract tested — POST /session sets the flag (app.py:141); exception → fallback to _run_phase_loop — one test each ordered. **LOW** `_discard_evidence_file` swallows unlink OSError without dbg (silent failed delete re-opens the evidence-splice bug) — dbg ordered. Verified clean: single Session factory (no bypass/rehydration hole; /chain/run out of scope), fail-open logs + no double-record, all 9 skip-set names real + tail inputs unaffected + rebuild_generated never True for old pipeline, task_hash/lineage safe. Reviewer re-ran suites independently: 941 passed |
| User screenshot triage round 2 (v9 artifact, 3 screenshots) | ✅ triaged — NO new defect class, all map to dispatched A–F; line refs sent to builder as deterministic acceptance repros | "Format error" = C+A: lines 33/49/51 refusal-prose ("only retrieved information regarding the HTTPS protocol" / "mathematical constant Pi") + piday.org & wiki-Pi cited in-app; line 37 Microsoft Agent Framework (pypi) = THIRD subject not in task → extra defect-E repro (E must kill this source class, not just π pages). "Too simple, no value" = D: lines 39–44 literal 2-box `Pi -->|integrates with| Craft` — R2 3-diagram spec makes this shape impossible. "Format error or bad URL" = F: line 109 `- [source](nader.substack…)` link-text harvested from rendered body + wrong-referent URLs (105/107/108) in References — F kills the harvesting, E/A kill the URLs upstream. Bonus repro: lines 53–69 JS example is generic nader.substack tutorial code, not Pi/Craft → R2 code examples must source from subject-tagged claims. New regression checks: no markdown-link list items in References; no References URL absent from claims.jsonl |
| Integration diagram slice (defect D core, R2 partial) | ✅ built + live-verified (v11 run, s_d33a552f6dfa, 0.5907, same lineage) — ACCEPTED by lead | Cluster diagram generic over N subjects: LLM components tagged per subject → `subgraph` per subject; `_integration_label` grounding gate — cross-edge ships ONLY on (a) joint claim naming a mechanism (regex: API/CLI/MCP/SDK/extension/plugin/webhook/connector/interface, label from actual claim text) or (b) any joint claim (generic label) or (c) each subject independently claims a documented interface; None → NO diagram (never fabricate). Same rule gates both LLM diagram and deterministic 2-cluster fallback. Joint query upgraded: descriptors + union of both subjects' resolved anchors (cap 4), no hardcoded interface words. v11 mermaid parsed from recorded artifact: 2 subgraphs, cross-edge `Skills and MCPs (Craft) → Agent Harness (Pi) [MCP]` — mechanism word from evidence. 8 new diagram tests (incl. one-sided-architecture rejection → fallback), module 34, suite 949. KNOWN RESIDUAL (builder-flagged, lead-ruled): "Microsoft Agent Framework" component in Pi cluster = defect-E wrong-referent source still alive; NOT gated diagram-side (symptom-patching) — E fix must make it disappear downstream, survival would falsify the E diagnosis. STILL OWED in R2: Pi + Craft standalone architecture diagrams (Part 1.3(c) is THREE) + per-subject/integration code examples. Queue reset: HIGH+MEDIUM+LOW → R1 → remaining R2 |
| Per-subject architecture diagrams (R2, Part 1.3(c) completion) | ◐→✅ VERIFIED RESOLVED 2026-07-10: option-(b) fully applied (prompt now fed claims_text, half-application fixed + regression-locked `test_splice_subject_diagram_prompt_is_fed_claims_not_home_section_prose`), `_MIN_NODES=4` floor + filler-label rejection kept; live 3/3 grounded diagrams (Pi + Craft standalone + integration) on 4/4 consecutive recent runs (s_bf63191ab773/s_fda2c420bd59/s_b320f6fb3670/s_a4a244ff795d) — stochastic-count failure gone. Original snapshot below: mechanism built + unit-verified; grounding source REDESIGNED by lead ruling (2026-07-06) | Builder shipped `_pick_subject_home`/`_subject_components_prompt`/`_splice_subject_diagram` + captions via `structural_producer._diagram_explanation_sentence` (reuse, not reinvention; bare-label lint trap caught in builder's own test). Self-caught live bug: unscoped whole-report prompt → two near-duplicate diagrams when both subjects share a home section → subject-scoped prompt. Live v12/v13: diagram COUNT stochastic (2 then 1 of 3) — home-section PROSE too thin for the ≥4-component grounding floor on some runs. **LEAD RULING: option (b)** — ground subject diagrams against ALL of that subject's claims, not rendered section prose (prose is a lossy downstream artifact; prose-grounding re-imports weak-model-prose variability; claims-grounding is deterministic given research output + symmetric with the integration diagram's joint-claims grounding). Keep ≥4 floor, no-fabricate, captions; home-section pick = splice location only. Option (a) accept-stochastic REJECTED — 1.3(c) hard-requires 3/3, sub-3 after (b) = thin research (real signal). Module 38 tests, suite 953. SIDE-EVIDENCE for wiring HIGH: v12/v13 ran over the existing lineage yet fresh content survived → override may not fire on the experiment driver's mode; builder ordered to answer (i) does this mode seed artifact.md (ii) or did fresh win on length — decides which mode the HIGH regression test must target |
| FRAME relationship-resolution step (R2, user-specced flow: disambiguate → relationship) | ✅ built + live-verified (v14 run, s_1129c78a3f83, lineage intact) — ACCEPTED | `_resolve_relationship(subjects, resolved, requirement, judge)` → `(mechanism, verify_terms)`, same shape as `_disambiguate_subject`; fail-open to "composed side-by-side, mechanism unknown" (no judge / <2 subjects / any error — never stalls research); pairwise after per-subject disambiguation, skipped for single-subject. Joint queries now DERIVE from the hypothesis (`d0 d1 mechanism` + `d0 d1 verify_words`), replacing anchor-concatenation entirely (dead anchors_by_subject tracking removed). Hypothesis is a SEARCH/PROMPT directive only — `_integration_label` grounding gate unchanged, so it never becomes content by itself; artifact carries it only as a labeled assumption ("Hypothesized relationship: Pi calls Craft via an SDK-based task delegation pattern") in Scope. Live: relationship dbg line fired, joint fetch 2/2 (previously thin/generic under anchor-derived queries — confirms the generic-concatenation diagnosis), mermaid=2 (known pre-option-(b) stochastic subject-diagram variability, not new). 4 new tests (fail-open / skip / parse / joint-queries+assumptions capture), module 42, suite 957 |
| Integration section + code examples + summary conclusion (R2, "all flows consider relationships") | ✅ built + live-verified (v15 run, s_7f10de6e7e59, lineage intact) — ACCEPTED | `_ensure_integration_section` inserts "{A} and {B} Integration" before References when N≥2 (skips if a section already covers it); diagram_home now prefers it. Code mirrors the diagram pattern: `_splice_subject_code` (per subject, ONLY that subject's claims, compile-gated) + `_splice_integration_code` with all 3 rules — compile gate, `_integration_interfaces_grounded` (interface word in generated code must exist in claims, else reject), caption "Proposed usage — … Illustrative only … not quoted source code". N=1 keeps old single-code path untouched. `_write_summary(mechanism=)` states the relationship conclusion. BUILDER SELF-CAUGHT (test-first): regex `\b` treats `_` as word char → "mcp" inside `call_mcp_server()` NEVER matches → rule (b) would silently fail open on ~all real generated Python; fixed by tokenizing `[a-zA-Z]+` runs. Live v15: `## Pi and Craft Integration` in skeleton ✓, both subject code examples shipped ✓, code_home=None (old path stood down) ✓, fences=9, mermaid=2. HONEST GAP: integration code example rejected by its own gates this run (no lucky re-run chase; accept path proven by 3 unit tests) — same grounding-variability class as subject diagrams, addressed by option-(b) regrounding. 13 new tests, module 55, suite 970 |
| Wiring HIGH — repro investigation (TEAM-LEAD, read-only) | ✅ REPRO RESOLVED: mechanism real, mode-gated; fix mandate unchanged, test must target AUTO mode | Seed-write site confirmed: runner.py ~:2897 `_prior` block writes `_seed_clean` to current workspace artifact.md + section sync BEFORE generation. Why v9–v15 never regressed: (1) `research_first_probe.dbg` has ZERO seed lines — plain POST/session+SSE runs never execute the `_prior` seeding block (auto-improve/hill-climb mode only); ws_artifact empty at :2593, finalize materialization writes it AFTER the override point (v15 mtimes: artifact.md+sections at finalize time, 152s after claims.jsonl) · (2) even if seeded, v15 fresh 14,751B > v14 prior 10,977B — override needs seed LONGER; lineage grew monotonically. DANGER WINDOW = exactly R1: garbage-stripping makes fresh SHORTER → on auto mode the stale bloated seed wins and silently re-records pre-rebuild content. Fix: write final_output to artifact.md right after rebuild returns (override no-op + correct next-seed); regression test MUST drive the auto/seeding path — a plain-run test passes today without the fix and proves nothing |
| Defect A trace-first diagnosis (builder, v8 trace + claims.jsonl) — LEAD HYPOTHESIS FALSIFIED | ✅ diagnosed; fix plan approved with D corrected; implementation in flight | Real mechanisms, per bad URL: **wiki/Pi** never became a claim (offtopic hard-band dropped it twice) — artifact presence was the already-fixed undeleted-evidence-file disk scan, NOT a clamp bug · **piday.org/million NEVER FETCHED AT ALL** (zero evidence file, zero claim, whole-workspace grep) — the WRITE model FABRICATED the citation from training memory while writing refusal-prose → NEW defect class: citation fabrication, zero grounding enforcement (mirror image of the under-citation gap) · **E root cause**: `_anchor_hits` counts ONE generic single-word token ("agent" in "agent-framework") as a pass — round-4 token-loosening (needed for craft-agents-oss paraphrase) over-corrected · **A.4 real**: joint loop `loop_subject=None` disables clamp by design; nader.substack's own 4 claims tagged inconsistently (3× ['Pi'], 1× ['Pi','Craft']). APPROVED FIXES: A.1 = URL⊆claims invariant, artifact-WIDE (per-section post-filter + whole-artifact ASSEMBLE sweep incl. Exec Summary; dbg per stripped URL) · A.4 clamp joint claims to subjects whose OWN anchors hit that source · E gate = multi-word phrase hit OR ≥2 distinct single-word hits · B quote length-cap + non-prose-density reject · C remove "if insufficient, say so" prompt escape-hatch + strip refusal-shaped sentences · D CORRECTED by lead: hypothesis-mechanism may upgrade a generic cross-edge label ONLY when its terms independently hit in joint-claim text (corroborated = grounded; uncorroborated = generic label or no edge — "hypothesis never becomes content" preserved); _MIN_NODES floor approved. HIGH+MEDIUM+LOW folded into same round deliverable |
| R1 defects A–E IMPLEMENTED + live-verified (v19 run, s_01a158fd8661, hill-climb mode, lineage intact) — ACCEPTED with rulings | ✅ built; F + HIGH/MEDIUM/LOW still owed; lineage audit ordered | **A.1** `_keep_grounded_paragraphs` in _write_section (paragraph survives only if it cites ≥1 URL whose DOMAIN matches the section's claims) + prompt drops "if insufficient, say so" (= the C fix, same mechanism) + "never a URL from memory" · **A.4** joint claims clamped `tags = list(subjects)` unconditionally · **E** `_subject_name_absent` ADDITIVE gate (anchor tolerance unchanged — builder proved tightening _anchor_hits is unsafe: every stricter variant either kept pypi, broke the craft-paraphrase precedent, or rejected pi.dev itself); live dbg shows pypi/agent-framework dropped at selection · **B** `_is_code_dense` rejects markdown link/image syntax pre-density + `_MAX_QUOTE_CHARS=400` · **C** via A.1 grounding (deliberately NO phrase blocklist — pattern not wording) · **D** trivial 2-box fallback RETIRED from _splice_diagram (no diagram beats trivial; tension on record: 3/3 acceptance now rides on option-(b) claims-grounding richness). **SELF-CAUGHT REGRESSION**: A.4's all-subject tags piled every joint claim into round-robin's subjects[0] bucket → displaced per-subject claims → 5/6 sections "no content", score 0.13; fixed = multi-subject claims get their OWN bucket. Second find: weak model truncates real URL paths (same-domain) — indistinguishable from fabrication at exact-URL match → domain-level grounding (lead-ACCEPTED for prose ONLY; References stay exact per F). v19: no piday/wiki/pypi anywhere, no refusal-prose, 2×python+1×mermaid clean, 5-component 2-subgraph diagram w/ "MCPs" cross-edge, 12/12 compliance. ~12 new tests, suite 981. FLAGGED not fixed (out of scope): integration section re-echoes earlier sections (gemma habit — or seed echo, audit ordered). LEAD ORDERS: (1) URGENT read-only audit of v16–v19 rows — hill-climb mode + now-shorter fresh outputs = the un-fixed HIGH's exact firing condition; check for stale-seed re-records before acceptance (2) confirm _write_summary runs the grounding filter (SCOPE NOTE 1 artifact-wide sweep unconfirmed) (3) next deliverable = HIGH fix + auto-mode test + MEDIUM + LOW + F, nothing else first |
| Defect F: References BUILT from claims.jsonl (R1 complete: A–F all fixed + live-verified) | ✅ built + live-verified byte-exact (v20 run, s_77515c3b73ea, 0.4724 mid-climb, lineage intact) — ACCEPTED | `_rebuild_references_from_claims(text, claims)` replaces the References body with deduped claims[].url in first-appearance order (reuses artifact_text._REFERENCES_HEADING_RE — no regex re-derivation); fail-open on no-heading/no-claims; call-site swap in research_first ASSEMBLE only, old pipeline's rebuild_references_section untouched. v20 References = exactly 6 entries, byte-for-byte the claims URL set both directions — **github.com/craft-ai-agents/craft-agents-oss IS in References**, proving the lead's 1.3(a) mechanical-floor ruling (first Craft repo citation in ANY recorded run of this lineage). No wikimedia/wikipedia/placeholder. 3 new tests (quote-embedded wiki/SVG links excluded; both fail-open cases), module 67, suite 984. Builder correctly left the stray in-prose bullet (gemma echo habit, out of scope). REMAINING WORK LIST pinned to exactly 4: (1) lineage audit v16–v20 (un-fixed HIGH ran live on hill-climb mode) (2) HIGH fix + auto-mode test + MEDIUM 2 tests + LOW dbg (3) confirm/wire _write_summary through grounding filter (4) option-(b) regrounding — then STOP for reviewer pass + staged acceptance run |
| URL⊆claims invariant + joint anchor gate + markup-dense quote reject — AND latent dedupe_sections bug EXPOSED | ◐→✅ VERIFIED RESOLVED 2026-07-10: ruling 1 done (`_domain` domain-level grounding, `_drop_ungrounded_sentences`+artifact-wide), ruling 2 done (dedupe_sections NOT run in research_first ASSEMBLE — "by CONSTRUCTION" comment :2924), ruling (c) source-sanitize live (self-heading strip at WRITE :2828/:1731 + `_find_duplicate_headings` tripwire :2464), markup-dense reject live (:96); owed HIGH fixed (rows 545/546, suite 992) + lineage audit accepted (row 548). Live 3/3 recent runs: fences balanced, zero dup headings, zero bare-lang-lines. RESIDUAL (honest): buggy `dedupe_sections` still EXISTS in artifact_text.py:662, reachable only from old-pipeline `normalize_artifact` — quarantined not deleted (ruling 2: fix only if old pipeline outlives it). Original snapshot below: built; 2 lead rulings issued (domain-match rework + dedupe removal); audit/HIGH STILL owed | Built: joint fetch loop now gated on UNION of all subjects' anchors (was: only generic offtopic floor) · `_is_markup_dense_quote` (>30% link/URL chars) rejects clean-quoting non-prose (LaTeX render dumps) · `_drop_ungrounded_sentences(text, claims)` — sentence-level, FULL claims set (fixes round-robin dilution: any claim's citation survives regardless of section slice), sections + summary, dbg per drop. Live: fired 23×, ALL same-domain path truncations (pi.dev/docs/latest vs …/sdk), zero fabrications leaked → **RULING 1: prose match = DOMAIN-level (exact was over-strict; words 1370→1038, score 0.47→0.27 were self-inflicted); References stay exact (F)**. **NEW LATENT BUG, byte-for-byte proof**: `dedupe_sections` (artifact_text.py, shared) fence-masking fails → python `#` comment lines treated as heading splits → full English sentence interleaved INSIDE a ```python fence, Key Findings → "_(to be completed)_", claims-built References swallowed, diagram misplaced under References. Trigger: invariant made remaining prose repeat across sections → merge path exercised for the first time. **RULING 2 = construction over guard: REMOVE dedupe_sections from research_first ASSEMBLE** (linear pipeline writes each section once — duplicate headings impossible by construction; pass can only corrupt) + dbg tripwire if duplicate headings ever appear; artifact_text.py NOT touched — fence-masking defect FILED here as old-pipeline latent bug (evidence: builder's pre/post dump, 2026-07-06), fix only if old pipeline outlives it. Suite 989. Owed list unchanged: lineage audit · HIGH+MEDIUM+LOW · summary-filter confirmation post-rework · option-(b) |
| Wiring HIGH — CONFIRMED via controlled RED test; complete 2-piece fix ruled | ✅ repro proven mechanically; fix approved (bypass + materialize overwrite), implementation in flight | Builder's rigor: rejected its own 2-identical-runs "proof" (pipeline is deterministic — greedy decode, same search → coincidence not ruled out) AND its live marker-injection attempts (seed-sync/reconciliation legitimately mutated the synthetic marker — too confounded). Definitive: `tests/test_hc_continuation_ws_artifact.py` — monkeypatched rebuild returns 97-char fresh; seeded 1600-char clean stale prior; auto_improve on → **recorded 1600 chars, stale verbatim** (RED against current code). v12/v13 explained: no auto_improve → no seed → :2593 compare is a no-op; their artifact.md is byte-identical to result_text = materialize-at-finalize output, not a seed. SCOPE: bug fires ONLY on `hill_climb_config.auto_improve=True` sessions. FIX RULED (2 pieces, lead's write-after-return WITHDRAWN — builder proved it makes materialize skip → next seed is pre-refinement): (1) bypass `and not self._used_research_first` at runner.py:2593 (research_first never writes its own artifact.md → any file present is by definition older) (2) `_pass_materialize_artifact` OVERWRITES when `state.rebuild_generated` — bypass-alone leaves the stale seed on disk (materialize skips on exists) and the NEXT continuation seeds from prior session's artifact.md preferentially → seed(n+1)=stale(n−1) re-entry; overwrite closes the chain. Regression test gains 2nd assertion: post-run workspace artifact.md == fresh (the assertion bypass-only would pass without) |
| Wiring HIGH — FIXED (write-early), regression test GREEN bidirectionally | ✅ done + accepted as-built; suite 992 | Implementation: `_run_research_first_generation` writes final_output to workspace artifact.md immediately after `generate_research_first` returns (fail-open dbg on write error). LEAD RULING SUPERSEDED: builder built write-early (the original preference) instead of bypass+overwrite — accepted because it closes BOTH holes in one write (:2593 compare no-ops on ws==fresh; stale seed replaced on disk → next continuation seeds fresh) AND the builder's earlier pre-refinement objection is moot for rebuild runs (content-mutating passes skipped). Interplay VERIFIED empirically, not argued: controlled full-Runner.run() script + live run s_c25cd5afb2a6 — recorded result_text == workspace artifact.md byte-exact (97/97, 16580/16580); materialize's `not exists()` guard correctly no-ops. Regression test validated BOTH directions (fix disabled → RED with the exact 1600-stale symptom; restored → GREEN); builder self-caught that patching the wrapper method instead of the underlying function tested AROUND the fix. Residual FOR REVIEWER: theoretical _strip_preamble divergence (DB post-strip vs disk pre-strip) if rebuild output ever carries a preamble — assembler shouldn't produce one. Remaining before reviewer handoff: MEDIUM 2 tests + LOW dbg (in flight) → summary-filter confirm → option-(b) |
| D-correction + A.1 artifact-wide + MEDIUM/LOW round — plus NEW fence-eating bug found+fixed | ✅ accepted (live 0.528, highest this session, 2209 words); ruling 1 (domain-level) STILL unapplied per its own live data; ruling 2 status unconfirmed | **D corrected per lead ruling**: `_integration_label(mechanism, verify_terms)` — hypothesis upgrades a generic label ONLY when corroborated in joint-claim text; builder SELF-CAUGHT that unfiltered matching would rubber-stamp ANY hypothesis (mechanism sentence + joint claims both always name the subjects) → `exclude` subject-name tokens from corroboration. 4 tests. **A.1 artifact-wide**: `_drop_ungrounded_sentences_artifact_wide` at ASSEMBLE covers whole doc incl. Exec Summary (minus References — F already exact); summary-filter item CLOSED. **NEW BUG live-found**: naive sentence-splitter treated a weak-model's unprompted embedded ```python block as "one sentence" with one placeholder URL → discarded the ENTIRE block; fixed ONCE at shared `_drop_ungrounded_sentences` (fence-aware; all 3 call paths inherit) + regression test; new §1 lesson: any text pass on raw model output must be fence-aware regardless of when fences "should" appear. **MEDIUM/LOW confirmed intact.** Live: no wrong-referent leaks, 18 drops all path-truncation, zero code-shaped drops. Suite 1002. OUTSTANDING: ruling 1 unapplied (18 truncation drops = exact match still active — ~18 grounded sentences of real substance discarded per run); ruling 2 (dedupe_sections removal + tripwire) unconfirmed; option-(b) not started |
| Lineage audit 492bae60177b (34 rows, ids 1532–1567) + row-1558 retirement + FINAL domain-vs-exact ruling | ✅ audit accepted; row-1558 DELETE approved (builder executes, reports rowcount); domain-level ruling issued FINAL | Findings: v8–v15 pollution = pre-fix history, expected · **v25 (id 1558) = builder's own SQL-injected repro marker in the production DB** (self-disclosed; before it switched to an isolated unit-test DB) — DELETE approved with exact WHERE id=1558 · v26–v32: 7 byte-identical rows, verdict honestly AMBIGUOUS (post-stabilization determinism vs pre-fix override are observationally identical from content; controlled unit test remains the proof of mechanism) — no action, quarantined by recency · v33–v34 post-fix: genuinely distinct content (16454/17052) = fix working forward · seeding risk: latest_with_content picks newest (v34) → polluted rows can't seed the acceptance run · echo pattern PROVEN gemma habit not seed contamination (zero-seed v33/v34 exhibit it). Exec-Summary filter CONFIRMED (wrapped + artifact-wide double coverage). **FINAL RULING (post-crossing, on merits not timeline): prose = DOMAIN-level matching** — all observed drops in 2 live runs were grounded same-domain truncations (18–23 sentences/run of real substance); cross-domain fabrication dies identically; same-domain path fabrication = accepted lower-harm residual; References EXACT unchanged. Ruling 2 (dedupe removal) status STILL unconfirmed — re-queried |
| Option-(b) claims-grounding for subject diagrams — DONE; ⚠ 3/3-diagram risk remains live | ✅ built + tested (suite 1003); builder STOPPED per instruction; 3 queued decisions pending its next wake | `_splice_subject_diagram(claims=...)` grounds against ALL of the subject's own claims; home-section pick = splice location ONLY; kept _MIN_NODES floor, no-fabricate, caption, subject-scoped prompt. New cross-subject isolation test (Craft-only component must not ground a Pi diagram — inexpressible under shared section-prose grounding). **⚠ ACCEPTANCE RISK → ROOT-CAUSED BY LEAD (10-min verify)**: v35 shipped only the integration diagram, but "claims not rich enough" is FALSIFIED — s_38a683836ce7 claims: Pi=7 (pi-agent-core, Agent Loop, AgentContext, AgentHarness, serial/concurrent execution = 5+ components), Craft=12 (Chromium browser, MCP servers, skills import, connectors, Ollama/LM Studio). Actual cause: **option-(b) HALF-applied** — research_first.py:1193 `_subject_components_prompt(subject, text)` still feeds the LLM the home section's PROSE; only the grounding check reads claims. The model can't propose components it never sees → ≤3 groundable → floor fails → no diagram. Fix dispatched: feed subj_claims text to the prompt (check stays as belt-and-suspenders); same audit ordered for _splice_subject_code; new test = component named ONLY in claims must ground; one live re-verify expecting 3/3. 3/3 diagrams is a HARD acceptance criterion (Part 1.3(c)). Builder standing by; still queued on its side: domain-level rework (FINAL ruling), dedupe_sections removal confirmation, row-1558 delete |
| Rulings 1+2 applied — domain-level CONFIRMS prediction (0.5984 session high); tripwire fires on day one, exposes hidden dedupe path; ruling (c) issued | ✅ ruling 1 done+live-proven; ruling 2 done with caveat → source-sanitization ruled | **Ruling 1**: `_drop_ungrounded_sentences` → `_domain(url)` vs claims' domain set; live v36/v37 score 0.5984 (session high), 19809 chars / 2553 words (recovered exactly as predicted), zero cross-domain fabrications. **Ruling 2 + tripwire find**: direct dedupe_sections call removed; `_find_duplicate_headings` tripwire FIRED — builder traced fully instead of dismissing: (1) lead's "impossible by construction" premise FALSIFIED — WRITE model stochastically prepends its own section heading (4 sections) + the integration-echo habit duplicates other sections' headings; (2) saved artifact was clean anyway because **dedupe_sections still runs via `_pass_post_gate_finalize` → `normalize_artifact`** — a deliberately-unskipped pass (readability-refine + result.md archiving) → the proven fence-corruption path is STILL ARMED against rebuild output that now routinely ships # -commented python fences. **LEAD RULING (c)**: kill duplicates at SOURCE in research_first WRITE — strip leading self-heading from section/summary responses; other in-body heading lines = prompt violations → drop if matching a skeleton section name (echo), demote to plain text otherwise; fence-aware. Makes the construction guarantee TRUE → post_gate_finalize's merge path stays cold → keep it UNSKIPPED. Tripwire stays; silence = guarantee holds, future firing = new defect class. Suite 1005. Remaining: this sanitization + claims-fed _subject_components_prompt fix + row-1558 delete confirm → STOP for reviewer |
| ⚠ USER SCREENSHOTS round 3 (4 imgs) — fence-PARITY corruption live in v36/v37; lead-verified in recorded artifact | 🔴 dispatched, diagnose-first; blocks acceptance | Recorded result.md (s_5afa87bea5fb/s_ed3122139651): 14 fence marks = COUNT-balanced (count-based eraser-A guard passed) but **2 bare language lines ("typescript"/"python" as prose)** — one detached opener flips parity for the REST of the document: ## Implications/## Limitations/## References render INSIDE boxes; code renders OUTSIDE unfenced → `def **init**(self)` (markdown ate __init__); caption divorced from code; `_(to be completed)_` placeholder visible; ## Pi and Craft Integration after References inside box. ONE root cause, all 4 screenshots. Prime suspect: post_gate_finalize (readability-refine LLM and/or normalize_artifact) — its accept gate checks fence COUNT only, structurally blind to tag detachment. Also: DB result_text 19809 vs result.md 20027 (218-char delta) may localize the mutating stage. Fix ordered by construction: (1) rewrite-pass accept gates upgraded COUNT→STRUCTURE (language-tagged openers, strict alternation, zero bare-lang lines, zero headings inside fences; reject-don't-repair) (2) deterministic final-artifact checks: no bare lang lines / no ## inside fences / tagged openers / no placeholder text. Pre-reviewer list now: this + ruling-(c) sanitization + claims-fed _subject_components_prompt + row-1558 delete |
| Resume pass: pre-reviewer list closed + fence structure accept gate | ✅ code-level checks green; full suite green | Verified row 1558 is already deleted (`SELECT ... WHERE id=1558` returned no rows). Verified existing resumed fixes on disk: ruling-(c) source heading sanitization, claims-fed `_subject_components_prompt`, rebuild-aware `_pick_scored_source`, materialize overwrite/write-early defenses. Added `_artifact_structure_ok` in `finalize.py` and gated `_pass_post_gate_finalize` so rewrite candidates with detached language tag lines, unclosed/malformed fences, `##` headings inside fences, or known placeholders are rejected (keeps prior clean text; reject-don't-repair). Plain untagged markdown fences remain valid and are regression-tested. Tightened two sanitizer tests to exact-line assertions (`"## X"` is a substring of `"### X"`). Evidence: targeted `test_finalize.py` → 11 passed; `test_research_first.py` → 91 passed; runner/routing/session/HIGH slice → 163 passed; structural producer → 10 passed. Comparable full suite (`cd backend && .venv/bin/python -m pytest tests/ -q`) → 1017 passed, 4 deselected. Validation drift vs handoff's 1007 green: +10 passing tests, no comparable failures; root-level system-Python run failed only because `mdformat` is missing outside the backend venv. |
| Full-slice internal review gate | ✅ non-blocking COMMENT (code-reviewer APPROVE + architect WATCH) | Required `$code-review` skill lanes ran via native subagents after validation. Fixes from review: opted-in `research_first` failures are fail-visible (no silent `_run_phase_loop` fallback; routing regression asserts `ErrorEvent`, empty terminal `done`, and no legacy success row); `_integration_label` now excludes tokenized subject-name tokens before corroborating mechanism labels (multi-word subject regression); nullable finalize streak annotation and integration-home test typing cleaned; touched production comments genericized (`Pi|Craft` grep in `research_first.py`/`runner.py` = 0). Evidence: changed-code slice → 109 passed; comparable full suite → 1018 passed, 4 deselected (+1 expected regression-test drift); `git diff --check c85bbc6` clean. Final code-reviewer: APPROVE, 0 issues. Final architect: WATCH, not BLOCK — `post_gate_finalize` remains a secondary artifact writeback owner for rebuild-generated runs; follow-up debt, not an Attempt 11 blocker. |
| R3 SPEC (USER, 2026-07-06): What/Why/How/Where report framework — relationship must CLASSIFY, not presume | ✅ DELIVERED via MVP-8 (see row below): `_classify_relationship` returns {cooperates/competes/alternative/independent/unknown}; competes/alternative branch → `_splice_comparison_table` instead of the integration diagram | User framework: **What** = what are these objects (FRAME disambiguation ✅ covers) · **Why** = why do they appear together — relate how: can they work together or do they COMPETE? (GAP: `_resolve_relationship` presumes cooperation — "which surface of A could drive/host/call B" — no compete branch) · **How** = if compete: how + pros/cons; if cooperate: mechanism + pros/cons (GAP: only the cooperate flow exists — integration section/diagram/code; compete branch needs comparison axes + pros/cons table instead) · **Where** = boundaries: scope, limitation, budget, effort (Scope+Limitations ✅; effort/budget angle thin). Design: relationship step returns a CLASSIFICATION {cooperates / competes / independent / unknown} grounded in joint evidence, fail-open to unknown; ALL downstream relationship flows (section, diagram, code/comparison, summary conclusion) branch on it. Never force-fit an integration story onto competing subjects |
| Attempt 11 (final acceptance run, after R1+R2) | 🔴 FAILED + one allowed rerun used | Original attempt 11 (`s_fa2816b1e000`, score 0.7644) improved substance but missed acceptance: only 2 Mermaid diagrams, raw metadata leaked, malformed integration/code/prose. Focused hardening shipped as `85c5999` after validation (`99` focused tests; `116` regression slice; full suite `1025 passed, 4 deselected`) and internal `$code-review` skill gate (code-reviewer APPROVE, architect WATCH → final COMMENT). Single allowed rerun `s_86350e8b9b76` completed without runtime error but failed acceptance harder: score 72.58, `task_success=false`, 1148 words, 1 Mermaid, no craft-agents-oss citation, no integration section, no relationship summary, generic agentic-AI sources only. Metadata leak fix held (`ARTICLE_TITLE`/`PATCH_TARGET` absent; `SEARCH` only inside `RESEARCH_FINDING`). Stop condition: no further rerun until research/source-selection regression is re-diagnosed. **✅ RESOLVED 2026-07-09 — stop condition lifted: the regression was re-diagnosed (query-directed extraction + moving-window fetch) and acceptance reached (0.92–1.0); see Part 1.4-GS2 result + GS4.** |
| Blocker-removal MVP sequence (2026-07-08, user-approved full-auto) — MVP-0..4 CLOSED offline | ✅ recovery slice committed; B1–B4 fixes verified by replay; stop condition LIFTED (RCA proof-backed + fixes replay-verified) | MVP-0: suite 1054 passed/4 deselected → recovery slice landed as 5 logical commits (marker publish gate / glue-label drop / RESEARCH_FINDING heading strip + component-label gate / offline MVP harness / PLAN docs). MVP-1 (B1): committed whole-task guard had a HOLE — only-covers-branch case fell back to `[title]` = whole-task-shaped subject (verified: title == 'Study how to use Pi and Craft…'); fixed by `_subjects_from_requirement` LLM fallback with deterministic validation (verbatim-substring + ≤4 words + ≠whole-task + compound-split AFTER whole-task check), TDD 4 tests, module 108 passed. MVP-2 (B2): replay v40/v41 result.md → `_strip_patch_metadata_lines` kills `### RESEARCH_FINDING` at source (0 left) AND `evaluate_publish_readiness` rejects both (ready=False, marker issue named). MVP-3 (B3): replay observed filler labels (The/Repository/Contains/Over/Various/Including) → all dropped, real labels (pi-agent-core/MCP servers/Agent Harness/API) all kept; NEW residual found+fixed: corroborated hypothesis SENTENCE shipped verbatim as v40 cross-edge label → `_integration_label` caps at ≤6 words, reduces to interface word / corroborated verify term (touched-module slice 163 passed). MVP-4 (B4): offline probe on saved v38 claims (Pi=7/Craft=12/joint=2) with real gemma client → 3/3 diagrams BOTH repeats, zero sentence labels (`scratchpad/diag_probe_mvp4.py`). Acceptance checker `scratchpad/accept_check.py` validated against v38 (fails on exactly its 5 documented defects). Next: MVP-5 live probe (s_f46ecd2d066b in flight) → review lanes → acceptance. |

| MVP-5b/5c + acceptance run v44 (2026-07-08) — formal a–e CLEARED, 2 real residuals found by verification | ✅ a–e pass live; ⚠ ordering defect + domain-hardcoding surfaced | Two deeper defects fixed past B1–B4: (5b) diagram glue-word labels — prompt-hardening (generic proper-component naming rule in all 3 diagram prompts, no blocklist) took Pi diagram from `Commits/Branch/Designed` → real `pi-agent-core/Agent Loop/AgentContext/AgentHarness` (v43 0.6688); (5c) integration code shipped 0/4 → **4/4** after root-cause: prompt was seeded with the ungrounded FRAME hypothesis (`SDK`), so gemma wrote `import pi_sdk` and the grounding gate correctly rejected every sample — fixed by steering the code prompt to CLAIM-documented interfaces (`_grounded_interface_words`), single-sourced `_INTERFACE_WORD_ALT`. Separate-lane code-review: **APPROVE-WITH-FIXES** (0 crit/high; dbg-log gap fixed, stopword-dedup deferred into MVP-8). **Acceptance run v44 (`s_65f50e596ddb`, id 1577, 0.5984): formal Part 1.3 a–e ALL PASS** — craft-agents-oss cited, 2637 words, 3 diagrams w/ grounded cross-edge, **integration code + Proposed-usage caption present**, integration section present (FIRST run in lineage to clear the full a–e bar). VERIFICATION (per "verify don't trust") found: (i) References ⊆ claims holds byte-exact (my accept_check references-subset FAIL was a checker false-positive — grabbed in-prose URLs after the heading); (ii) **REAL DEFECT: two sections render AFTER `## References`** (a model-emitted "Concrete Implementation Risks…" section + the "Pi and Craft Integration" section) — References must be last; e exists but is misplaced. Not acceptance-clean despite a–e greps passing. |
| MVP-8 SPEC (USER 2026-07-08): remove domain-hardcoding — LLM-determined relationship, not presumed "integration" | ⬜ NEXT (user-directed after v44) | User: `_INTERFACE_WORD_ALT` (api/cli/mcp/sdk/…) is DOMAIN hardcoding — not all research reports integrate via a software interface; and "integration" itself is a presumed relationship. Directive: use the LLM to classify the per-task relationship + mechanism, grounded in claims. Design (this is R3 pulled forward): extend `_resolve_relationship` → `{kind: cooperates/competes/extends/alternative/independent/unknown, descriptor, mechanism_terms}`; section name + cross-edge label + integration-code vocabulary all derive from that per-task output + claim grounding, NOT fixed enums; compete/alternative branch → comparison (axes+pros/cons) instead of integration diagram/code; independent → no forced relationship artifact. Also fixes the v44 ordering defect (References must stay last) and subsumes the `_FEATURE_STOPWORDS`/`_GENERIC_LABELS` blocklist duplication. Needs spec→plan→TDD→fresh verify. |

| MVP-8 R3 redesign LANDED + live-verified; seed-merge runner defect PINNED (2026-07-08) | ✅ hardcoding removed + classification honors task intent; 🔴 seed-carry-forward merge blocks clean acceptance | **Directive DONE:** interface enum (`api|cli|mcp|sdk|…`) removed from production (`grep _INTERFACE_WORD studio/*.py` = 0); relationship kind/section-name/edge-label/code-vocabulary all LLM-classified per-task (`_classify_relationship` → `Relationship{kind,descriptor,mechanism,mechanism_terms}`, fail-open unknown) + claim-grounded (`_grounded_mechanism_terms`); competes/alternative → comparison table branch, independent → no relationship artifact; suite 1075 passed/4 deselected; executor slice 1b + separate-lane review APPROVE-WITH-FIXES (dbg-log fix applied), dead `_DIAGRAM_HOME_RE` removed. **Classification honors task intent (user ruling):** prompt weighs task FRAMING over subject similarity — "use X and Y together" → cooperates/extends; offline 3/3 `extends`, live v47 emitted an "Integrated Agentic Workflow" (extends) section. **Ordering fix (References-last) baked into `normalize_artifact` + `artifact_text.references_last`, proven offline on v45/v47 — but BYPASSED live:** `_pass_post_gate_finalize` (holds normalize + result.md archive) is SKIPPED for `rebuild_generated`; rebuild writes result.md from runner `final_output`. **PINNED ROOT CAUSE:** v47 artifact.md carries BOTH v46's seed "Architectural Comparison" AND fresh "Integrated Agentic Workflow" — research_first inserts exactly ONE relationship section and returns references-last, so the RUNNER is merging the carried-forward seed into the rebuild output (seed-carry-forward class, wiring-HIGH area runner ~2593/2897/4418). Effect: section accumulation across lineage + References not last + score noise (v47 0.528). **STALE-SERVER TRAP fixed:** v45/v46 (0.8536, byte-identical) were served by a uvicorn started 20:09 — 53 min before the 21:02 fixes; `pkill` silently failed, port stayed bound, old code served → invalid "passes". Added a start-time-vs-source-mtime preflight to `scratchpad/probe_run_sse.py`. **NEXT (not started — substantial, risky, own slice):** stop the runner merging the seed into rebuild result.md (rebuild result.md must == generate_research_first return); then the ordering + single-relationship-section both hold. Do NOT patch at session end. |
| MVP-9 seed-merge FIXED + live-verified (2026-07-08) | ✅ done | Root cause isolated empirically: `generate_research_first`'s RAW return is CLEAN (offline: exactly one relationship section "Pi and Craft: Integrated Agentic Workflow", References LAST, 3 diagrams) — 100% of the v45–v47 pollution (stale "Architectural Comparison" section accumulating + References-not-last) came from the runner's hill-climb SEED CARRY-FORWARD, which writes the prior lineage artifact into artifact.md AND splits it into per-section files that a downstream merge pulls back in. research_first is cold-start by design (D4 / `_run_research_first_generation` docstring). **Fix:** `_seed_carry_forward` now skips entirely when `_use_research_first(session)` (one guard on the `auto_improve` gate) — one commit, +regression test `test_research_first_seed_carry_forward_is_skipped` (asserts no seed copied) + the existing continuation test stays green. Runner/continuation/routing slice 162 passed. **Live-verified v48 (`s_0b520045f6a7`, id 1581):** `fmt. references LAST` now PASSES (empty trailing-section list — accumulation gone), no stray sections, refs⊆claims exact. **Also fixed this session:** the STALE-SERVER trap (uvicorn no --reload + failed pkill served old code, invalidating v45/v46) — added a start-time-vs-source-mtime preflight to `scratchpad/probe_run_sse.py`. **Remaining (gemma generation-quality ceiling, NOT a structural bug):** per-run variance in word count / diagram count / whether the relationship section lands as a clean `##` (v48 was a thin 1587-word run, score 0.4912; v45/v46 hit 0.8536). Full a–e now depends on generation richness, not on any hardcoding or ordering defect. |

**Residuals:** ~~em-dash syntax error~~ ✅ fixed round 2 (`_fenced_code_compiles`).
~~echoed-H1 heading level~~ → CONFIRMED LIVE by user screenshot (2026-07-05
~23:23): verbatim README quote carrying markdown structure renders as a giant
mid-document heading + the same quote embedded twice (inline paraphrase + spilled
block). UNHELD, folded into the wiring round: (1) structure-bearing quotes render
as blockquotes (`> ` per line — verbatim text preserved, structure can't leak),
(2) one embedded quote per section per source-substring (containment counts),
(3) mechanical acceptance: artifact heading count == skeleton heading count.
→ ✅ CLOSED 2026-07-06: quote-neutralize addendum built + v10 live run passed the
mechanical check (heading count 9 == skeleton exact; see status-log row above).

### 2.3 Attempt slate (HISTORICAL — old §14 hub/spoke; superseded by the research_first rebuild)

(old §14, "Attempt-9 fix slate (ordered)" list; the diagnosis that produced this
slate is in Part 4.2–4.3, and its committed measured results are logged in
Part 3.1)

1. **rebuild_references erasure** — ✅ RESOLVED (renamed: it was never rebuild_references). Per-pass fences= trace named BOTH erasers; eraser A committed `27beff6` (synth accept gate rejects fence loss), eraser B committed `d4e84a9` (duplicate-section birth fix in _synthesize_windowed + 3 keep-longest→fold-merge conversions + placeholder-aware join; 925 passed, ruff clean, 7 files +316/−29). Measured next: attempt 10 run must show fences surviving L0 write-through (finalize log fences pre==post).
2. Diagram insertion carries explanatory prose (kills the 6→7 lint veto). — ✅ COMMITTED (deterministic sentence from node labels; block format centralized in diagram_render.build_diagram_block)
3. repair_lints logs lint names when it cannot act. — ✅ COMMITTED (unconditional residual logging, single tail exit, cap 10)
4. covers-X deterministic downgrade gate (mention-vs-substance). — ✅ COMMITTED (distance-based contexts ≥100 chars apart + citation within 120 chars; fails open ≤2-char subjects)
5. Writeback-normalize fence repair (mid-run cleanliness; formatter-after-repairs
   invariant pinned by test — mdformat/Flowmark/PyMarkdown all launder broken fences,
   evidenced 2026-07-05; Flowmark and PyMarkdown evaluated and rejected).
6. P1-core: subject ledger + unmet-subject search directive into worker/tool-loop
   prompts (Craft searches need an ACTOR, not another verdict).
7. Compliance-verdict memoization per text-hash (17 wasted rounds).
8b. Diagram requirement-conditioning (user finding, attempt 9): the produced
   diagram is a generic component sketch, not the REQUIRED "integration of Pi and
   Craft" architecture — build_components_prompt never receives the requirement
   branch text (E7 birth-certificate conditioning); ALSO downstream of the Craft
   coverage gap (can't diagram integration with zero Craft content — P1 again).
8. AST swallowed-content lint via markdown-it-py (already a transitive dep) — catches
   the giant-fence-swallows-headings class generically.

## Part 3 — Execution log (LIVING)

### 3.1 Execution log

(old §13 table, verbatim — one row per committed fix with measured result;
append new rows here going forward)

| Fix (commit) | Status | Measured result |
|---|---|---|
| **S1 textutil consolidation** | committed | 3-way `_dbg` copy + 5 URL-regex variants collapsed to one leaf module; 846→ suite green; `extract_urls` deliberately widened vs the old first-excluded-char truncation (`?q=(pi)&page=2` no longer loses its tail) — pinned by regression test |
| **Extraction fixes: AND/OR contrast + decontaminated example + SUBJECT COVERAGE + do-not-invent reorder** | committed | Live re-extraction on the real task, run twice, stable: 12 groups; "include example code" / "include design architecture" now SEPARATE mandatory groups (was one OR group — root cause of the diagram being invisible to compliance since day one); "covers Pi" / "covers Craft" emitted as separate rows; reorder eliminated the borderline "covers agent development" over-extraction (13→12) |
| **L0 structural producer + skip-reason observability** | committed | Run 1534: first structural content EVER to survive to a recorded artifact (code fence, score 0.657 vs 0.304 honest baseline). Run 1537: diagram branch now ATTEMPTED (proves the OR-split works end-to-end); insertion vetoed by `_accept` (lint-worse), veto now logged with the exact criterion. Code fence in 1537 was born at GENERATION (phase-1 requirement notice) — prevention, L0 correctly no-oped |
| **S2 finalize pass-list (=L2)** | committed | runner.py −869 lines; 17 passes, verbatim order, one wrapper owning fail-open + ran/changed/reason + inert-pass ledger (≥3 no-op epochs flagged). Adversarial test EMPIRICALLY disproved the "later passes raise safely" assumption — record pass silently persisted an inflated row on scoring failure → `state.mined` gate restores the old abort contract (858 passed) |
| **Observability: extraction groups + per-branch compliance verdicts + _accept criterion** | committed | Closes run-1537's three blind spots: what the live run extracted / whether covers-X was judged (or wrongly SATISFIED) / which criterion vetoed L0's diagram. Verified next live run |
| **L3 lineage immune system** | committed | Gate walks past crashed (score 0.0), citation-free, and median-outlier rows; `failed_partial` exempt (status is the better signal, pinned by existing test). Lint criterion DROPPED after it broke `test_bad_mermaid_seed_reaches_reducer_with_repair_instruction`: lint-broken rows are §14.6 self-heal INPUT, not poison (REVISES §6 L3 / §12 slice-4 spec). 8 new tests. **Incident (CORRECTED)**: the `_SEED_HARD_LINT_MARKERS` allowlist initially reported as a suspicious injected edit was actually a legitimate build by a second agent executing a double-assigned copy of the L3 slice whose spec (mine) said "zero HARD lints; soft lints OK" — an orchestration collision, not an attack. The reverted split stays reverted on the merits (lint criterion dropped entirely); process fix: a reassigned slice requires a confirmed stand-down before the new assignee starts |
| **Junk-source pipeline (density floor + gray-zone judge + patch rules + memoization)** | committed (earlier today) | Every run since: π-Wikipedia dropped in hard band (0.0021), dictionary pages dropped via judge, `judge=CACHED` on repeats, genuine pi.dev kept; 5/5 surviving URLs genuine in runs 1534/1537 |
| **Infra: oMLX eviction cascade** | mitigated, app-fix queued | Root cause: BGE embedder loading mid-run alongside gemma-26B trips oMLX's own memory enforcer → evicts gemma mid-request → server aborts (2 identical crashes at s2). Mitigation: pre-warm BGE before the run — attempt 7 ran to completion. App defect exposed: crashed runs record score 0.0 rows (poisons seeding; manual row surgery needed twice) → L1 crashed/unscored status rule (§12), L3 score>0 criterion interim |
| **L3 review fixes** | committed | Reviewer MEDIUM: the no-content skip in latest_with_content was still silent → now logs version+reason (no silent path); LOW: median test comment corrected (self-inclusive, not leave-one-out). 163 targeted tests green |
| **Observability triple (extraction groups / per-branch compliance verdicts / L0 _accept criterion)** | committed | Next live run shows exactly what was extracted, every covers-X verdict, and which criterion vetoes an L0 insertion — closes run-1537's three blind spots |
| **Format repairs: fence-line contamination + doubled citation** | committed | New lint #13 (fence line carries trailing content — none of the 12 prior lints caught it) + deterministic repairs sharing ONE predicate (textutil.fence_rest_contaminated) so lint and repair cannot disagree; doubled `[title](url) url` collapsed on exact norm_url match, punctuation preserved. Verified against run-1537's real artifact: line-93 lint fires and repairs clean, line-105 duplicate collapses. Suite 874. Side effect: the fence lint was the pre-existing lints_before=1 that made _accept veto L0's diagram — repair runs BEFORE structural_producer in the pass order, so diagram insertion is unblocked next run |
| **Fence-survival instrumentation (per-pass fences= + L0 write-through before/after)** | committed | Run v5's contradiction (L0 changed=True insertion, all later passes changed=False, zero fences recorded) was unresolvable statically — offline reproduction of the section write-through preserves the fence on both fresh and copied-live workspaces. Next run names the eraser pass mechanically |
| **Eraser A: synth accept gate rejects fence loss (`27beff6`)** | committed | Attempt-9 trace showed synthesize_readability accepting a rewrite that dropped fences 2→0 (guard checked URLs/overlap/ratio, was fence-blind). `_synthesize_block` now compares fence-pair counts (`src.count("```")//2`) and REJECTs on "fence lost". Deterministic — no run needed to prove the guard fires; attempt 10 proves survival end-to-end |
| **Eraser B: duplicate-section birth fix + content-folding merges (`d4e84a9`)** | committed | Root cause of the five duplicate-## disease: `_synthesize_windowed` prepended the heading onto a heading-INCLUSIVE `split_sections` body (`whole = f"{heading}\n{body}"` → `whole = body`; ~10-line echo-client repro). Three keep-longest-discard-rest merges (dedupe_sections, merge_duplicate_sections, split_artifact_to_sections) converted to fold-merge so duplicates no longer EAT content; `_dedup_paragraphs` drops byte-identical echoes regardless of URL (reviewer HIGH — fold-merge would have multiplied citations); pre-existing test that encoded the citation-doubling as expected behavior split honestly. 925 passed, ruff clean, 7 files +316/−29 |
| **Rebuild: `research_first.py` committed (`c85bbc6`)** | committed, NOT wired | Linear FRAME→RESEARCH→CLAIMS→WRITE→ASSEMBLE generator, +1001 lines, 22 tests, suite 931. Probe v8: both subjects covered by construction (craft-agents-oss + earendil-works/pi cited, zero definition sources — the old pipeline has NEVER produced a Craft citation in 7 runs). Reviewer APPROVE after HIGH fix (round-robin claim capping; first-N starved subject-2 at WRITE). Wire-in decision pending (USER) |
| **Attempt 10 = A+B eraser verification run (run 1540, v7 0.5923)** | ✅ MEASURED — erasers dead | First run EVER where ALL structural content survived to the recorded artifact: `typescript` code block + mermaid diagram + 4 fence markers, finalize trace `fences=4` constant through the changed=True write-through and every later pass (v5 same trace was 4→2→0). Score 0.5923 = 2nd-highest honest. Remaining defects confirmed as COVERAGE not erasure: 0 web searches (rode 9-file inherited evidence), 1 "craft" mention, 0 Craft URLs, diagram = Pi-only architecture (4 subgraphs) not the required Pi+Craft integration. Exactly the predicted split: erasers fixed ≠ coverage fixed — P1-core actor (backlog item 2) and 8b remain the binding gaps on the old pipeline; rebuild covers both by construction |
| **Research-first acceptance hardening (`85c5999`)** | committed + measured, acceptance still failed | Post-attempt-11 focused fix: strips spontaneous section-writer fenced blocks and raw patch/search metadata echoes; deterministic fallback diagrams now use subject-scoped feature extraction, reject ambiguous duplicate-label LLM cross-edges, and ignore unknown component subjects before fallback. Validation before commit: `test_research_first.py` 99 passed; py_compile clean; `git diff --check` clean; regression slice 116 passed; full backend suite 1025 passed, 4 deselected (expected +4 test drift). Internal `$code-review` skill gate after tests: code-reviewer APPROVE, architect WATCH → final COMMENT. Single allowed acceptance rerun `s_86350e8b9b76` failed: score 72.58, `task_success=false`, 1148 words, 1 Mermaid, no craft-agents-oss, no integration section; metadata leak fix held. Next work is rediagnosis of research/source-selection regression, not another blind rerun. |
| **Subject extraction generic guard (`875f033`)** | committed, not rerun live | Root cause of rerun `s_86350e8b9b76` failure traced to requirement extraction collapsing subject coverage into one whole-task `covers ...` branch (`claims.jsonl` subject = full task sentence), while a healthy prior probe emitted separate subjects. Fix is generic: tighten `_extract_prompt()` to forbid whole-task `covers ...` subject coverage and emit no coverage line when no specific named thing is identifiable; removed task-specific production comments/examples from touched production files. Validation after final diff: py_compile clean; focused tests `138 passed, 2 deselected`; broader slice `155 passed, 2 deselected`; full backend suite `1025 passed, 4 deselected` (no count drift from latest comparable); `git diff --check` clean; exact production grep for prior task names/examples returned 0 matches. Internal code-review skill after tests: code-reviewer APPROVE, architect WATCH → final COMMENT; WATCH notes future hardening could deterministically drop malformed whole-task `covers ...` lines. No live acceptance rerun yet; next live run remains blocked on deliberate source-selection regression check, not blind rerun. |
| **Whole-task subject branch drop (`ae63c25`)** | committed, not rerun live | Deliberate source-selection regression check completed: `research_first` consumed extractor output at the FRAME boundary without task context, so a malformed whole-task `covers ...` branch could still become the sole research subject and drive generic source selection. Fix is generic and deterministic: `_extract_subjects(..., requirement=...)` normalizes the clean base task and drops only exact whole-task coverage branches, preserving valid separate subjects and fallback-to-title behavior when no specific subject exists. Validation: TDD red test failed first with `TypeError`; targeted regression passed; nearby FRAME tests passed; focused `test_research_first.py test_requirement_compliance.py` → `139 passed`; py_compile clean via backend venv; full backend suite → `1026 passed, 4 deselected` (+1 expected test drift vs previous 1025); `git diff --check` clean; production grep for prior task names/examples returned 0 matches. Internal code-review skill after tests: code-reviewer found no actionable correctness regressions; architect CLEAR. Next step is the controlled acceptance rerun against Part 1.3. |
| **Research/source-selection recovery addendum** | planned — blocks next acceptance rerun | See `PLAN-research-first-recovery-addendum.md`. Code-review blockers remain for fail-visible per-subject source/claim coverage, N-subject relationship evidence, corroborated summary mechanisms, evidence-bearing diagram labels, and publish-blocking internal markers. External-study pattern supports source coverage gates before synthesis; recovery must stay generic and avoid task/topic hardcoding. |
| **Research-quality MVP harness** | implemented, not production-wired | Added offline evaluator `backend/studio/research_quality_mvp.py` plus tests. It reuses existing `artifact.md` / `result.md` and `claims.jsonl` workspace artifacts to score generic subject evidence, relationship evidence, diagram-label quality, and internal-marker gates before production promotion. Calibration: `s_86350e8b9b76` fails evidence/relationship/marker gates; `s_798eeda98bd1` passes evidence/relationship but fails diagram-label/marker gates. Extended 2026-07-07 with `evaluate_gate_health()` canaries so the MVP distinguishes "artifact failed a healthy gate" from "gate is faulty": canaries detect both too-loose gates (known-bad artifacts unexpectedly pass) and too-strict gates (known-good artifact unexpectedly fails). Second extension after research-app OSS study (STORM, PaperQA2, GPT Researcher, Open Deep Research, OpenScience; Shepherd as retained-output supervision pattern only): added MVP checks for 3+ subject all-subject relationship evidence, summary mechanism grounding against URL-backed relationship claims, and Mermaid node/edge-label grounding against URL-backed evidence. Internal review found two false-pass risks; fixed by requiring all non-generic summary mechanism terms to be grounded and by excluding uncited claims from diagram label evidence. Follow-up review found one 3-subject false-fail edge; fixed by grounding summaries against all URL-backed multi-subject claims while keeping the separate all-subject coverage gate. Final internal review found a compound-label false-pass (`API Adapter` against cited `API Bridge`); fixed by requiring every non-filler diagram-label token to appear in URL-backed evidence and by updating the renderer acronym contract. Chrome visual inspection of `s_f8717d412f10/result.md` confirmed visible outline/layout defects (`RESEARCH_FINDING`, pending section content, weak diagram labels), and the MVP now flags diagram-label quality, summary mechanism grounding, and marker leaks while gate health passes. Validation: TDD red import failure first for gate health; second TDD red showed five expected MVP misses; review-fix TDD red showed 2 expected false-passes; follow-up TDD red showed 1 expected false-fail; final compound-label regression added; focused `test_research_quality_mvp.py test_research_first.py test_diagram_render.py` → 136 passed; full backend suite → 1050 passed, 4 deselected; `py_compile` clean; `diagram_render` self-check OK; `git diff --check` clean. Drift: +11 MVP/tests total expected from gate-health + Attempt-11 coverage + review false-pass/false-fail coverage; no focused-suite failures. |
| **Attempt-11 root-cause proof + marker gate promotion** | implemented + post-review fix applied | Proved two concrete failure paths from saved lineage artifacts, not a hypothesis: latest workspaces `s_798eeda98bd1` / `s_f8717d412f10` contain visible `RESEARCH_FINDING` scaffolding and Mermaid labels such as `The`, `Repository`, `Contains`, `Over`, `Various`, `Including`; replaying pre-fix `HEAD` feature-label extraction over the saved `claims.jsonl` reproduces those bad labels exactly, while current generic component-token filtering removes the sentence-glue labels. Also proved a production gate gap: before this row, `evaluate_publish_readiness()` returned publish-ready for `s_798eeda98bd1` despite `RESEARCH_FINDING`; the only marker check lived in depth/evidence paths and rebuild-generated runs skip the mutating publish pass. Promoted internal marker leakage into direct publish-readiness checks; internal code-review caught an over-broad `URL:`/`SEARCH:` false positive, fixed by limiting raw metadata detection to known worker-record shapes (`SEARCH: ok/error`, URL records with actual URLs/none, and internal finding fields) while preserving reader-facing `URL:`/`Search:` headings and labels. Validation: replayed old/current artifact checks; focused marker/diagram regressions → 8 passed; targeted report/MVP/diagram/research-first slice → 158 passed; comparable backend suite from `backend/` → 1054 passed, 4 deselected. Drift: +4 expected passing tests vs prior 1050 comparable run (+2 marker/diagram proof tests, +2 precision regressions); no comparable failures. Internal review status: code-reviewer HIGH fixed, architect WATCH (private helper reuse + field-label precision watch), final COMMENT. |

### 3.2 Lineage scoreboard

(old §13 tail, verbatim)

**Lineage scoreboard** (task 492bae60177b, honest cold chain): v1 0.145 → v2 0.304 (guards+salvage) → v3 0.657 (L0 code fence) → v4 0.433 (attempt 7 — no crash, fence survived, zero junk, but ZERO web searches issued: run rode inherited evidence, so the covers-Craft loop was never exercised; score dip is one epoch of synthesis rejects on a seeded run) → v5 0.41 (attempt 8 — eraser trace armed) → v6 0.4949 (attempt 9 — first diagram ever recorded; both erasers named) → **v7 0.5923 (attempt 10, run 1540 — erasers VERIFIED dead: code block + diagram + all fences survived end-to-end for the first time; coverage gaps remain)**. **Post-rebuild (research_first, §16) tail: acceptance reached — deterministic v-runs hold 0.92–1.0 through ~v80 (extraction fix 0.57→0.92; code-fabrication guard `0bf8c69`; diagram edge `686d7b0` live v80; titled references `030ffba`).**

## Part 4 — Findings & attempt history

### 4.1 Loop-health diagnosis (RC1–RC5)

(old §5, in full; LOOP-HEALTH ANALYSIS — why self-improvement underdelivers,
2026-07-05, user + article)

**Reference frame:** the loop canon — DISCOVER→PLAN→EXECUTE→VERIFY→ITERATE, where
VERIFY is the heart, STATE is the memory, stop-conditions are the sanity, and
writer/reviewer separation is most of the quality. Measured against it, the studio
over-built EXECUTE/ITERATE and under-built VERIFY/STATE.

#### 4.1.1 Root causes (ranked, all live-evidenced)

**RC1 — Verifier quality gap (the heart is the weakest organ).**
Self-improvement = improvement_signal × iterations. The signal comes ONLY from verify,
and studio's verify (a) passed wrong work — run 1529 scored 0.97 on prose-about-code,
zero fences; (b) uses crude proxies — "Analytical depth" is a discourse-marker count;
(c) is fail-open — a compliance/scorer error silently skips the gate; (d) LLM
self-scores are so noisy the seeder had to switch to latest-not-best. A loop iterating
on a noisy/inflated signal converges on nothing — the article's "AI nodding at itself."

**RC2 — Silent-failure disease (Ralph Wiggum mode).**
83 bare `except Exception` in runner.py; the editor pass, both depth passes, and the
synthesis pass were each discovered DEAD only by human archaeology (0.00s timers, byte-
identical rows). The loop kept "iterating" without the passes that were supposed to
improve it — running ≠ progressing, and nothing in the system could tell the difference.

**RC3 — State without an immune system.**
The corrupted 0.087 run (1530) became lineage-latest and would have seeded the next
epoch; goal-attach rotates task_hash into a cold start; every recovery so far has been
manual sqlite surgery. The article's "little notebook" must be poison-resistant or the
loop LEARNS the poison.

**RC4 — Execute-contract ceilings the loop cannot cross.**
One-sentence-per-source weave, cited-URL drops, density caps, prose-only reducer
patches (all deliberate anti-regression choices) put a hard ceiling on depth. The same
two weakness rows repeated FIVE runs unchanged — the loop kept pushing against an
invariant wall with no way to notice "this weakness is not fixable by another epoch."

**RC5 — Untracked economics.**
No cost-per-accepted-change metric. The first accepted editor round EVER was run 1531;
before that, every editor round was attempted, rejected, and paid for invisibly.
Acceptance below ~50% means the loop costs more than it saves — nobody could see it.

#### 4.1.2 Expectation calibration (the four-condition test)

The article's条件 4 — "done is objective" — is only PARTIALLY true for research
reports: fences/citations/sections are objective (and now gated); prose depth is
judged by proxies. So "fully autonomous self-improvement to excellence" will
asymptote by design. The honest target: **converge to high-0.8s with zero corruption,
zero silent stalls, and every stall explained in the run report.**

### 4.2 Attempt-8 diagnosis narrative

(old §14 header + first two paragraphs; run recorded v5 0.41, 2026-07-05 late — new
top suspect at the time)

S2's per-pass ledger paid off in one run: `structural_producer_l0: changed=True`
(code inserted, bytes verified) → `rebuild_references: changed=True` → passes 8–17
all changed=False — yet compliance at pass 14 saw NO fence and the recorded text has
zero fences. **Only rebuild_references changed text between the verified insertion
and the verified absence → prime suspect for structural-content erasure** (stale-
source overwrite or section-split swallowing the appended block — the dual-source
class the S2 spec flagged). Pre-S2 this was undiagnosable.

Also confirmed this run: extraction 12 groups correct live; covers-Craft
NOT_SATISFIED honest but NO actor issues Craft searches (0 all run) — P1-core is the
fix, not more verdicts; diagram veto precisely attributed (`_accept: lint count
worse 6→7` — inserted diagram trips the explanatory-prose lint; fix = insert WITH
prose); editor retry burned ~17 identical 12-branch judge rounds on unchanged text
(compliance-verdict memoization per text-hash, L5); fence-contamination repair had
nothing to act on this run (lints_before=6 were other kinds — repair_lints must log
lint NAMES when changed=False).

### 4.3 Eraser trace & residual-lint discoveries

(old §14 tail paragraph)

Lineage: 0.145 → 0.304 → 0.657 → 0.433 → 0.41 → **v6 0.4949 (attempt 9): FIRST
DIAGRAM EVER RECORDED** (explanatory-prose fix cleared _accept; survived every
downstream pass). Attempt-9 trace named BOTH erasers: synthesize_readability
(fences 2→0 — accept guard checks URLs/overlap/ratio but not fences) and the L0
section write-through on duplicate-section docs (4→2 — code block died there;
revert guard committed). Residual lints finally named: five duplicate ## sections +
content-after-References — ONE disease behind the swallowed docs, deleted
subsections, and write-through erasure. A+B bundle COMMITTED (A `27beff6`, B
`d4e84a9`; 925 passed); attempt 10 = first run where structural content should
survive end-to-end.

## Part 5 — Design reference

### 5.1 Writer reference process (P1–P4)

(old §9, in full; REFERENCE-PROCESS GAP ANALYSIS — plan like a researcher, not a
section-filler, 2026-07-05)

Method (user-directed): write down how a competent human researcher would execute the
same task end-to-end, then diff that reference process against what the app actually
does. Not limited to current frameworks. Trigger: run 1534 covered Pi only — the task
says "study how to use Pi AND Craft" and all ~25 worker queries were Pi-only, zero
Craft sources fetched, report silently one-sided.

#### 5.1.1 The reference process (how a human does it)

0. **Disambiguate the subjects first.** "Pi" and "Craft" are ambiguous names. One
   discovery search per subject to decide WHICH Pi / WHICH Craft is meant; if a
   subject has no clear hit, that becomes a stated assumption in Scope — never a
   silent drop. (The studio's failure was born here: no query ever probed Craft.)
1. **Plan by research questions, not sections.** ~6 questions (what is Pi; what is
   Craft; minimal agent in each; how do they COMBINE; what architecture; when to use
   which). Sections are presentation containers; questions are the units of work.
2. **ToC = imposed sections with committed content per section** (incl. comparison
   table + combined-topology diagram in Evidence and Analysis).
3. **Search/fetch/study with a coverage ledger.** Per-subject query fanout (never
   joint-only), docs > repo > tutorials triage, ledger rows: every question and every
   subject has fetched evidence OR an explicit "searched, not found" entry. Draft only
   when the ledger is full; negative results go to Limitations.
4. **Self-review against the task's own checklist** (both subjects? code? diagram?
   sections?) before finalizing. [Studio now has this half: compliance + L0.]

#### 5.1.2 Gap table → ingestion workstreams P1–P4

| Reference step | Studio today | Workstream |
|---|---|---|
| Coverage ledger per subject | Nothing tracks subject→queries→sources→citations; hill-climb carry-forward LOCKS IN a one-sided artifact (workers extend the existing doc, queries follow it) | **P1 subject ledger** |
| Question-first planning | Section-first; spokes are goal-blind section fillers; no unit of work equals "understand Craft" | **P2 question-first hub planning** |
| Disambiguation probe | None; researches whatever the first queries happen to return | **P3 subject disambiguation** |
| Negative-result honesty | Silent omission (fabrication now blocked by guards, but silence remains) | **P4 explicit not-found contract** |

**P1 — subject coverage ledger (highest leverage, cheapest; do first).** ✅ DONE (committed 2026-07-10, `0f72b1d`; live-verified v90 s_943063443a1b — coverage.json populated {Pi:q3/s3/c5, Craft:q3/s3/c3, __joint__} with P3 descriptors; SSE `coverage` event + durable sink; `_coverage_cited`/`_artifact_cited_urls` boundary-guarded url→subjects join, NOT name-substring)
Deterministic dict keyed on extracted subjects (extraction item-5 rule supplies
"covers <subject>" rows): {subject: queries_issued, sources_fetched, cited_in_artifact}.
Populated from the existing web-tool call log + fetched-sources.json + citation set.
Wire-ups: (a) subject with 0 sources → injected worker/reducer guidance naming the
gap ("subject Craft has zero sources — issue direct searches"); (b) compliance row
"covers X" NOT_SATISFIED feeds existing _structural_opps path (no new machinery);
(c) verify stage records per-subject coverage into the run record for lineage.
Breaks the carry-forward lock-in: v(n+1) sees the ledger, not just the one-sided text.

**P2 — question-first planning (deep fix, prompt-level, medium risk).** ✅ DONE-in-research_first `0b4fc47` (this spec is stale hub/spoke framing; the linear rebuild realized the intent).
Hub planning emits research questions + per-question search plans BEFORE section
assignment; spokes own questions, reducer owns section placement. Sections stop being
the unit of research.
> **Realized in research_first:** `_extract_question_contracts` (FRAME) emits per-question
> contracts `{question, subject, answer_form, min_evidence}` BEFORE research; `_questions_by_subject`
> drives per-subject search; `_resolve_question_contracts` measures answering per question. Sections
> are no longer the unit of research. The hub/spoke "spokes/reducer" wording is retired.

**P3 — subject disambiguation probe (small, rides on P1).** ✅ DONE via REUSE (`0f72b1d`) — `_disambiguate_subject` already existed; slice wired its (descriptor, anchors) into the P1 ledger rows + Scope. Zero rebuild (discovery caught the plan's stale ⬜).
Per subject: one cheap discovery search + one LLM call "which interpretation of
<subject> does this task mean? state it in one line" → recorded into Scope section +
ledger. Kills the silent wrong-subject/no-subject failure mode.

**P4 — explicit not-found contract (small, rides on P1).** ✅ DONE (`0f72b1d`) — 0-source subject → auto Limitations line (`_limitations_note`, match-only home so it never splices into a wrong section). Live v90 correctly SILENT (both subjects sourced); 0-source path unit-tested.
Reducer/finalize contract: a subject whose ledger row is empty after the run gets an
auto-placed Limitations entry ("No public sources found for <subject> under
interpretation <I>; coverage is limited to <other subjects>"). Honest asymmetry beats
silent asymmetry — and the LLM judge scores it as legitimate reflection, not a gap.

#### 5.1.3 Sequencing amendment (extends §7)

P1 slots immediately after S2 (finalize pass-list), BEFORE L3 — coverage failure is
user-visible, lineage immunity is internal. P3/P4 ride in the same slice as P1 (share
the ledger). P2 is its own later slice (after L1 verify hardening, since question-first
planning changes what verify must check). Extraction item 5 ("covers <subject>" rows)
is the prerequisite for P1 and is already in flight in the current fix batch.

### 5.2 Editorial gate (E1–E11) + verdict router

(old §10, in full — ◐ ~5/11 E-rows covered by existing+today's machinery (E1/E4/E6/E11
✅, E3/E5 partial); unifying editorial pass = L1, §12 slice 6, not started)

§9 described the WRITER's reference process for one task. This section (a) generalizes
it into the unified pipeline every research-report task should run, and (b) adds the
missing second role: the REVIEWER/EDITOR responsible for publication, with a baseline
checklist and a verdict router. The editor spec is the concrete implementation spec
for L1 (verify hardening) — L1 should implement §10.2/§10.3, not invent its own.

#### 5.2.1 Unified writer pipeline (generalizes §9.1 to every research report)

Five stages; each has an entry artifact and an exit criterion, recorded in workspace:

1. **FRAME** — extract requirements (subjects, structural, sections; extraction rules
   from the 2026-07-05 fix batch); disambiguate every named subject (P3); write
   research questions. Exit: subject ledger initialized, questions listed.
2. **RESEARCH** — per-subject and per-question query fanout (never joint-only);
   triage docs > repo > tutorials; fetch + cache. Exit: ledger full — every subject
   and question has sources OR an explicit not-found row (P1/P4).
3. **DRAFT** — hub/spoke drafting into the section plan; structural content (code/
   diagram/table) drafted where the requirement routes it, grounded in evidence.
   Exit: all sections non-stub, all requirement rows addressed or declared.
4. **EDIT** — the §10.2 editorial gate. Exit: verdict ∈ {PUBLISH, MINOR, MAJOR, REJECT}
   with per-row evidence.
5. **PUBLISH/REVISE** — MINOR → deterministic finalize passes (references rebuild, L0
   structural producer, lint repair); MAJOR → rows become NEXT-version opportunities
   with specific gap statements (hill-climb seeds); REJECT → writeback discarded
   (guards). Verdict + row evidence recorded in the run record (feeds L3 lineage).

#### 5.2.2 Editor's baseline checklist (publish standard)

Split by verification mode. DET rows are cheap and always run; LLM rows are bounded
judge calls. Every row emits (pass/fail, evidence pointer) into the run record.

| # | Row | Mode | Machinery |
|---|-----|------|-----------|
| E1 | Every promised section present, in order | DET | compliance (exists) |
| E2 | No stub sections: every heading (incl. dynamic) has ≥ substantive-content floor beneath it (words excluding headings/links); run-1531's empty "Code Implementation Examples" fails here | DET | NEW — stub detector |
| E3 | Subject coverage: every ledger subject cited in body OR declared in Limitations | DET | P1 ledger (planned) |
| E4 | Citations resolve: every cited URL fetched-verified; no junk band (topical floor); quotes verify against cached pages | DET+LLM | grounding oracle + offtopic floor (exist) |
| E5 | No orphan references: References list ⊇ body citations and ⊆ body citations (both directions) | DET | NEW — cheap set compare (rebuild_references partially covers) |
| E6 | Structural requirements met: code fence present where required AND code-dense; mermaid present AND lints; tables well-formed | DET | compliance + L0 + lint (exist) |
| E7 | Dynamic sections satisfy their SPAWNING requirement (see §10.3) | DET+LLM | NEW — requirement-conditioned section check |
| E8 | Executive summary consistency: no claim in the summary that the body doesn't support | LLM | NEW — one judge call, summary vs body outline |
| E9 | Internal consistency: entity names/numbers stable across sections | LLM | NEW — one judge call (cheap, over stripped text) |
| E10 | Honest limitations: Limitations names the ACTUAL gaps (ledger not-found rows, thin-source subjects), not boilerplate | DET+LLM | P4 + judge |
| E11 | Presentation: heading hierarchy sane, no lint errors, section lengths within band | DET | artifact_lint (exists) |

#### 5.2.3 Verifying dynamically generated sections

A dynamic section (spawned by requirement routing, e.g. "Code Implementation
Examples") must carry its birth certificate: the requirement branch that spawned it,
persisted in the workspace section map (dyn_sections already stores the routing —
extend it to store the branch text). Verification is then requirement-conditioned,
not generic:

1. **Shape check (DET)**: the section body contains the content TYPE its branch
   demands — code branch → a real fence passing the code-density gate; architecture/
   diagram branch → a mermaid block that lints, or (until produced) FAIL that routes
   to L0. Generic non-empty prose under a code heading is a FAIL, not a pass —
   exactly the run-1531 defect class.
2. **Substance check (DET)**: E2 stub floor applies to the section body.
3. **Satisfaction check (LLM)**: one compliance-judge call: "Does this section's
   content satisfy this requirement branch?" — the same judge contract compliance
   already uses for NOT_SATISFIED detection, scoped to the section body instead of
   the whole artifact (tighter context = more reliable on weak models).
4. **Grounding check (DET)**: any citation inside the dyn section obeys E4.

Pass = all four. Fail routes by stage: missing shape → L0 producer (finalize);
present-but-unsatisfying → MAJOR revision opportunity naming the branch.

#### 5.2.4 Verdict router (maps editorial outcome onto existing loop machinery)

- **PUBLISH**: all E-rows pass → record, serve.
- **MINOR** (only auto-fixable rows failed: E5, E6-shape, E11): run the deterministic
  finalize fixers (L0, rebuild_references, lint repair), re-check the failed rows
  once, then PUBLISH. No LLM re-drafting.
- **MAJOR** (content rows failed: E2, E3, E7-satisfaction, E8, E10): the failed rows
  — with their evidence strings — become the next version's seeded opportunities
  (existing _structural_opps + weakness records). This replaces vague mined
  weaknesses with editor-precise gap statements; hill-climb inherits a to-do list,
  not a vibe.
- **REJECT** (grounding rows failed: E4 fabrication/junk): writeback discarded by
  existing guards; the rejection reason recorded for lineage.

#### 5.2.5 Fit into sequencing

- E-rows that already exist (E1, E4, E6, E11) need only WIRING into a single
  editorial pass with per-row evidence records — that IS L1, now spec'd.
- E2 + E5 are small deterministic additions; land with L1.
- E7 lands with the dyn-section birth-certificate extension (small; touches section
  routing + one judge call).
- E3/E10 depend on P1/P4 (§9.2) — the ledger slice.
- E8/E9 are single bounded judge calls — land last in L1, cheapest to defer.
- Verdict router (§10.4) replaces the current score-only postrun decision; MAJOR-row
  seeding supersedes generic weakness mining for tasks with requirements (L4 synergy:
  repeat-failing rows escalate).

### 5.3 First-principles reasoning trace (D-A–D-G, D1–D5)

(old §11, in full — ⬜ D1–D5 pending (§12 slices 5/10/11/14); D-B's fractal-critic
principle already delivered at finalize level via S2 ledger + verdict/gate logging)

§9/§10 mapped the pipeline at STAGE level. This section traces the reasoning INSIDE
each stage as a competent human actually performs it, then names the design-level
gaps. Explicitly not limited by current code/design — several findings require
architecture changes, not patches.

#### 5.3.1 The reasoning trace (what actually happens in my head, step by step)

**Reading the task.** Parse: two tools, one purpose, deliverable + two hard artifacts.
Immediately flag TWO uncertainties: (a) which "Pi"? which "Craft"? — confidence LOW;
(b) how do Pi and Craft RELATE (harness + framework? alternatives? composable?) —
the relationship is itself a research question. I now hold a belief state with
per-item confidence, and my next action is chosen to reduce the LARGEST uncertainty
first. I do not start writing anything.

**Disambiguation searches.** One query per subject, separately. Key move: I read the
result LIST itself as evidence about the world (which interpretation dominates, how
mature the ecosystem is), not merely as fetch candidates. If "Craft agent framework"
returns nothing relevant, I form explicit hypotheses — (a) niche/new tool, (b) name
collision (Craft.do? CraftCMS?), (c) user means a similar-sounding tool — and each
hypothesis gets its own reformulated query. 2–3 reformulations per hypothesis BEFORE
concluding "not found". If still unresolved: ASK THE USER, or proceed under a
recorded, visible assumption.

**Study.** I read pages with questions in hand (extension model? state? minimal
example?) — reading is extraction against a schema, not summarization. Notes are
claims WITH provenance and confidence: claim → source → sure/unsure. Two sources
disagreeing is flagged as a conflict to resolve, never silently averaged. I stop
reading a thread when marginal information gain drops — budget follows expected
value, not fixed caps.

**Writing.** Strict dependency order: Evidence FIRST (facts down), then Key Findings
(patterns ACROSS the evidence), then Implications (consequences of the findings),
Executive Summary LAST (it can only summarize what now exists). The code example
gets run — or at minimum type-checked against the documented API. The diagram is
drawn only after I understand the architecture: a diagram is COMPRESSED
UNDERSTANDING, and if I cannot draw it, that is a signal to go back and research
more, not a missing artifact to bolt on.

**Self-review.** Re-read as the REQUESTER with their original words in hand ("did I
get what I asked for?"), then per-claim provenance spot-check, then explicit gap
declaration. Only then submit.

#### 5.3.2 Design-level defects this exposes (ranked by depth)

| # | Defect | Human behavior | App behavior (by design, not by bug) |
|---|--------|----------------|--------------------------------------|
| D-A | **No uncertainty representation** | Belief state + confidence drives next action | Text in, text out; acts confidently on unexamined assumptions — root ancestor of the wrong-subject, one-sided-coverage, and fabrication failures |
| D-B | **No inner critic per step (verification is terminal, not fractal)** | Search contains diagnose→reformulate; reading contains conflict detection; writing contains does-this-follow | ONE verify at loop end; a bad search result is never diagnosed, it just yields bad fetches |
| D-C | **No claims layer between sources and draft** | pages → claims-with-provenance → outline → text | evidence pages → section text directly; nothing to measure coverage/conflicts/summary-consistency AGAINST (findings.py is embryonically this, but used as patch material, not as the substrate the report compiles from) |
| D-D | **No write-order DAG** | Evidence→Findings→Implications→Summary; summary written last from the final body | All sections drafted in parallel; exec summaries overclaim because they are written blind (E8 in §10 DETECTS this; write-order PREVENTS it) |
| D-E | **No clarification/assumption channel** | Irreducible ambiguity → ask user, or record visible assumption | Fire-and-forget; ambiguity resolved silently by whatever the first search returns |
| D-F | **Artifacts treated as slot-filling, not understanding-compression** | Can't draw the diagram → research more | Can't draw the diagram → L0 bolts one on (right pragmatic patch; wrong to stop there — the upstream signal "system never understood the architecture" is discarded) |
| D-G | **Budget by fixed caps, not information gain** | Stop when marginal gain drops; reallocate to the weak subject | Fixed fetch caps, uniform effort per section regardless of where understanding is thin |

#### 5.3.3 Ingestion — new design workstreams D1–D5

**D1 — claims-with-provenance knowledge layer** (fixes D-C, enables half of §10):
elevate findings (claim, quote, URL, confidence, subject-tag) from transient patch
material to the persistent substrate: workspace `claims.jsonl`, grow-only, deduped.
Draft/synthesis prompts CITE claim IDs; coverage (E3), conflicts, exec-summary
consistency (E8) become queries over the claims table instead of LLM whole-text
judgments. Biggest single design change; most downstream payoff. ✅ ~90% REALIZED-IN-RESEARCH_FIRST — `claims.jsonl` grow-only substrate + `_persist_claims`; WRITE compiles sections FROM claims; coverage (`_coverage_cited`) + question resolution (`_resolve_question_contracts`) are QUERIES over claims, not whole-text LLM judgments. Residual: no per-claim `confidence` field, no explicit claim-ID citations (cite by URL) — both feed D5's belief state, low standalone value.

**D2 — search-step critic with reformulation loop** (fixes D-B for the research
stage, extends P1): after each search, one cheap judgment — "do these results
answer the question? if not, why, and what query next?" — with 2–3 bounded
reformulations. Failure diagnosis, not just failure detection. Rides on P1's ledger
(the 0-source subject triggers it). ✅ DONE (`0f72b1d`) — `_reformulate_queries` (≤3 model-proposed queries, fed descriptor/anchors so a retry never regresses to the bare ambiguous name) + bounded retry sharing the SAME offtopic/anchor/name gates via an extracted `_run_query` closure (byte-identical when no reformulation fires); fail-open [] so a 0-source subject never stalls the run. 0-source path unit-tested (live v90 both subjects sourced → critic idle, as designed).

**D3 — assumption/clarification channel** (fixes D-E, small): unresolvable
disambiguation → recorded assumption, injected into Scope ("interpreting Craft as
X"), surfaced in GUI/run record. Optional interactive mode: pause-and-ask when the
user is present. Cheapest item here, disproportionate payoff. ✅ DONE via P1 ride (`0f72b1d`) — the disambiguation descriptor/anchors now persist structurally in `coverage.json` + the SSE `coverage` event (surfaced in run record), on top of the existing Scope prose assumption line. Interactive pause-and-ask mode NOT built (deferred; non-interactive assumption-recording is the shipped scope).

**D4 — write-order DAG** (fixes D-D, medium): Evidence-bearing sections draft first;
Findings synthesize from claims (D1); Exec Summary generated LAST from the final
body in the finalize phase (it is a deterministic-order pass, like L0). Kills the
overclaiming-summary class by construction; E8 remains as the detector. ✅ REALIZED-BY-CONSTRUCTION — the linear pipeline writes each section from its claims, then `_write_summary` runs LAST (`research_first.py:3556`, after the section loop) over the final body. The formal DAG abstraction isn't built, but its outcome (summary can't overclaim beyond the written body) holds; E8 detector stays.

**D5 — belief/uncertainty state** (fixes D-A/D-F/D-G, north star): per-subject and
per-question confidence in the ledger; next-epoch budget allocated to lowest
confidence; a failed structural producer RAISES a research question instead of only
inserting content. Hardest; do last; D1–D4 are its prerequisites.
✅ **CLOSED 2026-07-12 (code-verified)** — the confidence-ledger + next-epoch-budget
core is DEAD-BY-COLD-START (0 `confidence|epoch|belief` refs in `research_first.py`;
no next epoch to consume a confidence signal — MEASURED-NULL, same class as L4). The
one live part — a failed structural producer raising a research signal not empty
content — is REALIZED-BY-CONSTRUCTION as `_recover_underevidenced`'s closed-loop
in-run recovery → declared question-limitation (+ `_classify_answerability` for
structurally-unanswerable asks). No confidence/budget build (speculative, consumer-less).

#### 5.3.4 Relation to §9/§10

§10's editor is DETECTION; §11 is PREVENTION BY CONSTRUCTION. Pairings:
E3 detects uncovered subjects / P1+D2 prevent them; E8 detects summary overclaim /
D4 prevents it; E4 detects ungrounded text / D1 prevents it (text compiled from
claims can cite only what exists); E7 detects empty dynamic sections / D5-F treats
the emptiness as a research signal. Detection stays even after prevention lands —
belt and suspenders — but prevention is what makes the loop converge instead of
oscillating between defect and patch.

Sequencing: D3 immediately (rides anywhere); D2 with the P1 slice; D1 as its own
major slice after L1 (the editor needs to exist first to measure D1's payoff);
D4 after D1 (findings must be claims-backed before ordering matters); D5 last.

### 5.4 L0 structural producer design

(old §8, in full; STRUCTURAL-CONTENT ROOT CAUSE — why diagrams/tables/code never
ship, 2026-07-05)

**User observation (correct):** many rounds of diagram/table testing, essentially zero
structural content in any final artifact.

#### 5.4.1 The architectural fault (three compounding decisions)

1. **Generation-time ban.** The reducer patch contract is prose-only (anti-regression
   invariant) — structural content can never enter during the phases that produce 95%
   of the artifact. Everything hangs on post-hoc passes.
2. **Post-hoc = conjunction of conservative gates.** A diagram must survive warrant
   detection → generation → grounding guard → score non-regression → weakness-count
   improvement → local-debt drop. Five independent rejection points multiply into
   "almost never ships." Run 1532: editor round IMPROVED score 0.145→0.167, still
   reverted (weak count flat).
3. **Misaligned acceptance oracle.** The rubric is sourcing/verification-heavy; a
   diagram/code block adds no URLs, so it barely moves score or weakness count — the
   protection rules systematically reject the required deliverable class. Meanwhile
   evidence/ holds actual source code no pass ever uses.

Proof by contrast: `rebuild_references_section` — deterministic finalize step
(build → validate → insert → fail-open) — worked on its FIRST live run and every run
since. Nothing structural uses that pattern.

#### 5.4.2 Fix — L0: requirement-driven deterministic structural producer

(NEW TOP ITEM at the time — ✅ DONE, committed; code path verified live run 1534;
diagram path unblocked by fence-lint repair, verify attempt 8)

At finalization, for each still-unmet code/diagram/table-shaped requirement branch
(the compliance downgrades now detect these deterministically):

- **diagram** → existing A2 path (COMPONENT/EDGE lines from the bare model →
  `diagram_render` renders + grounds + inserts deterministically) — run it HERE,
  unconditionally on the unmet branch, not behind the opportunity routing.
- **code** → grounded snippet: prefer a real excerpt from evidence/ source files
  (attributed, fenced, language-tagged); LLM fallback generates from the fetched
  evidence with the same grounding guard.
- **table** → findings/ranking table from already-parsed findings (the ranking-table
  machinery exists).

Acceptance = structural validity only: mermaid lint passes, fences balanced, no
citation lost, compliance branch flips to satisfied. NO score/weak-count conditions —
burden of proof moves from producer to rejector, matching References. Fail-open per
branch. Placement: the section the planner designated for it (dynamic sections /
REQUIRED SUB-SECTIONS), else the best-matching section.

Sequencing: L0 implements immediately after attempt 5 records (its diag shows which
gate kills the routed retry, informing how much of the old path L0 replaces).

### 5.5 Loop-health workstreams (L1–L6)

(old §6, in full; LOOP-HEALTH WORKSTREAMS combine with S1–S5; L2 = S2)

### L1 — Verify hardening (RC1) [after S1/S2 land] — ✅ DONE 371861f (spec now = §10.2 editorial checklist + crashed-status rule) (editorial gate E1-E7/E9/E11 + verdict router live in finalize.py; the ⬜ was stale)
- Deterministic gates FIRST, LLM judgment second: every requirement-shaped check gets
  a hard oracle where one exists (fences, mermaid, citations — landed 2026-07-05;
  extend to tables/word-counts when asked for).
- Scorer on the judge model, not the generator (writer/reviewer separation at the
  SCORE, same degrade-to-base pattern as the reducer).
- Distinguish "verified bad" from "could not verify" everywhere (the
  ComplianceCheckUnavailable pattern, applied to scorer + publish gate): fail-open may
  skip an action, but must never RECORD a pass.
- Tiny fixed eval set (3 requirement-shaped tasks with known-good properties) run as a
  probe script after scorer/rubric changes — rubric changes get calibrated, not vibed.

### L2 — Dead-pass detector (RC2) — this IS S2's pass-list, plus one rule — ✅ DONE (shipped inside S2: per-pass ran/changed/reason + inert-streak flag)
Every pass emits ran/changed/reason via the uniform wrapper (S2). Add: postrun
diagnostics flag any pass with N consecutive no-op epochs on a task ("pass X inert 3
epochs — investigate or expected?"). Silence is no longer indistinguishable from health.

### L3 — Lineage immune system (RC3) — ✅ DONE (committed 2026-07-05; lint criterion dropped, see annotation below)
Deterministic seed-eligibility gate in `latest_with_content`: a row is seed-eligible
only if its artifact passes lint + has ≥1 citation + score not >50% below lineage
median. (REVISED 2026-07-05: lint criterion dropped — lint-broken rows are §14.6
self-heal input, not poison; see `task_runs._seed_ineligible_reason` comment.)
Ineligible rows stay recorded (history) but are skipped for seeding — no more
manual backup-and-delete surgery. Plus: goal/constraints NEVER rotate task identity
(already fixed) — add a regression test if missing.

### L4 — Repeat-weakness escalation (RC4) — ✅ MEASURED-NULL `69bdcf8` (research_first is cold-start; no hot iterative loop to escalate over — codex-concurred; test `test_repeat_failure_machinery_not_invoked_under_research_first`)
When the SAME weakness row survives K=3 epochs byte-identical, stop retrying the same
lever: escalate deterministically — (1) route to the structural editor with the
weakness named, (2) relax the specific execute-contract cap for that section for one
epoch (e.g. allow multi-sentence weave from under-used sources), (3) if still stuck,
mark "app-limit reached" in the run report and STOP burning epochs on that row. The
loop learns to notice its own walls instead of spinning at them.

### L5 — Acceptance economics (RC5) — ◐ PARTIAL (verdict memoization done; per-run cost line pending)
Per-run postrun block: per-pass attempted/accepted counts + tokens spent per accepted
change (data already flows through timing_sink/_dbg; aggregate it). Feeds S5: passes
with chronic ~0% acceptance are candidates for removal or redesign — measured, not
guessed.

### L6 — Probe-before-wire (process rule, institutionalize today's lesson) — ✅ ADOPTED (live probes used for extraction fix + synthesis guards + L0 verification)
No new pass/guard/threshold enters the loop without a standalone probe script proving
ONE manual run behaves (the article's "get one manual run reliable first"). The
2026-07-05 probes (synthesis guards, topical-floor calibration) are the template —
both found the assumption wrong before it shipped deeper.

## Part 6 — Simplification program

### 6.1 Objective & success metrics

(old §0, verbatim)

- **Objective:** behavior-preserving structural cleanup. Every existing feature, guard,
  and event keeps working; the code that implements them gets fewer copies, fewer
  nesting layers, and one home per concept.
- **Metrics (measured before/after):**
  1. `runner.py` line count 5,098 → target < 2,500 (extractions, not deletions).
  2. Bare `except Exception` sites in runner.py 83 → < 20 (one structured pass-wrapper
     replaces the ad-hoc ones; fail-open SEMANTICS preserved, every swallow logged).
  3. URL-handling variants 31 (across 10 files) → 1 shared module.
  4. Full backend suite green after EVERY step (826 passed baseline, 2026-07-05).
  5. One cold E2E after completion: score within noise of the attempt-4 baseline,
     identical diag pass sequence.

### 6.2 Audit evidence (2026-07-05)

(old §1, verbatim)

| Finding | Evidence | Cost already paid |
|---|---|---|
| runner.py god module | 5,098 lines; plan/seed/phase-loop/finalize/editor/record all inline | every session pays navigation + merge-conflict tax |
| Swallowed-exception disease | 83 bare `except Exception` in runner.py alone (tools 15, findings 8) | "0.00s stage timer" bug class; judge-flake crash took 3 attempts to localize |
| URL handling duplicated | 31 regex/normalize variants in 10 files; `_urls` vs `_norm_urls` vs `_normalize_url` | TODAY: synthesis guard rejected valid rewrites because ITS copy didn't normalize |
| `_dbg` duplicated | runner.py + artifact_text.py (comment admits circular-import workaround) | drift risk; already 2 copies |
| Mermaid regex duplicated | `_MERMAID_BLOCK_RE` in artifact_text.py AND requirement_compliance.py | divergence risk (one is `\b`-anchored, one DOTALL-matched) |
| Guard families overlap | synthesis guards, `_sanitize_llm_patches`, expand guards, publish gate all re-implement URL-preservation / length / topicality checks | TODAY: topical floor had to be patched in TWO places (findings + patches) |
| Stale probe scripts in repo | backend/e2e_demo_fix{,2,3}.py, calib_*.py, a2_live_validate.py, editor_*_check.py | noise in every grep/audit |
| Oversized modules | 12 files > 400 lines (tools 1476, task_runs 1147, artifact_text 927…) | general |

### 6.3 S-workstreams (S1–S5)

(old §2, in full; priority order, each independently shippable + test-gated)

### S1 — `studio/textutil.py`: one home for text primitives (LOW RISK, do first) — ✅ DONE (committed 2026-07-05)
Move + de-duplicate: URL_RE, `norm_url()` (single rstrip-punctuation rule),
`extract_urls()`, `content_word_stems()`, `mask_fenced_code` (import from rubric or move
here), `_dbg()` (env-gated diag logger), mermaid-block regex, fence pair-parsing
(`_has_code_fence` logic). Callers import; behavior identical (unit tests pin each
primitive once instead of per-copy).
**Kills:** the entire "fix must land in N places" bug class hit twice today.

### S2 — finalize pipeline as an ordered pass list (MEDIUM RISK, biggest layer cut) — ✅ DONE (committed 2026-07-05; cold E2E compare pending attempt 8)
The epoch-end sequence in runner.py (synthesize → readability → repair_lints →
grounded-full archive → expand → publish gate → editor retry → presentation →
references rebuild → score/record) is ~1,500 lines of nested inline try-blocks. Extract
to `studio/finalize.py`: each pass = `(name, fn(state) -> state)` run by ONE wrapper
that owns fail-open, timing (`timing_sink`), and `_dbg` entry/exit. Order and skip
conditions preserved verbatim.
**Kills:** most of the 83 ad-hoc excepts; every future pass gets observability for free
(no more silent-dead passes — the editor-pass and depth-pass blindness were both this).

### S3 — guard primitives (`studio/guards.py`) (MEDIUM RISK) — ✅ DONE (committed 2026-07-10)
`urls_preserved(before, after, *, allow_new)`, `length_ratio_ok(after, before, *, min_ratio, max_chars)`,
`invented_headings(before, after, *, max_level, mask)`, `topical_verdict(url, req_words, requirement, judge)`
— synthesis guards, `_sanitize_llm_patches`, expand guards, structural_producer, and runner COMPOSE these
instead of re-implementing. All fail-open (cannot raise). Per-guard tests in `tests/test_guards.py` pin every
verbatim threshold. Divergences reconciled EXPLICITLY (no behavior change): count-vs-set left in
`runner._publish_revision_regressed`; per-site heading level/mask args; caller-supplied topical `req_words`.
The behavior-improving fixes (close H4-6 heading hole, mask findings headings, unify req_words to base) are
now a clean opt-in — decoupled from the refactor. Verify: 1131 passed; ruff + F821 clean; import smoke OK;
2 adversarial reviewers (behavior-drift + fail-open-drift) APPROVE.

### S4 — delete/move stale scripts — RESOLVED AS NO-OP (2026-07-05)
Verified: all probe/demo scripts live under `backend/tmp/` which is gitignored — the
REPO is already clean; the audit's Pyright sweep saw local disk, not tracked files.
Local probes are kept deliberately (L6 probe-before-wire values them). No action.

### S5 — measured efficiency wins (ONLY with numbers) — ✅ DONE by MEASUREMENT (2026-07-10): both candidates NULL, no code shipped
- **Offtopic-verdict memoization** — done earlier (kept; relocated into `guards.topical_verdict` by S3).
- **rubric_score / score_breakdown memo — MEASURED NOT WORTH IT.** Env-gated diag (`STUDIO_S5_DIAG`)
  on a live research_first run (`s_bf63191ab773`, v89, score 0.6479, 15.8KB artifact, lineage 492bae60177b):
  `score_breakdown` called **3×/run**, all on identical text (dup=2). A memo saves 2 regex-scans of a 16KB
  string per **13-min run** ≈ microseconds. The plan's premise — "called many times per epoch from editor
  recount loops (`_editor_scored_issues`)" — is DEAD on the research_first architecture: the linear
  FRAME→RESEARCH→CLAIMS→WRITE→ASSEMBLE pipeline scores its artifact a fixed ~3× (assemble + gate new/prior),
  not dozens. The recount loop that made rubric hot was replaced by §16.
- **`_page_for_url` / `_url_in_cache` normalized-key index — MEASURED NOT WORTH IT.** Same run: **40 lookups,
  scan_units=364, max cache_size=16.** ~364 substring-compares total over the whole run — an O(n) scan over a
  ~16-entry cache is trivial. Worse, the lookup is TOLERANT CONTAINMENT (`u in ck or ck in u`), not exact-key,
  so a `{norm_url: page}` dict would CHANGE matching semantics — the index would have to be an exact fast-path
  with fallback to the linear scan, i.e. risk for zero measured gain.
- **CAVEAT (honest, not overclaimed):** measured on the research_first pipeline (task_hash 492bae60177b), the
  canonical path. The legacy runner editor-recount path still exists; if it is ever re-exercised as the primary
  loop, re-measure `rubric_score` hotness there before concluding the memo is dead for THAT path too.
- Diag instrument was throwaway scaffolding (env-gated, text-only key, no-op when unset) — reverted after
  capture; not committed. NOT speculative async/caching beyond these; the gate held (null result recorded).

### Explicit non-goals
- No behavior changes, no guard-threshold changes, no prompt changes.
- No renaming of public event shapes, DB schema, or SSE frames.
- No new abstractions beyond the three named modules (YAGNI).

### 6.4 Verification protocol (per step)

(old §3, verbatim)

1. Full backend suite (baseline 826 passed) — must stay green.
2. `uvx ruff check` on touched files (F821 catches extraction-scope NameErrors — known
   gotcha).
3. After S2 (the risky one): one cold E2E on the Pi/Craft task; compare score vs
   attempt-4 baseline, diff the diag18 pass sequence (same passes, same order).
4. Codex adversarial review of the whole refactor branch before push (standing
   workflow).

### 6.5 Unified execution order

(old §12, in full, 2026-07-05 late — supersedes §7, §9.3, §10.5-seq, §11.4-seq)

Reconciles the S (simplify), L (loop-health), P (coverage), E (editorial rows),
D (design) series into ONE order. Two live-evidence adjustments folded in:

- **L3 ELEVATED** (was after P1): manual lineage surgery has now been needed TWICE in
  one day (run-1531 stub backup; crashed v4 with score 0.0 from the oMLX outage —
  `latest_with_content` would have seeded v5 from a 4K crash stub). Recurring manual
  surgery = the disease L3 cures; it is small (task_runs.py only) and blocks nothing.
- **L1 gains an evidenced item**: a run that CRASHES must record status
  "crashed/unscored", never a numeric score (the 0.0 both poisoned lineage seeding
  and would poison any cross-run stats). This is the "verified bad ≠ could not
  verify" rule applied to the run record itself.

Order (each step: suite green → reviewer pass → commit; cold E2E where marked):

| # | Slice | Contents | Status |
|---|-------|----------|--------|
| 1 | S1 | textutil consolidation | DONE (committed) |
| 2 | EXTRACT | AND/OR + subject-coverage extraction fixes + L0 observability | DONE (committed); live verify in flight (attempt 7 retry) |
| 3 | S2 (=L2) | finalize pass-list + dead-pass detector | IN FLIGHT (producer) → cold E2E compare vs diag19 |
| 4 | L3 | lineage immune system: seed-eligibility gate (lint + ≥1 citation + score floor + NOT crashed) | next after S2 commits |
| 5 | P1+P3+P4+D2+D3 | subject ledger slice: coverage ledger, disambiguation probe, not-found honesty, search-step critic, assumption channel | one slice — all ride the ledger |
| 6 | L1 (=E-wiring) | editorial pass: wire existing E1/E4/E6/E11 + new E2/E5 + crashed-status rule + judge-model scorer | cold E2E after |
| 7 | S3 | guard primitives module | ✅ DONE (committed 2026-07-10) |
| 8 | E7 | dyn-section birth certificate + requirement-conditioned check | |
| 9 | L4 | repeat-weakness escalation (uses S2 pass ledger + L1 row evidence) | |
| 10 | D1 | claims-with-provenance layer | major slice |
| 11 | D4 + E8/E9 | write-order DAG (summary-last) + consistency judges | D4 prevents, E8 detects |
| 12 | P2 | question-first planning | after L1 changes what verify checks |
| 13 | L5+S5 | economics: verdict memoization (done); rubric memo + cache index MEASURED NULL 2026-07-10 (S5 ✅ no code); per-run cost line still open (L5) | S5 done; L5 cost-line pending |
| 14 | D5 | belief/uncertainty state | ✅ CLOSED 2026-07-12: confidence-ledger/next-epoch-budget DEAD-BY-COLD-START; D5-F realized-by-construction (`_recover_underevidenced`) |

Standing rules unchanged: no hardcoding (test-enforced), no behavior change inside
refactor slices, subagent build + separate reviewer, codex adversarial review before
any push, kill/fix/retest on live defects.

### 6.6 Historical sequencing drafts (superseded)

Both drafts below are superseded by the Unified Execution Order (§6.5, old §12);
kept verbatim for history (see the Part 1.1 dashboard note against old §4/§7).

#### 6.6.1 Old §4 — Sequencing

1. Wait for attempt-4 cold run to record (baseline + today's fixes verified live).
2. S4 (trivial) + S1 (shared primitives) → suite → commit each.
3. S2 finalize pipeline → suite + cold E2E compare → commit.
4. S3 guards → suite → commit.
5. S5 with before/after timing numbers → commit.
6. Codex review, then user decides on push (~45 commits ahead by then).

#### 6.6.2 Old §7 — Combined sequencing (supersedes §4)

1. Attempt-4 cold run records (baseline for everything).
2. S4 stale scripts → S1 textutil (shared primitives).
3. S2 finalize pass-list == L2 dead-pass detector (one workstream).
4. L3 lineage immune system (small, high leverage, isolated in task_runs.py).
5. L1 verify hardening (scorer on judge model + never-record-on-unverified).
6. S3 guard primitives, then L4 repeat-weakness escalation (builds on S3).
7. L5 economics in postrun + S5 measured efficiency wins.
8. Codex adversarial review of the whole branch; user decides push.

Each step: full suite green + probe where applicable; after 3 and 5: cold E2E compare.

## Part 7 — Master ledger

(old §17 intro, adapted; merged from PLAN-CONSOLIDATED.md, 2026-07-05 night; that
file is now a stub pointing here)

This doc is now the SINGLE plan. PLAN-CONSOLIDATED.md's four live assets
(requirement matrix, stale-design corrections, backlog, settled negatives) are
merged below, refreshed against tonight's state. **Part 1.2 (old §17.3) is THE
unified backlog** — the lists in Part 6.5/Part 2.3/Part 2.1 (old §12/§14/§15)
remain as dated history; when they disagree with Part 1.2, Part 1.2 wins.

### 7.1 Requirement status matrix (delta only — full 1a/1b tables from the old CONSOLIDATED remain accurate; changed/new rows below)

(old §17.1, verbatim)

Legend: ✅ fulfilled (code-verified) · 🟡 partial · ❌ unfulfilled

| Requirement | Status | Evidence (2026-07-05 night) |
|---|---|---|
| Platform foundations (SPEC 1a: SSE, panels, M7–M10, goal/scheduler) | ✅ all | unchanged from morning sweep |
| Workstreams A–P (1b) | unchanged | D ❌ · J/K/L/M/O 🟡 · rest ✅ — no 1b work done tonight |
| Strong-model reducer (was backlog P0-2b) | ✅ | committed today; reducer runs on judge client, spokes stay on session model |
| Code-shaped compliance downgrade (was backlog P2-8b) | ✅ | committed; joined by diagram-shaped AND covers-shaped gates (distance-based substance check) |
| Deterministic references rebuild (was backlog P2-8) | ✅ | `rebuild_references_section` finalize pass — and EXONERATED as the eraser (per-pass fence trace) |
| Requirement extraction: AND/OR contrast + SUBJECT COVERAGE | ✅ | live 12 stable groups; "example code" / "architecture diagram" now separate mandatory rows; covers Pi / covers Craft separate rows |
| Structural content survives to recorded artifact | 🟡→verify | L0 producer ✅ (v3 code fence, v6 first diagram ever) · BOTH erasers fixed (A `27beff6`, B `d4e84a9`) · attempt 10 IN FLIGHT is the proof run |
| Duplicate-section disease | ✅ fixed | birth: `_synthesize_windowed` heading double-prepend; spread: 3 keep-longest merges → fold-merge; 925 tests |
| Junk-source pipeline | ✅ | density floor + gray-zone judge + memoization; π-Wikipedia/dictionary pages dropped every run since |
| Craft coverage in the artifact | ✅ (rebuild) | research_first covers Craft by construction; craft-agents-oss + agents.craft.do cited in the acceptance runs (0.92–1.0) |
| Report depth proven good live | ✅ (rebuild) | deterministic acceptance 0.92–1.0 live (task_hash 492bae60177b); the pre-rebuild oscillation was hub/spoke, replaced by §16 |
| Pi+Craft integration diagram | ✅ `686d7b0` | grounded directional edge live v80: `Craft.Claude Agent SDK →"utilizes"→ Pi` (endpoint-pick nit remains) |

### 7.2 Corrected stale designs

(old §17.2, verbatim)

All 16 corrections from the morning sweep stand (SPEC M7–M9 shipped; Workstream
O half-live; publish gate lives in `report_quality.py`; `find_template` IS
called; expand_sections exists AND runs; README task_hash/:8000/score-semantics
traps; pre-07-04 line anchors approximate — ARCHITECTURE §10 only). One
addition tonight:

17. **"rebuild_references erasure" (§14 attempt-9 slate item 1)** → wrong
    suspect. Per-pass fence tracing named synthesize_readability (fence-blind
    accept guard) and the L0 section write-through (duplicate-section trigger).
    rebuild_references never ate anything.

### 7.3 Settled negatives (do not rebuild) — morning list + tonight's additions

(old §17.4, verbatim)

Morning list stands (worker search_evidence · cosine seed detector · pairwise
epoch judge · solved/total score · whole-doc echo · auto report-routing
override · web_fetch dedup · F3 anti-truncation · deterministic regex mermaid
repair · gemma citation ceiling on s_db8f2ec3b920 · reducer prose-only patch
contract · goal-blind spokes · two fan-out levers distinct). Added tonight:

- **Flowmark / PyMarkdown / markdown-parser as fence repair**: rejected with
  evidence — all launder broken fences into VALID 4-backtick-wrapped garbage.
  mdformat only, and only AFTER deterministic repairs (invariant test-pinned).
- **Keep-longest-discard-rest duplicate-section merging**: rejected — it was
  a content EATER (swallowed docs, deleted subsections). Fold-merge is the rule.
- **Task-specific phrase lists / hardcoding in source**: prohibited (user
  directive, test-enforced). Structural + distance-based checks only.
- **Guard-patching a generation-core defect past ~2 rounds**: six-run
  oscillation proved guards detect but never converge. Construction fix
  (rebuild the producer) after 2 failed patch rounds on one defect family.

### 7.4 Doc disposition (updated)

(old §17.5, verbatim)

| Doc | Trust for | Do NOT trust for |
|---|---|---|
| **THIS FILE** | the plan, the backlog (§17.3, now Part 1.2), execution log (§13, now Part 3.1), rebuild track (§16, now Part 2.2) | pre-§13 status claims older than their dates |
| PLAN-CONSOLIDATED.md | **THIS FILE — the single source of truth** (absorbed the simplification plan + recovery addendum 2026-07-08) | — |
| PLAN-codebase-simplification.md | nothing — RETIRED stub pointing here (its full Parts 0–7 are this file's body) | everything |
| PLAN-research-first-recovery-addendum.md | nothing — RETIRED stub, folded into Part 8 below | everything |
| ARCHITECTURE-doc-generation-pipeline.md | pipeline mechanics, line index §10 | pre-07-03 line cites |
| SPEC.md / DESIGN-v2.md | contracts + invariants/rationale | milestone/backlog status |
| WORKLOG | what landed ≤ entry 178 (07-03) | 07-04/05 wave (git log + §13 here, now Part 3.1) |
| REBUILD-LESSONS.md | binding constraints for §16 rebuild work (now Part 2.2) | — |
| Big PLAN (A–P) / CURRENT task list / old PLAN-*/HANDOFF-* | specs + settled negatives + commit hashes | status markers, "next steps" |

## Part 8 — Recovery reference & external patterns (folded from PLAN-research-first-recovery-addendum.md, 2026-07-06/07)

> **Status note (2026-07-08):** the addendum's four **Required Recovery Changes** are now largely DELIVERED — (2) generic N-subject relationship model = MVP-8 R3 (`_classify_relationship` {cooperates/competes/extends/alternative/independent/unknown}, claim-grounded, enum removed); (3) evidence-bearing diagram labels = MVP-5b (proper-component naming, glue-word drop); (4) markers publish-blocking = done (`evaluate_publish_readiness` rejects `RESEARCH_FINDING` etc.). (1) fail-visible per-subject coverage gate before WRITE remains PARTIAL (offtopic floor + anchor gate exist; a hard `failed_partial` stop for a zero-source subject is not yet wired). The reference material below is kept for the coverage-gate work and future provenance decisions.

### 8.1 Required Recovery Changes (original addendum text)

1. **Fail-visible per-subject coverage gate before WRITE.** Each requested subject must have ≥1 accepted source and ≥1 claim-bearing evidence path before section writing. One generic targeted recovery query allowed for a zero-source subject; if still empty, stop with `failed_partial`, not weak fallback prose. *(PARTIAL — see status note.)*
2. **Generic N-subject relationship model** (not first-pair). Pairwise/all-subject relationship records; labels from a small generic vocabulary; only corroborated relationships enter summaries/diagrams/code. *(✅ MVP-8.)*
3. **Evidence-bearing diagram labels.** Fallback labels derived from cited evidence/claims; reject filler; omit or fail visibly if ungroundable. *(✅ MVP-5b.)*
4. **Markers publish-blocking.** Any internal token/marker/placeholder/metadata echo in the final artifact blocks publication with a diagnostic. *(✅ done.)*

### 8.2 MVP harness (`backend/studio/research_quality_mvp.py`)

Offline diagnostic over `artifact.md`/`result.md` + `claims.jsonl`. Gates: `subject_evidence`, `relationship_evidence`, `n_subject_relationship_evidence`, `diagram_label_quality`, `summary_mechanism_grounding`, `internal_marker_leaks`; plus gate-health canaries (known-bad fixtures that MUST fail each gate). Read-only by design — promotion moves individual checks into the `research_first`/finalize boundaries, not the whole module into generation. `scratchpad/accept_check.py` layers the Part 1.3 a–e + format checks (References-last, refs⊆claims) on top for acceptance runs.

### 8.3 External patterns & OSS study (reference, not dependencies)

Comparable research/report tools separate **source planning/curation from synthesis** — synthesis should not start until source coverage + evidence contracts exist for the requested subjects (generic, no topic hardcoding): OpenAI/Gemini Deep Research, NotebookLM, Elicit systematic review, M365 Copilot Researcher. OSS study (2026-07-07): STORM (coverage-before-writing), PaperQA2 (search→evidence→answer), GPT Researcher (planner/executor/publisher + eval folders), Open Deep Research (stage/config/eval separation), OpenScience (provenance DAG: produced/consumed/derived-from/supports/refutes; structured reviewer findings), Shepherd (durable reviewable execution traces). **Finding:** no single OSS app solves all blockers; the common reusable pattern is a first-class evidence/provenance layer between research and write — copy the small patterns, not the platforms. Diagram-label provenance is AgentKit's own concern (no OSS solves it directly).

### 8.4 Standing assumptions (binding)

- No hardcoded task/subject names, topic-specific prose, or fixed templates in production code.
- No new dependencies unless a reviewed plan proves the current stack cannot meet the requirement.
- Acceptance runs are staged after review; every event lands in Part 2.2/3.1 with measured results.
