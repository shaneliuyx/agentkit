"""Tests for agentkit.planner — self-planning: task → subtask DAG → graph config.

All offline (no network, no LLM): the deterministic decomposer is default;
LLM decomposer is tested via a pure fake callable. DAG validation (cycles,
unresolved deps) is tested with hand-crafted bad inputs.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from agentkit.planner import (
    Plan,
    PlanStep,
    emit_graph_config,
    plan,
    plan_to_graph_config,
)
from agentkit.types import ChatResult, Message


# ---------------------------------------------------------------------------
# -- helpers / fakes --------------------------------------------------------
# ---------------------------------------------------------------------------

class _FakeDecomposer:
    """Fake LLM decomposer: returns a fixed JSON list of step dicts."""

    def __init__(self, steps: list[dict[str, Any]]) -> None:
        self._steps = steps
        self.calls = 0

    def __call__(self, task: str) -> list[dict[str, Any]]:
        self.calls += 1
        return self._steps


# ---------------------------------------------------------------------------
# -- Plan / PlanStep dataclasses --------------------------------------------
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_plan_step_is_frozen():
    step = PlanStep(id="s1", description="do it", depends_on=())
    with pytest.raises((AttributeError, TypeError)):
        step.id = "other"  # type: ignore[misc]


@pytest.mark.unit
def test_plan_is_frozen():
    s = PlanStep(id="s1", description="x", depends_on=())
    p = Plan(task="t", steps=(s,))
    with pytest.raises((AttributeError, TypeError)):
        p.task = "y"  # type: ignore[misc]


@pytest.mark.unit
def test_plan_step_optional_fields_default_to_none():
    step = PlanStep(id="s1", description="step", depends_on=())
    assert step.role is None
    assert step.difficulty is None


@pytest.mark.unit
def test_plan_step_with_role_and_difficulty():
    step = PlanStep(id="s1", description="analyze", depends_on=("s0",),
                    role="researcher", difficulty="medium")
    assert step.role == "researcher"
    assert step.difficulty == "medium"
    assert step.depends_on == ("s0",)


# ---------------------------------------------------------------------------
# -- plan() — deterministic decomposer (default, no network) ----------------
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_plan_deterministic_single_sentence_gives_one_step():
    p = plan("rename a variable")
    assert isinstance(p, Plan)
    assert len(p.steps) >= 1
    assert all(isinstance(s, PlanStep) for s in p.steps)


@pytest.mark.unit
def test_plan_deterministic_and_conjunction_splits():
    """Tasks with ' and ' should decompose into at least 2 steps."""
    p = plan("research the topic and write a summary")
    assert len(p.steps) >= 2


@pytest.mark.unit
def test_plan_deterministic_numbered_phrases_split():
    """Numbered phrases like '1. X 2. Y 3. Z' decompose into 3 steps."""
    p = plan("1. collect data 2. analyze results 3. write report")
    assert len(p.steps) == 3


@pytest.mark.unit
def test_plan_deterministic_steps_form_valid_dag():
    """Default decomposer must emit a valid DAG (no cycles, all deps resolve)."""
    p = plan("research the topic and write a draft and review the draft")
    _assert_valid_dag(p)


@pytest.mark.unit
def test_plan_always_returns_plan_object():
    """Even a vacuous single-word task must return a valid Plan."""
    p = plan("hello")
    assert isinstance(p, Plan)
    assert len(p.steps) >= 1
    assert p.task == "hello"


# ---------------------------------------------------------------------------
# -- plan() — injected LLM decomposer --------------------------------------
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_plan_with_llm_decomposer_uses_it():
    fake = _FakeDecomposer([
        {"id": "s1", "description": "collect data", "depends_on": []},
        {"id": "s2", "description": "analyze",       "depends_on": ["s1"]},
        {"id": "s3", "description": "report",        "depends_on": ["s2"]},
    ])
    p = plan("do research", decomposer=fake)
    assert fake.calls == 1
    assert len(p.steps) == 3
    assert p.steps[0].id == "s1"
    assert p.steps[1].depends_on == ("s1",)
    assert p.steps[2].depends_on == ("s2",)


@pytest.mark.unit
def test_plan_llm_decomposer_passes_task_string():
    received: list[str] = []

    def capturing_decomposer(task: str) -> list[dict[str, Any]]:
        received.append(task)
        return [{"id": "s1", "description": task, "depends_on": []}]

    task = "analyze the codebase and propose refactors"
    plan(task, decomposer=capturing_decomposer)
    assert received == [task]


@pytest.mark.unit
def test_plan_llm_decomposer_with_role_and_difficulty():
    fake = _FakeDecomposer([
        {"id": "s1", "description": "research", "depends_on": [],
         "role": "researcher", "difficulty": "low"},
        {"id": "s2", "description": "write",    "depends_on": ["s1"],
         "role": "writer",     "difficulty": "high"},
    ])
    p = plan("research and write", decomposer=fake)
    assert p.steps[0].role == "researcher"
    assert p.steps[1].difficulty == "high"


# ---------------------------------------------------------------------------
# -- DAG validation — cycles and unresolved deps must be rejected -----------
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_plan_rejects_cycle():
    """A → B → A cycle must raise ValueError."""
    fake = _FakeDecomposer([
        {"id": "a", "description": "step a", "depends_on": ["b"]},
        {"id": "b", "description": "step b", "depends_on": ["a"]},
    ])
    with pytest.raises(ValueError, match="[Cc]ycle|[Cc]ircular"):
        plan("task", decomposer=fake)


@pytest.mark.unit
def test_plan_rejects_unresolved_dep():
    """depends_on referencing a non-existent id must raise ValueError."""
    fake = _FakeDecomposer([
        {"id": "s1", "description": "step", "depends_on": ["ghost"]},
    ])
    with pytest.raises(ValueError, match="[Uu]nresolved|[Uu]nknown|ghost"):
        plan("task", decomposer=fake)


@pytest.mark.unit
def test_plan_rejects_self_dep():
    """A step depending on itself is a trivial cycle."""
    fake = _FakeDecomposer([
        {"id": "s1", "description": "step", "depends_on": ["s1"]},
    ])
    with pytest.raises(ValueError, match="[Cc]ycle|[Ss]elf"):
        plan("task", decomposer=fake)


@pytest.mark.unit
def test_plan_rejects_three_node_cycle():
    """A → B → C → A (longer cycle) must also be caught."""
    fake = _FakeDecomposer([
        {"id": "a", "description": "a", "depends_on": ["c"]},
        {"id": "b", "description": "b", "depends_on": ["a"]},
        {"id": "c", "description": "c", "depends_on": ["b"]},
    ])
    with pytest.raises(ValueError, match="[Cc]ycle|[Cc]ircular"):
        plan("task", decomposer=fake)


# ---------------------------------------------------------------------------
# -- plan_to_graph_config ---------------------------------------------------
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_plan_to_graph_config_shape():
    """Output must match GraphStore.create_graph dag shape: {nodes, edges}."""
    p = Plan(task="do X", steps=(
        PlanStep(id="s1", description="step 1", depends_on=()),
        PlanStep(id="s2", description="step 2", depends_on=("s1",)),
    ))
    cfg = plan_to_graph_config(p)
    assert "nodes" in cfg and "edges" in cfg
    assert "s1" in cfg["nodes"] and "s2" in cfg["nodes"]


@pytest.mark.unit
def test_plan_to_graph_config_node_has_type_and_payload():
    """Each node must have 'type' (str) and 'payload' (dict) — Node shape."""
    p = Plan(task="do X", steps=(
        PlanStep(id="s1", description="step 1", depends_on=()),
    ))
    cfg = plan_to_graph_config(p)
    node = cfg["nodes"]["s1"]
    assert "type" in node
    assert isinstance(node["type"], str)
    assert "payload" in node
    assert isinstance(node["payload"], dict)


@pytest.mark.unit
def test_plan_to_graph_config_payload_has_description():
    """Payload must carry the step description so a worker knows what to do."""
    p = Plan(task="task", steps=(
        PlanStep(id="s1", description="analyze the logs", depends_on=()),
    ))
    cfg = plan_to_graph_config(p)
    payload = cfg["nodes"]["s1"]["payload"]
    assert "description" in payload
    assert payload["description"] == "analyze the logs"


@pytest.mark.unit
def test_plan_to_graph_config_edges_match_deps():
    """Edges must be [[dep_id, step_id], ...] for each depends_on entry."""
    p = Plan(task="pipeline", steps=(
        PlanStep(id="s1", description="collect", depends_on=()),
        PlanStep(id="s2", description="process", depends_on=("s1",)),
        PlanStep(id="s3", description="report",  depends_on=("s2",)),
    ))
    cfg = plan_to_graph_config(p)
    assert ["s1", "s2"] in cfg["edges"]
    assert ["s2", "s3"] in cfg["edges"]
    assert len(cfg["edges"]) == 2


@pytest.mark.unit
def test_plan_to_graph_config_parallel_no_edges():
    """Steps with no deps produce zero edges (parallel-ready)."""
    p = Plan(task="parallel", steps=(
        PlanStep(id="a", description="work A", depends_on=()),
        PlanStep(id="b", description="work B", depends_on=()),
    ))
    cfg = plan_to_graph_config(p)
    assert cfg["edges"] == []


@pytest.mark.unit
def test_plan_to_graph_config_role_in_payload_when_set():
    """role and difficulty, when set, are included in payload."""
    p = Plan(task="t", steps=(
        PlanStep(id="s1", description="analyze", depends_on=(),
                 role="researcher", difficulty="medium"),
    ))
    cfg = plan_to_graph_config(p)
    payload = cfg["nodes"]["s1"]["payload"]
    assert payload.get("role") == "researcher"
    assert payload.get("difficulty") == "medium"


# ---------------------------------------------------------------------------
# -- emit_graph_config — file round-trip ------------------------------------
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_emit_graph_config_writes_json_file():
    p = Plan(task="do X", steps=(
        PlanStep(id="s1", description="step 1", depends_on=()),
        PlanStep(id="s2", description="step 2", depends_on=("s1",)),
    ))
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "plan.json"
        emit_graph_config(p, path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert "nodes" in data and "edges" in data


@pytest.mark.unit
def test_emit_graph_config_round_trips():
    """Written JSON must round-trip back to the same dag structure."""
    p = Plan(task="pipeline", steps=(
        PlanStep(id="s1", description="collect", depends_on=()),
        PlanStep(id="s2", description="process", depends_on=("s1",)),
    ))
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "plan.json"
        emit_graph_config(p, path)
        data = json.loads(path.read_text())
        cfg = plan_to_graph_config(p)
        assert data["nodes"] == cfg["nodes"]
        assert data["edges"] == cfg["edges"]


@pytest.mark.unit
def test_emit_graph_config_accepts_str_path():
    """path param may be a str or a Path — both must work."""
    p = Plan(task="t", steps=(
        PlanStep(id="s1", description="step", depends_on=()),
    ))
    with tempfile.TemporaryDirectory() as d:
        str_path = str(Path(d) / "plan.json")
        emit_graph_config(p, str_path)
        assert Path(str_path).exists()


@pytest.mark.unit
def test_emit_graph_config_includes_task_metadata():
    """Top-level JSON may include a 'task' key for human readability."""
    p = Plan(task="analyze the logs", steps=(
        PlanStep(id="s1", description="step", depends_on=()),
    ))
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "plan.json"
        emit_graph_config(p, path)
        data = json.loads(path.read_text())
        # task metadata is optional but encouraged
        assert data.get("task") == "analyze the logs" or "nodes" in data


# ---------------------------------------------------------------------------
# -- integration: plan() → plan_to_graph_config() → GraphStore loadable ----
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_plan_to_config_loadable_by_graph_store():
    """The emitted config must be consumable by GraphStore.create_graph."""
    from agentkit.runtime.graph_store import GraphStore
    import tempfile as tf

    p = Plan(task="pipeline", steps=(
        PlanStep(id="s1", description="collect", depends_on=()),
        PlanStep(id="s2", description="process", depends_on=("s1",)),
        PlanStep(id="s3", description="report",  depends_on=("s2",)),
    ))
    dag = plan_to_graph_config(p)
    with tf.TemporaryDirectory() as d:
        store = GraphStore(str(Path(d) / "test.db"))
        gid = store.create_graph("test_plan", dag)
        assert gid.startswith("g_")
        rid = store.start_run(gid, "test")
        states = store.node_states(rid)
        # s1 has no deps → ready; s2, s3 are pending
        assert states["s1"] == "ready"
        assert states["s2"] == "pending"
        assert states["s3"] == "pending"


# ---------------------------------------------------------------------------
# -- __main__ self-check (mirrors topology/core pattern) -------------------
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_main_self_check_passes():
    """The module's __main__ block must not raise."""
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "agentkit.planner.core"],
        capture_output=True, text=True,
        cwd="/Users/yuxinliu/code/agentkit",
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# -- helpers ----------------------------------------------------------------
# ---------------------------------------------------------------------------

def _assert_valid_dag(p: Plan) -> None:
    """Assert no cycles and all depends_on ids resolve."""
    ids = {s.id for s in p.steps}
    for s in p.steps:
        for dep in s.depends_on:
            assert dep in ids, f"unresolved dep {dep!r} in step {s.id!r}"
    # topological sort check (Kahn's)
    from collections import defaultdict, deque
    in_deg: dict[str, int] = {s.id: 0 for s in p.steps}
    adj: dict[str, list[str]] = defaultdict(list)
    for s in p.steps:
        for dep in s.depends_on:
            adj[dep].append(s.id)
            in_deg[s.id] += 1
    q: deque[str] = deque(sid for sid, d in in_deg.items() if d == 0)
    visited = 0
    while q:
        n = q.popleft()
        visited += 1
        for nbr in adj[n]:
            in_deg[nbr] -= 1
            if in_deg[nbr] == 0:
                q.append(nbr)
    assert visited == len(p.steps), "cycle detected in plan DAG"
