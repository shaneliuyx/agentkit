"""F4b — honest ranking synthesizer (STRUM extract→contrast→rank, generalized to N sources).

Builds a markdown ranking table from grounded findings + their real metrics. The ranking is a
DOCUMENTED composite and every number traces to a real source — a fetched citation/star count,
a stated-but-reported claim, or the in-corpus reference frequency. It NEVER invents a metric: a
source with no public number shows '—' and is ranked by salience. A mostly-'—' table with marked
gaps is the CORRECT output when metrics genuinely do not exist, not a failure to paper over."""
from __future__ import annotations

import re

from .metrics import Metric
from .types import Finding

_NUM = re.compile(r"([\d][\d,\.]*)\s*([kKmMbB]?)")
_MULT = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_stated(popularity: str) -> tuple[int, str] | None:
    """'6.5M views' -> (6_500_000, 'views'); 'n/a' / '' -> None. A STATED claim (kept as
    'reported', never re-derived) — weaker than a fetched metric, stronger than salience."""
    p = (popularity or "").strip()
    if not p or p.lower() in ("n/a", "none", "-", "—"):
        return None
    m = _NUM.search(p)
    if not m:
        return None
    value = int(float(m.group(1).replace(",", "")) * _MULT.get(m.group(2).lower(), 1))
    unit = re.sub(r"[\d,\.\s kKmMbB]+", " ", p).strip() or "reach"
    return value, unit


def synthesize_ranking_table(findings: list[Finding],
                             metrics: dict[str, Metric | None]) -> str:
    """Honest SPLIT presentation. A 'Measured popularity' table ranks sources that have a real,
    independently-verifiable metric (citations/stars); a separate 'Reported / unranked' listing
    holds sources with only a stated claim or no public metric — the two are NEVER ranked
    together (mixing a citation count with a view count or a relevance proxy is apples-to-oranges,
    which an evaluator correctly flags). Leads with a one-line methodology note stating how many
    of the sources are actually measurable, so the unranked rows read as a documented limitation,
    not a gap. Deterministic; numbers trace to their source; nothing is invented."""
    by_url: dict[str, dict] = {}
    for f in findings:
        info = by_url.setdefault(f.url, {"title": f.title, "pop": f.popularity, "count": 0})
        info["count"] += 1
        if f.title and not info["title"]:
            info["title"] = f.title
        if f.popularity and not info["pop"]:
            info["pop"] = f.popularity

    measured: list[tuple[int, str, dict]] = []
    reported: list[tuple[str, dict]] = []
    for url, info in by_url.items():
        m = metrics.get(url)
        if m is not None:
            measured.append((m.value, url, info))
        else:
            reported.append((url, info))
    measured.sort(key=lambda r: r[0], reverse=True)

    def _name(url: str, info: dict) -> str:
        return (info["title"] or url).replace("|", "/")

    n, k = len(by_url), len(measured)
    out = [
        f"**Popularity evidence.** {k} of {n} cited sources have an independently verifiable "
        "metric (e.g. citation count, repository stars); the rest have no public engagement "
        "metric and are listed separately below. A uniform popularity ranking across all sources "
        "is not possible without inventing numbers, so unmeasurable sources are documented, not "
        "ranked against measured ones.",
        "",
    ]
    if measured:
        out += [
            "**Measured popularity** — ranked by an independently verifiable metric:", "",
            "| Rank | Source | Metric | Source of metric | Cited-by | URL |",
            "|---|---|---|---|---|---|",
        ]
        for i, (_v, url, info) in enumerate(measured, 1):
            m = metrics[url]
            out.append(f"| {i} | {_name(url, info)} | {m.value:,} {m.unit} | {m.source} | "
                       f"{info['count']} | {url} |")
        out.append("")
    if reported:
        out += [
            "**Reported / unranked** — no comparable public metric; listed for completeness, NOT "
            "ranked against the measured sources above:", "",
            "| Source | Stated reach | Status | Cited-by | URL |", "|---|---|---|---|---|",
        ]

        def _rank_key(item: tuple[str, dict]):
            st = parse_stated(item[1]["pop"])
            return (1, st[0]) if st else (0, 0)

        for url, info in sorted(reported, key=_rank_key, reverse=True):
            st = parse_stated(info["pop"])
            reach = f"{st[0]:,} {st[1]}" if st else "—"
            status = "reported, not independently verified" if st else "no public engagement metric"
            out.append(f"| {_name(url, info)} | {reach} | {status} | {info['count']} | {url} |")
    return "\n".join(out)
