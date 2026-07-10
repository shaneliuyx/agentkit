"""Guarded expansion of underdeveloped sections (PLAN §9 step 5 / P5).

The depth problem: 400KB of fetched evidence collapses to a ~20KB report and the
hill-climb never expands it, because the reducer's per-section cap and
already-cited-URL drop starve depth. This stage adds depth ONLY from grounded,
under-used evidence: for each evidence source cited <2x, synthesize <=1 paragraph
(which MUST carry that source's URL) into an underdeveloped section.

Guards (any failure → return the input UNCHANGED — no-op is a successful terminal
state, PLAN §9): a paragraph without its URL is dropped; the whole expansion is
rejected if it de-cites, regresses ``rubric_score``, or lowers the prose-words-
per-URL ratio (anti-citation-wall — depth must be prose, not a link dump).

The URL-based "under-used source" heuristic is conservative: a source cited many
times can still have unused passages it cannot see (that intra-source depth is out
of scope for v1). It fires cleanly on genuinely thin reports and no-ops on
well-developed ones, which is the safe direction.
"""
from __future__ import annotations

import re
from collections.abc import Callable

# S1: moved to studio.textutil.extract_urls (aliased so both call sites below —
# _source_primary_url's "first URL" and body_urls' occurrence-COUNTING — keep
# their un-deduplicated list, unchanged).
from studio.guards import urls_preserved as _urls_preserved
from studio.textutil import extract_urls as _urls


def _words(text: str) -> int:
    return len((text or "").split())


def _sections(text: str) -> list[tuple[str, int]]:
    """(heading, word_count) for each ## section."""
    heads = [(m.group(1).strip(), m.start()) for m in re.finditer(r"^##\s+(.+)$", text, re.M)]
    out = []
    for i, (h, pos) in enumerate(heads):
        end = heads[i + 1][1] if i + 1 < len(heads) else len(text)
        out.append((h, _words(text[pos:end])))
    return out


def _prose_per_url(text: str) -> float:
    url_lines = sum(1 for ln in text.splitlines() if "http://" in ln or "https://" in ln)
    return _words(text) / max(1, url_lines)


def _source_primary_url(output: str) -> str | None:
    us = _urls(output)
    return us[0] if us else None


def expand_underdeveloped_sections(
    *,
    text: str,
    requirement: str,
    evidence_outputs: dict[str, str],
    verified_urls: list[str] | None,
    required_sections: list[str] | None,
    chat: Callable[[str], str],
    rubric_score: Callable[..., float],
    word_floor: int = 150,
    max_sources: int = 6,
) -> tuple[str, dict]:
    """Return ``(expanded_or_unchanged_text, stats)``. Dependency-injected so it
    is testable without a live model or the runner (pass any ``chat``)."""
    from agentkit.artifacts.patcher import DocPatch, reduce_patches  # noqa: PLC0415

    stats = {"underdeveloped": [], "unused_sources": [], "added": 0, "rejected_reason": None}
    secs = _sections(text)
    # References is a link list, never prose-expand it.
    under = [h for h, w in secs if w < word_floor and "reference" not in h.lower()]
    stats["underdeveloped"] = under
    if not under:
        return text, stats  # well-developed → no-op

    body_urls = _urls(text)
    unused = {}
    for sid, out in evidence_outputs.items():
        url = _source_primary_url(out)
        if not url:
            continue
        if sum(1 for u in body_urls if u == url or url in u or u in url) < 2:
            unused[sid] = (url, out)
    stats["unused_sources"] = list(unused)
    if not unused:
        return text, stats  # no under-used evidence to draw on → no-op

    target = under[0]
    doc = text
    ppu_before = _prose_per_url(text)
    for sid, (url, out) in list(unused.items())[:max_sources]:
        prompt = (
            f"From the SOURCE below, write ONE paragraph (3-5 sentences) of grounded "
            f"analysis that adds depth to the '{target}' section of a research report "
            f"answering: {requirement[:300]}\n"
            f"The paragraph MUST end with the source URL in parentheses. No fabrication.\n"
            f"SOURCE URL: {url}\n=== SOURCE ===\n{out[:6000]}\n=== END ==="
        )
        try:
            para = (chat(prompt) or "").strip()
        except Exception:  # noqa: BLE001 — a bad synthesis call is skipped, never fatal
            continue
        # Guard (codex P1-c): the paragraph must carry THIS source's exact URL,
        # and must NOT introduce any URL absent from this source's evidence block
        # — otherwise a fabricated/unrelated URL rides into the report.
        if not para or url not in para:
            continue
        if any(pu not in out for pu in _urls(para)):
            continue
        anchor_m = re.search(rf"^##\s+{re.escape(target)}.*$", doc, re.M)
        if not anchor_m:
            continue
        heading = anchor_m.group(0)
        res = reduce_patches(doc, [[DocPatch(op="insert_after", anchor=heading, content=f"\n\n{para}", source="expand")]])
        if res.text != doc:
            doc = res.text
            stats["added"] += 1

    if stats["added"] == 0:
        return text, stats

    # Whole-expansion guards — any failure reverts to the input (no-op success).
    if not _urls_preserved(text, doc):  # lost-only: expansion may add source URLs
        stats["rejected_reason"] = "de-cite"
        return text, stats
    if _prose_per_url(doc) < ppu_before * 0.95:
        stats["rejected_reason"] = "citation-wall"
        return text, stats
    before = rubric_score(text, verified_urls=verified_urls or None, required_sections=required_sections)
    after = rubric_score(doc, verified_urls=verified_urls or None, required_sections=required_sections)
    if after < before - 1e-3:
        stats["rejected_reason"] = "score-regress"
        return text, stats
    return doc, stats
