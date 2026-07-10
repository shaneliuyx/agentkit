"""studio.finalize — the epoch-end finalize pipeline as an ordered pass list.

PLAN-codebase-simplification.md S2: ``Runner._postrun_score_and_record`` (runner.py,
pre-extraction) was ~1,000 lines of nested inline try-blocks — every pass silently
swallowed its own exceptions, and several ran invisibly-inert for a long time before
anyone noticed (the "editor=0.00s" / dead-editor-pass bug class was exactly this: a
pass silently never firing looked identical to it running and legitimately no-op'ing).

This module turns the sequence into an explicit, ORDERED list of ``(name, fn)`` passes
run by ONE wrapper (:func:`run_passes`) that owns:
  * fail-open — a raising pass is caught, logged via ``_dbg``, and execution continues
    to the next pass (never crashes the run);
  * an entry/exit diagnostic per pass (ran / changed / reason-if-skipped-or-failed);
  * an inert-pass ledger (PLAN L2): a pass that no-ops for >=3 consecutive epochs in
    THIS run gets flagged via ``_dbg`` at the end of the epoch (cross-RUN escalation
    is a separate PLAN item, L4, not handled here).

HARD CONSTRAINT: every pass body below is the ORIGINAL runner.py block, MOVED not
rewritten — same order, same skip conditions, same comments explaining WHY each guard
exists. See "FAULT-PROPAGATION NOTE" below for the one place this is not a strict
byte-for-byte behavior guarantee.

FAULT-PROPAGATION NOTE: in the pre-extraction code, most passes already had their own
self-contained ``try/except`` (a raise there was already isolated). A handful did NOT
(``score_and_mine_weaknesses``, the pre-``accept_epoch`` setup in ``epoch_gate``,
``prune_resolved_weaknesses``, and the final ``score_scorecard_and_record``) — they
relied on ONE outer try in ``_postrun_score_and_record`` that, on any exception
anywhere in that unguarded span, silently aborted EVERY remaining pass (including the
final DB record and ``HillClimbEvent`` emit), leaving the epoch's outcome unrecorded.
Under this module's per-pass wrapper, a raise in one of those four passes is still
caught and logged, but the REMAINING passes still get a chance to run. There is a
THIRD outcome besides "no-op" and "raise on missing state": a later pass can SUCCEED
on semantically-empty defaults — proven empirically for the scoring→record pair,
where a ``score_result`` failure left ``state.weaknesses`` at its default and the
record pass happily persisted a row with an inflated adjusted_score plus a
HillClimbEvent. RESOLUTION: ``state.mined`` is set only when
``score_and_mine_weaknesses`` completes, and ``score_scorecard_and_record`` gates on
it (early return, logged, no row, no event) — restoring the pre-extraction
"scoring failure means the epoch is not recorded" contract. Passes BETWEEN the two
still run on a scoring failure (text-only side effects, no persistence) — that
residual divergence is accepted and covered by the L2 pass ledger's visibility.
"""
from __future__ import annotations

import time
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from studio.artifact_text import (
    _refine_readability,
    _repair_lints,
    _strip_preamble,
    _synthesize_analysis,
    add_missing_section_citations,
    normalize_artifact,
    strip_satisfied_placeholders,
)
from studio.events import EvidenceEvent, GateEvent, HillClimbEvent
from studio.runner import (
    _RESUME_OUTPUT_FLOOR,
    _active_template,
    _epoch_status,
    _prune_resolved_weaknesses,
    _publish_revision_regressed,
    _run_editor_pass,
    _scoring_template,
    _update_active_template_from_artifact,
    _verified_urls_from_cache,
    _web_cache_available,
    _write_artifact_through_sections,
    EpochResult,
)
from studio.textutil import dbg as _dbg


@dataclass
class FinalizeState:
    """Everything the finalize pass list reads or mutates across pass boundaries.

    Deliberately narrow: only values genuinely READ by a LATER pass after being SET
    by an earlier one are fields here. Values used within a single pass only stay
    local to that pass function, exactly as in the pre-extraction code (mirrors what
    exists — no speculative modeling)."""

    # -- run-scoped context (read-only after construction) ------------------
    runner: Any  # the Runner instance — cross-cutting state already lives on it
    session: Any
    outputs: dict[str, str]
    base_client: Any
    judge_client: Any
    use_llm: bool
    base_requirement: str
    original_requirement: str
    artifact_copied: bool
    reducer_gaps: list[str]
    hc_cfg: dict

    # -- setup-computed, read-only across passes -----------------------------
    store: Any
    thash: str
    effective_ws_root: Path
    art_file: Path
    art_path: str
    is_last_epoch: bool

    # -- mutated by passes ----------------------------------------------------
    scored_text: str
    result_output: str
    verified_urls: list[str]
    seed_text: str
    outcome: EpochResult
    weaknesses: list[str] = field(default_factory=list)
    evidence_rows: list[dict[str, Any]] = field(default_factory=list)
    #: True only after score_and_mine_weaknesses COMPLETED this epoch. The record
    #: pass gates on it: pre-extraction, a raise in scoring aborted the shared outer
    #: try so no row/event was ever produced — this flag preserves that contract
    #: under the per-pass fail-open wrapper (see FAULT-PROPAGATION NOTE).
    mined: bool = False
    #: Set by the runner when THIS epoch's text came from research_first's
    #: ASSEMBLE stage (PLAN §16) rather than the seed-and-patch phase loop. A mode
    #: marker, not a task-specific check — ASSEMBLE already owns dedupe/references/
    #: repairs/structural production, so re-running the old content-mutating passes
    #: against its output would be the "three copies of the truth" disease
    #: (REBUILD-LESSONS §3) one stage later. The recording/scoring tail is
    #: unaffected: it is not in ``_CONTENT_MUTATING_PASSES`` below.
    rebuild_generated: bool = False


_BARE_FENCE_LANGS = {
    "bash",
    "javascript",
    "js",
    "json",
    "mermaid",
    "python",
    "py",
    "sh",
    "shell",
    "ts",
    "typescript",
}
_PLACEHOLDER_MARKERS = (
    "_(pending - needs sourced content)_",
    "_(to be completed)_",
)
_FENCE_LANG_RE = re.compile(r"^[A-Za-z0-9_+.-]+$")


def _artifact_structure_ok(text: str) -> bool:
    """Reject rewrite outputs that break markdown fence structure.

    Count-balanced fences are not enough: the live failure had the same number
    of ``` markers but detached ``python``/``typescript`` tag lines, flipping
    parity for every later heading. This is an accept gate, not a repair pass.
    """
    in_fence = False
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        if not in_fence and stripped.lower() in _BARE_FENCE_LANGS:
            return False
        if any(marker in stripped for marker in _PLACEHOLDER_MARKERS):
            return False
        if in_fence and stripped.startswith("## "):
            return False
        if stripped.startswith("```"):
            rest = stripped[3:].strip()
            if in_fence:
                if rest:
                    return False
                in_fence = False
            else:
                if rest and not _FENCE_LANG_RE.fullmatch(rest):
                    return False
                in_fence = True
    return not in_fence


PassFn = Callable[[FinalizeState], FinalizeState]


def _pass_normalize_dedupe(state: FinalizeState) -> FinalizeState:
    """N1 / anti-accumulation (verified live): collapse DUPLICATE section headings in
    the FINAL artifact before it is scored, served, and carried forward as the next
    seed. A gemma spoke that echoes the whole document, stacked by the grow-only
    writeback, repeats every template section 8-10x; dedupe keeps the richest body
    per heading. Done here (the finalization boundary, no accept_rewrite guard) so a
    clean, single-outline document is what the user sees AND what the next epoch
    seeds from — the lineage cannot re-inherit the bloat. No-op on an already-clean
    document."""
    try:
        deduped = normalize_artifact(state.scored_text or "")
        if deduped != state.scored_text:
            state.scored_text = deduped
            state.result_output = normalize_artifact(state.result_output or "")
            if state.art_file.exists():
                state.scored_text = _write_artifact_through_sections(
                    state.session, state.effective_ws_root, state.scored_text,
                    state.original_requirement,
                )
                state.result_output = state.scored_text
                _update_active_template_from_artifact(state.session, state.scored_text)
            _dbg(f"normalize_artifact: un-glued + deduped → {len(state.scored_text)} chars")
    except Exception:  # noqa: BLE001 — dedupe is best-effort; never break recording
        pass
    return state


