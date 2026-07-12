"""Tests for M8/M9 helper functions in studio.runner (DESIGN §2.2, §3, §5).

All four helpers are pure functions — no LLM, no network, no filesystem.
Tests cover:
  - _parse_epic_plan   : happy path, missing block, bad JSON, wrong shape
  - _build_planner_cot_prompt  : required structural markers present
  - _parse_patches_from_output : happy path, missing block, bad JSON, multi-patch
"""

from __future__ import annotations

import json

from studio.runner import (
    _build_planner_cot_prompt,
    _build_reducer_refine_prompt,
    _dedupe_assignment,
    _parse_assigned,
    _parse_epic_plan,
    _parse_patches_from_output,
    _detect_gaps,
    _gap_sections,
    _unresolved_block,
    _phase_search_failed,
    _plan_from_epics,
    _research_findings_to_patches,
)


def test_detect_gaps_flags_placeholder_only() -> None:
    # 2026-06-27 fix: ONLY empty/placeholder sections are gaps. Substantive prose
    # whose citations live elsewhere (a References section) is NOT a gap — the old
    # "prose without inline URL" rule mis-flagged it and exploded agent sizing.
    doc = (
        "# Report\nOverview paragraph with enough words to count as real content.\n"
        "## Intro\nReal intro with [src](https://a.com) and detail.\n\n"
        "## Results\n_(pending — needs sourced content)_\n\n"
        "## Analysis\nThis section makes several substantive claims about agent loops "
        "and verifiers and patterns but cites nothing inline; refs live in References.\n"
    )
    gaps = _detect_gaps(doc)
    msgs = " ".join(m for _, m in gaps)
    assert any("Results" in m and "placeholder" in m for _, m in gaps)
    assert "Intro" not in msgs      # sourced section is not a gap
    assert "Analysis" not in msgs   # prose-without-inline-URL is NO LONGER a gap


def test_detect_gaps_clean_doc_has_none() -> None:
    doc = "## A\nGrounded [x](https://x.com) content.\n## B\nMore [y](https://y.com).\n"
    assert _detect_gaps(doc) == []


def test_detect_gaps_empty_section() -> None:
    gaps = _detect_gaps("## Sources\n\n## Refs\n[r](https://r.com)\n")
    msgs = [m for _, m in gaps]
    assert any("Sources" in m for m in msgs) and not any("Refs" in m for m in msgs)


def test_unresolved_block_surfaces_still_open_repeat_failures() -> None:
    # §11.4: a repeat-failure still present this run is appended below the result,
    # never hidden. Normalization strips the [section] label to match history.
    repeat_failed = {"missing urls on cited articles"}
    weaknesses = ["[## Sources] missing URLs on cited articles", "[## Intro] too short"]
    block = _unresolved_block(weaknesses, repeat_failed, 3)
    assert "Known unresolved issues" in block
    assert "missing URLs on cited articles" in block  # the persistent one surfaced
    assert "too short" not in block                   # not a repeat-failure


def test_unresolved_block_empty_when_nothing_persistent() -> None:
    assert _unresolved_block(["[## Intro] too short"], {"missing urls"}, 3) == ""
    assert _unresolved_block([], set(), 3) == ""


def test_gap_sections_consolidate_to_top_level() -> None:
    # Regression for the 2026-06-27 gap-flood: many empty SUB-sections collapse to
    # their few top-level (h1/h2) parents, so the worklist (and agent count) stays
    # bounded by the document's real section count, not the raw gap count.
    doc = (
        "# Report\nOverview paragraph with enough words to count as real content.\n"
        "## Findings\nIntro to findings with body text here.\n"
        "### Sub A\n\n### Sub B\n\n### Sub C\n\n"  # 3 empty sub-sections, one parent
        "## Sources\n\n"                            # one empty top-level section
    )
    sections = _gap_sections(_detect_gaps(doc))
    assert sections == ["## Findings", "## Sources"]  # 4 gaps → 2 top-level sections


