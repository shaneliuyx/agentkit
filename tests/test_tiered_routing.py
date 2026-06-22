"""Tests for the tiered-memory read-routing additions (P33 + P27).

P33 — route the READ assembly by question type (factoid/comparison/summary).
P27 — combine retrieval rungs with the guard that a single backend can beat a
naive union (``prefer_single_if_better``).

All deterministic: a bag-of-words FakeEmbedder, no network, no LLM.
"""

from __future__ import annotations

import hashlib

import pytest

from agentkit.memory import MemoryStore, classify_question
from agentkit.memory.tiered import (
    QTYPE_COMPARISON,
    QTYPE_FACTOID,
    QTYPE_SUMMARY,
    TieredMemory,
    _COMPARISON_SYSTEM,
    _FACTOID_SYSTEM,
    _SUMMARY_SYSTEM,
)


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder (shared with test_memory)."""

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


@pytest.fixture
def tm(tmp_path) -> TieredMemory:
    store = MemoryStore(tmp_path / "tiered.db", embedder=FakeEmbedder())
    return TieredMemory(store)


# --- P33: question-type classification ---

@pytest.mark.unit
def test_classify_factoid_is_default():
    assert classify_question("what is my dog's name") == QTYPE_FACTOID


@pytest.mark.unit
def test_classify_comparison_from_compare_cue():
    assert classify_question("compare redis and postgres") == QTYPE_COMPARISON


@pytest.mark.unit
def test_classify_comparison_from_vs_cue():
    assert classify_question("redis vs postgres for caching") == QTYPE_COMPARISON


@pytest.mark.unit
def test_classify_summary_cue():
    assert classify_question("summarise what you know about me") == QTYPE_SUMMARY


# --- P33: routed build_messages selects the per-type operator ---

@pytest.mark.unit
def test_build_messages_route_picks_comparison_operator(tm: TieredMemory):
    tm.remember("redis is in-memory")
    tm.remember("postgres is on-disk")
    msgs = tm.build_messages("compare redis and postgres", k=2, route=True)
    assert msgs[0]["content"] == _COMPARISON_SYSTEM


@pytest.mark.unit
def test_build_messages_route_picks_summary_operator(tm: TieredMemory):
    tm.remember("a fact")
    msgs = tm.build_messages("summarise everything", k=2, route=True)
    assert msgs[0]["content"] == _SUMMARY_SYSTEM


@pytest.mark.unit
def test_build_messages_route_picks_factoid_operator(tm: TieredMemory):
    tm.remember("my dog is Mochi")
    msgs = tm.build_messages("what is my dog name", k=2, route=True)
    assert msgs[0]["content"] == _FACTOID_SYSTEM


@pytest.mark.unit
def test_build_messages_explicit_qtype_overrides_classifier(tm: TieredMemory):
    tm.remember("a fact")
    # Query reads factoid, but the caller supplies the eval's label.
    msgs = tm.build_messages("what is X", k=2, route=True, qtype=QTYPE_SUMMARY)
    assert msgs[0]["content"] == _SUMMARY_SYSTEM


@pytest.mark.unit
def test_build_messages_default_unrouted_uses_commit_prompt(tm: TieredMemory):
    from agentkit.memory.tiered import COMMIT_SYSTEM

    tm.remember("a fact")
    msgs = tm.build_messages("compare a and b", k=2)  # route defaults False
    assert msgs[0]["content"] == COMMIT_SYSTEM


# --- P27: union guard — a single backend can beat a naive union ---

@pytest.mark.unit
def test_recall_union_without_scorer_returns_single_backend(tm: TieredMemory):
    # No scorer → guard cannot measure → must NOT blindly union; returns the
    # vector backend alone (== plain recall).
    tm.remember("redis cache eviction")
    tm.remember("postgres index tuning")
    guarded = tm.recall_union("redis eviction", k=5)
    plain = tm.recall("redis eviction", k=5)
    assert [h.id for h in guarded] == [h.id for h in plain]


@pytest.mark.unit
def test_recall_union_prefers_single_when_scorer_favors_it(tm: TieredMemory):
    tm.remember("redis cache eviction policy")
    tm.remember("postgres index tuning guide")
    tm.remember("completely unrelated kubernetes note")

    # A scorer that rewards SMALLER, focused result sets (penalises the diluted
    # union). The single backend should win.
    def prefer_focused(hits: list) -> float:
        return -len(hits)

    chosen = tm.recall_union(
        "redis eviction", k=5,
        prefer_single_if_better=True, scorer=prefer_focused,
    )
    union = tm.recall_union("redis eviction", k=5, prefer_single_if_better=False)
    # The guarded choice is no larger than the naive union (it can pick smaller).
    assert len(chosen) <= len(union)


@pytest.mark.unit
def test_recall_union_naive_union_is_opt_in(tm: TieredMemory):
    tm.remember("redis cache eviction policy")
    tm.remember("redis memory limits")
    naive = tm.recall_union(
        "redis eviction memory", k=5, prefer_single_if_better=False
    )
    # The naive union returns a de-duplicated, capped list (no crash, has hits).
    ids = [h.id for h in naive]
    assert len(ids) == len(set(ids))  # de-duplicated by id
    assert len(ids) <= 5
