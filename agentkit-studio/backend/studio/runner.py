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

import time
from dataclasses import replace
from typing import Any, Callable

from agentkit.orchestrator.fanout import BudgetExceeded, FanoutBudget
from agentkit.planner.core import Plan, plan
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
from studio.session import RunSnapshot, Session
from studio.shared_bridge import TokenAccounting, UsageReport
from studio.tools import ToolAugmentedClient, web_toolkit_available
from studio.workspace import Workspace

#: Emit sink: the runner calls this for every event; app.py wires it to a queue.
Emit = Callable[[StudioEvent], None]


def _render_graph(plan_obj: Plan) -> GraphEvent:
    """Derive the render graph (SPEC §6): a phase node per step, expanded into
    intra-phase agent nodes per topology, plus inter-phase ``depends_on`` edges.

    Node kinds: ``phase`` (the step) + ``agent``/``hub``/``reduce``/``stage`` for
    the topology expansion. The runtime ``n_agents`` (from ``phase_done``)
    reconciles spoke counts later on the frontend.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    peers = 3  # default fan-out breadth (mirrors dynamic._DEFAULT_PEERS)

    for step in plan_obj.steps:
        phase_id = step.id
        nodes.append(
            {
                "id": phase_id,
                "kind": "phase",
                "phase": phase_id,
                "label": step.description[:80],
                "state": "pending",
            }
        )
        topo = step.topology or SINGLE
        _expand_topology(nodes, edges, phase_id, topo, peers)
        for dep in step.depends_on:
            edges.append({"from": dep, "to": phase_id, "kind": "depends"})

    return GraphEvent(nodes=nodes, edges=edges)


def _expand_topology(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    phase_id: str,
    topo: str,
    peers: int,
) -> None:
    """Append intra-phase agent nodes/edges for one phase's topology."""

    def agent(idx: int, kind: str = "agent") -> str:
        nid = f"{phase_id}:{kind}{idx}"
        nodes.append(
            {"id": nid, "kind": kind, "phase": phase_id, "label": kind, "state": "pending"}
        )
        return nid

    if topo == SINGLE:
        a = agent(0)
        edges.append({"from": phase_id, "to": a, "kind": "intra"})
    elif topo == STAR:
        spokes = [agent(i) for i in range(peers)]
        reduce_id = agent(0, "reduce")
        for s in spokes:
            edges.append({"from": phase_id, "to": s, "kind": "intra"})
            edges.append({"from": s, "to": reduce_id, "kind": "reduce"})
    elif topo == MESH:
        ps = [agent(i) for i in range(peers)]
        for i, a in enumerate(ps):
            for b in ps[i + 1 :]:
                edges.append({"from": a, "to": b, "kind": "mesh"})
        reduce_id = agent(0, "reduce")
        for a in ps:
            edges.append({"from": a, "to": reduce_id, "kind": "reduce"})
    elif topo == MAP:
        # MAP fan-out: N workers (one per upstream item), then reduce.
        # peers is a best-effort count — actual count depends on upstream list.
        workers = [agent(i) for i in range(peers)]
        reduce_id = agent(0, "reduce")
        for w in workers:
            edges.append({"from": phase_id, "to": w, "kind": "intra"})
            edges.append({"from": w, "to": reduce_id, "kind": "reduce"})
    elif topo == PIPELINE:
        stages = [agent(i, "stage") for i in range(3)]  # mirrors _PIPELINE_STAGES
        edges.append({"from": phase_id, "to": stages[0], "kind": "intra"})
        for a, b in zip(stages, stages[1:]):
            edges.append({"from": a, "to": b, "kind": "pipeline"})


def _with_upstream(description: str, upstream: str) -> str:
    """Fold upstream outputs into a step description — byte-identical to
    ``agentkit.topology.dynamic._with_upstream`` so the Studio-driven single-step
    sub-plan produces the same prompts a full ``run_plan`` would."""
    if upstream:
        return f"{description}\n\nContext from prior steps:\n{upstream}"
    return description