def test_findings_to_patches_additive_with_target() -> None:
    txt = (
        "## RESEARCH_FINDING\nARTICLE_TITLE: Loop Engineering\n"
        "URL: https://addyosmani.com/loop\nKEY_INSIGHT: verifier is the bottleneck\n"
        "POPULARITY: 6.5M views\nPATCH_TARGET: ## Sources\n"
    )
    ps = _research_findings_to_patches(txt)
    assert len(ps) == 1
    assert ps[0].op == "insert_after" and ps[0].anchor == "## Sources"
    assert "https://addyosmani.com/loop" in ps[0].content
    assert "6.5M views" in ps[0].content


def test_findings_to_patches_drops_unsourced() -> None:
    # No real URL → not content → no patch (§11 grounding).
    txt = "## RESEARCH_FINDING\nARTICLE_TITLE: x\nURL: (none)\nPATCH_TARGET: ## S\n"
    assert _research_findings_to_patches(txt) == []


def test_findings_to_patches_append_when_no_target() -> None:
    txt = "## RESEARCH_FINDING\nARTICLE_TITLE: T\nURL: https://x.com\n"
    ps = _research_findings_to_patches(txt)
    assert len(ps) == 1 and ps[0].op == "append" and ps[0].anchor is None


def test_findings_to_patches_multiple_blocks() -> None:
    txt = (
        "## RESEARCH_FINDING\nURL: https://a.com\nPATCH_TARGET: ## A\n\n"
        "## RESEARCH_FINDING\nURL: https://b.com\nPATCH_TARGET: ## B\n"
    )
    ps = _research_findings_to_patches(txt)
    assert len(ps) == 2
    assert all(p.op == "insert_after" for p in ps)


def test_findings_to_patches_dedupes_repeated_blocks() -> None:
    txt = (
        "## RESEARCH_FINDING\nURL: https://a.com/x\nKEY_INSIGHT: same claim\n"
        "PATCH_TARGET: ## A\n\n"
        "## RESEARCH_FINDING\nURL: https://a.com/x\nKEY_INSIGHT: same claim\n"
        "PATCH_TARGET: ## A\n"
    )

    ps = _research_findings_to_patches(txt)

    assert len(ps) == 1


# ---------------------------------------------------------------------------
# §11 — worker contract + all-error halt
# ---------------------------------------------------------------------------

def test_phase_search_failed_all_error_no_findings() -> None:
    outs = ["I searched.\nPATCHES:\n```json\n[]\n```\nSEARCH: error"]
    assert _phase_search_failed(outs) is True


def test_phase_search_failed_false_when_any_finding() -> None:
    outs = ["## RESEARCH_FINDING\nURL: https://x\nSEARCH: ok", "nothing\nSEARCH: error"]
    assert _phase_search_failed(outs) is False


def test_phase_search_failed_false_when_search_ok() -> None:
    assert _phase_search_failed(["searched, found little\nSEARCH: ok"]) is False


def test_phase_search_failed_empty_outputs() -> None:
    assert _phase_search_failed([]) is False


def test_phase_search_failed_no_status_is_false() -> None:
    # No SEARCH status at all → don't claim a search failure.
    assert _phase_search_failed(["some normal output with no status"]) is False


class _FakeChatResult:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeClient:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.seen: str | None = None

    def chat(self, messages, tools=None):  # noqa: ANN001
        self.seen = messages[0]["content"]
        return _FakeChatResult(self.reply)


def test_plan_from_epics_builds_phases_from_epic_plan() -> None:
    """Epic planning wired: the planner LLM's EPIC_PLAN → one phase per epic."""
    epic_json = json.dumps({
        "epics": [
            {"id": "epic-1", "title": "Gather", "description": "Find articles",
             "depends_on": [], "branches": [{"id": "b1", "description": "x"}]},
            {"id": "epic-2", "title": "Write", "description": "Write report",
             "depends_on": ["epic-1"], "branches": [{"id": "b2", "description": "y"}]},
        ]
    })
    client = _FakeClient(f"Planning…\n\nEPIC_PLAN:\n```json\n{epic_json}\n```\n")
    plan_obj = _plan_from_epics("find articles and write report", client)
    assert [s.id for s in plan_obj.steps] == ["epic-1", "epic-2"]
    assert plan_obj.steps[1].depends_on == ("epic-1",)
    # the planner prompt actually reached the client
    assert "EPIC_PLAN" in (client.seen or "")


