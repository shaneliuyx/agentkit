"""PLAN items 3 + 7: URL normalization, cache verification, fabricated-URL neutralization."""
from studio.task_runs import (
    _normalize_url,
    neutralize_unverified_urls,
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
