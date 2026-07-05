# PLAN — codebase simplification: fewer layers, shared primitives, no feature loss

**Date:** 2026-07-05
**Trigger (user):** "we always patch original code — review the whole codebase, reduce
hierarchy/layers, improve efficiency, do not downgrade original features."
**Status:** PLANNED — audit done (evidence below); execution starts AFTER the attempt-4
cold run records (refactoring during a measurement run muddies both).

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

### S1 — `studio/textutil.py`: one home for text primitives (LOW RISK, do first)
Move + de-duplicate: URL_RE, `norm_url()` (single rstrip-punctuation rule),
`extract_urls()`, `content_word_stems()`, `mask_fenced_code` (import from rubric or move
here), `_dbg()` (env-gated diag logger), mermaid-block regex, fence pair-parsing
(`_has_code_fence` logic). Callers import; behavior identical (unit tests pin each
primitive once instead of per-copy).
**Kills:** the entire "fix must land in N places" bug class hit twice today.

### S2 — finalize pipeline as an ordered pass list (MEDIUM RISK, biggest layer cut)
The epoch-end sequence in runner.py (synthesize → readability → repair_lints →
grounded-full archive → expand → publish gate → editor retry → presentation →
references rebuild → score/record) is ~1,500 lines of nested inline try-blocks. Extract
to `studio/finalize.py`: each pass = `(name, fn(state) -> state)` run by ONE wrapper
that owns fail-open, timing (`timing_sink`), and `_dbg` entry/exit. Order and skip
conditions preserved verbatim.
**Kills:** most of the 83 ad-hoc excepts; every future pass gets observability for free
(no more silent-dead passes — the editor-pass and depth-pass blindness were both this).

### S3 — guard primitives (`studio/guards.py`) (MEDIUM RISK)
`urls_preserved(before, after)`, `length_ratio_ok`, `no_invented_headings(masked)`,
`topical_verdict(url, req, judge)` — synthesis guards, `_sanitize_llm_patches`, and
expand guards COMPOSE these instead of re-implementing. Per-guard unit tests move to
one file.

### S4 — delete/move stale scripts — RESOLVED AS NO-OP (2026-07-05)
Verified: all probe/demo scripts live under `backend/tmp/` which is gitignored — the
REPO is already clean; the audit's Pyright sweep saw local disk, not tracked files.
Local probes are kept deliberately (L6 probe-before-wire values them). No action.

### S5 — measured efficiency wins (ONLY with numbers)
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

### L1 — Verify hardening (RC1) [after S1/S2 land]
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

### L2 — Dead-pass detector (RC2) — this IS S2's pass-list, plus one rule
Every pass emits ran/changed/reason via the uniform wrapper (S2). Add: postrun
diagnostics flag any pass with N consecutive no-op epochs on a task ("pass X inert 3
epochs — investigate or expected?"). Silence is no longer indistinguishable from health.

### L3 — Lineage immune system (RC3)
Deterministic seed-eligibility gate in `latest_with_content`: a row is seed-eligible
only if its artifact passes lint + has ≥1 citation + score not >50% below lineage
median. Ineligible rows stay recorded (history) but are skipped for seeding — no more
manual backup-and-delete surgery. Plus: goal/constraints NEVER rotate task identity
(already fixed) — add a regression test if missing.

### L4 — Repeat-weakness escalation (RC4)
When the SAME weakness row survives K=3 epochs byte-identical, stop retrying the same
lever: escalate deterministically — (1) route to the structural editor with the
weakness named, (2) relax the specific execute-contract cap for that section for one
epoch (e.g. allow multi-sentence weave from under-used sources), (3) if still stuck,
mark "app-limit reached" in the run report and STOP burning epochs on that row. The
loop learns to notice its own walls instead of spinning at them.

### L5 — Acceptance economics (RC5)
Per-run postrun block: per-pass attempted/accepted counts + tokens spent per accepted
change (data already flows through timing_sink/_dbg; aggregate it). Feeds S5: passes
with chronic ~0% acceptance are candidates for removal or redesign — measured, not
guessed.

### L6 — Probe-before-wire (process rule, institutionalize today's lesson)
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
