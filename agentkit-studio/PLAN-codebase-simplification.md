# PLAN — codebase simplification: fewer layers, shared primitives, no feature loss

**Date:** 2026-07-05
**Trigger (user):** "we always patch original code — review the whole codebase, reduce
hierarchy/layers, improve efficiency, do not downgrade original features."
**Status:** PLANNED — audit done (evidence below); execution starts AFTER the attempt-4
cold run records (refactoring during a measurement run muddies both).

## STATUS BY CHAPTER (updated 2026-07-05 late)

| Ch. | Topic | Status |
|-----|-------|--------|
| 1 | Audit evidence | ✅ done — drove S1–S5 |
| 2 | S-workstreams | S1 ✅ · S2 ✅ · S3 ⬜ (after attempt 9) · S4 ✅ no-op · S5 ◐ |
| 3 | Verification protocol | ✅ in force — suite 836→890, reviewer pass per slice, live E2E per behavior change |
| 4, 7 | Sequencing (old) | superseded by §12 |
| 5 | Loop-health diagnosis RC1–5 | ✅ complete — every RC now has live evidence + a workstream |
| 6 | L-workstreams | L1 ⬜ (slice 6) · L2 ✅ · L3 ✅ · L4 ⬜ · L5 ◐ · L6 ✅ adopted |
| 8 | L0 structural producer | ✅ committed — code path verified live (v3 0.657); diagram veto attributed, prose fix in review round |
| 9 | Writer reference process P1–P4 | ⬜ — P1 bundle is next major slice (5); prerequisite covers-X extraction ✅ live |
| 10 | Editorial gate E1–E11 | ◐ ~5/11 rows covered (E1/E4/E6/E11 ✅, E3/E5 partial); L1 unifier not started |
| 11 | Design workstreams D1–D5 | ⬜ — D-B's fractal-critic principle partially delivered via S2 ledger + gate logging |
| 12 | Unified execution order | ACTIVE tracker — slices 1–4 committed; current work = §14 slate |
| 13 | Execution log | living log — one row per committed fix with measured result |
| 14 | Attempt-8 results + attempt-9 slate | item 1 instrumented (trace armed) · items 2–4 built, reviewer MEDIUMs in fix round · items 5–8 queued |

## 0. Objective & success metric

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

## 1. Audit evidence (2026-07-05)

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

## 2. Workstreams (priority order, each independently shippable + test-gated)

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

### S3 — guard primitives (`studio/guards.py`) (MEDIUM RISK) — ⬜ PENDING (next after attempt 8)
`urls_preserved(before, after)`, `length_ratio_ok`, `no_invented_headings(masked)`,
`topical_verdict(url, req, judge)` — synthesis guards, `_sanitize_llm_patches`, and
expand guards COMPOSE these instead of re-implementing. Per-guard unit tests move to
one file.

### S4 — delete/move stale scripts — RESOLVED AS NO-OP (2026-07-05)
Verified: all probe/demo scripts live under `backend/tmp/` which is gitignored — the
REPO is already clean; the audit's Pyright sweep saw local disk, not tracked files.
Local probes are kept deliberately (L6 probe-before-wire values them). No action.

### S5 — measured efficiency wins (ONLY with numbers) — ◐ PARTIAL (offtopic-verdict memoization DONE; rest pending timing numbers)
- Memoize `rubric_score`/`score_breakdown` per text-hash within a run (called from
  editor recount loops many times per epoch on identical text). Measure call count via
  diag before/after.
- `_page_for_url` / `_url_in_cache` scan the whole cache per lookup — build one
  normalized-key index per reduce call.
- NOT speculative async/caching beyond these two; anything else needs a timing number
  first (T1 stage timings are the instrument).

### Explicit non-goals
- No behavior changes, no guard-threshold changes, no prompt changes.
- No renaming of public event shapes, DB schema, or SSE frames.
- No new abstractions beyond the three named modules (YAGNI).

## 3. Verification protocol (per step)

1. Full backend suite (baseline 826 passed) — must stay green.
2. `uvx ruff check` on touched files (F821 catches extraction-scope NameErrors — known
   gotcha).
3. After S2 (the risky one): one cold E2E on the Pi/Craft task; compare score vs
   attempt-4 baseline, diff the diag18 pass sequence (same passes, same order).
4. Codex adversarial review of the whole refactor branch before push (standing
   workflow).

## 4. Sequencing

