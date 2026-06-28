"""Semantic finding dedup (STRUM Attribute/Value-Merge): cluster, keep centers, drop rest.

Collapses the near-duplicate findings that an additive merge would otherwise dump into the
document (the live failure: ~26 findings appended as an unordered, repetitive block). Pure
and embedder-injected; a lexical fallback runs when no embedder is available."""
from __future__ import annotations

import math

from .types import Embedder, Finding


def _cosine(a: list[float], b: list[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


def _key(f: Finding) -> str:
    """Dedup key: the verbatim quote when present (the strongest signal), else the framing."""
    return (f.quote or f.why or f.title or f.url).strip().lower()


def _strength(f: Finding) -> tuple[bool, int]:
    """Tie-break: keep verified-quote > more-evidence > first-seen."""
    return (f.quote_verified, len((f.quote or "") + (f.why or "")))


def dedupe_findings(
    findings: list[Finding],
    embedder: Embedder | None,
    threshold: float = 0.85,
) -> tuple[list[Finding], int]:
    """Greedy semantic clustering. A finding within ``threshold`` cosine of an already-kept
    center is a duplicate and is dropped; the stronger of the two is kept as the center
    (verified-quote > longer-evidence > first). ``embedder`` None -> lexical fallback that
    collapses exact normalized keys only. Order-preserving. Returns ``(kept, n_dropped)``."""
    if not findings:
        return [], 0

    if embedder is None:
        seen: dict[str, Finding] = {}
        order: list[str] = []
        for f in findings:
            k = _key(f)
            if k not in seen:
                seen[k] = f
                order.append(k)
            elif _strength(f) > _strength(seen[k]):
                seen[k] = f
        kept = [seen[k] for k in order]
        return kept, len(findings) - len(kept)

    try:
        vecs = embedder.embed([_key(f) for f in findings])
    except Exception:  # noqa: BLE001 — embedder down → no dedup, never drop blindly
        return list(findings), 0

    kept: list[Finding] = []
    kept_vecs: list[list[float]] = []
    dropped = 0
    for f, v in zip(findings, vecs):
        hit = next((i for i, kv in enumerate(kept_vecs) if _cosine(v, kv) >= threshold), None)
        if hit is None:
            kept.append(f)
            kept_vecs.append(v)
        else:
            dropped += 1
            if _strength(f) > _strength(kept[hit]):
                kept[hit] = f  # promote the stronger finding to the cluster center
    return kept, dropped