def test_plan_from_epics_falls_back_when_no_epics() -> None:
    """A planner that emits no parseable EPIC_PLAN → deterministic plan() (never break)."""
    client = _FakeClient("I cannot produce a plan.")
    plan_obj = _plan_from_epics("do something", client)
    assert len(plan_obj.steps) >= 1  # deterministic decomposer produced steps


def test_plan_from_epics_drops_dangling_and_self_deps() -> None:
    epic_json = json.dumps({
        "epics": [
            {"id": "e1", "description": "A", "depends_on": ["e1", "ghost"]},
            {"id": "e2", "description": "B", "depends_on": ["e1"]},
        ]
    })
    client = _FakeClient(f"EPIC_PLAN:\n```json\n{epic_json}\n```")
    plan_obj = _plan_from_epics("task", client)
    by_id = {s.id: s for s in plan_obj.steps}
    assert by_id["e1"].depends_on == ()          # self + dangling stripped
    assert by_id["e2"].depends_on == ("e1",)


# ---------------------------------------------------------------------------
# _parse_epic_plan
# ---------------------------------------------------------------------------

_VALID_EPIC_PLAN = json.dumps({
    "epics": [
        {
            "id": "epic-1",
            "title": "Gather",
            "description": "Search for relevant articles",
            "depends_on": [],
            "branches": [
                {"id": "b-1a", "description": "Find top loop engineering posts"},
                {"id": "b-1b", "description": "Find agent development tutorials"},
            ],
        },
        {
            "id": "epic-2",
            "title": "Synthesise",
            "description": "Compile findings into a report",
            "depends_on": ["epic-1"],
            "branches": [
                {"id": "b-2a", "description": "Draft report introduction"},
            ],
        },
    ]
})


def test_parse_epic_plan_fenced_json_block() -> None:
    text = f"Some planning text.\n\nEPIC_PLAN:\n```json\n{_VALID_EPIC_PLAN}\n```\n"
    epics = _parse_epic_plan(text)
    assert len(epics) == 2
    assert epics[0]["id"] == "epic-1"
    assert len(epics[0]["branches"]) == 2
    assert epics[1]["depends_on"] == ["epic-1"]


def test_parse_epic_plan_unfenced_json_block() -> None:
    text = f"Plan output:\n\nEPIC_PLAN:\n{_VALID_EPIC_PLAN}\n"
    epics = _parse_epic_plan(text)
    assert len(epics) == 2


def test_parse_epic_plan_missing_block_returns_empty() -> None:
    assert _parse_epic_plan("No epic plan here.") == []


def test_parse_epic_plan_invalid_json_returns_empty() -> None:
    text = "EPIC_PLAN:\n```json\n{broken json\n```\n"
    assert _parse_epic_plan(text) == []


def test_parse_epic_plan_missing_epics_key_returns_empty() -> None:
    text = 'EPIC_PLAN:\n```json\n{"phases": []}\n```\n'
    assert _parse_epic_plan(text) == []


def test_parse_epic_plan_empty_epics_list() -> None:
    text = 'EPIC_PLAN:\n```json\n{"epics": []}\n```\n'
    assert _parse_epic_plan(text) == []


# ---------------------------------------------------------------------------
# _build_planner_cot_prompt
# ---------------------------------------------------------------------------

def test_build_planner_cot_prompt_contains_goal() -> None:
    prompt = _build_planner_cot_prompt(
        goal="Find the top agent-dev articles",
        artifact_path="/workspace/artifact.md",
        artifact_summary="",
        weaknesses_block="",
    )
    assert "Find the top agent-dev articles" in prompt


def test_build_planner_cot_prompt_contains_epic_plan_marker() -> None:
    prompt = _build_planner_cot_prompt("goal", "path", "", "")
    assert "EPIC_PLAN:" in prompt


def test_build_planner_cot_prompt_contains_artifact_path() -> None:
    prompt = _build_planner_cot_prompt("goal", "/ws/artifact.md", "", "")
    assert "/ws/artifact.md" in prompt


def test_build_planner_cot_prompt_includes_weaknesses() -> None:
    prompt = _build_planner_cot_prompt("goal", "path", "", "- Missing URLs")
    assert "Missing URLs" in prompt


def test_build_planner_cot_prompt_none_weaknesses_safe() -> None:
    prompt = _build_planner_cot_prompt("goal", "path", "", "")
    assert "EPIC_PLAN:" in prompt