def _pass_materialize_artifact(state: FinalizeState) -> FinalizeState:
    """ROOT CAUSE (verified live, editor=0.00s): cold-start "auto"-mode runs end with
    NO canonical artifact.md — the skeleton bootstrap is gated on mode=="llm" and only
    seeded lineage runs get the file copied in. Every downstream stage gated on
    art_file.exists() — the editor/presentation passes (diagrams/tables/lists), the
    mermaid-repair write-back, and the next run's seed carry-forward — was silently
    dead for exactly those runs. Materialize the finalized text under the canonical
    name (sections + assembled artifact.md) before those gates evaluate.

    On a ``rebuild_generated`` run (research_first), OVERWRITE when the on-disk
    file doesn't already hold ``scored_text``: research_first's own
    write-after-return (runner.py) can fail (a swallowed OSError) leaving an
    auto-improve seed-carry-forward's STALE copy on disk with nothing left to
    correct it before the next continuation seeds from it. This writes the
    file DIRECTLY — never through ``_write_artifact_through_sections`` — that
    helper's title/section-split machinery is built for the old hub/spoke
    pipeline's incremental skeleton and mutates a finished document (verified:
    it rewrites a generic-looking H1 via ``resolve_report_title`` even when
    the text is already complete). research_first's ASSEMBLE stage already
    owns its own title and structure; the file must match it byte-for-byte."""
    if state.rebuild_generated:
        try:
            current = state.art_file.read_text(encoding="utf-8") if state.art_file.exists() else None
        except OSError:
            current = None
        if current != state.scored_text and (state.scored_text or "").strip():
            try:
                state.art_file.write_text(state.scored_text, encoding="utf-8")
                _dbg(f"materialized artifact.md ({len(state.scored_text)} chars) — rebuild_generated overwrite (stale/missing file)")
            except OSError as exc:
                _dbg(f"materialize_artifact: rebuild_generated overwrite failed {exc!r}")
        return state
    if not state.art_file.exists() and (state.scored_text or "").strip():
        try:
            state.scored_text = _write_artifact_through_sections(
                state.session, state.effective_ws_root, state.scored_text,
                state.original_requirement,
            )
            state.result_output = state.scored_text
            _update_active_template_from_artifact(state.session, state.scored_text)
            _dbg(f"materialized artifact.md ({len(state.scored_text)} chars) — cold-start auto-mode run")
        except Exception:  # noqa: BLE001 — materialization is best-effort
            pass
    return state


def _pass_synthesize_readability(state: FinalizeState) -> FinalizeState:
    """FINAL instructor-tone readability refine (user request) — supersedes the plain
    analysis pass (PLAN item 1A): its directive already weaves in analysis +
    reflection, AND rewrites the report into clear teaching prose that explains
    complex theory in plain language, every citation intact. Section-windowed, on the
    raw judge client (no tools/fetch); each section rejected if it drops a URL or
    shrinks. Runs only on the FINAL epoch — it is the polish step — to bound the
    per-section LLM cost.

    NOT gated on use_llm (mode-gate disease, 2026-07-05): _synthesize_analysis is the
    ONLY writer of cross-section analysis — the "Evidence synthesis" and "Analytical
    depth" rubric rows it exists to satisfy were the exact residual weaknesses on
    every auto-mode run, because mode=="auto" made this pass dead. base_client is
    always built; depth is independent of the generation mode."""
    if (state.is_last_epoch and state.scored_text
            and len(state.scored_text) > 800 and "http" in state.scored_text):
        try:
            syn, changed = _synthesize_analysis(
                state.scored_text, state.base_client, state.original_requirement
            )
            if changed:
                state.scored_text = syn
                state.result_output = syn
            syn, changed = _refine_readability(
                state.scored_text, state.base_client, state.original_requirement
            )
            if changed:
                state.scored_text = syn
                state.result_output = syn
                try:
                    state.verified_urls = _verified_urls_from_cache(state.scored_text)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001 — synthesis must never break recording
            pass
    return state


def _pass_repair_lints(state: FinalizeState) -> FinalizeState:
    """§14.6 root-cause fix (reported broken-diagram bug): lint the OUTPUT and, if a
    mermaid block is malformed, repair JUST that block via the model and splice it
    back deterministically (whole-doc repair truncates a large artifact — verified).
    Covers the single-epoch/cold-start case the seed-only repair clause and the
    next-epoch self-heal both miss. No-op when the document is already clean.

    NOT gated on use_llm: output VALIDITY is independent of the generation mode — a
    hill-climb run in the default "auto" mode (use_llm False) still ships a doc whose
    mermaid must be valid. base_client is always built, so repair can run."""
    if state.base_client is not None and state.scored_text:
        try:
            from studio.artifact_lint import lint_artifact as _lint_dbg
            lints_before = _lint_dbg(state.scored_text)
            rep, rchanged = _repair_lints(
                state.scored_text, state.base_client, state.original_requirement
            )
            # Observability (the bug was un-diagnosable because repair was silent):
            # record whether it ran, fired, and the residual lint count.
            _dbg(
                f"repair_lints: lints_before={len(lints_before)} "
                f"changed={rchanged} lints_after={len(_lint_dbg(rep))}"
            )
            if rchanged:
                state.scored_text = rep
                state.result_output = rep
                try:
                    if state.art_file.exists():
                        state.scored_text = _write_artifact_through_sections(
                            state.session, state.effective_ws_root, state.scored_text,
                            state.original_requirement,
                        )
                        state.result_output = state.scored_text
                        _update_active_template_from_artifact(state.session, state.scored_text)
                except Exception:  # noqa: BLE001 — write-back is best-effort
                    pass
        except Exception as rexc:  # noqa: BLE001 — repair must never break recording
            _dbg(f"repair_lints: EXCEPTION {rexc!r}")
    return state


def _pass_neutralize_urls(state: FinalizeState) -> FinalizeState:
    """PLAN item 3: neutralize fabricated/unverified URLs before scoring AND serving,
    so a reducer-invented link cannot earn citation credit or reach the user.
    FAIL-OPEN — an empty verified set (search down) changes nothing."""
    try:
        from studio.task_runs import neutralize_unverified_urls, strip_unverified_lines
        # Finding 6: fail-open neutralization keeps a transient outage from blanking
        # real citations, but that same behavior lets fabricated URLs through when
        # verification simply COULDN'T run. Log the two apart so an operator can tell
        # "no sources verified" from "cache unreadable".
        if not state.verified_urls and not _web_cache_available() and "http" in (state.scored_text or ""):
            _dbg(
                "url-verification UNAVAILABLE (web cache missing/unreadable); "
                "cited URLs served UNVERIFIED — possible fabrication passing through"
            )
        cleaned = strip_unverified_lines(
            neutralize_unverified_urls(state.scored_text, state.verified_urls)
        )
        if cleaned != state.scored_text:
            state.scored_text = cleaned
            state.result_output = strip_unverified_lines(
                neutralize_unverified_urls(state.result_output, state.verified_urls)
            )
            try:
                if state.art_file.exists():
                    state.scored_text = _write_artifact_through_sections(
                        state.session, state.effective_ws_root, state.scored_text,
                        state.original_requirement,
                    )
                    state.result_output = state.scored_text
                    _update_active_template_from_artifact(state.session, state.scored_text)
            except Exception:  # noqa: BLE001 — write-back is best-effort
                pass
    except Exception:  # noqa: BLE001
        pass
    return state


def _pass_structural_producer(state: FinalizeState) -> FinalizeState:
    """L0 (PLAN §8): deterministic structural producer. Every generation-time
    diagram/code path is prose-only by contract, so structural content only ever
    ships through post-hoc editor retries gated on score/weak-count movement the
    rubric barely rewards — the root cause of "almost never ships." Mirrors
    rebuild_references_section below: build -> validate -> insert -> fail-open,
    judged on structural validity ONLY. Runs on scored AND served text, same as
    every other finalize pass here."""
    runner = state.runner
    if runner._task_requirements and state.base_client is not None:
        try:
            from studio.structural_producer import produce_missing_structures

            evidence_dir = state.effective_ws_root / state.session.session_id / "evidence"
            sp_text, sp_stats = produce_missing_structures(
                state.scored_text,
                runner._task_requirements,
                client=state.base_client,
                evidence_dir=evidence_dir if evidence_dir.is_dir() else None,
                dyn_sections=getattr(runner, "_dyn_subsections", None),
            )
            _dbg(f"structural_producer: {sp_stats}")
            if sp_text != state.scored_text:
                state.scored_text = sp_text
                state.result_output = sp_text
                try:
                    if state.art_file.exists():
                        _fences_pre = sp_text.count("```")
                        state.scored_text = _write_artifact_through_sections(
                            state.session, state.effective_ws_root, state.scored_text,
                            state.original_requirement,
                        )
                        state.result_output = state.scored_text
                        # Run v5: fence vanished between L0's verified insertion and
                        # compliance with every pass reporting changed=False — this
                        # line splits "eraser is the write-through" from "eraser is
                        # a later pass" definitively.
                        _fences_post = (state.scored_text or "").count("```")
                        _dbg(
                            f"structural_producer write-through: fences "
                            f"{_fences_pre}→{_fences_post}"
                        )
                        if _fences_post < _fences_pre:
                            # Attempt 9 caught the section round-trip DROPPING an
                            # inserted block live (4→2; the doc carried duplicate
                            # ## sections that confuse the splitter). Losing
                            # verified content is worse than skipping the section
                            # sync: keep the inserted text and write it to disk
                            # directly so all three sources stay aligned.
                            _dbg(
                                "structural_producer write-through: REVERTED — "
                                "section round-trip lost fenced blocks; keeping "
                                "inserted text verbatim"
                            )
                            state.scored_text = sp_text
                            state.result_output = sp_text
                            try:
                                state.art_file.write_text(sp_text, encoding="utf-8")
                            except Exception:  # noqa: BLE001 — disk sync best-effort
                                pass
                        _update_active_template_from_artifact(state.session, state.scored_text)
                        state.verified_urls = _verified_urls_from_cache(state.scored_text)
                except Exception:  # noqa: BLE001 — write-back is best-effort
                    pass
                sp_detail = "; ".join(
                    f"inserted {'grounded code excerpt' if k == 'code' else 'mermaid diagram'}"
                    for k in ("code", "diagram") if sp_stats.get(k) == "inserted"
                )
                runner._emit(GateEvent(
                    name="structural-producer",
                    outcome="pass",
                    detail=sp_detail or "structural content inserted",
                    sandboxed=True,
                ))
        except Exception as spexc:  # noqa: BLE001 — L0 must never break recording
            _dbg(f"structural_producer: EXCEPTION {spexc!r}")
    return state