#: A document is "complete" if its last non-space char closes a sentence/structure.
#: Used to reject a truncated artifact in favor of a complete synthesis (see the
#: result_output selection below). Markdown reports legitimately end on a period,
#: list/table row, fence, blockquote, or heading underline — so the set is permissive;
#: a bare cutoff mid-word/URL (the truncation symptom) fails it.
_CLEAN_END_CHARS = frozenset(".!?)]\"'`|>*-_")


def _ends_cleanly(text: str) -> bool:
    """True if ``text`` ends at a sentence/structure boundary (not truncated mid-line)."""
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in _CLEAN_END_CHARS


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
        self._acc = TokenAccounting()
        self._current_step_id = ""
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
        """Execute the full pipeline for ``requirement``, emitting every event."""
        self._t0 = time.perf_counter()
        try:
            self._run_inner(requirement)
        except Exception as exc:  # noqa: BLE001 - any failure becomes an error frame
            self._emit(ErrorEvent(message=str(exc), where="runner"))
            # Still emit a terminal done so the frontend leaves the running state.
            self._emit(self._done_event("", cancelled=False))

    def _run_inner(self, requirement: str) -> None:
        session = self._session

        # Inject goal end_state and constraints into requirement so the agent
        # sees them during planning — not just during post-phase verification.
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

        # Stash the original requirement so task_hash is stable across iterations
        # (the seeder may rewrite requirement with "ITERATION N —..." prefix).
        _original_requirement = requirement

        # Hill climb: if auto_improve is on and a prior run exists for this task,
        # copy its artifact into the current workspace and prefix the requirement
        # with the prior score + weaknesses so the agent edits rather than regenerates.
        _hc_cfg = getattr(session, "hill_climb_config", None) or {}
        if _hc_cfg.get("auto_improve"):
            import shutil
            from studio.task_runs import TaskRunStore, task_hash as _task_hash
            _thash = _task_hash(requirement)
            _store = TaskRunStore()
            # Prefer best-scoring run with content; fallback to latest.
            _prior = _store.best(_thash)
            if _prior and not (_prior.result_text or "").strip():
                _prior = _store.latest(_thash)
            if _prior:
                from studio.workspace import workspace_root as _ws_root_fn3
                _eff_ws2 = self._workspace_root or _ws_root_fn3()
                _prior_art = _eff_ws2 / _prior.session_id / "artifact.md"
                _artifact_copied = False
                if _prior_art.exists():
                    _curr_ws = Workspace(session.session_id, root=_eff_ws2)
                    shutil.copy(_prior_art, _curr_ws.root / "artifact.md")
                    _artifact_copied = True
                # Accumulate weaknesses from ALL prior runs (not just the best) so
                # every failure lesson carries forward. Deduplicate by exact string.
                _all_prior = _store.all_runs(_thash)
                _seen: set[str] = set()
                _all_weaknesses: list[str] = []
                for _run in _all_prior:
                    for _w in _run.weaknesses:
                        if _w not in _seen:
                            _seen.add(_w)
                            _all_weaknesses.append(_w)
                _fix_rules = "\n".join(
                    f"- OUTPUT MUST NOT exhibit: {w}" for w in _all_weaknesses
                )
                # Workers only have web_search/web_fetch — no file-read tool.
                # Avoid instructing them to read artifact.md (they can't).
                # Inject weaknesses as hard constraints on the ORIGINAL task instead.
                if _fix_rules:
                    requirement = (
                        f"{requirement}\n\n"
                        f"QUALITY CONSTRAINTS (accumulated from all prior attempts):\n{_fix_rules}"
                    )

        # session frame
        self._emit(
            SessionEvent(llm=session.llm_info, embed=session.embed_info, mode=session.mode)
        )

        # build the usage-capturing client (injected factory in tests)
        base_client = self._build_client()
        # Wrap in a web_search tool loop when tools are enabled (run_plan stays
        # unchanged — it sees a plain LLMClient that happens to run a tool loop).
        client = self._maybe_tool_augment(base_client)

        # plan → emit plan. A seeded session pre-seeds decomposition from a
        # chosen loop-library loop (emit loop_seed); else cold decomposition.
        seed_steps = session.seed_steps
        if seed_steps:
            plan_obj = plan(requirement, decomposer=make_seeded_decomposer(seed_steps))
            self._emit(LoopSeedEvent(loop_id=session.seed_loop_id, steps=seed_steps))
        else:
            plan_obj = plan(requirement)
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

        # assign topologies (auto; llm path only when mode=='llm' AND client given)
        use_llm = session.mode == "llm"
        plan_obj = assign_topologies(
            plan_obj, mode="auto", client=client, llm=use_llm
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

        outputs: dict[str, str] = {}
        cancelled = False
        final_output = ""
        #: Gate outcomes collected across phases — the Loop Doctor's safe_actions
        #: check reads these at run end (no re-running of any gate).
        gate_events: list[GateEvent] = []

        for step in plan_obj.steps:
            if session.cancel_requested:
                cancelled = True
                break

            self._current_step_id = step.id
            self._phase_captured = 0
            self._emit(PhaseStartEvent(step_id=step.id))

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

            try:
                result = run_plan(sub_plan, client, budget=budget, max_workers=4)
            except BudgetExceeded as exc:
                self._emit(
                    BudgetEvent(spent=exc.spent, ceiling=session.budget_ceiling, exceeded=True)
                )
                cancelled = True
                break

            sr = result.runs[0]
            outputs[step.id] = sr.output
            final_output = sr.output

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

        # Hill climb post-run: score output, mine weaknesses, record, emit HillClimbEvent.
        # Runs regardless of hill_climb_config so task_hash-based lookup always has data.
        try:
            from studio.task_runs import (
                TaskRun,
                TaskRunStore,
                mine_weaknesses_from_outputs,
                score_result,
                task_hash as _task_hash,
            )
            _store = TaskRunStore()
            _thash = _task_hash(_original_requirement)
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
            _score, _scorer_feedback = score_result(_scored_text, _original_requirement, client)
            # Mine from what the run ACTUALLY produced, not the persisted artifact.
            # The scorer uses artifact.md (to give credit for saved work), but the
            # miner must see result_output so it can flag synthesis failures (e.g.
            # "workers returned status only") rather than rubber-stamping the seed.
            _mine_text = result_output if result_output else _scored_text
            _weaknesses = mine_weaknesses_from_outputs(
                {k: v for k, v in outputs.items()},
                _mine_text,
                _original_requirement,
                client,
                scorer_feedback=_scorer_feedback,
            )
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
                )
            )
            _prev_score = 0.0
            if _version > 1:
                _prev = _store.all_runs(_thash)
                if len(_prev) >= 2:
                    _prev_score = _prev[-2].score
            _delta = _score - _prev_score
            _hc_cfg2 = getattr(session, "hill_climb_config", None) or {}
            _min_delta = float(_hc_cfg2.get("min_improvement", 0.02))
            _max_epochs = int(_hc_cfg2.get("max_epochs", 5))
            if _version >= _max_epochs:
                _status = "converged"
            elif _version > 1 and _delta < _min_delta:
                _status = "plateau"
            else:
                _status = "improving"
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

        # done
        self._emit(self._done_event(result_output, cancelled=cancelled))

    # -- helpers -----------------------------------------------------------

    def _build_client(self) -> LLMClient:
        """Build the run's LLMClient — injected factory in tests, else from spec."""
        if self._client_factory is not None:
            return self._client_factory(self._on_usage)
        backend = resolve_backend(self._session.llm_spec)
        # session info may be filled lazily; ensure label/model present
        return build_chat_client(backend, self._on_usage)

    def _maybe_tool_augment(self, client: LLMClient) -> LLMClient:
        """Wrap ``client`` in the tool loop (web_search + jailed file tools) when
        tools are enabled.

        Gated on ``session.tools_enabled`` AND web_toolkit being importable; in
        tests an injected ``search_fn`` (set via ``self._search_fn``) bypasses the
        import so no network is hit. The file tools are confined to a per-session
        :class:`~studio.workspace.Workspace` (realpath jail). Returns the bare
        client when tools are off.
        """
        enabled = self._session.tools_enabled and (
            self._search_fn is not None or web_toolkit_available()
        )
        if not enabled:
            return client
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
