"""Tests for the two deterministic run-1537 format repairs in studio.artifact_text:
fence-line contamination (a ``` marker with trailing content glued on) and doubled
citations (``[title](URL) URL`` where the bare URL duplicates the link target).
Both are pure text transforms — no LLM, no fixtures beyond inline strings.
"""
from __future__ import annotations

from studio.artifact_text import _repair_doubled_citations, _repair_fence_contamination


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
