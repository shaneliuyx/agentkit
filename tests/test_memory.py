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


# --- P34 evidence/provenance: source tag round-trips and survives search ---

def test_source_defaults_none_for_back_compat(store: MemoryStore) -> None:
    store.add("episodic", "no provenance given")
    entry = store.get_recent(limit=1)[0]
    assert entry.source is None


def test_source_round_trips_through_get_recent(store: MemoryStore) -> None:
    store.add("episodic", "user asked about cats", source="user")
    entry = store.get_recent(limit=1)[0]
    assert entry.source == "user"


def test_source_survives_search(store: MemoryStore) -> None:
    store.add("semantic", "deploy ran at noon", source="assistant")
    store.add("semantic", "unrelated weather note", source="user")
    hits = store.search("deploy noon", top_k=2)
    match = next(h for h in hits if h.content == "deploy ran at noon")
    assert match.source == "assistant"


def test_source_persists_across_reopen(tmp_path) -> None:
    path = tmp_path / "prov.db"
    s1 = MemoryStore(path, embedder=FakeEmbedder())
    s1.add("episodic", "provenance fact", source="tool:git")
    s1.close()
    s2 = MemoryStore(path, embedder=FakeEmbedder())
    assert s2.get_recent(limit=1)[0].source == "tool:git"


# --- P36 read/retention loop: read records usage; retention consumes it ---

def test_search_bumps_access_count_and_last_used(store: MemoryStore) -> None:
    store.add("semantic", "alpha beta gamma")
    store.add("semantic", "delta epsilon zeta")
    before = {e.content: e for e in store.get_recent("semantic", limit=10)}
    assert before["alpha beta gamma"].access_count == 0
    assert before["alpha beta gamma"].last_used is None

    store.search("alpha beta", top_k=1)  # track defaults True

    after = {e.content: e for e in store.get_recent("semantic", limit=10)}
    assert after["alpha beta gamma"].access_count == 1
    assert after["alpha beta gamma"].last_used is not None
    assert after["delta epsilon zeta"].access_count == 0  # not returned → not bumped


def test_search_track_false_is_side_effect_free(store: MemoryStore) -> None:
    store.add("semantic", "alpha beta gamma")
    store.search("alpha beta", top_k=1, track=False)
    entry = store.get_recent("semantic", limit=1)[0]
    assert entry.access_count == 0
    assert entry.last_used is None


def test_returned_entry_reflects_the_bump(store: MemoryStore) -> None:
    store.add("semantic", "alpha beta gamma")
    [hit] = store.search("alpha beta", top_k=1)
    assert hit.access_count == 1  # the just-recorded usage is visible on the result


def test_evict_coldest_consumes_access_signal(store: MemoryStore) -> None:
    store.add("semantic", "hot record alpha")
    store.add("semantic", "cold record beta")
    # Warm up only the first record.
    store.search("hot record alpha", top_k=1)
    store.search("hot record alpha", top_k=1)
    evicted = store.evict_coldest(keep=1)
    assert evicted == 1
    survivors = [e.content for e in store.get_recent("semantic", limit=10)]
    assert survivors == ["hot record alpha"]


def test_evict_coldest_noop_when_under_budget(store: MemoryStore) -> None:
    store.add("semantic", "only one")
    assert store.evict_coldest(keep=5) == 0
    assert store.count() == 1


# --- P35 abstention = topic-presence (distinct from groundedness) ---

def test_topic_present_true_when_subject_in_store(store: MemoryStore) -> None:
    store.add("semantic", "the capital of Australia is Canberra")
    assert store.topic_present("Australia capital") is True


def test_topic_present_false_when_subject_absent(store: MemoryStore) -> None:
    store.add("semantic", "the capital of Australia is Canberra")
    # Subject genuinely not in records → abstain.
    assert store.topic_present("quantum chromodynamics lagrangian") is False


def test_topic_present_empty_store_abstains(tmp_path) -> None:
    empty = MemoryStore(tmp_path / "empty2.db", embedder=FakeEmbedder())
    assert empty.topic_present("anything at all") is False


def test_topic_present_considers_source_tag(store: MemoryStore) -> None:
    store.add("semantic", "a generic note", source="kubernetes")
    assert store.topic_present("kubernetes") is True


# --- P23 cheap-first retrieval ladder: keyword rung before the vector tier ---

def test_prefilter_narrows_to_keyword_matches(store: MemoryStore) -> None:
    store.add("semantic", "redis cache eviction policy")
    store.add("semantic", "postgres index tuning guide")
    store.add("semantic", "completely unrelated topic here")
    hits = store.search("redis eviction", top_k=10, prefilter=True, track=False)
    # Only the rows sharing a keyword survive the cheap rung.
    contents = {h.content for h in hits}
    assert "redis cache eviction policy" in contents
    assert "postgres index tuning guide" not in contents


def test_prefilter_falls_back_when_no_keyword_match(store: MemoryStore) -> None:
    store.add("semantic", "redis cache eviction policy")
    # No keyword overlap → fall back to full vector tier, never empty out.
    hits = store.search("zzz qqq", top_k=10, prefilter=True, track=False)
    assert len(hits) == 1


def test_prefilter_default_off_preserves_vector_behavior(store: MemoryStore) -> None:
    store.add("semantic", "redis cache eviction policy")
    store.add("semantic", "postgres index tuning guide")
    plain = store.search("redis eviction", top_k=10, track=False)
    # Default (no prefilter) returns the full vector-ranked set.
    assert len(plain) == 2
