"""PLAN items 3 + 7: URL normalization, cache verification, fabricated-URL neutralization."""
from studio.task_runs import (
    _normalize_url,
    neutralize_unverified_urls,
    strip_unverified_lines,
    verified_urls_in_cache,
)


def test_normalize_unifies_scheme_slash_and_tracking():
    a = _normalize_url("http://Example.com/path/")
    b = _normalize_url("https://example.com/path")
    c = _normalize_url("https://example.com/path?utm_source=x&id=7")
    assert a == b
    # tracking param dropped, real param kept
    assert c == "https://example.com/path?id=7"


def test_verified_urls_matches_across_format_variance():
    # cache holds the https, no-slash form; citation uses http + trailing slash.
    cache = {"search:q": [{"url": "https://example.com/article"}]}
    text = "See http://example.com/article/ for details."
    assert verified_urls_in_cache(cache, text) == ["http://example.com/article/"]


def test_verified_urls_includes_fetch_entries():
    cache = {"fetch:https://blog.dev/post:body": "…fetched content…"}
    text = "Source: https://blog.dev/post"
    assert verified_urls_in_cache(cache, text) == ["https://blog.dev/post"]


def test_neutralize_replaces_unverified_keeps_verified():
    text = "Real https://real.com/x and fake https://fake.com/y here."
    out = neutralize_unverified_urls(text, ["https://real.com/x"])
    assert "https://real.com/x" in out
    assert "https://fake.com/y" not in out
    assert "(unverified)" in out


def test_neutralize_fail_open_on_empty_verified_set():
    # search down → verified set empty → never strip every citation.
    text = "Cited https://maybe-real.com/z."
    assert neutralize_unverified_urls(text, []) == text
    assert neutralize_unverified_urls(text, None) == text


def test_neutralize_preserves_trailing_punctuation():
    text = "Bad link https://fake.com/q)."
    out = neutralize_unverified_urls(text, ["https://real.com/x"])
    assert out == "Bad link (unverified))."


# --- Regression: orphaned brackets from wrapped citations (session s_7da9e7c683ac) ---
# The reducer sometimes emits a markdown link whose anchor text is itself a URL, e.g.
# `[https://a](https://b)`, optionally wrapped in citation parens `([...](...))`. The old
# bare-URL regex swapped only the inner URL text and left the enclosing brackets, producing
# invalid markdown like `[(unverified))`, `([(unverified)))`, and `((unverified))`.
_VERIFIED = ["https://real.com/kept"]


def test_neutralize_paren_wrapped_markdown_link_no_orphan_brackets():
    # artifact.md:160 → observed broken output was "...thin client ([(unverified)))."
    text = "...thin client ([https://a.io/title](https://fake.sub/x))."
    out = neutralize_unverified_urls(text, _VERIFIED)
    assert out == "...thin client (unverified)."  # outer citation parens collapse too
    assert "[" not in out and "]" not in out
    assert "((unverified))" not in out


def test_neutralize_plain_markdown_link_url_label_no_orphan_brackets():
    # artifact.md:179 → observed broken output was "OpenClaw. [(unverified))"
    text = "OpenClaw. [https://a.io/title](https://nader.sub/p/x)"
    out = neutralize_unverified_urls(text, _VERIFIED)
    assert out == "OpenClaw. (unverified)"
    assert "[" not in out and "]" not in out


def test_neutralize_list_item_markdown_link_no_orphan_brackets():
    # artifact.md:187 → observed broken output was "- [(unverified))"
    text = "- [https://a.io/title](https://fake.sub/y)"
    out = neutralize_unverified_urls(text, _VERIFIED)
    assert out == "- (unverified)"


def test_neutralize_markdown_link_keeps_meaningful_label():
    text = "See [Craft Agents docs](https://fake.sub/z) for more."
    out = neutralize_unverified_urls(text, _VERIFIED)
    assert out == "See Craft Agents docs (unverified) for more."


def test_neutralize_bare_paren_url_no_double_paren():
    text = "external sandboxing (https://fake.sub/pi)."
    out = neutralize_unverified_urls(text, _VERIFIED)
    assert out == "external sandboxing (unverified)."
    assert "((unverified))" not in out


def test_neutralize_keeps_verified_wrapped_link_and_paren():
    text = "a [Real Title](https://real.com/kept) and (https://real.com/kept)."
    out = neutralize_unverified_urls(text, _VERIFIED)
    assert out == text  # verified → untouched, brackets preserved


