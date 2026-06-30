"""studio.runner — the step-loop driver that emits the SSE sequence (SPEC §5.2).

``agentkit.topology.dynamic.run_plan`` is synchronous and emits nothing mid-run,
so Studio drives the phase loop itself and delegates each step to ``run_plan``
on a SINGLE-STEP sub-plan, folding upstream outputs into the description exactly
as agentkit's own ``_with_upstream`` does. The real STAR/MESH/PIPELINE fan-out
happens inside that per-step ``run_plan`` call; Studio adds observability around
it.

Event ordering (SPEC §4):
  session → plan → topology → graph → (per phase: phase_start, [router],
  [memory], [token…], [agent_event], phase_done, [dag], [selfimprove],
  [evolve], [gate]) → budget → verify → done

Token frames fire *during* a phase via the ``on_usage`` callback closed over the
current step (StudioChatClient calls it per LLM call). The runner runs in a
worker thread; events cross to the SSE generator through a queue (app.py owns the
asyncio bridge). Here the runner just calls an injected ``emit(event)`` sink.
"""

from __future__ import annotations

import json as _json
import re as _re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from agentkit.orchestrator.fanout import BudgetExceeded, FanoutBudget
from agentkit.planner.core import Plan, PlanStep, plan
from agentkit.topology.core import MAP, MESH, PIPELINE, SINGLE, STAR
from agentkit.topology.dynamic import assign_topologies, run_plan
from agentkit.types import LLMClient

from studio.backends import build_chat_client, build_embedder, resolve_backend
from studio.events import (
    BudgetEvent,
    DoneEvent,
    ErrorEvent,
    GateEvent,
    GoalMetEvent,
    GraphEvent,
    HillClimbEvent,
    LoopSeedEvent,
    PhaseDoneEvent,
    PhaseStartEvent,
    PlanEvent,
    SessionEvent,
    StudioEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    TopologyEvent,
)
from studio.loops import make_seeded_decomposer
from studio.panels.dag import DagTracker
from studio.panels.evolve import build_evolve_event
from studio.panels.loopdoctor import build_loopdoctor_event
from studio.panels.memory import MemoryTracker
from studio.panels.router import build_router_event
from studio.panels.security import run_gate_event
from studio.panels.selfimprove import SelfImproveTracker
from studio.panels.verify import build_verify_event
from studio.model_profiles import resolve_model_profile
from studio.session import RunSnapshot, Session
from studio.shared_bridge import TokenAccounting, UsageReport
from studio.tools import ToolAugmentedClient, web_toolkit_available
from studio.workspace import Workspace

# ---------------------------------------------------------------------------
# Re-exports — stateless helpers extracted into focused modules (SRP).
# These names remain importable from ``studio.runner`` for callers/tests that do
# ``from studio.runner import X``. The runner body uses them unchanged.
# ---------------------------------------------------------------------------
from studio.prompts import (  # noqa: E402,F401
    _build_executor_prompt,
    _build_hub_cot_prompt,
    _build_planner_cot_prompt,
    _build_reducer_refine_prompt,
    _build_skeleton,
    _build_worker_cot_prompt,
    _today_note,
)
from studio.findings import (  # noqa: E402,F401
    _apply_ranking,
    _findings_to_patches,
    _make_section_reducer,
    _parse_findings,
    _parse_patches_from_output,
    _prefetch_cited,
    _research_findings_to_patches,
    _weakness_score,
)
from studio.planning import (  # noqa: E402,F401
    EpochResult,
    _dedupe_assignment,
    _dedupe_plan_steps,
    _epoch_status,
    _expand_topology,
    _parse_assigned,
    _parse_epic_plan,
    _phase_search_failed,
    _plan_from_epics,
    _render_graph,
    _with_upstream,
)
from studio.artifact_text import (  # noqa: E402,F401
    _detect_gaps,
    _ends_cleanly,
    _gap_sections,
    _merge_missing_sections,
    _repair_lints,
    _strip_preamble,
    _synthesize_analysis,
    _unresolved_block,
)


#: Emit sink: the runner calls this for every event; app.py wires it to a queue.
Emit = Callable[[StudioEvent], None]


def _dbg(msg: str) -> None:
    """Append a throughput-diagnostic line to the file named by OMC_THROUGHPUT_DEBUG.

    A no-op unless that env var is set, so production and tests write nothing. Used to
    localize where findings are lost between the spokes and the artifact (raw findings →
    grounded floor patches → patches actually applied → grow-only writeback)."""
    import os
    path = os.environ.get("OMC_THROUGHPUT_DEBUG")
    if not path:
        return
    try:
        with open(path, "a") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


