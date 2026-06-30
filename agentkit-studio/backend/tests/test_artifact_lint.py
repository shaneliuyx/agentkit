"""Tests for studio.artifact_lint — the content-validity weakness source (§14.6).

The hill-climb loop is blind to MALFORMED content (it only names what is missing).
These pin the deterministic lints that turn a broken mermaid edge / truncated code
fence into a named weakness so the reducer repairs it.
"""
from __future__ import annotations

from pathlib import Path

from studio.artifact_lint import lint_artifact


def test_flags_mermaid_edge_glued_to_label() -> None:
    """The exact real failure: `ToolSelector|Read|` (no `-->`) → 'got PIPE' parse error."""
    text = (
        "## Design Architecture\n\n```mermaid\ngraph TD\n"
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
        "## Flow\n\n```mermaid\ngraph TD\n"
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


def test_clean_document_has_no_issues() -> None:
    assert lint_artifact("# Title\n\n## A\nbody\n\n## B\nmore\n") == []
    assert lint_artifact("") == []


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