def _pass_rebuild_references(state: FinalizeState) -> FinalizeState:
    """P2-8: References is a deterministic bibliography of BODY-cited URLs
    (post-neutralize, so only surviving citations earn an entry). Runs on scored AND
    served text so junk References prose (off-topic findings, refusal filler) can
    neither earn scoring credit nor reach the user."""
    try:
        from studio.artifact_text import rebuild_references_section

        rebuilt = rebuild_references_section(state.scored_text)
        if rebuilt != state.scored_text:
            state.scored_text = rebuilt
            state.result_output = rebuild_references_section(state.result_output)
            _dbg("references section rebuilt deterministically from body citations")
            try:
                if state.art_file.exists():
                    state.scored_text = _write_artifact_through_sections(
                        state.session, state.effective_ws_root, state.scored_text,
                        state.original_requirement,
                    )
                    state.result_output = state.scored_text
            except Exception:  # noqa: BLE001 — write-back is best-effort
                pass
    except Exception:  # noqa: BLE001 — bibliography rebuild must never break scoring
        pass
    return state


def _pass_score_and_mine_weaknesses(state: FinalizeState) -> FinalizeState:
    """Score the artifact (LLM judge, feedback text only — the RECORDED score is the
    deterministic rubric computed later), mine weaknesses against the full artifact,
    then apply the deterministic refinement passes: reducer-gap prepend, lint
    prepend, section-presence guard, false-weakness refutation, semantic dedup.

    See the module FAULT-PROPAGATION NOTE: this block had no self-contained
    try/except pre-extraction (it relied on the one outer try in
    _postrun_score_and_record)."""
    from studio.task_runs import mine_weaknesses_from_outputs, score_result

    _score, scorer_feedback = score_result(
        state.scored_text, state.original_requirement, state.base_client,
        verified_urls=state.verified_urls or None,
    )
    # Mine against the full artifact so the miner sees real content (URLs, citations,
    # conclusions). Pass result_output in the outputs dict to let the miner still
    # catch synthesis failures like "workers returned status only". Without this, the
    # miner saw the 3K reducer response instead of the 28K artifact and reported "no
    # URLs" when 8 real URLs already existed.
    mine_outputs = {k: v for k, v in state.outputs.items()}
    if state.result_output:
        mine_outputs["reducer_response"] = state.result_output
    weaknesses = mine_weaknesses_from_outputs(
        mine_outputs,
        state.scored_text,
        state.original_requirement,
        state.base_client,
        scorer_feedback=scorer_feedback,
        verified_urls=state.verified_urls or None,  # cache-as-oracle (§11.10)
    )
    # §11.4: prepend the reducer's last-phase gaps — they are concrete and grounded
    # ("§Results: no source URL"), so they make the next run's constraints specific.
    # Dedup against the LLM-mined set.
    if state.reducer_gaps:
        seen_w = set(weaknesses)
        weaknesses = [g for g in state.reducer_gaps if g not in seen_w] + weaknesses
    # §14.6: deterministic content-validity lints (malformed mermaid edge, truncated
    # code fence) the gap-based miner never names. Prepend so they seed the next run's
    # constraints and the reducer repairs them in place (accept_rewrite already
    # permits the rewrite). Concrete + grounded, like reducer_gaps. Surfaced to the
    # user via HillClimbEvent.weaknesses too.
    try:
        from studio.artifact_lint import lint_artifact
        lints = lint_artifact(state.scored_text or "")
        if lints:
            seen_w = set(weaknesses)
            weaknesses = [w for w in lints if w not in seen_w] + weaknesses
    except Exception:  # noqa: BLE001 — lint is best-effort, never block recording
        pass
    # Deterministic section guard (DESIGN §14.2): the moving-window miner now sees
    # the whole artifact, but the SCORER still windows (20K) and its UNMET line is
    # fed to the miner as a starting point — so a tail section past the scorer's
    # window can still echo through as a false "missing X". Drop any missing/absent-
    # section weakness for a section the concept-aware FULL-TEXT check confirms is
    # present, so a complete report is not penalised for a blind spot. Only when a
    # rubric template is configured (else there is no authoritative section list).
    tmpl = _active_template(state.session)
    if tmpl and weaknesses:
        from studio.rubric import sections_present
        present = {s.lower() for s in sections_present(state.scored_text, tmpl)}
        if present:
            weaknesses = [
                w for w in weaknesses
                if not (
                    ("missing" in w.lower() or "absent" in w.lower())
                    and any(s in w.lower() for s in present)
                )
            ]
    # PLAN N2/N3: refute miner hallucinations of MISSING/TRUNCATED content that is
    # actually PRESENT (example code, conclusion, summary, clean section ends) —
    # deterministic, template-independent, so it also fires when no rubric template
    # is configured. A phantom weakness otherwise depresses adjusted_score and seeds
    # a fix for nothing.
    if weaknesses:
        from studio.task_runs import refute_false_weaknesses
        weaknesses = refute_false_weaknesses(weaknesses, state.scored_text or "")
    # Semantic dedup: the moving-window miner can surface the SAME issue under two
    # section prefixes (e.g. the popularity-ranking gap as both [## Source Selection]
    # and [## Key Findings]). Exact-string dedup misses these re-phrasings, so they
    # count as two unsolved items and depress solved/total — part of why the recorded
    # score jitters epoch-to-epoch on an improving document. Collapse near-duplicates
    # (cosine >= the same 0.85 threshold _weakness_score uses), keeping the first.
    runner = state.runner
    if runner._embedder is not None and len(weaknesses) > 1:
        from studio.task_runs import _cosine
        try:
            wvecs = runner._embedder.embed(weaknesses)
            kept: list[str] = []
            kept_vecs: list = []
            for wk, wv in zip(weaknesses, wvecs):
                if any(_cosine(wv, kv) >= 0.85 for kv in kept_vecs):
                    continue
                kept.append(wk)
                kept_vecs.append(wv)
            weaknesses = kept
        except Exception:  # noqa: BLE001 — dedup is best-effort; embedder may be down
            pass
    state.weaknesses = weaknesses
    state.mined = True
    return state


def _pass_epoch_gate(state: FinalizeState) -> FinalizeState:
    """Phase-1 keep/discard gate (DESIGN §14.1): when this epoch seeded from a prior
    best, KEEP its artifact only if a label-free judge strictly prefers it over the
    seed. On reject, restore the prior so the carry-forward seed never regresses
    (worst case = prior good report retained). Cold start (no seed) always accepts.
    Reuses agentkit.evolve.self_preference via studio.epoch_gate; a judge failure
    fails OPEN (no worse than the old ungated length ratchet).

    See the module FAULT-PROPAGATION NOTE: the preference-function selection below
    (before the accept_epoch call) had no self-contained try/except pre-extraction."""
    runner = state.runner
    if state.artifact_copied and state.seed_text.strip():
        from studio.epoch_gate import accept_epoch, make_rubric_preference
        if runner._prefer_fn is not None:
            pf = runner._prefer_fn
            prefer = lambda n, p: pf(n, p, state.original_requirement)  # noqa: E731
        else:
            # DEFAULT judge = deterministic research-report rubric (DESIGN §14.2). An
            # LLM "which is better?" judge ties strong-vs-stub even on sonnet (§14.1
            # D4); the rubric separates them reproducibly. verified_urls is the
            # accuracy oracle (URLs confirmed real via the web cache). Weights +
            # deliverable template come from the GUI rubric_config.
            rc = getattr(state.session, "rubric_config", None) or {}
            prefer = make_rubric_preference(
                state.verified_urls or None,
                weights=rc.get("weights"),
                required_sections=_active_template(state.session),
            )
        try:
            if not accept_epoch(state.scored_text, state.seed_text, prefer):
                state.seed_text = _write_artifact_through_sections(
                    state.session, state.effective_ws_root, state.seed_text,
                    state.original_requirement,
                )
                _update_active_template_from_artifact(state.session, state.seed_text)
                state.result_output = state.seed_text
                state.scored_text = state.seed_text
                state.verified_urls = _verified_urls_from_cache(state.scored_text)
                _dbg("epoch gate: reverted to prior (new not preferred)")
            else:
                _dbg("epoch gate: kept new epoch (preferred over prior)")
        except Exception:  # noqa: BLE001 — gate must never crash the run
            pass
    return state


