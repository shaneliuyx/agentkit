"""agentkit.orchestrator.diversity — PURE token-Jaccard novelty check.

Used by the orchestrator to keep candidate research directions diverse: a new
direction is only worth spawning if it is sufficiently different from the ones
already tried. The similarity is cheap token-level Jaccard — NO embeddings, NO
network, NO randomness — so the whole module is deterministic and pure.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> set[str]:
    """Lowercase word-split into a set of alphanumeric tokens."""
    return set(_WORD_RE.findall(s.lower()))


def similarity(a: str, b: str) -> float:
    """Token-level Jaccard similarity in [0.0, 1.0].

    Two empty strings are treated as identical (1.0); an empty vs non-empty
    pair has no overlap (0.0).
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    union = ta | tb
    if not union:
        return 0.0
    return len(ta & tb) / len(union)


def most_similar(candidate: str, tried: list[str]) -> tuple[str, float]:
    """Return the (most-similar tried item, its similarity) to ``candidate``.

    Returns ``("", 0.0)`` when ``tried`` is empty.
    """
    if not tried:
        return "", 0.0
    best_item = tried[0]
    best_score = similarity(candidate, tried[0])
    for item in tried[1:]:
        score = similarity(candidate, item)
        if score > best_score:
            best_item, best_score = item, score
    return best_item, best_score


def is_novel(candidate: str, tried: list[str], threshold: float = 0.6) -> bool:
    """True when ``candidate`` is novel: max similarity to ``tried`` < threshold."""
    _, best = most_similar(candidate, tried)
    return best < threshold


if __name__ == "__main__":
    assert _tokens("Hello, World!") == {"hello", "world"}

    # Identical strings → 1.0; disjoint → 0.0.
    assert similarity("a b c", "a b c") == 1.0
    assert similarity("a b c", "x y z") == 0.0
    # Half overlap: {a,b} & {b,c} = {b}; union {a,b,c} → 1/3.
    assert abs(similarity("a b", "b c") - 1 / 3) < 1e-9

    # most_similar picks the closest and reports its score.
    item, score = most_similar("scale the cache layer", ["scale the cache tier", "rewrite ui"])
    assert item == "scale the cache tier", item
    assert score > 0.5, score
    assert most_similar("anything", []) == ("", 0.0)

    # Novelty: a near-duplicate is NOT novel; a different direction IS.
    tried = ["optimize the database index", "add a redis cache"]
    assert is_novel("redesign the frontend routing", tried) is True
    assert is_novel("optimize the database index strategy", tried, threshold=0.6) is False

    print("diversity self-check OK")
