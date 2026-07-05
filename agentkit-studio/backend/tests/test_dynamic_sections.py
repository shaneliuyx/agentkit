"""Workstream P — planner-reviewed requirement-driven dynamic sections.

The template is only the STARTING outline: the planner reviews it against the
requirement's deliverable-shaped asks and may add a section or sub-section.
The scoring baseline stays frozen (no moving target) — additions land on
``rubric_config["active_template"]`` only.
"""

from __future__ import annotations

import copy
from typing import Any

from studio.planning import (
    apply_section_decisions,
    deliverable_candidates,
    requirement_section_decisions,
)
from studio.runner import _build_template_skeleton

_PI_CRAFT_REQ = (
    "Study how to use Pi and Craft to develop agents and create a research "
    "report, need to include example code and design architecture."
)

_TEMPLATE = [
    "Executive Summary",
    "Scope and Research Questions",
    "Key Findings",
    "Evidence and Analysis",
    "Limitations and Open Questions",
    "References",
]


class _ScriptedClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    def chat(self, messages: list[dict], **_kw: Any) -> Any:
        self.calls += 1

        class _R:
            pass

        r = _R()
        r.text = self._text
        return r


# ── deterministic candidate extraction ───────────────────────────────────────

def test_pi_craft_requirement_yields_both_deliverables() -> None:
    """The live requirement that exposed the gap MUST extract both asks."""
    cands = deliverable_candidates(_PI_CRAFT_REQ)
    assert "example code" in cands
    assert "design architecture" in cands


def test_no_form_deliverable_means_no_candidates_and_no_llm_call() -> None:
    client = _ScriptedClient("[]")
    decisions = requirement_section_decisions(
        client, _TEMPLATE, "Write a thorough report on EU carbon policy."
    )
    assert decisions == []
    assert client.calls == 0  # the trigger gate: no candidates → no review call


def test_count_constraints_are_not_deliverables() -> None:
    cands = deliverable_candidates(
        "Keep it under 800 words and cite at least 3 sources."
    )
    assert cands == []


# ── deterministic fallback decisions ─────────────────────────────────────────

def test_fallback_creates_new_sections_for_unmatched_deliverables() -> None:
    decisions = requirement_section_decisions(None, _TEMPLATE, _PI_CRAFT_REQ)
    by_deliverable = {d["deliverable"]: d for d in decisions}
    assert by_deliverable["example code"]["action"] == "new_section"
    assert by_deliverable["design architecture"]["action"] == "new_section"


def test_fallback_maps_to_existing_section_on_concept_match() -> None:
    outline = _TEMPLATE + ["Example Code Walkthrough"]
    decisions = requirement_section_decisions(None, outline, _PI_CRAFT_REQ)
    code = next(d for d in decisions if d["deliverable"] == "example code")
    assert code["action"] == "existing"
    assert code["title"] == "Example Code Walkthrough"


# ── LLM review path ──────────────────────────────────────────────────────────

def test_llm_decisions_are_parsed_and_validated() -> None:
    client = _ScriptedClient(
        'Here you go:\n[{"deliverable": "example code", "action": "subsection", '
        '"title": "Example Code", "parent": "Evidence and Analysis"}, '
        '{"deliverable": "design architecture", "action": "new_section", '
        '"title": "Proposed Architecture", "parent": null}]'
    )
    decisions = requirement_section_decisions(client, _TEMPLATE, _PI_CRAFT_REQ)
    assert decisions[0]["action"] == "subsection"
    assert decisions[0]["parent"] == "Evidence and Analysis"
    assert decisions[1]["title"] == "Proposed Architecture"


def test_malformed_llm_reply_falls_back_to_deterministic() -> None:
    client = _ScriptedClient("I think the outline looks fine as is.")
    decisions = requirement_section_decisions(client, _TEMPLATE, _PI_CRAFT_REQ)
    assert len(decisions) == 2
    assert all(d["action"] == "new_section" for d in decisions)


def test_invalid_action_falls_back() -> None:
    client = _ScriptedClient(
        '[{"deliverable": "example code", "action": "rewrite_everything", '
        '"title": "X", "parent": null}]'
    )
    decisions = requirement_section_decisions(client, _TEMPLATE, _PI_CRAFT_REQ)
    assert all(d["action"] in ("existing", "new_section", "subsection") for d in decisions)


