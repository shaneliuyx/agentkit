"""Section-level helpers for non-additive restructuring.

``accept_rewrite`` relaxes the writeback ratchet from whole-document grow-only to
PER-SECTION grow-only, so a reviser may REPLACE one section (repair, ranking table, dedup)
even when net length shrinks — while still guaranteeing no sourced section is deleted."""
from __future__ import annotations

import re


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into top-level ('##') sections.

    Returns ordered ``(heading, body)`` pairs; ``body`` INCLUDES the heading line and any
    nested '###' subsections beneath it. Content before the first '##' (title + intro) is the
    first pair, keyed '(intro)'. Deterministic, 0 LLM."""
    out: list[tuple[str, str]] = []
    head: str | None = None
    buf: list[str] = []
    for line in text.splitlines(keepends=True):
        if re.match(r"^##\s", line):
            if head is not None or buf:
                out.append((head or "(intro)", "".join(buf)))
            head = line.strip()
            buf = [line]
        else:
            buf.append(line)
    if head is not None or buf:
        out.append((head or "(intro)", "".join(buf)))
    return out


def _section_content(body: str) -> str:
    """The section body MINUS its heading line (the actual content, for emptiness checks)."""
    parts = body.split("\n", 1)
    return (parts[1] if len(parts) > 1 else "").strip()


def accept_rewrite(old: str, new: str) -> bool:
    """True iff ``new`` is a safe replacement for ``old``: no '##' section that had CONTENT in
    ``old`` is deleted or emptied in ``new`` (section identity = heading text).

    Permits replace / repair / dedup even when net length shrinks (the per-section ratchet),
    while preserving the anti-regression guarantee at section granularity — a section may be
    rewritten shorter, but not gutted to a bare heading or removed. Content is checked beyond
    the heading line so emptying a section is caught even though the heading remains. Falls back
    to a whole-document length check when ``old``'s headings are not unique (identity
    ambiguous). Never accepts blank output."""
    if not new.strip():
        return False
    old_pairs = split_sections(old)
    headings = [h for h, _ in old_pairs]
    if len(set(headings)) != len(headings):  # ambiguous identity → conservative length check
        return len(new) >= len(old)
    new_secs = {h: _section_content(b) for h, b in split_sections(new)}
    for h, b in old_pairs:
        if _section_content(b) and not new_secs.get(h):
            return False  # a section that had content vanished or was gutted to its heading
    return True
