"""agentkit.runtime.pool — N parallel workers draining a run's READY frontier.

The durable driver in `topology.pipeline` was single-threaded: correct + durable
but it never *overlapped* independent nodes, so a star/tree's leaves ran one at a
time. This pool runs N workers that genuinely overlap.

WHY threads, not asyncio: the node handlers are SYNCHRONOUS blocking I/O (a
`client.chat` HTTP call per llm node). Python releases the GIL during blocking
I/O, so a `ThreadPoolExecutor` of N workers overlaps those calls for real — no
async rewrite of the handler needed. Cross-PROCESS safety still comes from the
`GraphStore`'s `FileLock`; the in-process `claim_lock` below only stops sibling
THREADS from entering that one shared FileLock instance at once (the lab's
single-use-fd bug, BCJ-1). The claim is already a serialization point by design
(`ORDER BY name LIMIT 1`), so gating it costs nothing; `mark_done`/`mark_failed`
open their own connections (WAL) and stay fully concurrent.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from agentkit.runtime.graph_store import GraphStore, Node

NodeHandler = Callable[[Node], dict[str, Any]]


class _Peak:
    """Observed high-water mark of concurrently-executing handlers — the
    *measured* evidence that the topology actually overlapped (not the target)."""

    def __init__(self) -> None:
        self._cur = 0
        self.peak = 0
        self._lock = threading.Lock()

    def enter(self) -> None:
        with self._lock:
            self._cur += 1
            self.peak = max(self.peak, self._cur)

    def exit(self) -> None:
        with self._lock:
            self._cur -= 1


def run_graph(
    store: GraphStore,
    run_id: str,
    handler: NodeHandler,
    concurrency: int = 4,
    *,
    idle_poll_s: float = 0.02,
    max_wall_s: float = 300.0,
) -> dict[str, Any]:
    """Run `run_id` to completion with `concurrency` worker threads.

    Each worker loops: claim a READY node (under the in-process claim lock), run
    the handler, mark_done / mark_failed. A worker exits when no node is
    claimable and the run is no longer `running`. `max_wall_s` is a deadlock
    backstop (e.g. a terminally-failed node blocking its downstream forever).

    Returns {"wall_s", "peak_concurrency", "nodes_done", "results"}.
    """
    claim_lock = threading.Lock()   # one thread inside the shared FileLock at a time
    peak = _Peak()
    results: dict[str, dict[str, Any]] = {}
    res_lock = threading.Lock()
    done = 0
    done_lock = threading.Lock()
    deadline = time.perf_counter() + max_wall_s

    def worker(worker_id: str) -> None:
        nonlocal done
        while time.perf_counter() < deadline:
            with claim_lock:
                node = store.claim_ready_node(run_id, worker_id)
            if node is None:
                if store.run_status(run_id) != "running":
                    return
                time.sleep(idle_poll_s)   # a peer is mid-node; it will unblock soon
                continue
            peak.enter()
            try:
                out = handler(node)
                store.mark_done(run_id, node.name, out)
                with res_lock:
                    results[node.name] = out
                with done_lock:
                    done += 1
            except Exception as exc:      # durable retry/fail path
                store.mark_failed(run_id, node.name, repr(exc))
                with res_lock:
                    results[node.name] = {"text": f"[error: {exc}]"}
            finally:
                peak.exit()

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        for fut in [ex.submit(worker, f"w{i}") for i in range(max(1, concurrency))]:
            fut.result()
    return {
        "wall_s": time.perf_counter() - start,
        "peak_concurrency": peak.peak,
        "nodes_done": done,
        "results": results,
    }


def _demo() -> None:
    """Self-check: parallel leaves genuinely overlap (peak > 1), no LLM/network.

    A star DAG (1 dispatch → 4 workers → reduce) with a handler that sleeps;
    with concurrency=4 the four workers must overlap (peak >= 2), and all 6
    nodes complete."""
    import tempfile
    from pathlib import Path

    dag = {
        "nodes": {k: {"type": "tool", "payload": {}} for k in
                  ("dispatch", "w1", "w2", "w3", "w4", "reduce")},
        "edges": ([["dispatch", f"w{i}"] for i in range(1, 5)]
                  + [[f"w{i}", "reduce"] for i in range(1, 5)]),
    }

    def handler(node: Node) -> dict[str, Any]:
        time.sleep(0.05)   # simulate I/O so overlap is observable
        return {"text": node.name}

    with tempfile.TemporaryDirectory() as d:
        store = GraphStore(str(Path(d) / "pool.db"))
        gid = store.create_graph("star", dag)
        run_id = store.start_run(gid, "manual")
        out = run_graph(store, run_id, handler, concurrency=4)
        assert out["nodes_done"] == 6, out
        assert store.run_status(run_id) == "done"
        assert out["peak_concurrency"] >= 2, f"no overlap: {out}"
    print(f"runtime.pool._demo OK (peak_concurrency={out['peak_concurrency']})")


if __name__ == "__main__":
    _demo()