def _pass_post_gate_finalize(state: FinalizeState) -> FinalizeState:
    """POST-GATE FINALIZATION (fixes the reported served-broken-mermaid + quote-wall):
    the normalize / repair / readability passes above run BEFORE the keep-discard
    gate, so a REVERT to the raw seed THROWS THEM AWAY and serves an unrepaired,
    unrefined document. Re-apply them to whatever the gate kept, so the SERVED +
    RECORDED artifact is ALWAYS normalized (no dup/glued headings), mermaid-repaired,
    and — on the final epoch — rewritten into instructor-readable prose.
    normalize/repair are idempotent; readability is bounded by its URL + min_ratio
    guards (never drops a citation)."""
    try:
        original = state.scored_text or ""
        fin = normalize_artifact(state.scored_text or "")
        fin, _ = _repair_lints(fin, state.base_client, state.original_requirement)
        # §5.4b: the GROUNDED FULL (normalized + repaired, pre-readability) is the
        # archive — preserve it to result.md before readability shrinks it.
        # artifact.md then holds the readable+deduped version, which is the SEED for
        # the next turn and the scored artifact (any cleanup must land on the seed or
        # it is wasted).
        grounded_full = fin
        if (state.use_llm and state.is_last_epoch and fin
                and len(fin) > 800 and "http" in fin):
            sr, sc = _synthesize_analysis(fin, state.base_client, state.original_requirement)
            if sc:
                fin = strip_satisfied_placeholders(normalize_artifact(sr))
                grounded_full = fin
            rr, rc = _refine_readability(fin, state.base_client, state.original_requirement)
            if rc:
                fin = strip_satisfied_placeholders(normalize_artifact(rr))
        if not _artifact_structure_ok(fin):
            _dbg("post-gate finalize: rejected structurally invalid rewrite")
            return state
        if original and not _artifact_structure_ok(original):
            _dbg("post-gate finalize: original artifact is structurally invalid")
        if state.art_file.exists() and grounded_full and grounded_full != fin:
            (state.art_file.parent / "result.md").write_text(grounded_full)
            _dbg(f"archived grounded-full → result.md ({len(grounded_full)} chars)")
        if fin and fin != (state.scored_text or ""):
            state.scored_text = fin
            state.result_output = fin
            if state.art_file.exists():
                fin = _write_artifact_through_sections(
                    state.session, state.effective_ws_root, fin, state.original_requirement
                )
                _update_active_template_from_artifact(state.session, fin)
                state.scored_text = fin
                state.result_output = fin
            state.verified_urls = _verified_urls_from_cache(state.scored_text)
            _dbg(f"post-gate finalize → {len(fin)} chars")
    except Exception:  # noqa: BLE001 — finalization must never crash recording
        pass
    return state


def _pass_expand_underdeveloped(state: FinalizeState) -> FinalizeState:
    """Deterministic report publish gate prep — §9 step 5: grow depth from under-used
    grounded evidence BEFORE the publish gate. Guarded + no-op-safe
    (studio.expand_sections) — zero LLM calls when no section is under the word
    floor, so healthy reports pay nothing. On accept it persists through the SAME
    section machinery the publish-accept path uses, so the served artifact stays
    consistent.

    NOT gated on use_llm (same mode-gate disease as the skeleton and
    _synthesize_analysis): expand is the depth-grower for under-used grounded
    evidence and must run in auto mode too — its own word-floor guard already makes
    it free on healthy reports."""
    runner = state.runner
    if state.base_client is not None:
        try:
            from studio.expand_sections import expand_underdeveloped_sections
            from studio.rubric import rubric_score as exp_rubric

            # Resume depth-restore: a restarted / silent-worker run has an empty or
            # thin `outputs`, so expand would start with nothing even though the
            # prior run persisted its worker outputs as evidence. Re-feed those
            # (current-run outputs win), bounded by the per-row char trim already
            # applied on record.
            exp_outputs = state.outputs
            if len(state.outputs) < _RESUME_OUTPUT_FLOOR:
                from studio.task_runs import outputs_from_evidence_rows
                # Walk newest→oldest for the first run that actually persisted
                # worker-output evidence: the newest row can be a failed_partial
                # snapshot recorded WITHOUT evidence (codex P2), which would
                # otherwise mask an older completed run's usable outputs.
                prior_outputs: dict[str, str] = {}
                for pr in reversed(state.store.all_runs(state.thash)):
                    prior_outputs = outputs_from_evidence_rows(pr.evidence)
                    if prior_outputs:
                        break
                if prior_outputs:
                    exp_outputs = {**prior_outputs, **state.outputs}
            pre_exp = state.scored_text or state.result_output or ""
            exp_text, exp_stats = expand_underdeveloped_sections(
                text=pre_exp,
                requirement=state.original_requirement,
                evidence_outputs=exp_outputs,
                verified_urls=state.verified_urls,
                required_sections=_active_template(state.session),
                chat=lambda p: getattr(
                    state.base_client.chat([{"role": "user", "content": p}]), "text", ""
                ) or "",
                rubric_score=exp_rubric,
            )
            _dbg(f"expand: stats={exp_stats} outputs={len(exp_outputs)}")
            if exp_stats["added"] and exp_text != pre_exp:
                state.scored_text = exp_text
                state.result_output = exp_text
                if state.art_file.exists():
                    state.scored_text = _write_artifact_through_sections(
                        state.session, state.effective_ws_root, state.scored_text,
                        state.original_requirement,
                    )
                    state.result_output = state.scored_text
                    _update_active_template_from_artifact(state.session, state.scored_text)
                    state.verified_urls = _verified_urls_from_cache(state.scored_text)
                runner._emit(GateEvent(
                    name="depth-expansion",
                    outcome="pass",
                    detail=f"added {exp_stats['added']} grounded paragraph(s) from under-used evidence",
                    sandboxed=True,
                ))
        except Exception as exp_exc:  # noqa: BLE001 — depth expansion is best-effort; never blocks publish
            _dbg(f"expand: EXCEPTION {type(exp_exc).__name__}: {exp_exc}")
    return state


