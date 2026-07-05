# HANDOFF — attempt-9 verification, fence-eraser trace, remaining slate (2026-07-05 evening)

Continuation of HANDOFF-depth-and-dynamic-sections.md. Design/status authority:
`PLAN-codebase-simplification.md` (STATUS BY CHAPTER dashboard at top; §12 order;
§13 per-fix results; §14 attempt-8 findings + attempt-9 slate).

## Where things stand

- **Branch** `build-research-report-generator-plan`, ~71 commits ahead of origin
  (push = user's call, codex adversarial review queued before push).
- **Suite** 892 passed, 4 deselected (was 836 this morning). Ruff F821 clean.
- **Committed today** (each with §13 row + measured result): S1 textutil; extraction
  AND/OR + SUBJECT COVERAGE + do-not-invent reorder; L0 + observability (extraction
  groups, per-branch compliance verdicts, _accept criterion); S2 finalize pass-list
  (runner −869 lines, per-pass ledger + mined-gate); L3 seed-eligibility gate (lint
  criterion deliberately dropped — §14.6 self-heal conflict); format repairs (fence
  contamination lint #13 + repair, doubled citations); fence-survival
  instrumentation; attempt-9 items 2–4 (diagram-with-prose, residual-lint logging,
  covers-X substance gate).
- **Lineage** (task 492bae60177b): v1 0.145 → v2 0.304 → v3 0.657 → v4 0.433 → v5
  0.41. Two crashed 0.0 rows were backed up + deleted (oMLX eviction cascade — BGE
  loading mid-run evicted gemma; mitigated by pre-warming the embedder before runs).

## IN FLIGHT right now

1. **Attempt 9** (driver task bxqzjrol2, monitor bncdexpew, diag =
   scratchpad/diag21.log). Purpose, in order of importance:
   - **Name the fence-eraser pass**: v5 recorded ZERO fences despite L0's verified
     insertion with every later pass changed=False. Offline repro exonerated
     write_section_workspace (fresh + live-copy). The wrapper now logs `fences=N`
     per pass + the L0 write-through logs before/after. Finalize entry showed
     fences=2. READ THE TRACE: first pass where fences drops = the eraser.
   - Diagram: first `_accept` pass expected (explanatory sentence now attached;
     the 6→7 lint veto was the diagram tripping the explanatory-prose lint).
   - `repair_lints: residual (N): ...` — finally names the unfixable lints
     (v5 had 6 unknown).
   - covers-Craft verdict stability (gate can only downgrade; wobble was raw judge).
2. **Item 5 build** (l3-builder): wire the deterministic fence/citation repairs into
   the PER-STEP writeback normalize ("normalize step=sN" path) — user screenshotted
   the mid-run swallowed-document defect twice; finalize-only repair leaves the doc
   broken all run. Constraint given: do not touch finalize.py.

## Next steps (after attempt 9 records)

1. Read diag21: eraser verdict → fix it (likely small once named).
2. Verify + review + commit item 5; flip §13/§14/dashboard rows.
3. Remaining slate: item 6 P1-core (subject ledger + search DIRECTIVE into
   worker/tool-loop prompts — run 8+9 proved verdicts alone produce ZERO Craft
   searches; no actor), item 7 compliance-verdict memoization per text-hash (~17
   identical 12-branch judge rounds per editor retry), item 8 AST swallowed-content
   lint via markdown-it-py (mdformat's embedded parser — zero new deps).
3. Then §12 slice 6 = L1 editorial pass (spec: §10.2 E-rows + crashed/unscored
   status rule) and slice 7 = S3 guards.

## Known ceilings / decisions made (don't re-litigate without new evidence)

- covers-gate proximity heuristic PASSES title+scope mentions near a Pi URL
  (attempt 9 round 1) — expected; the honest fix is P1's per-subject source ledger,
  not more thresholds. Gate fails open on ≤2-char subjects ("Pi") — documented.
- Formatters (mdformat, evaluated Flowmark/PyMarkdown — both rejected, see §14
  item 5 note) LAUNDER broken fences into valid-wrapped garbage. Invariant:
  repairs before any formatter; mdformat stays the only formatter, last pass.
- L3 lint criterion stays dropped (lint-broken rows are §14.6 self-heal input).
- Editor text-rewrite retries cannot fix structural/coverage hard issues
  (runs 8+9: burned rounds, opp count never moved) — producers and searches fix
  those; consider capping the retry rounds when hard issues are structural-only.

## Environment runbook

- Backend: `cd agentkit-studio/backend && OMC_THROUGHPUT_DEBUG=$SC/diagN.log
  nohup .venv/bin/uvicorn studio.app:app --port 8770 > $SC/uvicornN.log 2>&1 &`
  (SC = session scratchpad). Restart REQUIRED after backend edits (no --reload).
- Before every gemma run: verify oMLX up (`:8000/v1/models`) AND pre-warm BGE
  (`POST /v1/embeddings model=bge-m3-mlx-fp16`) — otherwise oMLX's memory enforcer
  evicts gemma mid-run and the whole server aborts (two crashes; crash.log shows
  Fatal Python error in the embedding thread).
- Driver: scratchpad/experiment_pi_craft.py (drains SSE fully; never curl-abort).
- DB: ALWAYS absolute path `agentkit-studio/backend/tmp/task_runs.db` — a stale
  copy exists at repo-root tmp/ and cwd drifts after `cd` for commits (this caused
  a false "lineage deleted" alarm AND the "phantom failing test" — which was the
  PARENT repo's tests/test_e2e_self_improving.py, now fixed for d5f21c3 semantics).
- Crashed runs record score-0.0 rows: back up + delete before reseeding
  (L3 gate skips them for seeding, but L1's crashed-status rule is the real fix).
- Team: l3-builder (executor, responsive), l0-reviewer (static review per slice),
  l0-producer (HARD STAND-DOWN — inbox delivers out of order; caused a
  double-assignment collision earlier, recorded in §13; reassignments need a
  confirmed stand-down first).
