"""Tests for studio.artifact_lint — the content-validity weakness source (§14.6).

The hill-climb loop is blind to MALFORMED content (it only names what is missing).
These pin the deterministic lints that turn a broken mermaid edge / truncated code
fence into a named weakness so the reducer repairs it.
"""
from __future__ import annotations

from pathlib import Path

from studio.artifact_text import add_missing_section_citations
from studio.artifact_lint import lint_artifact


def test_flags_mermaid_edge_glued_to_label() -> None:
    """The exact real failure: `ToolSelector|Read|` (no `-->`) → 'got PIPE' parse error."""
    text = (
        "## Design Architecture\n\nThis diagram explains tool routing.\n\n```mermaid\ngraph TD\n"
        "    ToolSelector -->|Search| WebTool\n"
        "    ToolSelector|Read| ReadTool\n"
        "```\n"
    )
    issues = lint_artifact(text)
    assert len(issues) == 1
    assert "Malformed mermaid edge" in issues[0]
    assert "Design Architecture" in issues[0]  # located to its section


def test_valid_mermaid_is_clean() -> None:
    """A well-formed `A -->|label| B` must NOT be flagged (no false positive)."""
    text = (
        "## Flow\n\nThis diagram explains the search and confirmation path.\n\n"
        "```mermaid\ngraph TD\n"
        "    A -->|Search| B\n    B -.->|maybe| C\n    C ==>|yes| D\n"
        "```\n"
    )
    assert lint_artifact(text) == []


def test_ignores_pipes_outside_mermaid() -> None:
    """A markdown TABLE uses pipes but is not mermaid — must not be flagged."""
    text = "## Data\n\n| col a | col b |\n|-------|-------|\n| 1 | 2 |\n"
    assert lint_artifact(text) == []


def test_flags_unbalanced_code_fence() -> None:
    """An odd number of ``` markers = a truncated/unclosed block."""
    issues = lint_artifact("## Code\n\n```python\nx = 1\n")  # never closed
    assert any("Unbalanced code fence" in w for w in issues)


def test_flags_empty_or_malformed_tables() -> None:
    malformed = "## Data\n\n| A | B |\n| -- | --- |\n| 1 | 2 |\n"
    empty = "## Comparison\n\n| A | B |\n| --- | --- |\n| TBD | — |\n"

    assert any("Malformed markdown table separator" in w for w in lint_artifact(malformed))
    assert any("Empty comparison table" in w for w in lint_artifact(empty))


def test_flags_unexplained_mermaid_diagram() -> None:
    text = "## Architecture\n\n```mermaid\ngraph TD\nA --> B\n```\n"

    issues = lint_artifact(text)

    assert any("Mermaid diagram has no nearby explanatory prose" in w for w in issues)


def test_flags_python_syntax_but_ignores_pseudocode() -> None:
    bad_python = "## Code\n\n```python\ndef broken(:\n    pass\n```\n"
    pseudocode = "## Algorithm\n\n```pseudocode\nIF x THEN\n  RETURN y\n```\n"

    assert any("Python code block has syntax error" in w for w in lint_artifact(bad_python))
    assert lint_artifact(pseudocode) == []


def test_clean_document_has_no_issues() -> None:
    assert lint_artifact("# Title\n\n## A\nbody\n\n## B\nmore\n") == []
    assert lint_artifact("") == []


def test_add_missing_section_citations_repairs_long_uncited_sections() -> None:
    prose = " ".join(["evidence"] * 160)
    text = f"# Report\n\n## Analysis\n\n{prose}\n\n## References\n\n- https://example.com/source\n"

    repaired = add_missing_section_citations(text, ["https://example.com/source"])

    assert "## Analysis" in repaired
    assert repaired.count("https://example.com/source") == 2
    assert not any("Long evidence-bearing section" in issue for issue in lint_artifact(repaired))


def test_flags_report_body_after_references() -> None:
    text = """# Report

## Executive Summary

Summary with source https://example.com/source.

## References

- https://example.com/source

### Analysis

This stale section should not appear after references.
"""

    issues = lint_artifact(text)

    assert any("References section is followed" in issue for issue in issues)


def test_bad_report_fixture_flags_known_quality_failures() -> None:
    fixture = Path(__file__).parent / "fixtures" / "bad_report_duplicate_sections.md"
    issues = lint_artifact(fixture.read_text(encoding="utf-8"))
    joined = "\n".join(issues)

    assert "Duplicate section heading" in joined
    assert "Placeholder text remains" in joined
    assert "unverified" in joined.lower()
    assert "Citation wall" in joined
    assert "Code fragment appears outside" in joined
    assert "Long evidence-bearing section has no citation URL" in joined


# --- clean `(unverified)` visibility ----------------------------------------
# entry 173 fixed neutralize_unverified_urls to heal garbled brackets into a
# clean bare `(unverified)` placeholder — but lint_artifact never learned to
# flag that CLEAN form (only the old garbled `((unverified)` / `](unverified`
# forms), so a citation that failed verification ships to the user with zero
# visibility into the fix loop. Real live evidence: session s_791e2db70e88
# (task_hash 39ee3efddbd9) — lint_artifact returned 0 issues on an artifact
# that visibly said "(unverified)" three times.

def test_flags_clean_bare_unverified_marker() -> None:
    text = "The service runs as a thin client (unverified)."
    issues = lint_artifact(text)
    assert any("(unverified)" in issue for issue in issues)


def test_garbled_marker_still_flagged_unchanged() -> None:
    # Regression: the OLD garbled-form message must still fire (entry 173
    # behavior untouched).
    text = "thin client ((unverified))."
    issues = lint_artifact(text)
    assert any("Malformed or explicitly unverified markdown link" in i for i in issues)


def test_clean_report_with_no_unverified_marker_stays_clean() -> None:
    text = "A report citing a real source ([Example](https://example.com/a))."
    issues = lint_artifact(text)
    assert not any("unverified" in i.lower() for i in issues)


def test_profile_appropriate_clean_report_has_no_new_quality_issues() -> None:
    text = """# Market Report

## Executive Summary

The market is expanding according to a primary filing
([Example](https://example.com/filing)).

## Evidence and Analysis

The reported growth signal is supported by the filing
([Example](https://example.com/filing)) and an independent survey
([Survey](https://example.com/survey)).

## Limitations and Uncertainty

The sample is narrow, so the estimate should be treated as directional
([Survey](https://example.com/survey)).

## References

- https://example.com/filing
- https://example.com/survey
"""
    assert lint_artifact(text) == []
