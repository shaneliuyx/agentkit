"""Tests for the deterministic run-1537/§14-slate format repairs in studio.artifact_text:
fence-line contamination (a ``` marker with trailing content glued on), doubled
citations (``[title](URL) URL`` where the bare URL duplicates the link target), and
duplicate ## sections (merge_duplicate_sections). All pure text transforms — no LLM,
no fixtures beyond inline strings.
"""
from __future__ import annotations

from studio.artifact_text import (
    _repair_doubled_citations,
    _repair_fence_contamination,
    merge_duplicate_sections,
)
from studio.textutil import norm_urls


def test_repairs_closing_fence_with_glued_citation() -> None:
    text = "```python\nprint('hi')\n``` https://example.com/report\nafter\n"
    out, changed = _repair_fence_contamination(text)
    assert changed
    assert out == "```python\nprint('hi')\n```\nhttps://example.com/report\nafter\n"


def test_repairs_opening_fence_with_glued_url_and_no_language() -> None:
    """No legal language tag survives a URL glued onto an opener — the whole
    contaminated rest moves BEFORE the fence (landing it in the code body would
    corrupt the block). The preceding line is non-blank, so a blank line is
    inserted too — otherwise the moved citation fuses into that line's paragraph."""
    text = "before\n```https://example.com/src\nprint('hi')\n```\nafter\n"
    out, changed = _repair_fence_contamination(text)
    assert changed
    assert out == "before\n\nhttps://example.com/src\n```\nprint('hi')\n```\nafter\n"


def test_legal_language_tag_opener_is_untouched() -> None:
    text = "```python\nprint('hi')\n```\n"
    out, changed = _repair_fence_contamination(text)
    assert not changed
    assert out == text


def test_fence_repair_is_idempotent() -> None:
    text = "```python\nprint('hi')\n``` https://example.com/report\n"
    out1, _ = _repair_fence_contamination(text)
    out2, changed2 = _repair_fence_contamination(out1)
    assert out2 == out1
    assert not changed2


def test_opening_fence_glue_after_non_blank_line_gets_its_own_paragraph() -> None:
    """A citation glued to an opening fence right after a sentence (no blank line
    between them, the common LLM shape) must NOT fuse into that sentence's
    paragraph — a blank line is inserted before the moved citation. Complements
    test_repairs_opening_fence_with_glued_url_and_no_language above by covering
    the WITH-a-language-tag variant (```python https://... vs bare ```https://...)."""
    text = "As shown below:\n```python https://example.com/doc\nprint(1)\n```\n"
    out, changed = _repair_fence_contamination(text)
    assert changed
    assert "As shown below:\n\nhttps://example.com/doc\n```python" in out


def test_indented_list_item_fence_keeps_moved_citation_indented() -> None:
    """Both branches must prefix the moved line with the fence's own indent, or a
    citation moved out of a list-item's fence de-indents to the margin and falls
    out of the list item."""
    opener = "  - Example:\n    ```python https://example.com/opener\n    x = 1\n    ```\n"
    out, changed = _repair_fence_contamination(opener)
    assert changed
    assert "    https://example.com/opener\n    ```python" in out

    closer = "  - Example:\n    ```python\n    x = 1\n    ``` https://example.com/closer\n"
    out2, changed2 = _repair_fence_contamination(closer)
    assert changed2
    assert "    ```\n    https://example.com/closer" in out2


def test_fence_contamination_repair_idempotent_on_new_placement_cases() -> None:
    opener = "As shown below:\n```python https://example.com/doc\nprint(1)\n```\n"
    indented = "  - Example:\n    ```python\n    x = 1\n    ``` https://example.com/closer\n"
    for text in (opener, indented):
        once, _ = _repair_fence_contamination(text)
        twice, changed_again = _repair_fence_contamination(once)
        assert twice == once
        assert not changed_again


def test_repairs_doubled_citation_exact_match() -> None:
    text = "See [Report](https://example.com/report) https://example.com/report for details."
    out, changed = _repair_doubled_citations(text)
    assert changed
    assert out == "See [Report](https://example.com/report) for details."


