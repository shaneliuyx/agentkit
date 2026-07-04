"""Pure-renderer unit tests for the A2 deterministic diagram path (Bug A).

Deterministic, no LLM/network: the model's job (emit COMPONENT/EDGE lines) is faked
with literal strings, and grounding is a pure literal-token check against the report
prose (no embedder). Everything asserted here is code we own — parse, render, ground,
cap, place. The renderer must NEVER emit invalid mermaid regardless of input.
"""
from __future__ import annotations

import re

from studio import diagram_render as dr

_REPORT = (
    "# Agent Loops Report\n\n"
    "## Architecture\n\n"
    "The Planner produces a plan. The Executor runs tools. The Memory store "
    "persists state. The Scorer grades the artifact against a rubric.\n\n"
    "## References\n\n- https://x.test/e\n"
)


def _balanced(body: str) -> bool:
    return (
        body.count("[") == body.count("]")
        and body.count("(") == body.count(")")
        and body.count("{") == body.count("}")
    )


def test_renderer_emits_valid_flowchart_from_clean_lines() -> None:
    raw = (
        "COMPONENT: Planner | builds plan\n"
        "COMPONENT: Executor | runs tools\n"
        "COMPONENT: Memory | persists state\n"
        "COMPONENT: Scorer | grades output\n"
        "EDGE: Planner -> Executor | hands off\n"
        "EDGE: Executor -> Scorer\n"
    )
    body = dr.render_grounded_diagram(raw, _REPORT)
    assert body is not None
    assert body.splitlines()[0] == "flowchart TD"
    assert _balanced(body)
    assert "-->" in body
    assert '["Planner"]' in body and "N0" in body  # synthetic ids, quoted labels


def test_renderer_tolerates_malformed_and_stray_lines() -> None:
    """Interleaved prose, blank lines, and a broken EDGE must not crash or corrupt the
    output — stray lines are skipped, valid grounded ones still render."""
    raw = (
        "Sure! Here is the architecture:\n"
        "COMPONENT: Planner | builds plan\n"
        "\n"
        "garbage line with no keyword\n"
        "COMPONENT: Executor runs tools\n"  # no pipe — still a valid component name
        "COMPONENT: Memory | persists state\n"
        "COMPONENT: Scorer | grades\n"
        "EDGE: Planner Executor\n"  # malformed (no ->) — skipped
        "EDGE: Planner -> Executor\n"
    )
    body = dr.render_grounded_diagram(raw, _REPORT)
    assert body is not None
    assert body.startswith("flowchart TD")
    assert _balanced(body)


def test_literal_guard_rejects_invented_components() -> None:
    """Every component fabricated (no >=4-char token appears in the report) → all
    dropped → below _MIN_NODES → None."""
    raw = "\n".join(f"COMPONENT: Zorptron{i} | invented" for i in range(6))
    assert dr.render_grounded_diagram(raw, _REPORT) is None


def test_literal_guard_drops_ungrounded_keeps_grounded() -> None:
    """Mix: only components whose token literally appears in the report survive; a
    single grounded node (< _MIN_NODES) → None."""
    raw = (
        "COMPONENT: Planner | real\n"
        "COMPONENT: Zorptron | invented\n"
        "COMPONENT: Blivet | invented\n"
    )
    assert dr.render_grounded_diagram(raw, _REPORT) is None


def test_grounding_matches_inflected_forms() -> None:
    """Word-start matching keeps a real term even as a plural/inflection in the prose
    ('Loop' vs 'Loops', 'Produce' vs 'produces') — real nodes are not lost to surface
    variation, which is why literal grounding is safe here."""
    raw = (
        "COMPONENT: Loop | the report's title term (appears as 'Loops')\n"
        "COMPONENT: Produce | appears as 'produces'\n"
        "COMPONENT: Persist | appears as 'persists'\n"
        "COMPONENT: Grade | appears as 'grades'\n"
    )
    body = dr.render_grounded_diagram(raw, _REPORT)
    assert body is not None and body.startswith("flowchart TD")


def test_short_token_labels_fall_open() -> None:
    """Labels with no >=4-char token are un-checkable, so grounding falls open (keeps
    them) — the accept gate is the backstop; grounding must not reject what it can't check."""
    raw = "\n".join(f"COMPONENT: AI{i} | x" for i in range(5))
    body = dr.render_grounded_diagram(raw, _REPORT)
    assert body is not None and body.startswith("flowchart TD")


def test_renderer_caps_node_count() -> None:
    """A runaway list is capped at _MAX_NODES rendered nodes (all grounded via the
    'Planner' token that is present in the report)."""
    raw = "\n".join(f"COMPONENT: Planner {i} | role" for i in range(20))
    body = dr.render_grounded_diagram(raw, _REPORT)
    assert body is not None
    node_ids = set(re.findall(r"\bN\d+\b", body))
    assert len(node_ids) <= dr._MAX_NODES


def test_edges_only_between_rendered_nodes() -> None:
    """An edge to a dropped/unknown component is skipped, never left dangling."""
    raw = (
        "COMPONENT: Planner | real\n"
        "COMPONENT: Executor | real\n"
        "COMPONENT: Memory | real\n"
        "COMPONENT: Scorer | real\n"
        "EDGE: Planner -> Ghostnode | to a non-component\n"
        "EDGE: Planner -> Executor\n"
    )
    body = dr.render_grounded_diagram(raw, _REPORT)
    assert body is not None
    assert body.count("-->") == 1  # the Ghostnode edge is dropped


def test_insertion_places_block_in_body_before_references() -> None:
    body = dr.render_grounded_diagram(
        "COMPONENT: Planner | a\nCOMPONENT: Executor | b\n"
        "COMPONENT: Memory | c\nCOMPONENT: Scorer | d\n"
        "EDGE: Planner -> Executor\n",
        _REPORT,
    )
    out = dr.insert_diagram_block(_REPORT, body)
    assert out.index("## Architecture") < out.index("```mermaid") < out.index("## References")


def test_insertion_appends_when_no_target_or_references() -> None:
    plain = "Just prose, no headings at all.\n"
    out = dr.insert_diagram_block(plain, "flowchart TD\n    N0[\"X\"]")
    assert out.startswith(plain)
    assert "```mermaid" in out
