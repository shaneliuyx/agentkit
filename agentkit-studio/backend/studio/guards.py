"""studio.guards — the four shared accept/reject primitives (PLAN S3).

Six inline copies of "did this transform drop a citation?", five of "is the
rewrite too short?", two of "did it invent a heading?", and one topical-URL
oracle already lived scattered across artifact_text / findings /
structural_producer / expand_sections / research_first / runner — each a near-copy
that DISAGREED with its siblings on normalization or direction (a fix had to land
in N places; a synthesis guard once rejected a valid rewrite because its own
URL-set copy did not normalize the same way as the reducer's). This module is the
one home. Every primitive is pure decision logic that COMPOSES the studio.textutil
S1 primitives (``norm_urls`` / ``mask_fenced_code`` / ``content_word_stems``)
instead of re-deriving URL/heading normalization, and none can raise into a run —
guards fail toward KEEPING the prior text.

Behavior-preserving relocation (S3 hard rule): where two existing copies genuinely
disagreed, the primitive is PARAMETERIZED and each migrated call site passes the
args that reproduce its OWN prior behavior verbatim. The defaults below encode the
recommended semantics for a NEW caller (lost-only URL preservation; H1-6 +
code-masked heading detection), but no existing site's behavior changes.

Deliberately NOT folded in (documented at their call sites):
  * ``runner._publish_revision_regressed`` count-based URL check — narrow regex +
    ``len()``-compare; unifying it onto ``norm_urls`` would flip its verdict on a
    URL-SWAP revision (drop A, add B → equal count today, lost-A under sets).
  * ``task_runs._normalize_url`` heavy canonicalization — a membership/dedup gate,
    a different concept from before/after preservation.
"""
from __future__ import annotations

import hashlib as _hashlib
import re as _re

from studio.textutil import dbg as _dbg
from studio.textutil import mask_fenced_code as _mask_fenced_code
from studio.textutil import norm_urls as _norm_urls


# ---------------------------------------------------------------------------
# 1. urls_preserved — anti-de-citation guard
# ---------------------------------------------------------------------------

def urls_preserved(before: str, after: str, *, allow_new: bool = True) -> bool:
    """True when *after* keeps every URL *before* had (no citation silently dropped).

    Composes ``textutil.norm_urls`` (broad ``https?://\\S+`` + trailing-punct rstrip,
    set-dedup). ``allow_new=True`` (default, the majority of sites) = lost-only: no
    before-URL dropped, gained URLs allowed. ``allow_new=False`` = bidirectional
    equality: also reject a fabricated NEW URL (the two synthesis sites'
    anti-hallucination copy). Pure set logic — cannot raise; on any doubt a caller
    keeps *before*."""
    before_urls, after_urls = _norm_urls(before), _norm_urls(after)
    if not before_urls <= after_urls:
        return False
    if not allow_new and not after_urls <= before_urls:
        return False
    return True


# ---------------------------------------------------------------------------
# 2. length_ratio_ok — char-length cap + regression floor
# ---------------------------------------------------------------------------

def length_ratio_ok(
    after: str, before: str | None = None, *,
    min_ratio: float | None = None, max_chars: int | None = None,
) -> bool:
    """True when *after*'s char length is acceptable (accept/keep).

    ``max_chars`` → reject when ``len(after) > max_chars`` (upper cap).
    ``min_ratio`` → reject when ``len(after) < int(min_ratio * len(before))``
    (anti-regression floor; the ``int()`` floor is load-bearing — do NOT round).
    Returns only the boolean; each caller keeps its own action (drop vs truncate vs
    keep-original). Word-count and prose-per-URL DENSITY floors are a different
    metric and are intentionally NOT handled here."""
    n = len(after or "")
    if max_chars is not None and n > max_chars:
        return False
    if min_ratio is not None and n < int(min_ratio * len(before or "")):
        return False
    return True


# ---------------------------------------------------------------------------
# 3. invented_headings — reject text that adds an unsanctioned heading
# ---------------------------------------------------------------------------

def invented_headings(
    before: str, after: str, *, max_level: int = 6, mask: bool = True,
) -> set[str]:
    """Heading LINES in *after* that are NOT in the sanctioned *before*.

    Caller rejects (keeps prior text / drops the candidate) when the returned set is
    non-empty. ``before=""`` → absolute ban (any heading is invented). ``mask=True``
    (default) blanks fenced code first so an in-code ``# comment`` never counts as a
    heading. Exact heading-LINE membership — no heading-key folding. Cannot raise."""
    pat = _re.compile(rf"(?m)^#{{1,{max_level}}}\s.*$")

    def _heads(t: str) -> set[str]:
        return set(pat.findall(_mask_fenced_code(t) if mask else (t or "")))

    return _heads(after) - _heads(before)