def test_doubled_citation_repair_keeps_trailing_punctuation() -> None:
    text = "See [Report](https://example.com/report) https://example.com/report, for details."
    out, changed = _repair_doubled_citations(text)
    assert changed
    assert out == "See [Report](https://example.com/report), for details."


def test_adjacent_different_url_is_untouched() -> None:
    text = "See [Report](https://example.com/report) https://other.com/x for details."
    out, changed = _repair_doubled_citations(text)
    assert not changed
    assert out == text


def test_doubled_citation_repair_is_idempotent() -> None:
    text = "See [Report](https://example.com/report) https://example.com/report for details."
    out1, _ = _repair_doubled_citations(text)
    out2, changed2 = _repair_doubled_citations(out1)
    assert out2 == out1
    assert not changed2


# --- §14 slate B: duplicate ## sections -------------------------------------

_DUP_DOC = (
    "# Report\n\n"
    "## Design Architecture\n\n"
    "```python\ndef run():\n    pass\n```\n\n"
    "### Data Flow\n\nHow data moves through the system. https://a.com/flow\n\n"
    "## Findings\n\nSome findings with https://a.com/x.\n\n"
    "## Design Architecture\n\n"
    "This section restates the overall system design in prose only, with no "
    "code or diagrams, just further discussion of the same architecture.\n\n"
    "## References\n\n- https://a.com/x\n- https://a.com/flow\n"
)


def test_merge_duplicate_sections_collapses_to_one_heading_and_keeps_content():
    out, changed = merge_duplicate_sections(_DUP_DOC)
    assert changed
    assert out.count("## Design Architecture") == 1
    assert "```python" in out
    assert "### Data Flow" in out
    assert "This section restates" in out  # second occurrence's content folded in


def test_merge_duplicate_sections_never_drops_a_url():
    out, _ = merge_duplicate_sections(_DUP_DOC)
    assert norm_urls(_DUP_DOC) <= norm_urls(out)


def test_merge_duplicate_sections_is_idempotent():
    once, _ = merge_duplicate_sections(_DUP_DOC)
    twice, changed_again = merge_duplicate_sections(once)
    assert twice == once
    assert not changed_again


def test_merge_duplicate_sections_noop_on_a_clean_doc():
    clean = "# Report\n\n## A\n\nbody.\n\n## B\n\nbody2 https://x.com\n"
    out, changed = merge_duplicate_sections(clean)
    assert not changed and out == clean


def test_merge_duplicate_sections_matches_the_lint_key_numbering_insensitive():
    """Same identity key as artifact_lint._duplicate_heading_issues (numbering-
    and case-insensitive) — this repair must actually clear THAT lint, not a
    stricter exact-text match that misses the shape the lint flags."""
    doc = (
        "## Executive Summary\n\nFirst version. https://a.com/1\n\n"
        "## 2. EXECUTIVE SUMMARY\n\nSecond version. https://a.com/2\n"
    )
    out, changed = merge_duplicate_sections(doc)
    assert changed
    assert out.lower().count("executive summary") == 1
    assert norm_urls(doc) <= norm_urls(out)


# --- review fix: exact-duplicate echoes must not multiply a citation ---------

def test_two_byte_identical_sections_keep_the_citation_exactly_once():
    doc = (
        "## Findings\n\nSame text here. https://a.com/x\n\n"
        "## Findings\n\nSame text here. https://a.com/x\n"
    )
    out, changed = merge_duplicate_sections(doc)
    assert changed
    assert out.count("https://a.com/x") == 1


def test_three_byte_identical_sections_keep_the_citation_exactly_once():
    doc = "## Findings\n\nSame text here. https://a.com/x\n\n" * 3
    out, changed = merge_duplicate_sections(doc)
    assert changed
    assert out.count("https://a.com/x") == 1


def test_similar_but_different_url_paragraphs_are_both_kept():
    """The never-drop rule protects DISTINCT citations, not identical copies —
    two different sentences sharing the same URL are not an exact duplicate."""
    doc = (
        "## Findings\n\nFirst point. https://a.com/x\n\n"
        "## Findings\n\nSecond, different point. https://a.com/x\n"
    )
    out, changed = merge_duplicate_sections(doc)
    assert changed
    assert out.count("https://a.com/x") == 2
    assert "First point." in out and "Second, different point." in out
