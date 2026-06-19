"""agentkit.orchestrator.select — cascade selection (IdeaScout pattern).

A cheap-then-expensive cascade for ranking candidate items (research
directions, ideas, tasks):

  1. ``prefilter`` — a cheap rule stage drops items that fail a predicate.
  2. ``score_and_rank`` — a rubric-weighted scoring stage orders the survivors.

The scorer is INJECTED: pass a pure function for a cheap heuristic, or an
LLM-backed function for the expensive tier. Either way the AGGREGATION here is
PURE — ``Rubric.aggregate`` is a deterministic weighted mean with NO time, NO
randomness, NO I/O. (Where the per-dimension scores come from is the injected
scorer's business; this module only combines and orders them.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Dimension:
    """One scoring dimension: a stable key, a display name, and a weight."""

    key: str
    name: str
    weight: float


@dataclass(frozen=True)
class Rubric:
    """A weighted set of scoring dimensions."""

    dimensions: tuple[Dimension, ...]

    def aggregate(self, scores: dict[str, float]) -> float:
        """Weighted mean of per-dimension scores (PURE).

        ``= sum(weight * scores.get(key, 0)) / sum(weights)``. Missing
        dimensions contribute 0. Returns 0.0 when total weight is 0 (guard
        against divide-by-zero from an all-zero-weight rubric).
        """
        total_weight = sum(d.weight for d in self.dimensions)
        if total_weight == 0:
            return 0.0
        weighted = sum(d.weight * scores.get(d.key, 0.0) for d in self.dimensions)
        return weighted / total_weight


def prefilter(items: list[T], predicate: Callable[[T], bool]) -> list[T]:
    """Cheap rule stage: keep only items for which ``predicate`` is truthy."""
    return [item for item in items if predicate(item)]


def score_and_rank(
    items: list[T],
    rubric: Rubric,
    scorer: Callable[[T, Rubric], dict[str, float]],
) -> list[tuple[T, float, dict[str, float]]]:
    """Score each item with the INJECTED ``scorer``, rank by aggregate desc.

    Returns ``[(item, aggregate, per_dimension_scores), ...]`` sorted by
    aggregate descending. Ties keep their original relative order (Python's
    sort is stable).
    """
    scored: list[tuple[T, float, dict[str, float]]] = []
    for item in items:
        per_dim = scorer(item, rubric)
        scored.append((item, rubric.aggregate(per_dim), per_dim))
    scored.sort(key=lambda triple: triple[1], reverse=True)
    return scored


def cascade(
    items: list[T],
    predicate: Callable[[T], bool],
    rubric: Rubric,
    scorer: Callable[[T, Rubric], dict[str, float]],
) -> list[tuple[T, float, dict[str, float]]]:
    """Run the full cheap-then-expensive cascade: prefilter then score_and_rank."""
    return score_and_rank(prefilter(items, predicate), rubric, scorer)


if __name__ == "__main__":
    # A rubric with two weighted dimensions.
    rubric = Rubric(
        dimensions=(
            Dimension(key="impact", name="Impact", weight=3.0),
            Dimension(key="effort", name="Low effort", weight=1.0),
        )
    )

    # aggregate is a weighted mean: (3*1.0 + 1*0.0) / 4 = 0.75.
    assert abs(rubric.aggregate({"impact": 1.0, "effort": 0.0}) - 0.75) < 1e-9
    # Missing dimension contributes 0: (3*0.5)/4 = 0.375.
    assert abs(rubric.aggregate({"impact": 0.5}) - 0.375) < 1e-9
    # Zero-weight guard.
    zero = Rubric(dimensions=(Dimension("x", "X", 0.0),))
    assert zero.aggregate({"x": 1.0}) == 0.0

    items = [
        {"name": "a", "impact": 0.9, "effort": 0.1, "ok": True},
        {"name": "b", "impact": 0.2, "effort": 0.9, "ok": True},
        {"name": "c", "impact": 1.0, "effort": 1.0, "ok": False},  # filtered out
    ]

    def _predicate(it: dict) -> bool:
        return bool(it["ok"])

    def _scorer(it: dict, rb: Rubric) -> dict[str, float]:
        return {"impact": it["impact"], "effort": it["effort"]}

    # prefilter cuts the count (c is dropped).
    assert len(prefilter(items, _predicate)) == 2

    ranked = cascade(items, _predicate, rubric, _scorer)
    assert len(ranked) == 2, ranked  # c was prefiltered
    # a (impact-heavy) outranks b under the impact-weighted rubric.
    assert ranked[0][0]["name"] == "a", ranked
    assert ranked[0][1] > ranked[1][1], ranked

    print("select self-check OK")
