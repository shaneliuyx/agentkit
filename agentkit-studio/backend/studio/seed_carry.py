"""Hill-climb seed carry-forward (extracted from the runner god module).

Decides how a continuation run seeds artifact.md from the latest lineage content —
prior-from-path, min-content gate, section-workspace sync. Behavior verbatim; carried
off Runner as a mixin resolved via MRO.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any


class SeedCarryForwardMixin:
    """Carries _seed_carry_forward off Runner (relocation, not redesign)."""

    if TYPE_CHECKING:  # self-contract: attributes the composing Runner provides
        _embedder: Any
        _workspace_root: Any
        _epoch: int

    def _seed_carry_forward(
        self, *, session, requirement: str, _base_requirement: str, _hc_cfg: dict,
        base_client=None,
    ) -> tuple[str, str, bool, object, int, str, bool, str]:
        """Hill-climb seed carry-forward (DESIGN §14.4 / §14.6 / §11.4).

        When auto_improve is on and a prior run exists for this task, copy its artifact
        into the current workspace, accumulate prior+similar-task weaknesses, and (when a
        real seed exists) switch the requirement to the patch-or-silent worker contract.
        Returns ``(requirement, _weaknesses_block, _artifact_copied, _eff_ws2, _seed_len,
        _seed_text, _seed_cross_task, _seed_topic)``. ``_seed_cross_task`` is True only
        when the seed came via R10 semantic similarity (a DIFFERENT task_hash) rather than
        this task's own exact-hash lineage — the signal that gates the relevance
        repair-clause/check below (studio.relevance): a same-task continuation seed carries
        no cross-topic contamination risk, so it must not pay for the extra per-section LLM
        calls. ``_seed_topic`` is that cross-task seed's ORIGINAL requirement text (empty
        otherwise) — it grounds the dynamic negative exemplar in studio.relevance's prompt.
        """
        # Late import (call-time) — no module-load cycle with studio.runner.
        from studio.workspace import Workspace
        from studio.textutil import dbg as _dbg
        from studio.artifact_text import _strip_preamble
        from studio.runner import (
            _pick_seed_with_content,
            _seed_prior_from_path,
            _sync_section_workspace,
            _use_research_first,
        )
        _artifact_copied = False
        _eff_ws2 = None
        _weaknesses_block = ""  # prior-run lessons → planner/hub constraints
        _seed_len = 0           # length of the seeded prior artifact (anti-regression)
        _seed_text = ""         # full prior artifact text (Phase-1 keep/discard gate)
        _seed_via_similarity = False  # cross-task R10 seed? (studio.relevance gate)
        _seed_topic = ""        # cross-task seed's ORIGINAL requirement (relevance exemplar)
        # §11.4 loop-closure check: normalized weaknesses recorded in >= REPEAT_LIMIT
        # prior runs of this task were injected and never fixed. The reducer drops
        # them from its handoff (below) instead of grinding on them forever. Empty
        # when not hill-climbing.
        _repeat_failed: set[str] = set()
        # research_first is COLD-START by design (D4 / _run_research_first_generation
        # docstring: "ignores any prior-artifact content"). Seeding it is not just
        # wasted — it actively corrupts: the seed is written to artifact.md AND split
        # into per-section files, and although the generator overwrites artifact.md
        # with its fresh output, the seed's section files survive and a downstream
        # merge pulls the prior lineage's relationship section back in (live v45–v47:
        # a stale "Architectural Comparison" section accumulated on top of the fresh
        # "Integrated Agentic Workflow", and References stopped being last). The
        # generator's raw output is clean (one relationship section, References last,
        # verified offline); the pollution is entirely this carry-forward. Skip it.
        if _hc_cfg.get("auto_improve") and not _use_research_first(session):
            from studio.task_runs import (
                TaskRunStore,
                base_identity as _base_identity,
                task_hash as _task_hash,
            )
            # PLAN item 5: hash the lineage base so a "Continue run" / chat-history re-run
            # seeds from the prior artifact instead of forking a new cold-start lineage.
            _thash = _task_hash(_base_identity(_base_requirement))
            # Pass the embedder so each run's requirement is embedded for R10
            # cross-task similarity retrieval (no-op when embedder is None).
            _store = TaskRunStore(embedder=self._embedder)
            _repeat_failed = _store.repeat_failures(_thash)
            # Use latest run with actual artifact content — LLM self-eval scores
            # are noisy; the most recent non-empty artifact has accumulated the
            # most incremental work and is the best hill-climb seed.
            from studio.workspace import workspace_root as _ws_root_fn3
            _eff_ws2 = self._workspace_root or _ws_root_fn3()
            # Explicit seed-file override (hill_climb_config.seed_path): point the
            # run at any artifact on disk, bypassing DB lookup. Takes precedence
            # over both exact-hash and semantic seeding — the escape hatch when a
            # weak same-task lineage would otherwise block a stronger seed.
            # ONLY on this run's first epoch (self._epoch <= 1) — epoch 2+ must
            # carry forward the PREVIOUS EPOCH'S OWN result (via latest_with_content
            # below, which _store.record already persisted at the end of the prior
            # epoch), or a multi-epoch hill-climb never improves: every epoch would
            # re-seed from the same static file and discard its own progress.
            _prior = (
                _seed_prior_from_path(
                    str(_hc_cfg.get("seed_path") or ""), _thash, requirement
                )
                if self._epoch <= 1
                else None
            )
            if _prior is not None:
                _dbg(f"seed via explicit seed_path ({len(_prior.result_text)} chars)")
            else:
                _prior = _store.latest_with_content(_thash, ws_root=_eff_ws2)
            _seed_via_similarity = False
            if _prior is None and self._embedder is not None:
                # No EXACT task_hash prior — e.g. a loop-seed rotates the requirement's
                # identity so the exact-key lookup misses and the run would cold-start.
                # Fall back to SEMANTIC search: seed from the most similar prior task's
                # artifact so a loop-seeded (or reworded) run IMPROVES the closest existing
                # report instead of regenerating from scratch. Only a genuinely novel task
                # (nothing clears the threshold) truly cold-starts. Threshold is stricter
                # than R10 weakness retrieval (0.35): seeding a whole artifact from a weakly
                # related task is worse than cold-starting, so require a close match.
                _SEED_SIM_THRESHOLD = 0.6
                _MIN_SEED_CHARS = 500  # skip empty/placeholder priors — seeding garbage is worse than cold
                _sims = _store.similar_runs(
                    requirement, self._embedder, k=8,
                    min_similarity=_SEED_SIM_THRESHOLD, exclude_hash=_thash,
                )
                # sims are similarity-desc; take the closest prior that actually has content
                # (many high-similarity rows are empty 0.0-score placeholder runs).
                _picked = _pick_seed_with_content(
                    _sims, _eff_ws2, _MIN_SEED_CHARS,
                    recency_fn=_store.session_recency,
                )
                if _picked is not None:
                    _prior, _sim_score = _picked
                    _seed_via_similarity = True
                    # The seed's OWN requirement is the real "different subject in
                    # the same broad field" — grounds studio.relevance's dynamic
                    # negative exemplar (EXAMPLE A) instead of a generic phrase.
                    _seed_topic = (_prior.requirement or "").strip()
                    _dbg(f"seed via semantic similarity: {_prior.session_id} "
                         f"(sim={_sim_score:.3f}, thash={_prior.task_hash}, score={_prior.score}) "
                         f"— no exact-hash prior")
            if _prior:
                _prior_art = _eff_ws2 / _prior.session_id / "artifact.md"
                _artifact_copied = False
                # Seed source: the prior session's on-disk artifact.md when it
                # survives (richest — the section-keyed handoff), ELSE the DB-
                # persisted result_text. The fallback is load-bearing: artifact.md
                # is a TRANSIENT working file written only when a run goes through
                # the reducer/patch path — a raw-synthesis run (e.g. an oMLX model
                # that dumped findings instead of patching sections) finalizes
                # result.md but NEVER writes artifact.md. The durable deliverable
                # is result.md == result_text in the DB, recorded for every run.
                # Keying the seed on artifact.md alone meant most priors had no
                # seed → _artifact_copied stayed False → the keep/discard gate
                # below was SKIPPED → a regressed epoch overwrote the served
                # deliverable with no protection (the hill-climb regression that
                # served a 0.12 stub over a 0.41 prior; DESIGN §14.6).
                _raw_seed: str | None = None
                if _prior_art.exists():
                    try:
                        _raw_seed = _prior_art.read_text()
                    except OSError:
                        _raw_seed = None
                if _raw_seed is None and (_prior.result_text or "").strip():
                    _raw_seed = _prior.result_text
                # COARSE WHOLE-DOC SEED GATE (studio.relevance.seed_doc_relevance):
                # ONE LLM call, cross-task seeds ONLY. A same-topic-different-hash
                # seed (loop-seed rehash, reworded requirement) is genuinely
                # reusable; a cross-FIELD R10 seed (catalog-management doc pulled
                # into a research-report task) contaminates. This gate summarizes
                # the whole seed + task and drops the seed on a NOT_RELATED verdict,
                # falling back to the cold-start blank template (the _artifact_copied
                # == False path below at §14.1). Additive to the per-section
                # relevance_issues() check, which still runs every epoch regardless.
                if _raw_seed is not None and _seed_via_similarity:
                    from studio.relevance import seed_doc_relevance
                    if not seed_doc_relevance(base_client, _raw_seed, requirement):
                        _dbg(f"seed dropped by coarse relevance gate (NOT_RELATED): "
                             f"{_prior.session_id} thash={_prior.task_hash} "
                             f"topic={_seed_topic[:60]!r} → cold-start blank template")
                        _raw_seed = None
                        _seed_via_similarity = False
                        _seed_topic = ""
                if _raw_seed is not None:
                    _curr_ws = Workspace(session.session_id, root=_eff_ws2)
                    # §11.4: SANITIZE inherited corruption first — an artifact a
                    # prior reducer poisoned with a commentary preamble would
                    # otherwise be locked in by the grow-only ratchet forever (a
                    # clean-up that shortens it reads as a regression). Strip on seed
                    # so _seed_len is the CLEAN baseline the run grows from. Seed the
                    # current workspace's artifact.md so the run edits rather than
                    # regenerates, and _seed_text feeds the Phase-1 keep/discard gate.
                    try:
                        _seed_clean = _strip_preamble(_raw_seed)
                        (_curr_ws.root / "artifact.md").write_text(_seed_clean)
                        # CRITICAL: split the seed into per-section files too, exactly
                        # like the cold-start skeleton path (see _sync_section_workspace
                        # at the skeleton bootstrap below). The section files are the
                        # SOURCE OF TRUTH — the phase loop rebuilds artifact.md via
                        # assemble_artifact_from_sections. Without this sync the seed
                        # lived ONLY in artifact.md while the section files held stale
                        # scaffold, so the first assemble silently collapsed a 22K seed
                        # to ~5K in epoch 1 (round-1 shrink). Round-trip is lossless
                        # (split_artifact_to_sections keeps every heading, template or
                        # not), so this preserves the full seed and the accept_rewrite
                        # ratchet then has real content to protect.
                        _sync_section_workspace(session, _eff_ws2, _seed_clean)
                        try:
                            from studio.section_workspace import assemble_artifact_from_sections
                            _asm_dbg = assemble_artifact_from_sections(_curr_ws.root)
                            _dbg(f"seed-sync assembled={len(_asm_dbg)} "
                                 f"h1={_asm_dbg.count(chr(10)+'# ')} h2={_asm_dbg.count(chr(10)+'## ')}")
                        except Exception:  # noqa: BLE001 — diagnostic only
                            pass
                        _artifact_copied = True
                        _seed_len = len(_seed_clean)
                        _seed_text = _seed_clean  # Phase-1 gate: prior best to beat
                    except OSError:
                        _seed_len = 0
                        _seed_text = ""
                # Accumulate weaknesses from this task's prior runs AND from
                # semantically SIMILAR prior tasks (R10) — every failure lesson,
                # including cross-task ones, carries forward. Deduplicated by
                # exact string; exact-task lessons rank first. Degrades to
                # exact-task-only when no embedder is available.
                # Cap to the top-N most relevant lessons (exact-task first). The
                # full accumulated set across many prior runs can be dozens of
                # items; injecting all of them bloats the requirement and makes
                # the planner explode each lesson into its own phase. 10 is plenty
                # of signal without overwhelming the plan.
                _MAX_INJECTED_WEAKNESSES = 10
                _all_weaknesses = _store.accumulated_weaknesses(
                    requirement, _thash, embedder=self._embedder,
                )[:_MAX_INJECTED_WEAKNESSES]
                # Weaknesses are CONSTRAINTS for the planner/hub (quality bar to
                # meet), NOT tasks to decompose — threaded via _weaknesses_block
                # into _plan_from_epics so epic planning treats them correctly,
                # and onto session.weaknesses so each phase hub sees them too.
                _weaknesses_block = "\n".join(f"- {w}" for w in _all_weaknesses)
                session.weaknesses = _all_weaknesses  # hub reads getattr(session,"weaknesses")
                _fix_items = "\n".join(
                    f"  {i+1}. {w}" for i, w in enumerate(_all_weaknesses)
                )
                # Workers use web_search/web_fetch only — no write_file tool. Multiple
                # workers run concurrently; writing a shared artifact.md would cause
                # conflicts. Each worker's TEXT OUTPUT is its "temp file": the runner
                # collects outputs[step.id] = sr.output and the reducer receives all
                # of them via upstream context. RESEARCH_FINDING blocks let the reducer
                # apply each finding independently.
                #
                # Prompt structure: imperative tool-call instruction FIRST, schema
                # SECOND. "FIND AND OUTPUT" framing causes narration (model says "I'll
                # search" but never calls the tool). "Use web_search tool right now"
                # triggers actual tool_call responses the loop can execute.
                # Gate the patch-or-silent EDIT contract on a real seed, not just on
                # a prior RUN existing. PATCH_TARGET ("a section heading in the
                # artifact") + "find nothing → output NOTHING, the reducer keeps the
                # existing doc" only make sense when there IS a seeded doc. Injected
                # without one (prior run recorded but its artifact.md never written),
                # a weak model is told to patch a phantom: most workers go silent →
                # the reducer has no base → a 28-line scrap dump that scores BELOW a
                # clean from-scratch run (v2 0.12 < v1 0.41). With no seed, fall back
                # to normal generation; weaknesses still steer the planner softly via
                # _weaknesses_block / session.weaknesses set above (DESIGN §14.6).
                if _fix_items and _artifact_copied:
                    _finding_schema = (
                        "## RESEARCH_FINDING\n"
                        "ARTICLE_TITLE: <exact title>\n"
                        "URL: https://<exact URL — required>\n"
                        "POPULARITY: <verifiable signal: top-N result, N shares, N citations>\n"
                        "PUBLICATION: <date or unknown>\n"
                        "KEY_INSIGHT: <one sentence relevant to the task>\n"
                        "PATCH_TARGET: <exact article name or section heading in the artifact>\n"
                    )
                    requirement = (
                        f"{requirement}\n\n"
                        f"Use the web_search tool right now to find the following missing data:\n"
                        f"{_fix_items}\n\n"
                        f"For each item found, output a RESEARCH_FINDING block:\n"
                        f"{_finding_schema}\n"
                        f"Call web_search immediately.\n\n"
                        f"WORKER CONTRACT (DESIGN §11.2) — patch-or-silent:\n"
                        f"  - Found sourced content (with a real URL) → output a RESEARCH_FINDING.\n"
                        f"  - Found nothing → output NOTHING. Do NOT write a sentence explaining\n"
                        f"    why (no 'web search unavailable', no 'I could not find...'). Silence\n"
                        f"    means 'no change' — the reducer keeps the existing doc as-is.\n"
                        f"  - End with exactly ONE status line:\n"
                        f"      SEARCH: ok      (the search tool worked, whatever it returned)\n"
                        f"      SEARCH: error   (the search tool itself failed — quota/timeout/down)\n"
                        f"  URL is required in every RESEARCH_FINDING. Failure-narration is forbidden."
                    )
        return (
            requirement, _weaknesses_block, _artifact_copied, _eff_ws2,
            _seed_len, _seed_text, _seed_via_similarity, _seed_topic,
        )
