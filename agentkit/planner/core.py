"""agentkit.planner.core — task → subtask DAG → runtime graph config.

Design rules (match agentkit conventions):
  - Frozen dataclasses only. No mutation.
  - LLM decomposer is injected and OPTIONAL. The default deterministic
    decomposer splits on ' and ', numbered phrases, or sentence boundaries
    so tests run with no network and no vendor SDK.
  - DAG validation (no cycles, all depends_on ids resolve) is always applied
    regardless of which decomposer is used.
  - plan_to_graph_config() emits the {"nodes", "edges"} shape that
    GraphStore.create_graph() consumes directly.
  - emit_graph_config() writes that shape to a JSON file (the
    "self-plan emits a config file, not code" deliverable — P1 + P3).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# -- Data model -------------------------------------------------------------
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlanStep:
    """One unit of work in a plan. Immutable snapshot.

    Attributes:
        id:          Unique identifier within the plan (e.g. "s1").
        description: Human-readable description of the work to do.
        depends_on:  Tuple of step ids that must complete before this one.
        role:        Optional agent role hint (e.g. "researcher", "writer").
        difficulty:  Optional difficulty hint (e.g. "low", "medium", "high").
    """

    id: str
    description: str
    depends_on: tuple[str, ...] = ()
    role: str | None = None
    difficulty: str | None = None


@dataclass(frozen=True)
class Plan:
    """An ordered, validated subtask DAG for a task.

    Attributes:
        task:  The original task string.
        steps: Topologically-ordered tuple of PlanStep objects.
    """

    task: str
    steps: tuple[PlanStep, ...]


# ---------------------------------------------------------------------------
# -- DAG validation ---------------------------------------------------------
# ---------------------------------------------------------------------------

def _validate_dag(steps: list[PlanStep]) -> None:
    """Raise ValueError if the step list has cycles or unresolved depends_on.

    Uses Kahn's algorithm (BFS topological sort). O(V+E).
    """
    ids = {s.id for s in steps}

    # Check all deps resolve and catch self-deps (trivial cycle)
    for s in steps:
        for dep in s.depends_on:
            if dep == s.id:
                raise ValueError(
                    f"Self-dependency in step {s.id!r}: a step cannot depend on itself."
                )
            if dep not in ids:
                raise ValueError(
                    f"Unresolved dependency {dep!r} in step {s.id!r}: "
                    f"no step with that id exists."
                )

    # Kahn's: detect cycles
    in_deg: dict[str, int] = {s.id: 0 for s in steps}
    adj: dict[str, list[str]] = defaultdict(list)
    for s in steps:
        for dep in s.depends_on:
            adj[dep].append(s.id)
            in_deg[s.id] += 1

    queue: deque[str] = deque(sid for sid, d in in_deg.items() if d == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for nbr in adj[node]:
            in_deg[nbr] -= 1
            if in_deg[nbr] == 0:
                queue.append(nbr)

    if visited != len(steps):
        raise ValueError(
            "Circular dependency detected in plan: the step graph contains a cycle."
        )


# ---------------------------------------------------------------------------
# -- Deterministic decomposer (default, no LLM) ----------------------------
# ---------------------------------------------------------------------------

_NUMBERED_RE = re.compile(
    r"\d+\.\s+(.+?)(?=\s+\d+\.|$)", re.DOTALL
)


def _deterministic_decompose(task: str) -> list[dict[str, Any]]:
    """Split a task string into step dicts without any LLM call.

    Strategy (tried in order):
      1. Numbered list: "1. X 2. Y 3. Z" → one step per numbered item.
      2. ' and ' conjunction split → one step per clause.
      3. Sentence split on '. ' → one step per sentence.
      4. Fallback: the whole task is one step.

    Produces a linear (pipeline) dependency chain so the result is a valid DAG.
    """
    # 1. Numbered list
    numbered = _NUMBERED_RE.findall(task)
    if len(numbered) >= 2:
        return _linear_steps(numbered)

    # 2. ' and ' conjunction
    parts_and = [p.strip() for p in re.split(r"\s+and\s+", task, flags=re.IGNORECASE)
                 if p.strip()]
    if len(parts_and) >= 2:
        return _linear_steps(parts_and)

    # 3. Sentence split
    sentences = [s.strip() for s in re.split(r"\.\s+", task) if s.strip()]
    if len(sentences) >= 2:
        return _linear_steps(sentences)

    # 4. Single-step fallback
    return [{"id": "s1", "description": task.strip(), "depends_on": []}]


def _linear_steps(descriptions: list[str]) -> list[dict[str, Any]]:
    """Build a linear (pipeline) chain from a list of descriptions."""
    steps = []
    for i, desc in enumerate(descriptions, 1):
        steps.append({
            "id": f"s{i}",
            "description": desc.strip(),
            "depends_on": [f"s{i - 1}"] if i > 1 else [],
        })
    return steps


# ---------------------------------------------------------------------------
# -- plan() — main entry point ----------------------------------------------
# ---------------------------------------------------------------------------

#: Type alias for an injected decomposer callable.
Decomposer = Callable[[str], list[dict[str, Any]]]


def plan(
    task: str,
    *,
    decomposer: Decomposer | None = None,
) -> Plan:
    """Decompose *task* into a validated subtask DAG and return a Plan.

    Args:
        task:        The task string to plan.
        decomposer:  Optional callable ``(task: str) -> list[dict]`` that
                     returns step dicts with keys: id, description, depends_on
                     (list of ids), and optionally role, difficulty.
                     When omitted, the deterministic heuristic decomposer is
                     used so the function always works offline.

    Returns:
        A frozen :class:`Plan` whose steps form a valid DAG.

    Raises:
        ValueError: If the decomposer returns a step graph with cycles or
                    unresolved dependency ids.
    """
    decompose = decomposer if decomposer is not None else _deterministic_decompose
    raw_steps = decompose(task)

    steps = [
        PlanStep(
            id=s["id"],
            description=s["description"],
            depends_on=tuple(s.get("depends_on") or []),
            role=s.get("role"),
            difficulty=s.get("difficulty"),
        )
        for s in raw_steps
    ]

    _validate_dag(steps)
    return Plan(task=task, steps=tuple(steps))


# ---------------------------------------------------------------------------
# -- plan_to_graph_config ---------------------------------------------------
# ---------------------------------------------------------------------------

def plan_to_graph_config(plan_obj: Plan) -> dict[str, Any]:
    """Serialize a Plan to the runtime graph config shape.

    The returned dict matches the ``dag`` argument expected by
    ``GraphStore.create_graph(name, dag)``:

    .. code-block:: python

        {
            "nodes": {
                "s1": {"type": "tool", "payload": {"description": "...", ...}},
                ...
            },
            "edges": [["s1", "s2"], ...],
        }

    Node ``type`` is ``"tool"`` (the generic default); the payload carries
    at minimum a ``description`` field so a worker knows what to execute.
    Optional ``role`` and ``difficulty`` are included in the payload when set.
    """
    nodes: dict[str, Any] = {}
    edges: list[list[str]] = []

    for step in plan_obj.steps:
        payload: dict[str, Any] = {"description": step.description}
        if step.role is not None:
            payload["role"] = step.role
        if step.difficulty is not None:
            payload["difficulty"] = step.difficulty

        nodes[step.id] = {"type": "tool", "payload": payload}

        for dep in step.depends_on:
            edges.append([dep, step.id])

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# -- emit_graph_config — write config to file -------------------------------
# ---------------------------------------------------------------------------

def emit_graph_config(plan_obj: Plan, path: str | Path) -> None:
    """Write the graph config for *plan_obj* to *path* as JSON.

    The written file is the "self-plan emits a config file, not code"
    deliverable (P1 + P3). It is directly loadable by GraphStore:

    .. code-block:: python

        dag = json.loads(Path(path).read_text())
        store.create_graph("my_plan", dag)

    Args:
        plan_obj: The Plan to serialize.
        path:     Destination file path (str or Path). Parent directory must
                  exist. Overwrites if already present.
    """
    cfg = plan_to_graph_config(plan_obj)
    # Include task metadata for human readability (optional but encouraged)
    out = {"task": plan_obj.task, **cfg}
    Path(path).write_text(json.dumps(out, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# -- __main__ self-check ----------------------------------------------------
# ---------------------------------------------------------------------------

def _demo() -> None:
    """Assert-based self-check; prints 'OK ...' per section."""

    # 1. Deterministic decomposer — numbered list
    p = plan("1. collect data 2. analyze results 3. write report")
    assert len(p.steps) == 3, p.steps
    assert p.steps[0].id == "s1"
    assert p.steps[1].depends_on == ("s1",)
    assert p.steps[2].depends_on == ("s2",)
    print("OK: numbered-list decomposition")

    # 2. Deterministic decomposer — 'and' split
    p2 = plan("research the topic and write a summary")
    assert len(p2.steps) >= 2
    print("OK: 'and' conjunction split")

    # 3. Single-step fallback
    p3 = plan("rename a variable")
    assert len(p3.steps) >= 1
    assert p3.task == "rename a variable"
    print("OK: single-step fallback")

    # 4. Injected decomposer
    def fake(task: str) -> list[dict]:
        return [
            {"id": "a", "description": "step A", "depends_on": []},
            {"id": "b", "description": "step B", "depends_on": ["a"]},
        ]

    p4 = plan("do something", decomposer=fake)
    assert len(p4.steps) == 2
    assert p4.steps[1].depends_on == ("a",)
    print("OK: injected decomposer")

    # 5. Cycle detection
    def cyclic(_: str) -> list[dict]:
        return [
            {"id": "x", "description": "x", "depends_on": ["y"]},
            {"id": "y", "description": "y", "depends_on": ["x"]},
        ]

    try:
        plan("cyclic", decomposer=cyclic)
        raise AssertionError("should have raised ValueError for cycle")
    except ValueError:
        pass
    print("OK: cycle detection")

    # 6. Unresolved dep detection
    def bad_dep(_: str) -> list[dict]:
        return [{"id": "s1", "description": "step", "depends_on": ["ghost"]}]

    try:
        plan("bad", decomposer=bad_dep)
        raise AssertionError("should have raised ValueError for unresolved dep")
    except ValueError:
        pass
    print("OK: unresolved dep detection")

    # 7. plan_to_graph_config shape
    p5 = Plan(task="pipeline", steps=(
        PlanStep(id="s1", description="collect", depends_on=()),
        PlanStep(id="s2", description="process", depends_on=("s1",)),
    ))
    cfg = plan_to_graph_config(p5)
    assert "nodes" in cfg and "edges" in cfg
    assert cfg["nodes"]["s1"]["type"] == "tool"
    assert cfg["nodes"]["s1"]["payload"]["description"] == "collect"
    assert ["s1", "s2"] in cfg["edges"]
    print("OK: plan_to_graph_config shape")

    # 8. emit_graph_config round-trip
    import tempfile
    p6 = Plan(task="emit test", steps=(
        PlanStep(id="s1", description="step 1", depends_on=()),
        PlanStep(id="s2", description="step 2", depends_on=("s1",)),
    ))
    with tempfile.TemporaryDirectory() as d:
        out_path = Path(d) / "plan.json"
        emit_graph_config(p6, out_path)
        data = json.loads(out_path.read_text())
        assert data["task"] == "emit test"
        assert "nodes" in data and "edges" in data
        assert data["nodes"]["s2"]["payload"]["description"] == "step 2"
    print("OK: emit_graph_config round-trip")

    # 9. GraphStore integration
    import tempfile as tf
    from agentkit.runtime.graph_store import GraphStore

    p7 = Plan(task="gs test", steps=(
        PlanStep(id="s1", description="collect", depends_on=()),
        PlanStep(id="s2", description="process", depends_on=("s1",)),
    ))
    dag = plan_to_graph_config(p7)
    with tf.TemporaryDirectory() as d:
        store = GraphStore(str(Path(d) / "test.db"))
        gid = store.create_graph("demo_plan", dag)
        rid = store.start_run(gid, "self-check")
        states = store.node_states(rid)
        assert states["s1"] == "ready", states
        assert states["s2"] == "pending", states
    print("OK: GraphStore integration")


if __name__ == "__main__":
    _demo()
