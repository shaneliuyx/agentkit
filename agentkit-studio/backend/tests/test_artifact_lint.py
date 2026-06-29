"""Tests for studio.artifact_lint — the content-validity weakness source (§14.6).

The hill-climb loop is blind to MALFORMED content (it only names what is missing).
These pin the deterministic lints that turn a broken mermaid edge / truncated code
fence into a named weakness so the reducer repairs it.
"""
from __future__ import annotations

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