# ── apply: live outline grows, frozen scoring untouched ──────────────────────

def _rc() -> dict:
    return {
        "template": list(_TEMPLATE),
        "scoring_template": list(_TEMPLATE),
        "scoring_matrix": [{"category": "Code quality / examples", "weight": 8}],
    }


def test_apply_grows_active_template_only_scoring_stays_frozen() -> None:
    rc = _rc()
    frozen_before = copy.deepcopy(
        {k: rc[k] for k in ("template", "scoring_template", "scoring_matrix")}
    )
    added, subs = apply_section_decisions(rc, [
        {"deliverable": "design architecture", "action": "new_section",
         "title": "Proposed Architecture", "parent": None},
        {"deliverable": "example code", "action": "subsection",
         "title": "Example Code", "parent": "Evidence and Analysis"},
    ])
    assert added == ["Proposed Architecture"]
    assert rc["active_template"][-1] == "Proposed Architecture"
    assert subs == {"Evidence and Analysis": ["Example Code"]}
    # the no-moving-target contract: frozen keys byte-identical
    assert {k: rc[k] for k in frozen_before} == frozen_before


def test_apply_caps_new_sections() -> None:
    rc = _rc()
    titles = ["Proposed Architecture", "Sample Implementation", "Deployment Guide",
              "Benchmark Results", "Migration Path", "Cost Breakdown"]
    decisions = [
        {"deliverable": t.lower(), "action": "new_section", "title": t, "parent": None}
        for t in titles
    ]
    added, _ = apply_section_decisions(rc, decisions)
    assert len(added) == 3


def test_apply_dedupes_against_existing_concepts() -> None:
    rc = _rc()
    added, _ = apply_section_decisions(rc, [
        {"deliverable": "key findings", "action": "new_section",
         "title": "Findings", "parent": None},  # concept-dup of "Key Findings"
    ])
    assert added == []
    assert "active_template" not in rc  # nothing changed → live outline untouched


# ── skeleton renders subsections ─────────────────────────────────────────────

def test_skeleton_renders_subsection_placeholders_under_parent() -> None:
    skel = _build_template_skeleton(
        ["Evidence and Analysis", "References"],
        {"evidence and analysis": ["Example Code"]},
    )
    lines = skel.splitlines()
    parent_i = lines.index("## Evidence and Analysis")
    sub_i = lines.index("### Example Code")
    refs_i = lines.index("## References")
    assert parent_i < sub_i < refs_i


def test_skeleton_drops_subsection_with_unknown_parent() -> None:
    skel = _build_template_skeleton(
        ["References"], {"Nonexistent Parent": ["Orphan"]}
    )
    assert "Orphan" not in skel


# ── sub-section OWNERSHIP: pending ### headings reach a worker's assignment ──
# Live finding (2026-07-05, s_f649739da1af): planner-injected sub-sections
# appeared in ZERO spoke inputs — rows carry section titles, the ### markers
# live in file bodies, so no agent was responsible for them.

def test_pending_subsections_found_and_filled_ones_skipped(tmp_path) -> None:
    from studio.section_workspace import pending_subsections, write_section_workspace

    art = (
        "# T\n\n## Evidence and Analysis\nIntro prose.\n\n"
        "### Code Implementation Examples\n_(pending - needs sourced content)_\n\n"
        "### System Architecture Design\nReal sourced content already here.\n\n"
        "## References\nhttps://x.test\n"
    )
    write_section_workspace(tmp_path, art)
    subs = pending_subsections(tmp_path)
    assert subs == {"Evidence and Analysis": ["Code Implementation Examples"]}


def test_assignment_row_names_pending_subsections_for_owner_only() -> None:
    from studio.planning import build_section_assignment_rows

    rows = build_section_assignment_rows(
        ["Evidence and Analysis", "References"],
        subsections={"Evidence and Analysis": ["Code Implementation Examples",
                                               "System Architecture Design"]},
    )
    by_section = {r["section"]: r["assignment"] for r in rows}
    owner = by_section["## Evidence and Analysis"]
    assert "REQUIRED SUB-SECTIONS" in owner
    assert "### Code Implementation Examples" in owner
    assert "### System Architecture Design" in owner
    assert "REQUIRED SUB-SECTIONS" not in by_section["## References"]