def _pass_publish_gate(state: FinalizeState) -> FinalizeState:
    """Deterministic report publish gate: catches outputs that are structurally clean
    but fail the user's report contract (for example no citations or topic drift). It
    does not replace LLM planning; it only surfaces a final readiness verdict and
    feeds failures into the existing weakness/adjusted-score path."""
    runner = state.runner
    t_pub = time.monotonic()  # T1: publish-revision stage timer
    try:
        from studio.report_quality import (
            build_revision_evidence_text,
            combined_publish_issues,
            evaluate_publish_readiness,
        )
        from studio.publish_patch import apply_publish_patches, build_patch_prompt
        publish = evaluate_publish_readiness(
            state.original_requirement,
            state.scored_text or state.result_output or "",
            verified_urls=state.verified_urls or None,
            required_sections=_active_template(state.session),
        )
        evidence_text = build_revision_evidence_text(state.outputs)
        revision_issues = combined_publish_issues(
            publish, state.scored_text or state.result_output or "", evidence_text
        )
        if state.use_llm and revision_issues:
            if evidence_text:
                # §9 step 3: bounded fragment+anchor PATCHES, not a whole-doc rewrite.
                # The model names each defect and emits only the changed fragment; we
                # fuzzy-apply it. Whole-doc-shaped patches are rejected in
                # apply_publish_patches, so a weak model cannot smuggle a full
                # rewrite (the 21.4KB→9.5KB failure) through the patch channel.
                draft = state.scored_text or state.result_output or ""
                patch_prompt = build_patch_prompt(
                    state.original_requirement, draft, revision_issues, evidence_text
                )
                pr = state.base_client.chat([{"role": "user", "content": patch_prompt}])
                rev_text, pstats, unresolved = apply_publish_patches(
                    draft, getattr(pr, "text", "") or ""
                )
                # One bounded retry for unresolved anchors, then skip — no loop, so
                # the patch gate can never deadlock (§9 risk 1).
                if unresolved:
                    retry_prompt = (
                        patch_prompt
                        + "\n\nThese anchors were NOT found verbatim; re-emit "
                        "ONLY those patches with anchors copied EXACTLY from the "
                        "report:\n" + "\n".join(f"- {a[:120]}" for a in unresolved)
                    )
                    try:
                        pr2 = state.base_client.chat(
                            [{"role": "user", "content": retry_prompt}]
                        )
                        rev_text, pstats2, _ = apply_publish_patches(
                            rev_text, getattr(pr2, "text", "") or ""
                        )
                        pstats["applied"] += pstats2["applied"]
                    except Exception:  # noqa: BLE001 — retry is best-effort
                        pass
                rev_text = strip_satisfied_placeholders(
                    normalize_artifact(_strip_preamble(rev_text or "").strip())
                )
                if rev_text:
                    rev_verified = state.verified_urls
                    try:
                        rev_verified = _verified_urls_from_cache(rev_text)
                    except Exception:  # noqa: BLE001
                        pass
                    rev_publish = evaluate_publish_readiness(
                        state.original_requirement,
                        rev_text,
                        verified_urls=rev_verified or None,
                        required_sections=_active_template(state.session),
                    )
                    rev_issues = combined_publish_issues(rev_publish, rev_text, evidence_text)
                    # §9 step 1: a publish revision may only FIX defects, never
                    # shrink/de-cite/regress. Reject that class even when the
                    # rewrite is publish-clean (that is exactly how the
                    # 21.4KB→9.5KB loss slipped through). Gate error → NO-OP,
                    # never fail-open-accept (§9 risk 4).
                    rev_regress = _publish_revision_regressed(
                        state.scored_text or state.result_output or "",
                        rev_text,
                        required_sections=_active_template(state.session),
                        verified_pre=state.verified_urls,
                        verified_rev=rev_verified,
                    )
                    rev_ok = (not rev_issues) and (not rev_regress)
                    rg = GateEvent(
                        name="publish-revision",
                        outcome="pass" if rev_ok else "fail",
                        detail=(
                            "Report passed deterministic publish-readiness checks."
                            if rev_ok else "; ".join(
                                [*rev_issues, *(f"guard:{r}" for r in rev_regress)]
                            )
                        ),
                        sandboxed=True,
                    )
                    runner._emit(rg)
                    if rev_ok:
                        state.scored_text = rev_text
                        state.result_output = rev_text
                        state.verified_urls = rev_verified
                        publish = rev_publish
                        try:
                            if state.art_file.exists():
                                state.scored_text = _write_artifact_through_sections(
                                    state.session, state.effective_ws_root,
                                    state.scored_text, state.original_requirement,
                                )
                                state.result_output = state.scored_text
                                _update_active_template_from_artifact(state.session, state.scored_text)
                                state.verified_urls = _verified_urls_from_cache(state.scored_text)
                                publish = evaluate_publish_readiness(
                                    state.original_requirement,
                                    state.scored_text,
                                    verified_urls=state.verified_urls or None,
                                    required_sections=_active_template(state.session),
                                )
                        except Exception:  # noqa: BLE001
                            pass
        cited = add_missing_section_citations(
            state.scored_text or state.result_output or "",
            state.verified_urls or None,
        )
        if cited != (state.scored_text or state.result_output or ""):
            state.scored_text = cited
            state.result_output = cited
            publish = evaluate_publish_readiness(
                state.original_requirement,
                state.scored_text,
                verified_urls=state.verified_urls or None,
                required_sections=_active_template(state.session),
            )
            if state.art_file.exists():
                try:
                    state.scored_text = _write_artifact_through_sections(
                        state.session, state.effective_ws_root, state.scored_text,
                        state.original_requirement,
                    )
                    state.result_output = state.scored_text
                    _update_active_template_from_artifact(state.session, state.scored_text)
                except Exception:  # noqa: BLE001
                    pass
        residual_issues = combined_publish_issues(
            publish, state.scored_text or state.result_output or "", evidence_text
        )
        runner._last_publish_issues = tuple(residual_issues)
        pg = GateEvent(
            name="publish-ready",
            outcome="pass" if not residual_issues else "fail",
            detail=(
                "Report passed deterministic publish-readiness checks."
                if not residual_issues else "; ".join(residual_issues)
            ),
            sandboxed=True,
        )
        runner._emit(pg)
        if residual_issues:
            seen_w = set(state.weaknesses)
            state.weaknesses = [w for w in residual_issues if w not in seen_w] + state.weaknesses
    except Exception as pub_err:  # noqa: BLE001 — publish gate must never break recording
        # P2-b: a gate ERROR is a no-op (prior text kept), but make it OBSERVABLE — a
        # silently swallowed exception looks like "gate passed". Emit a failed event
        # so no-op is visible.
        try:
            runner._emit(GateEvent(
                name="publish-ready",
                outcome="fail",
                detail=f"publish gate error (kept prior text): {pub_err}",
                sandboxed=True,
            ))
        except Exception:  # noqa: BLE001 — telemetry must not raise
            pass
    runner._stage_add("publish", t_pub)
    return state


def _pass_prune_resolved_weaknesses(state: FinalizeState) -> FinalizeState:
    """Finalization/revision can repair defects after the miner/linter already
    recorded them. Prune resolved deterministic lint strings against the exact
    artifact that will be scored and served.

    See the module FAULT-PROPAGATION NOTE: this call had no self-contained
    try/except pre-extraction."""
    if state.weaknesses:
        state.weaknesses = _prune_resolved_weaknesses(
            state.weaknesses, state.scored_text or state.result_output or ""
        )
    return state


def _pass_requirement_compliance(state: FinalizeState) -> FinalizeState:
    """studio.requirement_compliance: generic check that the FINAL artifact satisfies
    the EXPLICIT requirements literally stated in the task ("include a diagram",
    "cite at least 3 sources", "under 800 words", ...). Nothing here is
    keyword-specific: the model extracts the requirements from the task text (ONCE
    per run, cached) and verifies them against this epoch's assembled artifact (once
    per epoch). Misses feed the editor weakness list (so the editor can repair them
    in-place this same epoch) AND the recorded score penalty, mirroring
    studio.relevance. Fail-open."""
    runner = state.runner
    runner._epoch_compliance_penalty = 0.0
    runner._epoch_compliance_issues = []
    runner._epoch_quality_opportunities = []
    # L1: distinguishes "verified: nothing found" from "could NOT verify" (judge
    # outage). Only the latter degrades the recorded run to status="unverified" so a
    # blip drops seed-eligibility instead of faking a clean `completed`.
    runner._epoch_compliance_unavailable = False
    if state.base_client is not None:
        try:
            from studio.requirement_compliance import (
                ComplianceCheckUnavailable,
                extract_requirements,
                requirement_compliance_issues,
            )
            if runner._task_requirements is None:
                runner._task_requirements = extract_requirements(
                    state.base_client, state.original_requirement
                )
            # No extracted requirements ⇒ nothing checkable — NOT an outage. Skip the
            # strict call so a requirement-free task never reads as "could-not-verify".
            if runner._task_requirements:
                try:
                    comp_pen, comp_issues, comp_opps = requirement_compliance_issues(
                        state.base_client,
                        runner._task_requirements,
                        state.scored_text or state.result_output or "",
                        strict=True,
                    )
                except ComplianceCheckUnavailable as exc:
                    # Judge down / unparseable reply: dock SKIPPED (score stays the
                    # deterministic rubric), status carries the "unverified" flag.
                    runner._epoch_compliance_unavailable = True
                    _dbg(
                        "compliance: could-not-verify (judge down: "
                        f"{exc}) — dock skipped, status→unverified"
                    )
                    return state
                runner._epoch_compliance_penalty = comp_pen
                runner._epoch_compliance_issues = comp_issues
                # OR-sibling opportunities NEVER touch the penalty/hard-issue list —
                # they only ride into the editor as optional polish.
                runner._epoch_quality_opportunities = comp_opps
        except Exception as exc:  # noqa: BLE001 — a code bug here is NOT a judge
            # outage: fail open (dock skipped) but do NOT flag unverified. Log so a
            # genuinely broken compliance path leaves a trace (no silent swallow).
            _dbg(f"compliance: non-outage exception (fail-open, status unchanged): {exc!r}")
    return state


def _pass_editor(state: FinalizeState) -> FinalizeState:
    """Editor phase (goal-aware final quality pass, includes the diagram/table/list
    presentation retry). Runs ONCE per hill-climb epoch here — after this epoch's
    deterministic section-assembly, before the score/record below (whose rubric this
    reuses). Fixes weaknesses/lint via scoped patch_artifact tool calls only (no
    whole-doc echo), <=2 rounds, FULL revert on regression (artifact.md +
    sections/*.md + active_outline.json). Fail-open."""
    runner = state.runner
    t_editor = time.monotonic()  # T1: editor-pass stage timer
    try:
        edited, editor_weaknesses = _run_editor_pass(
            session=state.session,
            base_client=state.base_client,
            scored_text=state.scored_text or "",
            verified_urls=state.verified_urls,
            effective_ws_root=state.effective_ws_root,
            art_file=state.art_file,
            original_requirement=state.original_requirement,
            emit=runner._emit,
            workspace_root=runner._workspace_root,
            on_tool_call=runner._emit_tool_call,
            on_tool_result=runner._emit_tool_result,
            step_id_getter=lambda: "editor",
            # Both precomputed-upstream weakness sources (relevance + compliance)
            # union into the editor's issue list; both penalties thread through the
            # same precomputed-float param (clamped in rubric_score) so the editor's
            # score oracle matches the record.
            extra_issues=(runner._epoch_relevance_issues or [])
            + (runner._epoch_compliance_issues or []),
            relevance_penalty=runner._epoch_relevance_penalty
            + runner._epoch_compliance_penalty,
            # Optional OR-sibling polish, threaded SEPARATELY from the hard weakness
            # list. _recount_opportunities re-verifies the artifact for the editor's
            # narrow soft-accept tie-breaker; it only runs when opportunities exist
            # (see _run_editor_pass), so the normal path pays no extra compliance call.
            quality_opportunities=runner._epoch_quality_opportunities,
            opportunity_recount=(
                runner._make_opportunity_recount(state.base_client)
                if (runner._epoch_quality_opportunities
                    or runner._epoch_compliance_issues)
                else None
            ),
            # A2 diagram grounding uses the pipeline's BGE-M3 embedder for the
            # SEMANTIC fabrication guard; None → the guard falls open (accept gate is
            # the backstop).
            embedder=runner._embedder,
            # Strong-model judge for presentation detection (form/diagram warrant).
            judge_client=state.judge_client,
        )
        if edited and edited != state.scored_text:
            state.scored_text = edited
            state.result_output = edited
            state.verified_urls = _verified_urls_from_cache(state.scored_text)
        # The editor pass, when it ran (editor_weaknesses is not None), computed the
        # FRESH post-editor-phase weakness list (rubric + lint) against whatever
        # state actually resulted — improved or reverted. That list REPLACES the
        # pre-editor-phase one wholesale (never a stale carry forward): this IS what
        # gets recorded for this epoch and handed to the next epoch's planner via the
        # HillClimbEvent/TaskRunStore below. A `None` means the editor never ran
        # (gated off) — leave weaknesses as the earlier miner/lint/gate pipeline
        # already produced.
        if editor_weaknesses is not None:
            state.weaknesses = editor_weaknesses
    except Exception:  # noqa: BLE001 — editor pass must never crash recording
        pass
    runner._stage_add("editor", t_editor)
    return state


