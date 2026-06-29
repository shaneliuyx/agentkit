"""Tests for the episodic-memory poison filter (DESIGN §14.6).

The memory store is SHARED across runs. Storing a worker's refusal / clarification
request as an episodic "finding" makes it resurface at high similarity in later
phases and future runs, priming the next worker to echo it. record() must drop
these; genuine findings must still be stored.
"""
from __future__ import annotations

from studio.panels.memory import MemoryTracker, _is_low_value_memory


def test_is_low_value_memory_flags_refusals() -> None:
    assert _is_low_value_memory(
        "I appreciate you sharing this, but I need to clarify: these appear to be "
        "duplicate statements of intent rather than actual research findings."
    )
    assert _is_low_value_memory("To help you effectively, I need: 1. actual data")
    assert _is_low_value_memory("I could not find any sources for this topic.")


def test_is_low_value_memory_keeps_real_content() -> None:
    assert not _is_low_value_memory(
        "## Key Findings\nLoop engineering uses control loops and feedback to steer "
        "agent behavior; see ReAct and Reflexion."
    )
    assert not _is_low_value_memory("")


def test_record_skips_refusals_keeps_findings() -> None:
    """record() stores a real finding but drops a refusal — without touching the
    real shared store (a fake store captures what would be persisted)."""
    class _FakeStore:
        def __init__(self) -> None:
            self.added: list[str] = []

        def add(self, tier, content, metadata=None) -> None:
            self.added.append(content)

    tracker = MemoryTracker(None)        # embedder None → store disabled
    tracker._store = _FakeStore()        # inject a capturing store

    tracker.record("e1:s1", "I appreciate you sharing this, but I need to clarify…")
    tracker.record("e1:s2", "## Key Findings\nReal sourced content about loops.")

    assert tracker._store.added == ["## Key Findings\nReal sourced content about loops."]
