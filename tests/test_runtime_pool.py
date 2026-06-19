"""Tests for agentkit.runtime.pool — parallel worker pool (no network)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from agentkit.runtime import GraphStore, Node, run_graph


def _star_dag() -> dict:
    return {
        "nodes": {k: {"type": "tool", "payload": {}} for k in
                  ("dispatch", "w1", "w2", "w3", "w4", "reduce")},
        "edges": ([["dispatch", f"w{i}"] for i in range(1, 5)]
                  + [[f"w{i}", "reduce"] for i in range(1, 5)]),
    }


def _sleep_handler(node: Node) -> dict[str, Any]:
    time.sleep(0.05)  # simulate I/O so overlap is observable
    return {"text": node.name}


@pytest.mark.unit
def test_run_graph_overlaps_and_completes(tmp_path: Path):
    store = GraphStore(str(tmp_path / "pool.db"))
    run_id = store.start_run(store.create_graph("star", _star_dag()), "manual")
    out = run_graph(store, run_id, _sleep_handler, concurrency=4)
    assert out["nodes_done"] == 6
    assert store.run_status(run_id) == "done"
    assert out["peak_concurrency"] >= 2          # the 4 leaves genuinely overlapped
    assert set(out["results"]) == {"dispatch", "w1", "w2", "w3", "w4", "reduce"}


@pytest.mark.unit
def test_run_graph_concurrency_one_is_sequential(tmp_path: Path):
    store = GraphStore(str(tmp_path / "seq.db"))
    run_id = store.start_run(store.create_graph("star", _star_dag()), "manual")
    out = run_graph(store, run_id, _sleep_handler, concurrency=1)
    assert out["nodes_done"] == 6
    assert out["peak_concurrency"] == 1          # no overlap with one worker


@pytest.mark.unit
def test_run_graph_failed_node_does_not_hang(tmp_path: Path):
    # A node that always raises exhausts retries → FAILED; the run must still
    # terminate (the run-not-running exit / deadline backstop), not spin forever.
    store = GraphStore(str(tmp_path / "fail.db"))
    dag = {"nodes": {"a": {"type": "tool", "payload": {}}}, "edges": []}
    run_id = store.start_run(store.create_graph("solo", dag), "manual")

    def boom(node: Node) -> dict[str, Any]:
        raise RuntimeError("always fails")

    out = run_graph(store, run_id, boom, concurrency=2, max_wall_s=10.0)
    assert store.run_status(run_id) == "failed"
    assert out["nodes_done"] == 0
