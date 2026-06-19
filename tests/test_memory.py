"""Tests for agentkit.memory.store — uses a deterministic FakeEmbedder (no network)."""

from __future__ import annotations

import hashlib

import pytest

from agentkit.memory import MemoryStore


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder for tests (no network).

    Each lowercased token sets one dimension (token hash → index), so texts
    that share vocabulary get a POSITIVE cosine similarity and unrelated texts
    stay near-orthogonal. Same text always yields the same vector, so ordering
    is stable and reproducible. This mirrors how a real embedder behaves
    (related text → higher similarity) closely enough to exercise search,
    ranking, and the ``inject_context`` relevance filter honestly.
    """

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            out.append(vec)
        return out


class FailingEmbedder:
    """Always raises — used to prove failure tolerance."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedder offline")


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    return MemoryStore(tmp_path / "mem.db", embedder=FakeEmbedder())


def test_add_and_count(store: MemoryStore) -> None:
    store.add("episodic", "a")
    store.add("semantic", "b")
    assert store.count() == 2
    assert store.count("episodic") == 1
    assert store.count("semantic") == 1


def test_search_orders_by_similarity(store: MemoryStore) -> None:
    store.add("semantic", "addition combines two numbers")
    store.add("semantic", "the sky is blue today")
    store.add("semantic", "addition is a math operation")
    hits = store.search("addition math", top_k=3)
    assert len(hits) == 3
    sims = [h.similarity for h in hits]
    assert sims == sorted(sims, reverse=True)


def test_search_type_filter(store: MemoryStore) -> None:
    store.add("episodic", "did a thing")
    store.add("semantic", "a fact")
    store.add("procedural", "a how-to")
    hits = store.search("anything", memory_type="semantic", top_k=10)
    assert all(h.memory_type == "semantic" for h in hits)
    assert len(hits) == 1


def test_inject_context_formats_block(store: MemoryStore) -> None:
    store.add("semantic", "always validate inputs")
    ctx = store.inject_context("validate inputs")
    assert ctx.startswith("<memory_context>")
    assert ctx.endswith("</memory_context>")
    assert "always validate inputs" in ctx


def test_inject_context_empty_on_no_hits(tmp_path) -> None:
    empty = MemoryStore(tmp_path / "empty.db", embedder=FakeEmbedder())
    assert empty.inject_context("anything") == ""


def test_get_recent_returns_newest_first(store: MemoryStore) -> None:
    store.add("episodic", "first")
    store.add("episodic", "second")
    store.add("episodic", "third")
    recent = store.get_recent(limit=2)
    assert len(recent) == 2
    assert recent[0].content == "third"
    assert recent[1].content == "second"


def test_embedding_failure_stores_without_vector(tmp_path) -> None:
    s = MemoryStore(tmp_path / "fail.db", embedder=FailingEmbedder())
    with pytest.warns(UserWarning):
        s.add("episodic", "stored even though embedding failed")
    # Row exists ...
    assert s.count() == 1
    # ... but a vector-less row never appears in similarity search (which itself
    # raises here because the query cannot be embedded).
    with pytest.raises(RuntimeError):
        s.search("anything")


def test_inject_context_degrades_when_embedder_down(tmp_path) -> None:
    s = MemoryStore(tmp_path / "down.db", embedder=FailingEmbedder())
    # inject_context must never raise — it returns "" instead.
    assert s.inject_context("anything") == ""


def test_record_trajectory(store: MemoryStore) -> None:
    rid = store.record_trajectory({"summary": "solved task", "metadata": {"k": 1}},
                                  salience=0.8)
    assert rid > 0
    recent = store.get_recent("episodic", limit=1)
    assert recent[0].content == "solved task"
    assert recent[0].metadata["salience"] == 0.8
    assert recent[0].metadata["k"] == 1
