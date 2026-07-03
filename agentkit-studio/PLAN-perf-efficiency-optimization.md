# PLAN — Whole-Process Efficiency & Performance Optimization (v2, post codex review)

**Date:** 2026-07-03
**Scope:** end-to-end run latency + compute cost of the document-generation pipeline
(`backend/studio/runner.py` + collaborators + `agent-prep/shared/web_toolkit`).
**Hard constraint:** zero regression on output quality (judge score, citation grounding,
publish readiness, hill-climb carry-forward lineage) and zero functional regression (all
existing tests green, SSE/GUI contract unchanged).

**v2 changes after codex review (session `019f26fe`):** P0-A redesigned (WAL dropped — it broke
`_verified_urls_from_cache`; format-compatible debounced atomic rewrite instead), P0-B redesigned
(parallel pure fetches + single-thread cache insert, preserving the `_fetch_cache` single-writer
invariant), **P1-A dropped** (wrong premise: `rubric_scorecard_100` is deterministic, not an LLM
judge — `rubric.py:567`), **P1-B dropped** (post-run calls are sequentially dependent:
`score_result` → `_scorer_feedback` → `mine_weaknesses_from_outputs`, `runner.py:3577`), P2-B
rescoped (retry loop lives in agentkit's private `_resilient`; SSE-per-retry is an upstream
change, out of scope), verification protocol hardened (cold/forced-miss coverage, lineage parity,
score-metric disambiguation).

---

## 0. Objective & success metric

- **Objective:** reduce wall-clock time per run (single epoch, fixed task) and per full
  hill-climb (5 epochs), without moving quality metrics.
- **Primary metric:** end-to-end run duration (SSE `phase_start`→terminal `done`).
- **Quality-parity metrics (must not regress):**
  - **LLM judge score** (`score_result`, the only LLM score): mean over 3 A/B runs within
    judge-noise band — band measured empirically first (3× same-artifact scoring spread), not
    assumed.
  - **Deterministic score** (`rubric_scorecard_100`): byte-identical artifact ⇒ must be exactly
    equal (unit-level assertion, not an E2E band).
  - **Grounded-citation count** A ≥ B **plus** human spot-review of the artifact diff
    (count alone is gameable — citation walls).
  - `evaluate_publish_readiness` verdict unchanged.
  - **Hill-climb lineage parity:** after A/B, `task_runs` rows show the same version increment,
    and epoch N+1's `latest_with_content` seed resolves to epoch N's row.
    `_postrun_score_and_record` ordering is load-bearing (`runner.py:3384`) — untouched.
  - Full pytest suite green (`backend/tests`) **and** web_toolkit's own direct readers / lab
    replay verified in `agent-prep` (the cache module lives there).
- **Secondary metric:** total LLM tokens per run (from `UsageReport` accounting).

## 1. Baseline instrumentation (FIRST — every impact estimate below is a hypothesis until this lands)

- **T1. Per-stage wall-clock timers** in `runner.py`: plan, topology-assign, skeleton, per phase
  (split: spoke fan-out / reducer(a) / `_prefetch_cited` / scorecard), editor pass, publish
  revision, postrun. Emitted as a `timing` field on **existing** SSE `phase_done`/`done` events
  (additive field, no new event type — frontend reducer tolerance verified) + one summary line
  in the run log.
