"""Typed evidence items derived from grounded research findings."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from dataclasses import replace
from typing import Any, Literal
from urllib.parse import urlsplit


EvidenceStatus = Literal[
    "verified",
    "partially_supported",
    "disputed",
    "outdated",
    "unverified",
    "weak",
]


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    claim: str
    source: str
    source_type: str
    url: str
    date: str
    reliability: str
    relevance: str
    status: EvidenceStatus
    used_in: str
    quote: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def source_type_for_url(url: str) -> str:
    host = _host(url)
    path = (url or "").lower()
    if "arxiv.org" in host or "doi.org" in host or "pubmed.ncbi.nlm.nih.gov" in host:
        return "preprint"
    if "github.com" in host:
        return "repository"
    if host.endswith(".gov") or any(x in host for x in ("nist.gov", "iso.org", "w3.org", "ietf.org")):
        return "standard"
    if any(x in host for x in ("docs.", "developer.", "devdocs.", "learn.microsoft.com")) or "/docs" in path:
        return "official_docs"
    if ".edu" in host:
        return "academic"
    if any(x in host for x in ("medium.com", "substack.com", "blog.", "wordpress.com")):
        return "blog"
    if any(x in host for x in ("reddit.com", "news.ycombinator.com", "twitter.com", "x.com")):
        return "forum_social"
    return "web"


def _host(url: str) -> str:
    try:
        return urlsplit(url or "").netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def _claim_key(claim: str) -> str:
    words = re.findall(r"[a-z0-9]+", (claim or "").lower())
    return " ".join(w for w in words if len(w) > 2)[:160]


_PRIMARY_SOURCE_TYPES = {"official_docs", "academic", "preprint", "standard", "repository"}
_MAJOR_SECTION_RE = re.compile(r"(?i)(finding|analysis|recommendation|conclusion|decision|risk|summary)")


def _reliability_for(source_type: str, quote_verified: bool, grounded: bool) -> str:
    if quote_verified:
        return "verified quote"
    if source_type in _PRIMARY_SOURCE_TYPES and grounded:
        return "primary source"
    if grounded:
        return "cached source"
    return "unverified"


def apply_corroboration(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Downgrade weak single-source major claims unless corroborated."""
    hosts_by_claim: dict[str, set[str]] = {}
    for item in items:
        key = _claim_key(item.claim)
        if not key:
            continue
        hosts_by_claim.setdefault(key, set()).add(_host(item.url))

    out: list[EvidenceItem] = []
    for item in items:
        key = _claim_key(item.claim)
        major = bool(_MAJOR_SECTION_RE.search(item.used_in or ""))
        primary = item.source_type in _PRIMARY_SOURCE_TYPES or item.reliability == "verified quote"
        corroborated = len(hosts_by_claim.get(key, set())) >= 2
        if major and not primary and not corroborated:
            out.append(replace(
                item,
                status="weak",
                notes="single non-primary source; corroborate with an independent source",
            ))
        elif corroborated and not item.notes:
            out.append(replace(item, notes="corroborated by independent source"))
        else:
            out.append(item)
    return out


def evidence_from_findings(findings: list, final_text: str = "") -> list[EvidenceItem]:
    """Convert grounded findings to report evidence rows.

    Keeps only URLs that appear in the final artifact when `final_text` is passed,
    so dropped worker findings do not show as used evidence.
    """
    cited = set(re.findall(r"https?://\S+", final_text or ""))
    cited = {u.rstrip(".,);]") for u in cited}
    rows: list[EvidenceItem] = []
    seen: set[str] = set()
    for finding in findings or []:
        url = str(getattr(finding, "url", "") or "").rstrip(".,);]")
        if not url or (cited and url not in cited):
            continue
        quote = str(getattr(finding, "quote", "") or "")
        claim = str(getattr(finding, "why", "") or quote or getattr(finding, "title", "") or url)
        used_in = str(getattr(finding, "patch_target", "") or "")
        key = f"{url}\n{used_in}\n{claim}"
        if key in seen:
            continue
        seen.add(key)
        grounded = bool(getattr(finding, "grounded", False))
        quote_verified = bool(getattr(finding, "quote_verified", False))
        source_type = source_type_for_url(url)
        status: EvidenceStatus = "verified" if quote_verified else ("partially_supported" if grounded else "unverified")
        rows.append(EvidenceItem(
            id="ev_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10],
            claim=claim,
            source=str(getattr(finding, "title", "") or url),
            source_type=source_type,
            url=url,
            date="",
            reliability=_reliability_for(source_type, quote_verified, grounded),
            relevance=str(getattr(finding, "popularity", "") or ""),
            status=status,
            used_in=used_in,
            quote=quote,
            notes="",
        ))
    return apply_corroboration(rows)


def render_evidence_matrix(items: list[EvidenceItem]) -> str:
    if not items:
        return ""
    lines = [
        "| Claim | Source | Status | Used in | Caveats |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in items:
        source = f"[{item.source}]({item.url})" if item.url else item.source
        caveats = item.notes or item.reliability
        lines.append(f"| {item.claim} | {source} | {item.status} | {item.used_in} | {caveats} |")
    return "\n".join(lines)