# --- Healing ALREADY-GARBLED markers from before the entry-173 fix -----------
# Once the pre-fix bug ran, the original URL is destroyed, so `_CITATION_RE`
# (which matches on a live URL) can never find these again — a fresh run of
# the FIXED neutralize_unverified_urls on an artifact carried forward via
# auto_improve from before the fix still shows the raw garbled bytes. These
# are real observed strings from a live re-run of the exact task that
# originally surfaced entry 173 (session s_196ef7b0cd4b, task_hash
# 39ee3efddbd9, continuing the s_7da9e7c683ac lineage, 2026-07-03).

def test_neutralize_heals_garbled_paren_wrapped_marker_no_verified_urls():
    text = (
        "with the desktop app connecting as a thin client ([(unverified))). "
    )
    out = neutralize_unverified_urls(text, [])  # empty verified set — no live URL to match
    assert out == "with the desktop app connecting as a thin client (unverified). "
    assert "[" not in out and "((unverified))" not in out


def test_neutralize_heals_garbled_bracket_marker_no_verified_urls():
    text = "OpenClaw. [(unverified))"
    assert neutralize_unverified_urls(text, None) == "OpenClaw. (unverified)"


def test_neutralize_heals_garbled_list_item_marker_no_verified_urls():
    assert neutralize_unverified_urls("- [(unverified))", []) == "- (unverified)"


def test_neutralize_heals_garbled_marker_even_alongside_a_live_citation():
    # Healing is unconditional (runs before the verified-set gate), so it fires
    # in the SAME pass that also neutralizes a genuinely unverified live URL.
    text = "Old scar: [(unverified)) New: https://fake.sub/z here."
    out = neutralize_unverified_urls(text, _VERIFIED)
    assert out == "Old scar: (unverified) New: (unverified) here."


def test_neutralize_does_not_touch_already_clean_marker():
    # Idempotency: a bare, already-healed `(unverified)` has no extra leading
    # `[`/`(` or trailing `)`, so the healing regex must never re-touch it.
    text = "Already clean: (unverified) — nothing to do here."
    assert neutralize_unverified_urls(text, []) == text


# --- strip_unverified_lines -------------------------------------------------
# Real live evidence (session s_791e2db70e88, task_hash 39ee3efddbd9): a
# citation that failed verification should not ship AT ALL — not as a bare
# placeholder bullet, not as an inline tail tag, not as a full reference entry
# with a fake URL. Generic: matches only on the literal placeholder text
# `neutralize_unverified_urls` already produces (never a task/domain keyword),
# so this is a pure "delete what's tagged" pass, unconditional, right after
# neutralization at the runner.py call site.

def test_strips_bare_placeholder_only_bullet():
    text = "Some real reference.\n\n- (unverified)\n\nMore text."
    out = strip_unverified_lines(text)
    assert "(unverified)" not in out
    assert "Some real reference." in out and "More text." in out


def test_strips_bare_placeholder_only_bullet_with_asterisk_marker():
    assert "(unverified)" not in strip_unverified_lines("* (unverified)\n")


def test_strips_inline_prose_line_with_trailing_marker():
    # The whole claim goes, not just the tag — an unverifiable citation is
    # dropped entirely rather than shipped with a caveat.
    text = "Intro line stays.\n\nThe desktop app connects as a thin client (unverified).\n\nOutro stays."
    out = strip_unverified_lines(text)
    assert "(unverified)" not in out
    assert "Intro line stays." in out and "Outro stays." in out


def test_strips_reference_line_with_real_content_and_marker():
    text = "- Nader (2024), a real reference title. (unverified)\n- Real one (https://real.example)\n"
    out = strip_unverified_lines(text)
    assert "(unverified)" not in out
    assert "Nader" not in out
    assert "Real one" in out


def test_strips_multiple_unverified_lines():
    text = "- (unverified)\n- (unverified)\n- Real one (https://real.example)\n"
    out = strip_unverified_lines(text)
    assert out.count("(unverified)") == 0
    assert "Real one" in out


def test_does_not_touch_lines_without_the_marker():
    text = "Perfectly fine line.\nAnother clean line ([Example](https://example.com))."
    assert strip_unverified_lines(text) == text


def test_empty_input_is_no_op():
    assert strip_unverified_lines("") == ""
    assert strip_unverified_lines(None) == ""
