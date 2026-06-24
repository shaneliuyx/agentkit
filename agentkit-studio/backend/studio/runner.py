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
    GraphEvent,
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
            sub_step = replace(
                step, description=_with_upstream(step.description, upstream), depends_on=()
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

        # budget gauge
        if budget is not None:
            self._emit(
                BudgetEvent(
                    spent=budget.spent_total,
                    ceiling=session.budget_ceiling,
                    exceeded=False,
                )
            )

        # verification (pure tier, always runs)
        verify_event = build_verify_event(final_output)
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
                result=final_output,
                cancelled=cancelled,
            )
        )

        # done
        self._emit(self._done_event(final_output, cancelled=cancelled))

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
