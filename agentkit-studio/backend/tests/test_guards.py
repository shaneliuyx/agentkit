"""One home for the four shared accept/reject primitives (studio.guards, PLAN S3).

Each block pins the primitive's VERBATIM threshold / direction so a future change
to a number (or a flipped comparison polarity) fails here rather than silently in a
live run. Behavior these tests lock:
  * urls_preserved  — set-based, textutil-normalized, lost-only vs bidirectional.
  * length_ratio_ok — strict ``>`` cap, ``int()`` regression floor.
  * invented_headings — H1-6 default, code-masking default, exact-line membership.
  * topical_verdict — 0.010 / 0.030 density bands, fail-open to KEEP/None.
"""
from __future__ import annotations

from studio import guards as g


# ---------------------------------------------------------------------------
# 1. urls_preserved
# ---------------------------------------------------------------------------

def test_urls_preserved_lost_only_default_allows_gained() -> None:
    # A gained URL is fine by default (readability/insertion/expansion sites).
    assert g.urls_preserved("see http://x.com", "see http://x.com and http://y.com")
    # A dropped URL is never fine.
    assert not g.urls_preserved("see http://x.com", "nothing here")


def test_urls_preserved_allow_new_false_rejects_fabricated_url() -> None:
    # The two synthesis sites also reject a NEW (fabricated) URL — bidirectional.
    assert not g.urls_preserved(
        "http://x.com", "http://x.com http://y.com", allow_new=False
    )
    # Exact same URL set both ways is accepted.
    assert g.urls_preserved("a http://x.com b", "http://x.com only", allow_new=False)


def test_urls_preserved_composes_textutil_trailing_punct_normalization() -> None:
    # Trailing wrapper punctuation is stripped by textutil.norm_urls, so a URL that
    # merely gains/loses a trailing paren counts as the SAME URL (not a de-citation).
    assert g.urls_preserved("(http://x.com)", "http://x.com", allow_new=False)


# ---------------------------------------------------------------------------
# 2. length_ratio_ok
# ---------------------------------------------------------------------------

def test_length_ratio_ok_max_chars_is_strict_greater_than() -> None:
    # At the cap = kept; one over = rejected (pins the strict ``>``, e.g. the 2500 sites).
    assert g.length_ratio_ok("x" * 2500, max_chars=2500)
    assert not g.length_ratio_ok("x" * 2501, max_chars=2500)


def test_length_ratio_ok_min_ratio_uses_int_floor_not_round() -> None:
    # int(0.9 * 10) == 9. len 9 accepted (9 < 9 is False), len 8 rejected.
    # round(0.9*10)==9 too, but int() is load-bearing where the product is fractional:
    assert g.length_ratio_ok("x" * 9, "y" * 10, min_ratio=0.9)
    assert not g.length_ratio_ok("x" * 8, "y" * 10, min_ratio=0.9)
    # int(0.8 * 3) == 2 (round would give 2 here too); int(0.8*7)==5 vs round==6 —
    # len 5 must be ACCEPTED under int(), which round() would reject:
    assert g.length_ratio_ok("x" * 5, "y" * 7, min_ratio=0.8)


def test_length_ratio_ok_no_bounds_is_true() -> None:
    assert g.length_ratio_ok("anything")


# ---------------------------------------------------------------------------
# 3. invented_headings
# ---------------------------------------------------------------------------

def test_invented_headings_delta_keeps_preexisting_and_flags_new() -> None:
    before = "# Title\n## A"
    after = "# Title\n## A\n## B"
    assert g.invented_headings(before, after) == {"## B"}
    assert g.invented_headings(before, "# Title\n## A") == set()


def test_invented_headings_absolute_ban_with_empty_before() -> None:
    assert g.invented_headings("", "## Any") == {"## Any"}
    assert g.invented_headings("", "no heading here") == set()


def test_invented_headings_default_level_is_h1_through_h6() -> None:
    # Default max_level=6: H6 counts, 7 hashes does not (pins the {1,6} bound).
    assert g.invented_headings("", "###### h6") == {"###### h6"}
    assert g.invented_headings("", "####### h7") == set()
    # max_level=3 (the artifact_text synthesis site) ignores H4-H6.
    assert g.invented_headings("", "#### h4", max_level=3) == set()


def test_invented_headings_masks_fenced_code_by_default() -> None:
    fenced = "```python\n# not a heading\n```"
    assert g.invented_headings("", fenced) == set()           # masked (default)
    assert g.invented_headings("", fenced, mask=False) == {"# not a heading"}


# ---------------------------------------------------------------------------
# 4. topical_verdict
# ---------------------------------------------------------------------------

def _page(match_count: int, total: int) -> str:
    """A page of *total* [a-z]{4,} tokens, *match_count* of them the stem 'alpha'."""
    return " ".join(["alpha"] * match_count + ["zzzz"] * (total - match_count))


def test_topical_verdict_density_bands_are_pinned() -> None:
    # The two literal thresholds — a change to either fails here.
    assert g._OFFTOPIC_HARD_DENSITY == 0.010
    assert g._OFFTOPIC_CLEAR_DENSITY == 0.030


def test_topical_verdict_hard_band_drops(monkeypatch) -> None:
    monkeypatch.setattr("studio.tools._page_for_url", lambda _u: _page(1, 1000))
    # density 0.001 < 0.010 → off-topic (True/drop), no judge needed.
    assert g.topical_verdict("http://x", {"alpha"}, "req") is True


def test_topical_verdict_clear_band_keeps(monkeypatch) -> None:
    monkeypatch.setattr("studio.tools._page_for_url", lambda _u: _page(40, 1000))
    # density 0.04 > 0.030 → on-topic (False/keep).
    assert g.topical_verdict("http://x", {"alpha"}, "req") is False


def test_topical_verdict_gray_band_without_judge_fails_open_keep(monkeypatch) -> None:
    monkeypatch.setattr("studio.tools._page_for_url", lambda _u: _page(20, 1000))
    # density 0.02 in [0.010, 0.030], judge=None → fail-open KEEP (False).
    assert g.topical_verdict("http://x", {"alpha"}, "req", judge=None) is False


def test_topical_verdict_uncached_page_is_unknown_none(monkeypatch) -> None:
    monkeypatch.setattr("studio.tools._page_for_url", lambda _u: "")
    assert g.topical_verdict("http://x", {"alpha"}, "req") is None
