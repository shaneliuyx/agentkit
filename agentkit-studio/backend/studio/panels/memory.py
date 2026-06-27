"""studio.panels.memory — memory panel (SPEC §5.5 #1).

A ``MemoryStore`` records each phase's output as an episodic memory and recalls
the most relevant prior entries before the next phase. The panel emits a
``memory`` frame with the current entries + which similarity scores recalled
them.

Graceful degradation (SPEC §9): a ``MemoryStore`` needs an embedder. If the
embedder service (oMLX :8000) is down, ``add`` stores without a vector (the
store's own non-fatal behavior) and ``search`` raises — caught here, surfaced as
a notice, never crashing the run.
"""

from __future__ import annotations

from typing import Any

from agentkit.memory.store import MemoryEntry, MemoryStore
from agentkit.types import Embedder

from studio.events import MemoryEvent
from studio.workspace import workspace_root


class MemoryTracker:
    """Owns a per-run ``MemoryStore`` and turns it into ``memory`` frames.

    Construct with the session embedder (may be None → memory disabled). All
    writes/reads are best-effort: an embedder failure degrades to an empty panel
    with a notice rather than raising into the run loop.
    """

    def __init__(self, embedder: Embedder | None) -> None:
        self._store: MemoryStore | None = None
        self._notice = ""
        if embedder is None:
            self._notice = "no embedder configured — memory panel disabled"
            return
        try:
            db_path = str(workspace_root().parent / "shared_memory.db")
            self._store = MemoryStore(db_path, embedder=embedder)
        except Exception as exc:  # noqa: BLE001
            self._notice = f"memory store unavailable: {exc}"

    def record(self, step_id: str, output: str) -> None:
        """Store one phase output as an episodic memory (best-effort)."""
        if self._store is None or not output.strip():
            return
        try:
            self._store.add("episodic", output, metadata={"step_id": step_id})
        except Exception as exc:  # noqa: BLE001
            self._notice = f"memory write degraded: {exc}"

    def recall(self, query: str, *, top_k: int = 5) -> MemoryEvent:
        """Search the store for ``query`` → ``MemoryEvent`` (empty on failure)."""
        if self._store is None:
            return MemoryEvent(entries=[], notice=self._notice)
        try:
            hits: list[MemoryEntry] = self._store.search(query, top_k=top_k, track=False)
        except Exception as exc:  # noqa: BLE001 - embedder down ⇒ notice, no crash
            return MemoryEvent(entries=[], notice=f"memory recall degraded: {exc}")
        entries = [
            {
                "id": h.id,
                "text": h.content,
                "tier": h.memory_type,
                "score": round(h.similarity, 4),
            }
            for h in hits
        ]
        return MemoryEvent(entries=entries, notice=self._notice)
