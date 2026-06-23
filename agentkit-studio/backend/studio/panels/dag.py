"""studio.panels.dag — durable DAG panel (SPEC §5.5 #5).

``plan_to_graph_config(plan)`` → ``GraphStore.create_graph`` → ``start_run``
materializes the plan as a durable run whose node states (pending/ready/running/
done/failed) drive ``dag`` frames. Studio simulates the run forward in lockstep
with the phase loop: as each phase completes, the corresponding node is marked
done and downstream nodes promote ready→pending automatically (GraphStore owns
that transition), so the panel mirrors real execution state.

The store is a throwaway SQLite file under a temp dir — durability is the
demonstrated property, not a Studio requirement.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agentkit.planner.core import Plan, plan_to_graph_config
from agentkit.runtime.graph_store import GraphStore

from studio.events import DagEvent


class DagTracker:
    """Owns a ``GraphStore`` run and emits ``DagEvent`` snapshots as phases run.

    Construct once per studio run from the assigned ``Plan``; call
    ``snapshot()`` to read current node states, ``mark_done(step_id)`` after each
    phase completes. All operations no-op-safely if the store could not be built.
    """

    def __init__(self, plan: Plan) -> None:
        self._ok = False
        self.graph_id = ""
        self._run_id = ""
        self._edges: list[list[str]] = []
        self._store: GraphStore | None = None
        try:
            dag = plan_to_graph_config(plan)
            self._edges = [list(e) for e in dag.get("edges", [])]
            db_path = str(Path(tempfile.mkdtemp(prefix="studio_dag_")) / "dag.db")
            self._store = GraphStore(db_path)
            self.graph_id = self._store.create_graph(plan.task[:60] or "plan", dag)
            self._run_id = self._store.start_run(self.graph_id, "studio")
            self._ok = True
        except Exception:  # noqa: BLE001 - DAG panel must never break the run
            self._ok = False

    def snapshot(self) -> DagEvent:
        """Read current node states into a ``DagEvent`` (empty if store absent)."""
        if not self._ok or self._store is None:
            return DagEvent(graph_id="", nodes=[], edges=[])
        try:
            states = self._store.node_states(self._run_id)
        except Exception:  # noqa: BLE001
            states = {}
        nodes = [{"id": nid, "status": status} for nid, status in states.items()]
        return DagEvent(graph_id=self.graph_id, nodes=nodes, edges=self._edges)

    def mark_done(self, step_id: str, *, tokens: int = 0) -> None:
        """Mark a node DONE in the durable store; promotion is automatic."""
        if not self._ok or self._store is None:
            return
        try:
            # Claim then complete so the node leaves the READY/PENDING frontier.
            self._claim(step_id)
            self._store.mark_done(self._run_id, step_id, {"tokens": tokens})
        except Exception:  # noqa: BLE001
            pass

    def _claim(self, step_id: str) -> None:
        """Best-effort: a node must be RUNNING before ``mark_done`` reads cleanly.

        ``claim_ready_node`` picks the lowest-name ready node, which may not be
        ``step_id``; we only need the row to exist, so a failed/mismatched claim
        is harmless — ``mark_done`` writes the status directly by name.
        """
        store = self._store
        if store is None:
            return
        try:
            store.claim_ready_node(self._run_id, "studio")
        except Exception:  # noqa: BLE001
            pass