def _pass_evidence_export(state: FinalizeState) -> FinalizeState:
    """Render the evidence matrix panel from parsed findings and emit it."""
    runner = state.runner
    try:
        from studio.evidence import evidence_from_findings, render_evidence_matrix
        from studio.findings import _parse_findings

        findings = []
        for out in state.outputs.values():
            findings.extend(_parse_findings(out))
        evidence_items = evidence_from_findings(
            findings, state.scored_text or state.result_output or ""
        )
        state.evidence_rows = [item.to_dict() for item in evidence_items]
        runner._last_evidence_count = len(state.evidence_rows)
        runner._last_weak_evidence_count = sum(
            1 for row in state.evidence_rows if row.get("status") == "weak"
        )
        runner._last_evidence_matrix = render_evidence_matrix(evidence_items)
        runner._emit(EvidenceEvent(
            items=state.evidence_rows,
            matrix=runner._last_evidence_matrix,
        ))
    except Exception:  # noqa: BLE001 - evidence export is best-effort
        pass
    return state


# --------------------------------------------------------------------------- #
# L1 unified editorial gate (§5.2.4) — a READ-ONLY pass that emits per-row
# verdicts + evidence and NEVER mutates content. MINOR/MAJOR "fixes" are the
# existing deterministic passes / next-epoch weakness seeds; REJECT rides status.
# The row-computing core is a pure function (no Runner / FinalizeState) so the
# offline probe (backend/tmp/editorial_probe.py) can drive it directly.
# --------------------------------------------------------------------------- #

_CITE_MARKER_RE = re.compile(r"\[([1-9]\d*)\]")  # citation markers start at [1]; [0] is code indexing
#: A References-list entry: `[1] ...`, `1. ...`, or `- [1] ...` at line start.
_REF_ENTRY_RE = re.compile(r"(?m)^\s*(?:-\s*)?(?:\[(\d+)\]|(\d+)\.)\s+\S")


def _references_marker_diff(text: str) -> dict[str, list[int]] | None:
    """Both-ways consistency between References-list entry numbers and the inline
    ``[N]`` citation markers in the body. ``None`` when there is no References
    section (caller ⇒ could_not_verify, never a false fail). Number-based because
    research_first's ASSEMBLE renders inline URLs as ``[N]`` markers (raw URLs are
    de-stuffed), so a raw-URL compare would false-fail every real artifact."""
    from studio.artifact_text import _REFERENCES_HEADING_RE
    m = _REFERENCES_HEADING_RE.search(text or "")
    if not m:
        return None
    from studio.textutil import mask_fenced_code
    # Mask fenced code in the body before scanning for markers: `arr[0]`/`list[2]`
    # inside a code block are indexing, not citations, and would false-fail E5.
    body, refs = mask_fenced_code(text[: m.start()]), text[m.start():]
    ref_nums = {int(a or b) for a, b in _REF_ENTRY_RE.findall(refs)}
    body_nums = {int(n) for n in _CITE_MARKER_RE.findall(body)}
    return {
        "orphan": sorted(ref_nums - body_nums),      # listed, never cited
        "dangling": sorted(body_nums - ref_nums),    # cited marker, no entry
    }


def _row(name: str, verdict: str, evidence: str, *, required: bool = False) -> dict[str, Any]:
    return {"row": name, "verdict": verdict, "evidence": evidence, "required": required}


def compute_editorial_rows(
    *,
    text: str,
    required_sections: list[str] | None,
    coverage: dict | None,
    rebuild_generated: bool,
) -> list[dict[str, Any]]:
    """Pure editorial gate: return one ``{row, verdict, evidence, required}`` per
    check. ``verdict`` ∈ {"pass","fail","could_not_verify"}. Every row body is
    wrapped so a raised check degrades to ``could_not_verify`` — a broken check
    NEVER records a pass and NEVER breaks the run. ``coverage=None`` means the
    coverage ledger is absent (E3 ⇒ could_not_verify). Generic over any task."""
    from studio.artifact_lint import lint_artifact
    from studio.rubric import sections_present
    from studio.artifact_lint import _stub_section_issues

    rows: list[dict[str, Any]] = []
    txt = text or ""
    lints: list[str] = []
    lint_ok = True  # False ⇒ lint_artifact RAISED; empty `lints` then means
    # "could not verify", NOT "clean" — E6/E11 must not read it as a pass.
    try:
        lints = lint_artifact(txt)
    except Exception as exc:  # noqa: BLE001
        lint_ok = False
        _dbg(f"editorial[lint]: EXCEPTION {exc!r}")

    # E1 — required sections present (deterministic, full-text).
    try:
        req = [s for s in (required_sections or []) if s and s.strip()]
        if not req:
            rows.append(_row("E1", "pass", "no required sections specified"))
        else:
            present = set(sections_present(txt, req))
            missing = [s for s in req if s not in present]
            rows.append(_row("E1", "pass" if not missing else "fail",
                             "all sections present" if not missing
                             else f"missing sections: {missing}"))
    except Exception as exc:  # noqa: BLE001
        _dbg(f"editorial[E1]: EXCEPTION {exc!r}")
        rows.append(_row("E1", "could_not_verify", f"exception: {exc!r}"))

    # E2 — no stub sections (thin non-structural bodies).
    try:
        stubs = _stub_section_issues(txt)
        rows.append(_row("E2", "pass" if not stubs else "fail",
                         "no stub sections" if not stubs else f"stubs: {stubs}"))
    except Exception as exc:  # noqa: BLE001
        _dbg(f"editorial[E2]: EXCEPTION {exc!r}")
        rows.append(_row("E2", "could_not_verify", f"exception: {exc!r}"))

    # E3 — every subject cited-in-artifact OR declared not-found. Reads the P1
    # coverage ledger. Missing/empty ledger ⇒ could_not_verify (never a pass).
    # Required for status ONLY on a research_first run, where coverage.json is a
    # write contract, so its absence is a real verification gap (not legacy-path).
    try:
        if not coverage:
            rows.append(_row("E3", "could_not_verify", "coverage.json missing/empty",
                             required=rebuild_generated))
        else:
            residual = [
                s for s, r in coverage.items()
                if s != "__joint__" and isinstance(r, dict)
                and int(r.get("cited_in_artifact", 0) or 0) <= 0
                and int(r.get("sources_fetched", 0) or 0) > 0
            ]
            rows.append(_row("E3", "pass" if not residual else "fail",
                             "all subjects cited-or-declared" if not residual
                             else f"uncited subjects with sources: {residual}"))
    except Exception as exc:  # noqa: BLE001
        _dbg(f"editorial[E3]: EXCEPTION {exc!r}")
        rows.append(_row("E3", "could_not_verify", f"exception: {exc!r}"))

    # E4 — citations resolve (grounding). research_first ASSEMBLE unconditionally
    # runs the artifact-wide ungrounded-sentence drop before returning, so a
    # rebuild_generated artifact is grounded BY CONSTRUCTION (that code path
    # provably executed). Absent that construction guarantee ⇒ could_not_verify
    # (claims set is not available here to re-detect). REJECT residue is enforced
    # upstream at ASSEMBLE; a "fail" here would ride status→rejected (P1b).
    try:
        if rebuild_generated:
            rows.append(_row("E4", "pass",
                             "grounded-by-construction (ASSEMBLE artifact-wide drop)"))
        else:
            rows.append(_row("E4", "could_not_verify",
                             "no grounding construction guarantee (non-research_first path)"))
    except Exception as exc:  # noqa: BLE001
        _dbg(f"editorial[E4]: EXCEPTION {exc!r}")
        rows.append(_row("E4", "could_not_verify", f"exception: {exc!r}"))

    # E5 — References set-consistent both ways (orphan + dangling markers).
    try:
        diff = _references_marker_diff(txt)
        if diff is None:
            rows.append(_row("E5", "could_not_verify", "no References section"))
        elif not diff["orphan"] and not diff["dangling"]:
            rows.append(_row("E5", "pass", "references match body citations both ways"))
        else:
            rows.append(_row("E5", "fail",
                             f"orphan refs {diff['orphan']}, dangling markers {diff['dangling']}"))
    except Exception as exc:  # noqa: BLE001
        _dbg(f"editorial[E5]: EXCEPTION {exc!r}")
        rows.append(_row("E5", "could_not_verify", f"exception: {exc!r}"))

    # E6 — structural validity (mermaid / table / fence). Subset of the lint list.
    # Gated on lint_ok: a raised lint check leaves lints=[] (indistinguishable from
    # clean), so record could_not_verify rather than a false pass.
    try:
        if not lint_ok:
            rows.append(_row("E6", "could_not_verify",
                             "lint check raised — structural validity unverifiable"))
        else:
            struct = [w for w in lints
                      if any(k in w.lower() for k in ("mermaid", "table", "fence", "code"))]
            rows.append(_row("E6", "pass" if not struct else "fail",
                             "structural blocks valid" if not struct else f"structural lints: {struct}"))
    except Exception as exc:  # noqa: BLE001
        _dbg(f"editorial[E6]: EXCEPTION {exc!r}")
        rows.append(_row("E6", "could_not_verify", f"exception: {exc!r}"))

    # E11 — lint clean (the full deterministic content-validity list). Same lint_ok
    # gate: no pass when the check could not run.
    if not lint_ok:
        rows.append(_row("E11", "could_not_verify",
                         "lint check raised — content validity unverifiable"))
    else:
        rows.append(_row("E11", "pass" if not lints else "fail",
                         "lint clean" if not lints else f"lint: {lints}"))
    return rows


