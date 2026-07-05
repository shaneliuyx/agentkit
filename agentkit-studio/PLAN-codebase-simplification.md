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

### S4 — delete/move stale scripts (TRIVIAL)
`e2e_demo_fix*.py`, `calib_*.py`, `a2_live_validate.py`, `editor_*_check.py` → delete
(git history keeps them). Anything still referenced by docs → `backend/probes/`.

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