def test_build_planner_cot_prompt_guides_report_plan_depth() -> None:
    prompt = _build_planner_cot_prompt("write a research report", "path", "", "")

    assert "requested report size" in prompt
    assert "Compact report" in prompt
    assert "Standard report" in prompt
    assert "Large, high-impact" in prompt
    assert "planning heuristics, not hardcoded stages" in prompt
    assert "durable_board" in prompt
    assert "gateway" in prompt
    assert "A later LLM topology selector will make the final choice" in prompt


# ---------------------------------------------------------------------------
# _build_reducer_refine_prompt (DESIGN §2.2 Step 5)
# ---------------------------------------------------------------------------

def test_build_reducer_refine_prompt_has_editorial_checklist() -> None:
    prompt = _build_reducer_refine_prompt("my goal", "/ws/artifact.md")
    low = prompt.lower()
    assert "coherence" in low
    assert "redundancy" in low
    assert "conflict" in low  # resolves <!-- conflict --> markers
    assert "my goal" in prompt
    assert "/ws/artifact.md" in prompt


def test_build_reducer_refine_prompt_demands_complete_output() -> None:
    """Refine must not truncate — it asks for the full document."""
    prompt = _build_reducer_refine_prompt("g", "p")
    low = prompt.lower()
    assert "no truncation" in low or "complete" in low
    assert "merged document" in low


def test_build_reducer_refine_prompt_uses_numbered_step_cot() -> None:
    """R7: ALL agent-facing prompts must use detailed numbered-step CoT form."""
    prompt = _build_reducer_refine_prompt("g", "p")
    # Sequential numbered steps, not a flat bullet list.
    for marker in ("Step 1", "Step 2", "Step 3", "Step 4", "Step 8"):
        assert marker in prompt, f"reducer prompt missing {marker} (not CoT form)"


# ---------------------------------------------------------------------------
# _parse_patches_from_output
# ---------------------------------------------------------------------------

_PATCH_LIST = json.dumps([
    {"op": "insert_after", "anchor": "## Sources", "content": "- New source", "source": "w1"},
    {"op": "append", "anchor": None, "content": "## Conclusion\nDone.", "source": "w1"},
])


def test_parse_patches_fenced_block() -> None:
    text = f"Step 6 — my patches:\n\nPATCHES:\n```json\n{_PATCH_LIST}\n```\n"
    patches = _parse_patches_from_output(text)
    assert len(patches) == 2
    assert patches[0].op == "insert_after"
    assert patches[0].anchor == "## Sources"
    assert patches[1].op == "append"
    assert patches[1].anchor is None


def test_parse_patches_alternative_key_format() -> None:
    text = '{"patches": [{"op": "append", "anchor": null, "content": "x", "source": "w2"}]}'
    patches = _parse_patches_from_output(text)
    assert len(patches) == 1
    assert patches[0].op == "append"


def test_parse_patches_missing_block_returns_empty() -> None:
    assert _parse_patches_from_output("No PATCHES here.") == []


def test_parse_patches_invalid_json_returns_empty() -> None:
    text = "PATCHES:\n```json\n[broken\n```\n"
    assert _parse_patches_from_output(text) == []


def test_parse_patches_non_dict_items_skipped() -> None:
    text = 'PATCHES:\n```json\n[null, "string", {"op": "append", "content": "ok"}]\n```\n'
    patches = _parse_patches_from_output(text)
    assert len(patches) == 1
    assert patches[0].op == "append"


def test_parse_patches_defaults_op_to_append_when_missing() -> None:
    text = 'PATCHES:\n```json\n[{"content": "fallback"}]\n```\n'
    patches = _parse_patches_from_output(text)
    assert patches[0].op == "append"


# ---------------------------------------------------------------------------
# _parse_assigned / _dedupe_assignment (R2 — code-validated non-overlap)
# ---------------------------------------------------------------------------

def test_parse_assigned_fenced_block() -> None:
    text = (
        'ASSIGNED:\n```json\n'
        '{"agent_1": ["## Intro", "## Background"], "agent_2": ["## Results"]}\n```\n'
    )
    a = _parse_assigned(text)
    assert a == {"agent_1": ["## Intro", "## Background"], "agent_2": ["## Results"]}


