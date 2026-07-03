# HANDOFF — structural retry shipped, LLM classifier shipped, live e2e validation in progress

**Date:** 2026-07-03 (continuation of `HANDOFF-requirement-compliance-diagram-reliability.md`)
**Status:** All planned code for this session is DONE and tested (636 backend tests, 0
regressions). One e2e validation run is LIVE in the background, not yet reached its result.
Session cost was very high — resume by checking the live run's outcome FIRST, don't re-launch
work blindly. Read WORKLOG entries 174-175 for full technical detail.

---

## What shipped this session (all in `studio/runner.py` + `studio/task_runs.py`, uncommitted)

1. **Entry 174 — bounded structural retry.** `_editor_structural_retry`: up to 3 candidate
   attempts per structural quality-opportunity, each from the SAME pre-attempt snapshot,
   accepts the first non-regressing candidate that strictly reduces the opportunity count.
   Codex-reviewed; one real finding (`opportunity_recount` uncaught exceptions could skip a
   restore) fixed via shared `_safe_recount` helper, applied to both the new retry AND the
   pre-existing entry-168 round-level gate.
2. **Citation scar-tissue healing** (`task_runs.py::_GARBLED_UNVERIFIED_RE` +
   `neutralize_unverified_urls`). Heals ALREADY-GARBLED `[(unverified))`-style markers
   inherited via auto_improve from before entry 173's fix — these were undetectable by the
   URL-matching `_CITATION_RE` since the original bug already destroyed the URL. Runs
   unconditionally (even when `verified_urls` is empty), independent of citation verification.
3. **Entry 174 follow-up — LLM-fallback structural classifier** (`_classify_structural_opportunity`
   / `_is_structural_opportunity`). User caught that the hardcoded `_STRUCTURAL_OPP_RE` keyword
   regex is whack-a-mole — THIS EXACT TASK's own OR-branch phrasing ("design architecture", no
   "diagram" word) was missing from the list. Fixed the immediate gap (`architecture` added to
   the regex) AND replaced the long-term approach: regex stays as a zero-cost fast path, but any
   opportunity it doesn't recognize now gets ONE real LLM classification call (`STRUCTURAL` or
   `PLAIN`), generalizing to "blueprint"/"wireframe"/"topology map"/anything else. Codex-reviewed
   and hardened: prompt-injection delimiting (`"""..."""` + "untrusted data" framing), 4-6
   few-shot examples (Codex's flagged biggest risk was a weak local model defaulting to PLAIN
   on unfamiliar phrasing), `_dbg()` tracing of every verdict.
4. **Entry 175 — PROPOSED, NOT BUILT.** User's architecture correction: the goal-blind
   invariant applies to spoke WORKERS, not the orchestrator (which already sees the task text
   and already builds each worker's section assignment). Proposes routing a per-section
   structural hint from the orchestrator to the relevant worker at ToC-construction time —
   would supersede today's editor-retrofit approach if it works (diagram drafted in original
   context beats retrofitting one in cold at the end). Real remaining work is TWO parts: (a)
   orchestrator→worker hint threading (small), (b) a NEW, narrowly-gated reducer
   structural-passthrough contract (the real design work — the reducer's prose-only contract
   is a deliberate anti-hallucination guard from entry 162, not something to loosen carelessly).
   Full writeup in WORKLOG entry 175. Do not start without checking whether entry 174's fix
   (below) already reaches acceptable reliability.

## LIVE — the e2e validation run, resume HERE first

