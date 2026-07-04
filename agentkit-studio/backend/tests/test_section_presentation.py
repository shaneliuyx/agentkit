"""Unit tests for the per-section presentation pass (Follow-up #2, MVP diagram path).

Pure/deterministic: the model is faked (detector verdict keyed on heading; component
extraction returns fixed COMPONENT/EDGE lines). Everything asserted is code we own —
split, signal pre-filter, idempotency, ranking, insertion+caption, local-debt check.
"""
from __future__ import annotations

import re

from studio import section_presentation as sp

_ARTIFACT = (
    "# Agent Report\n\n"
    "## Executive Summary\n\nA short narrative overview of the project and its aims.\n\n"
    "## Architecture\n\nThe Planner builds a plan. The Executor runs tools. The Memory "
    "store persists state. The Scorer grades the artifact.\n\n"
    "## Data Table\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n"
    "## References\n\n- https://x.test/e\n"
)

_COMPONENTS = (
    "COMPONENT: Planner | builds plan\nCOMPONENT: Executor | runs tools\n"
    "COMPONENT: Memory | persists state\nCOMPONENT: Scorer | grades output\n"
    "EDGE: Planner -> Executor\nEDGE: Executor -> Scorer\n"
)


class _R:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeClient:
    """Detector returns DIAGRAM for any heading in ``diagram_headings``; component
    extraction (prompt has '=== REPORT ===') returns fixed lines."""

    def __init__(self, diagram_headings: set[str], components: str = _COMPONENTS) -> None:
        self.diagram_headings = diagram_headings
        self.components = components

    def chat(self, messages, tools=None):
        content = messages[0]["content"] if messages else ""
        if "=== REPORT ===" in content:
            return _R(self.components)
        m = re.search(r"SECTION HEADING: (.+)", content)
        head = m.group(1).strip() if m else ""
        return _R("DIAGRAM" if any(h in head for h in self.diagram_headings) else "PROSE")


def test_split_h2_drops_title_and_preamble() -> None:
    secs = sp.split_h2(_ARTIFACT)
    assert [s.heading for s in secs] == [
        "## Executive Summary", "## Architecture", "## Data Table", "## References",
    ]


def test_has_visual_detects_table_and_mermaid() -> None:
    assert sp.has_visual("| a | b |\n| - | - |")
    assert sp.has_visual("```mermaid\nflowchart TD\n```")
    assert not sp.has_visual("Just prose about a Planner and an Executor.")


def test_candidate_signal_prefilter_gates_llm() -> None:
    """A low-signal (< _MIN_SIGNAL named components) section is never sent to the
    detector — even a client that would say DIAGRAM for everything yields no eligible."""
    client = _FakeClient(diagram_headings={"References"})  # References names <3 components
    elig = sp.eligible_sections(client, sp.split_h2(_ARTIFACT))
    assert all("References" not in s.heading for s in elig)


def test_eligible_skips_prose_and_existing_visual() -> None:
    client = _FakeClient(diagram_headings={"Architecture", "Data Table"})
    elig = sp.eligible_sections(client, sp.split_h2(_ARTIFACT))
    heads = [s.heading for s in elig]
    assert "## Architecture" in heads          # DIAGRAM + no visual → eligible
    assert "## Data Table" not in heads         # DIAGRAM but already a table → skipped (idempotent)
    assert "## Executive Summary" not in heads  # PROSE → skipped


def test_plan_one_inserts_captioned_diagram_in_chosen_section() -> None:
    client = _FakeClient(diagram_headings={"Architecture"})
    new_text, heading, telem = sp.plan_one(client, _ARTIFACT)
    assert heading == "## Architecture"
    assert new_text is not None
    assert "```mermaid" in new_text and "flowchart TD" in new_text
    assert "*Figure:" in new_text  # caption (C8)
    # Block landed INSIDE Architecture, before Data Table.
    assert new_text.index("## Architecture") < new_text.index("```mermaid") < new_text.index("## Data Table")
    assert sp.section_has_visual(new_text, "## Architecture")  # local debt 1 -> 0 (C1)
    assert telem == {"debt_total": 1, "attempted": 1, "satisfied": 1}


def test_plan_one_ranks_highest_signal_first() -> None:
    """Two eligible sections → the higher-signal one is chosen (C5 one-diagram)."""
    art = (
        "# R\n\n## Small\n\nThe Planner calls the Executor and the Scorer.\n\n"
        "## Big\n\nThe Planner, Executor, Memory, Scorer, Reducer, and Grounder all interact.\n\n"
    )
    client = _FakeClient(diagram_headings={"Small", "Big"})
    _, heading, telem = sp.plan_one(client, art)
    assert heading == "## Big"           # more named components → higher signal → chosen
    assert telem["debt_total"] == 2       # both counted as debt
    assert telem["satisfied"] == 1        # only one drawn


def test_plan_one_returns_none_when_nothing_warrants() -> None:
    client = _FakeClient(diagram_headings=set())  # detector says PROSE everywhere
    new_text, heading, telem = sp.plan_one(client, _ARTIFACT)
    assert new_text is None and heading is None
    assert telem["debt_total"] == 0


def test_plan_one_routes_detection_to_judge_generation_to_client() -> None:
    """The judge_client decides warrant (gemma over-affirms structure → detection belongs
    on a strong model); the generation client extracts components. Detection must NEVER hit
    the generation client, and the judge's verdict is what selects the section."""
    judge = _FakeClient(diagram_headings={"Architecture"})

    class _GenOnly:
        """Extraction-only: answers the components prompt, and would say PROSE for any
        detection turn — so if detection wrongly routed here, no diagram would be chosen."""

        def __init__(self) -> None:
            self.detect_calls = 0

        def chat(self, messages, tools=None):
            content = messages[0]["content"] if messages else ""
            if "=== REPORT ===" in content:
                return _R(_COMPONENTS)
            self.detect_calls += 1
            return _R("PROSE")

    gen = _GenOnly()
    new_text, heading, telem = sp.plan_one(gen, _ARTIFACT, judge_client=judge)
    assert heading == "## Architecture"          # judge detected it, not the gen client
    assert new_text is not None and "```mermaid" in new_text
    assert gen.detect_calls == 0                  # detection never routed to the generator


def test_detect_diagram_fails_closed_on_error() -> None:
    class _Boom:
        def chat(self, messages, tools=None):
            raise RuntimeError("detector down")
    assert sp.detect_diagram(_Boom(), "## Architecture", "The Planner calls the Executor.") is False
