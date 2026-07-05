# PLAN — generic required-component extraction → assignment → generation (diagram/table/code)

**Date:** 2026-07-03
**Status:** SUPERSEDED by §6 (live-run evidence flipped the diagnosis). The pre-run P1–P5
workstreams below are kept for history; §6 is the authoritative corrected spec. No code written yet.

> **§6 is authoritative.** A completed 2-epoch gemma run (session s_0a3669434b76, task
> b9d55be4f6d6) proved the pre-run P4 premise WRONG — the structural retry DOES fire. The real
> bugs are different. Read §6 first.
**Problem owner:** the diagram/table/code deliverable must be produced for ANY task that
requires it, independent of whether the task phrases it as mandatory ("and") or optional
("or"). The current design only reliably produces structural content when it lands on the
`quality_opportunity` path — a routing accident of task phrasing.

---

## 0. Objective & success metric

- **Objective:** a required structural component (diagram / table / code example) that the task
  asks for is reliably PRODUCED in the final artifact, generically, regardless of task phrasing
  or which requirement-group shape (AND vs OR) it parses into.
- **Primary metric:** on a fixed benchmark task that requires an architecture diagram, the final
  artifact contains a well-formed, artifact-grounded ` ```mermaid ` block — measured across
  BOTH "…code **and** design architecture" and "…code **or** design architecture" phrasings, on
  the deployed gemma model (`gemma-4-26B-A4B-it-heretic-4bit` via oMLX :8000). Target: ≥ the
  entry-174 calibration bar (WORKLOG 174: 8/8 at temp 0.0/0.8), for both phrasings.
- **Parity metrics (must NOT regress):**
  - Citation grounding for PROSE unchanged — `_parse_findings` still drops any prose patch whose
    URL is missing/fabricated (the anti-fabrication guard, `findings.py:421-425`).
  - Deterministic rubric + LLM judge score on a byte-identical artifact unchanged.
  - Every revert-on-regression invariant in `_run_editor_pass` / `_editor_structural_retry`
    (snapshot/restore, no net-new weakness, score-non-regression) intact.
  - Hill-climb lineage parity (task_hash unchanged; seed carry-forward unchanged).
  - Full `pytest tests` green.

## 1. Verified root cause (deterministic, traced 2026-07-03)

`requirement_compliance_issues` (`requirement_compliance.py:314-341`) aggregates per-branch
verdicts into two lists that flow to DIFFERENT destinations at epoch end (`runner.py:3975-4026`):

- `hard_issues` (mandatory unmet branch) → `extra_issues` + score penalty (`runner.py:4012-4015`).
  Reaches the editor only as a TEXT weakness — a soft editor turn does NOT reliably emit mermaid
  (WORKLOG 172).
- `quality_opportunities` (unmet OR-sibling of an already-satisfied group) → threaded to
  `_run_editor_pass(quality_opportunities=…)` → classified by `_is_structural_opportunity`
  (`runner.py:1275-1276`) → structural ones routed to `_editor_structural_retry`
  (`runner.py:1392`), the bounded 3-attempt fresh-snapshot generator WORKLOG 174 measured 8/8.

The structural-retry classifier is applied ONLY to `quality_opportunities`, never to
`hard_issues`. Phrasing decides the group shape (`requirement_compliance.py:323-339`):
"or" → one OR-group, unmet diagram branch = quality_opportunity (has a generator);
"and" → two 1-branch groups, unmet diagram branch = hard_issue (no generator). Hence the
phrasing-dependent behaviour. The entry-176 `_DIAGRAM_SHAPED_RE` downgrade gate
(`requirement_compliance.py:309-313`) is correct and stays; it is upstream of this split.

## 2. Design (4 parts)

### P1 — Extract required components → assignments (proactive)

`extract_requirements(base_client, _original_requirement)` already runs once/run
(`runner.py:3972`, cached in `self._task_requirements`) but only feeds the reactive verify step.
Extend so each extracted requirement is tagged with a **producible component type**
(prose / diagram / table / code / citation-count / length …) and, for structural types,
assigned to the most-relevant section as a first-class deliverable the planner/worker sees.
- Reuse the existing extraction call — do NOT add a second LLM extraction pass.
- Component-type tagging reuses `_STRUCTURAL_OPP_RE` + `_is_structural_opportunity` (regex
  fast-path + LLM fallback) — no new fixed keyword vocabulary.

### P2 — Spoke workers produce assigned structural components (generation-time)

The assigned structural component for a section is injected into that section's worker prompt so
the diagram/table/code is drafted INLINE during initial generation, not bolted on at the end.
- Open question for codex: section-ownership routing — which section owns "an architecture
  diagram"? Proposed: the section whose title/subject best matches the component (embedding or
  LLM one-shot), falling back to a designated structural home (e.g. "Background and Context").

### P3 — Reducer emits/preserves structural blocks (contract extension, NOT relaxation)

Today the reducer patch contract (`findings.py:268-270`) is prose-only: "one short sentence,
MUST include ≥1 http(s) URL, MUST NOT contain '#'". This is the citation-grounding guard —
`_parse_findings` (`findings.py:421-425`) drops any finding lacking a verified URL.
- Add a SEPARATE `structural` patch op alongside the prose op. The structural op is:
  - **EXEMPT** from the URL requirement (a synthesized diagram cites no external page), and
  - allowed to contain `#`/code fences/multi-line, and
  - guarded a DIFFERENT way: (a) well-formedness (```mermaid/table/code block parses) AND
    (b) artifact-groundedness — its content must be derivable from the artifact's own prose
    (the WORKLOG-172 property: the mermaid stages matched the artifact's own Plan→Act→Observe
    →Reflect text). Proposed check: entity/keyword overlap with the artifact, LLM-fallback.
- **INVARIANT:** the prose op keeps its URL-grounding EXACTLY as today. The structural op is a
  new lane, not a loosening of the existing one — no prose patch may reach the doc without a
  verified URL. This is the single highest-risk change; codex should scrutinize that the two
  lanes cannot cross-contaminate (a prose claim smuggled through the structural op).

### P4 — Editor structural retry as backstop for BOTH lists (generic fix)

In `_run_editor_pass`, classify structural items out of the compliance `hard_issues`
(currently only `extra_issues`) with the SAME `_is_structural_opportunity`, and route them into
`_editor_structural_retry` alongside structural `quality_opportunities`. This closes the or/and
asymmetry even when P1–P3 don't produce the component upstream.
- **Accept-gate generalization:** the retry currently keeps a candidate only if it (a) doesn't
  regress score/weakness/lint AND (b) strictly reduces the OR-sibling opportunity count
  (`opportunity_recount`, `runner.py:1287`). For a structural HARD issue the success signal is
  "the required structural block now exists / compliance penalty dropped". `_make_opportunity_recount`
  must be generalized (or paired) to count unsatisfied STRUCTURAL requirements broadly, not only
  OR-siblings — else a hard-issue candidate that adds the diagram is reverted for a flat count.
  Open question for codex: generalize the recount vs add a parallel "structural-satisfied" gate.
- Hard structural issues STAY in the penalty/weakness accounting; the retry is additive and
  restores-on-fail, so an unresolved mandatory diagram is still correctly penalized.

## 3. Non-regression verification protocol

1. Full `pytest tests` green + new unit tests per part.
2. **Real-gemma calibration** (mirror WORKLOG 172/174, no LLM mocking): drive the real path on a
   realistic artifact + "add an architecture diagram" requirement, for BOTH "and" and "or"
   phrasings, temp 0.0 (1 trial) + temp 0.8 (3 trials); assert a well-formed grounded mermaid
   appears and survives the revert gate (`lint_artifact` clean). Report honest counts; do not
   manufacture a passing scenario.
3. Prose-grounding regression: a fabricated-URL prose patch is STILL dropped by `_parse_findings`
   after the structural lane is added (pin the two lanes are independent).
4. Lineage/score parity on a byte-identical artifact.
5. One live-GUI E2E (browser EventSource) on the benchmark task → artifact has the diagram, DB
   row recorded, no regression in judge/deterministic score.

### P5 — Report depth / evidence utilization (separable workstream)

**Observed (live run s_0a3669434b76, 2026-07-03):** 400KB fetched evidence across 10 sources →
20.3KB report, 2485 words, 10 URLs cited (~5% of evidence volume; ~250 words synthesized per
source). Epoch 2 extracted 23 raw findings but applied **NO PATCHES** (all deduped/doc-dup against
the v9 seed) — the report is frozen at the seed's depth and the hill-climb expands it no further.

**Grounded mechanism (traced):**
- `findings.py:333` — "F6: same-URL merge + scaffolding strip + **per-section density cap**. Thins
  the wall." A per-section density cap actively thins content; designed against citation-walls, it
  may over-thin legitimate depth.
- `findings.py:268` — reducer patch = "one short paragraph or sentence, not a markdown section" →
  small accretions; depth builds slowly and plateaus.
- Aggressive dedup (`dedup`/`doc_dup`) removes re-extracted findings against the seed → a
  seed-carried hill-climb epoch adds ~nothing, so depth never scales with evidence.

**Direction (for codex to shape):** make synthesized depth scale with available grounded evidence —
e.g. a density cap that is a floor-aware target (not a hard thin), a synthesis pass that expands
under-developed sections when unused grounded findings remain, and/or a "coverage" signal (fraction
of distinct grounded sources materially used) fed into the rubric so a shallow report is scored
down. **Guard:** must NOT reintroduce citation-walls or ungrounded padding — depth must come from
USED grounded evidence, not filler. Metric: report length/word-count and distinct-source-coverage
rise on a fixed evidence set without any drop in grounding or judge score.

**Open question for codex:** is P5 a tuning of the existing density cap + dedup thresholds, or does
it need a new "expand under-developed sections from unused grounded findings" synthesis stage?

## 4. Sequencing (post-review)

1. **P4 only** — classify structural `hard_issues` (same `_is_structural_opportunity`) and feed
   them to `_editor_structural_retry`.
2. Add a **separate** strict `structural_requirement_recount` gate used only by
   `_editor_structural_retry` for hard structural issues — do NOT generalize
   `_make_opportunity_recount` (runner.py:3436; its OR-sibling-only meaning is a safety contract).
3. Wire the recount even when `quality_opportunities` is empty, IF structural hard issues exist
   (today the recount is only created when opportunities exist, runner.py:4021).
4. Tests: mandatory-diagram hard issue triggers retry; accepted only when strict compliance drops;
   unknown recount restores snapshot; existing OR-opportunity tests unchanged.
5. Real-gemma calibration for BOTH "and" and "or" (mirror WORKLOG 172/174).
6. **P5 as a separate PR** after P4.
7. **P3 skipped. P1/P2 deferred** unless P4 fails real calibration.

## 5. Review outcome & resolved decisions (codex session 019f27af + user, 2026-07-03)

- **P3 (url-exempt structural reducer op) — DROPPED.** Codex: the reducer is safe precisely
  because `_sanitize_llm_patches` (findings.py:369) + `_parse_findings` (findings.py:421) reject
  any non-URL-grounded content; a structural lane is *mechanically* a relaxation — "artifact-
  groundedness by keyword/entity overlap" passes copied nouns while inventing relationships, so a
  fabricated diagram edge could slip through. **User decision: accept — keep reducer strictly
  prose-only.** Structural content is produced by the editor structural retry (P4), optionally
  spoke workers later. (Supersedes the earlier "reducer can emit structural" direction.)
- **P4 accept-gate — SEPARATE recount, not generalized.** Add `structural_requirement_recount`
  (strict compliance) used only by the structural retry; leave `_make_opportunity_recount`
  untouched. Acceptance: score non-decrease + no net-new normalized weakness + structural-
  unsatisfied count strictly decreases + restore-on-unknown/failure (preserves the revert
  invariant at runner.py:1090/1119).
- **P1/P2 — DEFERRED.** Root cause is a routing bug the P4 backstop already solves; proactive
  assignment adds planner/section-ownership/worker-variance surface not yet justified. Revisit
  only if P4 fails real-gemma calibration or places diagrams incoherently.
- **P5 — multiple filters, not just the density cap.** Codex found the plateau is compounded:
  already-cited URLs dropped before consolidation (findings.py:327), same-URL merge keeps one
  finding/URL (dedup.py:77), per-target cap drops findings after **3 per section** (dedup.py:92),
  plus the one-short-paragraph reducer. Fix = a small **expand-underdeveloped-sections** stage fed
  by UNUSED grounded findings, hard-guarded (only `_parse_findings` survivors; URL retention;
  expand only sections below an evidence/word floor; ≤1 synthesized paragraph per source/cluster;
  reject if citation density rises without synthesis density). Do NOT loosen dedup globally
  (reintroduces citation-walls).

---

## 6. CORRECTED DIAGNOSIS — authoritative (live run s_0a3669434b76 / b9d55be4f6d6, 2026-07-03)

A completed 2-epoch gemma run (bare "and" requirement, seed = v9 artifact, 2 epochs) produced:

| | v1 (epoch 1) | v2 (epoch 2, KEPT) |
|---|---|---|
| score_result (LLM judge) | 0.97 | **0.7531 ↓** |
| size | 20.3KB / 2485w | **9.5KB / 1164w ↓** |
| mermaid blocks | 0 | 0 |
| unique URLs | 10 | 9 |
| artifact_lint issues | 1 | **14 ↑** |

Evidence fetched: 400KB / 10 sources. T1 stage timings: postrun=1499s (dominant), editor=636s,
plan=28s, publish=36s, topology=42s, total=3683s (~61min for 2 epochs). No errors, no tracebacks.

### Bug A — structural retry fires but gemma emits 0 diagrams (NOT a routing bug)

Log: `editor structural retry attempt=1..3 REJECT score 0.753->0.753 opp=1` (×2 rounds = 6 attempts).
`opp=1` ⇒ the diagram WAS a `quality_opportunity` and the retry DID fire — the pre-run P4 premise
("structural hard_issues never reach the retry") is FALSE for this task. `grep mermaid agent_io.jsonl`
= **0**: gemma never produced a mermaid across all 6 attempts. Contradicts WORKLOG 174's 8/8 — that
calibrated on a richer artifact; on this degraded 9.5KB report gemma has little to ground a diagram in.
**Fix ≠ routing.** Options: (a) deterministic mermaid SCAFFOLD the model fills in (most reliable on a
weak model), (b) stronger/forced retry prompt, (c) more attempts. Needs real-gemma calibration on the
ACTUAL degraded artifact, not a hand-picked easy one.

### Bug B — epoch gate prefers SHRINKAGE (the dominant "shallow report" driver)

`accept_epoch` (epoch_gate.py:27) keeps an epoch iff `make_rubric_preference` (epoch_gate.py:82-87):
`rubric_score(new) - rubric_score(prior) > eps`. `rubric_score` is DETERMINISTIC and **length/depth-
blind**. The docstring records they REMOVED the old length ratchet (it let same-length-worse rewrites
through) and replaced it with pure rubric preference — leaving NO depth protection. So epoch 2's 9.5KB
rewrite scored marginally higher on the length-blind rubric and was KEPT over the 20KB seed, even though
`score_result` (the LLM judge) rated it 0.22 LOWER. The two scorers disagree and the length-blind one
gates. Result: the report ratchets DOWN.

### Bug C — depth: 400KB evidence → best-case 20KB report (~5%)

Multi-filter plateau (codex-confirmed): per-target cap of 3 findings/section (dedup.py:92), already-cited
URLs dropped pre-consolidation (findings.py:327), same-URL merge (dedup.py:77), one-short-paragraph
reducer contract (findings.py:268).

### Unified fix (B+C share a root: nothing VALUES depth/evidence-coverage)

Add a **grounded-source-coverage dimension to `rubric_score`** (studio.rubric) = fraction of DISTINCT
grounded sources (verified URLs / `_parse_findings` survivors) materially synthesized into the report.
This simultaneously: (B) makes the epoch gate STOP preferring shrinkage — a content-dropping rewrite now
loses coverage and fails the gate; (C) scores shallow reports lower so the hill-climb is pushed toward
depth. Pair with the P5 guarded expand-underdeveloped-sections stage for active depth growth.
**Guards:** coverage must count only GROUNDED synthesis (no citation-walls / ungrounded padding gaming
the dimension); do NOT reinstate a blind length ratchet (the thing they deliberately removed); prose
URL-grounding unchanged; every revert invariant intact.

### Revised implementation workstreams (user chose ALL THREE, 2026-07-03)

1. **B+C — coverage dimension in `rubric_score`** (studio/rubric.py) + wire into the epoch-gate
   preference AND the recorded/hill-climb scoring, so gate and judge stop disagreeing on depth. Highest
   leverage; fixes the shrink-ratchet and the shallowness together. Guard the anti-gaming invariant.
2. **C — expand-underdeveloped-sections stage** (P5 §): guarded synthesis of UNUSED grounded findings
   into sections below an evidence/word floor; raise the per-section finding cap (dedup.py:92) or make it
   floor-aware; stop dropping already-cited URLs pre-consolidation when the section is under-developed.
3. **A — diagram forcing**: deterministic mermaid scaffold the model completes (preferred over prompt-
   only), with real-gemma calibration on the degraded artifact; keep the existing retry snapshot/restore
   + accept gates; a produced-but-ungrounded diagram must still be rejected.
4. **Tests**: real-gemma calibration for the diagram on the ACTUAL artifact shape; a regression test that
   a content-dropping rewrite is now REJECTED by the gate (pins Bug B); a depth test that coverage rises
   without grounding loss; existing revert/grounding tests unchanged.
5. **Re-run**: SAME task ("and" bare → b9d55be4f6d6), SAME config (gemma, 2 epochs, seed_path=v9
   artifact). Verify vs this baseline: diagram present (was 0), report does NOT shrink / word-count &
   source-coverage up (was 20KB→9.5KB), score does not regress, lints down from 14.

---

## 7. CONSOLIDATED PLAN — my diagnosis + codex correction, VERIFIED (2026-07-03)

Codex independently re-diagnosed on the run evidence and **refuted my §6 Bug B** (I blamed the epoch
gate). I verified codex's finding against the on-disk io files. Consolidated, evidence-backed:

### Bug B — CORRECTED: unguarded post-gate publish-revision, NOT the epoch gate

**Verified evidence:** `io/e2:s20.out.md` (epoch-2 output, PRE-finalize) = **21,442 bytes / 2545 words**;
final `artifact.md`/`result.md` = **9,451 / 1164 words**. The epoch gate (epoch_gate.py:27) accepted the
21.4KB text — it did the right thing. The ~11KB loss is entirely POST-gate.

**Root cause (runner.py:3843-3902):** the publish-revision is a **whole-document LLM rewrite** —
`base_client.chat(build_publish_revision_prompt(full_report, issues, evidence))` — accepted WHOLESALE on
line 3882 `if not _rev_issues:` (only "the rewrite passes deterministic publish-readiness checks"). There
is **NO shrink guard, NO verified-URL-preservation, NO score/quality-regression revert** — unlike the editor
pass (full snapshot/restore + score-non-regression + no-net-new-weakness). On a weak model (gemma) the
rewrite condensed the report ~55% and dropped URL density (codex: 48 URL-lines→17, even a typo
`dubit3`↔`dabit3`). This is the "whole-document echo" failure the reducer designs against (findings.py:211),
reintroduced unguarded in the publish path. Explains the score drop (base ~0.897 → 0.7531 recorded) and is
the dominant driver of the shallow served report.

**Fix (minimal, mirrors the editor guards):** before accepting `_rev_text` (line 3882), REJECT the revision
— keep pre-revision `_scored_text` — if it (a) shrinks materially (word/char below a margin of pre-revision),
OR (b) drops verified-URL count / distinct-source coverage, OR (c) regresses the deterministic rubric/judge
score. A publish revision must only FIX cited defects, never shrink or de-cite. (Larger alternative: scope it
to targeted patches instead of a whole-doc rewrite — same anti-echo principle as the reducer. Start w/ guard.)

### Bug A — unchanged: structural retry fires, gemma emits 0 mermaid

Retry mechanism correct; weak model produces no diagram on the degraded artifact. Note: even a pre-gate
diagram could be stripped by the unguarded publish-revision (Bug B) — so **fix B first** or a produced diagram
won't survive. Fix: deterministic mermaid scaffold the model completes + real-gemma calibration on the actual
artifact.

### Bug C — depth: partially resolved by fixing B

Fixing B stops the ~55% shrink, restoring the ~20KB baseline. Remaining depth gap (400KB→20KB) is the P5
multi-filter issue (per-target cap 3/section dedup.py:92, cited-URL drop findings.py:327, one-short-paragraph
reducer). The unified coverage-in-rubric dimension (my §6 hypothesis) is now SECONDARY — codex did not verdict
it (timed out); defer, since fixing B recovers most of the loss. Revisit only if depth is still short after B.

### Consolidated sequence

1. **Bug B FIRST** — guard the publish-revision (shrink + URL-preservation + score-regression → reject
   rewrite, keep pre-revision text). Smallest, highest-leverage; recovers report size + citations. Regression
   test: a shrinking/de-citing publish rewrite is REJECTED.
2. **Bug A** — deterministic mermaid scaffold + gemma calibration on the real artifact.
3. **Bug C / P5** — guarded expand-underdeveloped-sections + floor-aware per-section cap; coverage-in-rubric
   deferred.
4. Real-gemma calibration; codex review of the diff; then **re-run** same task/config, compare to §6 baseline
   (was 9.5KB/0.75/0-diagram/14-lint → expect ≥20KB, no shrink, diagram present, score ≥ v1, lints down).

**Method note:** two diagnoses were wrong before the evidence (pre-run "hard_issue routing"; §6 "epoch gate
prefers shrinkage"). Both corrected only by reading actual run artifacts / independent codex review. Fix B is
grounded in measured 21.4KB→9.5KB pre/post-gate file sizes, not a theory.

---

## 8. FIELD RESEARCH — how existing frameworks implement each new-arch primitive (2026-07-03)

Studied at implementation level (not abstracts). Per primitive: the proven pattern → what our MVP copies.

### 8.1 Patch-based transforms (replaces whole-doc publish-revision — Bug B)

- **Step-DeepResearch `patch` action** (arXiv 2512.20491 §5.2, stepfun-ai/StepDeepResearch): agent emits only
  the modified fragment + minimal ANCHOR context; the TOOL applies it atomically via **fuzzy matching**.
  Designed explicitly for mid-sized models ("diff formats impose a reasoning burden on mid-sized models").
  Measured: **>70% output-token reduction** on long-report local polishing + higher success on complex edits.
  → This is the exact spec for the new publish-revision: LLM names defect → emits fragment+anchor → tool
  applies. Never re-emits the document.
- **Aider edit-format benchmarks** (aider.chat/docs/benchmarks, unified-diffs): (a) plain-text edit formats
  BEAT structured JSON/function-call formats — counterintuitive but repeatedly measured; (b) weak models
  (GPT-3.5-class) pathologically stuff the WHOLE file into a search/replace block — with gemma, cap patch
  size and reject whole-doc-shaped patches; (c) **flexible/fuzzy patch application is load-bearing** —
  disabling it = 9× edit failures; failed patch → one retry with corrected anchor; (d) udiff cut GPT-4-Turbo
  "lazy elision" 3×. (e) Aider's **architect/editor split**: a planning turn decides WHAT to change in plain
  text; a separate editing turn encodes it as a patch — helps weak editors. Our reducer already discovered
  the same contract independently (small scoped patches, never re-emit doc, findings.py:211) — the field
  confirms it; the fix is applying it to the ONE stage that violates it.

### 8.2 Deterministic scaffold for structural content (Bug A)

- **Mermaid Safe Mode / Comment-First Protocol** (EugeneJian/Mermaid_Safe_Mode, MIT): before EVERY node the
  model writes `%% Type: Decision {}` then TRANSCRIBES those exact symbols into the node — converts diagram
  gen from probabilistic reasoning to pattern-matching ("State Reification": externalize fragile latent state
  into the context window). Verified on Qwen-Turbo/Haiku/Flash-class (= gemma class): syntax errors
  **~8-15% → <0.1%**, one LLM call, ~100-token prompt overhead. Constraints that make it work: ≤10-15 nodes,
  NO styling (classDef/linkStyle banned — biggest error source), all labels double-quoted.
  → Drop-in for `_editor_structural_retry_prompt`: CFP system-prompt + node budget + no-styling, THEN our
  existing validate/lint + grounding accept-gate as the rare-repair fallback (defense-in-depth, same as their
  production-hardening tier).
- **system-design-visualizer** pattern: LLM emits structured component list → deterministic NORMALIZATION
  layer renders Mermaid-safe syntax. Even stronger separation (model never writes mermaid at all) — candidate
  A3 if CFP alone underperforms on gemma: model lists components/edges as plain lines, our code renders the
  mermaid deterministically. Grounding check then runs on the component list (names must appear in artifact
  prose), which is EASIER to verify than raw mermaid.

### 8.3 Recursive ε-monotonic accept gate (Bug B generalization)

- **CogGen** (ACL 2026 Findings): recursive accept at BOTH macro (global report) and micro (section)
  granularity — draft(t+1) accepted only if quality gain > ε. Validates: one gate primitive, applied
  recursively, not one flat gate at one stage.
- **Self-Refine + optimal-stopping literature** (arXiv 2303.17651; clawrxiv 2604.02035): over-editing DEGRADES
  quality; refinement needs an explicit stop rule ("continue iff expected gain > cost"), self-reported
  "done" is poorly calibrated. → Publish-revision must be gated AND allowed to conclude "no revision needed"
  — today it always rewrites (no stop rule at all).
- Our `epoch_gate.accept_epoch` + `make_rubric_preference(eps=0.01)` IS already this primitive at epoch
  granularity — the redesign extends the SAME function to every mutating stage (per user constraint §7:
  reuse, don't fork).

### 8.4 Outline-as-scaffold for depth (Bug C)

- **STORM** (stanford-oval/storm, 29.7k★, NAACL'24): outline generated FROM curated research, then populated
  **section by section** with per-section references; polish is a SEPARATE scoped stage (add summary, dedup) —
  even STORM's "polish" never free-rewrites the document. 4 modules with defined interfaces (curation /
  outline / generation / polish) = the content-vs-presentation split, shipped at scale.
- **ScaffoldAgent** (arXiv 2606.20122): outline as an EVOLVING scaffold with three explicit ops —
  **Expansion / Contraction / Revision** — scheduled by a utility signal (retrieval gain + structural quality
  + trial-generation quality) that also decides termination ("further structural change unlikely to improve").
  → Our deliverable template = the outline; depth work (P5 expand-underdeveloped-sections) becomes an
  Expansion op triggered by unused-grounded-findings utility, with Contraction explicitly NOT auto-fired
  (that's Bug B's shrinkage — contraction only on redundancy evidence, gated).
- **WebThinker** section-subtask mapping (via Zylos survey): subtask findings map 1:1 to report sections →
  synthesis is merge-and-edit, never free-form regeneration. Matches our section-ownership model.

### 8.5 Reuse verdict

Adopt directly: Step-DeepResearch patch action semantics (fragment+anchor+fuzzy apply) · Mermaid Safe Mode CFP
prompt (MIT, copy-paste) · CogGen ε-accept shape (already have it in epoch_gate — extend) · ScaffoldAgent
Expand/Contract/Revise op vocabulary for the template. Consider (A3 fallback): visualizer-style
components-list → deterministic renderer. Nothing here requires a new framework dependency — all patterns
graft onto existing studio/ parts (per §7 constraint: reducer contract, epoch_gate, rubric/scoring_matrix,
weakness mining, template).

---

## 9. CODEX REDESIGN VERDICT + FINAL CONSOLIDATED SPEC (2026-07-03, codex medium-effort inline)

**Verdict: architecture SOUND, with two corrections.**

1. **NOT one global ε-gate.** A uniform monotonic gate can freeze early mediocre structure when
   `rubric_score` misses depth/coverage/diagram value. Reuse `epoch_gate.accept_epoch` (epoch_gate.py:27)
   as the ONE primitive everywhere, but each stage gets a **stage-appropriate `prefer` fn**: global rubric
   for whole-artifact stages, section rubric for section patches, structural validator for diagrams — plus
   an explicit **no-op exit** (tie rejects; no-op is a SUCCESSFUL terminal state, not failure).
2. **CFP is the MVP candidate, components-list→deterministic-renderer is the likely final default.** CFP
   still asks gemma to emit mermaid; the live run showed 6 attempts / 0 mermaid. If CFP misses on real
   gemma, switch — the components-list is also easier to ground-check.

**Codex-refined migration sequence (each step independently shippable + testable):**
1. **Guard current publish-revision** (`runner.py:3843-3902`): `_rev_text` cannot replace `_scored_text` if
   it materially shrinks / drops verified URLs / regresses rubric-preference. Pins the 21.4KB→9.5KB loss.
2. **Stage-gate wrapper around existing `accept_epoch`** — every mutating stage gets accept|reject|no-op
   with a stage-appropriate prefer fn. Not a new gate.
3. **Patch-based publish-revision** — reuse reducer semantics (`_make_section_reducer` findings.py:196,
   `_sanitize_llm_patches` findings.py:369): bounded fragment+anchor patches, reject whole-doc-shaped
   patches, failed anchor → one retry or no-op.
4. **Structural scaffold** — keep existing `_STRUCTURAL_OPP_RE` + `_classify_structural_opportunity`
   (runner.py:747) detection; swap `_editor_structural_retry_prompt` (runner.py:1027) body for CFP;
   generated structure must pass mermaid/lint/grounding + the stage gate. CFP fails on gemma → renderer.
5. **Expansion stage** — consumes ONLY unused grounded findings (`_parse_findings` findings.py:421) into
   underdeveloped sections; Contraction never auto-fires, but local dedup/replace stays allowed under gate
   (else bloat).
6. **Coverage-as-matrix-dimension LAST**, only after B/A/C evidence (`rubric_scorecard_100` rubric.py:566,
   `resolve_scoring_matrix` rubric.py:497) — a later A/B, not bundled into the first fix.

**MVP harness (codex-checked):** candidates B0-B2 / A(CFP vs renderer) / C(expansion) / coverage-A/B are
right; real on-disk artifacts mandatory (synthetic docs too easy). Metrics to ADD beyond the obvious:
**anchor-failure rate, rejected-whole-doc-patch count, gate-rejection-reason histogram, section-level
coverage floor, and "diagram survived publish"** (Bug B can erase Bug A's output — end-to-end survival is
the real metric).

**Underweighted risks (codex):**
- **Patch-gate deadlock**: every candidate patch failing anchor/quality must terminate in no-op + telemetry,
  never spin.
- **Dedup vs expansion fight**: the cited-URL drop (findings.py:327) will STARVE the expansion stage —
  needs a section-underdeveloped exception.
- **Patch sanitizer is URL/additive-biased** (findings.py:394): publish fixes for lint/placeholder/diagram
  need non-URL structural patch TYPES — extend the contract by patch type, do NOT blindly reuse the
  validator unchanged.
- **Fail-open is dangerous outside epochs**: `make_preference` ACCEPTS on judge error (epoch_gate.py:48) —
  correct for epochs (never strand work), wrong for postrun publish/structural mutation, where a gate error
  must **no-op, not accept**.

**This §9 + §7 constraint (reuse, don't fork) + §8 patterns = the authoritative implementation spec.**

---

## 10. STRUCTURAL-CONTENT / PRESENTATION DESIGN (Bug A generalization, 2026-07-04)

Live re-run (v4) PROVED Bug B fixed (19.8KB not 9.5KB, 10 URLs, 0.79). Bug A (diagram) still 0 —
CFP fires but the live retry routes through a TOOL-AUGMENTED editor and weak gemma can't insert a
mermaid via `patch_artifact`. Harness tested CFP as a bare completion (fidelity gap). Fix = A2 path,
generalized to a full per-section presentation decision. Owned by agent `diagram-fixer` (task #22).

**Core principle (user):** PER SECTION, pick the RIGHT PRESENTATION for each content chunk. Decision
by content shape — the unified ladder:
- PARAGRAPH — flowing argument/logic; few short items; patterns lists can't hold.
- BULLETED LIST — ≥3-4 PARALLEL items sharing a category, unordered.
- NUMBERED LIST — order/sequence/priority matters (steps, ranked).
- TABLE — multiple components need COMPARISON across attributes, OR a list past ~8-10 items.
- DIAGRAM — multiple components + RELATIONSHIPS / FLOW / STRUCTURE.

**Detection (per section, tiered):** deterministic pre-filter (keywords/relational verbs/≥3 named
components/numbered steps) → semantic classifier (reuse `_is_structural_opportunity` LLM-fallback
pattern, run over CONTENT) returning presentation-type. GATES: text-first threshold (≲5-6 items stays
prose), no-duplication (don't restate prose or an existing block; idempotent), highlight-main-findings.

**Generation (cheapest-tier-that's-correct):** DETERMINISTIC parse + render (list→mermaid / rows→md
table — code renders, weak model never writes syntax or issues a tool call); SEMANTIC embedding-cosine
grounding (BGE-M3, NOT brittle keyword — catches implicit meaning); code-inserts directly (no gemma
tool call); existing accept gate (score/opp/lint non-regression) as backstop; captions on every block.

**Cost/fidelity tiering (user principle):** deterministic WHERE it yields the same result (cheaper+
faster); embedding where meaning is needed but not full reasoning; LLM only if embedding insufficient.
Never trade correctness for cheapness (keyword grounding was rejected for this reason).

**Sequencing:** (1) DIAGRAM path — PROMPT-TEST FIRST on live gemma (prove it extracts a correct
grounded per-section component-list before wiring), then implement + live-validate a mermaid LANDS via
the production path; (2) TABLE path (same pattern, simpler render); (3) paragraph/list reformatting
layer (separate report-quality workstream, deferred). `studio/diagram_render.py` (deterministic render)

+ `tests/test_diagram_render.py` already built; the tool-call-bypass wiring + detection are pending the
prompt-test checkpoint.

**Best-practice sources baked in:** APA tables-figures; Turabian ch.11 (verbal-vs-visual, ≤5-6 items);
Canada CCDR (tables=precise/comparison, figures=trends/relationships/process); CSE Science Editor
(table data-ink); Google Tech Writing + Microsoft Style Guide + Cornell CHEC (list vs paragraph,
parallelism, 2-7 items); UK DfE + WSDOT (headings, accessibility, no spacing hacks). STORM/ScaffoldAgent
(per-section population + polish never free-rewrites).

---

## 11. DEPTH-STALL DIAGNOSIS — fundamentals verified (2026-07-05, probe + calibration)

**Question (user):** is the shallow report (Evidence synthesis 2.5/14.7, Analytical depth
1.8/10.5, byte-identical 1527→1531) a weak-model ceiling or application logic? Never
directly tested before — the strong-model reducer (P0-2b) was built on the untested
assumption. Verified today with two controlled probes.

### 11.1 What the "depth" rows actually are

Not an LLM judgment. Both rows map to ONE deterministic signal in `studio/rubric.py`:
`analysis = count(_ANALYSIS_MARKERS) / 6` — discourse-marker density ("however",
"in contrast", "this implies", "compared to", …). Run 1531's 1607-word report contained
**one marker** in total. Full credit needs six. This is a low bar, not a reasoning test.

### 11.2 Probe result — model ceiling is FALSE

`scratchpad/probe_synthesis.py`: run 1531's real artifact through the PRODUCTION
`_synthesize_analysis` path (same windowing, same guards, same directive) on the real
gemma client:

```
BEFORE  markers=1  analysis=0.17  Evidence synthesis 2.33/14  Analytical depth 1.67/10
AFTER   markers=5  analysis=0.83  Evidence synthesis 11.67/14 Analytical depth 8.33/10
```

Gemma synthesizes fine. 4/9 sections accepted; the 5 rejects are GUARD BUGS, not model
failures (probe_diag.log):

1. **invented-headings false positive** — gemma added example code; `#`-comments inside
   the fenced block match the heading regex (guard scans raw text, not code-masked).
   Also explains dying code fences. Fix: `mask_fenced_code` before heading extraction.
2. **URL set-compare unnormalized** — `lost=2 gained=2` pairs are the SAME URLs
   re-tokenized (markdown-link wrap, trailing punctuation). Fix: normalize
   (rstrip `.,);]`) both sides before compare. 4 of 5 rejects show this signature.

### 11.3 Off-topic floor calibration — set-overlap fundamentally broken

Calibrated against 40 real cached pages of the Pi/Craft task (.web_cache.json):
π-Wikipedia scores set-overlap **0.62 — above most genuine pages** (23K tokens hit
8/13 common requirement words incidentally). The 0.15 set-overlap floor shipped earlier
today could never work; live attempt 3 confirmed (pi_wiki=True, dict=True, offtopic=0).

Density (req-word-stem occurrences / page tokens) separates: junk 0.0019–0.0136,
genuine 0.0124–0.32 — one overlap point (dictionary/limitation 0.0136 vs a genuine nav
page 0.0124). Landed: density two-tier + LLM gray zone (drop <0.010, keep >0.030,
judge-client binary verdict between; fail-open). Same lesson as `studio/relevance.py`:
lexical metrics saturate; constrained binary classification doesn't.

### 11.4 Plan (ordered)

1. ~~Density+gray-zone topical floor on findings AND reducer LLM patches~~ (landed,
   tested — pending live verification).
2. Fix `_synthesize_block` guards: code-mask before invented-heading check; normalize
   URLs before set-compare. Re-run probe — expect ≥8/9 sections accepted, live analysis
   signal ≈0.8 without any model upgrade.
3. Keep P0-2b strong-model reducer (already landed with call-time degrade) as
   composition-quality lever — it was NOT the depth lever; the guards were.
4. Fresh cold run (attempt 4) measuring: depth rows move, no π/dictionary citations,
   code fence + mermaid present (compliance downgrades force the editor loop),
   filled dynamic sections, no escape-flood.
5. Session fixes already landed en route: depth-pass observability (`synth[...]`
   accept/reject + expand stats), code-shaped compliance downgrade (P2-8b), QUOTE
   escape-flood normalization, reducer judge-flake degrade.

**Meta-lesson (user rule, reaffirmed):** verify fundamentals BEFORE building on a
hypothesis — the depth stall was 100% app logic; the untested "weak model" assumption
nearly shipped a model upgrade as the fix. Calibrate every deterministic threshold
against real data before trusting it.
