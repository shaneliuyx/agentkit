"""F4a — real source-metric acquisition for the ranking synthesizer.

Fetches the REAL popularity signal a source type actually has — academic papers → Semantic
Scholar citation count, GitHub repos → stars — and NEVER invents one (a blog with no public
metric returns None and is ranked by an in-corpus salience proxy downstream). Rate-limit-safe:
the arxiv lookups go through ONE S2 *batch* request, results are cached by the caller, the HTTP
client retries with backoff on 429, and ANY failure degrades to None — a metric is best-effort
enrichment, never a hard dependency that can block or crash a run."""
from __future__ import annotations

import re
import time
from collections import namedtuple
from typing import Any, Callable

Metric = namedtuple("Metric", "value unit source")

_ARXIV = re.compile(r"arxiv\.org/(?:abs|html|pdf)/(\d{4}\.\d{4,5})", re.I)
_GITHUB = re.compile(r"github\.com/([\w.-]+/[\w.-]+?)(?:[/#?]|$)", re.I)

#: http(method, url, json=..., headers=...) -> parsed JSON (dict/list) or None.
Http = Callable[..., Any]


def source_kind(url: str) -> tuple[str | None, str]:
    """Classify a source URL → ('arxiv', paper_id) | ('github', 'owner/repo') | (None, '')."""
    m = _ARXIV.search(url or "")
    if m:
        return ("arxiv", m.group(1))
    m = _GITHUB.search(url or "")
    if m:
        return ("github", m.group(1).rstrip("/"))
    return (None, "")


def _default_http(method: str, url: str, json: Any = None,
                  headers: dict | None = None, timeout: int = 10) -> Any:
    import requests  # local import so agentkit.artifacts imports without requests
    last = None
    for attempt in range(3):
        try:
            r = requests.request(method, url, json=json, headers=headers or {}, timeout=timeout)
            if r.status_code == 429:  # rate-limited → back off and retry
                time.sleep(2 ** attempt)
                last = None
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001 — caller treats any failure as a miss
            last = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    if last:
        raise last
    return None


def fetch_metrics(
    urls: list[str],
    *,
    s2_key: str | None = None,
    cache: dict | None = None,
    http: Http | None = None,
) -> dict[str, Metric | None]:
    """Best-effort real metric per URL. arxiv → S2 citationCount (ONE batch request for all
    papers); github → stargazers_count; anything else → None. Cache-first (``cache`` dict, key
    ``metric:<url>`` storing the ``Metric`` tuple or None); on any network/parse failure the
    source degrades to None. Never raises."""
    cache = cache if cache is not None else {}
    http = http or _default_http
    out: dict[str, Metric | None] = {}
    arxiv: dict[str, str] = {}   # paper_id -> url
    github: list[tuple[str, str]] = []

    def _store(u: str, m: Metric | None) -> None:
        out[u] = m
        cache[f"metric:{u}"] = tuple(m) if m else None

    for u in urls:
        ck = f"metric:{u}"
        if ck in cache:
            v = cache[ck]
            out[u] = Metric(*v) if v else None
            continue
        kind, ident = source_kind(u)
        if kind == "arxiv":
            arxiv[ident] = u
        elif kind == "github":
            github.append((ident, u))
        else:
            _store(u, None)

    if arxiv:
        try:
            hdr = {"x-api-key": s2_key} if s2_key else {}
            data = http(
                "POST",
                "https://api.semanticscholar.org/graph/v1/paper/batch?fields=citationCount",
                json={"ids": [f"ARXIV:{i}" for i in arxiv]},
                headers=hdr,
            )
            ids = list(arxiv)
            recs = data if isinstance(data, list) else []
            for i, rec in zip(ids, recs):
                cc = rec.get("citationCount") if isinstance(rec, dict) else None
                _store(arxiv[i], Metric(int(cc), "citations", "semantic-scholar")
                       if cc is not None else None)
            for i in ids:  # any id S2 didn't return → degrade
                out.setdefault(arxiv[i], None)
        except Exception:  # noqa: BLE001 — S2 down/limited → all arxiv degrade
            for u in arxiv.values():
                _store(u, None)

    for repo, u in github:
        try:
            d = http("GET", f"https://api.github.com/repos/{repo}",
                     headers={"Accept": "application/vnd.github+json"})
            st = d.get("stargazers_count") if isinstance(d, dict) else None
            _store(u, Metric(int(st), "stars", "github") if st is not None else None)
        except Exception:  # noqa: BLE001
            _store(u, None)

    return out
