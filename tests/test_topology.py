"""Tests for agentkit.topology — rule selection, DAG gen, config, pipeline.

All offline (no network, no LLM): the rule tree is pure; the pipeline runs with
a deterministic fake client over a temp SQLite GraphStore.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentkit.topology import (
    DURABLE_BOARD,
    GATEWAY,
    MESH,
    PIPELINE,
    SINGLE,
    STAR,
    TREE,
    TaskSpec,
    build_config,
    emit_topologies_py,
    from_json,
    generate_dag,
    run_task,
    select_topology,
    to_json,
)
from agentkit.types import ChatResult, Message


# -- rule tree (§2.7 decision order) ---------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("spec,expected", [
    (TaskSpec("rename var"), SINGLE),                                          # Q1
    (TaskSpec("x", subtasks=("a", "b"), single_agent_sufficient=True), SINGLE),
    (TaskSpec("pr", subtasks=("sec", "test", "perf"), subtasks_independent=True), STAR),  # Q3
    (TaskSpec("fix", subtasks=("locate", "fix", "test")), PIPELINE),          # Q3 ordered
    (TaskSpec("dbg", subtasks=("a", "b"), subtasks_independent=True, workers_challenge=True), MESH),  # Q5
    (TaskSpec("a", subtasks=("x",), subtasks_independent=True, multiple_entry_points=True), GATEWAY),  # Q7
    (TaskSpec("m", subtasks=("x", "y"), subtasks_independent=True, cross_session=True), DURABLE_BOARD),  # Q4/Q8
    (TaskSpec("b", subtasks=("a", "b"), subtasks_independent=True, needs_subdecomposition=True), TREE),
])
def test_select_topology_matches_doc_rules(spec, expected):
    assert select_topology(spec).topology == expected


@pytest.mark.unit
def test_q7_routing_beats_everything():
    # Even with independence + challenge, multiple entry points wins (upstream).
    spec = TaskSpec("x", subtasks=("a", "b"), subtasks_independent=True,
                    workers_challenge=True, multiple_entry_points=True)
    assert select_topology(spec).topology == GATEWAY


# -- DAG generation --------------------------------------------------------

@pytest.mark.unit
def test_generate_dag_star_shape():
    spec = TaskSpec("pr", subtasks=("sec", "test", "perf"), subtasks_independent=True)
    dag, conc = generate_dag(select_topology(spec), spec, llm=False)
    assert conc == 3
    assert set(dag["nodes"]) == {"dispatch", "worker1", "worker2", "worker3", "reduce"}
    assert sum(1 for e in dag["edges"] if e[1] == "reduce") == 3


@pytest.mark.unit
def test_generate_dag_pipeline_is_linear():
    spec = TaskSpec("fix", subtasks=("locate", "fix", "test"))
    dag, conc = generate_dag(select_topology(spec), spec, llm=False)
    assert conc == 1
    assert dag["edges"] == [["stage1", "stage2"], ["stage2", "stage3"]]


# -- config round-trip + emitter -------------------------------------------

@pytest.mark.unit
def test_config_json_round_trip_is_type_faithful():
    spec = TaskSpec("pr", subtasks=("sec", "test"), subtasks_independent=True)
    cfg = build_config(spec, llm=False)
    rt = from_json(to_json(cfg))
    assert rt.dag == cfg.dag
    assert rt.spec.subtasks == ("sec", "test")          # tuple, not list
    assert isinstance(rt.spec.subtasks, tuple)
    assert rt.choice.questions_fired == cfg.choice.questions_fired


@pytest.mark.unit
def test_emitted_topologies_py_executes_and_matches():
    import types
    spec = TaskSpec("pr", subtasks=("sec", "test", "perf"), subtasks_independent=True)
    cfg = build_config(spec, llm=False)
    src = emit_topologies_py(cfg)
    mod = types.ModuleType("gen")
    exec(compile(src, "<gen>", "exec"), mod.__dict__)  # noqa: S102 — codegen test
    dag, conc = mod.build()
    assert dag == cfg.dag and conc == cfg.concurrency
    assert mod.TOPOLOGY == "star"


# -- pipeline (durable run, fake client) -----------------------------------

class _FakeLLM:
    def __init__(self) -> None:
        self.n = 0

    def chat(self, messages: list[Message], tools: Any = None) -> ChatResult:
        self.n += 1
        return ChatResult(text=f"answer-{self.n}", tool_calls=[], total_tokens=5)


@pytest.mark.unit
def test_pipeline_runs_star_to_completion():
    spec = TaskSpec("review PR", subtasks=("security", "tests", "perf"),
                    subtasks_independent=True)
    r = run_task(spec, _FakeLLM())
    assert r.topology == STAR and r.concurrency == 3
    assert r.run_status == "done"
    assert set(r.results) == {"dispatch", "worker1", "worker2", "worker3", "reduce"}


@pytest.mark.unit
def test_pipeline_single_task():
    r = run_task(TaskSpec("rename a variable"), _FakeLLM())
    assert r.topology == SINGLE and r.run_status == "done"
    assert list(r.results) == ["agent"]


@pytest.mark.unit
def test_infer_spec_resolves_single_vs_subtasks_contradiction():
    # Model returns the common contradiction: single_agent_sufficient=true WHILE
    # listing several independent subtasks. infer must coerce → not single.
    from agentkit.topology.infer import infer_spec

    class _ContradictoryLLM:
        def chat(self, messages: list[Message], tools: Any = None) -> ChatResult:
            return ChatResult(text=(
                '{"subtasks": ["a", "b", "c"], "single_agent_sufficient": true, '
                '"subtasks_independent": true, "needs_subdecomposition": false, '
                '"workers_challenge": false, "cross_session": false, '
                '"needs_human_in_loop": false, "needs_recovery": false, '
                '"multiple_entry_points": false}'), tool_calls=[], total_tokens=1)

    spec = infer_spec("review a PR", _ContradictoryLLM())
    assert spec.single_agent_sufficient is False  # coerced: 3 subtasks ⇒ not single
    assert select_topology(spec).topology == STAR


@pytest.mark.unit
def test_pipeline_pipeline_order():
    spec = TaskSpec("fix bug", subtasks=("locate", "write fix", "add test"))
    r = run_task(spec, _FakeLLM())
    assert r.topology == PIPELINE and r.run_status == "done"
    assert set(r.results) == {"stage1", "stage2", "stage3"}