- **Backend**: uvicorn restarted at the START of this run with ALL of today's fixes loaded
  (pid was 77190 when last checked — verify still running with
  `ps aux | grep "uvicorn studio.app"`, restart per `CLAUDE.md`'s pattern if dead).
- **Driver**: `.venv/bin/python /private/tmp/claude-501/-Users-yuxinliu/c0934bd3-ac09-44dd-b99d-94e1c73afe56/scratchpad/e2e_driver.py`
  (a small script POSTing `/session` → `/session/{id}/hill-climb` → draining `/run/{id}` SSE to
  EOF — reconstruct from `CLAUDE.md`'s pattern if that scratchpad file is gone by the time you
  resume; scratchpad may not survive across sessions).
- **Session**: `s_791e2db70e88`, continuing task_hash `39ee3efddbd9` (the "Pi and Craft" task —
  same one entry 167/173 originated from), auto_improve seeded from the prior lineage (latest
  recorded version 6, score 0.7182, from the KILLED prior run `s_196ef7b0cd4b` which used
  PRE-FIX code and made no net progress — that run's 2 real findings, duplicate section heading
  and garbled-citation scar tissue, are what led to fixes #2 and #3 above).
- **Logs**: `/private/tmp/claude-501/-Users-yuxinliu/c0934bd3-ac09-44dd-b99d-94e1c73afe56/scratchpad/thru2.log`
  (the `_dbg()` throughput trace — look for `structural-opportunity classify:` lines, the new
  classifier's verdicts, and `editor structural retry attempt=` lines for retry activity),
  `e2e_run2.log` (raw SSE driver output), `backend2.log` (uvicorn stdout/stderr).
- **As of last check**: epoch 1 completed — 8 phase steps + verify + "epoch gate: kept new
  epoch (preferred over prior)" (a GOOD sign — the killed prior run's epoch 1 reverted; this
  one didn't). Editor pass ran both rounds (`editor round=1 REJECT score 0.764->0.764 weak
  4->4`, `editor round=2 REJECT score 0.764->0.728 weak 4->5`, both cleanly reverted — normal
  revert-on-regression, NOT a bug). **Key finding: NO `structural-opportunity classify:` trace
  line appeared in either round** — meaning `_is_structural_opportunity` was never invoked at
  all this epoch. Two possible explanations, NOT yet disambiguated:
  (a) `requirement_compliance_issues` genuinely returned an empty `quality_opportunities` list
  this run (no unmet-but-satisfied-sibling opportunity was generated for "design architecture"
  this time — possible run-to-run variance in the compliance LLM's own judgment, or a deeper
  issue with OR-group extraction/verification for this specific task), or
  (b) `_classify_structural_opportunity`'s real LLM call raised an exception that fail-opened
  BEFORE reaching the `_dbg()` line (the current code logs the verdict only on the success
  path, inside the `try` — an exception skips logging entirely, so this failure mode is
  currently invisible in the trace).

  **UPDATE — run completed all 3 epochs (converged, v7=0.7644 → v8=0.7896 → v9=0.7896 flat).
  `structural-opportunity classify` NEVER appeared even ONCE across all 3 epochs' worth of
  editor passes (6 editor rounds total, all REJECT/revert — normal, not bugs). No mermaid in
  the final artifact.** This rules out explanation (b) above being the ONLY story — across 3
  independent compliance-check calls, `quality_opportunities` coming back empty every single
  time is much more consistent with (a): the compliance verifier (`requirement_compliance_issues`
  in `studio/requirement_compliance.py`) is likely judging the "design architecture" OR-branch
  as SATISFIED by the artifact's EXISTING PROSE alone (it has an "## Architecture" section
  describing the system in words) — "design architecture" is genuinely ambiguous between
  "produce a visual/diagram of the architecture" and "discuss/design the architecture in prose,"
  and the artifact already does the latter. **This is a DEEPER, more upstream finding than the
  regex-keyword gap fixed this session**: even a perfect structural-opportunity classifier can't
  fire if the opportunity-generation layer never flags an opportunity in the first place. Today's
  classifier fix is real, tested, and Codex-reviewed — but genuinely untested end-to-end this
  session because it was never invoked on a live run.
  **Next session's first move, in order of cheapest-to-check:**
  1. Directly call `requirement_compliance_issues(base_client, task_requirements, artifact_text)`
     against this task's real artifact + a real gemma client, and inspect the raw
     SATISFIED/NOT_SATISFIED verdict text for the "design architecture" branch — confirms or
     refutes the hypothesis without a full multi-epoch run.
  2. If confirmed, the fix is likely in `_verify_prompt` (`requirement_compliance.py`) —
     tightening the SATISFIED bar for a structural-shaped branch (the SAME `_STRUCTURAL_OPP_RE`
     / `_is_structural_opportunity` classifier built this session could gate this: if a branch
     is structural-shaped, require the artifact to contain an ACTUAL structural block, not just
     prose ABOUT the topic, before marking it SATISFIED) rather than in the editor-side
     classifier/retry, which are downstream of this and already correct.
  3. Add a real regression test reproducing this exact case (an artifact with prose-only
     "Architecture" discussion + no diagram, verified against a "...or design architecture"
     OR-branch) to `test_requirement_compliance.py` before touching the verify prompt.
- **What "success" looks like**: a `structural-opportunity classify:` trace line in thru2.log
  showing `STRUCTURAL` for the architecture-diagram opportunity, followed by an
  `editor structural retry attempt=N ACCEPT` line, and a real mermaid block in the final
  artifact — proving today's 3 fixes work together end-to-end on the exact task that started
  this whole thread.
- **If the scratchpad files are gone** (likely — scratchpad does not persist across sessions):
  the backend/driver processes may ALSO be gone (background OS processes don't survive a
  machine reboot but may survive a Claude session end depending on how the terminal/shell was
  torn down) — check `ps aux | grep uvicorn` and `sqlite3 tmp/task_runs.db` for whether a new
  version beyond 6 was ever recorded for task_hash `39ee3efddbd9` before deciding whether to
  relaunch.

## Standing constraints (unchanged from the prior handoff)

- Do NOT commit without explicit fresh instruction.
- Task-neutral guardrail: Studio's own code stays generic, no hardcoded domain vocabulary.
- Goal-blind spoke-worker invariant: still respected by everything shipped this session.
- Reducer's prose-only patch contract: still respected — entry 175 is the one proposal that
  would need to touch it, deliberately not started yet.

## Files changed this session (uncommitted, on top of the prior handoff's list)

`studio/runner.py` (`_EDITOR_STRUCTURAL_RETRY_ATTEMPTS`, `_structural_opportunity_block`,
`_editor_structural_retry_prompt`, `_safe_recount`, `_editor_structural_retry`,
`_STRUCTURAL_CLASSIFY_EXAMPLES`, `_classify_structural_opportunity`,
`_is_structural_opportunity`, `_STRUCTURAL_OPP_RE` `architecture` keyword,
`_run_editor_pass` round-loop wiring), `studio/task_runs.py` (`_GARBLED_UNVERIFIED_RE`,
`neutralize_unverified_urls` healing pass), `tests/test_runner.py` (+14 tests),
`tests/test_url_guard.py` (+5 tests), `WORKLOG-research-report-generator-plan.md` (entries
174-175). Run `git status --short` before doing anything destructive.
