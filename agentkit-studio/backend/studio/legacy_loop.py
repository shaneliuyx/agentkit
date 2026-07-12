"""Legacy seed-and-patch generation loop (rollback path).

Quarantined verbatim from runner.py — this is the pre-research_first generator, kept
as the operational rollback (STUDIO_DISABLE_RESEARCH_FIRST). The active default is
research_first; behavior/event-ordering here are unchanged from the extracted original.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation-only; the runtime deps come via a late import inside the method
    from studio.events import GateEvent


class LegacyLoopMixin:
    """Carries the legacy _run_phase_loop off the Runner god class (relocation, not redesign).

    Runner inherits this; the method stays a normal `self.`-bound method resolved via MRO.
    """

    def _run_phase_loop(
        self,
        *,
        session,
        plan_obj,
        client,
        base_client,
        budget,
        _ledger,
        _sizing_cfg,
        _lc,
        _eff_ws2,
        _artifact_copied: bool,
        _seed_len: int,
        _seed_text: str,
        use_llm: bool,
        requirement: str,
        mem,
        dag,
        selfimp,
        outputs: dict[str, str],
        gate_events: list[GateEvent],
        _reducer_gaps: list[str],
        _seed_cross_task: bool = False,
        _seed_topic: str = "",
        base_requirement: str = "",
    ) -> tuple[bool, str, str]:
        """Per-phase execution loop + the post-loop atomic patch-apply
        (DESIGN §3 / §5 / §11). Drives each phase through ``run_plan`` on a single-step
        sub-plan, emits the per-phase event sequence, writes back the grow-only artifact,
        then folds worker PATCHES/RESEARCH_FINDING blocks into artifact.md. Mutates
        ``outputs`` / ``gate_events`` / ``_reducer_gaps`` in place; returns
        ``(cancelled, final_output, _seed_text)``. Behavior and event ordering are
        unchanged — extracted verbatim from ``_run_inner``.
        """
        # Late import (call-time) resolves every runner-module dependency without an
        # import cycle: studio.runner is fully loaded by the time a run executes.
        from agentkit.orchestrator.fanout import BudgetExceeded
        from agentkit.planner.core import Plan
        from agentkit.topology.core import MAP, MESH
        from agentkit.topology.dynamic import run_plan
        from studio.events import GoalMetEvent, PhaseDoneEvent
        from studio.panels.evolve import build_evolve_event
        from studio.panels.router import build_router_event
        from studio.section_workspace import (
            clear_completed_assignments,
            section_file_map,
            write_assignment_queue,
        )
        from studio.runner import (
            Any,
            BudgetEvent,
            GateEvent,
            Path,
            PhaseStartEvent,
            SINGLE,
            STAR,
            TaskRecord,
            _active_template,
            _build_executor_prompt,
            _build_reducer_refine_prompt,
            _dbg,
            _dedupe_assignment,
            _detect_gaps,
            _final_evidence_dossier,
            _final_step_instruction,
            _full_scoring_matrix,
            _gap_sections,
            _length_ratio_ok,
            _make_section_reducer,
            _merge_weaknesses,
            _parse_assigned,
            _parse_patches_from_output,
            _per_phase_compliance_repair_clause,
            _phase1_requirement_notice,
            _phase_search_failed,
            _prompt_scoring_matrix,
            _re,
            _repair_doubled_citations,
            _repair_fence_contamination,
            _research_findings_to_patches,
            _score_text_weaknesses,
            _scoring_template,
            _strip_preamble,
            _strip_task_scoring_block,
            _update_active_template_from_artifact,
            _with_upstream,
            _write_artifact_through_sections,
            active_outline_titles,
            build_section_assignment_rows,
            merge_duplicate_sections,
            normalize_artifact,
            replace,
            strip_satisfied_placeholders,
            time,
            verify_assignment_coverage,
            workspace_root,
        )
        cancelled = False
        final_output = ""
        # P0-2b strong-model reducer: the reducer is where synthesis is actually
        # COMPOSED — the two stuck rubric rows (Evidence synthesis, Analytical
        # depth) live or die on its patches, and the weak generation model is the
        # suspected ceiling (run 1531). Same degrade-to-base pattern as hybrid
        # planning: judge spec (default haiku) when available, base client in
        # tests / on backend failure. Spokes stay on the session model.
        _reducer_client = self._build_judge_client(base_client)
        # Reset per-epoch relevance state (studio.relevance) — read by
        # _postrun_score_and_record via these instance attrs (mirrors the existing
        # self._last_scorecard_100 cross-method bridge). Cleared every epoch so a
        # stale value never leaks from a prior (possibly cross-task-seeded) epoch
        # into one with no seed at all.
        self._epoch_relevance_penalty = 0.0
        self._epoch_relevance_issues = []
        self._epoch_relevance_checked = False
        for _phase_idx, step in enumerate(plan_obj.steps):
            if session.cancel_requested:
                cancelled = True
                break

            self._current_step_id = step.id
            self._phase_captured = 0
            self._phase_timing = {}  # T1: fresh per-phase timing bucket
            # Planned fan-out (sizing cap + 1 reduce) so the DAG shows the agents
            # as RUNNING up front, not a default guess corrected only at phase_done.
            _planned_n = (_sizing_cfg.max_agents + 1) if _sizing_cfg is not None else None
            self._emit(PhaseStartEvent(step_id=step.id, n_agents=_planned_n))

            # router panel
            self._emit(build_router_event(step))

            # memory recall before the phase (what prior lessons apply)
            self._emit(mem.recall(step.description))

            # fold upstream outputs, then run the single-step sub-plan
            upstream = "\n\n".join(
                f"[{dep}] {outputs[dep]}" for dep in step.depends_on if outputs.get(dep)
            )
            is_last = step is plan_obj.steps[-1]
            desc = step.description
            # Inject the top-level task into every step whose description does not
            # already contain it.  This matters especially for downstream phases
            # (e.g. "create a research report") that are too terse to be meaningful
            # without the original goal, and for PIPELINE stages (previously STAR)
            # where the hub description never contained the full task text.
            topo = step.topology or SINGLE
            # PLAN P2 (goal-blind workers): a reducer-backed fan-out phase runs goal-BLIND
            # executor spokes — the executor prompt below carries the bounded ASSIGNMENT
            # (this phase's description) + weaknesses, NOT the global goal. So the global
            # TASK is NOT prepended for them (goal-knowledge follows the role: hub/reducer
            # stay goal-aware, workers do not). Goal-aware / non-executor phases (terse
            # downstream SINGLE/PIPELINE, or runs without loop_config) still get the task.
            _worker_phase = (_lc is not None and topo in (STAR, MAP, MESH))
            if plan_obj.task and plan_obj.task not in desc and not _worker_phase:
                desc = f"TASK: {plan_obj.task}\n\n{desc}"
            # On the final step, if there is upstream content, prefix with an
            # explicit instruction to output the artifact rather than asking for
            # more context. Loop catalog "stop" steps are written for humans; the
            # LLM needs an imperative framing to produce the artifact, not a
            # meta-decision about whether to continue.
            if is_last and upstream:
                _merge_weaknesses(session, _score_text_weaknesses(session, upstream))
                if _artifact_copied:
                    from studio.rubric import format_scoring_rules
                    # Prior artifact seeded into workspace. The reducer has no read_file
                    # tool, so inject the seeded content directly into the prompt.
                    # Workers produced RESEARCH_FINDING blocks in their text output;
                    # the reducer applies each block to the artifact independently.
                    # After the step runs we write sr.output back to artifact.md.
                    # NOTE: this is the CURRENT on-disk artifact (seed + prior phases'
                    # writebacks this epoch), used only for the repair/relevance clauses
                    # below. It is a LOCAL snapshot — it must NOT clobber the parameter
                    # `_seed_text`, which is the ACTUAL seed that started this epoch and
                    # is the "prior best to beat" baseline the Phase-1 keep/discard gate
                    # (epoch_gate.accept_epoch) compares against. Overwriting it here made
                    # the gate compare this epoch's own output against itself.
                    _seed_on_disk = ""
                    if _eff_ws2 is not None:
                        try:
                            _seed_on_disk = (_eff_ws2 / session.session_id / "artifact.md").read_text()
                        except OSError:
                            pass
                    # §14.6: the additive-merger rules below forbid rewriting — which also
                    # blocks REPAIR, so a malformed mermaid edge / truncated code block in
                    # the seed is immortal. Carve a NARROW exception: when the seed has
                    # FLAGGED malformations, permit fixing exactly those in place (syntax
                    # only, content kept). accept_rewrite already allows the rewrite; this
                    # lifts the PROMPT-level ban for the specific defects, nothing else.
                    _repair_clause = ""
                    try:
                        from studio.artifact_lint import lint_artifact
                        _seed_lints = lint_artifact(_seed_on_disk)
                        if _seed_lints:
                            _repair_items = "\n".join(f"      - {w}" for w in _seed_lints)
                            _repair_clause = (
                                f"  - EXCEPTION (repair-in-place): fix ONLY these malformed "
                                f"blocks — correct the syntax, keep the content and length "
                                f"roughly the same; everything else stays verbatim:\n"
                                f"{_repair_items}\n"
                            )
                    except Exception:  # noqa: BLE001 — repair clause is best-effort
                        pass
                    # Relevance repair-in-place exception (mirrors the lint repair
                    # clause above; studio.relevance). Calibration against a real
                    # contaminated run showed cross-task R10 seeds (a prior task
                    # merely SIMILAR to this one) can carry claims/citations that
                    # belong to the PRIOR topic — and that embedding cosine cannot
                    # detect this (both requirements score high against either
                    # topic). A narrow per-section binary LLM check flags it
                    # instead; the worker gets a bounded license to REPLACE (not
                    # append alongside) exactly those flagged sections. Gated on
                    # _seed_cross_task so a normal same-task hill-climb epoch never
                    # pays for the extra per-section LLM calls.
                    _relevance_repair_clause = ""
                    if _seed_cross_task and base_client is not None:
                        try:
                            from agentkit.artifacts.sections import split_sections
                            from studio.relevance import relevance_issues
                            _seed_sections = {
                                _re.sub(r"^#{1,6}\s*", "", h).strip(): b
                                for h, b in split_sections(_seed_on_disk)
                                if h != "(intro)" and (b or "").strip()
                            }
                            _rel_penalty, _rel_issues = relevance_issues(
                                base_client, _seed_sections,
                                plan_obj.task or requirement,
                                seed_topic=_seed_topic,
                            )
                            self._epoch_relevance_penalty = _rel_penalty
                            self._epoch_relevance_issues = _rel_issues
                            # Fix 2: the relevance-check actually ran this epoch, so the
                            # recorded score accounts for cross-task contamination. Marks
                            # the TaskRun relevance_checked=True (see _postrun record).
                            self._epoch_relevance_checked = True
                            if _rel_issues:
                                _rel_items = "\n".join(f"      - {w}" for w in _rel_issues)
                                _relevance_repair_clause = (
                                    f"  - EXCEPTION (relevance-repair-in-place): this "
                                    f"section's seeded content belongs to a DIFFERENT "
                                    f"task's topic, not the current task — DROP that "
                                    f"content entirely (delete it, do not keep or reword "
                                    f"it) and WRITE NEW content addressing the CURRENT "
                                    f"task in its place. Everything else stays verbatim:\n"
                                    f"{_rel_items}\n"
                                )
                        except Exception:  # noqa: BLE001 — relevance check is best-effort
                            pass
                    # Fix 1 (defense-in-depth): whenever this epoch's content came from a
                    # cross-task R10 seed, inject an UNCONDITIONAL adaptation instruction —
                    # independent of whether the LLM relevance classifier above flagged any
                    # specific section. The classifier has a known ~85% recall ceiling
                    # (a self-propagating contamination case slipped through it this
                    # session), so this always-on notice is the detection-independent
                    # backup. Pure prompt text gated on the known-boolean cross-task-seed
                    # flag — no extra LLM call, no new detection.
                    _cross_task_seed_notice = ""
                    if _seed_cross_task:
                        _cross_task_seed_notice = (
                            "  - CROSS-TASK SEED: this document was seeded from a "
                            "DIFFERENT but related prior task. For ANY section containing "
                            "content that actually belongs to that PRIOR task's topic "
                            "rather than the CURRENT task — even if not specifically "
                            "flagged above — DROP that content entirely (delete it, do "
                            "not keep or reword it) and WRITE NEW content addressing the "
                            "CURRENT task in its place.\n"
                        )
                    # Requirement-compliance repair clause (studio.requirement_compliance).
                    # The prior epoch's verification flagged EXPLICIT task requirements the
                    # then-current artifact failed to satisfy; tell the executor to add what
                    # is missing now (no extra LLM call — reuses the last computed result,
                    # which feeds forward across epochs like the weakness list). Empty on the
                    # first epoch (nothing verified yet); the same-epoch editor pass is the
                    # cold-start backstop.
                    _compliance_repair_clause = ""
                    if self._epoch_compliance_issues:
                        _comp_items = "\n".join(
                            f"      - {w}" for w in self._epoch_compliance_issues
                        )
                        _compliance_repair_clause = (
                            f"  - STATED REQUIREMENTS NOT YET MET: the task explicitly "
                            f"asked for the following and the current document does not "
                            f"satisfy them — add what is missing so each is fulfilled:\n"
                            f"{_comp_items}\n"
                        )
                    _evidence_ws = (
                        (_eff_ws2 or self._workspace_root or workspace_root()) / session.session_id
                    )
                    _seed_scoring_block = (
                        format_scoring_rules(_full_scoring_matrix(session)).strip()
                        or "- (no scoring matrix provided)"
                    )
                    _seed_weakness_block = "\n".join(
                        f"- {w}" for w in (getattr(session, "weaknesses", []) or [])
                        if str(w).strip()
                    ) or "- (none)"
                    _seed_evidence_block = _final_evidence_dossier(
                        upstream,
                        workspace_dir=_evidence_ws,
                    ) or "- (no fetched evidence files available)"
                    # G2 (PLAN §4/§4d): DETERMINISTIC SECTION ASSEMBLY — the reducer does NOT
                    # LLM-merge the whole document. The old prompt injected the full seed
                    # (`_art_ctx`) and demanded "output the CURRENT ARTIFACT with additions,
                    # length >= input" — a whole-doc ECHO that a model truncates on a large doc
                    # (verified 68 KB → 36 KB). Removed. Assembly is now mechanical: workers
                    # emit RESEARCH_FINDING blocks → the section reducer / post-loop
                    # `reduce_patches` fold them into the on-disk artifact section by section,
                    # in document order. The LLM never re-emits the document, so truncation is
                    # impossible. (`_seed_on_disk` — the local current-artifact snapshot — feeds
            # only the seed-lint / relevance repair clauses; `_seed_text` is untouched.)
                    desc = (
                        f"You are a research EXECUTOR improving an existing deliverable.\n"
                        f"The current artifact lives on disk; a DETERMINISTIC reducer assembles it\n"
                        f"from your findings — you NEVER re-emit or echo the whole document (echoing\n"
                        f"a long document truncates it and REGRESSES the deliverable).\n\n"
                        f"Emit ONLY RESEARCH_FINDING blocks for the gaps/weaknesses in your\n"
                        f"assignment — each with a real URL you actually fetched, an exact\n"
                        f"PATCH_TARGET section heading, and a verbatim QUOTE. Found nothing for a\n"
                        f"gap → emit nothing for it (no narration, no 'search unavailable').\n"
                        f"CITE ONLY a URL you fetched; never invent or alter one.\n"
                        f"{_repair_clause}"
                        f"{_relevance_repair_clause}"
                        f"{_cross_task_seed_notice}"
                        f"{_compliance_repair_clause}\n"
                        f"\nFULL SCORING STANDARD:\n{_seed_scoring_block}\n\n"
                        f"UNRESOLVED WEAKNESSES:\n{_seed_weakness_block}\n\n"
                        f"FETCHED EVIDENCE FILES:\n{_seed_evidence_block}\n\n"
                        f"If fetched evidence file paths are listed and read_file is available,\n"
                        f"inspect them before deciding a weakness is unsupported.\n\n"
                        f"Workflow instruction: {desc}"
                    )
                else:
                    from studio.rubric import format_scoring_rules
                    _evidence_ws = (
                        (_eff_ws2 or self._workspace_root or workspace_root()) / session.session_id
                    )
                    desc = _final_step_instruction(
                        plan_obj.task or requirement,
                        desc,
                        scoring_rules=format_scoring_rules(_full_scoring_matrix(session)),
                        weaknesses=getattr(session, "weaknesses", []) or [],
                        evidence_dossier=_final_evidence_dossier(
                            upstream,
                            workspace_dir=_evidence_ws,
                        ),
                    )
            sub_step = replace(
                step, description=_with_upstream(desc, upstream), depends_on=()
            )
            sub_plan = Plan(task=plan_obj.task, steps=(sub_step,))

            # M9: inject hub CoT prompt when loop_config active and phase fans out.
            # The step description becomes the hub's system prompt inside run_plan.
            # Reducer-backed fan-outs (STAR/MAP/MESH) — these now ALL route their worker
            # drafts through the section-aware reducer (§4b), so every spoke gets the
            # research-EXECUTOR framing and emits RESEARCH_FINDING blocks the reducer folds
            # in. PIPELINE (ordered stages) has no fan-in reducer and is coerced→STAR under
            # hill-climb, so it never reaches here. Breadth stays bounded by run_plan's
            # max_agents (caps _facets regardless of topology). Same condition as the
            # P2 TASK-injection gate above — reuse the one named flag so they can't drift.
            if _lc is not None and _worker_phase:
                _hub_art_text = ""
                if _eff_ws2 is not None:
                    _hub_art_file = _eff_ws2 / session.session_id / "artifact.md"
                    if _hub_art_file.exists():
                        _hub_art_text = _hub_art_file.read_text()[:3000]
                _hub_wk_lines = "\n".join(
                    f"- {w}" for w in getattr(session, "weaknesses", []) or []
                )
                # §11.10: frame the STAR spokes as EXECUTORS, not planning hubs.
                # The old _build_hub_cot_prompt made every spoke "the planning hub"
                # → they emitted TASK_LIST/ASSIGNED (plans) instead of fetching, so
                # the reducer got analysis, the artifact gained no sources, and the
                # score stalled. Execute-and-emit-RESEARCH_FINDING framing instead.
                # Worker prompts later strip the global scoring block so section
                # workers only receive cropped, relevant rules in their focus text.
                # Strip it from the goal before building the executor prompt;
                # otherwise `_strip_task_scoring_block` sees the marker inside
                # TASK GOAL and truncates the rest of the executor contract
                # (including the web_search/web_fetch mandate).
                _executor_goal = _strip_task_scoring_block(plan_obj.task or requirement)
                _executor_desc = _build_executor_prompt(
                    goal=_executor_goal,
                    artifact_text=_hub_art_text,
                    weaknesses_block=_hub_wk_lines,
                )
                sub_step = replace(
                    sub_step,
                    description=_executor_desc + "\n\n" + sub_step.description,
                )
                sub_plan = replace(sub_plan, steps=(sub_step,))

            # M8: dynamic worker count from LoopConfig sizing (DESIGN §3).
            # TWO DISTINCT LEVERS (the 2026-06-27 gap-flood fix):
            #   _max_workers  → concurrency (how many spokes run at once),
            #                   derived from the remaining-task count.
            #   _max_agents   → breadth   (how many spokes EXIST), the raw
            #                   max_agents slider. Passing only _max_workers
            #                   capped concurrency while STAR/MAP still spawned
            #                   18 spokes; run_plan now clamps the COUNT too.
            _max_workers = 4
            _max_agents = None
            if _sizing_cfg is not None:
                from agentkit.topology.sizing import compute_n_agents
                _n_remaining = max(1, len(_ledger.remaining()))
                _max_workers = compute_n_agents(_n_remaining, _sizing_cfg)
                _max_agents = _sizing_cfg.max_agents

            if _lc is not None and _worker_phase:
                sub_step = replace(
                    sub_step,
                    description=_strip_task_scoring_block(sub_step.description),
                )
                sub_plan = replace(sub_plan, steps=(sub_step,))
                _sections_for_workers: list[str] = []
                _section_files_for_workers: dict[str, str] = {}
                _assignment_root: Path | None = (
                    (_eff_ws2 or self._workspace_root or workspace_root()) / session.session_id
                )
                if _eff_ws2 is not None:
                    _session_root = _eff_ws2 / session.session_id
                    _sections_for_workers = active_outline_titles(_session_root)
                    _section_files_for_workers = section_file_map(_session_root)
                    _hub_art_file = _session_root / "artifact.md"
                    if not _sections_for_workers and _hub_art_file.exists():
                        try:
                            from agentkit.artifacts.sections import split_sections
                            _sections_for_workers = [
                                h for h, _b in split_sections(_hub_art_file.read_text())
                                if h.lstrip().startswith("## ")
                            ]
                        except OSError:
                            _sections_for_workers = []
                _tmpl_for_workers = _active_template(session)
                if _tmpl_for_workers:
                    from studio.rubric import _content_tokens
                    _seen_section_tokens = [
                        _content_tokens(s) for s in _sections_for_workers
                    ]
                    for _tmpl_section in _tmpl_for_workers:
                        _want = _content_tokens(str(_tmpl_section))
                        if not any(_want and _want & _seen for _seen in _seen_section_tokens):
                            _sections_for_workers.append(str(_tmpl_section))
                # Section-file assignment is a queue: one worker call fetches one
                # section file. The queue length controls total foci so active
                # files are not silently dropped by the max_agents breadth cap;
                # _max_workers remains the concurrency throttle.
                # Pending ### sub-headings (Workstream P injections and any
                # other source) must be NAMED in the owning section's
                # assignment — rows carry titles, the placeholders live in
                # file bodies, so without this no worker ever sees them.
                _subs_for_workers: dict[str, list[str]] = {}
                if _eff_ws2 is not None:
                    try:
                        from studio.section_workspace import pending_subsections
                        _subs_for_workers = pending_subsections(
                            _eff_ws2 / session.session_id
                        )
                    except Exception:  # noqa: BLE001 — ownership hint is best-effort
                        _subs_for_workers = {}
                _queue_rows = build_section_assignment_rows(
                    _sections_for_workers,
                    getattr(session, "weaknesses", []) or [],
                    section_files=_section_files_for_workers,
                    agent_slots=_max_workers,
                    scoring_matrix=_prompt_scoring_matrix(session),
                    subsections=_subs_for_workers,
                )
                if _assignment_root is not None:
                    write_assignment_queue(_assignment_root, _queue_rows)
                _worker_foci = tuple(row["assignment"] for row in _queue_rows)
                if _worker_foci:
                    _max_agents = max(_max_agents or 0, len(_worker_foci))
                    sub_step = replace(sub_step, worker_foci=_worker_foci)
                    sub_plan = replace(sub_plan, steps=(sub_step,))

            # Collision guard (DESIGN §3.1): mark this phase in-flight before it
            # runs so remaining() excludes it; mark_done() clears it after. Keeps
            # the same task from appearing as "remaining" while it is executing.
            _ledger.mark_in_flight(step.id)

            # §4.5: under hill-climb, inject the section-aware reducer so the STAR
            # reduce step merges/refines/reviews worker output into the current
            # artifact (vs the generic synthesis). Read the running artifact fresh
            # each phase — it is the section-keyed handoff from the prior phase.
            _reducer = None
            if _artifact_copied and _eff_ws2 is not None:
                _cur_art = ""
                try:
                    _raw_art_dbg = (_eff_ws2 / session.session_id / "artifact.md").read_text()
                    _cur_art = _strip_preamble(_raw_art_dbg)
                    _dbg(f"step {step.id} START artifact={len(_raw_art_dbg)} "
                         f"stripped={len(_cur_art)} "
                         f"h1={_raw_art_dbg.count(chr(10)+'# ')} "
                         f"h2={_raw_art_dbg.count(chr(10)+'## ')}")
                except Exception:  # noqa: BLE001 — section writeback must not crash the run
                    pass
                from studio.rubric import format_scoring_rules
                # FULL frozen scoring matrix with the profile/template fallback (same fix as
                # the final-synthesis path): when scoring lives in the requirement rather than
                # rubric_config, the raw .get("scoring_matrix") is None and the reducer would
                # otherwise see "- (no scoring matrix provided)". _full_scoring_matrix falls
                # back to the profile/template default so every reducer measures against the
                # full standard.
                _reducer_ws = None
                try:
                    _reducer_ws = _eff_ws2 / session.session_id
                except Exception:  # noqa: BLE001
                    _reducer_ws = None
                # PER-PHASE requirement compliance (studio.requirement_compliance),
                # injected into the goal-aware REDUCER prompt only (never the goal-blind
                # spoke workers — goal-knowledge follows the role: hub/reducer). ADDITIVE
                # to the entry 167-170 epoch-end backstop, which still runs unchanged.
                #   * Phase 1 (first phase of the epoch, cold-start included): a PROACTIVE
                #     notice of the full cached requirement list — nothing is generated yet,
                #     so there is nothing to verify; give the reducer the whole checklist to
                #     address from the start.
                #   * Phases 2..N: VERIFY the partial artifact assembled from the sections
                #     produced SO FAR and inject only what is STILL unaddressed (hard misses
                #     + not-yet-included OR opportunities) so it gets fulfilled while phases
                #     remain. Fail-open — both helpers return "" on any failure.
                _requirement_clause = ""
                if self._task_requirements:
                    if _phase_idx == 0:
                        _requirement_clause = _phase1_requirement_notice(
                            self._task_requirements
                        )
                    else:
                        # Codex review: prefer the already-read on-disk artifact.md
                        # (`_cur_art`) over re-assembling from section files here.
                        # `patch_artifact` (an editor/tool-call path) can mutate
                        # artifact.md directly WITHOUT syncing section files, so a
                        # blind assemble-from-sections at this boundary could clobber
                        # those unsynced edits. Only assemble as a fallback when
                        # artifact.md itself is missing/empty.
                        _partial_artifact = _cur_art
                        if not _partial_artifact:
                            try:
                                from studio.section_workspace import (
                                    assemble_artifact_from_sections,
                                )
                                _partial_artifact = assemble_artifact_from_sections(
                                    _eff_ws2 / session.session_id
                                ) or ""
                            except Exception:  # noqa: BLE001 — fail open, no clause
                                _partial_artifact = ""
                        _requirement_clause = _per_phase_compliance_repair_clause(
                            base_client, self._task_requirements, _partial_artifact
                        )
                _reducer = _make_section_reducer(
                    _reducer_client, _cur_art, getattr(session, "weaknesses", []) or [],
                    embedder=self._embedder,   # F1: dedup near-duplicate findings
                    scoring_rules=format_scoring_rules(_full_scoring_matrix(session)),
                    # Fetched materials handoff: same FETCHED EVIDENCE FILES the final step
                    # gets, built from URLs cited in the workers + current artifact this phase.
                    evidence_fn=lambda _text: _final_evidence_dossier(
                        _text, workspace_dir=_reducer_ws
                    ),
                    requirement_clause=_requirement_clause,
                    timing_sink=self._phase_time_add,  # T1: reducer + prefetch timing
                    # Topical floor for findings: the CLEAN task text, not the
                    # iteration-prefixed `requirement` — the prefix boilerplate
                    # dilutes the vocabulary overlap ~10x and false-drops sources.
                    requirement=base_requirement,
                    fallback_client=client,  # call-time degrade if the judge flakes
                )

            try:
                _t_spoke = time.monotonic()  # T1: spoke fan-out + reduce wall
                result = run_plan(
                    sub_plan, client, budget=budget,
                    max_workers=_max_workers, max_agents=_max_agents,
                    reducer=_reducer,
                )
                self._phase_time_add("spoke", time.monotonic() - _t_spoke)
            except BudgetExceeded as exc:
                self._emit(
                    BudgetEvent(spent=exc.spent, ceiling=session.budget_ceiling, exceeded=True)
                )
                self._last_stop_reason = "budget_exceeded"
                cancelled = True
                break

            sr = result.runs[0]
            if _lc is not None and _worker_phase and "_assignment_root" in locals():
                _worker_done = max(0, len(sr.agent_io) - 1)
                if _assignment_root is not None and _worker_done:
                    clear_completed_assignments(_assignment_root, _worker_done)
            outputs[step.id] = sr.output
            final_output = sr.output

            # §14.2: if the search tool itself failed for this whole phase, surface
            # it as a visible gate check — a broken-search run must not masquerade
            # as a finished one (and the anti-regression guard keeps the seed).
            if _phase_search_failed([sr.output]):
                _sf_gate = GateEvent(
                    name="search-availability",
                    outcome="fail",
                    detail="Search tool failed for this phase (SEARCH: error, no findings); "
                           "deliverable left unchanged (no regression).",
                )
                gate_events.append(_sf_gate)
                self._emit(_sf_gate)

            # R2 enforcement: validate the hub's ASSIGNED block in CODE (not just
            # prompt). Parse agent→sections, detect any section claimed by >1
            # agent, and deterministically reassign (first-claim-wins). Surface
            # the result as a gate check so violations are visible, not silent.
            _assigned = _parse_assigned(sr.output)
            # G3/G4 wiring: the goal-blind executor path emits no ASSIGNED block, so without a
            # hub the reduce-time coverage check (verify_assignment_coverage) would never run.
            # When no LLM assignment arrived, synthesize a DETERMINISTIC hub assignment from the
            # rubric template — the agreed deliverable spec IS the assignment: every required
            # section is an assigned improve/create job. The reducer then verifies each was
            # actually delivered (present + populated); an unmet one becomes a next-epoch
            # weakness (a missing section = an unmet "create" job, closing the G4 loop).
            if not _assigned:
                _tmpl_cov = _active_template(session)
                if _tmpl_cov:
                    _assigned = {"deliverable": list(_tmpl_cov)}
            if _assigned:
                _clean, _overlaps = _dedupe_assignment(_assigned)
                if _overlaps:
                    _gate = GateEvent(
                        name="worker-assignment",
                        outcome="warn",
                        detail=(
                            f"{len(_overlaps)} section(s) assigned to >1 agent: "
                            f"{', '.join(_overlaps[:8])}"
                            f"{'…' if len(_overlaps) > 8 else ''}. "
                            f"Deterministically reassigned first-claim-wins."
                        ),
                    )
                else:
                    _gate = GateEvent(
                        name="worker-assignment",
                        outcome="pass",
                        detail="Section partition non-overlapping (1 section ≤ 1 agent).",
                    )
                gate_events.append(_gate)
                self._emit(_gate)

            # When the reducer received the seeded artifact as context and produced
            # an improved version, write it back to artifact.md so the scorer and
            # the next epoch both see the improved content. Anti-regression guard:
            # only overwrite if the new output is >5000 chars AND not shorter than
            # the seed — a thin/failed run must keep the prior good doc, never
            # shrink it (worst case = no improvement, never regression).
            # §4.5: write EVERY phase's reduced output back (was last-phase only),
            # so artifact.md is the running section-keyed handoff to the next
            # phase. Grow-only ratchet: only overwrite if not shorter than the
            # current artifact, then raise _seed_len — the doc grows monotonically
            # across phases AND epochs, never regresses (a thin/failed reduce keeps
            # the prior good doc).
            _clean_out = _strip_preamble(sr.output)  # never persist reducer commentary
            _candidate_out = strip_satisfied_placeholders(normalize_artifact(_clean_out))
            # F2: per-section ratchet. The old whole-doc grow-only rule (len >= _seed_len)
            # rejected ANY shrink, blocking dedup/replace/repair. accept_rewrite allows a
            # rewrite (even shorter) as long as no section that had CONTENT is deleted or
            # gutted — preserving anti-regression at section granularity.
            _art_path = (_eff_ws2 / session.session_id / "artifact.md") if _eff_ws2 is not None else None
            _old_art = ""
            if _art_path is not None:
                try:
                    _old_art = _art_path.read_text()
                except Exception:  # noqa: BLE001 — format hygiene must not crash the run
                    pass
            from agentkit.artifacts.sections import accept_rewrite
            if (_artifact_copied and _art_path is not None
                    and len(_candidate_out.strip()) > 0
                    and accept_rewrite(_old_art, _candidate_out)):
                try:
                    _candidate_out = _write_artifact_through_sections(
                        session, _eff_ws2, _candidate_out, requirement
                    )
                    _update_active_template_from_artifact(session, _candidate_out)
                    _dbg(f"writeback ACCEPT step={step.id} {len(_old_art)}→{len(_candidate_out)}")
                    _seed_len = len(_candidate_out)   # track current length for the next phase
                except OSError:
                    pass
            elif _artifact_copied and _art_path is not None:
                _dbg(f"writeback REJECT step={step.id} clean_len={len(_candidate_out)} "
                     f"(accept_rewrite: a sourced section was deleted/gutted)")

            # Per-phase format hygiene (PLAN N1 + glued-heading 'wrong format' fix): normalize
            # the PERSISTED artifact in place after each writeback — un-glue mid-line headings
            # (## A### B → two blocks) and collapse duplicate sections (richest body kept) — so
            # the INTERMEDIATE artifact stays well-formed, not only the final one. Independent of
            # accept_rewrite: it ONLY cleans, never grows, so the next phase reads a clean base.
            # No-op on an already-clean document.
            if _art_path is not None:
                try:
                    _cur_norm = _art_path.read_text()
                    _normed = strip_satisfied_placeholders(normalize_artifact(_cur_norm))
                    # §14 slate item 5 (user-escalated): the deterministic fence/citation
                    # repairs used to run only at finalize, so a glued fence broke markdown
                    # for the REST of the run (GUI shows a swallowed document, every later
                    # step inherits it). Applied LAST, after normalize, so they see the
                    # final per-step text — deterministic + idempotent, no LLM mermaid
                    # repair here (that stays finalize-only, studio/finalize.py).
                    _normed, _fence_fixed = _repair_fence_contamination(_normed)
                    _normed, _dup_fixed = _repair_doubled_citations(_normed)
                    # §14 slate item B: duplicate ## sections (born in _synthesize_windowed,
                    # fixed at source there — this is the same defense-in-depth pattern as
                    # the fence/citation pair above, for a doc that arrives ALREADY duplicated
                    # via an older seed or a reducer patch echo).
                    _normed, _dupsec_fixed = merge_duplicate_sections(_normed)
                    if _fence_fixed or _dup_fixed:
                        _dbg("normalize: fence/citation repairs applied")
                    if _dupsec_fixed:
                        _dbg("normalize: duplicate-section merge applied")
                    if _normed != _cur_norm:
                        _normed = _write_artifact_through_sections(
                            session, _eff_ws2, _normed, requirement
                        )
                        _update_active_template_from_artifact(session, _normed)
                        _seed_len = len(_normed)
                        _dbg(f"normalize step={step.id} {len(_cur_norm)}→{len(_normed)}")
                except OSError:
                    pass

            # G3 (PLAN §4 P1): reduce-time assignment verification. The hub assigned bounded
            # jobs BY SECTION (_assigned); after the reducer assembled, deterministically
            # check each assigned section is present + populated in the artifact. An unmet
            # assignment (absent / still placeholder) becomes a next-epoch weakness — closing
            # the hub-assigns → reducer-verifies loop — and is surfaced as a gate check.
            if _assigned and _art_path is not None:
                _doc_after = ""
                try:
                    _doc_after = _art_path.read_text()
                except OSError:
                    pass
                _unmet = verify_assignment_coverage(_assigned, _doc_after)
                if _unmet:
                    _reducer_gaps.extend(_unmet)
                    _ag = GateEvent(
                        name="assignment-coverage",
                        outcome="warn",
                        detail=(
                            f"{len(_unmet)} assigned section(s) not addressed this phase: "
                            f"{', '.join(u.split(']')[0].lstrip('[') for u in _unmet[:6])}"
                            f"{'…' if len(_unmet) > 6 else ''}. Carried to next epoch as weaknesses."
                        ),
                    )
                else:
                    _ag = GateEvent(
                        name="assignment-coverage",
                        outcome="pass",
                        detail="Every hub-assigned section is present and populated.",
                    )
                gate_events.append(_ag)
                self._emit(_ag)

            # Scoring rules shrink only for future worker prompts. Reducers and
            # phase/final scoring always use the full frozen matrix.
            _rc_phase = getattr(session, "rubric_config", None) or {}
            if _art_path is not None and _rc_phase.get("scoring_matrix"):
                try:
                    from studio.rubric import (
                        remaining_scoring_matrix,
                        rubric_scorecard_100,
                        scorecard_weaknesses,
                    )
                    _doc_after = _art_path.read_text()
                    _t_score = time.monotonic()  # T1: per-phase rubric scorecard
                    _phase_scorecard = rubric_scorecard_100(
                        _doc_after,
                        required_sections=_scoring_template(session),
                        scoring_matrix=_full_scoring_matrix(session),
                        weights=_rc_phase.get("weights"),
                    )
                    self._phase_time_add("scorecard", time.monotonic() - _t_score)
                    _remaining = remaining_scoring_matrix(_phase_scorecard)
                    _rc_phase["remaining_scoring_matrix"] = _remaining
                    _phase_weaknesses = scorecard_weaknesses(
                        _phase_scorecard,
                        _scoring_template(session),
                    )
                    if _phase_weaknesses:
                        _current_w = list(getattr(session, "weaknesses", []) or [])
                        _seen = set(_current_w)
                        session.weaknesses = [
                            w for w in _phase_weaknesses if w not in _seen
                        ] + _current_w
                except Exception:  # noqa: BLE001 — prompt pruning must not break execution
                    pass

            # Reconcile tokens run_plan counted that on_usage did not capture.
            # A StudioChatClient fires on_usage per call (with the in/out split);
            # a raw/CLI client does not, so its tokens only surface in
            # StepRun.tokens. Emit a per-phase frame for any remainder so the HUD
            # always reconciles to DynamicPlanResult.total_tokens (SPEC §8 M3).
            self._reconcile_phase_tokens(step.id, sr.tokens)

            self._emit(
                PhaseDoneEvent(
                    step_id=step.id,
                    topology=sr.topology,
                    n_agents=sr.n_agents,
                    tokens=sr.tokens,
                    wall_s=sr.wall_s,
                    output=sr.output,
                    timing=dict(self._phase_timing),  # T1: per-phase breakdown snapshot
                )
            )
            self._checkpoints.append({
                "id": f"cp_{step.id}",
                "phase_id": step.id,
                "artifact_path": "artifact.md" if _art_path is not None else "",
                "evidence_count": 0,
                "score": None,
                "weakness_count": len(getattr(session, "weaknesses", []) or []),
                "observation_ids": [
                    row.get("id") for row in self._agent_trace
                    if row.get("step_id") == step.id
                ],
                "tokens": sr.tokens,
                "wall_s": sr.wall_s,
            })

            # M8: record this phase as completed (all_tasks was seeded up front,
            # so mark_done moves it from remaining/in-flight to completed).
            _ledger.mark_done(step.id)

            # §14.7: append this phase's INPUT (the full prompt the agent was given) and
            # OUTPUT to a per-session JSONL so a failed run is diagnosable offline — the
            # fastest way to SEE goal/intent stacking, an upstream fold, a recalled
            # refusal, or a worker that dumped raw findings instead of patching. Lives
            # next to artifact.md in the workspace. Best-effort; never breaks the run.
            _io_ws_root = _eff_ws2 or self._workspace_root or workspace_root()
            if _io_ws_root is not None:
                try:
                    import json as _json
                    # §4c: the structured I/O log IS the role contract made observable.
                    # Artifacts live on disk; records carry only PATHS + small structured
                    # fields (the old record INLINED the full prompt + output → a 144 KB
                    # record on a 68 KB doc). Bodies → io/<step>.{in,out}.md. We emit the
                    # PLAN §4c per-ROLE schemas at step granularity: a goal-aware HUB record
                    # (assignments by section), a goal-blind AGENT record (executor artifacts),
                    # and a goal-aware REDUCER record (handoff). The reducer's run-level
                    # new_weaknesses/score are filled in the run-summary record at run end
                    # (scoring happens once post-loop). True PER-SPOKE agent records need
                    # run_plan to surface per-agent I/O — sequenced with the fan-out work.
                    _io_dir = _io_ws_root / session.session_id / "io"
                    _io_dir.mkdir(parents=True, exist_ok=True)
                    _in_p = _io_dir / f"{step.id}.in.md"
                    _out_p = _io_dir / f"{step.id}.out.md"
                    _in_p.write_text(sub_step.description, encoding="utf-8")
                    _out_p.write_text(sr.output, encoding="utf-8")
                    _recs: list[dict[str, Any]] = []
                    # HUB — goal-aware planner: section assignments (when the hub emitted them).
                    if _assigned:
                        _recs.append({
                            "role": "hub", "step": step.id, "topology": topo,
                            "input": {"requirement": (plan_obj.task or "")[:200],
                                      "target_doc": str(_art_path)},
                            "output": {"assignments": [
                                {"agent_id": _ag, "job": {"sections": _secs}}
                                for _ag, _secs in _assigned.items()
                            ]},
                            "tokens": sr.tokens,
                        })
                    # AGENT — goal-blind executor: TRUE PER-SPOKE records from the
                    # StepRun.agent_io trail run_plan now surfaces (§4c per-agent records).
                    # Each spoke's prompt+output go to disk; the record carries paths +
                    # agent_id (the unit the reducer verifies). Falls back to one phase-level
                    # record when no per-spoke trail exists (CLI / no fan-out).
                    _spoke_io = getattr(sr, "agent_io", ()) or ()
                    if _spoke_io:
                        for _si, _rio in enumerate(_spoke_io):
                            _sp_in = _io_dir / f"{step.id}.spoke{_si}.in.md"
                            _sp_out = _io_dir / f"{step.id}.spoke{_si}.out.md"
                            _sp_in.write_text(str(_rio.get("prompt", "")), encoding="utf-8")
                            _sp_out.write_text(str(_rio.get("output", "")), encoding="utf-8")
                            _recs.append({
                                "role": _rio.get("role", "agent"), "step": step.id,
                                "topology": topo, "agent_id": f"{step.id}:spoke{_si}",
                                "input": {"requirement": str(_sp_in), "target_doc": str(_art_path)},
                                "output": {"artifacts": [str(_sp_out)]},
                                "tokens": int(_rio.get("tokens", 0) or 0),
                            })
                    else:
                        _recs.append({
                            "role": "agent", "step": step.id, "topology": topo,
                            "input": {"requirement": str(_in_p), "target_doc": str(_art_path)},
                            "output": {"artifacts": [str(_out_p)]},
                            "n_agents": sr.n_agents, "tokens": sr.tokens,
                        })
                    # Persist the reducer prompt+output so its FULL SCORING RULES / weaknesses /
                    # FETCHED EVIDENCE injection is inspectable (previously the reducer stage left
                    # no io/ file — it runs inside run_plan, not as a recorded spoke).
                    _rcap = getattr(locals().get("_reducer", None), "_io_capture", None)
                    if _rcap and _rcap.get("prompt"):
                        _rd_in = _io_dir / f"{step.id}.reducer.in.md"
                        _rd_out = _io_dir / f"{step.id}.reducer.out.md"
                        _rd_in.write_text(str(_rcap.get("prompt", "")), encoding="utf-8")
                        _rd_out.write_text(str(_rcap.get("output", "")), encoding="utf-8")
                        _recs.append({
                            "role": "reducer", "step": step.id, "topology": topo,
                            "agent_id": f"{step.id}:reducer",
                            "input": {"requirement": str(_rd_in), "target_doc": str(_art_path)},
                            "output": {"artifacts": [str(_rd_out)]},
                        })
                    # REDUCER — goal-aware consolidator: the handoff artifact (run-level
                    # new_weaknesses/score arrive in the run-summary record at run end).
                    _recs.append({
                        "role": "reducer", "step": step.id, "topology": topo,
                        "output": {"new_weaknesses": [], "score": None,
                                   "handoff_artifacts": [str(_art_path)]},
                        "tokens": sr.tokens,
                    })
                    _io_path = _io_ws_root / session.session_id / "agent_io.jsonl"
                    with _io_path.open("a", encoding="utf-8") as _iof:
                        for _r in _recs:
                            _iof.write(_json.dumps(_r) + "\n")
                except Exception:  # noqa: BLE001 — diagnostics must never break the run
                    pass

            # post-phase panels
            mem.record(step.id, sr.output)
            dag.mark_done(step.id, tokens=sr.tokens)
            self._emit(dag.snapshot())
            self._emit(
                selfimp.assess_phase(
                    produced_output=bool(sr.output.strip()), metric=float(sr.tokens)
                )
            )
            self._emit(build_evolve_event(len(outputs), list(outputs.values())))
            gate_event = self._gate_event_for(step.id, sr.output)
            gate_events.append(gate_event)
            self._emit(gate_event)

            # Goal check: if session has a LoopGoal, verify after each phase.
            if getattr(session, 'goal', None) is not None:
                try:
                    from agentkit.loop.goal import check_goal
                    _verdict = check_goal(session.goal, cwd=self._sandbox_cwd)
                    if _verdict.met:
                        self._emit(GoalMetEvent(
                            end_state=session.goal.end_state,
                            evidence=_verdict.evidence,
                            reason=_verdict.reason,
                            step_id=step.id,
                        ))
                        break
                except Exception:  # noqa: BLE001
                    pass  # agentkit.loop not installed → skip silently

        # M8: if any worker output contained PATCHES blocks, apply them atomically
        # via reduce_patches() + write_artifact() (DESIGN §2.2).  Workers that
        # emit RESEARCH_FINDING text instead produce no patches — the existing
        # reducer prompt path runs unchanged for those phases.
        if _eff_ws2 is not None and outputs:
            _patch_groups: list[list] = []
            for _out in outputs.values():
                # §11.3: PATCHES from workers, plus RESEARCH_FINDING blocks
                # converted to additive patches — both merge programmatically so
                # the document is never re-emitted (and never shortened) by an LLM.
                _patches = _parse_patches_from_output(_out) + _research_findings_to_patches(_out)
                if _patches:
                    _patch_groups.append(_patches)
            if _patch_groups:
                from agentkit.artifacts.patcher import reduce_patches, write_artifact
                _art_file = _eff_ws2 / session.session_id / "artifact.md"
                _cur_text = _art_file.read_text() if _art_file.exists() else ""
                # Phase 2 (DESIGN §2.2 Step 5): an editorial refine pass over the merged text.
                # G2/§4d: this is a whole-doc ECHO (the model is fed the merged doc and asked
                # to re-emit it), which TRUNCATES a large document — banned at scale. So it is
                # gated to SMALL docs only (<= _G2_REFINE_MAX); a large doc keeps the
                # deterministic structural merge, and the SEPARATE windowed _synthesize_analysis
                # pass (run post-loop) adds cross-section analysis without echoing the whole doc.
                _G2_REFINE_MAX = 8_000
                _refine_fn = None
                if use_llm and len(_cur_text) <= _G2_REFINE_MAX:
                    _refine_goal = plan_obj.task or requirement
                    _refine_path = str(_art_file)

                    def _refine_fn(merged_text: str) -> str:
                        prompt = (
                            _build_reducer_refine_prompt(_refine_goal, _refine_path)
                            + merged_text
                            + "\n--- END MERGED DOCUMENT ---\n"
                        )
                        res = base_client.chat([{"role": "user", "content": prompt}])
                        out = (getattr(res, "text", "") or "").strip()
                        # Reject truncated/empty refinements: keep the clean merge.
                        # length_ratio_ok is >=-accept polarity here (the accept side of
                        # the same int()-floor the artifact_text reject sites use).
                        if _length_ratio_ok(out, merged_text, min_ratio=0.8):
                            return out
                        return merged_text

                _rr = reduce_patches(_cur_text, _patch_groups, llm_refine_fn=_refine_fn)
                # Anti-regression guard: use the SAME section-granular accept_rewrite
                # guard as the per-phase writeback path (above), not a whole-doc length
                # floor. accept_rewrite permits a legitimately shorter merge (dedup,
                # synthesis replacing verbose quote-dumping) while still rejecting any
                # merge that guts/deletes a section that had content — one consistent
                # anti-regression mechanism instead of two competing ones.
                from agentkit.artifacts.sections import accept_rewrite
                if _rr.text and accept_rewrite(_cur_text, _rr.text):
                    write_artifact(_art_file, _rr.text)
                    _dbg(f"writeback ACCEPT patch-apply {len(_cur_text)}→{len(_rr.text)}")
                elif _rr.text:
                    _dbg(f"writeback REJECT patch-apply clean_len={len(_rr.text)} "
                         f"(accept_rewrite: a sourced section was deleted/gutted)")
                # §11.4 gap routing: detect empty/placeholder sections, then
                # CONSOLIDATE by top-level section before routing so a noisy gap
                # list can't inflate agent sizing (2026-06-27 fix). Non-last phase
                # → hand the distinct sections to the next phase via the ledger
                # (one bounded task per section); last phase → carry gap messages
                # to the next run as weaknesses.
                _gaps = _detect_gaps(_rr.text or "")
                # §11.4 closure: a repeat-failure (recorded in >= REPEAT_LIMIT prior
                # runs) is NEVER dropped or hidden. It flows normally through handoff
                # so the LAST phase gets a final attempt; whatever is still open after
                # the run is surfaced to the user below the result (see run end). We
                # only TRACK them here for that report — _repeat_failed is computed at
                # run start and consumed at run end.
                if _gaps and not is_last:
                    for _si, _sec in enumerate(_gap_sections(_gaps)):
                        _ledger.add_task(TaskRecord(
                            id=f"gap-{step.id}-{_si}",
                            description=f"Revise/source section: {_sec}",
                        ))
                elif _gaps:  # last phase → carry to next run as SECTION-bound weaknesses
                    # Keep the section label so next run routes each weakness to the
                    # agent that owns that section (an agent can't patch unassigned
                    # sections — DESIGN §11.4).
                    _reducer_gaps.extend(f"[{_sec}] {_m}" for _sec, _m in _gaps)
        return cancelled, final_output, _seed_text