- **T2. Web-cache I/O counters** in `web_toolkit/_cache.py`: `_read()` call count, bytes parsed,
  cumulative time. Surfaced **in the run summary and on demand** (not at process exit — a GUI
  backend process doesn't exit between runs).
- T1 and T2 ship as **separate PRs** (different repos, independently revertable).
- Baseline: 3 warm + 1 forced-miss run on the benchmark task; record per-stage breakdown.

## 2. Optimizations (ranked: impact ÷ risk)

### P0-A. Web disk cache: load-once in-memory + debounced atomic rewrite (format-compatible)

**Evidence:** `_cache.py:33/55` — every `cache_lookup` parses the entire 13.6MB
`.web_cache.json`; every `cache_store` re-reads + rewrites it. Additionally
`runner._verified_urls_from_cache` (`runner.py:91`) parses the same file directly in the
scoring path, **failing open** on error. Hypothesis (validated by T2): 1–3+ min churn/run,
growing with cache size.

**Design (WAL rejected — codex correctly showed it silently breaks `_verified_urls_from_cache`
and any other direct reader, and re-implements storage semantics):**
- Module-level in-memory dict, loaded once per process under a `threading.Lock`.
- `cache_store`: update dict, mark dirty; **flush** = re-read disk, merge (disk ∪ ours, ours
  win on key conflict), write tmp file, `os.replace` (atomic). Debounced: every N stores
  (e.g. 25) or T seconds, plus an explicit `cache_flush()` at run end.
- **On-disk format unchanged** (single JSON object): every existing direct reader —
  `_verified_urls_from_cache`, agent-prep labs, `fetch_metrics` — keeps working; W3.7 replay
  compatibility preserved by construction.
- Cross-process: `fcntl.flock` around the read-merge-write flush prevents whole-file clobber
  between concurrent processes (merge-on-flush makes concurrent writers additive, not
  last-writer-wins). **Declared non-goal:** cross-process read-your-write freshness mid-run —
  a reader in another process sees new entries only after a flush (same as today, where it
  sees them only after the write; the window widens by ≤ the debounce interval).
- `_verified_urls_from_cache` gains a `cache_snapshot()` API path + a `cache_flush()` call
  before verification, closing the freshness window in the one quality-coupled reader **and**
  removing its own full-file parse. **Fail-open preserved:** the direct
  `.web_cache.json` parse remains as the fallback when the cache API import fails — the API
  path is an optimization layered on top, never a replacement; "cache module unavailable" must
  keep meaning "verification fails open", not "no verified URLs" (unit test pins this).
- **SQLite fallback:** if merge/locking complexity exceeds ~100 lines, switch to stdlib
  `sqlite3` with a one-time JSON import and a compatibility `cache_snapshot()` for direct
  readers — decided at implementation time, criterion recorded here.

**Tests:** unit — load-once, debounce, merge-on-flush (two writers, no loss), atomic replace,
corrupt-file recovery (`except → {}` semantics preserved), `cache_snapshot` freshness after
flush; integration — forced-miss run stores land on disk; agent-prep lab replay identical.

### P0-B. Parallelize `_prefetch_cited` fetches — without touching the single-writer cache invariant

**Evidence:** `findings.py:100` — serial `prefetch_url` over ≤24 URLs, every phase; each miss
is a 1–5s network fetch.

**Design (contextvars-copy rejected — codex correctly showed one copied Context can't be
entered by multiple threads, and `_fetch_cache` is designed single-writer, `tools.py:80`):**
- Split `prefetch_url` into fetch and store **by extracting its existing body**, not by
  reimplementing: a shared `_fetch_page(url) -> (key, page|None)` carries the exact current
  semantics (URL normalization/strip, `f"{u}|"` key format, ok/empty-content handling,
  truncation to `_MAX_FETCH_CHARS`, exception behavior); worker threads call only
  `_fetch_page` (no `_fetch_cache` access); the reducer thread then inserts results into ITS
  `_fetch_cache` serially. `prefetch_url` itself becomes `_fetch_page` + store, so serial and
  parallel paths share one implementation by construction. Single-writer invariant holds;
  grounding gate (`_parse_findings`, `findings.py:395`) sees exactly the same cache state as
  the serial version.
- URL list already deduped (`_cited_urls` seen-check) ⇒ no same-key duplicate fetches within
  the batch. `ThreadPoolExecutor(max_workers=8)`, preserve returned-count semantics.
- Honest risk note (not "none"): grounding correctness depends on the insert step running
  before `_parse_findings`; enforced by code order + a regression test (spoke-cited URL
  grounds after parallel prefetch; assert `_fetch_cache` contents identical serial vs
  parallel for a fixed draft set).

### P1. `_verified_urls_from_cache` parse elimination

Folded into P0-A (`cache_snapshot()`): the scoring-path 13.6MB parse (called per neutralize /
verification site) disappears. Listed separately so its win is measured separately in T1.

### P2-A. Web-cache size governance

Opt-in, operator-invoked `cache-gc` (env `WEB_CACHE_MAX_MB` + timestamp-per-entry going
forward). **Default OFF** — eviction changes replay behavior; documented tradeoff. Keeps P0-A
wins durable (load-once cost and memory otherwise grow forever).

### P2-B. Backend-down visibility (rescoped)

Original per-retry SSE idea rejected as scoped: retries live in agentkit's private
`_resilient()` with no callback hook (`client.py:115`) — changing that is an upstream agentkit
change, tracked separately. In-scope now: **session-start preflight ping** per resolved LLM
profile (1 tiny completion, fail → immediate **warning** status event) so a down backend (the
VibeProxy incident) surfaces in seconds instead of a silent multi-minute retry tail.
**Warn-only, never abort:** a transient one-call ping failure that the existing 7-retry path
would survive must not kill the session — the run proceeds regardless; the ping only informs.
No retry-behavior change ⇒ no quality risk. New event reuses the existing `ErrorEvent`/status shape — no SSE
contract change.

### Dropped after review (kept for the record)

- **P1-A scorecard memoization** — false premise: `rubric_scorecard_100` is deterministic local
  scoring (`rubric.py:567`), explicitly designed to never call an LLM. Negligible cost.
- **P1-B post-run parallelization** — false premise: `score_result` output feeds
  `mine_weaknesses_from_outputs` (`runner.py:3577`); embedding dedup needs `_weaknesses`.
  Sequential by design.

### Deliberately NOT in scope (quality risk too high)

- Cross-phase speculation / phase overlap (writeback-ratchet ordering semantics).
- Raising `_max_workers` above 4 (contention on local oMLX/SearXNG changes result pools).
- Skipping reducer refine echo or editor rounds (direct quality mechanisms).

## 3. Non-regression verification protocol (every optimization, before merge)

1. Full pytest suite (`backend/tests`) green + new unit tests per item above.
2. **web_toolkit changes additionally verified in agent-prep**: its unit tests + one lab replay
   (`WEB_CACHE=1`) produces identical pools.
3. **A/B benchmark — warm AND cold:** same fixed requirement, same seed state (copy
   `task_runs.db` + workspace). (a) 3 warm runs (`WEB_CACHE=1`) per side; (b) 1 forced-miss run
   per side (fresh `WEB_CACHE_PATH`) — the risky paths (fetch concurrency, flush/merge,
   recovery) only execute on misses; warm replay alone proves nothing about them.
4. Compare per §0: duration ↓, judge score within measured noise band, deterministic score
   unit-equal on identical artifacts, grounded citations ≥ + spot review, publish verdict =,
   lineage parity (seed row resolution for the next epoch).
5. One live-GUI E2E (browser EventSource — no script-side early disconnect) confirming SSE
   events, DB row recorded, evidence/io dirs populated.
6. One optimization per PR; instrumentation PRs separate per repo.

## 4. Sequencing

1. T1 (studio) + T2 (web_toolkit) instrumentation → baseline numbers.
2. P0-A cache rework (incl. `cache_snapshot` + agent-prep verification) — the P1 win rides along.
3. P0-B parallel prefetch.
4. Re-measure; publish before/after per-stage table.
5. P2-A, P2-B as hygiene follow-ups.
6. §3 protocol gates every merge.

## 5. Expected impact — hypotheses pending T1/T2 measurement

- P0-A + P1: eliminates the per-operation 13.6MB parse/rewrite churn (magnitude = T2's number);
  fixes in-process concurrent-store loss; narrows (not widens) the cross-process story via
  merge-on-flush.
- P0-B: prefetch segment ≈ max(fetch) instead of sum(fetch) per phase, up to 8× on misses.
- P2-B: backend-down failure surfaces in seconds (was: minutes of silent retry). Measured
  separately from latency wins (visibility fix, not a speed fix).

---

## 6. Not-started backlog reconciliation (from PLAN-research-report-skill-package-improvements.md + CURRENT task list)

Pending work re-scoped against **current** code (2026-07-03) and sequenced with this perf plan.
Verification greps run against `backend/studio/` — statuses below are code-checked, not
doc-trusted.

### 6.1 Status corrections (docs stale vs code)

- **O7 (final publish-ready assembler gate): LARGELY SUPERSEDED.** `evaluate_publish_readiness`
  (`report_quality.py:241`, 16K module) + the publish-revision pass landed via the concurrent
  relevance-refinement feature. Remaining work is a **delta audit only**: diff O7's rule list
  (dup headings, placeholder refs, unbalanced fences, malformed links, `(unverified)` links,
  required headings, citation retention) against the landed gate; implement only missing rules
  inside the existing gate. Do NOT build a parallel `finalize_report_quality` — two gates =
  drift.
- **Entry 166 "LOGGED, NOT FIXED" in the task list: STALE.** `_persist_partial_run`
  (`runner.py:4290`, arch doc §5c) landed. Task-list line should be moved to Completed.
- **Workstream D: profile routing half already exists** (`report_profiles.py`, 12K — maps
  `report_type` → template/scoring). Only the `ResearchConfig` intake object + frontend
  controls remain.
- **Workstream J: NOT greenfield.** `local_catalog/` is a data directory (`loops/`, `skills/`),
  and catalog surfaces already exist: `CatalogClient` (`app.py:67-80`), `/loops` + seed routes
  (`app.py:150+`), `/catalog/templates/*`. Re-scoped: J = **missing loop/skill CRUD +
  enable/disable + import/export + frontend Find/Loops/Skills/Sources tabs layered over the
  existing catalog/template/loop surfaces** — extend `CatalogClient`, don't build a parallel
  catalog stack.

### 6.2 Optimized backlog (with perf-plan interactions)

**O3 — JSONL finding contract for weak models.** Still valid; now perf-coupled:
- Reuses the same grounding oracle as `_parse_findings` (`findings.py:395`) → sequence **after
  P0-B**, whose regression tests pin the grounding path; `_parse_findings_jsonl` then inherits
  a tested `_prefetch_cited`/`_fetch_page` substrate instead of racing a moving one.
- **`_cited_urls` (`findings.py:71`) does NOT harvest raw JSONL today** — it matches `URL:`
  lines and *fenced* JSON blocks only; a bare `{"url": …}` line is invisible to it, so
  JSONL-only findings would never be prefetched and would die at the grounding gate. O3 must
  extend `_cited_urls` with line-level JSON parsing (reusing `_parse_findings_jsonl`'s line
  parser — one implementation), keeping a single URL-extraction path. Acceptance test:
  a JSONL-only finding's URL is prefetched by `_prefetch_cited` and survives `_parse_findings`.
- Perf upside (measure via T1): stricter contract ⇒ fewer dropped/malformed findings ⇒ fewer
  neutralize/repair cycles and wasted spoke tokens on weak models (memory: fenced-JSON parse
  gap already caused fetched=0 refusal-prose runs once).

**O5 — deterministic section assembler (weak-model mode).** Still valid; the **biggest
token/latency win in the backlog** (replaces LLM reduce with deterministic code in weak-model
mode) and the biggest quality risk. Conditions:
- Flag-gated per model profile (`model_profiles.py`), default OFF; strong-model path untouched.
- Ships only under §3's full A/B protocol (judge score band, citation parity, spot review) with
  a weak-model benchmark task added.
- After T1 baseline exists, so the reducer-stage saving is a measured number, not a claim.

**D — `ResearchConfig` intake.** Still valid; one hard perf/quality guard added:
- **Lineage guard (blocking design rule):** `ResearchConfig` must be excluded from
  `task_hash` base identity (`base_identity(_base_requirement)`, `runner.py:1646`), exactly as
  goal/template already are — otherwise every intake tweak forks the lineage → cold start →
  repeated re-research (the known task-hash rotation trap). Acceptance test: same topic ±
  config ⇒ same `task_hash`, seed carry-forward resolves.
- Brief injection into `_plan_from_epics`/`_build_skeleton`/`_build_executor_prompt` capped
  (~500 tokens) — it multiplies across every spoke call per phase per epoch.
- Empty config ⇒ byte-identical current behavior (existing acceptance test kept).

**J — catalog CRUD + frontend tabs.** Orthogonal to perf plan (no cache/prefetch/LLM-path
coupling). Build on the existing `local_catalog/` module. Sequenced last; no changes to its
original spec beyond that starting point.

**Deferred items:** unchanged, with one link — "SQLite indexing for observations" shares the
decision criterion recorded in P0-A's SQLite fallback: if the cache flips to sqlite3, adopt the
same pattern for observations rather than inventing a second storage approach.

### 6.3 Combined sequencing

1. T1+T2 instrumentation → baseline (perf §1)
2. P0-A cache rework (+P1 ride-along)
3. P0-B parallel prefetch (grounding tests pinned)
4. Re-measure; publish per-stage table
5. O7 delta audit (small, closes the workstream)
6. O3 JSONL contract (on the pinned grounding substrate)
7. O5 assembler, flag-gated weak-model only, full A/B
8. D ResearchConfig (with lineage guard)
9. P2-A / P2-B hygiene
10. J catalog (greenfield, independent)

Task-list housekeeping alongside: mark entry-166 line Completed; annotate O7 as superseded-with-delta.

## 7. Review status — codex cross-model review (session `019f26fe`)

- **Round 1:** 20+ findings; 4 accepted as plan-killing (WAL design, P1-A false premise,
  P1-B false premise, contextvars-copy hazard), rest folded into §0/§1/§3.
- **Round 2:** 3 blocking objections (fail-open fallback, `_fetch_page` extraction-not-rewrite,
  warn-only preflight) — all amended in.
- **Round 3: AGREED** (perf plan §0-§5). Non-blocking nits, adopted:
  1. Fallback test asserts *same behavior as the current direct parse*, not merely "fails open".
  2. `_fetch_page` stays private and tiny — no abstraction beyond sharing serial/parallel behavior.
  3. P2-B measured separately from latency wins.
- **Round 4 (§6 backlog reconciliation):** 2 blockers, both code-verified and amended:
  `_cited_urls` doesn't harvest raw JSONL (O3 must extend it, single line-parser
  implementation); Workstream J is not greenfield (`CatalogClient`/`/loops`/
  `/catalog/templates/*` exist; `local_catalog/` is a data dir) — re-scoped to extend existing
  surfaces.
- **Round 5: AGREED** (§6). Non-blocking nits, adopted:
  1. O3's JSONL line parser = one tiny helper shared by finding-parsing and URL harvest.
  2. J leaves template-catalog endpoints untouched unless the frontend needs links.
  3. J smoke test: disabling a local loop hides it from `/loops` without deleting its JSON.
