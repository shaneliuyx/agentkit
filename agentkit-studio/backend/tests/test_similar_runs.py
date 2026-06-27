"""Tests for R10: cross-task context retrieval (TaskRunStore.similar_runs).

The store retrieves prior context from SEMANTICALLY SIMILAR tasks, not just the
exact task_hash. A deterministic fake embedder keeps the test offline: it maps a
text to a vector over a fixed keyword vocabulary, so cosine similarity is
controlled and predictable (no oMLX / network needed).
"""

from pathlib import Path

import pytest

from studio.task_runs import TaskRun, TaskRunStore, task_hash


_VOCAB = ["agent", "loop", "cooking", "recipe", "finance", "tax"]


class FakeEmbedder:
    """Bag-of-keywords embedder: deterministic, offline, controllable."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            low = t.lower()
            out.append([1.0 if word in low else 0.0 for word in _VOCAB])
        return out


def _run(req: str, weaknesses: list[str], score: float, sid: str) -> TaskRun:
    return TaskRun(
        task_hash=task_hash(req), session_id=sid, version=1, score=score,
        weaknesses=weaknesses, artifact_path="", requirement=req,
    )


@pytest.fixture()
def store(tmp_path: Path) -> TaskRunStore:
    s = TaskRunStore(db_path=tmp_path / "t.db", embedder=FakeEmbedder())
    s.record(_run("build an agent loop framework", ["no eval harness"], 0.4, "s1"))
    s.record(_run("design an agentic loop system", ["missing retries"], 0.6, "s2"))
    s.record(_run("a cooking recipe for pasta", ["no salt"], 0.5, "s3"))
    s.record(_run("file my tax finance report", ["missing receipts"], 0.7, "s4"))
    return s


def test_similar_runs_finds_related_task_not_exact(store: TaskRunStore) -> None:
    # Query about agent loops → the two agent/loop tasks, NOT cooking/finance.
    hits = store.similar_runs("agent loop orchestration", FakeEmbedder(), k=5)
    reqs = [run.requirement for run, _sim in hits]
    assert any("agent loop" in r for r in reqs)
    assert any("agentic loop" in r for r in reqs)
    assert not any("cooking" in r or "tax" in r for r in reqs)


def test_similar_runs_ranked_by_similarity(store: TaskRunStore) -> None:
    hits = store.similar_runs("agent loop", FakeEmbedder(), k=5, min_similarity=0.1)
    sims = [sim for _run, sim in hits]
    assert sims == sorted(sims, reverse=True)  # descending


def test_similar_runs_excludes_current_hash(store: TaskRunStore) -> None:
    req = "design an agentic loop system"
    hits = store.similar_runs(req, FakeEmbedder(), k=5, exclude_hash=task_hash(req))
    assert all(run.task_hash != task_hash(req) for run, _ in hits)


def test_similar_runs_threshold_filters_unrelated(store: TaskRunStore) -> None:
    # High threshold + unrelated query → nothing clears the bar.
    hits = store.similar_runs("quantum chromodynamics", FakeEmbedder(),
                              k=5, min_similarity=0.9)
    assert hits == []


def test_similar_runs_no_embedder_returns_empty(store: TaskRunStore) -> None:
    assert store.similar_runs("agent loop", None, k=5) == []


def test_accumulated_weaknesses_merges_exact_then_similar(store: TaskRunStore) -> None:
    # Record a second run of an existing exact task so exact-history is non-empty.
    store.record(_run("build an agent loop framework", ["flaky tests"], 0.8, "s5"))
    exact = task_hash("build an agent loop framework")
    merged = store.accumulated_weaknesses(
        "build an agent loop framework", exact, embedder=FakeEmbedder(),
        min_similarity=0.1,
    )
    # exact-task lessons first
    assert merged[:2] == ["no eval harness", "flaky tests"]
    # similar-task lesson (from the agentic-loop task) also pulled in
    assert "missing retries" in merged
    # unrelated tasks' lessons excluded
    assert "no salt" not in merged and "missing receipts" not in merged


def test_accumulated_weaknesses_dedups(store: TaskRunStore) -> None:
    store.record(_run("build an agent loop framework", ["no eval harness"], 0.9, "s6"))
    exact = task_hash("build an agent loop framework")
    merged = store.accumulated_weaknesses(
        "build an agent loop framework", exact, embedder=FakeEmbedder(),
    )
    assert merged.count("no eval harness") == 1


def test_accumulated_weaknesses_no_embedder_is_exact_only(store: TaskRunStore) -> None:
    exact = task_hash("build an agent loop framework")
    merged = store.accumulated_weaknesses("build an agent loop framework", exact)
    assert merged == ["no eval harness"]  # only the exact task's lesson


def test_accumulated_weaknesses_consolidates_near_duplicates(tmp_path: Path) -> None:
    """dedup + CONSOLIDATE: two weaknesses that embed identically (same vocab)
    collapse to one, even though their strings differ."""
    s = TaskRunStore(db_path=tmp_path / "c.db", embedder=FakeEmbedder())
    # Both weaknesses contain only the vocab word "loop" → identical vectors →
    # cosine 1.0 → consolidated to the first.
    s.record(_run("build an agent loop framework",
                  ["the loop is weak", "loop needs work"], 0.5, "s1"))
    exact = task_hash("build an agent loop framework")
    merged = s.accumulated_weaknesses(
        "build an agent loop framework", exact, embedder=FakeEmbedder(),
        consolidate_threshold=0.85,
    )
    assert merged == ["the loop is weak"]  # second near-duplicate dropped


def test_consolidate_disabled_without_embedder_keeps_all(tmp_path: Path) -> None:
    s = TaskRunStore(db_path=tmp_path / "c2.db")  # no embedder
    s.record(_run("x", ["the loop is weak", "loop needs work"], 0.5, "s1"))
    merged = s.accumulated_weaknesses("x", task_hash("x"))  # no embedder
    assert merged == ["the loop is weak", "loop needs work"]  # both kept


def test_backfill_embeds_legacy_rows(tmp_path: Path) -> None:
    # Rows written WITHOUT an embedder (legacy) get embedded lazily on first query.
    s_noemb = TaskRunStore(db_path=tmp_path / "legacy.db")  # no embedder
    s_noemb.record(_run("build an agent loop framework", ["x"], 0.4, "s1"))
    # Reopen with an embedder and query — backfill should make the row findable.
    s = TaskRunStore(db_path=tmp_path / "legacy.db", embedder=FakeEmbedder())
    hits = s.similar_runs("agent loop", FakeEmbedder(), k=5, min_similarity=0.1)
    assert any("agent loop" in run.requirement for run, _ in hits)
