"""Pure-renderer unit tests for the A2 deterministic diagram path (Bug A).

Deterministic, no LLM/network: the model's job (emit COMPONENT/EDGE lines) is faked
with literal strings, and the SEMANTIC grounding embedder is faked with an axis
double (grounded concept → one axis, fabricated → orthogonal). Everything asserted
here is code we own — parse, render, ground, cap, place. The renderer must NEVER
emit invalid mermaid regardless of input.
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


class _AxisEmbedder:
    """Semantic double: any text mentioning a grounded concept → shared axis 0 (a
    grounded label and its section → cosine 1.0); each DISTINCT non-grounded string →
    its own axis (a fabricated label matches neither a grounded nor a 'References'
    section). Mirrors the production geometry closely enough to exercise the guard at
    the real 0.40 threshold."""

    _GROUNDED = ("planner", "executor", "memory", "scorer", "architecture", "agent", "rubric")

    def __init__(self) -> None:
        self._other: dict[str, int] = {}

    def embed(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            v = [0.0] * 129
            if any(g in low for g in self._GROUNDED):
                v[0] = 1.0
            else:
                idx = self._other.setdefault(low.strip(), len(self._other))
                v[1 + (idx % 128)] = 1.0
            out.append(v)
        return out


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
    body = dr.render_grounded_diagram(raw, _REPORT, embedder=_AxisEmbedder())
    assert body is not None
    assert body.splitlines()[0] == "flowchart TD"
    assert _balanced(body)
    assert "-->" in body
    assert '["Planner"]' in body and "N0" in body  # synthetic ids, quoted labels


def test_renderer_tolerates_malformed_and_stray_lines() -> None:
    """Interleaved prose, blank lines, and a broken EDGE must not crash or corrupt the
    output — stray lines are skipped, valid ones still render (fall-open, no embedder)."""
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
    body = dr.render_grounded_diagram(raw, _REPORT, embedder=None)
    assert body is not None
    assert body.startswith("flowchart TD")
    assert _balanced(body)


def test_semantic_guard_rejects_invented_components() -> None:
    """Every component fabricated (orthogonal embedding, cosine 0) → below floor → None."""
    raw = "\n".join(f"COMPONENT: Zorptron{i} | invented" for i in range(6))
    assert dr.render_grounded_diagram(raw, _REPORT, embedder=_AxisEmbedder()) is None


def test_semantic_guard_drops_ungrounded_keeps_grounded() -> None:
    """Mix: only semantically-grounded components survive; 1 grounded (< floor) → None."""
    raw = (
        "COMPONENT: Planner | real\n"
        "COMPONENT: Zorptron | invented\n"
        "COMPONENT: Blivet | invented\n"
    )
    assert dr.render_grounded_diagram(raw, _REPORT, embedder=_AxisEmbedder()) is None


def test_falls_open_when_no_embedder() -> None:
    """No embedder → trust the model's extraction (accept gate is the backstop). Even
    'fabricated' names render, because grounding cannot be checked without an embedder."""
    raw = "\n".join(f"COMPONENT: Zorptron{i} | x" for i in range(5))
    body = dr.render_grounded_diagram(raw, _REPORT, embedder=None)
    assert body is not None and body.startswith("flowchart TD")


def test_falls_open_when_embedder_raises() -> None:
    """A raising embedder (oMLX down) must fall open, never crash the retry."""
    class _Boom:
        def embed(self, texts):
            raise RuntimeError("embed backend down")
    raw = (
        "COMPONENT: Planner | a\nCOMPONENT: Executor | b\n"
        "COMPONENT: Memory | c\nCOMPONENT: Scorer | d\n"
    )
    body = dr.render_grounded_diagram(raw, _REPORT, embedder=_Boom())
    assert body is not None and body.startswith("flowchart TD")


def test_renderer_caps_node_count() -> None:
    """A runaway list is capped at _MAX_NODES rendered nodes (fall-open path)."""
    raw = "\n".join(f"COMPONENT: Node {i} | role" for i in range(20))
    body = dr.render_grounded_diagram(raw, _REPORT, embedder=None)
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
    body = dr.render_grounded_diagram(raw, _REPORT, embedder=_AxisEmbedder())
    assert body is not None
    assert body.count("-->") == 1  # the Ghostnode edge is dropped


def test_insertion_places_block_in_body_before_references() -> None:
    body = dr.render_grounded_diagram(
        "COMPONENT: Planner | a\nCOMPONENT: Executor | b\n"
        "COMPONENT: Memory | c\nCOMPONENT: Scorer | d\n"
        "EDGE: Planner -> Executor\n",
        _REPORT,
        embedder=_AxisEmbedder(),
    )
    out = dr.insert_diagram_block(_REPORT, body)
    assert out.index("## Architecture") < out.index("```mermaid") < out.index("## References")


def test_insertion_appends_when_no_target_or_references() -> None:
    plain = "Just prose, no headings at all.\n"
    out = dr.insert_diagram_block(plain, "flowchart TD\n    N0[\"X\"]")
    assert out.startswith(plain)
    assert "```mermaid" in out
