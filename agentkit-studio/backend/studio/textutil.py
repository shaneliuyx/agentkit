r"""studio.textutil — single home for text primitives (PLAN-codebase-simplification.md S1).

PLAN §1: 31 regex/normalize variants for URLs alone were scattered across 10 files
(``_urls`` vs ``_norm_urls`` vs ``_normalize_url``), plus 3 independent copies of
``_dbg`` and 2 diverging mermaid-block regexes — "a fix must land in N places" already
bit twice live (a synthesis guard rejected a valid rewrite because its own URL-set copy
didn't normalize the same way). This module is the one place those primitives live;
everywhere else imports (or thinly re-exports/aliases, where a caller's existing
private name must keep working — see each origin module for its alias comment).

Mostly pure move-and-import, with ONE deliberate exception: ``extract_urls``/
``norm_urls`` unify onto the broad ``URL_RE`` (``\S+``) + trailing-rstrip instead
of the narrower per-copy character-class regexes (e.g. ``[^\s)>\]\"'`]+``) that
``expand_sections._urls`` and ``artifact_text._urls_in_order`` used. Those narrow
classes stopped matching at the FIRST excluded char anywhere in the URL, not just
a trailing one — ``https://example.com/search?q=(pi)&page=2`` truncated to
``...q=(pi`` and silently dropped ``&page=2``. The new broad-match+rstrip version
captures the URL whole and is strictly more correct; confirmed no caller relies on
the old truncation. This is a deliberate WIDENING of capture, not a byte-for-byte
behavior-preserving relocation — see ``test_extract_urls_keeps_url_with_internal_bracket``.

Everything else is a verbatim move: no behavior changes, no threshold changes.
Where two existing copies had genuinely different semantics (not just cosmetic
regex-class differences), they were NOT merged — see ``MERMAID_OPEN_RE`` below
and the S1 report for the ones left alone entirely (``task_runs._normalize_url``,
scheme-stripping dedup keys).
"""
from __future__ import annotations

import os
import re


def dbg(msg: str) -> None:
    """Append a line to the file named by ``OMC_THROUGHPUT_DEBUG``; no-op unless set.

    Moved from ``studio.runner._dbg`` (previously copy-pasted into ``artifact_text.py``
    and ``structural_producer.py`` to dodge a circular import — this leaf module has no
    such risk, so both now alias here instead)."""
    path = os.environ.get("OMC_THROUGHPUT_DEBUG")
    if not path:
        return
    try:
        with open(path, "a") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

URL_RE = re.compile(r"https?://\S+")

#: Superset of every trailing-punctuation charset previously rstripped ad hoc per
#: copy. Backtick added for markdown-code-wrapped URLs, which one variant's
#: character class excluded but its rstrip charset did not cover.
_TRAILING_PUNCT = ".,;:)]>\"'`"


def norm_url(u: str) -> str:
    """Strip trailing wrapper/punctuation characters from a single matched URL."""
    return (u or "").rstrip(_TRAILING_PUNCT)


def extract_urls(text: str) -> list[str]:
    """URLs in *text*, normalized, in first-appearance order — NOT deduplicated.

    Some callers count citation occurrences (e.g. "cited fewer than 2 times"), so
    duplicates must survive; callers that want a unique set use ``norm_urls`` below,
    and callers that want a unique ORDERED list can wrap this in ``dict.fromkeys``.
    """
    return [norm_url(u) for u in URL_RE.findall(text or "")]


def norm_urls(text: str) -> set[str]:
    """Deduplicated URL set — for before/after citation-preservation comparisons.

    Moved verbatim from ``artifact_text._norm_urls``."""
    return set(extract_urls(text))


# ---------------------------------------------------------------------------
# Content words
# ---------------------------------------------------------------------------

_CONTENT_WORD_RE = re.compile(r"[a-z]{4,}")
_MIN_CONTENT_WORDS = 8


