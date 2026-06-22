"""Real-LLM integration test for Phase 8 dynamic per-step topology.

Marked ``@pytest.mark.integration`` so it is EXCLUDED from the default offline
suite (run with ``pytest -m integration``). It connects to a live oMLX backend
on :8000, plans a multi-part task, asserts the deterministic per-step topology
assignment (compare→MESH, write→SINGLE), then EXECUTES the plan against the
real model and asserts non-empty per-step output.

If oMLX is unreachable, the test SKIPS (never silently passes, never fakes).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from agentkit.planner import plan  # noqa: E402
from agentkit.topology import (  # noqa: E402
    MESH,
    SINGLE,
    assign_topologies,
    run_plan,
)

pytestmark = pytest.mark.integration


def _client_or_skip():
    """Build a real oMLX client, or skip if the backend is unreachable."""
    try:
        from dynamic_topology_e2e import OMLXClient, discover_model
        model = discover_model()
    except Exception as exc:  # connectivity / import
        pytest.skip(f"oMLX unreachable on :8000 ({exc})")
    return OMLXClient(model), model


def test_assignment_then_real_execution():
    client, model = _client_or_skip()

    task = "1. compare vector RAG and GraphRAG 2. write a short recommendation"
    assigned = assign_topologies(plan(task), mode="auto")

    by_desc = {st.description.lower(): st.topology for st in assigned.steps}
    compare = next(t for d, t in by_desc.items() if "compare" in d)
    write = next(t for d, t in by_desc.items() if "write" in d or "recommend" in d)
    assert compare == MESH
    assert write == SINGLE

    # Execute for real — assert each step produced non-empty model output.
    result = run_plan(assigned, client)
    assert len(result.runs) == len(assigned.steps)
    for run in result.runs:
        assert run.output.strip(), f"step {run.step_id} produced empty output"
        assert not run.output.startswith("[error"), run.output
    assert result.total_tokens > 0
    assert client.n_calls > len(assigned.steps)  # MESH fanned out > 1 call/step
