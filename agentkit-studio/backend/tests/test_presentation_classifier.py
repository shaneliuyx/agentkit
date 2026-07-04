"""VERIFY layer for the presentation classifier (PLAN-generic §10 detection core).

Labeled cases with a KNOWN ideal form prove the ladder recommends the right presentation
AND flags an improvement only where the current form differs — at the exact location.
Pure/deterministic (no LLM): every case is shaped so the deterministic tier commits.
"""
from __future__ import annotations

import pytest

from studio.presentation_classifier import (
    Form,
    analyze_report,
    classify_block,
    current_form,
    extract_features,
)

# --- Labeled content: (text, ideal recommended form) ------------------------------- #

_PARAGRAPH = (
    "Grounding is the substrate of trust: a report that cannot trace each claim to a "
    "source is persuasive noise, so we treat verification as the first-class objective "
    "rather than a finishing step."
)
_BULLETED = (
    "The toolkit ships several independent capabilities: web search over a live index, "
    "a local embedding store, a deterministic diagram renderer, and a citation verifier."
)  # 4 parallel items, no order → bulleted
_NUMBERED = (
    "First the Planner drafts an outline. Then the Executor fetches evidence for each "
    "section. Next the Reducer patches findings in. Finally the Scorer grades the result."
)  # sequence markers → numbered
_TABLE_COMPARISON = (
    "Redis is fast but volatile, whereas Postgres is durable but slower; compared to "
    "both, S3 is cheapest but has the highest latency and no query engine."
)  # comparison across attributes → table
_TABLE_LONG = (
    "The supported providers are Anthropic, OpenAI, Google, Mistral, Cohere, Groq, "
    "Together, Fireworks, DeepSeek, and Perplexity."
)  # 10 items (> 8) → table
_DIAGRAM = (
    "The Planner calls the Executor, which feeds the Memory store and sends its output "
    "to the Scorer; the Scorer returns weaknesses to the Planner."
)  # components + relationships → diagram


@pytest.mark.parametrize(
    "text, ideal",
    [
        (_PARAGRAPH, Form.PARAGRAPH),
        (_BULLETED, Form.BULLETED_LIST),
        (_NUMBERED, Form.NUMBERED_LIST),
        (_TABLE_COMPARISON, Form.TABLE),
        (_TABLE_LONG, Form.TABLE),
        (_DIAGRAM, Form.DIAGRAM),
    ],
)
def test_recommends_ideal_form_per_shape(text, ideal) -> None:
    """The ladder names the right presentation for each of the five content shapes."""
    assert classify_block(text).recommended is ideal


def test_text_first_floor_two_items_stays_prose() -> None:
    """Two parallel items do NOT earn a list — text-first (Turabian/Cornell)."""
    two = "The system has a planner and an executor."
    assert classify_block(two).recommended is Form.PARAGRAPH


def test_current_form_reads_markdown_surface() -> None:
    assert current_form("```mermaid\nflowchart TD\n```") is Form.DIAGRAM
    assert current_form("| a | b |\n| - | - |\n| 1 | 2 |") is Form.TABLE
    assert current_form("1. one\n2. two\n3. three") is Form.NUMBERED_LIST
    assert current_form("- one\n- two\n- three") is Form.BULLETED_LIST
    assert current_form("Just a flowing sentence about one thing.") is Form.PARAGRAPH


def test_improvement_flagged_only_when_form_wrong() -> None:
    """A comparison written as prose → improvement (→ TABLE). The SAME content already in
    a table → no improvement. This is the core 'identify what needs fixing' behavior."""
    prose = classify_block(_TABLE_COMPARISON)
    assert prose.current is Form.PARAGRAPH and prose.recommended is Form.TABLE
    assert prose.improvement_needed

    already = classify_block("| store | speed | durability |\n| - | - | - |\n| Redis | fast | low |")
    assert already.current is Form.TABLE and not already.improvement_needed


def test_analyze_report_pinpoints_exact_location() -> None:
    """Given a report, the walker returns the wrong-form sections with an offset that
    points at the actual body — 'identify the improvement at exactly the location'."""
    report = (
        "# Report\n\n"
        f"## Overview\n\n{_PARAGRAPH}\n\n"          # correct as prose → no improvement
        f"## Storage Options\n\n{_TABLE_COMPARISON}\n\n"  # prose, should be TABLE
        f"## Pipeline\n\n{_DIAGRAM}\n\n"            # prose, should be DIAGRAM
    )
    imps = analyze_report(report)
    by_head = {i.heading: i for i in imps}
    assert "## Overview" not in by_head                      # correctly left alone
    assert by_head["## Storage Options"].recommended is Form.TABLE
    assert by_head["## Pipeline"].recommended is Form.DIAGRAM
    # Offsets point at the real bodies.
    assert report[by_head["## Storage Options"].char_offset:].lstrip().startswith("Redis")
    assert report[by_head["## Pipeline"].char_offset:].lstrip().startswith("The Planner")


def test_features_are_measured_not_guessed() -> None:
    """Spot-check the feature signals the ladder decides on are actually extracted."""
    f = extract_features(_DIAGRAM)
    assert f.n_components >= 3 and f.has_relationships
    assert extract_features(_TABLE_COMPARISON).has_comparison
    assert extract_features(_NUMBERED).has_sequence and extract_features(_NUMBERED).n_items >= 3


def test_no_false_improvement_on_correct_prose() -> None:
    """A flowing-argument section already in prose is never flagged (no over-listing)."""
    report = f"# R\n\n## Why This Matters\n\n{_PARAGRAPH}\n"
    assert analyze_report(report) == []
