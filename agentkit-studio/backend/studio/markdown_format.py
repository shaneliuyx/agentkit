"""Cosmetic markdown normalization for the final served artifact only.

Runs post-run, AFTER all scoring/gating (rubric_score / epoch_gate /
relevance_issues) — the scorers must always see the raw generated text, never a
reformatted proxy. Fails open: any formatter error returns the original text
unchanged so a malformed report can never block a run from completing.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def beautify_markdown(text: str) -> str:
    """Normalize spacing/list markers/table alignment via mdformat (GFM).

    Fail-open: returns ``text`` unchanged on any exception.
    """
    if not text:
        return text
    try:
        import mdformat

        return mdformat.text(text, extensions={"gfm"})
    except Exception:  # noqa: BLE001 — cosmetic pass must never break a run
        log.warning("beautify_markdown failed; serving unformatted text", exc_info=True)
        return text