# ---------------------------------------------------------------------------
# 4. topical_verdict — is a cited URL off-topic for the requirement?
# ---------------------------------------------------------------------------
#
# Topical floor calibration (2026-07-05, .web_cache.json of the live Pi/Craft
# task — 40 real pages). SET-OVERLAP of requirement words is BROKEN for this:
# the 23K-token π-Wikipedia page incidentally hits 8/13 common requirement
# words (0.62 — above many genuine pages). What separates junk is DENSITY:
# occurrences of requirement-word stems per page token.
#   junk:    π-Wikipedia 0.0019, dictionary/use 0.0067, dictionary/limitation 0.0136
#   genuine: 0.0124 (a nav page) … 0.32; bulk ≥ 0.018
# The bands overlap in [0.010, 0.030], so that gray zone goes to a binary LLM
# verdict on the page excerpt (same lesson as studio.relevance: lexical
# metrics saturate; a constrained binary classification does not).
_OFFTOPIC_HARD_DENSITY = 0.010   # below → drop deterministically
_OFFTOPIC_CLEAR_DENSITY = 0.030  # above → keep deterministically
_OFFTOPIC_VERDICT_RE = _re.compile(r"\b(IRRELEVANT|RELEVANT)\b", _re.IGNORECASE)

# Module-level cache for offtopic verdicts. Gray-zone URLs that need judge
# evaluation are expensive (full LLM round-trip per call). Pages and requirement
# are fixed within a run, so verdicts are safely cacheable by
# (normalized_url, sha256_hash_of_requirement). Cap at 512 entries; clear on overflow.
_OFFTOPIC_VERDICT_CACHE: dict[tuple[str, str], bool] = {}


def topical_verdict(url: str, req_words: set[str], requirement: str = "",
                    judge=None) -> bool | None:
    """Judge a cited URL by its CACHED PAGE: True = off-topic, False = on-topic,
    None = unknown (never cached → cannot judge; the grounding oracle owns
    fabrication). Density two-tier + LLM gray zone (see calibration above).
    Fail-open: gray zone without a judge, or a judge error, keeps the URL.

    ``req_words`` is supplied by the caller — findings feeds the RAW requirement's
    stems, research_first the ``_base_task_text`` (template-stripped) stems. That
    derivation divergence is intentional and preserved here; folding it in would
    change the findings-path density (S3: relocate the oracle, do not unify the
    two callers' preprocessing)."""
    from studio.tools import _page_for_url

    page = _page_for_url(url)
    if not page:
        return None
    toks = _re.findall(r"[a-z]{4,}", page.lower())
    if not toks:
        return None
    density = sum(1 for t in toks if t.rstrip("s") in req_words) / len(toks)
    if density < _OFFTOPIC_HARD_DENSITY:
        _dbg(f"offtopic[{url[:60]}]: DROP density={density:.4f} (hard band)")
        return True
    if density > _OFFTOPIC_CLEAR_DENSITY:
        return False
    if judge is None:
        _dbg(f"offtopic[{url[:60]}]: KEEP density={density:.4f} (gray, no judge)")
        return False

    # Gray zone: check memoization cache before calling judge.
    norm_url = url.strip().rstrip('/').lower()
    req_hash = _hashlib.sha256(requirement.encode()).hexdigest()[:12]
    cache_key = (norm_url, req_hash)

    if cache_key in _OFFTOPIC_VERDICT_CACHE:
        verdict = _OFFTOPIC_VERDICT_CACHE[cache_key]
        _dbg(f"offtopic[{url[:60]}]: {'DROP' if verdict else 'KEEP'} "
             f"density={density:.4f} (gray, judge=CACHED)")
        return verdict

    try:
        reply = judge.chat([{"role": "user", "content": (
            "You judge whether a fetched SOURCE PAGE is about the SPECIFIC "
            "subject of a task, or merely shares common words with it.\n\n"
            "IRRELEVANT includes a source that merely DEFINES a word the task "
            "happens to use (a dictionary-style entry) or covers a DIFFERENT "
            "subject that shares a name with something in the task (a homonym). "
            "Sharing vocabulary is not relevance; addressing the task's actual "
            "subject is.\n\n"
            f"TASK: {requirement[:400]}\n\n"
            f"SOURCE PAGE EXCERPT (from {url}):\n{page[:2000]}\n\n"
            "Answer on the last line with exactly one word: RELEVANT or IRRELEVANT."
        )}])
        matches = _OFFTOPIC_VERDICT_RE.findall(getattr(reply, "text", "") or "")
        verdict = matches[-1].upper() == "IRRELEVANT" if matches else False

        # Cache the verdict before returning.
        if len(_OFFTOPIC_VERDICT_CACHE) >= 512:
            _OFFTOPIC_VERDICT_CACHE.clear()
        _OFFTOPIC_VERDICT_CACHE[cache_key] = verdict

        _dbg(f"offtopic[{url[:60]}]: {'DROP' if verdict else 'KEEP'} "
             f"density={density:.4f} (gray, judge={'IRRELEVANT' if verdict else 'RELEVANT/unparsed'})")
        return verdict
    except Exception as exc:  # noqa: BLE001 — relevance judging is best-effort; keep on failure
        # Do NOT cache a failure: only a computed verdict may be memoized. A failed
        # judge call should simply be retried on the next call.
        _dbg(f"offtopic[{url[:60]}]: KEEP density={density:.4f} (gray, judge error {type(exc).__name__})")
        return False
