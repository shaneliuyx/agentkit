"""studio.loops — Forward Future loop-library catalog integration (M7 Wave 1).

The catalog (https://signals.forwardfuture.ai/loop-library/catalog.json,
schemaVersion 2) is a library of 61 published agent workflows ("loops"). Studio
can match a requirement against it and pre-seed a run from a chosen loop instead
of cold decomposition.

REAL catalog schema (verified against the live catalog.json):
  top-level: schemaVersion, name, updated, loopCount, categories[], loops[]
  per-loop:  number, slug, title, url, category{slug,label}, author, published,
             modified, description, useWhen, prompt, verification{title,detail},
             steps[] (flat list[str]), why, implementationNote, keywords[],
             related[]

There is NO id/summary/trigger field on a loop, so we map for the event schema:
  id ← slug, summary ← description, trigger ← useWhen, + keywords.

The catalog is untrusted reference data (its own ``usage.authorization`` note
says so): a matched loop SEEDS decomposition; it is never executed as authority.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATALOG_URL = "https://signals.forwardfuture.ai/loop-library/catalog.json"

#: Disk cache location + TTL (the catalog updates ~daily; a 24h TTL is plenty).
_CACHE_DIR = Path.home() / ".cache" / "agentkit-studio"
_CACHE_PATH = _CACHE_DIR / "loop_catalog.json"
_CACHE_TTL_S = 24 * 3600

#: Tokens dropped from keyword/relevance matching (too common to discriminate).
_STOP = frozenset(
    "the a an is are to of in on for and or with this that build make use using "
    "do does run loop agent workflow when how into from your you it".split()
)


@dataclass(frozen=True)
class LoopMatch:
    """A catalog loop matched to a requirement (mapped to the event schema)."""

    id: str  # ← slug
    title: str
    summary: str  # ← description
    url: str
    trigger: str  # ← useWhen
    keywords: tuple[str, ...]
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "trigger": self.trigger,
            "keywords": list(self.keywords),
            "score": round(self.score, 4),
        }


def _tokens(text: str) -> set[str]:
    """Lowercase content tokens (stopwords dropped) for keyword overlap."""
    raw = "".join(c if c.isalnum() else " " for c in text.lower()).split()
    return {t for t in raw if t and t not in _STOP and len(t) > 2}


class CatalogClient:
    """Loop-library catalog client: fetch + disk-cache + match + adapt.

    Construct with an explicit ``data`` dict (tests pass the bundled fixture so
    no network is touched) or let ``load()`` fetch + cache. ``fetcher`` is
    injectable for offline tests.
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] | None = data

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(
        cls,
        *,
        url: str = CATALOG_URL,
        cache_path: Path = _CACHE_PATH,
        ttl_s: float = _CACHE_TTL_S,
        fetcher: Any = None,
    ) -> "CatalogClient":
        """Load the catalog: fresh disk cache → network fetch → stale cache.

        Never raises on a network failure — a stale cache (any age) or, failing
        that, an empty catalog is returned so the panel degrades gracefully.
        ``fetcher(url) -> str`` is injectable; default uses urllib.
        """
        # 1. fresh cache
        cached = _read_cache(cache_path, ttl_s)
        if cached is not None:
            return cls(cached)

        # 2. network fetch
        fetch = fetcher or _http_fetch
        try:
            raw = fetch(url)
            data = json.loads(raw)
            _write_cache(cache_path, data)
            return cls(data)
        except Exception:  # noqa: BLE001 - degrade to stale cache or empty
            stale = _read_cache(cache_path, ttl_s=float("inf"))
            return cls(stale if stale is not None else {"loops": []})

    # -- access ------------------------------------------------------------

    @property
    def loops(self) -> list[dict[str, Any]]:
        return list((self._data or {}).get("loops", []))

    def get(self, loop_id: str) -> dict[str, Any] | None:
        """Look up a loop by its id (``slug``) or ``number``."""
        for loop in self.loops:
            if loop.get("slug") == loop_id or loop.get("number") == loop_id:
                return loop
        return None

    # -- matching ----------------------------------------------------------

    def find(self, requirement: str, *, limit: int = 3) -> list[LoopMatch]:
        """Match ``requirement`` against the catalog (keyword/relevance overlap).

        Scores each loop by token overlap of the requirement against the loop's
        title + description + useWhen + keywords, normalized by requirement size.
        Returns the top ``limit`` non-zero matches (catalog's recommendationLimit
        default is 3). Deterministic, model-free.
        """
        req = _tokens(requirement)
        if not req:
            return []
        scored: list[LoopMatch] = []
        for loop in self.loops:
            hay = _tokens(
                f"{loop.get('title','')} {loop.get('description','')} "
                f"{loop.get('useWhen','')} {' '.join(loop.get('keywords', []))}"
            )
            overlap = req & hay
            if not overlap:
                continue
            score = len(overlap) / len(req)
            scored.append(_to_match(loop, score))
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:limit]

    # -- adaptation --------------------------------------------------------

    def adapt(self, loop: dict[str, Any]) -> list[dict[str, Any]]:
        """Turn a loop's flat ``steps`` list into seed step dicts (a linear DAG).

        Mirrors ``planner._linear_steps``: ``sN`` depends on ``s(N-1)``. The
        loop's category slug becomes a ``role`` hint. Returns ``[]`` when the
        loop has no steps (caller falls back to cold decomposition).
        """
        steps = loop.get("steps") or []
        role = (loop.get("category") or {}).get("slug")
        out: list[dict[str, Any]] = []
        for i, desc in enumerate(steps, 1):
            out.append(
                {
                    "id": f"s{i}",
                    "description": str(desc).strip(),
                    "depends_on": [f"s{i - 1}"] if i > 1 else [],
                    "role": role,
                }
            )
        return out


def make_seeded_decomposer(seed_steps: list[dict[str, Any]]):
    """Build a ``planner.plan`` decomposer that returns fixed seed steps.

    Injected as ``planner.plan(task, decomposer=make_seeded_decomposer(...))`` so
    a run is pre-seeded from a published loop. The task argument is ignored — the
    steps are the loop's adapted steps. Falls back to a single-step plan when
    ``seed_steps`` is empty so ``plan`` always yields a valid DAG.
    """

    def _decompose(task: str) -> list[dict[str, Any]]:
        if not seed_steps:
            return [{"id": "s1", "description": task.strip(), "depends_on": []}]
        return [dict(s) for s in seed_steps]  # copy: never hand out shared dicts

    return _decompose


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _to_match(loop: dict[str, Any], score: float) -> LoopMatch:
    return LoopMatch(
        id=loop.get("slug", ""),
        title=loop.get("title", ""),
        summary=loop.get("description", ""),
        url=loop.get("url", ""),
        trigger=loop.get("useWhen", ""),
        keywords=tuple(loop.get("keywords", [])),
        score=score,
    )


def _http_fetch(url: str) -> str:
    # A real User-Agent is REQUIRED: the catalog CDN's WAF 403s the default
    # ``Python-urllib/x.y`` UA, which silently degraded /loops to 0 matches.
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "AgentKit-Studio/1.0 (loop-library catalog client)",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed catalog URL
        return resp.read().decode("utf-8")


def _read_cache(path: Path, ttl_s: float) -> dict[str, Any] | None:
    """Return cached catalog if present and within ``ttl_s``, else None."""
    try:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > ttl_s:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _write_cache(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:  # noqa: BLE001 - a cache write failure is non-fatal
        pass
