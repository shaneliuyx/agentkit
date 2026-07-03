# HANDOFF — citation/diagram reliability fixes shipped, citation-grounding bug fixed, GUI quick-start design doc ready for implementation

**Date:** 2026-07-03
**Status:** Three separate threads of work, ALL DONE and tested this session (655 backend
tests passing, 0 regressions, up from 653 at session start). Nothing is mid-flight. Next
session can start fresh on any of: (a) implementing the GUI design doc, (b) the two
deferred/lower-value items from the citation-grounding fix, (c) a live GUI re-run to confirm
the citation fix end-to-end (never re-validated live after the fix — see below).

Session ran long (~$240) and touched 21 files across three logically separate pieces of work.
Read this doc's three sections independently; they don't depend on each other.

---

## Thread 1 — diagram/citation reliability (validated live, DONE)

Starting bugs (found in a live artifact): `(unverified)` citation markers shipping visibly to
users, and diagrams never being generated despite being requested. Fixed with 3 mechanisms,
all Codex-reviewed before implementation per standing instruction:

1. **`strip_unverified_lines`** (`studio/task_runs.py`) — deletes any line containing
   `(unverified)` entirely (was previously a narrower orphaned-bullet strip). Wired into both
   `_scored_text` and `result_output` neutralize call sites in `runner.py`.
2. **Diagram-required note** (`studio/requirement_compliance.py`) — new `_DIAGRAM_SHAPED_RE` /
   `_MERMAID_BLOCK_RE` gate: downgrades a diagram-shaped requirement branch's verdict to
   NOT-SATISFIED when no mermaid block exists anywhere in the artifact, even if the model
   claimed prose-only coverage satisfies it. This was the root cause of "diagram requested,
   never generated" — the compliance checker was accepting prose ABOUT the architecture as
   satisfying a request FOR a diagram of it.
3. **Read-only-streak forcing** (`studio/tools.py`, `ToolAugmentedClient.chat()`) — tracks
   consecutive read-only tool iterations; injects a forcing turn ("make your decision now")
   once a phase spends half its iteration budget reading without ever writing. Found as a
   byproduct, not originally scoped: gemma was observed looping on reads with zero forcing
   mechanism.

**Validated two ways**: unit tests (`test_url_guard.py`, `test_artifact_lint.py`,
`test_requirement_compliance.py`, `test_tools.py`, `test_runner.py` — full additions in
WORKLOG entry 176), AND a full real production run through the real browser GUI (not a
fetch/EventSource bypass — user explicitly required GUI testing as "also a test for GUI").
That live run (task_hash `7f70f62b995b`, session `s_e05361a42d54`, v1, score 0.2857) produced
a real, well-formed mermaid diagram and zero `(unverified)` markers — both original bugs
confirmed fixed. Full detail: WORKLOG entry 176.

## Thread 2 — GUI quick-start UX design doc (design DONE, implementation NOT started)

While Thread 1's live run was in progress, observed real GUI friction (wrong-textarea
mistargeting mid-run, disconnected connect→configure→run sequence, hill-climb toggle buried
in a separate tab). Wrote up "Concrete optimizations, ranked by impact," sent to Codex for
review with explicit side-effect scrutiny, then formalized into
**`DESIGN-gui-quick-start-flow.md`** and sent for a SECOND Codex review round. Both rounds'
findings are folded into the doc — it is ready to implement as-is, not a draft.

**Codex round-2 verdicts baked into the doc:**
- #1 (connected-state feedback), #2 (textarea distinction), #3 (default mode → `llm`), #6
  (stale-session feedback) are straightforward, ship first.
- #4 (auto-connect) needed a redesign (not "connect on focus" — too eager, and `ChatPanel`
  can't self-connect without a lifted callback) — the doc's shape is Codex-approved, including
  failure semantics and the stale-session→reconnect interaction with #6.
- **#5 (inline hill-climb toggle) is flagged "only half-fixed" — do NOT implement as
  currently drafted.** The redesign fixes the inline-checkbox's own state fork, but the
  MODAL (`LoopConfigPanel.tsx:134,687`) still shadows its own local state
  (`hcMetric`/`hcMinDelta`/`hcMaxEpochs`/`hcAutoImprove`) until "Apply" is clicked — the modal
  itself needs a state-source refactor first. The doc's Rollout order section explicitly
  excludes #5 from the ship sequence until that separate design pass happens.
- Rollout order: #1→#2→#3→#6→#4, with #6 a hard prerequisite for #4 (Send's stale-session
  reconnect path has nothing to check until #6 exists). #5 excluded, needs its own pass.

**Next step if resuming this thread**: implement in the rollout order above, starting with #1.
No further Codex review needed for items #1/#2/#3/#6/#4 — the doc already carries two rounds
of review. #5 needs a fresh design (and likely a fresh Codex round) before it's implementable.

## Thread 3 — citation-grounding bug (root-caused, fixed, tested; NOT re-validated live)

Same live run from Thread 1 (score 0.2857) shipped with **zero URL-backed citations** despite
genuinely fetching 16 real URLs across 24 grounded `RESEARCH_FINDING` blocks (verbatim quotes,
real sources — arxiv/github/deepwiki/etc, confirmed in the session's `io/*.out.md`
transcripts). Investigated why the 8 listed scoring gaps (source quality 0/14.7, citation
integrity 0/14.7 ×2, evidence synthesis 4.9/14.7 ×2, publish-gate no-source-URL, missing
how-to steps) couldn't be bridged despite real research having happened, plus why the
session's `evidence/` workspace subdir was never created.