def _editorial_run_status(rows: list[dict[str, Any]], compliance_unavailable: bool) -> str:
    """Verdict router → recorded run status. REJECT (E4 residue) → "rejected";
    a judge outage OR a required row that could-not-verify → "unverified"; else
    "completed" (MINOR/MAJOR fails still record completed — the fix is the
    next-epoch weakness seed, never a faked pass and never a 0.0).

    Empty ``rows`` means the gate itself could-not-run (crashed before emitting any
    row) — that is an UNVERIFIED run, never a silent "completed" with a full score
    (which would be seed-eligible and poison the lineage median)."""
    if not rows:
        return "unverified"
    # NOTE: E4-fail→"rejected" is currently unreachable (compute_editorial_rows E4
    # only emits pass/could_not_verify; residue-REJECT is enforced upstream at
    # ASSEMBLE). If E4-fail is ever wired here, ensure an all-rejected lineage cannot
    # cold-start latest_with_content past the keep/discard anti-regression gate
    # (task_runs seed-exclusion of "rejected" must keep at least one servable ancestor).
    if any(r.get("row") == "E4" and r.get("verdict") == "fail" for r in rows):
        return "rejected"
    if compliance_unavailable:
        return "unverified"
    if any(r.get("verdict") == "could_not_verify" and r.get("required") for r in rows):
        return "unverified"
    return "completed"


def _editorial_fail_weaknesses(rows: list[dict[str, Any]]) -> list[str]:
    """Fail-row evidence as weakness strings → the existing next-epoch seed path."""
    return [f"[editorial:{r['row']}] {r['evidence']}"
            for r in rows if r.get("verdict") == "fail"]


