"""Pin each studio.textutil primitive once (S1: PLAN-codebase-simplification.md).

These primitives used to be N copies scattered across studio/*.py; this file is now
the single place their behavior is tested, per the S1 module docstring.
"""
from __future__ import annotations

import studio.textutil as tu


def test_norm_url_strips_markdown_wrap_and_trailing_punctuation():
    # A markdown-link close paren and trailing sentence punctuation are the two
    # real-world cases that caused a false "citation lost" in a synthesis guard.
    assert tu.norm_url("https://x.test/a)") == "https://x.test/a"
    assert tu.norm_url("https://x.test/a.") == "https://x.test/a"
    assert tu.norm_url("https://x.test/a,") == "https://x.test/a"
    assert tu.norm_url("https://x.test/a>") == "https://x.test/a"
    assert tu.norm_url('"https://x.test/a"') == '"https://x.test/a'  # only trailing side stripped
    assert tu.norm_url("`https://x.test/a`") == "`https://x.test/a"


def test_extract_urls_preserves_order_and_duplicates():
    text = "See https://a.test/1) and https://b.test/2. Then https://a.test/1 again."
    urls = tu.extract_urls(text)
    assert urls == ["https://a.test/1", "https://b.test/2", "https://a.test/1"]


def test_norm_urls_dedupes_into_a_set():
    text = "[Title](https://a.test/x) and https://a.test/x. Also https://b.test/y"
    assert tu.norm_urls(text) == {"https://a.test/x", "https://b.test/y"}


def test_extract_urls_keeps_url_with_internal_bracket():
    """Reviewer finding (S1): the narrow character-class regexes this module
    replaced (``expand_sections._urls``, ``artifact_text._urls_in_order``) stopped
    matching at the FIRST excluded char anywhere in the URL, not just a trailing
    one — silently truncating a real URL with an internal parenthesis. Pin the
    new, strictly-more-correct wide-capture behavior so it never gets "fixed back"
    to the lossy narrow regex."""
    text = "See https://example.com/search?q=(pi)&page=2 for results."
    assert tu.extract_urls(text) == ["https://example.com/search?q=(pi)&page=2"]


def test_content_word_stems_below_threshold_is_empty():
    assert tu.content_word_stems("fix the bug") == set()  # too few distinct 4+ letter stems


def test_content_word_stems_collapses_plurals_via_stem():
    req = ("Research how build agent frameworks with tool calling loops "
           "memory systems planning modules evaluation harnesses")
    stems = tu.content_word_stems(req)
    assert "framework" in stems  # "frameworks" stemmed
    assert "loop" in stems       # "loops" stemmed


def test_mask_fenced_code_hides_hash_comments_but_keeps_line_count():
    text = "# Real Heading\n```python\n# not a heading\nx = 1\n```\nMore text\n"
    masked = tu.mask_fenced_code(text)
    assert "# not a heading" not in masked
    assert "# Real Heading" in masked
    assert masked.count("\n") == text.count("\n")  # line positions preserved


def test_has_code_fence_true_for_real_code_false_for_mermaid_only():
    assert tu.has_code_fence("```python\nprint(1)\n```") is True
    assert tu.has_code_fence("```mermaid\nflowchart TD\n```") is False
    assert tu.has_code_fence("no fences here") is False


def test_mermaid_regexes_diverge_on_an_unclosed_fence():
    """The two mermaid regexes are DELIBERATELY not unified (S1 report): a
    presence check on a truncated/unclosed fence must still say "yes" (the open
    fence is real evidence of intent), while a whole-block EXTRACT correctly
    finds nothing to extract."""
    unclosed = "## Architecture\n```mermaid\nflowchart TD\n    A --> B\n"
    assert tu.MERMAID_OPEN_RE.search(unclosed) is not None
    assert tu.MERMAID_BLOCK_RE.search(unclosed) is None

    closed = "## Architecture\n```mermaid\nflowchart TD\n    A --> B\n```\n"
    assert tu.MERMAID_OPEN_RE.search(closed) is not None
    assert tu.MERMAID_BLOCK_RE.search(closed) is not None


def test_dbg_is_a_noop_without_the_env_var(monkeypatch, tmp_path):
    monkeypatch.delenv("OMC_THROUGHPUT_DEBUG", raising=False)
    tu.dbg("should not raise or write anywhere")  # just must not throw


def test_dbg_appends_to_the_configured_file(monkeypatch, tmp_path):
    log = tmp_path / "debug.log"
    monkeypatch.setenv("OMC_THROUGHPUT_DEBUG", str(log))
    tu.dbg("line one")
    tu.dbg("line two")
    assert log.read_text() == "line one\nline two\n"