def content_word_stems(text: str) -> set[str]:
    """Content-word STEMS of *text* (moved from ``findings._req_content_words``).

    Empty set = too thin to judge (fewer than ``_MIN_CONTENT_WORDS`` distinct stems)."""
    words = {w.rstrip("s") for w in _CONTENT_WORD_RE.findall((text or "").lower())}
    return words if len(words) >= _MIN_CONTENT_WORDS else set()


# ---------------------------------------------------------------------------
# Fenced code / mermaid
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```")


def mask_fenced_code(text: str) -> str:
    """Blank out fenced code regions, preserving line count (PLAN N4).

    Moved verbatim from ``studio.rubric.mask_fenced_code``; heading scans
    (``^#{1,6}``) otherwise count a python comment like ``# --- MOCK ---`` inside a
    ```` ```python ```` block as an H1, polluting the structure signal,
    ``sections_present``, and gap detection. Replacing each fenced line (fences
    included) with an empty line keeps every real heading's line position intact
    while hiding in-code ``#`` lines. An unterminated fence masks to end-of-text (a
    truncated block has no real headings anyway).
    """
    if "```" not in (text or ""):
        return text or ""
    out: list[str] = []
    in_fence = False
    for ln in (text or "").split("\n"):
        if _FENCE_RE.match(ln):
            in_fence = not in_fence
            out.append("")          # the fence line itself is not a heading
            continue
        out.append("" if in_fence else ln)
    return "\n".join(out)


#: Full fenced mermaid block (open fence through close fence) — for EXTRACTING or
#: REPLACING the block body. Moved from ``artifact_text._MERMAID_BLOCK_RE``.
MERMAID_BLOCK_RE = re.compile(r"```mermaid\b.*?```", re.DOTALL)

#: Opening fence ONLY — for a cheap PRESENCE check. Moved from
#: ``requirement_compliance._MERMAID_BLOCK_RE``. Deliberately NOT unified with
#: ``MERMAID_BLOCK_RE`` above: on a truncated/unclosed mermaid fence the DOTALL
#: pattern finds no match (no closing ```` ``` ````) while this one still does,
#: which is the presence check's correct answer. PLAN's "keep semantics identical
#: per call site" — same name, two regexes.
MERMAID_OPEN_RE = re.compile(r"```mermaid\b")


def has_code_fence(text: str) -> bool:
    """True when *text* contains a fenced block that is NOT mermaid.

    Moved verbatim from ``requirement_compliance._has_code_fence``. Fence lines
    alternate opener/closer; an opener whose info-string is not ``mermaid`` counts,
    labeled or not — so an unlabeled real code block passes and a mermaid diagram
    alone does not."""
    fences = re.findall(r"(?m)^\s*```([^\n`]*)$", text or "")
    return any(
        not info.strip().lower().startswith("mermaid")
        for i, info in enumerate(fences)
        if i % 2 == 0
    )


#: A fence line ``optional-indent + ``` + trailing content`` — matches both an
#: opener (``` ```python``` ``) and a closer. Shared by the lint check
#: (studio.artifact_lint) and the deterministic repair (studio.artifact_text) so
#: the two can never drift on what counts as contamination.
FENCE_LINE_RE = re.compile(r"^(?P<indent>[ \t]*)```(?P<rest>[^\n]*)$", re.MULTILINE)


def fence_rest_contaminated(rest: str, *, is_opening: bool) -> bool:
    """True when a fence line's trailing content (after the ``` marker) is invalid.

    A CLOSING fence must carry nothing after the marker — any non-empty trailing
    content (observed shape: a citation URL glued on by the model, e.g. ` ``` https:
    //example.com`) is contamination. An OPENING fence's trailing content is
    normally a legal language tag (```` ```python ````), so it's only contamination
    when it contains a URL scheme (a language name never does)."""
    rest = rest.strip()
    if not is_opening:
        return bool(rest)
    return "://" in rest