def test_parse_assigned_missing_block_returns_empty() -> None:
    assert _parse_assigned("no assigned block here") == {}


def test_parse_assigned_bad_json_returns_empty() -> None:
    assert _parse_assigned("ASSIGNED:\n```json\n{broken\n```") == {}


def test_dedupe_assignment_clean_partition_no_overlap() -> None:
    assigned = {"a1": ["## Intro", "## Bg"], "a2": ["## Results"]}
    clean, overlaps = _dedupe_assignment(assigned)
    assert overlaps == []
    assert clean == assigned


def test_dedupe_assignment_first_claim_wins() -> None:
    # "## Results" claimed by both a1 and a2 → a1 (earlier) keeps it.
    assigned = {"a1": ["## Intro", "## Results"], "a2": ["## Results", "## Refs"]}
    clean, overlaps = _dedupe_assignment(assigned)
    assert overlaps == ["## Results"]
    assert clean == {"a1": ["## Intro", "## Results"], "a2": ["## Refs"]}


def test_dedupe_assignment_collapses_within_agent_repeats() -> None:
    assigned = {"a1": ["## X", "## X", "## Y"]}
    clean, overlaps = _dedupe_assignment(assigned)
    assert clean == {"a1": ["## X", "## Y"]}
    assert overlaps == ["## X"]


def test_dedupe_assignment_three_agents_one_section() -> None:
    assigned = {"a1": ["## S"], "a2": ["## S"], "a3": ["## S"]}
    clean, overlaps = _dedupe_assignment(assigned)
    assert clean == {"a1": ["## S"], "a2": [], "a3": []}
    assert overlaps == ["## S"]  # reported once


def test_loop_config_max_agents_flows_to_sizing() -> None:
    # 2026-06-27: the max-agents cap is menu-configurable — it must travel
    # from_dict → LoopConfig → SizingConfig → compute_n_agents.
    from studio.models import LoopConfig
    from agentkit.topology.sizing import compute_n_agents

    lc = LoopConfig.from_dict({"max_agents": 3, "max_tasks_per_agent": 5})
    assert lc.sizing().max_agents == 3
    assert compute_n_agents(74, lc.sizing()) == 3  # pre-fix: ceil(74/5)=15


def test_set_hill_climb_wires_sizing_into_loop_config() -> None:
    # Regression: the hill-climb endpoint used to DROP the Agent Sizing sliders
    # (they only matched the 3/5 defaults by coincidence). It must now patch
    # session.loop_config, which is what the runner reads for sizing.
    from studio.app import registry, set_hill_climb
    from agentkit.topology.sizing import compute_n_agents

    s = registry.create(
        llm_spec={"name": "haiku", "model": "m", "endpoint": "e"},
        embed_spec={}, llm_info={}, embed_info={}, mode="llm",
        budget_ceiling=None,
    )
    set_hill_climb(s.session_id, {
        "auto_improve": True, "max_agents": 3,
        "max_tasks_per_agent": 4, "min_tasks_per_agent": 2,
    })
    cfg = registry.get(s.session_id).loop_config.sizing()
    assert cfg.max_agents == 3 and cfg.max_tasks_per_agent == 4
    assert compute_n_agents(74, cfg) == 3


def test_rubric_defaults_expose_profile_templates() -> None:
    from studio.app import rubric_defaults

    defaults = rubric_defaults()

    assert defaults["report_type"] == "general"
    presets = {p["report_type"]: p for p in defaults["template_presets"]}
    assert "general" in presets
    assert "deep_technical" in presets
    assert "Practical Implementation" in presets["deep_technical"]["sections"]


def test_set_rubric_can_select_profile_template() -> None:
    from studio.app import registry, set_rubric

    s = registry.create(
        llm_spec={"name": "haiku", "model": "m", "endpoint": "e"},
        embed_spec={}, llm_info={}, embed_info={}, mode="llm",
        budget_ceiling=None,
    )
    out = set_rubric(s.session_id, {"report_type": "deep_technical"})

    cfg = out["rubric_config"]
    assert cfg["report_type"] == "deep_technical"
    assert "Practical Implementation" in cfg["template"]
    assert "Appendix B. Evidence Matrix" in cfg["template"]