1. Wait for attempt-4 cold run to record (baseline + today's fixes verified live).
2. S4 (trivial) + S1 (shared primitives) → suite → commit each.
3. S2 finalize pipeline → suite + cold E2E compare → commit.
4. S3 guards → suite → commit.
5. S5 with before/after timing numbers → commit.
6. Codex review, then user decides on push (~45 commits ahead by then).

---

## 5. LOOP-HEALTH ANALYSIS — why self-improvement underdelivers (2026-07-05, user + article)

**Reference frame:** the loop canon — DISCOVER→PLAN→EXECUTE→VERIFY→ITERATE, where
VERIFY is the heart, STATE is the memory, stop-conditions are the sanity, and
writer/reviewer separation is most of the quality. Measured against it, the studio
over-built EXECUTE/ITERATE and under-built VERIFY/STATE.

### 5.1 Root causes (ranked, all live-evidenced)

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

### 5.2 Expectation calibration (the four-condition test)

The article's条件 4 — "done is objective" — is only PARTIALLY true for research
reports: fences/citations/sections are objective (and now gated); prose depth is
judged by proxies. So "fully autonomous self-improvement to excellence" will
asymptote by design. The honest target: **converge to high-0.8s with zero corruption,
zero silent stalls, and every stall explained in the run report.**

## 6. LOOP-HEALTH WORKSTREAMS (combine with S1–S5; L2 = S2)

### L1 — Verify hardening (RC1) [after S1/S2 land] — ⬜ PENDING (spec now = §10.2 editorial checklist + crashed-status rule)
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

### L4 — Repeat-weakness escalation (RC4) — ⬜ PENDING (§12 slice 9)
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

## 7. Combined sequencing (supersedes §4)

1. Attempt-4 cold run records (baseline for everything).
2. S4 stale scripts → S1 textutil (shared primitives).
3. S2 finalize pass-list == L2 dead-pass detector (one workstream).
4. L3 lineage immune system (small, high leverage, isolated in task_runs.py).
5. L1 verify hardening (scorer on judge model + never-record-on-unverified).
6. S3 guard primitives, then L4 repeat-weakness escalation (builds on S3).
7. L5 economics in postrun + S5 measured efficiency wins.
8. Codex adversarial review of the whole branch; user decides push.

Each step: full suite green + probe where applicable; after 3 and 5: cold E2E compare.

---

## 8. STRUCTURAL-CONTENT ROOT CAUSE — why diagrams/tables/code never ship (2026-07-05)

**User observation (correct):** many rounds of diagram/table testing, essentially zero
structural content in any final artifact.

### 8.1 The architectural fault (three compounding decisions)

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

### 8.2 Fix — L0: requirement-driven deterministic structural producer (NEW TOP ITEM) — ✅ DONE (committed; code path verified live run 1534; diagram path unblocked by fence-lint repair, verify attempt 8)

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

## 9. REFERENCE-PROCESS GAP ANALYSIS — plan like a researcher, not a section-filler (2026-07-05)

Method (user-directed): write down how a competent human researcher would execute the
same task end-to-end, then diff that reference process against what the app actually
does. Not limited to current frameworks. Trigger: run 1534 covered Pi only — the task
says "study how to use Pi AND Craft" and all ~25 worker queries were Pi-only, zero
Craft sources fetched, report silently one-sided.

### 9.1 The reference process (how a human does it)

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

### 9.2 Gap table → ingestion workstreams P1–P4

| Reference step | Studio today | Workstream |
|---|---|---|
| Coverage ledger per subject | Nothing tracks subject→queries→sources→citations; hill-climb carry-forward LOCKS IN a one-sided artifact (workers extend the existing doc, queries follow it) | **P1 subject ledger** |
| Question-first planning | Section-first; spokes are goal-blind section fillers; no unit of work equals "understand Craft" | **P2 question-first hub planning** |
| Disambiguation probe | None; researches whatever the first queries happen to return | **P3 subject disambiguation** |
| Negative-result honesty | Silent omission (fabrication now blocked by guards, but silence remains) | **P4 explicit not-found contract** |

**P1 — subject coverage ledger (highest leverage, cheapest; do first).**
Deterministic dict keyed on extracted subjects (extraction item-5 rule supplies
"covers <subject>" rows): {subject: queries_issued, sources_fetched, cited_in_artifact}.
Populated from the existing web-tool call log + fetched-sources.json + citation set.
Wire-ups: (a) subject with 0 sources → injected worker/reducer guidance naming the
gap ("subject Craft has zero sources — issue direct searches"); (b) compliance row
"covers X" NOT_SATISFIED feeds existing _structural_opps path (no new machinery);
(c) verify stage records per-subject coverage into the run record for lineage.
Breaks the carry-forward lock-in: v(n+1) sees the ledger, not just the one-sided text.

**P2 — question-first planning (deep fix, prompt-level, medium risk).** ⬜ PENDING (§12 slice 12)
Hub planning emits research questions + per-question search plans BEFORE section
assignment; spokes own questions, reducer owns section placement. Sections stop being
the unit of research.

**P3 — subject disambiguation probe (small, rides on P1).** ⬜ PENDING (§12 slice 5)
Per subject: one cheap discovery search + one LLM call "which interpretation of
<subject> does this task mean? state it in one line" → recorded into Scope section +
ledger. Kills the silent wrong-subject/no-subject failure mode.

**P4 — explicit not-found contract (small, rides on P1).** ⬜ PENDING (§12 slice 5)
Reducer/finalize contract: a subject whose ledger row is empty after the run gets an
auto-placed Limitations entry ("No public sources found for <subject> under
interpretation <I>; coverage is limited to <other subjects>"). Honest asymmetry beats
silent asymmetry — and the LLM judge scores it as legitimate reflection, not a gap.

### 9.3 Sequencing amendment (extends §7)

P1 slots immediately after S2 (finalize pass-list), BEFORE L3 — coverage failure is
user-visible, lineage immunity is internal. P3/P4 ride in the same slice as P1 (share
the ledger). P2 is its own later slice (after L1 verify hardening, since question-first
planning changes what verify must check). Extraction item 5 ("covers <subject>" rows)
is the prerequisite for P1 and is already in flight in the current fix batch.

## 10. UNIFIED PIPELINE + EDITORIAL GATE — publish-standard spec (2026-07-05, user-directed) — ◐ ~5/11 E-rows covered by existing+today's machinery (E1/E4/E6/E11 ✅, E3/E5 partial); unifying editorial pass = L1, §12 slice 6, not started

§9 described the WRITER's reference process for one task. This section (a) generalizes
it into the unified pipeline every research-report task should run, and (b) adds the
missing second role: the REVIEWER/EDITOR responsible for publication, with a baseline
checklist and a verdict router. The editor spec is the concrete implementation spec
for L1 (verify hardening) — L1 should implement §10.2/§10.3, not invent its own.

### 10.1 Unified writer pipeline (generalizes §9.1 to every research report)

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

### 10.2 Editor's baseline checklist (publish standard)

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

### 10.3 Verifying dynamically generated sections

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

### 10.4 Verdict router (maps editorial outcome onto existing loop machinery)

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

### 10.5 Fit into sequencing

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

## 11. FIRST-PRINCIPLES REASONING TRACE — design-level defects (2026-07-05, user-directed) — ⬜ D1–D5 pending (§12 slices 5/10/11/14); D-B's fractal-critic principle already delivered at finalize level via S2 ledger + verdict/gate logging

§9/§10 mapped the pipeline at STAGE level. This section traces the reasoning INSIDE
each stage as a competent human actually performs it, then names the design-level
gaps. Explicitly not limited by current code/design — several findings require
architecture changes, not patches.

### 11.1 The reasoning trace (what actually happens in my head, step by step)

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

### 11.2 Design-level defects this exposes (ranked by depth)

| # | Defect | Human behavior | App behavior (by design, not by bug) |
|---|--------|----------------|--------------------------------------|
| D-A | **No uncertainty representation** | Belief state + confidence drives next action | Text in, text out; acts confidently on unexamined assumptions — root ancestor of the wrong-subject, one-sided-coverage, and fabrication failures |
| D-B | **No inner critic per step (verification is terminal, not fractal)** | Search contains diagnose→reformulate; reading contains conflict detection; writing contains does-this-follow | ONE verify at loop end; a bad search result is never diagnosed, it just yields bad fetches |
| D-C | **No claims layer between sources and draft** | pages → claims-with-provenance → outline → text | evidence pages → section text directly; nothing to measure coverage/conflicts/summary-consistency AGAINST (findings.py is embryonically this, but used as patch material, not as the substrate the report compiles from) |
| D-D | **No write-order DAG** | Evidence→Findings→Implications→Summary; summary written last from the final body | All sections drafted in parallel; exec summaries overclaim because they are written blind (E8 in §10 DETECTS this; write-order PREVENTS it) |
| D-E | **No clarification/assumption channel** | Irreducible ambiguity → ask user, or record visible assumption | Fire-and-forget; ambiguity resolved silently by whatever the first search returns |
| D-F | **Artifacts treated as slot-filling, not understanding-compression** | Can't draw the diagram → research more | Can't draw the diagram → L0 bolts one on (right pragmatic patch; wrong to stop there — the upstream signal "system never understood the architecture" is discarded) |
| D-G | **Budget by fixed caps, not information gain** | Stop when marginal gain drops; reallocate to the weak subject | Fixed fetch caps, uniform effort per section regardless of where understanding is thin |

### 11.3 Ingestion — new design workstreams D1–D5

**D1 — claims-with-provenance knowledge layer** (fixes D-C, enables half of §10):
elevate findings (claim, quote, URL, confidence, subject-tag) from transient patch
material to the persistent substrate: workspace `claims.jsonl`, grow-only, deduped.
Draft/synthesis prompts CITE claim IDs; coverage (E3), conflicts, exec-summary
consistency (E8) become queries over the claims table instead of LLM whole-text
judgments. Biggest single design change; most downstream payoff.

**D2 — search-step critic with reformulation loop** (fixes D-B for the research
stage, extends P1): after each search, one cheap judgment — "do these results
answer the question? if not, why, and what query next?" — with 2–3 bounded
reformulations. Failure diagnosis, not just failure detection. Rides on P1's ledger
(the 0-source subject triggers it).

**D3 — assumption/clarification channel** (fixes D-E, small): unresolvable
disambiguation → recorded assumption, injected into Scope ("interpreting Craft as
X"), surfaced in GUI/run record. Optional interactive mode: pause-and-ask when the
user is present. Cheapest item here, disproportionate payoff.

**D4 — write-order DAG** (fixes D-D, medium): Evidence-bearing sections draft first;
Findings synthesize from claims (D1); Exec Summary generated LAST from the final
body in the finalize phase (it is a deterministic-order pass, like L0). Kills the
overclaiming-summary class by construction; E8 remains as the detector.

**D5 — belief/uncertainty state** (fixes D-A/D-F/D-G, north star): per-subject and
per-question confidence in the ledger; next-epoch budget allocated to lowest
confidence; a failed structural producer RAISES a research question instead of only
inserting content. Hardest; do last; D1–D4 are its prerequisites.

### 11.4 Relation to §9/§10

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

## 12. UNIFIED EXECUTION ORDER (2026-07-05 late — supersedes §7, §9.3, §10.5-seq, §11.4-seq)

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
| 7 | S3 | guard primitives module | |
| 8 | E7 | dyn-section birth certificate + requirement-conditioned check | |
| 9 | L4 | repeat-weakness escalation (uses S2 pass ledger + L1 row evidence) | |
| 10 | D1 | claims-with-provenance layer | major slice |
| 11 | D4 + E8/E9 | write-order DAG (summary-last) + consistency judges | D4 prevents, E8 detects |
| 12 | P2 | question-first planning | after L1 changes what verify checks |
| 13 | L5+S5 | economics: verdict memoization (done), rubric memo, cache index, per-run cost line | needs timing numbers |
| 14 | D5 | belief/uncertainty state | north star, last |

Standing rules unchanged: no hardcoding (test-enforced), no behavior change inside
refactor slices, subagent build + separate reviewer, codex adversarial review before
any push, kill/fix/retest on live defects.

## 13. EXECUTION LOG — what landed, and the measured result of each fix (2026-07-05)

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

**Lineage scoreboard** (task 492bae60177b, honest cold chain): v1 0.145 → v2 0.304 (guards+salvage) → v3 0.657 (L0 code fence) → v4 0.433 (attempt 7 — no crash, fence survived, zero junk, but ZERO web searches issued: run rode inherited evidence, so the covers-Craft loop was never exercised; score dip is one epoch of synthesis rejects on a seeded run). **Attempt 8 (after L3 commit + restart onto S2 code) is the decisive verification**: S2 pass-sequence E2E diff + visible covers-Craft verdicts + L0 diagram veto reason, all in one run.


## 14. ATTEMPT-8 RESULTS (run recorded v5 0.41, 2026-07-05 late) — new top suspect

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

### Attempt-9 fix slate (ordered)
1. **rebuild_references erasure** — ◐ IN PROGRESS: instrumentation committed (per-pass fences= trace); offline repro EXONERATED write_section_workspace (fresh + live-copy both preserve); next run pinpoints. Original suspect statement kept below for the record (BLOCKER for
   all structural content; everything else is moot while inserted blocks get erased).
2. Diagram insertion carries explanatory prose (kills the 6→7 lint veto). — ◐ BUILDING (l3-builder)
3. repair_lints logs lint names when it cannot act. — ◐ BUILDING (l3-builder)
4. covers-X deterministic downgrade gate (mention-vs-substance). — ◐ BUILDING (l3-builder; run-8 evidence: judge verdict wobbled SATISFIED→NOT on identical mention-only content)
5. Writeback-normalize fence repair (mid-run cleanliness; formatter-after-repairs
   invariant pinned by test — mdformat/Flowmark/PyMarkdown all launder broken fences,
   evidenced 2026-07-05; Flowmark and PyMarkdown evaluated and rejected).
6. P1-core: subject ledger + unmet-subject search directive into worker/tool-loop
   prompts (Craft searches need an ACTOR, not another verdict).
7. Compliance-verdict memoization per text-hash (17 wasted rounds).
8. AST swallowed-content lint via markdown-it-py (already a transitive dep) — catches
   the giant-fence-swallows-headings class generically.

Lineage: 0.145 → 0.304 → 0.657 → 0.433 → 0.41 (decline driven by the erasure bug +
uncovered subject; L3 gate keeps v5 seed-eligible — median floor not tripped).