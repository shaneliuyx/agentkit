"""Tests for M8/M9 helper functions in studio.runner (DESIGN §2.2, §3, §5).

All four helpers are pure functions — no LLM, no network, no filesystem.
Tests cover:
  - _parse_epic_plan   : happy path, missing block, bad JSON, wrong shape
  - _build_planner_cot_prompt  : required structural markers present
  - _build_hub_cot_prompt      : deliverable-exists vs first-run branches
  - _parse_patches_from_output : happy path, missing block, bad JSON, multi-patch
"""

from __future__ import annotations

import json

from studio.runner import (
    _build_hub_cot_prompt,
    _build_planner_cot_prompt,
    _build_reducer_refine_prompt,
    _build_worker_cot_prompt,
    _dedupe_assignment,
    _parse_assigned,
    _parse_epic_plan,
    _parse_patches_from_output,
    _phase_search_failed,
    _plan_from_epics,
    _research_findings_to_patches,
)


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


# ---------------------------------------------------------------------------
# §11 — worker contract + all-error halt
# ---------------------------------------------------------------------------

def test_worker_prompt_patch_or_silent_contract() -> None:
    p = _build_worker_cot_prompt("task", "doc")
    low = p.lower()
    assert "patch-or-silent" in low
    assert "found nothing" in low and "no patch" in low
    assert "search: ok" in low and "search: error" in low
    assert "never rewrites" in low and "shortens" in low  # additive reducer promise


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


# ---------------------------------------------------------------------------
# _build_hub_cot_prompt
# ---------------------------------------------------------------------------

def test_build_hub_cot_prompt_with_artifact_uses_read_step() -> None:
    prompt = _build_hub_cot_prompt(
        goal="Research loop engineering",
        artifact_path="/ws/artifact.md",
        ledger_block="COMPLETED TASKS FROM PRIOR PHASES:\n(none)",
        weaknesses_block="",
        artifact_text="# Report\n## Section 1\n...",
        max_tasks_per_agent=5,
    )
    assert "existing deliverable" in prompt.lower()
    assert "DELIVERABLE_PATH" in prompt


def test_build_hub_cot_prompt_no_artifact_uses_first_run_step() -> None:
    prompt = _build_hub_cot_prompt(
        goal="Research loop engineering",
        artifact_path="/ws/artifact.md",
        ledger_block="",
        weaknesses_block="",
        artifact_text="",
        max_tasks_per_agent=5,
    )
    assert "No existing deliverable" in prompt


def test_build_hub_cot_prompt_injects_ledger_block() -> None:
    ledger = "COMPLETED TASKS FROM PRIOR PHASES:\n- [epic-1] Gather phase"
    prompt = _build_hub_cot_prompt("goal", "path", ledger, "", "", 3)
    assert "epic-1" in prompt


def test_build_hub_cot_prompt_max_tasks_per_agent() -> None:
    prompt = _build_hub_cot_prompt("goal", "path", "", "", "", max_tasks_per_agent=7)
    assert "7" in prompt


def test_build_hub_cot_prompt_contains_patches_instruction() -> None:
    prompt = _build_hub_cot_prompt("goal", "path", "", "", "", 5)
    assert "PATCHES" in prompt


def test_build_hub_cot_prompt_step5_is_section_based() -> None:
    """DESIGN §5.1 Step 5: assignment by document section, not by topic."""
    prompt = _build_hub_cot_prompt("goal", "path", "", "", "", 5)
    low = prompt.lower()
    assert "by document section" in low or "document section" in low
    assert "non-overlapping" in low
    assert "verbatim" in low
    assert "not by topic" in low


# ---------------------------------------------------------------------------
# _build_worker_cot_prompt (DESIGN §5.3)
# ---------------------------------------------------------------------------

def test_build_worker_cot_prompt_has_verbatim_anchor_rule() -> None:
    prompt = _build_worker_cot_prompt(
        task_list_for_agent="- improve ## Results",
        artifact_current_text="# Report\n## Results\n...",
    )
    low = prompt.lower()
    assert "verbatim" in low
    assert "anchor" in low
    assert "do not paraphrase" in low
    assert "PATCHES" in prompt


def test_build_worker_cot_prompt_injects_assignment_and_content() -> None:
    prompt = _build_worker_cot_prompt(
        task_list_for_agent="MYTASK-XYZ",
        artifact_current_text="MYDOC-ABC",
    )
    assert "MYTASK-XYZ" in prompt
    assert "MYDOC-ABC" in prompt


def test_build_worker_cot_prompt_forbids_file_writes() -> None:
    prompt = _build_worker_cot_prompt("t", "c")
    assert "Do NOT write to any file" in prompt


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


def test_all_agent_prompts_are_numbered_step_cot() -> None:
    """R7 (universal): every prompt builder uses numbered-step CoT."""
    builders = [
        _build_planner_cot_prompt("g", "p", "", ""),
        _build_hub_cot_prompt("g", "p", "", "", "", 5),
        _build_worker_cot_prompt("t", "c"),
        _build_reducer_refine_prompt("g", "p"),
    ]
    for prompt in builders:
        assert "Step 1" in prompt and "Step 2" in prompt


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
