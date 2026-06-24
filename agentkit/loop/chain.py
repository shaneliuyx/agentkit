"""agentkit.loop.chain — DAG composition of loops.

Agentkit has no primitive for chaining loops: Studio runs one plan, the
orchestrator runs one search space. There is no way to say "run research
loop, feed findings into verification loop, then trigger deploy loop."
This module provides that primitive.

Design:
  - LoopSpec is an immutable description: a named runner callable + optional
    goal + dependency list.
  - LoopChain assembles specs into a DAG validated for cycles, then runs
    each spec in topological order, passing the predecessor's output_ctx into
    each downstream spec's merged input.
  - For durable cross-process execution, build a GraphStore graph where each
    node's payload is a LoopSpec.

External analogues:
  - Prefect Automations: loop A completion fires an event → triggers loop B.
  - LangGraph: add_node("inner", inner_graph.compile()) embeds one graph as
    a node inside an outer graph.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from agentkit.loop.goal import LoopGoal, StopVerdict, check_goal


@dataclass(frozen=True)
class LoopSpec:
    """An immutable description of one loop in a chain.

    Attributes:
        name:       Unique identifier within a LoopChain.
        runner:     Callable(input_ctx: dict) -> output_ctx: dict. Receives
                    merged input (initial_ctx + upstream outputs) and must
                    return an output dict.
        goal:       Optional LoopGoal. When provided, check_goal() runs before
                    the runner; if already met, the runner is skipped.
        depends_on: Names of specs that must complete before this one.
    """

    name: str
    runner: Callable[[dict[str, Any]], dict[str, Any]]
    goal: LoopGoal | None = None
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpecResult:
    """Immutable outcome of running one LoopSpec."""

    name: str
    output: dict[str, Any]
    skipped: bool
    verdict: StopVerdict | None


@dataclass(frozen=True)
class ChainResult:
    """Immutable outcome of a full LoopChain.run() call."""

    outputs: dict[str, dict[str, Any]]
    results: list[SpecResult]
    status: str  # "done" | "skipped"


class LoopChain:
    """A DAG of LoopSpecs that runs in dependency order.

    Usage::

        chain = (
            LoopChain()
            .add(LoopSpec("research", run_research))
            .add(LoopSpec("verify", run_verify, depends_on=("research",)))
            .add(LoopSpec(
                "deploy", run_deploy,
                goal=LoopGoal("Deploy succeeds", "curl -s /health", "ok"),
                depends_on=("verify",),
            ))
        )
        result = chain.run({"task": "ship billing v2"})
    """

    def __init__(self) -> None:
        self._specs: dict[str, LoopSpec] = {}
        self._order: list[str] = []

    def add(self, spec: LoopSpec) -> "LoopChain":
        """Register a LoopSpec. Returns self for chaining."""
        if spec.name in self._specs:
            raise ValueError(f"duplicate spec name: {spec.name!r}")
        for dep in spec.depends_on:
            if dep not in self._specs:
                raise ValueError(
                    f"spec {spec.name!r} depends on unknown spec {dep!r}; "
                    "add dependencies before their dependents"
                )
        self._specs[spec.name] = spec
        self._order = self._topo_sort()
        return self

    def run(
        self,
        initial_ctx: dict[str, Any] | None = None,
        cwd: str = ".",
    ) -> ChainResult:
        """Execute all specs in topological order, passing outputs downstream.

        Each spec's runner receives a merged input_ctx: the initial_ctx plus
        the output_ctx of every immediate predecessor (keyed as
        ``_{dep_name}_output``). A spec whose goal is already satisfied before
        its runner fires is skipped with an empty output dict.
        """
        ctx = dict(initial_ctx or {})
        outputs: dict[str, dict[str, Any]] = {}
        spec_results: list[SpecResult] = []
        any_skipped = False

        for name in self._order:
            spec = self._specs[name]

            merged: dict[str, Any] = {**ctx}
            for dep in spec.depends_on:
                if dep in outputs:
                    merged[f"_{dep}_output"] = outputs[dep]

            # Pre-check: skip if goal already met by a prior spec's side effects
            pre_verdict: StopVerdict | None = None
            if spec.goal is not None:
                pre_verdict = check_goal(spec.goal, cwd=cwd)
                if pre_verdict.met:
                    outputs[name] = {}
                    spec_results.append(
                        SpecResult(name=name, output={}, skipped=True, verdict=pre_verdict)
                    )
                    any_skipped = True
                    continue

            output = spec.runner(merged)
            outputs[name] = output

            post_verdict: StopVerdict | None = None
            if spec.goal is not None:
                post_verdict = check_goal(spec.goal, cwd=cwd)

            spec_results.append(
                SpecResult(
                    name=name,
                    output=output,
                    skipped=False,
                    verdict=post_verdict or pre_verdict,
                )
            )

        return ChainResult(
            outputs=outputs,
            results=spec_results,
            status="skipped" if any_skipped else "done",
        )

    def _topo_sort(self) -> list[str]:
        in_degree: dict[str, int] = {n: 0 for n in self._specs}
        children: dict[str, list[str]] = {n: [] for n in self._specs}
        for spec in self._specs.values():
            for dep in spec.depends_on:
                in_degree[spec.name] += 1
                children[dep].append(spec.name)

        queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
        result: list[str] = []
        while queue:
            node = queue.popleft()
            result.append(node)
            for child in children[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(result) != len(self._specs):
            cycle = set(self._specs) - set(result)
            raise ValueError(f"cycle detected in LoopChain involving: {cycle}")
        return result


if __name__ == "__main__":
    calls: list[str] = []

    def _runner_a(ctx: dict) -> dict:
        calls.append("a")
        return {"a_result": "hello"}

    def _runner_b(ctx: dict) -> dict:
        calls.append("b")
        assert ctx.get("_a_output") == {"a_result": "hello"}, ctx
        return {"b_result": "world"}

    chain = (
        LoopChain()
        .add(LoopSpec("a", _runner_a))
        .add(LoopSpec("b", _runner_b, depends_on=("a",)))
    )
    result = chain.run({"seed": "x"})
    assert calls == ["a", "b"], calls
    assert result.outputs["b"] == {"b_result": "world"}
    assert result.status == "done"

    try:
        chain.add(LoopSpec("a", _runner_a))
        assert False, "should have raised"
    except ValueError:
        pass

    try:
        LoopChain().add(LoopSpec("x", _runner_a, depends_on=("missing",)))
        assert False, "should have raised"
    except ValueError:
        pass

    print("loop.chain self-check OK")