**Root cause** (confirmed via `omc ask codex` review — two rounds, artifacts under
`.omc/artifacts/ask/codex-*citation*`): `studio/tools.py`'s `_fetch_cache` is deliberately
`contextvars`-scoped per run-thread (prevents cross-SESSION grounding leakage — a real,
intentional fix). STAR/MAP spokes fan out via `ThreadPoolExecutor`
(`agentkit/topology/dynamic.py:794`), whose worker threads don't inherit the parent's
contextvars context. The reducer (`_make_section_reducer` in `studio/findings.py`) runs back
on the parent thread — and if ITS OWN `client.chat()` call triggers any tool activity, the
parent's local cache becomes non-empty with entries unrelated to what the spokes actually
fetched. `_parse_findings`'s grounding gate then drops EVERY real spoke finding, not just
some — Codex validated empirically that an empty reducer cache correctly fails-open (keeps
all 24), but a parent cache with even one unrelated URL drops all 24.

The missing `evidence/` dir is the SAME bug, not separate: `_final_evidence_dossier`
(`runner.py:436`) returns `""` before ever reaching `evidence_dir.mkdir()` (line 479) when it
finds zero URLs in the document text (line 448) — zero surviving citations → early return →
dir never created.

**Fix applied** (`studio/findings.py::_prefetch_cited` — Codex's explicitly-preferred
lower-risk path over merging spoke-thread caches into the parent context, which would touch
`agentkit` core's generic thread-dispatch semantics for unclear added benefit):
- Removed the stale `if not _fetch_cache: return 0` guard — the assumption that empty cache
  always means safe fail-open is only true if the cache STAYS empty for the rest of the call.
- New `_cited_urls()` helper extracts cited URLs from both plain `URL:` lines AND JSON-wrapped
  `{"RESEARCH_FINDING": {"URL": ...}}` findings (the old regex only scanned plain lines,
  missing the oMLX/qwen JSON-fenced format `_parse_findings` itself already parses).
- `_PREFETCH_LIMIT` raised 8→24 (a 3-phase fan-out routinely cites 8-9 URLs per phase alone).

Updated `test_prefetch_cited_extracts_dedups_and_caps` (its old empty-cache-is-no-op
assertion no longer holds) and added `test_prefetch_cited_runs_even_with_empty_starting_cache`
+ `test_prefetch_cited_extracts_urls_from_json_shaped_findings`. Full detail: WORKLOG entry 177.

**NOT yet done — this is the most valuable next step for this thread**: the fix has NOT been
re-validated with a live GUI run against the same or a similar research task. Thread 1's live
run predates this fix. Running the exact same or a similar task again and confirming the
final artifact now carries real citations (and that `evidence/` gets created) would close the
loop that Thread 1 opened.

**Deferred, per Codex's assessment of low value / out of scope for this fix:**
- A raw `web_fetch{url:<|"|>...}<tool_call|>` token-leak seen in one spoke turn
  (`s12.spoke4.out.md`) — a narrow `_parse_inline_tool_calls` coverage gap affecting ~1 of ~30
  tool-calling turns in the test run, unrelated to the citation-loss mechanism.
- A regression test asserting intra-run spoke-to-reducer cache visibility under an ACTIVE
  unrelated parent cache (Codex's point 4) — the prefetch strengthening makes this scenario
  recoverable without needing cache-merge machinery; revisit only if the grounding-drop
  symptom resurfaces.

## Standing constraints

- Do NOT commit without explicit fresh instruction.
- Every plan/solution gets a Codex review before implementation (user's standing rule this
  session — followed for all three threads above).
- Task-neutral guardrail: Studio's own code stays generic, no hardcoded domain vocabulary.
- GUI testing uses the real browser (Chrome DevTools MCP), not a fetch/EventSource bypass, per
  explicit user correction earlier this session — see memory
  `reference_agentkit_studio_gui_test_quirks.md` for the tooling gotchas hit along the way
  (wrong-textarea targeting, `fill()` unreliable for long text, cancel-then-resend 409s).

## Files changed this session

`backend/studio/task_runs.py` (`strip_unverified_lines`), `backend/studio/artifact_lint.py`
(clean-marker detection), `backend/studio/runner.py` (rename + neutralize wiring +
`lint_markers` addition), `backend/studio/tools.py` (read-only-streak forcing),
`backend/studio/requirement_compliance.py` (new file — diagram-required downgrade logic),
`backend/studio/findings.py` (`_prefetch_cited` strengthening, `_cited_urls`,
`_PREFETCH_LIMIT` 8→24), `backend/tests/test_url_guard.py`, `test_artifact_lint.py`,
`test_requirement_compliance.py` (new file), `test_tools.py`, `test_runner.py` (multiple
additions across the session), `DESIGN-gui-quick-start-flow.md` (new),
`WORKLOG-research-report-generator-plan.md` (entries 176-177). Run `git status --short`
before doing anything destructive — this repo carries a large pre-existing uncommitted diff
from many prior sessions; most of the ~50 modified files `git status` shows predate this
session and are unrelated to it.
