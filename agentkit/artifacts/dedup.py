"""Semantic finding dedup (STRUM Attribute/Value-Merge): cluster, keep centers, drop rest.

Collapses the near-duplicate findings that an additive merge would otherwise dump into the
document (the live failure: ~26 findings appended as an unordered, repetitive block). Pure
and embedder-injected; a lexical fallback runs when no embedder is available."""
from __future__ import annotations

import math
import re
from dataclasses import replace

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


def _default_norm_url(u: str) -> str:
    """Minimal URL canonicalizer for same-URL grouping. studio passes its richer
    ``_normalize_url`` (tracking-param strip etc.); this is the no-studio fallback."""
    s = (u or "").strip().rstrip(".,)\"'>").lower()
    s = s.split("#", 1)[0].split("?", 1)[0]
    if s.startswith("http://"):
        s = "https://" + s[len("http://"):]
    return s.rstrip("/")


_SCAFFOLD_RE = re.compile(
    r"^\s*this\s+(?:section\s+)?"
    r"(?:defines|provides|shows|demonstrates|illustrates|explains|describes|"
    r"highlights|introduces|covers|outlines|presents|details|offers|gives|"
    r"means|indicates|suggests|reveals|notes|states)\b[:,]?\s*",
    re.IGNORECASE,
)


def _strip_scaffold(why: str) -> str:
    """Drop the boilerplate "This defines: …" lead the reducer otherwise stacks 30× into
    a quote-wall. Idempotent; leaves real framing prose intact."""
    out = _SCAFFOLD_RE.sub("", why or "", count=1).lstrip()
    return (out[:1].upper() + out[1:]) if out else (why or "")


def consolidate_findings(
    findings: list[Finding],
    *,
    norm_url=None,
    max_per_target: int = 3,
    strip_scaffold: bool = True,
) -> tuple[list[Finding], dict]:
    """Thin the quote-wall at its source — runs AFTER semantic ``dedupe_findings``.

    1. **Same-URL merge** — one finding per canonical URL, keeping the richest
       (``_strength``); 26 sentences off one source collapse to one.
    2. **Scaffolding strip** — remove the repetitive "This defines:" lead from ``why``.
    3. **Density cap** — at most ``max_per_target`` findings per ``patch_target`` so no
       single section becomes a citation dump.

    Pure, order-preserving. Returns ``(kept, stats)`` with per-stage drop counts."""
    nu = norm_url or _default_norm_url
    n0 = len(findings)

    by_url: dict[str, Finding] = {}
    order: list[str] = []
    for f in findings:
        k = nu(f.url)
        if k not in by_url:
            by_url[k] = f
            order.append(k)
        elif _strength(f) > _strength(by_url[k]):
            by_url[k] = f
    merged = [by_url[k] for k in order]
    n_url = n0 - len(merged)

    if strip_scaffold:
        merged = [replace(f, why=_strip_scaffold(f.why)) for f in merged]

    per_target: dict[str, int] = {}
    capped: list[Finding] = []
    n_cap = 0
    for f in merged:
        t = (f.patch_target or "").strip().lower()
        if max_per_target and per_target.get(t, 0) >= max_per_target:
            n_cap += 1
            continue
        per_target[t] = per_target.get(t, 0) + 1
        capped.append(f)

    return capped, {"url_merged": n_url, "capped": n_cap, "kept": len(capped)}


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