class Runner:
    """Drives one studio run end-to-end, emitting the ordered SSE sequence.

    Constructed per run from a ``Session`` + an ``Emit`` sink + an injected
    ``LLMClient`` factory (so tests pass a fake client with no network). The
    factory takes the ``on_usage`` callback and returns a client.
    """

    def __init__(
        self,
        session: Session,
        emit: Emit,
        *,
        client_factory: Callable[[Callable[[UsageReport], None]], LLMClient] | None = None,
        embedder: Any = None,
        sandbox_cwd: str = ".",
        search_fn: Callable[..., list[Any]] | None = None,
        fetch_fn: Callable[..., Any] | None = None,
        workspace_root: Any = None,
        prefer_fn: Callable[[str, str, str], int] | None = None,
    ) -> None:
        self._session = session
        self._emit = emit
        self._client_factory = client_factory
        self._embedder = embedder
        self._sandbox_cwd = sandbox_cwd
        #: Injected web_search fn for the tool loop (tests pass a stub → no net).
        self._search_fn = search_fn
        #: Injected web_fetch fn for the tool loop (tests pass a stub → no net).
        self._fetch_fn = fetch_fn
        #: Workspace root override for the file-tool jail (tests pass a tmp dir).
        self._workspace_root = workspace_root
        #: Phase-1 epoch keep/discard judge (new, prior, requirement) -> net pref.
        #: Tests inject a deterministic stub; production builds one from
        #: agentkit.evolve.self_preference (see studio.epoch_gate, DESIGN §14.1).
        self._prefer_fn = prefer_fn
        self._acc = TokenAccounting()
        self._current_step_id = ""
        #: §14.4 epoch heartbeat: 1-based index of the current in-process pass when
        #: the epoch loop is engaged (0 = single-pass mode → step ids un-prefixed).
        self._epoch = 0
        #: Effective hill-climb config for this run, resolved once in run() from the
        #: session config + the persisted per-task snapshot (None until run() sets it).
        self._effective_hc: dict | None = None
        #: Final output / cancel flag of the last pass — run() reads these to emit the
        #: single terminal `done` after the epoch loop (the done moved out of _run_inner).
        self._last_result = ""
        self._last_cancelled = False
        #: Wall-clock start of the run, stamped in run(); the done frame reports
        #: real elapsed time (per-phase wall_s lives on phase_done).
        self._t0: float | None = None
        #: Tokens captured via on_usage for the current phase (used to reconcile
        #: against run_plan's StepRun.tokens for non-StudioChatClient backends).
        self._phase_captured = 0

    # -- token plumbing ----------------------------------------------------

    def _on_usage(self, usage: UsageReport) -> None:
        """Per-call usage sink: feed accounting + push a ``token`` frame.

        Closes over ``_current_step_id`` so each frame is attributed to the phase
        running when the LLM call fired.
        """
        self._acc.add(usage)
        self._phase_captured += usage.input_tokens + usage.output_tokens
        self._emit(
            TokenEvent(
                step_id=self._current_step_id,
                input=usage.input_tokens,
                output=usage.output_tokens,
                total=usage.input_tokens + usage.output_tokens,
                estimated=usage.estimated,
                cumulative={
                    "input": self._acc.total_input_tokens,
                    "output": self._acc.total_output_tokens,
                    "total": self._acc.total_tokens,
                    "estimated": self._acc.tokens_estimated,
                },
            )
        )

    def _reconcile_phase_tokens(self, step_id: str, step_tokens: int) -> None:
        """Emit a ``token`` frame for tokens ``run_plan`` counted but ``on_usage``
        did not capture (the raw/CLI-client case, where no UsageReport fires).

        Such tokens carry no in/out split and no usage telemetry, so they are
        booked as ``estimated`` output tokens — flipping the run's sticky ``~``,
        which is the honest signal for "this backend did not report a split".
        Keeps the HUD reconciled to ``DynamicPlanResult.total_tokens`` (SPEC §8).
        """
        remainder = step_tokens - self._phase_captured
        if remainder <= 0:
            return
        self._on_usage(
            UsageReport(input_tokens=0, output_tokens=remainder, estimated=True)
        )

    # -- the run -----------------------------------------------------------

    def run(self, requirement: str) -> None:
        """Execute the full pipeline for ``requirement``, emitting every event.

        §14.4 epoch heartbeat: when ``auto_improve`` is on AND ``max_epochs > 1``
        the run auto-iterates ``_run_inner`` up to ``max_epochs`` times — each pass
        seeds from the prior via the existing ``latest_with_content`` carry-forward —
        breaking early on plateau/converged or cancellation. Otherwise a single pass
        runs, exactly as before. The terminal ``done`` is emitted once, after the loop.
        """
        self._t0 = time.perf_counter()
        try:
            cfg = self._resolve_hc_config(requirement)
            self._effective_hc = cfg
            auto = bool(cfg.get("auto_improve"))
            max_epochs = int(cfg.get("max_epochs", 5))
            if auto and max_epochs > 1:
                for i in range(max_epochs):
                    self._epoch = i + 1
                    res = self._run_inner(requirement)
                    # Stop only on a genuine PLATEAU (this epoch's score didn't beat the
                    # prior by min_improvement) or cancel — NOT on "converged". "converged"
                    # is `_version >= max_epochs`, and `_version` is the CUMULATIVE all-time
                    # version (next_version = MAX(version)+1 over every prior run of this
                    # task). On a task with history it fires on the FIRST epoch, so the loop
                    # ran once and max_epochs=2 did a single pass while two MANUAL runs did
                    # two — the "1+1 ≠ 2 epochs" surprise. The per-run epoch count is already
                    # bounded by range(max_epochs); converged must not also gate it. (§14.8)
                    if res.status == "plateau":
                        break
                    if self._session.cancel_requested:
                        break
            else:
                self._epoch = 0
                self._run_inner(requirement)
            # Single terminal done for the whole stream (all epochs share one stream).
            self._emit(
                self._done_event(self._last_result, cancelled=self._last_cancelled)
            )
        except Exception as exc:  # noqa: BLE001 - any failure becomes an error frame
            self._emit(ErrorEvent(message=str(exc), where="runner"))
            # Still emit a terminal done so the frontend leaves the running state.
            self._emit(self._done_event("", cancelled=False))

    def _resolve_hc_config(self, requirement: str) -> dict:
        """Resolve the effective hill-climb config (DESIGN §14.4 persistence).

        Session config wins. When the session did not pin the epoch budget (no
        config, or a config missing ``max_epochs``) the per-task snapshot recorded
        by a prior run is loaded via ``TaskRunStore.latest_config`` — keyed by the
        SAME base ``task_hash`` the artifact carry-forward uses — so a requirement
        remembers its epoch budget across backend restarts. ``requirement`` here is
        the base requirement (goal/template are injected inside ``_run_inner`` only),
        so the key never forks the task identity (§14.1 D1).
        """
        session_cfg = dict(getattr(self._session, "hill_climb_config", None) or {})
        if "max_epochs" not in session_cfg:
            try:
                from studio.task_runs import (
                    TaskRunStore,
                    base_identity as _base_identity,
                    task_hash as _task_hash,
                )
                # PLAN item 5: key the config lookup on the lineage base, not the raw
                # (possibly conversational) requirement, so a "Continue run" finds its
                # prior epoch budget instead of cold-starting.
                persisted = TaskRunStore().latest_config(
                    _task_hash(_base_identity(requirement))
                )
            except Exception:  # noqa: BLE001 — persistence is best-effort
                persisted = {}
            if persisted:
                return {**persisted, **session_cfg}  # session always wins
        return session_cfg

    def _run_inner(self, requirement: str) -> EpochResult:
        session = self._session
        # §14.4: reset per-pass step/phase accumulators so each in-process epoch is a
        # clean sub-DAG (the panel trackers below are already rebuilt per pass).
        self._current_step_id = ""
        self._phase_captured = 0
        # Default outcome — if a pass errors before scoring, the loop treats it as
        # "improving" (score 0) and continues until max_epochs (DESIGN §14.4 #1).
        _outcome = EpochResult(version=0, score=0.0, delta=0.0, status="improving")

        # Task IDENTITY for hill-climb continuity is the BASE requirement, captured
        # BEFORE the goal/constraints block (just below) and the per-iteration prefix
        # (~L1281) are prepended. Hashing the augmented string forked the lineage every
        # time a goal was attached: same task, new task_hash → cold-start v1, no artifact
        # carry-forward, weakness-score 0/N = 0.00. The goal still STEERS the agent (it
        # stays in `requirement` for planning); it just no longer changes the task's
        # identity. Used at the auto_improve seed lookup and the run-record block below.
        _base_requirement = requirement

        # Inject goal end_state and constraints into requirement so the agent sees them
        # (worker goal + post-phase verification). NOT into the PLANNER input: when the
        # goal overlaps the task, prepending it doubled the text and the planner split the
        # repeat into DUPLICATE phases (the Pi/Craft run — a goal == the requirement made
        # "Craft…" / "create a report" appear twice). So the goal lives only in
        # `requirement`/`_original_requirement`; the planner reads `_plan_requirement`,
        # which starts from the clean base.
        _goal = getattr(session, "goal", None)
        if _goal is not None:
            _parts: list[str] = []
            if getattr(_goal, "end_state", None):
                _parts.append(f"Goal: {_goal.end_state}")
            _constraints = getattr(_goal, "constraints", None) or []
            if _constraints:
                _parts.append("Constraints:\n" + "\n".join(f"- {c}" for c in _constraints))
            if _parts:
                requirement = "\n".join(_parts) + "\n\n" + requirement

        # Wire the deliverable TEMPLATE into GENERATION (DESIGN §14.2): when the GUI has set
        # a rubric template, instruct the agent to produce those sections so the template
        # STEERS the report instead of only being graded after the fact. Appended AFTER
        # `_base_requirement` is captured (never changes task_hash), and to BOTH the full
        # `requirement` AND the goal-free `_plan_requirement` — distinct section names
        # (unlike the goal) never duplicate the task, so the template IS safe in the planner
        # input. Only when explicitly configured — defaulting to DEFAULT_TEMPLATE would
        # force report headings onto non-research tasks and compromise generation.
        _plan_requirement = _base_requirement
        _rc = getattr(session, "rubric_config", None) or {}
        _template = _rc.get("template")
        if _template:
            _sections = "\n".join(f"- {s}" for s in _template)
            _tpl_suffix = (
                "\n\nStructure the deliverable with these sections (use them as "
                "top-level headings, in order):\n" + _sections
            )
            requirement = requirement + _tpl_suffix
            _plan_requirement = _plan_requirement + _tpl_suffix

        # Stash the original requirement so task_hash is stable across iterations
        # (the seeder may rewrite requirement with "ITERATION N —..." prefix).
        _original_requirement = requirement

        # Hill climb: if auto_improve is on and a prior run exists for this task,
        # copy its artifact into the current workspace and prefix the requirement
        # with the prior score + weaknesses so the agent edits rather than regenerates.
        # Effective config = the one run() resolved (session ⊕ persisted, §14.4); fall
        # back to the raw session config when _run_inner is exercised directly.
        _hc_cfg = (
            self._effective_hc
            if self._effective_hc is not None
            else (getattr(session, "hill_climb_config", None) or {})
        )
        (
            requirement, _weaknesses_block, _artifact_copied, _eff_ws2,
            _seed_len, _seed_text,
        ) = self._seed_carry_forward(
            session=session,
            requirement=requirement,
            _base_requirement=_base_requirement,
            _hc_cfg=_hc_cfg,
        )

        # session frame
        self._emit(
            SessionEvent(llm=session.llm_info, embed=session.embed_info, mode=session.mode)
        )

        # build the usage-capturing client (injected factory in tests)
        base_client = self._build_client()
        # Wrap in a web_search tool loop when tools are enabled (run_plan stays
        # unchanged — it sees a plain LLMClient that happens to run a tool loop).
        # When a prior artifact was seeded, also offer read_artifact/patch_artifact
        # so concurrent workers can apply OCC patches directly to artifact.md.
        _art_for_tools = (
            _eff_ws2 / session.session_id / "artifact.md"
            if _artifact_copied and _eff_ws2 is not None
            else None
        )
        client = self._maybe_tool_augment(base_client, artifact_path=_art_for_tools)

        # plan → emit plan. Three paths:
        #   1. Seeded session  → pre-seed decomposition from a loop-library loop.
        #   2. LLM mode        → EPIC-BASED planning (DESIGN §2.3): the planner
        #                        LLM emits an EPIC_PLAN; each epic is one phase.
        #   3. Offline/auto    → deterministic plan() (no LLM available; tests).
        seed_steps = session.seed_steps
        use_llm = session.mode == "llm"
        # Plan from the BASE requirement, NOT the goal/template-injected `requirement`.
        # The goal end_state is prepended to `requirement` for steering, but when it
        # OVERLAPS the task the planner split the DOUBLED text into duplicate phases — a
        # goal == the requirement produced "Craft…" / "create a report" twice (the Pi/Craft
        # run). The goal still steers via the keep/discard gate + verification; it must not
        # become phase-splitting text. _base_requirement is also free of the
        # weakness/template bloat the epic planner already wanted to avoid.
        if seed_steps:
            plan_obj = plan(_plan_requirement, decomposer=make_seeded_decomposer(seed_steps))
            self._emit(LoopSeedEvent(loop_id=session.seed_loop_id, steps=seed_steps))
        elif use_llm:
            # Planner runs on base_client (no tool loop — planning needs no web).
            plan_obj = _plan_from_epics(
                _plan_requirement, base_client, weaknesses_block=_weaknesses_block
            )
        else:
            plan_obj = plan(_plan_requirement)
        # Collapse duplicate phases regardless of which planner produced them (seeded,
        # epic-LLM, or deterministic): a goal listing several sub-tasks otherwise yields
        # the same phase twice in the DAG (the Pi/Craft run), doubling agents + tokens.
        plan_obj = _dedupe_plan_steps(plan_obj)
        # §14.4 re-entrancy: when the epoch loop replays _run_inner in-process, prefix
        # every step id (and its depends_on edges) with the epoch index so each pass is
        # a distinct sub-DAG and ids never collide across epochs (e.g. e2:s3). Single-pass
        # runs keep epoch 0 and are left un-prefixed (back-compat for existing tests).
        if self._epoch > 0:
            _pfx = f"e{self._epoch}:"
            plan_obj = replace(
                plan_obj,
                steps=tuple(
                    replace(
                        s,
                        id=_pfx + s.id,
                        depends_on=tuple(_pfx + d for d in s.depends_on),
                    )
                    for s in plan_obj.steps
                ),
            )
        # Capture the plan-as-dicts once: the PlanEvent payload AND the input the
        # Loop Doctor audits (its clear_stopping check walks this DAG at run end).
        plan_step_dicts = [
            {
                "id": s.id,
                "description": s.description,
                "depends_on": list(s.depends_on),
                "role": s.role,
                "difficulty": s.difficulty,
            }
            for s in plan_obj.steps
        ]
        self._emit(PlanEvent(task=plan_obj.task, steps=plan_step_dicts))

        # assign topologies (auto; llm path only when mode=='llm' AND client given).
        # use_llm already computed above for the epic-planning branch.
        plan_obj = assign_topologies(
            plan_obj, mode="auto", client=client, llm=use_llm
        )
        # Hill-climb REQUIRES STAR on every phase (DESIGN §11.4): only STAR's
        # reducer does the section-aware merge/refine/review of each worker's
        # per-section output against that section's weakness list, producing the
        # section-keyed {document, weaknesses} handoff that the next phase (and
        # next epoch) accumulates. MESH/PIPELINE/SINGLE have no such reducer, so
        # auto-derived topology would silently break the improvement loop. The
        # breadth cap (run_plan max_agents) keeps the forced STAR from exploding.
        if _hc_cfg.get("auto_improve"):
            plan_obj = replace(
                plan_obj,
                steps=tuple(replace(s, topology=STAR) for s in plan_obj.steps),
            )
        topology_map = {s.id: (s.topology or SINGLE) for s in plan_obj.steps}
        self._emit(
            TopologyEvent(
                steps=[{"id": sid, "topology": topo} for sid, topo in topology_map.items()]
            )
        )

        # derived render graph
        self._emit(_render_graph(plan_obj))

        # panel trackers
        dag = DagTracker(plan_obj)
        self._emit(dag.snapshot())
        mem = MemoryTracker(self._embedder)
        selfimp = SelfImproveTracker()
        budget = (
            FanoutBudget(ceiling=session.budget_ceiling)
            if session.budget_ceiling is not None
            else None
        )

        # M8: cross-phase TaskLedger and dynamic sizing (DESIGN §3, §5)
        from agentkit.orchestrator.ledger import TaskRecord, TaskLedger
        _ledger = TaskLedger()
        # Seed the ledger with every planned phase UP FRONT (DESIGN §2.3 / §3.2).
        # Without this, all_tasks stayed empty and remaining() was structurally
        # always empty — the REMAINING block printed "(none)" and worker sizing
        # saw max(1,0)=1 every phase. Seeding makes remaining() reflect real
        # pending work; mark_done() (end of loop) moves each finished phase to
        # completed, so later hubs see an accurate COMPLETED-vs-REMAINING split
        # and never re-assign prior-phase work.
        for _s in plan_obj.steps:
            _ledger.add_task(TaskRecord(id=_s.id, description=_s.description[:120]))
        _lc = getattr(session, "loop_config", None)
        _sizing_cfg = _lc.sizing() if _lc is not None else None

        outputs: dict[str, str] = {}
        _reducer_gaps: list[str] = []   # §11.4 last-phase gaps → next-run weaknesses

        # §14.1: create == improve. When improving but NO prior doc exists yet,
        # bootstrap a skeleton (headings + placeholders from the goal, no search) so
        # the phase loop fills it ADDITIVELY — the same pipeline as improving an
        # existing doc, instead of asking one LLM to author the whole report.
        if (_hc_cfg.get("auto_improve") and not _artifact_copied
                and _eff_ws2 is not None and use_llm):
            _skel = _build_skeleton(plan_obj.task or requirement, base_client,
                                    embedder=self._embedder)
            if _skel:
                _skel_file = _eff_ws2 / session.session_id / "artifact.md"
                _skel_file.parent.mkdir(parents=True, exist_ok=True)
                _skel_file.write_text(_skel)
                _artifact_copied = True       # additive pipeline now has a base
                _seed_len = len(_skel)        # may grow from here, never shrink below

        # §14.6: a SEEDED run keeps the seed's section structure — the reducer
        # PATCHES existing headings, it never injects a missing one. So a rubric-
        # template section absent from the seed (e.g. "Limitations and Open
        # Questions") is mined as a weakness EVERY epoch yet never created: there is
        # no PATCH_TARGET heading to fill, so hill-climb "can't create new sections."
        # Fix: append each MISSING template section as an empty heading + placeholder
        # so the additive patch pipeline has a target to fill. Concept-aware match
        # (sections_present) avoids re-adding a renamed-but-present section. Covers
        # both paths — on cold start the skeleton already has every section, so the
        # missing set is empty and this is a no-op. Only when a template is configured.
        _tmpl_sections = (getattr(session, "rubric_config", None) or {}).get("template")
        if _artifact_copied and _tmpl_sections and _eff_ws2 is not None:
            _art_f = _eff_ws2 / session.session_id / "artifact.md"
            try:
                _cur = _art_f.read_text()
                _merged = _merge_missing_sections(_cur, _tmpl_sections)
                if _merged != _cur:
                    _art_f.write_text(_merged)
                    _seed_len = len(_merged)
                    _dbg("seeded missing template section(s)")
            except Exception:  # noqa: BLE001 — structure-merge is best-effort
                pass
        #: Gate outcomes collected across phases — the Loop Doctor's safe_actions
        #: check reads these at run end (no re-running of any gate).
        gate_events: list[GateEvent] = []

        cancelled, final_output, _seed_text = self._run_phase_loop(
            session=session,
            plan_obj=plan_obj,
            client=client,
            base_client=base_client,
            budget=budget,
            _ledger=_ledger,
            _sizing_cfg=_sizing_cfg,
            _lc=_lc,
            _eff_ws2=_eff_ws2,
            _artifact_copied=_artifact_copied,
            _seed_len=_seed_len,
            _seed_text=_seed_text,
            use_llm=use_llm,
            requirement=requirement,
            mem=mem,
            dag=dag,
            selfimp=selfimp,
            outputs=outputs,
            gate_events=gate_events,
            _reducer_gaps=_reducer_gaps,
        )

        # budget gauge
        if budget is not None:
            self._emit(
                BudgetEvent(
                    spent=budget.spent_total,
                    ceiling=session.budget_ceiling,
                    exceeded=False,
                )
            )

        # If the final step produced less than its direct predecessor, fall back
        # to the predecessor's output. In research loops, the last step is a
        # meta "stop/continue" decision — the real artifact lives in the step it
        # depends on (its direct predecessor in the DAG).
        last_step = plan_obj.steps[-1] if plan_obj.steps else None
        predecessor_id = (
            last_step.depends_on[-1] if (last_step and last_step.depends_on) else None
        )
        predecessor_output = outputs.get(predecessor_id, "") if predecessor_id else ""
        result_output = (
            predecessor_output
            if predecessor_output and len(predecessor_output) > len(final_output)
            else final_output
        )

        # Steps that write their artifact to artifact.md produce content in a file
        # rather than the LLM text response, so prefer the file — BUT only when it is
        # complete. Auto-improve copies the prior best artifact into the workspace as a
        # seed; if the agent doesn't overwrite it, the file is a STALE (and here,
        # truncated) seed. The old "prefer the longest text" rule then re-kept that
        # truncated seed over the agent's fresh, complete-but-shorter synthesis — every
        # iteration re-scored the same truncated text and the score could never climb
        # past the "not truncated" criterion. Fix: only prefer the file when it is longer
        # AND ends cleanly; a truncated file loses to the agent's actual final output.
        ws_artifact = self._read_workspace_artifact()
        if ws_artifact and len(ws_artifact) > len(result_output) and _ends_cleanly(ws_artifact):
            result_output = ws_artifact

        # §11.10: strip any reducer commentary preamble from the DISPLAYED/stored
        # result too — not just artifact.md. A reducer that narrated ("The artifact
        # is complete… Weaknesses addressed: ✅… Remaining concern: future-dated…")
        # leaves that in result_output even when artifact.md was sanitized, since
        # the preamble version is longer and wins the length check above. That
        # commentary belongs in the surfaced _unresolved_block (chat), never in the
        # deliverable shown to the user.
        result_output = _strip_preamble(result_output)

        # verification (pure tier, always runs)
        verify_event = build_verify_event(result_output)
        self._emit(verify_event)

        # Loop Doctor (M8): audit the finished run against loop-library's
        # checklist, composed from the run's collected gate/verify outcomes +
        # the budget ceiling + the plan DAG. Suggestions only — never applied.
        loopdoctor_event = build_loopdoctor_event(
            plan_step_dicts,
            budget_ceiling=session.budget_ceiling,
            gate_events=gate_events,
            verify_event=verify_event,
        )
        self._emit(loopdoctor_event)

        # Record the finished run so GET /export can serialize it to a loop (M9).
        session.record_run(
            RunSnapshot(
                requirement=requirement,
                plan_steps=plan_step_dicts,
                topology=topology_map,
                loopdoctor_checks=loopdoctor_event.checks,
                budget_ceiling=session.budget_ceiling,
                result=result_output,
                cancelled=cancelled,
            )
        )

        _outcome, result_output = self._postrun_score_and_record(
            session=session,
            result_output=result_output,
            outputs=outputs,
            base_client=base_client,
            use_llm=use_llm,
            _base_requirement=_base_requirement,
            _original_requirement=_original_requirement,
            _artifact_copied=_artifact_copied,
            _seed_text=_seed_text,
            _reducer_gaps=_reducer_gaps,
            _hc_cfg=_hc_cfg,
            _outcome=_outcome,
        )

        # §14.4: the terminal `done` now lives in run() (emitted once after the epoch
        # loop). Stash this pass's final output + cancel flag so run() can build it,
        # and hand back the per-epoch outcome that drives continue/stop.
        self._last_result = result_output
        self._last_cancelled = cancelled
        return _outcome

    def _seed_carry_forward(
        self, *, session, requirement: str, _base_requirement: str, _hc_cfg: dict,
    ) -> tuple[str, str, bool, object, int, str]:
        """Hill-climb seed carry-forward (DESIGN §14.4 / §14.6 / §11.4).

        When auto_improve is on and a prior run exists for this task, copy its artifact
        into the current workspace, accumulate prior+similar-task weaknesses, and (when a
        real seed exists) switch the requirement to the patch-or-silent worker contract.
        Returns ``(requirement, _weaknesses_block, _artifact_copied, _eff_ws2, _seed_len,
        _seed_text)``. Extracted verbatim from ``_run_inner``; behavior unchanged.
        """
        _artifact_copied = False
        _eff_ws2 = None
        _weaknesses_block = ""  # prior-run lessons → planner/hub constraints
        _seed_len = 0           # length of the seeded prior artifact (anti-regression)
        _seed_text = ""         # full prior artifact text (Phase-1 keep/discard gate)
        # §11.4 loop-closure check: normalized weaknesses recorded in >= REPEAT_LIMIT
        # prior runs of this task were injected and never fixed. The reducer drops
        # them from its handoff (below) instead of grinding on them forever. Empty
        # when not hill-climbing.
        _repeat_failed: set[str] = set()
        if _hc_cfg.get("auto_improve"):
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
            _prior = _store.latest_with_content(_thash, ws_root=_eff_ws2)
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
            _seed_len, _seed_text,
        )

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
    ) -> tuple[bool, str, str]:
        """Per-phase execution loop + the post-loop atomic patch-apply
        (DESIGN §3 / §5 / §11). Drives each phase through ``run_plan`` on a single-step
        sub-plan, emits the per-phase event sequence, writes back the grow-only artifact,
        then folds worker PATCHES/RESEARCH_FINDING blocks into artifact.md. Mutates
        ``outputs`` / ``gate_events`` / ``_reducer_gaps`` in place; returns
        ``(cancelled, final_output, _seed_text)``. Behavior and event ordering are
        unchanged — extracted verbatim from ``_run_inner``.
        """
        cancelled = False
        final_output = ""
        for step in plan_obj.steps:
            if session.cancel_requested:
                cancelled = True
                break

            self._current_step_id = step.id
            self._phase_captured = 0
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
            if plan_obj.task and plan_obj.task not in desc:
                desc = f"TASK: {plan_obj.task}\n\n{desc}"
            # On the final step, if there is upstream content, prefix with an
            # explicit instruction to output the artifact rather than asking for
            # more context. Loop catalog "stop" steps are written for humans; the
            # LLM needs an imperative framing to produce the artifact, not a
            # meta-decision about whether to continue.
            if is_last and upstream:
                if _artifact_copied:
                    # Prior artifact seeded into workspace. The reducer has no read_file
                    # tool, so inject the seeded content directly into the prompt.
                    # Workers produced RESEARCH_FINDING blocks in their text output;
                    # the reducer applies each block to the artifact independently.
                    # After the step runs we write sr.output back to artifact.md.
                    _seed_text = ""
                    if _eff_ws2 is not None:
                        try:
                            _seed_text = (_eff_ws2 / session.session_id / "artifact.md").read_text()
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
                        _seed_lints = lint_artifact(_seed_text)
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
                    _art_ctx = (
                        f"CURRENT ARTIFACT (from prior run — base to improve):\n"
                        f"--- BEGIN ARTIFACT ---\n{_seed_text}\n--- END ARTIFACT ---\n\n"
                        if _seed_text else ""
                    )
                    desc = (
                        f"You are the reducer in a multi-worker research pipeline.\n"
                        f"You are an ADDITIVE MERGER, never a rewriter (DESIGN §11.3).\n\n"
                        f"Workers searched the web and produced RESEARCH_FINDING blocks above "
                        f"(each has ARTICLE_TITLE / URL / POPULARITY / PATCH_TARGET fields).\n\n"
                        f"ABSOLUTE RULES — violating these REGRESSES the deliverable:\n"
                        f"  - PRESERVE every existing section of the CURRENT ARTIFACT VERBATIM.\n"
                        f"    Do NOT summarize, shorten, condense, re-word, or remove anything.\n"
                        f"  - You may ONLY ADD content that comes from a worker's RESEARCH_FINDING\n"
                        f"    (with its URL). No finding for a section → leave that section exactly\n"
                        f"    as-is.\n"
                        f"  - CITE ONLY a URL that appears verbatim in a worker RESEARCH_FINDING\n"
                        f"    above. NEVER invent, guess, or alter a URL. If a claim has no such\n"
                        f"    URL, state it WITHOUT a citation — a fabricated link is worse than\n"
                        f"    none (it is detected and penalised).\n"
                        f"{_repair_clause}"
                        f"  - If workers found NOTHING (no RESEARCH_FINDING blocks above) AND there\n"
                        f"    is no repair exception above, output the CURRENT ARTIFACT completely\n"
                        f"    unchanged. Never write a 'blocker' or 'search unavailable' report —\n"
                        f"    that is failure-narration, not content.\n\n"
                        f"How to apply each RESEARCH_FINDING:\n"
                        f"  1. Find PATCH_TARGET in the artifact.\n"
                        f"  2. URL missing inline → add it next to the citation.\n"
                        f"  3. POPULARITY missing → add it in parentheses.\n"
                        f"  4. New article → add a summary paragraph + a References entry with the URL.\n\n"
                        f"Output: the CURRENT ARTIFACT with additions applied — every original\n"
                        f"section intact, output length STRICTLY >= the input length (a shorter\n"
                        f"output is rejected and the prior good doc is kept).\n\n"
                        f"{_art_ctx}"
                        f"Workflow instruction: {desc}"
                    )
                else:
                    desc = (
                        f"You are the final step of a multi-step agent workflow. "
                        f"The prior steps have already produced the following output. "
                        f"Your job: return the complete, final artifact exactly as produced "
                        f"by the prior steps (optionally refining it). "
                        f"Do NOT ask for more context or input — all necessary work is already done.\n\n"
                        f"Workflow instruction: {desc}"
                    )
            sub_step = replace(
                step, description=_with_upstream(desc, upstream), depends_on=()
            )
            sub_plan = Plan(task=plan_obj.task, steps=(sub_step,))

            # M9: inject hub CoT prompt when loop_config active and phase fans out.
            # The step description becomes the hub's system prompt inside run_plan.
            # STAR/MAP ONLY — by design: these are the section-partition fan-outs
            # the hub plans an ASSIGNED block for. MESH (debate) and PIPELINE
            # (ordered stages) have no section partition, so they skip the hub
            # CoT (no sizing/assignment features). Their breadth is still bounded
            # — run_plan's max_agents caps _facets/PIPELINE stages regardless.
            if _lc is not None and topo in (STAR, MAP):
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
                _executor_desc = _build_executor_prompt(
                    goal=plan_obj.task or requirement,
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
                    _cur_art = _strip_preamble(
                        (_eff_ws2 / session.session_id / "artifact.md").read_text()
                    )
                except OSError:
                    pass
                _reducer = _make_section_reducer(
                    client, _cur_art, getattr(session, "weaknesses", []) or [],
                    embedder=self._embedder,   # F1: dedup near-duplicate findings
                )

            try:
                result = run_plan(
                    sub_plan, client, budget=budget,
                    max_workers=_max_workers, max_agents=_max_agents,
                    reducer=_reducer,
                )
            except BudgetExceeded as exc:
                self._emit(
                    BudgetEvent(spent=exc.spent, ceiling=session.budget_ceiling, exceeded=True)
                )
                cancelled = True
                break

            sr = result.runs[0]
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
            # F2: per-section ratchet. The old whole-doc grow-only rule (len >= _seed_len)
            # rejected ANY shrink, blocking dedup/replace/repair. accept_rewrite allows a
            # rewrite (even shorter) as long as no section that had CONTENT is deleted or
            # gutted — preserving anti-regression at section granularity.
            _art_path = (_eff_ws2 / session.session_id / "artifact.md") if _eff_ws2 is not None else None
            _old_art = ""
            if _art_path is not None:
                try:
                    _old_art = _art_path.read_text()
                except OSError:
                    pass
            from agentkit.artifacts.sections import accept_rewrite
            if (_artifact_copied and _art_path is not None
                    and len(_clean_out.strip()) > 0
                    and accept_rewrite(_old_art, _clean_out)):
                try:
                    _art_path.write_text(_clean_out)
                    _dbg(f"writeback ACCEPT step={step.id} {len(_old_art)}→{len(_clean_out)}")
                    _seed_len = len(_clean_out)   # track current length for the next phase
                except OSError:
                    pass
            elif _artifact_copied and _art_path is not None:
                _dbg(f"writeback REJECT step={step.id} clean_len={len(_clean_out)} "
                     f"(accept_rewrite: a sourced section was deleted/gutted)")

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
                )
            )

            # M8: record this phase as completed (all_tasks was seeded up front,
            # so mark_done moves it from remaining/in-flight to completed).
            _ledger.mark_done(step.id)

            # §14.7: append this phase's INPUT (the full prompt the agent was given) and
            # OUTPUT to a per-session JSONL so a failed run is diagnosable offline — the
            # fastest way to SEE goal/intent stacking, an upstream fold, a recalled
            # refusal, or a worker that dumped raw findings instead of patching. Lives
            # next to artifact.md in the workspace. Best-effort; never breaks the run.
            if _eff_ws2 is not None:
                try:
                    import json as _json
                    _io_path = _eff_ws2 / session.session_id / "agent_io.jsonl"
                    with _io_path.open("a", encoding="utf-8") as _iof:
                        _iof.write(_json.dumps({
                            "step": step.id,
                            "topology": topo,
                            "input": desc,
                            "output": sr.output,
                            "n_agents": sr.n_agents,
                            "tokens": sr.tokens,
                        }) + "\n")
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
                # Phase 2 (DESIGN §2.2 Step 5): a full-document editorial refine pass
                # over the structurally-merged text. Only with a real LLM (mode=='llm');
                # offline/canned backends would corrupt the artifact, so refine is None.
                # The closure guards output length so a short/confused response cannot
                # clobber a clean merge (mirrors the >5000-char guard above).
                _refine_fn = None
                if use_llm:
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
                        if len(out) >= int(len(merged_text) * 0.8):
                            return out
                        return merged_text

                _rr = reduce_patches(_cur_text, _patch_groups, llm_refine_fn=_refine_fn)
                # Anti-regression guard: never replace the seed with a shorter
                # merged doc (worst case = no improvement, never regression).
                if _rr.text and len(_rr.text) >= _seed_len:
                    write_artifact(_art_file, _rr.text)
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

    def _postrun_score_and_record(
        self,
        *,
        session,
        result_output: str,
        outputs: dict[str, str],
        base_client,
        use_llm: bool,
        _base_requirement: str,
        _original_requirement: str,
        _artifact_copied: bool,
        _seed_text: str,
        _reducer_gaps: list[str],
        _hc_cfg: dict,
        _outcome: EpochResult,
    ) -> tuple[EpochResult, str]:
        """Post-run pipeline (DESIGN §14): score -> synthesize -> repair_lints ->
        neutralize URLs -> mine weaknesses -> epoch gate -> rubric/adjusted score ->
        record -> emit HillClimbEvent. Extracted verbatim from ``_run_inner``; the
        stage ORDER is load-bearing and unchanged, and every fail-open ``try`` guard
        is preserved. Returns ``(outcome, result_output)`` — ``result_output`` may be
        mutated by the synthesis/repair/neutralize/epoch-gate stages.
        """
        # Hill climb post-run: score output, mine weaknesses, record, emit HillClimbEvent.
        # Runs regardless of hill_climb_config so task_hash-based lookup always has data.
        try:
            from studio.task_runs import (
                TaskRun,
                TaskRunStore,
                base_identity as _base_identity,
                mine_weaknesses_from_outputs,
                score_result,
                task_hash as _task_hash,
            )
            # Embedder wired so this run's requirement is embedded on record()
            # → future runs can find it via similar_runs() (R10).
            _store = TaskRunStore(embedder=self._embedder)
            # Hash the BASE requirement (goal-invariant identity) — must match the
            # auto_improve seed-lookup hash above so a run records under the same
            # task_hash it seeded from. PLAN item 5: base_identity strips the GUI
            # continuation wrapper so a continued run records into the prior lineage.
            _thash = _task_hash(_base_identity(_base_requirement))
            from studio.workspace import workspace_root as _ws_root_fn
            _effective_ws_root = self._workspace_root or _ws_root_fn()
            _art_file = _effective_ws_root / session.session_id / "artifact.md"
            _art_path = str(_art_file)
            # Score the PERSISTED artifact, not the loose result_output. The final phase's
            # returned text and the artifact.md it wrote to disk can diverge (a phase may
            # return a short status string while the full report lives in the file). Since
            # auto-improve seeds the NEXT run from artifact.md, scoring anything else means
            # scoring one text and carrying forward another — the cause of phantom scores
            # (e.g. a recorded 0.50 on a report that re-scores 0.80). Prefer the file when
            # it is at least as substantial as the return; fall back to result_output.
            _scored_text = result_output
            try:
                if _art_file.exists():
                    _file_text = _art_file.read_text()
                    if len(_file_text.strip()) >= len((result_output or "").strip()):
                        _scored_text = _file_text
            except Exception:  # noqa: BLE001 - a read failure must not break recording
                pass
            # Scoring and weakness mining must use the RAW client (base_client), not the
            # ToolAugmentedClient. When the scorer has web_search available, it calls it
            # to verify citations — fabricated or paywalled articles score 0.0 even when
            # the output quality is genuinely good. The scorer is an LLM judge, not a
            # research agent; it must not make live web calls.
            _judge_client = base_client
            # Check scored text URLs against web cache — real (cached) URLs get marked
            # as verified so the judge doesn't penalise genuine citations as fabricated.
            _verified_urls: list[str] = []
            try:
                import json as _json
                import os as _os
                from studio.task_runs import verified_urls_in_cache
                if _os.path.exists(".web_cache.json"):
                    with open(".web_cache.json") as _cf:
                        _verified_urls = verified_urls_in_cache(
                            _json.load(_cf), _scored_text or ""
                        )
            except Exception:  # noqa: BLE001
                pass
            # PLAN item 1A: synthesis/analysis pass. The additive reducer cannot rewrite
            # (anti-regression §14.6), so its output is grounded-but-pasted. This adds an
            # analysis layer (interpretation + cross-source comparison) without dropping any
            # citation. Runs once per pass on the raw judge client (no tools/fetch), only on
            # a substantial, citation-bearing research doc; rejected if it loses a URL.
            if use_llm and _scored_text and len(_scored_text) > 800 and "http" in _scored_text:
                try:
                    _syn, _changed = _synthesize_analysis(
                        _scored_text, base_client, _original_requirement
                    )
                    if _changed:
                        _scored_text = _syn
                        result_output = _syn
                        # Re-derive the verified set against the synthesized text.
                        try:
                            import json as _json2
                            import os as _os2
                            from studio.task_runs import verified_urls_in_cache as _vuc
                            if _os2.path.exists(".web_cache.json"):
                                with open(".web_cache.json") as _cf2:
                                    _verified_urls = _vuc(_json2.load(_cf2), _scored_text)
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001 — synthesis must never break recording
                    pass
            # §14.6 root-cause fix (reported broken-diagram bug): lint the OUTPUT and, if a
            # mermaid block is malformed, repair JUST that block via the model and splice it
            # back deterministically (whole-doc repair truncates a large artifact — verified).
            # Covers the single-epoch/cold-start case the seed-only repair clause and the
            # next-epoch self-heal both miss. No-op when the document is already clean.
            # NOT gated on use_llm: output VALIDITY is independent of the generation mode —
            # a hill-climb run in the default "auto" mode (use_llm False) still ships a doc
            # whose mermaid must be valid. base_client is always built, so repair can run.
            if base_client is not None and _scored_text:
                try:
                    from studio.artifact_lint import lint_artifact as _lint_dbg
                    _lints_before = _lint_dbg(_scored_text)
                    _rep, _rchanged = _repair_lints(
                        _scored_text, base_client, _original_requirement
                    )
                    # Observability (the bug was un-diagnosable because repair was silent):
                    # record whether it ran, fired, and the residual lint count.
                    _dbg(
                        f"repair_lints: lints_before={len(_lints_before)} "
                        f"changed={_rchanged} lints_after={len(_lint_dbg(_rep))}"
                    )
                    if _rchanged:
                        _scored_text = _rep
                        result_output = _rep
                        try:
                            if _art_file.exists():
                                _art_file.write_text(_scored_text)
                        except Exception:  # noqa: BLE001 — write-back is best-effort
                            pass
                except Exception as _rexc:  # noqa: BLE001 — repair must never break recording
                    _dbg(f"repair_lints: EXCEPTION {_rexc!r}")
            # PLAN item 3: neutralize fabricated/unverified URLs before scoring AND serving,
            # so a reducer-invented link cannot earn citation credit or reach the user.
            # FAIL-OPEN — an empty verified set (search down) changes nothing.
            try:
                from studio.task_runs import neutralize_unverified_urls
                _cleaned = neutralize_unverified_urls(_scored_text, _verified_urls)
                if _cleaned != _scored_text:
                    _scored_text = _cleaned
                    result_output = neutralize_unverified_urls(result_output, _verified_urls)
                    try:
                        if _art_file.exists():
                            _art_file.write_text(_scored_text)
                    except Exception:  # noqa: BLE001 — write-back is best-effort
                        pass
            except Exception:  # noqa: BLE001
                pass
            _score, _scorer_feedback = score_result(
                _scored_text, _original_requirement, _judge_client,
                verified_urls=_verified_urls or None,
            )
            # Mine against the full artifact so the miner sees real content (URLs,
            # citations, conclusions). Pass result_output in the outputs dict to let
            # the miner still catch synthesis failures like "workers returned status
            # only". Without this, the miner saw the 3K reducer response instead of
            # the 28K artifact and reported "no URLs" when 8 real URLs already existed.
            _mine_outputs = {k: v for k, v in outputs.items()}
            if result_output:
                _mine_outputs["reducer_response"] = result_output
            _weaknesses = mine_weaknesses_from_outputs(
                _mine_outputs,
                _scored_text,
                _original_requirement,
                _judge_client,
                scorer_feedback=_scorer_feedback,
                verified_urls=_verified_urls or None,  # cache-as-oracle (§11.10)
            )
            # §11.4: prepend the reducer's last-phase gaps — they are concrete and
            # grounded ("§Results: no source URL"), so they make the next run's
            # constraints specific. Dedup against the LLM-mined set.
            if _reducer_gaps:
                _seen_w = set(_weaknesses)
                _weaknesses = [g for g in _reducer_gaps if g not in _seen_w] + _weaknesses
            # §14.6: deterministic content-validity lints (malformed mermaid edge,
            # truncated code fence) the gap-based miner never names. Prepend so they
            # seed the next run's constraints and the reducer repairs them in place
            # (accept_rewrite already permits the rewrite). Concrete + grounded, like
            # _reducer_gaps. Surfaced to the user via HillClimbEvent.weaknesses too.
            try:
                from studio.artifact_lint import lint_artifact
                _lints = lint_artifact(_scored_text or "")
                if _lints:
                    _seen_w = set(_weaknesses)
                    _weaknesses = [w for w in _lints if w not in _seen_w] + _weaknesses
            except Exception:  # noqa: BLE001 — lint is best-effort, never block recording
                pass
            # Deterministic section guard (DESIGN §14.2): the moving-window miner now sees the
            # whole artifact, but the SCORER still windows (20K) and its UNMET line is fed to
            # the miner as a starting point — so a tail section past the scorer's window can
            # still echo through as a false "missing X". Drop any missing/absent-section
            # weakness for a section the concept-aware FULL-TEXT check confirms is present, so
            # a complete report is not penalised for a blind spot. Only when a rubric template
            # is configured (else there is no authoritative section list).
            _tmpl = (getattr(session, "rubric_config", None) or {}).get("template")
            if _tmpl and _weaknesses:
                from studio.rubric import sections_present
                _present = {s.lower() for s in sections_present(_scored_text, _tmpl)}
                if _present:
                    _weaknesses = [
                        _w for _w in _weaknesses
                        if not (
                            ("missing" in _w.lower() or "absent" in _w.lower())
                            and any(_s in _w.lower() for _s in _present)
                        )
                    ]
            # Deterministic report publish gate: catches outputs that are structurally clean
            # but fail the user's report contract (for example no citations or topic drift).
            # It does not replace LLM planning; it only surfaces a final readiness verdict and
            # feeds failures into the existing weakness/adjusted-score path.
            try:
                from studio.report_quality import evaluate_publish_readiness
                _publish = evaluate_publish_readiness(
                    _original_requirement,
                    _scored_text or result_output or "",
                    verified_urls=_verified_urls or None,
                )
                _pg = GateEvent(
                    name="publish-ready",
                    outcome=_publish.outcome,
                    detail=_publish.detail,
                    sandboxed=True,
                )
                self._emit(_pg)
                if _publish.issues:
                    _seen_w = set(_weaknesses)
                    _weaknesses = [w for w in _publish.issues if w not in _seen_w] + _weaknesses
            except Exception:  # noqa: BLE001 — publish gate must never break recording
                pass
            # Semantic dedup: the moving-window miner can surface the SAME issue under two
            # section prefixes (e.g. the popularity-ranking gap as both [## Source Selection]
            # and [## Key Findings]). Exact-string dedup misses these re-phrasings, so they
            # count as two unsolved items and depress solved/total — part of why the recorded
            # score jitters epoch-to-epoch on an improving document. Collapse near-duplicates
            # (cosine >= the same 0.85 threshold _weakness_score uses), keeping the first.
            if self._embedder is not None and len(_weaknesses) > 1:
                from studio.task_runs import _cosine
                try:
                    _wvecs = self._embedder.embed(_weaknesses)
                    _kept: list[str] = []
                    _kept_vecs: list = []
                    for _wk, _wv in zip(_weaknesses, _wvecs):
                        if any(_cosine(_wv, _kv) >= 0.85 for _kv in _kept_vecs):
                            continue
                        _kept.append(_wk)
                        _kept_vecs.append(_wv)
                    _weaknesses = _kept
                except Exception:  # noqa: BLE001 — dedup is best-effort; embedder may be down
                    pass
            # Weaknesses are now an IMPROVEMENT SIGNAL only — they seed the next run's
            # constraints — NOT the score. The recorded score is the deterministic rubric,
            # computed AFTER the keep/discard gate below so it scores the artifact actually
            # kept. (solved/total retired: a count-based score punished thoroughness — more
            # mined weaknesses lowered it even as the document improved; DESIGN §14.2.)
            # (Remaining weaknesses are surfaced below the report via HillClimbEvent.weaknesses
            # — the frontend renders them — never written into result_output / the document.)
            # Phase-1 keep/discard gate (DESIGN §14.1): when this epoch seeded from a
            # prior best, KEEP its artifact only if a label-free judge strictly prefers
            # it over the seed. On reject, restore the prior so the carry-forward seed
            # never regresses (worst case = prior good report retained). Cold start
            # (no seed) always accepts. Reuses agentkit.evolve.self_preference via
            # studio.epoch_gate; a judge failure fails OPEN (no worse than the old
            # ungated length ratchet).
            if _artifact_copied and _seed_text.strip():
                from studio.epoch_gate import accept_epoch, make_rubric_preference
                if self._prefer_fn is not None:
                    _pf = self._prefer_fn
                    _prefer = lambda _n, _p: _pf(_n, _p, _original_requirement)
                else:
                    # DEFAULT judge = deterministic research-report rubric (DESIGN §14.2).
                    # An LLM "which is better?" judge ties strong-vs-stub even on sonnet
                    # (§14.1 D4); the rubric separates them reproducibly. _verified_urls
                    # is the accuracy oracle (URLs confirmed real via the web cache).
                    # Weights + deliverable template come from the GUI rubric_config.
                    _rc = getattr(session, "rubric_config", None) or {}
                    _prefer = make_rubric_preference(
                        _verified_urls or None,
                        weights=_rc.get("weights"),
                        required_sections=_rc.get("template"),
                    )
                try:
                    if not accept_epoch(_scored_text, _seed_text, _prefer):
                        _art_file.write_text(_seed_text)   # revert carry-forward seed
                        result_output = _seed_text
                        _scored_text = _seed_text
                        _dbg("epoch gate: reverted to prior (new not preferred)")
                    else:
                        _dbg("epoch gate: kept new epoch (preferred over prior)")
                except Exception:  # noqa: BLE001 — gate must never crash the run
                    pass
            # Recorded score = deterministic RUBRIC over the FINAL (post-gate) artifact —
            # the metric that actually tracks quality (DESIGN §14.2). Computed from the clean
            # _scored_text BEFORE the weakness annotation is appended, so the score is not
            # polluted by it. Weights + template come from the GUI rubric_config.
            from studio.rubric import rubric_score, adjusted_score
            _rcfg = getattr(session, "rubric_config", None) or {}
            _rubric_base = rubric_score(
                _scored_text or result_output or "",
                verified_urls=_verified_urls or None,
                weights=_rcfg.get("weights"),
                required_sections=_rcfg.get("template"),
            )
            # §14.7: the structural rubric measures QUANTITY (sections, URLs, words) and
            # saturates at 1.0 while real defects remain — it scored 1.0 on a report with a
            # malformed mermaid, fabricated URLs, and zero inline citations. Couple the
            # recorded score to the FINAL weaknesses so an open defect can never read as a
            # perfect score, and a doc with fewer/less-severe weaknesses scores higher.
            _score = adjusted_score(_rubric_base, _weaknesses)
            # Remaining weaknesses are surfaced BELOW the report in the result view via the
            # HillClimbEvent.weaknesses emitted below (the frontend renders them) — they must
            # NOT be concatenated into result_output, which IS the deliverable document
            # (saved, downloaded, recorded as result_text). Keeping them out keeps the report
            # clean and keeps the next-run seed uncontaminated.
            _version = _store.next_version(_thash)
            _store.record(
                TaskRun(
                    task_hash=_thash,
                    session_id=session.session_id,
                    version=_version,
                    score=_score,
                    weaknesses=_weaknesses,
                    artifact_path=_art_path,
                    requirement=_original_requirement,
                    result_text=result_output,
                    # §14.4: snapshot the effective hill-climb config so a later run of
                    # this task can recover its epoch budget across backend restarts.
                    config=_hc_cfg or {},
                )
            )
            # Template reuse: save a decent report's heading SKELETON so the next
            # semantically-similar research can seed its first document from it (best-effort;
            # dedups identical skeletons; needs an embedder to be searchable).
            if _score >= 0.6 and self._embedder is not None:
                try:
                    from studio.templates import TemplateStore, extract_skeleton
                    TemplateStore(embedder=self._embedder).save_template(
                        _original_requirement, extract_skeleton(result_output))
                except Exception:  # noqa: BLE001 — template save is non-critical
                    pass
            _prev_score = 0.0
            if _version > 1:
                _prev = _store.all_runs(_thash)
                if len(_prev) >= 2:
                    _prev_score = _prev[-2].score
            _delta = _score - _prev_score
            _hc_cfg2 = _hc_cfg
            _min_delta = float(_hc_cfg2.get("min_improvement", 0.02))
            _max_epochs = int(_hc_cfg2.get("max_epochs", 5))
            # PLAN item 8: status from the PER-RUN epoch index, not the cumulative version
            # (which fired "converged" mid-improvement on any task with history).
            _status = _epoch_status(self._epoch, _delta, _min_delta, _max_epochs)
            # §14.4: hand this pass's outcome back to run() so it can drive the loop.
            _outcome = EpochResult(
                version=_version, score=_score, delta=_delta, status=_status
            )
            self._emit(
                HillClimbEvent(
                    epoch=_version,
                    score=_score,
                    delta=_delta,
                    status=_status,
                    note=f"v{_version} score={_score:.2f}",
                    weaknesses=_weaknesses,
                    task_hash=_thash,
                )
            )
        except Exception:  # noqa: BLE001 — scoring failure must never crash the run
            pass
        return _outcome, result_output

    # -- helpers -----------------------------------------------------------

    def _build_client(self) -> LLMClient:
        """Build the run's LLMClient — injected factory in tests, else from spec."""
        if self._client_factory is not None:
            return self._client_factory(self._on_usage)
        backend = resolve_backend(self._session.llm_spec)
        # session info may be filled lazily; ensure label/model present
        return build_chat_client(backend, self._on_usage)

    def _maybe_tool_augment(
        self, client: LLMClient, *, artifact_path: Path | None = None
    ) -> LLMClient:
        """Wrap ``client`` in the tool loop (web_search + jailed file tools) when
        tools are enabled.

        Gated on ``session.tools_enabled`` AND web_toolkit being importable; in
        tests an injected ``search_fn`` (set via ``self._search_fn``) bypasses the
        import so no network is hit. The file tools are confined to a per-session
        :class:`~studio.workspace.Workspace` (realpath jail). When ``artifact_path``
        is provided, the artifact OCC tools (read_artifact / patch_artifact) are
        also offered. Returns the bare client when tools are off.
        """
        enabled = self._session.tools_enabled and (
            self._search_fn is not None or web_toolkit_available()
        )
        if not enabled:
            return client
        model_id = str(
            (self._session.llm_info or {}).get("model")
            or (self._session.llm_spec or {}).get("model")
            or ""
        )
        model_profile = resolve_model_profile(model_id)
        workspace = Workspace(self._session.session_id, root=self._workspace_root)
        return ToolAugmentedClient(
            client,
            on_tool_call=lambda sid, tool, args: self._emit(
                ToolCallEvent(step_id=sid, tool=tool, args=args)
            ),
            on_tool_result=lambda sid, tool, summary, n, notice, rejected: self._emit(
                ToolResultEvent(
                    step_id=sid,
                    tool=tool,
                    summary=summary,
                    n_results=n,
                    notice=notice,
                    rejected=rejected,
                )
            ),
            step_id_getter=lambda: self._current_step_id,
            search_fn=self._search_fn,
            fetch_fn=self._fetch_fn,
            workspace=workspace,
            artifact_path=artifact_path,
            max_iters=model_profile.max_tool_iters,
            max_searches=model_profile.max_searches,
            max_successful_fetches=model_profile.max_successful_fetches,
        )

    def _gate_event_for(self, step_id: str, output: str) -> GateEvent:
        """Run the phase output through the security gate as a text proposal."""
        proposal = {"type": "phase_output", "content": output, "description": output[:200]}
        return run_gate_event(f"phase:{step_id}", proposal, cwd=self._sandbox_cwd)

    def _done_event(self, final_output: str, *, cancelled: bool) -> DoneEvent:
        elapsed = time.perf_counter() - self._t0 if self._t0 is not None else 0.0
        return DoneEvent(
            total_tokens=self._acc.total_tokens,
            input=self._acc.total_input_tokens,
            output=self._acc.total_output_tokens,
            estimated=self._acc.tokens_estimated,
            wall_s=elapsed,
            result=final_output,
            cancelled=cancelled,
            result_path=self._write_result(final_output),
        )

    def _read_workspace_artifact(self) -> str:
        """Return content of artifact.md from the workspace if it exists."""
        try:
            ws = Workspace(self._session.session_id, root=self._workspace_root)
            artifact = ws.root / "artifact.md"
            if artifact.exists():
                return artifact.read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _write_result(self, final_output: str) -> str:
        """Save the final result to the session workspace → its absolute path.

        Best-effort: a write failure returns "" (the result still rides in the
        ``done`` event), and an empty result is not written.
        """
        if not final_output.strip():
            return ""
        try:
            ws = Workspace(self._session.session_id, root=self._workspace_root)
            ws.write("result.md", final_output)
            return str(ws.root / "result.md")
        except Exception:  # noqa: BLE001 - saving is auxiliary; never break `done`
            return ""