def _pass_editorial_gate(state: FinalizeState) -> FinalizeState:
    """L1 read-only editorial gate. Computes per-row verdicts against the FINAL
    artifact + coverage ledger, stashes them on the runner for the record pass
    (status + weakness seed), and durably emits ``editorial_rows.json`` beside
    coverage.json. Never mutates content; fail-open as a whole."""
    runner = state.runner
    runner._editorial_rows = []
    try:
        import json
        ws_dir = state.effective_ws_root / state.session.session_id
        coverage: dict | None = None
        cov_path = ws_dir / "coverage.json"
        if cov_path.exists():
            try:
                parsed = json.loads(cov_path.read_text(encoding="utf-8"))
                coverage = parsed if isinstance(parsed, dict) else None
            except Exception as exc:  # noqa: BLE001
                _dbg(f"editorial: coverage.json unreadable {exc!r}")
        rows = compute_editorial_rows(
            text=state.scored_text or state.result_output or "",
            required_sections=_scoring_template(state.session),
            coverage=coverage,
            rebuild_generated=state.rebuild_generated,
        )
        runner._editorial_rows = rows
        status = _editorial_run_status(
            rows, getattr(runner, "_epoch_compliance_unavailable", False)
        )
        verdict = {r["row"]: r["verdict"] for r in rows}
        _dbg(f"editorial_gate: status={status} rows={verdict}")
        try:
            ws_dir.joinpath("editorial_rows.json").write_text(
                json.dumps({"status": status, "rows": rows}, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001 — durable emit is best-effort
            _dbg(f"editorial_gate: rows.json write fail-open {exc!r}")
    except Exception as exc:  # noqa: BLE001 — a bad gate must never break the run
        _dbg(f"editorial_gate: EXCEPTION {exc!r}")
    return state


def _pass_score_scorecard_and_record(state: FinalizeState) -> FinalizeState:
    """Recorded score = deterministic RUBRIC over the FINAL (post-gate) artifact —
    the metric that actually tracks quality (DESIGN §14.2). Computed from the clean
    scored_text BEFORE the weakness annotation is appended, so the score is not
    polluted by it. Weights + template come from the GUI rubric_config. Then:
    record the TaskRun, save a template skeleton on a decent score, compute
    delta/status, and emit the epoch's HillClimbEvent.

    See the module FAULT-PROPAGATION NOTE: this whole block had no self-contained
    try/except pre-extraction — a raise anywhere in here used to silently abort the
    DB record and the HillClimbEvent emit entirely."""
    if not state.mined:
        _dbg(
            "score_scorecard_and_record: SKIPPED — score_and_mine_weaknesses did not "
            "complete this epoch (no DB row, no HillClimbEvent; preserves the "
            "pre-extraction abort contract)"
        )
        return state
    from studio.rubric import (
        adjusted_score,
        rubric_score,
        rubric_scorecard_100,
        scorecard_weaknesses,
    )
    from studio.task_runs import TaskRun, evidence_rows_from_outputs

    runner = state.runner
    rcfg = getattr(state.session, "rubric_config", None) or {}
    score_template = _scoring_template(state.session)
    # relevance_penalty (studio.relevance) was computed ONCE this epoch, upstream in
    # _run_phase_loop (gated on a cross-task seed) — threaded here as a plain
    # precomputed float so this scoring call stays pure/deterministic.
    rubric_base = rubric_score(
        state.scored_text or state.result_output or "",
        verified_urls=state.verified_urls or None,
        weights=rcfg.get("weights"),
        required_sections=score_template,
        relevance_penalty=runner._epoch_relevance_penalty,
        compliance_penalty=runner._epoch_compliance_penalty,
    )
    scorecard_100 = rubric_scorecard_100(
        state.scored_text or state.result_output or "",
        verified_urls=state.verified_urls or None,
        required_sections=score_template,
        scoring_matrix=rcfg.get("scoring_matrix"),
        weights=rcfg.get("weights"),
        relevance_penalty=runner._epoch_relevance_penalty,
        compliance_penalty=runner._epoch_compliance_penalty,
    )
    runner._last_scorecard_100 = scorecard_100
    weaknesses = state.weaknesses
    scorecard_weak = scorecard_weaknesses(scorecard_100, score_template)
    if scorecard_weak:
        seen_w = set(weaknesses)
        weaknesses = [w for w in scorecard_weak if w not in seen_w] + weaknesses
    # Surface relevance issues (studio.relevance) even when the editor pass was
    # gated off (no scoring matrix / tools disabled) — the editor path already folds
    # these into weaknesses via editor_weaknesses above; dedup makes this a no-op
    # there. rubric_base already reflects relevance_penalty separately.
    if runner._epoch_relevance_issues:
        seen_rw = set(weaknesses)
        weaknesses = weaknesses + [
            w for w in runner._epoch_relevance_issues if w not in seen_rw
        ]
    # Surface requirement-compliance misses (studio.requirement_compliance) in the
    # recorded weakness list too — the editor path already folds these via
    # editor_weaknesses above; dedup makes this a no-op there. rubric_base already
    # reflects compliance_penalty separately.
    if runner._epoch_compliance_issues:
        seen_cw = set(weaknesses)
        weaknesses = weaknesses + [
            w for w in runner._epoch_compliance_issues if w not in seen_cw
        ]
    # §14.7: the structural rubric measures QUANTITY (sections, URLs, words) and
    # saturates at 1.0 while real defects remain — it scored 1.0 on a report with a
    # malformed mermaid, fabricated URLs, and zero inline citations. Couple the
    # recorded score to the FINAL weaknesses so an open defect can never read as a
    # perfect score, and a doc with fewer/less-severe weaknesses scores higher.
    score = adjusted_score(rubric_base, weaknesses)
    # L1: editorial fail rows seed the NEXT epoch (they ride the same weakness list
    # as compliance/relevance above) WITHOUT re-docking THIS recorded score —
    # verifiability rides `status`, never the number. Appended after adjusted_score
    # so the deterministic rubric stays the recorded value.
    editorial_rows = getattr(runner, "_editorial_rows", None) or []
    ed_fails = _editorial_fail_weaknesses(editorial_rows)
    if ed_fails:
        seen_ew = set(weaknesses)
        weaknesses = weaknesses + [w for w in ed_fails if w not in seen_ew]
    state.weaknesses = weaknesses
    # Remaining weaknesses are surfaced BELOW the report in the result view via the
    # HillClimbEvent.weaknesses emitted below (the frontend renders them) — they
    # must NOT be concatenated into result_output, which IS the deliverable
    # document (saved, downloaded, recorded as result_text). Keeping them out keeps
    # the report clean and keeps the next-run seed uncontaminated.
    # Atomic allocate+insert (finding 3): next_version()+record() as two calls let
    # two concurrent runs of this task claim the same version.
    # L1: run lifecycle status — REJECT(E4)→rejected, judge-outage / required
    # could-not-verify → unverified, else completed. Non-completed rows are
    # auto seed-excluded (task_runs._seed_ineligible_reason), so a blip degrades
    # seed-eligibility, never the recorded number (no 0.0 poisons the lineage).
    run_status = _editorial_run_status(
        editorial_rows, getattr(runner, "_epoch_compliance_unavailable", False)
    )
    version = state.store.record_versioned(
        TaskRun(
            task_hash=state.thash,
            session_id=state.session.session_id,
            version=0,  # allocated atomically inside record_versioned
            score=score,
            status=run_status,
            weaknesses=weaknesses,
            artifact_path=state.art_path,
            requirement=state.original_requirement,
            result_text=state.result_output,
            # §14.4: snapshot the effective hill-climb config so a later run of this
            # task can recover its epoch budget across backend restarts.
            config=state.hc_cfg or {},
            # Persist the raw worker outputs alongside the evidence matrix so a
            # resumed run can re-feed them to the depth-expansion stage (which
            # otherwise starts from evidence_json=[]). Bounded per-output.
            evidence=state.evidence_rows + evidence_rows_from_outputs(state.outputs),
            # Fix 2: True only when relevance_issues() ran this epoch (cross-task
            # seed). Lets future similar_runs() deprioritize pre-feature seeds.
            relevance_checked=runner._epoch_relevance_checked,
        )
    )
    # §4c: run-level REDUCER summary record — the goal-aware consolidator's final
    # output (mined new_weaknesses + recorded score + the handoff artifact path).
    # The per-phase reducer records carry the handoff; scoring/mining happen once
    # post-loop, so this is where new_weaknesses/score land.
    try:
        import json as json4c
        io4c = state.effective_ws_root / state.session.session_id / "agent_io.jsonl"
        if io4c.parent.exists():
            with io4c.open("a", encoding="utf-8") as f4c:
                f4c.write(json4c.dumps({
                    "role": "reducer", "step": "run-summary",
                    "output": {"new_weaknesses": weaknesses, "score": score,
                               "scorecard_100": scorecard_100,
                               "handoff_artifacts": [state.art_path]},
                    "tokens": 0,
                }) + "\n")
    except Exception:  # noqa: BLE001 — diagnostics must never break recording
        pass
    # Template reuse: save a decent report's heading SKELETON so the next
    # semantically-similar research can seed its first document from it (best-effort;
    # dedups identical skeletons; needs an embedder to be searchable).
    if score >= 0.6 and runner._embedder is not None:
        try:
            from studio.templates import TemplateStore, extract_skeleton
            TemplateStore(embedder=runner._embedder).save_template(
                state.original_requirement, extract_skeleton(state.result_output))
        except Exception:  # noqa: BLE001 — template save is non-critical
            pass
    prev_score = 0.0
    if version > 1:
        prev = state.store.all_runs(state.thash)
        if len(prev) >= 2:
            prev_score = prev[-2].score
    delta = score - prev_score
    min_delta = float(state.hc_cfg.get("min_improvement", 0.02))
    max_epochs = int(state.hc_cfg.get("max_epochs", 5))
    # PLAN item 8: status from the PER-RUN epoch index, not the cumulative version
    # (which fired "converged" mid-improvement on any task with history).
    status = _epoch_status(runner._epoch, delta, min_delta, max_epochs)
    # §14.4: hand this pass's outcome back to run() so it can drive the loop.
    state.outcome = EpochResult(version=version, score=score, delta=delta, status=status)
    runner._emit(
        HillClimbEvent(
            epoch=version,
            score=score,
            delta=delta,
            status=status,
            note=f"v{version} score={score:.2f}",
            weaknesses=weaknesses,
            task_hash=state.thash,
        )
    )
    return state


#: Ordered finalize pipeline — VERBATIM order from the pre-extraction
#: Runner._postrun_score_and_record (PLAN §2 S2). Do not reorder without checking
#: the FAULT-PROPAGATION NOTE and every pass's own dependency on prior passes'
#: fields (state.scored_text / state.result_output / state.verified_urls /
#: state.weaknesses accumulate across the list).
PASSES: list[tuple[str, PassFn]] = [
    ("normalize_dedupe", _pass_normalize_dedupe),
    ("materialize_artifact", _pass_materialize_artifact),
    ("synthesize_readability", _pass_synthesize_readability),
    ("repair_lints", _pass_repair_lints),
    ("neutralize_urls", _pass_neutralize_urls),
    ("structural_producer_l0", _pass_structural_producer),
    ("rebuild_references", _pass_rebuild_references),
    ("score_and_mine_weaknesses", _pass_score_and_mine_weaknesses),
    ("epoch_gate", _pass_epoch_gate),
    ("post_gate_finalize", _pass_post_gate_finalize),
    ("expand_underdeveloped", _pass_expand_underdeveloped),
    ("publish_gate", _pass_publish_gate),
    ("prune_resolved_weaknesses", _pass_prune_resolved_weaknesses),
    ("requirement_compliance", _pass_requirement_compliance),
    ("editor", _pass_editor),
    ("editorial_gate", _pass_editorial_gate),
    ("evidence_export", _pass_evidence_export),
    ("score_scorecard_and_record", _pass_score_scorecard_and_record),
]


#: Passes that MUTATE the document text via the seed-and-patch machinery —
#: skipped when ``state.rebuild_generated`` (PLAN §16): research_first's
#: ASSEMBLE stage already owns dedupe, references, fence/citation repair, and
#: structural (code/diagram) production, and its `editor`/`expand`/`publish_gate`
#: equivalents don't apply to a document that was never seeded. The recording/
#: scoring tail (materialize_artifact, score_and_mine_weaknesses, epoch_gate,
#: post_gate_finalize, prune_resolved_weaknesses, requirement_compliance,
#: evidence_export, score_scorecard_and_record) is deliberately NOT here.
_CONTENT_MUTATING_PASSES = frozenset({
    "normalize_dedupe",
    "synthesize_readability",
    "repair_lints",
    "neutralize_urls",
    "structural_producer_l0",
    "rebuild_references",
    "expand_underdeveloped",
    "publish_gate",
    "editor",
})


def run_passes(state: FinalizeState) -> FinalizeState:
    """Run every pass in :data:`PASSES` in order. Fail-open per pass: an exception is
    caught, logged via ``_dbg``, and execution continues to the next pass (never
    aborts the run) — see the module FAULT-PROPAGATION NOTE for the narrow set of
    passes where this is not byte-identical to the pre-extraction "one shared outer
    try" behavior.

    L2 (PLAN): tracks a per-pass "changed this epoch" ledger on the Runner instance
    (``runner._finalize_noop_streak``) — a pass that no-ops for >=3 CONSECUTIVE
    epochs in THIS run is flagged via ``_dbg`` so an inert pass is visible instead of
    indistinguishable from "nothing to do here" health."""
    runner = state.runner
    streaks: dict[str, int] | None = getattr(runner, "_finalize_noop_streak", None)
    if streaks is None:
        streaks = {}
        runner._finalize_noop_streak = streaks
    for name, fn in PASSES:
        if state.rebuild_generated and name in _CONTENT_MUTATING_PASSES:
            _dbg(f"finalize[{name}]: SKIP (rebuild_generated)")
            continue
        before_scored, before_output = state.scored_text, state.result_output
        try:
            state = fn(state)
            changed = (
                state.scored_text != before_scored
                or state.result_output != before_output
            )
            # fences= tracks structural-content survival across passes — added after
            # run v5 recorded ZERO fences despite L0's changed=True insertion, with
            # every intermediate pass reporting changed=False (the eraser was
            # invisible; static analysis could not resolve the contradiction).
            _dbg(
                f"finalize[{name}]: ran changed={changed} "
                f"fences={(state.scored_text or '').count('```')}"
            )
            streaks[name] = 0 if changed else streaks.get(name, 0) + 1
        except Exception as exc:  # noqa: BLE001 — a pass must never abort the pipeline
            _dbg(f"finalize[{name}]: EXCEPTION {exc!r}")
            streaks[name] = streaks.get(name, 0) + 1
        if streaks.get(name, 0) >= 3:
            _dbg(f"finalize[{name}]: inert {streaks[name]} consecutive epochs")
    return state
