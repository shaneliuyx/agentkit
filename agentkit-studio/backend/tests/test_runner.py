"""Milestone-1 smoke: the runner drives end-to-end on a FAKE client, offline.

Asserts the SPEC §4 ordering guarantee and the token-honesty behavior. No API
key, no running services.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from agentkit.types import LLMClient
from studio.models import LoopConfig
from studio.events import StudioEvent
from studio.runner import (
    Runner,
    _final_evidence_dossier,
    _final_step_instruction,
    _full_scoring_matrix,
    _prompt_scoring_matrix,
    _prune_resolved_weaknesses,
)
from studio.session import SessionRegistry


def _make_session(mode: str = "auto", budget: float | None = None):
    reg = SessionRegistry()
    return reg.create(
        llm_spec={"profile": "qwen"},
        embed_spec={},
        llm_info={"label": "qwen", "model": "Qwen-test"},
        embed_info={"label": "none", "model": "none"},
        mode=mode,
        budget_ceiling=budget,
    )


def _run(factory: Callable[..., LLMClient], **kw) -> list[StudioEvent]:
    events: list[StudioEvent] = []
    session = _make_session(**kw)
    runner = Runner(session, events.append, client_factory=factory, embedder=None)
    # Numbered list → a 2-phase linear plan (deterministic decomposer).
    runner.run("1. compare redis and postgres 2. write a recommendation")
    return events


def test_done_writes_result_file(
    fake_client_factory: Callable[..., LLMClient], tmp_path
) -> None:
    """The finished result is saved to the session workspace and `done` reports
    its absolute path, matching the result text it carries."""
    from pathlib import Path

    events: list[StudioEvent] = []
    session = _make_session()
    runner = Runner(
        session,
        events.append,
        client_factory=fake_client_factory,
        embedder=None,
        workspace_root=tmp_path,
    )
    runner.run("1. compare redis and postgres 2. write a recommendation")
    done = [e for e in events if e.EVENT_TYPE == "done"][0]
    assert done.result_path.endswith("result.md")
    assert done.scorecard_100
    assert done.scorecard_100["categories"]
    assert done.review
    assert done.review["status"] in {"NOT_REQUIRED", "REVIEW_REQUIRED"}
    saved = Path(done.result_path)
    assert saved.is_file()
    assert saved.read_text(encoding="utf-8") == done.result


def test_normal_run_writes_agent_io_log(
    fake_client_factory: Callable[..., LLMClient], tmp_path
) -> None:
    events: list[StudioEvent] = []
    session = _make_session()
    runner = Runner(
        session,
        events.append,
        client_factory=fake_client_factory,
        embedder=None,
        workspace_root=tmp_path,
    )
    runner.run("1. compare redis and postgres 2. write a recommendation")

    log = tmp_path / session.session_id / "agent_io.jsonl"
    assert log.is_file()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert any('"step": "s1"' in line for line in lines)
    assert any('"step": "run-summary"' in line for line in lines)
    assert (tmp_path / session.session_id / "io" / "s1.in.md").is_file()
    s2_input = (tmp_path / session.session_id / "io" / "s2.in.md").read_text(
        encoding="utf-8"
    )
    assert "Context from prior steps:" in s2_input
    assert "[s1]" in s2_input


def test_loop_workers_receive_section_assignments_and_weakness_guidance(
    fake_client_factory: Callable[..., LLMClient], tmp_path
) -> None:
    events: list[StudioEvent] = []
    session = _make_session()
    session.loop_config = LoopConfig(max_tasks_per_agent=1, max_agents=5)
    session.rubric_config = {
        "weights": None,
        "template": ["Executive Summary", "Limitations"],
        "scoring_matrix": [
            {"category": "Scope and research framing", "points": 40, "signal": "structure"},
            {"category": "Citation integrity", "points": 30, "signal": "verification"},
            {"category": "Readability and formatting", "points": 30, "signal": "structure"},
        ],
    }
    session.weaknesses = [
        "[## Executive Summary] missing cited takeaway",
        "[document] missing source grounding",
    ]
    runner = Runner(
        session,
        events.append,
        client_factory=fake_client_factory,
        embedder=None,
        workspace_root=tmp_path,
    )

    runner.run("gather evidence for a short report")

    spoke_input = (tmp_path / session.session_id / "io" / "s1.spoke0.in.md").read_text(
        encoding="utf-8"
    )
    plan_event = next(e for e in events if e.EVENT_TYPE == "plan")
    plan_text = "\n".join(step["description"] for step in plan_event.steps)
    assert "Unified scoring requirements for this task:" in plan_text
    assert "Reducers measure the whole artifact against the full scoring matrix." in plan_text
    assert "ASSIGNED SECTIONS:" in spoke_input
    assert "## Executive Summary" in spoke_input
    assert "Run web_search THEN web_fetch" in spoke_input
    assert "OUTPUT FORMAT (critical)" in spoke_input
    assert "Unified scoring requirements for this task:" not in spoke_input
    assert "SCORING REQUIREMENTS FOR THIS SCOPE:" in spoke_input
    assert "Scope and research framing" in spoke_input
    assert "Readability and formatting" in spoke_input
    assert "Citation integrity" not in spoke_input
    assert "missing cited takeaway" in spoke_input
    assert "missing source grounding" in spoke_input
    assert "create and populate" in spoke_input
    assert "the strongest case for it" not in spoke_input
    assert "## (intro)" not in spoke_input


def test_scoring_matrix_helpers_keep_reducers_full() -> None:
    session = _make_session()
    full = [
        {"category": "Scope and research framing", "points": 70, "signal": "structure"},
        {"category": "Citation integrity", "points": 30, "signal": "verification"},
    ]
    remaining = [
        {"category": "Citation integrity", "points": 30, "signal": "verification"},
    ]
    session.rubric_config = {
        "scoring_matrix": full,
        "remaining_scoring_matrix": remaining,
    }

    assert _prompt_scoring_matrix(session) == remaining
    assert _full_scoring_matrix(session) == full


def test_full_scoring_matrix_falls_back_to_profile_template_default() -> None:
    session = _make_session()
    session.rubric_config = {}

    rules = _full_scoring_matrix(session)

    assert rules
    assert any(rule["category"] == "Citation integrity" for rule in rules)


def test_section_assignment_queue_fetches_all_files_despite_agent_cap(
    fake_client_factory: Callable[..., LLMClient], tmp_path
) -> None:
    events: list[StudioEvent] = []
    session = _make_session()
    session.loop_config = LoopConfig(max_tasks_per_agent=5, max_agents=1)
    session.rubric_config = {
        "weights": None,
        "template": ["Executive Summary", "References"],
    }
    runner = Runner(
        session,
        events.append,
        client_factory=fake_client_factory,
        embedder=None,
        workspace_root=tmp_path,
    )

    runner.run("gather evidence for a short report")

    io_dir = tmp_path / session.session_id / "io"
    first = (io_dir / "s1.spoke0.in.md").read_text(encoding="utf-8")
    second = (io_dir / "s1.spoke1.in.md").read_text(encoding="utf-8")
    # Cold-start runs now bootstrap the template skeleton (create == improve), so
    # every spoke prompt legitimately carries ALL section headings in the target-
    # doc body. Assignment ISOLATION lives in the ASSIGNMENT QUEUE FETCH block:
    # each agent's fetch names only its own section.
    def _assignment_block(prompt: str) -> str:
        return prompt.split("ASSIGNMENT QUEUE FETCH:", 1)[1]

    assert "ASSIGNMENT QUEUE FETCH:" in first
    assert "## Executive Summary" in _assignment_block(first)
    assert "## References" not in _assignment_block(first)
    assert "ASSIGNMENT QUEUE FETCH:" in second
    assert "## References" in _assignment_block(second)
    assert "## Executive Summary" not in _assignment_block(second)
    queue = tmp_path / session.session_id / "sections" / "assignment_queue.json"
    assert queue.read_text(encoding="utf-8").strip() == "[]"


def test_active_template_tracks_added_sections_without_removing_original() -> None:
    from studio.runner import (
        _active_report_title,
        _active_template,
        _scoring_template,
        _update_active_template_from_artifact,
    )

    session = _make_session()
    session.rubric_config = {
        "weights": None,
        "template": ["Executive Summary", "References"],
        "scoring_template": ["Executive Summary", "References"],
    }
    artifact = "# Catalog Control for Agent Skills\n\n## Executive Summary\nDone.\n\n## New Risk Analysis\nAdded.\n"

    active = _update_active_template_from_artifact(session, artifact)

    assert active == ["Executive Summary", "References", "New Risk Analysis"]
    assert _active_template(session) == active
    assert _scoring_template(session) == ["Executive Summary", "References"]
    assert _active_report_title(session) == "Catalog Control for Agent Skills"


def test_section_writeback_assembles_artifact_from_section_files(tmp_path) -> None:
    from studio.runner import (
        _update_active_template_from_artifact,
        _write_artifact_through_sections,
    )

    session = _make_session()
    session.rubric_config = {
        "weights": None,
        "template": ["Executive Summary", "References"],
    }
    artifact = (
        "# Report\n\n"
        "## Executive Summary\nSpecific summary.\n\n"
        "## New Section\nNew sourced content.\n"
    )

    assembled = _write_artifact_through_sections(
        session,
        tmp_path,
        artifact,
        "Write a research report about catalog management for local and remote agent loops.",
    )
    active = _update_active_template_from_artifact(session, assembled)

    root = tmp_path / session.session_id
    assert (root / "artifact.md").read_text(encoding="utf-8") == assembled
    assert (root / "sections" / "001-executive-summary.md").is_file()
    assert (root / "sections" / "002-references.md").is_file()
    assert (root / "sections" / "003-new-section.md").is_file()
    assert assembled.startswith("# Catalog management for local and remote agent loops\n\n")
    assert "## New Section" in assembled
    assert active == ["Executive Summary", "References", "New Section"]


def test_pick_scored_source_prefers_canonical_artifact(tmp_path) -> None:
    from studio.runner import _pick_scored_source

    ws = tmp_path / "s_x"
    ws.mkdir()
    (ws / "artifact.md").write_text("# Canonical report\n" + "body " * 50)
    (ws / "notes.md").write_text("# Bigger side file\n" + "noise " * 500)
    picked = _pick_scored_source(ws / "artifact.md", "short return")
    assert picked.startswith("# Canonical report")


def test_pick_scored_source_falls_back_to_agent_named_md(tmp_path) -> None:
    """Cold-start auto-mode runs never bootstrap artifact.md (skeleton is gated on
    mode=="llm") — the agent writes the report under its OWN filename. The
    finalizer must score that file, not the phase's short status return."""
    from studio.runner import _pick_scored_source

    ws = tmp_path / "s_x"
    ws.mkdir()
    report = "# Agent Frameworks Research Report\n" + "finding sentence. " * 200
    (ws / "agent_frameworks_research_report.md").write_text(report)
    picked = _pick_scored_source(ws / "artifact.md", "Report complete, see file.")
    assert picked == report


def test_pick_scored_source_skips_result_md_and_headingless_files(tmp_path) -> None:
    """codex P2: with artifact.md absent, the largest-md fallback must not pick a
    stale per-epoch result.md archive or a heading-less scratch dump over the
    agent's actual (report-like) deliverable."""
    from studio.runner import _pick_scored_source

    ws = tmp_path / "s_x"
    ws.mkdir()
    report = "# RAG Serving Stack\n\n## Components\n" + "finding sentence. " * 100
    (ws / "rag_serving_report.md").write_text(report)
    (ws / "result.md").write_text("# Stale prior-epoch archive\n" + "old " * 2000)
    (ws / "scratch.md").write_text("raw notes without any heading " * 500)
    picked = _pick_scored_source(ws / "artifact.md", "done, see file")
    assert picked == report


def test_pick_scored_source_never_prefers_shorter_file(tmp_path) -> None:
    from studio.runner import _pick_scored_source

    ws = tmp_path / "s_x"
    ws.mkdir()
    (ws / "stub.md").write_text("# stub")
    long_return = "# Full report\n" + "rich body. " * 100
    assert _pick_scored_source(ws / "artifact.md", long_return) == long_return
    # And with no files at all, the in-memory return survives untouched.
    assert _pick_scored_source(tmp_path / "s_none" / "artifact.md", long_return) == long_return


def test_section_writeback_prefers_active_report_title(tmp_path) -> None:
    from studio.runner import _write_artifact_through_sections

    session = _make_session()
    session.rubric_config = {
        "weights": None,
        "template": ["Executive Summary"],
        "active_title": "Managed Catalogs for Agent Skills",
    }
    artifact = "# Research Report\n\n## Executive Summary\nSpecific summary.\n"

    assembled = _write_artifact_through_sections(
        session,
        tmp_path,
        artifact,
        "Write a research report about a different topic.",
    )

    assert assembled.startswith("# Managed Catalogs for Agent Skills\n\n")


def test_llm_template_run_bootstraps_artifact_for_reducer(
    fake_client_factory: Callable[..., LLMClient], tmp_path
) -> None:
    events: list[StudioEvent] = []
    session = _make_session(mode="llm")
    session.rubric_config = {
        "weights": None,
        "template": ["Executive Summary", "Source References"],
    }
    runner = Runner(
        session,
        events.append,
        client_factory=fake_client_factory,
        embedder=None,
        workspace_root=tmp_path,
    )

    runner.run("write a sourced report")

    artifact = tmp_path / session.session_id / "artifact.md"
    assert artifact.is_file()
    text = artifact.read_text(encoding="utf-8")
    assert "## Executive Summary" in text
    assert "## Source References" in text


def test_event_order(fake_client_factory: Callable[..., LLMClient]) -> None:
    """session → plan → topology → graph → (per phase ...) → budget? → verify → done."""
    events = _run(fake_client_factory)
    types = [e.EVENT_TYPE for e in events]

    # Prefix is exact.
    assert types[:4] == ["session", "plan", "topology", "graph"], types

    # Terminal: done is last. Ordering: verify → loopdoctor → hill_climb → metrics → done.
    assert types[-1] == "done", types
    assert types[-2] == "metrics", types
    # The Loop Doctor audit is emitted exactly once, after verify, before done.
    assert types.count("loopdoctor") == 1
    assert types.index("verify") < types.index("loopdoctor") < types.index("hill_climb") < types.index("metrics") < types.index("done")

    # No event precedes session; nothing follows done.
    assert types.count("session") == 1
    assert types.count("done") == 1

    # Per-phase events appear and phase_start precedes phase_done for each step.
    assert "phase_start" in types and "phase_done" in types
    assert types.index("phase_start") < types.index("phase_done")

    # The router frame for a phase comes after that phase's start.
    first_start = types.index("phase_start")
    assert "router" in types[first_start:]


def test_two_phases_each_have_start_and_done(fake_client_factory) -> None:
    events = _run(fake_client_factory)
    starts = [e for e in events if e.EVENT_TYPE == "phase_start"]
    dones = [e for e in events if e.EVENT_TYPE == "phase_done"]
    assert len(starts) == 2  # the 2-step plan
    assert len(dones) == 2
    # phase_done carries the StepRun fields.
    assert dones[0].topology in {"single", "star", "mesh", "pipeline"}
    assert dones[0].n_agents >= 1


def test_token_frames_and_cumulative(fake_client_factory, fake_client) -> None:
    """token frames fire during phases; cumulative total reconciles to calls*5."""
    events = _run(fake_client_factory)
    tokens = [e for e in events if e.EVENT_TYPE == "token"]
    assert tokens, "expected token frames"
    done = [e for e in events if e.EVENT_TYPE == "done"][0]
    # Phase tokens must be > 0 (some pipeline stages track tokens outside phase loop).
    assert done.total_tokens > 0
    assert done.total_tokens == tokens[-1].cumulative["total"]


def test_done_reports_real_wall_time(fake_client_factory) -> None:
    """done.wall_s is the real elapsed run time, not a hardcoded 0.0 (honesty)."""
    events = _run(fake_client_factory)
    done = [e for e in events if e.EVENT_TYPE == "done"][0]
    assert done.wall_s > 0.0, done.wall_s
    # And it surfaces through the SSE payload the frontend reads.
    assert done.payload()["wall_s"] > 0.0


def test_metrics_event_emits_before_done(fake_client_factory) -> None:
    events = _run(fake_client_factory)
    types = [e.EVENT_TYPE for e in events]
    metrics = [e for e in events if e.EVENT_TYPE == "metrics"]
    done = [e for e in events if e.EVENT_TYPE == "done"][0]

    assert metrics
    assert types.index("metrics") < types.index("done")
    assert metrics[-1].metrics["stop_reason"] == "validation_passed"
    assert done.metrics == metrics[-1].metrics


def test_estimated_flag_sticky_offline(fake_client_factory) -> None:
    """A raw fake client (not a StudioChatClient) reports no usage split, so its
    run_plan tokens are reconciled as ESTIMATED output tokens — flipping the
    run's sticky ~ flag. This is the honest signal for a backend with no usage
    telemetry (SPEC §7). The split must never exceed the total."""
    events = _run(fake_client_factory)
    done = [e for e in events if e.EVENT_TYPE == "done"][0]
    assert done.estimated is True
    assert done.input + done.output == done.total_tokens


def test_verify_runs_offline(fake_client_factory) -> None:
    """The verify panel produces a finding for the fake's uncited claim."""
    events = _run(fake_client_factory)
    verify = [e for e in events if e.EVENT_TYPE == "verify"][0]
    # "The answer is 42." is an uncited claim → surfaced.
    assert verify.uncited, verify.uncited


def test_all_panel_events_present(fake_client_factory) -> None:
    """All 7 panel event types appear at least once (comprehensive build)."""
    events = _run(fake_client_factory)
    types = {e.EVENT_TYPE for e in events}
    for panel_type in ("memory", "selfimprove", "evolve", "gate", "dag", "verify", "router"):
        assert panel_type in types, f"missing panel event: {panel_type}"


def test_cancel_stops_before_phases(fake_client_factory) -> None:
    """A pre-cancelled session emits no phase_start and a cancelled done."""
    events: list[StudioEvent] = []
    session = _make_session()
    session.request_cancel()
    runner = Runner(session, events.append, client_factory=fake_client_factory, embedder=None)
    runner.run("1. step one 2. step two")
    types = [e.EVENT_TYPE for e in events]
    assert "phase_start" not in types
    done = [e for e in events if e.EVENT_TYPE == "done"][0]
    assert done.cancelled is True


def test_budget_exceeded_emits_budget(fake_client) -> None:
    """A tight ceiling on a fan-out phase trips BudgetExceeded → budget frame."""

    def factory(_on_usage) -> LLMClient:
        return fake_client

    events: list[StudioEvent] = []
    session = _make_session(budget=1.0)  # 1-token ceiling, fake charges 5/call
    runner = Runner(session, events.append, client_factory=factory, embedder=None)
    runner.run("compare redis and postgres")  # MESH → fan-out → charges > 1
    budget = [e for e in events if e.EVENT_TYPE == "budget"]
    assert budget and budget[0].exceeded is True


# --- M7 Wave 1 integration: seeded run + web-search tool loop -----------------

def test_seeded_run_emits_loop_seed(fake_client_factory) -> None:
    """A session seeded from a loop emits loop_seed and plans from the seed steps."""
    events: list[StudioEvent] = []
    session = _make_session()
    session.tools_enabled = False  # isolate the seeding behavior
    session.seed(
        "overnight-docs-sweep",
        [
            {"id": "s1", "description": "review changes", "depends_on": [], "role": "engineering"},
            {"id": "s2", "description": "fix docs", "depends_on": ["s1"], "role": "engineering"},
        ],
    )
    runner = Runner(session, events.append, client_factory=fake_client_factory, embedder=None)
    runner.run("update the docs")
    seed = [e for e in events if e.EVENT_TYPE == "loop_seed"]
    assert seed and seed[0].loop_id == "overnight-docs-sweep"
    # The plan reflects the seed (2 steps), not cold decomposition of the prompt.
    plan_evt = [e for e in events if e.EVENT_TYPE == "plan"][0]
    assert [s["id"] for s in plan_evt.steps] == ["s1", "s2"]


def test_section_reducer_emits_patches_no_full_regen() -> None:
    """Lever 3: the reducer asks for PATCHES (not a full-document regen) and applies
    them MECHANICALLY via reduce_patches, so a completion cap cannot truncate the
    artifact. The seed is preserved verbatim and the patch content is woven in."""
    from agentkit.types import ChatResult
    from studio.runner import _make_section_reducer

    captured: dict = {}

    class _C:
        def chat(self, messages, tools=None) -> ChatResult:
            captured["prompt"] = messages[-1]["content"]
            return ChatResult(
                text='PATCHES:\n```json\n'
                     '[{"op":"insert_after","anchor":"## Intro",'
                     '"content":"\\n\\nNew grounded sentence (https://x.example)."}]\n```',
                total_tokens=12,
            )

    reduce = _make_section_reducer(
        _C(), "## Intro\nseed text", ["## Intro: missing citation"]
    )
    text, tok = reduce(["worker found source X"])

    assert tok == 12
    # seed preserved (the patch weaves content BETWEEN heading and body — Lever 2)
    assert "## Intro" in text and "seed text" in text
    assert "New grounded sentence" in text                # patch applied mechanically
    p = captured["prompt"]
    assert "Do NOT re-emit" in p                           # patch contract, not full regen
    assert "PATCHES" in p
    assert "PATCH CONTENT CONTRACT" in p
    assert "content MUST NOT contain '#'" in p
    assert "output an empty JSON list" in p
    assert "## Intro" in p and "missing citation" in p     # artifact + weakness checklist
    assert "worker found source X" in p                    # worker drafts included


def test_section_reducer_drops_full_document_llm_patch() -> None:
    """A malformed reducer PATCHES block that echoes a whole document is rejected before
    reduce_patches can stack duplicate headings or skeleton placeholders."""
    from agentkit.types import ChatResult
    from studio.runner import _make_section_reducer

    class _BadPatch:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(
                text='PATCHES:\n```json\n'
                     '[{"op":"insert_after","anchor":"## Executive Summary",'
                     '"content":"\\n\\n# Bad Echo\\n\\n## Executive Summary\\n'
                     '_(pending - needs sourced content)_\\n'
                     'Bad echoed prose (https://bad.example)."}]\n```',
                total_tokens=4,
            )

    seed = "# Report\n\n## Executive Summary\nseed text"
    text, _ = _make_section_reducer(_BadPatch(), seed, [])(["worker draft without findings"])

    assert text == seed
    assert "# Bad Echo" not in text
    assert "pending - needs sourced content" not in text


def test_section_reducer_block_separates_llm_patch_content() -> None:
    """Valid LLM patches must not glue prose onto the heading line; otherwise the
    next writeback sees a different section identity and rejects the improvement."""
    from agentkit.types import ChatResult
    from studio.runner import _make_section_reducer

    class _TightPatch:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(
                text='PATCHES:\n```json\n'
                     '[{"op":"insert_after","anchor":"## Executive Summary",'
                     '"content":"Agent skills need governance (https://x.example)."}]\n```',
                total_tokens=4,
            )

    seed = "# Report\n\n## Executive Summary\n_(pending - needs sourced content)_"
    text, _ = _make_section_reducer(_TightPatch(), seed, [])(["worker draft"])

    assert "## Executive Summary\n\nAgent skills need governance" in text
    assert "## Executive SummaryAgent" not in text


def test_section_reducer_drops_duplicate_llm_source_in_target_section() -> None:
    """Reducer LLM patches must not keep adding the same source to the same section."""
    from agentkit.types import ChatResult
    from studio.runner import _make_section_reducer

    class _DuplicateSourcePatch:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(
                text='PATCHES:\n```json\n'
                     '[{"op":"insert_after","anchor":"## Key Findings",'
                     '"content":"Repeated source claim (https://x.example/a)."}]\n```',
                total_tokens=4,
            )

    seed = "# Report\n\n## Key Findings\nExisting claim (https://x.example/a).\n"
    text, _ = _make_section_reducer(_DuplicateSourcePatch(), seed, [])(["worker draft"])

    assert "Existing claim (https://x.example/a)." in text
    assert "Repeated source claim" not in text


def test_section_reducer_allows_same_llm_source_in_different_section() -> None:
    """A source can still support different sections; the duplicate guard is section-local."""
    from agentkit.types import ChatResult
    from studio.runner import _make_section_reducer

    class _CrossSectionPatch:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(
                text='PATCHES:\n```json\n'
                     '[{"op":"insert_after","anchor":"## Evidence and Analysis",'
                     '"content":"Evidence detail (https://x.example/a)."}]\n```',
                total_tokens=4,
            )

    seed = (
        "# Report\n\n"
        "## Key Findings\nExisting claim (https://x.example/a).\n\n"
        "## Evidence and Analysis\n_(pending - needs sourced content)_"
    )
    text, _ = _make_section_reducer(_CrossSectionPatch(), seed, [])(["worker draft"])

    assert "Evidence detail (https://x.example/a)" in text


def test_section_reducer_uses_floor_when_llm_patch_is_invalid() -> None:
    """Invalid LLM patches do not make the phase passive: grounded worker findings still
    become scoped deterministic patches."""
    from agentkit.types import ChatResult
    from studio.runner import _make_section_reducer

    class _BadPatch:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(
                text='PATCHES:\n```json\n'
                     '[{"op":"append","anchor":null,'
                     '"content":"# Echo\\n\\n## Findings\\n_(pending - needs sourced content)_"}]\n```',
                total_tokens=4,
            )

    seed = "# Report\n\n## Findings\n_(pending - needs sourced content)_"
    draft = (
        "RESEARCH_FINDING:\nARTICLE_TITLE: Grounded\n"
        "URL: https://grounded.example/report\nPATCH_TARGET: ## Findings\n"
        "WHY: Reducers need scoped evidence patches.\n"
    )
    text, _ = _make_section_reducer(_BadPatch(), seed, [])([draft])

    assert "# Echo" not in text
    assert "grounded.example/report" in text
    assert "Reducers need scoped evidence patches" in text


def test_section_reducer_deterministic_floor_from_findings() -> None:
    """Lever 3 floor: even when the model emits NO usable PATCHES, the reducer still
    folds the workers' own RESEARCH_FINDING blocks in as additive patches — a phase
    always makes grounded progress, never a no-op."""
    from agentkit.types import ChatResult
    from studio.runner import _make_section_reducer

    class _Empty:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(text="no patches here", total_tokens=3)

    # '## Findings' (not a source-selection heading) so the F5 ranking pass doesn't replace it —
    # this test isolates the deterministic floor, not F5.
    reduce = _make_section_reducer(_Empty(), "## Findings\n_(pending)_", [])
    draft = (
        "## RESEARCH_FINDING\nARTICLE_TITLE: Loop\nURL: https://addy.example/loop\n"
        "PATCH_TARGET: ## Findings\nCLAIM: Verifier is the bottleneck.\n"
    )
    text, _ = reduce([draft])
    assert "## Findings" in text and "_(pending)_" in text  # seed kept (woven between)
    assert "addy.example/loop" in text                      # finding woven in (no LLM patch)
    assert "Verifier is the bottleneck" in text


def test_apply_ranking_replaces_source_section_offline() -> None:
    """F5: _apply_ranking replaces the source-selection section with the honest split table.
    Blog-only sources → no fetchable metric → NO network/cache file I/O, all 'reported'."""
    from agentkit.artifacts.types import Finding
    from studio.runner import _apply_ranking
    doc = "# R\n\n## Source Selection\nold https://blog.example/a\n\n## Other\nkeep me\n"
    findings = [Finding(url="https://blog.example/a", title="Blog A")]
    out = _apply_ranking(doc, findings)
    assert "Popularity evidence." in out                # methodology note
    assert "Reported / unranked" in out                 # split presentation
    assert "no public engagement metric" in out         # blog honestly marked, no fabrication
    assert "## Other\nkeep me" in out                   # other sections untouched
    assert _apply_ranking(doc, []) == doc               # no findings → unchanged
    assert _apply_ranking("# R\n\n## Intro\nx\n", findings) == "# R\n\n## Intro\nx\n"  # no target


def test_section_reducer_demotes_missing_anchor_no_conflict_marker() -> None:
    """The throughput fix lands findings whose PATCH_TARGET heading isn't in the doc;
    reduce_patches would emit '<!-- conflict -->' markers (live: 13 in one phase). The
    reducer demotes a missing-anchor insert to a clean append — no markers leak in."""
    from agentkit.types import ChatResult
    from studio import tools
    from studio.runner import _make_section_reducer
    tools._fetch_cache.clear()
    tools._fetch_cache["https://real.example|"] = ("agents loop until done", 20)

    class _Empty:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(text="no patches", total_tokens=1)

    seed = "# Doc\n\n## Intro\nbody"
    draft = (
        "RESEARCH_FINDING:\nARTICLE_TITLE: R\nURL: https://real.example\n"
        "PATCH_TARGET: ## Nonexistent Section\nWHY: it matters.\n"
        "QUOTE: agents loop until done\n"
    )
    try:
        text, _ = _make_section_reducer(_Empty(), seed, [])([draft])
        assert "conflict" not in text.lower()        # no conflict marker leaked
        assert "## Nonexistent Section" in text       # missing PATCH_TARGET becomes a section
        assert "real.example" in text                # finding appended cleanly
        assert "## Intro\nbody" in text              # seed intact
    finally:
        tools._fetch_cache.clear()


# --- Per-phase requirement compliance wiring (additive to entries 167-170) -------
# Phase 1 of every epoch gets a PROACTIVE requirement notice; phases 2..N verify the
# partial artifact and inject only what is STILL unaddressed. Both are injected ONLY
# into the goal-aware reducer prompt, never spoke workers. Mirrors the existing
# _repair_clause / _relevance_repair_clause test structure.


def test_phase1_requirement_notice_lists_all_groups_or_empty() -> None:
    """The phase-1 proactive notice lists EVERY stated requirement group (single +
    OR alternatives rendered as 'X (or alternatively: Y)'), and is empty when the
    task stated no explicit checkable requirement."""
    from studio.runner import _phase1_requirement_notice

    reqs = [
        ["include example code", "include a design architecture"],
        ["cite at least 3 sources"],
    ]
    notice = _phase1_requirement_notice(reqs)
    assert "STATED TASK REQUIREMENTS" in notice
    assert "cite at least 3 sources" in notice
    assert "include example code" in notice
    assert "or alternatively: include a design architecture" in notice
    # No requirements → nothing injected (a task with no explicit checkable ask).
    assert _phase1_requirement_notice([]) == ""
    assert _phase1_requirement_notice(None) == ""


def test_phase1_notice_reaches_reducer_prompt() -> None:
    """A phase-1 reducer prompt build includes the full requirement list when
    requirements exist (the proactive path)."""
    from agentkit.types import ChatResult
    from studio.runner import _make_section_reducer, _phase1_requirement_notice

    captured: dict = {}

    class _C:
        def chat(self, messages, tools=None) -> ChatResult:
            captured["prompt"] = messages[-1]["content"]
            return ChatResult(text="PATCHES:\n```json\n[]\n```", total_tokens=1)

    notice = _phase1_requirement_notice([["include a diagram"], ["cite at least 3 sources"]])
    _make_section_reducer(
        _C(), "## Intro\nseed text", [], requirement_clause=notice
    )(["worker draft"])
    p = captured["prompt"]
    assert "STATED TASK REQUIREMENTS" in p
    assert "include a diagram" in p
    assert "cite at least 3 sources" in p


def test_per_phase_clause_lists_only_still_outstanding() -> None:
    """Phase-2+ verify-and-correct: a group already satisfied by the partial artifact
    is NOT re-mentioned; only the genuinely-unaddressed hard miss (and any unmet
    OR-sibling opportunity of an already-satisfied group) is surfaced."""
    from studio.runner import _per_phase_compliance_repair_clause

    # Branches (flat): 1='include example code' 2='include a design architecture'
    # (group 0, an OR), 3='cite at least 3 sources' (group 1).
    reqs = [["include example code", "include a design architecture"],
            ["cite at least 3 sources"]]

    class _Verifier:
        def __init__(self, reply): self.reply = reply
        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            return ChatResult(text=self.reply, total_tokens=5)

    # Group 0 satisfied via branch 1 (branch 2 unmet → opportunity); group 1 satisfied.
    reply = ("REQUIREMENT 1: SATISFIED\nREQUIREMENT 2: NOT_SATISFIED\n"
             "REQUIREMENT 3: SATISFIED")
    clause = _per_phase_compliance_repair_clause(
            _Verifier(reply), reqs, "doc with code:\n```python\nrun()\n```\n")
    assert "STATED REQUIREMENTS NOT YET ADDRESSED" in clause
    # Group 1 is satisfied → its requirement is NOT re-mentioned as a hard miss.
    assert "none of the stated alternatives" not in clause
    assert "not satisfied: 'cite at least 3 sources'" not in clause
    # Only the unmet OR-sibling of the already-satisfied group 0 is surfaced.
    assert "include a design architecture" in clause

    # Everything satisfied (both branches of group 0 + group 1) → empty clause.
    # The architecture branch needs a REAL mermaid block to count as genuinely
    # satisfied (diagram-shape gate, entry 176) — a bare SATISFIED verdict alone
    # is no longer enough for diagram-shaped phrasing.
    all_sat = ("REQUIREMENT 1: SATISFIED\nREQUIREMENT 2: SATISFIED\n"
               "REQUIREMENT 3: SATISFIED")
    doc_with_diagram = "doc ```mermaid\ngraph TD\nA-->B\n``` describing the architecture"
    assert _per_phase_compliance_repair_clause(_Verifier(all_sat), reqs, doc_with_diagram) == ""


def test_per_phase_clause_fails_open_on_verifier_error() -> None:
    """A phase-level verification failure fails open: no clause, no crash — so the
    phase proceeds with no repair-clause injected that turn."""
    from studio.runner import _make_section_reducer, _per_phase_compliance_repair_clause

    class _Boom:
        def chat(self, *a, **k):
            raise RuntimeError("verifier down")

    clause = _per_phase_compliance_repair_clause(
        _Boom(), [["cite at least 3 sources"]], "doc"
    )
    assert clause == ""

    # And an empty clause injects nothing into the reducer prompt.
    captured: dict = {}

    class _C:
        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            captured["prompt"] = messages[-1]["content"]
            return ChatResult(text="PATCHES:\n```json\n[]\n```", total_tokens=1)

    _make_section_reducer(_C(), "## Intro\nbody", [], requirement_clause=clause)(["d"])
    assert "STATED TASK REQUIREMENTS" not in captured["prompt"]


def test_quote_in_cache_substring_and_whitespace() -> None:
    """Lever 1 guard: a verbatim substring of a cached fetched page is grounded
    (whitespace differences from markdown re-wrap still match); an absent quote and
    a too-short quote are not grounded."""
    from studio import tools
    tools._fetch_cache.clear()
    tools._fetch_cache["https://a|"] = ("Agents loop until a goal is met,\nthen stop.", 40)
    try:
        assert tools._quote_in_cache("Agents loop until a goal is met, then stop.")
        assert tools._quote_in_cache("loop until a goal is met")
        assert not tools._quote_in_cache("this sentence is nowhere on the page")
        assert not tools._quote_in_cache("tiny")          # < _MIN_QUOTE_CHARS → no signal
    finally:
        tools._fetch_cache.clear()


def test_quote_in_cache_fuzzy_edge_words() -> None:
    """Fuzzy match: a quote with a dropped/added word at the edges still verifies (a
    >=70% contiguous verbatim run proves the page was read), but a pure paraphrase with
    no long verbatim run does not. This is why Lever-2 verbatim quotes can survive merge
    despite the model's imperfect reconstruction."""
    from studio import tools
    tools._fetch_cache.clear()
    tools._fetch_cache["https://p|"] = (
        "Rather than personally inspecting what the agents produce, we make them better.", 80
    )
    try:
        # extra leading word + dropped trailing word — core run still verbatim
        assert tools._quote_in_cache(
            "So rather than personally inspecting what the agents produce"
        )
        # pure paraphrase, no long verbatim run → not verified
        assert not tools._quote_in_cache(
            "instead of reviewing agent output ourselves we improve the agents"
        )
    finally:
        tools._fetch_cache.clear()


def test_findings_to_patches_drops_unfetched_url() -> None:
    """Lever 1 (URL is the grounding oracle): with the fetch cache populated, a finding
    whose URL was never fetched is dropped as fabricated; a finding whose URL IS in the
    cache is kept, its CLAIM woven, and its verbatim QUOTE included when the quote really
    appears on the fetched page."""
    from studio import tools
    from studio.runner import _research_findings_to_patches
    tools._fetch_cache.clear()
    tools._fetch_cache["https://real.example|"] = (
        "The real article says agents self-improve over runs.", 60
    )
    real = (
        "## RESEARCH_FINDING\nARTICLE_TITLE: Real\nURL: https://real.example\n"
        "PATCH_TARGET: ## Sources\nCLAIM: Agents improve.\n"
        "QUOTE: agents self-improve over runs\n"
    )
    fake = (
        "## RESEARCH_FINDING\nARTICLE_TITLE: Fake\nURL: https://fake.example\n"
        "PATCH_TARGET: ## Sources\nCLAIM: Made up.\n"
        "QUOTE: this text is nowhere in any fetched page\n"
    )
    try:
        patches = _research_findings_to_patches(real + fake)
        assert len(patches) == 1                              # fake URL (unfetched) dropped
        assert "real.example" in patches[0].content
        assert "Agents improve" in patches[0].content         # CLAIM woven (Lever 2)
        assert "agents self-improve over runs" in patches[0].content  # verbatim quote woven
    finally:
        tools._fetch_cache.clear()


def test_findings_to_patches_keeps_fetched_url_omits_unverifiable_quote() -> None:
    """Lever 1: a fetched URL with an imperfectly-reconstructed QUOTE is KEPT (URL is
    the oracle, not the quote — the live Martin Fowler regression where a 1-word
    mismatch dropped a real source), but the verbatim quote clause is omitted because
    that exact text is not on the page."""
    from studio import tools
    from studio.runner import _research_findings_to_patches
    tools._fetch_cache.clear()
    tools._fetch_cache["https://fowler.example|"] = (
        "The human in the loop must verify the output and understand the change.", 70
    )
    finding = (
        "## RESEARCH_FINDING\nARTICLE_TITLE: Fowler\nURL: https://fowler.example\n"
        "PATCH_TARGET: ## Sources\nCLAIM: Humans verify agent output.\n"
        "QUOTE: the human in the loop needs to verify what the AI is doing\n"  # paraphrased
    )
    try:
        patches = _research_findings_to_patches(finding)
        assert len(patches) == 1                              # kept on URL grounding
        assert "fowler.example" in patches[0].content
        assert "Humans verify agent output" in patches[0].content
        assert "The source states" not in patches[0].content  # unverifiable quote omitted
    finally:
        tools._fetch_cache.clear()


def test_prefetch_url_rejects_non_http_and_hits_cache() -> None:
    """Fetch-density helper: a non-http URL is never fetched; a URL already cached
    returns True with NO network call (so prefetch is test-safe when pre-cached)."""
    from studio import tools
    tools._fetch_cache.clear()
    try:
        assert tools.prefetch_url("(none)") is False
        assert tools.prefetch_url("") is False
        tools._fetch_cache["https://cached.example|"] = ("page text", 9)
        assert tools.prefetch_url("https://cached.example") is True   # cache hit, no net
    finally:
        tools._fetch_cache.clear()


def test_prefetch_cited_extracts_dedups_and_caps() -> None:
    """The reducer prefetch step extracts cited URLs from drafts, dedups them, and is
    bounded by the limit. Always attempts prefetch regardless of the reducer's own
    cache state (Codex review, 2026-07-03): the old empty-cache no-op guard assumed
    an empty cache always means fail-open grounding, but the reducer's own client.chat()
    call can populate its local cache with UNRELATED entries first — cache_active then
    becomes True with none of those entries matching the real cited URLs, so grounding
    drops everything unless prefetch runs anyway."""
    from studio import tools
    from studio.runner import _prefetch_cited
    tools._fetch_cache.clear()
    # pre-cache the URLs so prefetch is a cache-hit (no network), then count
    for u in ("https://x.example", "https://y.example", "https://z.example"):
        tools._fetch_cache[f"{u}|"] = ("p", 1)
    drafts = [
        "RESEARCH_FINDING:\nURL: https://x.example\nURL: https://y.example\n",
        "RESEARCH_FINDING:\nURL: https://z.example\nURL: https://x.example\n",  # dup x
    ]
    try:
        assert _prefetch_cited(drafts, limit=2) == 2       # 3 unique, capped at 2
    finally:
        tools._fetch_cache.clear()


def test_prefetch_cited_runs_even_with_empty_starting_cache() -> None:
    """Prefetch must not skip just because the cache is empty when it starts —
    that early-return regressed real citations once the reducer's own cache could
    become non-empty-but-irrelevant later in the same call (Codex review)."""
    from studio import tools
    from studio.runner import _prefetch_cited
    tools._fetch_cache.clear()
    drafts = ["RESEARCH_FINDING:\nURL: https://cached.example\n"]
    tools._fetch_cache["https://cached.example|"] = ("p", 1)  # cache-hit, no network
    try:
        assert _prefetch_cited(drafts) == 1
    finally:
        tools._fetch_cache.clear()


def test_prefetch_cited_extracts_urls_from_json_shaped_findings() -> None:
    """oMLX/qwen models emit findings as a fenced JSON object
    (```json {"RESEARCH_FINDING": {"URL": ...}}```) instead of plain 'URL:' lines —
    the old prefetch only scanned plain lines, so a JSON-only citation was never
    prefetched and stayed ungrounded even when genuinely fetchable."""
    from studio import tools
    from studio.runner import _prefetch_cited
    tools._fetch_cache.clear()
    tools._fetch_cache["https://json.example/p|"] = ("p", 1)  # cache-hit, no network
    drafts = ['```json\n{"RESEARCH_FINDING": {"URL": "https://json.example/p"}}\n```']
    try:
        assert _prefetch_cited(drafts) == 1
    finally:
        tools._fetch_cache.clear()


def test_prefetch_parallel_matches_serial_and_grounds(monkeypatch) -> None:
    """P0-B: parallel _prefetch_cited yields the SAME _fetch_cache as the serial
    prefetch_url loop for a fixed draft set, and a cited URL is grounded (cached)
    afterward. Workers run pure _fetch_page (no _fetch_cache access); the reducer
    thread inserts serially — single-writer invariant preserved. An unreachable URL
    (ok=False) is dropped by both paths, keeping fabricated citations ungrounded."""
    import sys
    import types

    from studio import tools
    from studio.findings import _cited_urls
    from studio.runner import _prefetch_cited

    pages = {
        "https://a.example": "alpha body",
        "https://b.example": "beta body",
        "https://c.example": "gamma body",
    }

    def _fake_web_fetch(u, selector=None):
        body = pages.get(u)
        return types.SimpleNamespace(ok=body is not None, content=body or "", bytes=len(body or ""))

    monkeypatch.setitem(sys.modules, "web_toolkit", types.SimpleNamespace(web_fetch=_fake_web_fetch))

    drafts = [
        "URL: https://a.example\nURL: https://b.example\n",
        "URL: https://c.example\nURL: https://a.example\n",   # dup a
        "URL: https://dead.example\n",                          # ok=False → dropped
    ]
    urls = _cited_urls(drafts)

    def _snapshot() -> dict:
        return {k: tools._fetch_cache[k] for k in tools._fetch_cache}

    try:
        # Serial baseline
        tools._fetch_cache.clear()
        for u in urls:
            tools.prefetch_url(u)
        serial = _snapshot()

        # Parallel
        tools._fetch_cache.clear()
        n = _prefetch_cited(drafts)
        parallel = _snapshot()

        assert parallel == serial                        # identical contents serial vs parallel
        assert "https://a.example|" in parallel          # cited URL grounded
        assert "https://dead.example|" not in parallel   # unreachable dropped
        assert n == 3                                    # a, b, c fetched; dead dropped
    finally:
        tools._fetch_cache.clear()


def test_prefetch_grounds_cited_url_through_parse_findings(monkeypatch) -> None:
    """entry-177 under parallel prefetch: a finding citing a URL the spoke only CITED
    (never directly fetched) survives _parse_findings' grounding gate (findings.py:449)
    ONLY because parallel _prefetch_cited fetched it. The cache is non-empty (cache_active
    True) but the cited URL is uncached — the exact trap where the gate drops a real
    finding — so this proves the parallel prefetch, not luck, is what grounds it."""
    import sys
    import types

    from studio import tools
    from studio.findings import _parse_findings, _prefetch_cited

    def _fake_web_fetch(u, selector=None):
        ok = u == "https://cited.example"
        return types.SimpleNamespace(ok=ok, content="cited body" if ok else "", bytes=10)

    monkeypatch.setitem(sys.modules, "web_toolkit", types.SimpleNamespace(web_fetch=_fake_web_fetch))

    draft = "RESEARCH_FINDING\nURL: https://cited.example\nWHY: because\n"
    try:
        # Non-empty cache with an UNRELATED entry → cache_active True, cited URL uncached.
        tools._fetch_cache.clear()
        tools._fetch_cache["https://unrelated.example|"] = ("x", 1)
        assert _parse_findings(draft) == []                       # dropped: cited-but-uncached

        # Parallel prefetch grounds the cited URL → the finding now survives the gate.
        _prefetch_cited([draft])
        survived = _parse_findings(draft)
        assert len(survived) == 1
        assert survived[0].url == "https://cited.example"
        assert survived[0].grounded is True
    finally:
        tools._fetch_cache.clear()


def test_findings_to_patches_parses_bare_finding_without_heading() -> None:
    """Parse mismatch fix: the executor emits a BARE 'RESEARCH_FINDING:' (no '##'),
    which the old '##'-required regex never matched — so the deterministic patch-floor
    silently produced zero patches (the load-bearing live no-op). Both bare and headed
    forms must parse."""
    from studio import tools
    from studio.runner import _research_findings_to_patches
    tools._fetch_cache.clear()
    tools._fetch_cache["https://x.example/p|"] = ("a sentence that is on the page here", 30)
    bare = (
        "Let me emit the findings.\n\nRESEARCH_FINDING:\nARTICLE_TITLE: P\n"
        "URL: https://x.example/p\nPATCH_TARGET: ## Sources\nCLAIM: A point.\n"
        "QUOTE: a sentence that is on the page here\n"
    )
    try:
        patches = _research_findings_to_patches(bare)
        assert len(patches) == 1                       # bare heading now parses
        assert "x.example/p" in patches[0].content
    finally:
        tools._fetch_cache.clear()


def test_findings_to_patches_keeps_on_verified_quote_despite_url_miss() -> None:
    """Lever 1 dual oracle: a finding whose URL is NOT in the cache is still KEPT when
    its verbatim QUOTE appears on a fetched page — a verified quote proves the page was
    read. (The live regression: both probe findings had verified quotes but were dropped
    on brittle URL exact-match; either grounding signal must suffice.)"""
    from studio import tools
    from studio.runner import _research_findings_to_patches
    tools._fetch_cache.clear()
    tools._fetch_cache["https://realpage.example/x|"] = (
        "Ralph is a technique. In its purest form, Ralph is a Bash loop.", 60
    )
    finding = (
        "## RESEARCH_FINDING\nARTICLE_TITLE: Ralph\nURL: https://elsewhere.example/y\n"
        "PATCH_TARGET: ## Sources\nCLAIM: Ralph is a bash loop technique.\n"
        "QUOTE: Ralph is a technique. In its purest form, Ralph is a Bash loop.\n"
    )
    try:
        patches = _research_findings_to_patches(finding)
        assert len(patches) == 1                              # kept on verified quote alone
        # the verbatim source excerpt is pasted as the evidence (copy-paste, Lever 2)
        assert '"Ralph is a technique. In its purest form, Ralph is a Bash loop."' in patches[0].content
    finally:
        tools._fetch_cache.clear()


def test_findings_to_patches_skips_quote_check_when_cache_empty() -> None:
    """Lever 1 is conservative: with no fetch cache (offline/test), the quote check
    is skipped — it can only remove a PROVEN fabrication, never block the additive
    path entirely."""
    from studio import tools
    from studio.runner import _research_findings_to_patches
    tools._fetch_cache.clear()
    finding = (
        "## RESEARCH_FINDING\nARTICLE_TITLE: T\nURL: https://x.example\n"
        "PATCH_TARGET: ## Sources\nCLAIM: A claim.\nQUOTE: some quote text here\n"
    )
    patches = _research_findings_to_patches(finding)
    assert len(patches) == 1
    assert "x.example" in patches[0].content


def test_executor_prompt_requires_copied_quote_and_why() -> None:
    """Lever 1/2: the executor schema demands a COPY-PASTED verbatim QUOTE (the
    evidence) + WHY (relevance) per source, and explicitly forbids paraphrase — the
    rephrased CLAIM was dropped because a model paraphrase can fabricate."""
    from studio.runner import _build_executor_prompt
    p = _build_executor_prompt("find popular articles", "# Doc\n## Sources\nx",
                               "- [## Sources] missing url")
    assert "QUOTE:" in p and "verbatim" in p.lower()
    assert "COPY" in p and "WHY:" in p
    assert "CLAIM:" not in p                              # rephrase field removed
    assert "PASTE its words" in p                         # copy-paste, not restate


def test_strip_preamble_removes_reducer_commentary() -> None:
    """The reducer's commentary preamble must never survive into the artifact —
    it belongs in the chat (surfaced block), and the grow-only ratchet would
    otherwise lock it in forever (the v26→v27 poison)."""
    from studio.runner import _strip_preamble
    poisoned = (
        "The artifact is complete. I've reviewed it against the checklist:\n\n"
        "**Weaknesses addressed in current artifact:**\n- x\n"
        "Remaining concern: y\n\n"
        "# Research Report\n\n## Sources\nclean body"
    )
    clean = _strip_preamble(poisoned)
    assert clean.startswith("# Research Report")
    assert "Weaknesses addressed" not in clean
    assert "Remaining concern" not in clean
    assert "## Sources\nclean body" in clean


def test_strip_preamble_noop_on_clean_or_headingless() -> None:
    from studio.runner import _strip_preamble
    assert _strip_preamble("# Title\n\nbody") == "# Title\n\nbody"   # already clean
    assert _strip_preamble("prose, no heading") == "prose, no heading"  # never destroy


def test_strip_preamble_removes_inherited_conflict_markers() -> None:
    """Inherited '<!-- conflict(...): anchor not found -->' markers (frozen into a seed by an
    earlier version, before anchor-demotion) must be sanitized at every artifact boundary; the
    content beneath each marker is kept, only the noise comment is removed."""
    from studio.runner import _strip_preamble
    poisoned = ("# Doc\n\nA finding sentence (https://x.example).\n"
                "<!-- conflict(finding): anchor not found -->\n\nNext finding.\n")
    clean = _strip_preamble(poisoned)
    assert "conflict" not in clean
    assert "A finding sentence" in clean and "Next finding." in clean


class _FakeEmb:
    """Embeds by keyword cluster: 'rank/comparison' issues are similar to each
    other (re-worded same issue), 'url' is its own cluster."""
    def embed(self, texts):
        out = []
        for t in texts:
            tl = t.lower()
            if any(k in tl for k in ("rank", "comparison", "comparative", "metric")):
                out.append([1.0, 0.0, 0.0])
            elif "url" in tl:
                out.append([0.0, 1.0, 0.0])
            else:
                out.append([0.0, 0.0, 1.0])
        return out


def test_weakness_score_semantic_no_false_solved() -> None:
    """A re-worded-but-unsolved weakness must NOT count as solved (the v27 bug:
    the miner re-words issues each run, so string-match falsely inflated the score
    on an UNCHANGED artifact)."""
    from studio.runner import _weakness_score
    prior = ["[## S] No comparative engagement metrics"]
    open_ = ["[## S] No systematic ranking or comparison", "[## Sources] Missing URLs"]
    # prior 'comparative' ~ open 'ranking' (same cluster) → prior still open → solved 0;
    # the URL weakness is genuinely new → total = 1 prior + 1 new = 2 → 0/2 = 0.0
    assert _weakness_score(prior, open_, embedder=_FakeEmb()) == 0.0


def test_weakness_score_semantic_genuine_solve() -> None:
    """A prior weakness with NO similar open weakness counts as solved."""
    from studio.runner import _weakness_score
    prior = ["[## A] ranking not systematic", "[## B] missing url"]
    open_ = ["[## A] no comparative metrics"]   # A persists (re-worded); B solved
    # solved 1 (B), still-open 1 (A), new 0 → total 2 → 0.5
    assert _weakness_score(prior, open_, embedder=_FakeEmb()) == 0.5


def test_weakness_score_no_weakness_is_one() -> None:
    """DESIGN §11.4: no weaknesses anywhere => nothing to fix => score 1.0."""
    from studio.runner import _weakness_score
    assert _weakness_score([], []) == 1.0
    assert _weakness_score(["[## A] x", "[## B] y"], []) == 1.0  # all prior solved


def test_weakness_score_solved_over_total() -> None:
    """score = solved / total. 3 of 5 prior weaknesses resolved => 0.6."""
    from studio.runner import _weakness_score
    prior = ["[## A] missing url", "[## B] thin", "[## C] no source",
             "[## D] gap", "[## E] stale"]
    still_open = ["[## B] thin", "[## C] no source"]  # 3 solved, 2 open
    assert _weakness_score(prior, still_open) == 0.6


def test_weakness_score_new_open_weakness_penalized() -> None:
    """A first run that introduces an open weakness (none solved) => 0.0."""
    from studio.runner import _weakness_score
    assert _weakness_score([], ["[## A] new problem"]) == 0.0


def test_today_note_injects_current_date() -> None:
    """Agents must know today's date so current-year sources aren't flagged future."""
    import datetime
    from studio.runner import _today_note
    note = _today_note()
    assert datetime.date.today().isoformat() in note
    assert "future" in note.lower()


def test_executor_prompt_frames_research_not_planning() -> None:
    """STAR spokes must be EXECUTORS (fetch + emit findings), not planning hubs —
    the planner framing was the score ceiling (§11.10). The spoke is goal-bounded:
    it may use the task goal for search relevance, but still executes only its
    assignment + weaknesses."""
    from studio.runner import _build_executor_prompt
    p = _build_executor_prompt("find popular articles", "# Doc\n## Sources\nx",
                               "- [## Sources] missing url")
    assert "RESEARCH EXECUTOR" in p
    assert "TASK_LIST" in p and "ASSIGNED" in p          # explicitly FORBIDDEN
    assert "RESEARCH_FINDING" in p and "URL:" in p       # the execute output format
    assert "missing url" in p                            # its weakness assignment survives
    assert "find popular articles" in p                  # needed for relevant search queries
    assert "TASK GOAL" in p and "assigned focus" in p
    assert "weaknesses are '(none)'" in p
    assert "planning hub" not in p                       # the bug framing is gone


def test_ends_cleanly_authoritative_truncation_signal() -> None:
    """P0: the deterministic truncation signal — a complete sentence or a URL-ending
    reference line is clean; a mid-word cut or empty text is not. This is what the miner
    trusts instead of inferring truncation from a window-excerpt boundary (the W3 fix)."""
    from studio.task_runs import _ends_cleanly
    assert _ends_cleanly("A complete sentence.")
    assert _ends_cleanly("Body.\n\n- Author. 'Title.' https://x.example/p")  # URL-ending ref
    assert _ends_cleanly("ends with a paren)")
    assert not _ends_cleanly("designing loops that prom")   # mid-word (the W3 case)
    assert not _ends_cleanly("   ")                         # empty


def test_verified_urls_in_cache_counts_search_and_fetch() -> None:
    """P0/W5 fix: a cited URL is verified if it is in a SEARCH-result list OR a
    'fetch:{url}:{selector}' key (the fetched page). The fetch entries were previously
    ignored, so a fetched-but-not-searched citation was wrongly flagged unverified."""
    from studio.task_runs import verified_urls_in_cache
    cache = {
        "v2|searxng:q": [{"title": "t", "url": "https://searched.example"}],
        "fetch:https://fetched.example:None": {"content": "..."},
        "fetch:https://other.example:None": {"content": "..."},
    }
    text = ("see https://searched.example and https://fetched.example. "
            "but https://uncached.example is not real")
    out = verified_urls_in_cache(cache, text)
    assert "https://searched.example" in out      # via search list
    assert "https://fetched.example" in out       # via fetch entry (the W5 fix)
    assert "https://uncached.example" not in out   # cited but never cached → unverified
    assert "https://other.example" not in out      # cached but not cited in text


def test_runner_verified_urls_from_cache_rechecks_final_text(tmp_path, monkeypatch) -> None:
    from studio.runner import _verified_urls_from_cache
    from web_toolkit import cache_flush, cache_store

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WEB_CACHE", "1")
    monkeypatch.setenv("WEB_CACHE_PATH", str(tmp_path / ".web_cache.json"))
    # P0-A primary path: entries are populated through the cache API (as web_fetch does),
    # so cache_snapshot() sees them; the function's own cache_flush() persists them.
    cache_store("fetch:https://before.example:None", {"content": "..."})
    cache_store("fetch:https://after.example:None", {"content": "..."})
    cache_flush()

    out = _verified_urls_from_cache("Final report cites https://after.example.")

    assert out == ["https://after.example"]


def test_runner_verified_urls_fallback_matches_direct_parse(tmp_path, monkeypatch) -> None:
    """Blocking codex requirement: when the web_toolkit cache API is unavailable, verification
    falls back to the ORIGINAL direct .web_cache.json parse and behaves identically."""
    import sys

    from studio.runner import _verified_urls_from_cache

    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "web_toolkit", None)  # force `from web_toolkit import ...` to fail

    # (a) missing file → [] (fail-open)
    assert _verified_urls_from_cache("cites https://x.example.") == []

    # (b) present file → verified URLs, identical to the pre-P0-A direct parse
    Path(".web_cache.json").write_text(json.dumps({
        "fetch:https://after.example:None": {"content": "..."},
    }))
    assert _verified_urls_from_cache("Final cites https://after.example.") == ["https://after.example"]

    # (c) corrupt file → [] (fail-open)
    Path(".web_cache.json").write_text("{ not json")
    assert _verified_urls_from_cache("cites https://after.example.") == []


def test_runner_web_cache_available_fallback_matches_direct_read(tmp_path, monkeypatch) -> None:
    """P0-A: when the cache API is unavailable, _web_cache_available falls back to the
    ORIGINAL direct read and behaves identically (present-nonempty→True, missing/empty→False)."""
    import sys

    from studio.runner import _web_cache_available

    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "web_toolkit", None)  # force cache API import to fail

    assert _web_cache_available() is False                 # missing file
    Path(".web_cache.json").write_text('{"k": "v"}')
    assert _web_cache_available() is True                  # present, non-empty
    Path(".web_cache.json").write_text("   ")
    assert _web_cache_available() is False                 # present but whitespace-only


def test_runner_final_dossier_fallback_matches_direct_parse(tmp_path, monkeypatch) -> None:
    """P0-A: when the cache API is unavailable, _final_evidence_dossier falls back to the
    ORIGINAL direct .web_cache.json parse — a cited URL cached only on disk still resolves."""
    import sys

    from studio.runner import _final_evidence_dossier

    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "web_toolkit", None)

    Path(".web_cache.json").write_text(json.dumps({
        "fetch:https://after.example:None": {"ok": True, "content": "DOSSIER_MARKER"},
    }))
    out = _final_evidence_dossier("report cites https://after.example.")
    assert "DOSSIER_MARKER" in out and "https://after.example" in out

    Path(".web_cache.json").write_text("{ not json")     # corrupt → no cached content
    assert "DOSSIER_MARKER" not in _final_evidence_dossier("cites https://after.example.")


def test_prune_resolved_weaknesses_drops_stale_final_lints() -> None:
    doc = """# Final

## Evidence and Analysis

This section now cites evidence clearly (https://example.com/source).

## References

- https://example.com/source
"""
    weaknesses = [
        "[document] Placeholder text remains in report: no specific urls were provided",
        "[Evidence and Analysis] Long evidence-bearing section has no citation URL.",
        "[document] Inclusion of provided citations in the text: the final output contains no in-text citations or a populated References section.",
        "[document] Keep this real scoring weakness",
    ]

    assert _prune_resolved_weaknesses(weaknesses, doc) == [
        "[document] Keep this real scoring weakness"
    ]


def test_miner_prompt_has_completeness_fact_and_full_url_list() -> None:
    """P0: the miner prompt carries the deterministic completeness fact (so it cannot
    hallucinate truncation from a window edge) and ALL verified URLs, not just the first 20
    (the cap that hid real citations and produced the W5 false positive)."""
    from agentkit.types import ChatResult
    from studio.task_runs import mine_weaknesses_from_outputs
    cap: dict = {}

    class _C:
        def chat(self, messages, tools=None) -> ChatResult:
            cap["p"] = messages[-1]["content"]
            return ChatResult(text="[]", total_tokens=1)

    urls = [f"https://real.example/{i}" for i in range(30)]   # > the old cap of 20
    mine_weaknesses_from_outputs(
        {"s1": "x"}, "A complete report. The end.", "task", _C(), verified_urls=urls,
    )
    p = cap["p"]
    assert "ends CLEANLY" in p and "do not flag truncation" in p   # completeness fact present
    assert "https://real.example/25" in p                          # 26th URL shown → cap > 20


def test_miner_marks_cached_urls_verified() -> None:
    """Cache-as-oracle (§11.10): a URL in the fetch cache is real, so the miner is
    told NOT to flag it as unverified/fabricated."""
    from agentkit.types import ChatResult
    from studio.task_runs import mine_weaknesses_from_outputs
    cap: dict = {}

    class _C:
        def chat(self, messages, tools=None) -> ChatResult:
            cap["p"] = messages[-1]["content"]
            return ChatResult(text="[]", total_tokens=1)

    mine_weaknesses_from_outputs(
        {"s1": "x"}, "doc body", "the task", _C(),
        verified_urls=["https://real.example.com/a"],
    )
    assert "VERIFIED SOURCES" in cap["p"]
    assert "https://real.example.com/a" in cap["p"]
    assert "not fabricated" in cap["p"].lower()


def test_hill_climb_honors_selected_topology_no_force_star(
    fake_client_factory, tmp_path
) -> None:
    """E1 (PLAN §4b): the force-STAR override is DELETED. Every topology now satisfies the
    assemble+verify reducer contract (STAR/MAP/MESH fold drafts; SINGLE identity-fold;
    PIPELINE terminal-capture), so the selector's verdict is HONORED under hill-climb instead
    of overridden. 'compare ...' → MESH stays MESH; 'write a recommendation' → SINGLE stays
    SINGLE — neither is forced to STAR."""
    events: list[StudioEvent] = []
    session = _make_session()
    session.hill_climb_config = {"auto_improve": True}
    runner = Runner(
        session, events.append, client_factory=fake_client_factory,
        embedder=None, workspace_root=tmp_path,
    )
    runner.run("1. compare redis and postgres 2. write a recommendation")
    topo = [e for e in events if e.EVENT_TYPE == "topology"][0]
    tops = {s["topology"] for s in topo.steps}
    # Selection honored: MESH and SINGLE both survive — no blanket force-STAR.
    assert "mesh" in tops, tops
    assert "single" in tops, tops
    # E2: every phase carries a rationale (no coercion language any more).
    assert all("rationale" in s for s in topo.steps), topo.steps
    assert not any("coerced" in s.get("rationale", "") for s in topo.steps), topo.steps


def test_no_hill_climb_keeps_auto_topology(fake_client_factory) -> None:
    """Without auto_improve, topology stays auto-derived (regression guard for
    the force-STAR override — it must not fire when hill-climb is off)."""
    events = _run(fake_client_factory)  # no hill_climb_config
    topo = [e for e in events if e.EVENT_TYPE == "topology"][0]
    tops = {s["topology"] for s in topo.steps}
    # "compare redis and postgres" → MESH; not forced to STAR.
    assert "mesh" in tops, tops


def test_task_hash_invariant_to_attached_goal(
    fake_client_factory, tmp_path, monkeypatch
) -> None:
    """Attaching a goal/constraints must NOT change a task's hill-climb identity.

    The goal block is prepended into `requirement` for steering, but task_hash is
    computed from the BASE requirement — so a goal-augmented run records under the
    SAME task_hash as a bare run, preserving artifact carry-forward. Regression guard
    for the bug where attaching a goal forked the lineage → cold-start v1, score 0.00.
    """
    import sqlite3
    from types import SimpleNamespace

    from studio.task_runs import task_hash

    # Isolate the run DB + workspace under tmp (both derive from this env root).
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    base = "1. compare redis and postgres 2. write a recommendation"

    def _run_and_get_recorded_hash(attach_goal: bool) -> str:
        session = _make_session()
        if attach_goal:
            session.goal = SimpleNamespace(
                end_state="produce a sourced recommendation",
                constraints=["cite sources", "be concise"],
            )
        runner = Runner(
            session, lambda _e: None,
            client_factory=fake_client_factory, embedder=None,
        )
        runner.run(base)
        con = sqlite3.connect(tmp_path / "task_runs.db")
        try:
            row = con.execute(
                "select task_hash from task_runs order by rowid desc limit 1"
            ).fetchone()
        finally:
            con.close()
        assert row is not None, "run did not record a task_runs row"
        return row[0]

    h_bare = _run_and_get_recorded_hash(attach_goal=False)
    h_goal = _run_and_get_recorded_hash(attach_goal=True)
    assert h_bare == h_goal == task_hash(base), (h_bare, h_goal, task_hash(base))


def test_rubric_template_steers_generation(fake_client_factory) -> None:
    """A configured rubric TEMPLATE is injected into the requirement the planner sees,
    so the report is generated toward those sections (DESIGN §11.6) — not only scored
    against them after the fact. With no rubric_config the requirement is untouched, so
    report headings are NOT forced onto non-research tasks (the no-compromise constraint).
    """

    def _plan_task(template: list[str] | None) -> str:
        events: list[StudioEvent] = []
        session = _make_session()
        if template is not None:
            session.rubric_config = {"weights": None, "template": template}
        runner = Runner(
            session, events.append, client_factory=fake_client_factory, embedder=None
        )
        runner.run("compare redis and postgres")
        return [e for e in events if e.EVENT_TYPE == "plan"][0].task

    with_tpl = _plan_task(["Executive Summary", "Methodology", "Conclusion"])
    without = _plan_task(None)
    assert "Executive Summary" in with_tpl and "Methodology" in with_tpl, with_tpl
    assert "Executive Summary" not in without, without  # untouched when unconfigured


def test_goal_excluded_from_planner_no_duplicate_phases(fake_client_factory) -> None:
    """A Goal overlapping the requirement must NOT reach the PLANNER: prepending it doubled
    the task and the splitter produced duplicate phases (the Pi/Craft run). The goal still
    rides in `requirement` for steering/verification, but PlanEvent.task is the clean base —
    so no `Goal:` text becomes a phase and no phase is duplicated."""
    from types import SimpleNamespace

    events: list[StudioEvent] = []
    session = _make_session()
    session.goal = SimpleNamespace(end_state="compare redis and postgres", constraints=[])
    runner = Runner(
        session, events.append, client_factory=fake_client_factory, embedder=None
    )
    runner.run("compare redis and postgres")

    plan_ev = [e for e in events if e.EVENT_TYPE == "plan"][0]
    assert "Goal:" not in plan_ev.task                       # goal kept OUT of planner input
    descs = [s["description"] for s in plan_ev.steps]
    assert len(descs) == len(set(descs)), descs              # no duplicate phases


def test_accept_epoch_keep_discard_gate() -> None:
    """Phase-1 gate (DESIGN §11.5): keep an epoch ONLY if strictly preferred over the
    prior. Closes the open-loop accept (the old length-only ratchet would write any
    non-shorter rewrite). Never reads the noisy absolute score — asks a label-free
    judge new-vs-prior, so a changing/gameable scorer can't drive acceptance.
    """
    from studio.epoch_gate import accept_epoch

    better = lambda _n, _p: 1   # noqa: E731
    worse = lambda _n, _p: -1   # noqa: E731
    tie = lambda _n, _p: 0      # noqa: E731

    assert accept_epoch("new report", "", worse) is True       # cold start → accept
    assert accept_epoch("", "prior report", better) is False   # empty epoch → keep prior
    assert accept_epoch("new", "prior", better) is True        # preferred → keep new
    assert accept_epoch("new", "prior", worse) is False        # worse → revert to prior
    assert accept_epoch("new", "prior", tie) is False          # tie → keep prior (strict)


def test_accept_epoch_with_real_reports() -> None:
    """Real-report input (DESIGN §11.5): the gate must KEEP the 58KB good report over
    the 4.5KB thin one, and revert a thin epoch back to a good prior.

    Uses a DETERMINISTIC quality proxy (verified-source density) so CI stays offline;
    the live LLM judge on these same fixtures is exercised manually (it exposed both
    the parser bug and the no-rubric hedging — see DESIGN §11.5 findings).
    """
    import pathlib

    from studio.epoch_gate import accept_epoch

    fx = pathlib.Path(__file__).parent / "fixtures"
    good = (fx / "report_good.md").read_text()
    thin = (fx / "report_thin.md").read_text()

    def _sig(s: str) -> int:  # verified-source density as a stand-in for the LLM judge
        return s.lower().count("verified") + s.lower().count("source:") + s.count("http")

    prefer = lambda _n, _p: _sig(_n) - _sig(_p)   # noqa: E731
    assert _sig(good) > _sig(thin)
    assert accept_epoch(good, thin, prefer) is True    # good epoch vs thin prior → keep new
    assert accept_epoch(thin, good, prefer) is False   # thin epoch vs good prior → revert


def test_tool_loop_emits_tool_events(fake_client) -> None:
    """With tools enabled + a mocked search_fn, a tool-calling client fires
    tool_call/tool_result during a phase (web_search runs, no network)."""
    from agentkit.types import ChatResult

    # A client that requests web_search once, then answers.
    class _ToolClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools=None) -> ChatResult:
            # Run-scoped requirement extraction runs once before the phase loop on
            # base_client; answer it out-of-band so it does not consume a scripted
            # tool-call slot ("write a short note" states no explicit requirement).
            if "You extract the EXPLICIT, CHECKABLE requirements" in str(messages):
                return ChatResult(text="NONE", total_tokens=1)
            self.calls += 1
            if self.calls == 1:
                return ChatResult(text="", total_tokens=3,
                                  tool_calls=[("web_search", {"query": "q"})])
            return ChatResult(text="answer.", total_tokens=2)

    def factory(_on_usage) -> LLMClient:
        return _ToolClient()

    def fake_search(query, *, results=5):
        from web_toolkit import SearchResult
        return [SearchResult(title="t", url="https://x.test/t", snippet="s")]

    events: list[StudioEvent] = []
    session = _make_session()  # tools_enabled defaults True
    runner = Runner(
        session, events.append, client_factory=factory, embedder=None,
        search_fn=fake_search,
    )
    runner.run("write a short note")  # SINGLE phase
    tool_calls = [e for e in events if e.EVENT_TYPE == "tool_call"]
    tool_results = [e for e in events if e.EVENT_TYPE == "tool_result"]
    assert tool_calls and tool_calls[0].tool == "web_search"
    assert tool_calls[0].step_id  # attributed to the running phase
    assert tool_results and tool_results[0].n_results == 1
    trace = [
        json.loads(line)
        for line in (session.last_run.agent_trace_jsonl or "").splitlines()
    ]
    checkpoints = [
        json.loads(line)
        for line in (session.last_run.checkpoints_jsonl or "").splitlines()
    ]
    assert trace and trace[0]["tool"] == "web_search"
    assert trace[0]["status"] == "ok"
    assert trace[0]["args_redacted"] == {"query": "q"}
    assert isinstance(trace[0]["ts"], float)
    assert checkpoints and checkpoints[-1]["phase_id"] == "final"
    assert checkpoints[-2]["phase_id"] == "pre_validation"
    assert checkpoints[-2]["publish_issue_count"] >= 0
    assert checkpoints[-2]["loopdoctor_failure_count"] >= 0
    assert trace[0]["id"] in checkpoints[-1]["observation_ids"]


def test_tool_arg_redaction_clips_secrets() -> None:
    redacted = Runner._redact_tool_args({
        "query": "x",
        "api_key": "sk-secret",
        "nested": {"password": "pw", "body": "a" * 250},
    })

    assert redacted["query"] == "x"
    assert redacted["api_key"] == "[redacted]"
    assert redacted["nested"]["password"] == "[redacted]"
    assert redacted["nested"]["body"].endswith("...[truncated]")


def test_gemma_profile_limits_searches_in_runner_tool_loop(fake_client) -> None:
    """Gemma's weak-model profile reaches the real runner tool wrapper."""
    from agentkit.types import ChatResult

    class _TwoSearchClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools=None) -> ChatResult:
            # Answer the pre-phase-loop requirement extraction out-of-band so it does
            # not consume a scripted search slot (see _ToolClient above).
            if "You extract the EXPLICIT, CHECKABLE requirements" in str(messages):
                return ChatResult(text="NONE", total_tokens=1)
            self.calls += 1
            _queries = ["one", "two", "three", "four"]
            if self.calls <= len(_queries):
                return ChatResult(
                    text="", total_tokens=1,
                    tool_calls=[("web_search", {"query": _queries[self.calls - 1]})],
                )
            return ChatResult(text="answer.", total_tokens=1)

    def factory(_on_usage) -> LLMClient:
        return _TwoSearchClient()

    queries: list[str] = []

    def fake_search(query, *, results=5):
        from web_toolkit import SearchResult

        queries.append(query)
        return [SearchResult(title="t", url="https://x.test/t", snippet="s")]

    events: list[StudioEvent] = []
    session = _make_session()
    session.llm_info = {
        "label": "gemma",
        "model": "gemma-4-26B-A4B-it-heretic-4bit",
    }
    runner = Runner(
        session,
        events.append,
        client_factory=factory,
        embedder=None,
        search_fn=fake_search,
    )
    runner.run("write a short note")

    rejected = [
        e for e in events
        if e.EVENT_TYPE == "tool_result" and e.tool == "web_search" and e.rejected
    ]
    # gemma budget is now 3 searches (raised from 1 once the fabricated-citation
    # forcing turn made the budget actually bind) — the 4th call is rejected.
    assert queries == ["one", "two", "three"]
    assert rejected
    assert "budget exhausted" in rejected[0].notice


def test_gemma_report_request_keeps_llm_epic_planning_by_default(fake_client_factory) -> None:
    """Weak-model budgets must not bypass the original LLM planner by default."""
    events: list[StudioEvent] = []
    session = _make_session(mode="llm")
    session.llm_info = {
        "label": "gemma",
        "model": "gemma-4-26B-A4B-it-heretic-4bit",
    }
    runner = Runner(session, events.append, client_factory=fake_client_factory, embedder=None)
    runner.run("Write a generic research report about local and remote skill catalogs.")

    plan_event = [e for e in events if e.EVENT_TYPE == "plan"][0]
    assert [step["id"] for step in plan_event.steps] != [
        "intake-profile",
        "source-plan",
        "retrieve-verify",
        "assemble-rewrite",
        "lint-publish",
    ]
    assert plan_event.steps


def test_gemma_planning_and_topology_selection_are_capped() -> None:
    from agentkit.types import ChatResult

    class CapturingClient:
        def __init__(self) -> None:
            self.max_tokens: list[int | None] = []
            self.n_calls = 0
            self.total_tokens = 0

        def chat(self, messages, tools=None, max_tokens=None):
            self.max_tokens.append(max_tokens)
            self.n_calls += 1
            self.total_tokens += 1
            content = str(messages[-1].get("content", ""))
            if "EPIC_PLAN:" in content:
                return ChatResult(
                    text=(
                        'EPIC_PLAN:\n```json\n{"epics":[{"id":"epic-1",'
                        '"title":"Gather evidence","description":"gather cited sources",'
                        '"topology":"star","depends_on":[],"branches":[]}]}'
                        "\n```"
                    ),
                    total_tokens=1,
                )
            return ChatResult(
                text=(
                    '{"topology":"star","rationale":"independent evidence gathering",'
                    '"questions_fired":["Q3"]}'
                ),
                total_tokens=1,
            )

    cap = CapturingClient()
    events: list[StudioEvent] = []
    session = _make_session(mode="llm")
    session.llm_info = {
        "label": "gemma",
        "model": "gemma-4-26B-A4B-it-heretic-4bit",
    }
    runner = Runner(session, events.append, client_factory=lambda _u: cap, embedder=None)
    runner.run("Write a research report about battery recycling policy.")

    assert 1024 in cap.max_tokens
    assert 512 in cap.max_tokens
    topo = [e for e in events if e.EVENT_TYPE == "topology"][0]
    assert topo.steps[0]["rationale"] == "independent evidence gathering"


def test_runner_maps_state_level_selector_topology_to_runtime_shape() -> None:
    from agentkit.types import ChatResult

    class DurableSelectorClient:
        def __init__(self) -> None:
            self.n_calls = 0
            self.total_tokens = 0

        def chat(self, messages, tools=None, max_tokens=None):
            self.n_calls += 1
            self.total_tokens += 1
            content = str(messages[-1].get("content", ""))
            if "EPIC_PLAN:" in content:
                return ChatResult(
                    text=(
                        'EPIC_PLAN:\n```json\n{"epics":[{"id":"epic-1",'
                        '"title":"Recoverable workflow","description":"recover after restart",'
                        '"depends_on":[],"branches":[]}]}'
                        "\n```"
                    ),
                    total_tokens=1,
                )
            return ChatResult(
                text=(
                    '{"topology":"durable_board",'
                    '"rationale":"needs restart recovery and persisted work state",'
                    '"questions_fired":["Q4","Q8"]}'
                ),
                total_tokens=1,
            )

    events: list[StudioEvent] = []
    session = _make_session(mode="llm")
    runner = Runner(
        session,
        events.append,
        client_factory=lambda _u: DurableSelectorClient(),
        embedder=None,
    )
    runner.run("Design a recoverable research workflow")

    topo = [e for e in events if e.EVENT_TYPE == "topology"][0]
    assert topo.steps[0]["topology"] == "single"
    assert "durable_board" in topo.steps[0]["rationale"]
    assert "execute as single" in topo.steps[0]["rationale"]
    assert "restart recovery" in topo.steps[0]["rationale"]


def test_publish_gate_emits_failure_for_report_without_sources(fake_client_factory) -> None:
    events: list[StudioEvent] = []
    session = _make_session()
    runner = Runner(session, events.append, client_factory=fake_client_factory, embedder=None)
    runner.run(
        "Write a research report about catalog management for agent loops and skills. "
        "Use fetched evidence and citations."
    )

    gates = [
        e for e in events
        if e.EVENT_TYPE == "gate" and getattr(e, "name", "") == "publish-ready"
    ]
    assert gates
    assert gates[0].outcome == "fail"
    assert "no source URL" in gates[0].detail


def test_publish_gate_error_emits_observable_failure(fake_client_factory, monkeypatch) -> None:
    # P2-b: a publish-gate EXCEPTION must emit a failed GateEvent (no-op kept
    # prior text), not swallow silently. build_revision_evidence_text is called
    # only inside the publish try, so forcing it to raise exercises that path.
    import studio.report_quality as _rq

    def _boom(*_a, **_k):
        raise RuntimeError("gate boom")

    monkeypatch.setattr(_rq, "build_revision_evidence_text", _boom)
    events: list[StudioEvent] = []
    session = _make_session()
    runner = Runner(session, events.append, client_factory=fake_client_factory, embedder=None)
    runner.run(
        "Write a research report about catalog management for agent loops and skills. "
        "Use fetched evidence and citations."
    )
    gate_errors = [
        e for e in events
        if e.EVENT_TYPE == "gate" and getattr(e, "name", "") == "publish-ready"
        and e.outcome == "fail" and "gate error" in (e.detail or "")
    ]
    assert gate_errors, "publish-gate exception must emit an observable failure event"


def test_final_report_step_requires_synthesis_and_reflection() -> None:
    prompt = _final_step_instruction(
        "Write a research report about catalog management. Include citations.",
        "Synthesize gathered intelligence.",
        scoring_rules="- Citation integrity: cite fetched evidence.",
        weaknesses=["[document] Missing limitations."],
        evidence_dossier="- evidence/fetched-sources.json — manifest of fetched source files",
    )

    assert "final synthesis step for a research report" in prompt
    assert "Use every relevant fetched finding" in prompt
    assert "evidence-backed analysis" in prompt
    assert "limitations, caveats, or reflection" in prompt
    assert "Do not invent source URLs" in prompt
    assert "FULL SCORING STANDARD" in prompt
    assert "Citation integrity" in prompt
    assert "UNRESOLVED WEAKNESSES" in prompt
    assert "Missing limitations" in prompt
    assert "FETCHED EVIDENCE FILES" in prompt
    assert "use read_file" in prompt
    assert "evidence/fetched-sources.json" in prompt


def test_final_evidence_dossier_writes_workspace_paths(tmp_path) -> None:
    from studio.tools import _fetch_cache

    _fetch_cache.clear()
    _fetch_cache["https://example.com/a|"] = ("Alpha fetched body", 18)
    dossier = _final_evidence_dossier(
        "Worker cited https://example.com/a",
        workspace_dir=tmp_path,
    )

    assert "evidence/fetched-sources.json" in dossier
    assert "evidence/source-001.md" in dossier
    assert "Alpha fetched body" not in dossier
    assert (tmp_path / "evidence" / "source-001.md").read_text() == (
        "URL: https://example.com/a\n\nAlpha fetched body"
    )
    manifest = json.loads((tmp_path / "evidence" / "fetched-sources.json").read_text())
    assert manifest == [
        {"url": "https://example.com/a", "path": "evidence/source-001.md", "bytes": 18}
    ]


def test_cold_final_step_gets_scoring_weaknesses_and_evidence_paths(tmp_path, monkeypatch) -> None:
    from agentkit.types import ChatResult
    from studio.tools import _fetch_cache

    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path))
    _fetch_cache.clear()
    _fetch_cache["https://example.com/a|"] = ("Cached page body only in source file", 36)

    prompts: list[str] = []

    class _CaptureClient:
        def chat(self, messages, tools=None) -> ChatResult:
            prompts.append(messages[-1]["content"])
            return ChatResult(
                text=(
                    "RESEARCH_FINDING:\n"
                    "ARTICLE_TITLE: Alpha\n"
                    "URL: https://example.com/a\n"
                    "PATCH_TARGET: ## Executive Summary\n"
                    "QUOTE: Alpha quoted sentence\n"
                    "WHY: Supports the report.\n"
                ),
                total_tokens=5,
            )

    session = _make_session()
    session.tools_enabled = False
    session.rubric_config = {}
    session.weaknesses = ["[document] Missing limitations."]
    events: list[StudioEvent] = []
    runner = Runner(
        session,
        events.append,
        client_factory=lambda _on_usage: _CaptureClient(),
        embedder=None,
        workspace_root=tmp_path,
    )

    runner.run(
        "1. Gather evidence about research report quality.\n"
        "2. Write a research report about research report quality."
    )

    final_prompts = [
        p for p in prompts
        if "final synthesis step for a research report" in p
    ]
    assert final_prompts
    final_prompt = final_prompts[-1]
    assert "FULL SCORING STANDARD" in final_prompt
    assert "Citation integrity" in final_prompt
    assert "- (none for this assigned section)" not in final_prompt
    assert "UNRESOLVED WEAKNESSES" in final_prompt
    assert "Missing limitations" in final_prompt
    assert "FETCHED EVIDENCE FILES" in final_prompt
    assert "evidence/fetched-sources.json" in final_prompt
    assert "evidence/source-001.md" in final_prompt
    assert "Cached page body only in source file" not in final_prompt
    assert (tmp_path / session.session_id / "evidence" / "source-001.md").is_file()
    prompt_text = next(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / session.session_id / "io").glob("*.in.md")
        if "final synthesis step for a research report" in path.read_text(encoding="utf-8")
    )
    assert "FULL SCORING STANDARD" in prompt_text
    assert "Citation integrity" in prompt_text
    assert "- (none for this assigned section)" not in prompt_text
    assert "UNRESOLVED WEAKNESSES" in prompt_text
    assert "FETCHED EVIDENCE FILES" in prompt_text
    assert "evidence/fetched-sources.json" in prompt_text
    assert "evidence/source-001.md" in prompt_text


def test_seeded_final_step_gets_scoring_weaknesses_and_evidence_paths(tmp_path, monkeypatch) -> None:
    from agentkit.types import ChatResult
    from studio.rubric import DEFAULT_TEMPLATE, default_scoring_matrix
    from studio.task_runs import TaskRun, TaskRunStore, base_identity, task_hash
    from studio.tools import _fetch_cache

    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path))
    requirement = (
        "1. Gather evidence about research report quality.\n"
        f"2. Write a research report about research report quality. Run id: {tmp_path.name}."
    )
    thash = task_hash(base_identity(requirement))
    TaskRunStore().record(TaskRun(
        task_hash=thash,
        session_id="prior",
        version=1,
        score=0.4,
        weaknesses=["[document] Missing limitations."],
        artifact_path="",
        requirement=requirement,
        result_text="# Seed Report\n\n## Executive Summary\nPrior sourced report.",
        config={"auto_improve": True, "max_epochs": 1},
    ))
    _fetch_cache["https://example.com/a|"] = ("Cached page body only in source file", 36)

    prompts: list[str] = []

    class _CaptureClient:
        def chat(self, messages, tools=None) -> ChatResult:
            prompts.append(messages[-1]["content"])
            return ChatResult(
                text=(
                    "RESEARCH_FINDING:\n"
                    "ARTICLE_TITLE: Alpha\n"
                    "URL: https://example.com/a\n"
                    "PATCH_TARGET: ## Executive Summary\n"
                    "QUOTE: Alpha quoted sentence\n"
                    "WHY: Supports the report.\n"
                ),
                total_tokens=5,
            )

    session = _make_session()
    session.tools_enabled = False
    session.hill_climb_config = {"auto_improve": True, "max_epochs": 1}
    session.rubric_config = {
        "template": DEFAULT_TEMPLATE,
        "scoring_template": DEFAULT_TEMPLATE,
        "scoring_matrix": default_scoring_matrix("general", DEFAULT_TEMPLATE),
    }
    events: list[StudioEvent] = []
    runner = Runner(
        session,
        events.append,
        client_factory=lambda _on_usage: _CaptureClient(),
        embedder=None,
        workspace_root=tmp_path,
    )

    runner.run(requirement)

    seeded_final_prompts = [
        p for p in prompts
        if "research EXECUTOR improving an existing deliverable" in p
    ]
    assert seeded_final_prompts
    final_prompt = seeded_final_prompts[-1]
    assert "FULL SCORING STANDARD" in final_prompt
    assert "Citation integrity" in final_prompt
    assert "UNRESOLVED WEAKNESSES" in final_prompt
    assert "Missing limitations" in final_prompt
    assert "FETCHED EVIDENCE FILES" in final_prompt
    assert "evidence/fetched-sources.json" in final_prompt
    assert "evidence/source-001.md" in final_prompt
    assert "Cached page body only in source file" not in final_prompt
    assert (tmp_path / session.session_id / "evidence" / "source-001.md").is_file()
    prompt_text = next(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / session.session_id / "io").glob("*.in.md")
        if "research EXECUTOR improving an existing deliverable" in path.read_text(encoding="utf-8")
    )
    assert "FULL SCORING STANDARD" in prompt_text
    assert "Citation integrity" in prompt_text
    assert "UNRESOLVED WEAKNESSES" in prompt_text
    assert "FETCHED EVIDENCE FILES" in prompt_text
    assert "evidence/fetched-sources.json" in prompt_text
    assert "evidence/source-001.md" in prompt_text


def test_final_non_report_step_keeps_generic_artifact_contract() -> None:
    prompt = _final_step_instruction(
        "Compare redis and postgres.",
        "Return a recommendation.",
        scoring_rules="- Citation integrity",
        weaknesses=["[document] Missing citations."],
    )

    assert "multi-step agent workflow" in prompt
    assert "optionally refining" in prompt
    assert "final synthesis step for a research report" not in prompt
    assert "FULL SCORING STANDARD" not in prompt


# --------------------------------------------------------------------------- #
# §14.4 Epoch heartbeat — one Run auto-iterates to max_epochs                  #
# --------------------------------------------------------------------------- #


def _stub_inner(runner, monkeypatch, scripts):
    """Replace runner._run_inner with a counter that emits a HillClimbEvent per
    pass and returns scripted EpochResults — so we test run()'s loop driving in
    isolation from the heavy pipeline. ``scripts`` is a list of statuses."""
    from studio.events import HillClimbEvent
    from studio.runner import EpochResult

    calls = {"n": 0, "epochs": []}

    def _fake(_req):
        i = calls["n"]
        calls["n"] += 1
        status = scripts[min(i, len(scripts) - 1)]
        version = i + 1
        calls["epochs"].append(runner._epoch)
        runner._emit(
            HillClimbEvent(
                epoch=version, score=0.1 * version, delta=0.1, status=status,
                note=f"v{version}", weaknesses=[], task_hash="t",
            )
        )
        runner._last_result = f"out{version}"
        runner._last_cancelled = False
        return EpochResult(version=version, score=0.1 * version, delta=0.1, status=status)

    monkeypatch.setattr(runner, "_run_inner", _fake)
    return calls


def test_epoch_loop_runs_until_max_epochs(
    fake_client_factory, tmp_path, monkeypatch
) -> None:
    """auto_improve + max_epochs=3, score always improving → _run_inner runs
    exactly 3 times (converged at v3), 3 HillClimbEvents (epochs 1,2,3), and
    exactly ONE terminal done for the whole stream."""
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    events: list[StudioEvent] = []
    session = _make_session()
    session.hill_climb_config = {"auto_improve": True, "max_epochs": 3}
    runner = Runner(
        session, events.append, client_factory=fake_client_factory,
        embedder=None, workspace_root=tmp_path,
    )
    calls = _stub_inner(runner, monkeypatch, ["improving", "improving", "converged"])
    runner.run("compare redis and postgres")

    assert calls["n"] == 3
    hc = [e for e in events if e.EVENT_TYPE == "hill_climb"]
    assert [e.epoch for e in hc] == [1, 2, 3]
    done = [e for e in events if e.EVENT_TYPE == "done"]
    assert len(done) == 1
    assert done[0].result == "out3"  # carries the last pass's output


def test_epoch_loop_stops_on_plateau(
    fake_client_factory, tmp_path, monkeypatch
) -> None:
    """Scores improve then flatten (delta < min_improvement at pass 2) → loop
    breaks early (2 of 5 passes); last status is 'plateau'; one done."""
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    events: list[StudioEvent] = []
    session = _make_session()
    session.hill_climb_config = {"auto_improve": True, "max_epochs": 5}
    runner = Runner(
        session, events.append, client_factory=fake_client_factory,
        embedder=None, workspace_root=tmp_path,
    )
    calls = _stub_inner(runner, monkeypatch, ["improving", "plateau"])
    runner.run("compare redis and postgres")

    assert calls["n"] == 2  # broke early, did NOT run all 5
    hc = [e for e in events if e.EVENT_TYPE == "hill_climb"]
    assert hc[-1].status == "plateau"
    assert len([e for e in events if e.EVENT_TYPE == "done"]) == 1


def test_single_pass_when_guard_off(
    fake_client_factory, tmp_path, monkeypatch
) -> None:
    """Guard: the loop engages ONLY when auto_improve AND max_epochs > 1.
    auto_improve=False OR max_epochs=1 → exactly one _run_inner pass, one done."""
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))

    def _run_with(cfg: dict) -> tuple[int, int, list[int]]:
        events: list[StudioEvent] = []
        session = _make_session()
        session.hill_climb_config = cfg
        runner = Runner(
            session, events.append, client_factory=fake_client_factory,
            embedder=None, workspace_root=tmp_path,
        )
        calls = _stub_inner(runner, monkeypatch, ["improving"])
        runner.run("compare redis and postgres")
        n_done = len([e for e in events if e.EVENT_TYPE == "done"])
        return calls["n"], n_done, calls["epochs"]

    # auto_improve off → single pass (epoch left at 0 = un-prefixed)
    n, d, epochs = _run_with({"auto_improve": False, "max_epochs": 5})
    assert n == 1 and d == 1 and epochs == [0]
    # max_epochs == 1 → single pass even with auto_improve on
    n, d, epochs = _run_with({"auto_improve": True, "max_epochs": 1})
    assert n == 1 and d == 1 and epochs == [0]


def test_config_persists_per_task(tmp_path, monkeypatch) -> None:
    """record() snapshots the hill-climb config; latest_config returns the most
    recent NON-EMPTY one; and a fresh run with no session config seeds its
    effective epoch budget from the store (survives a backend restart)."""
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    from studio.task_runs import TaskRun, TaskRunStore, task_hash

    db = tmp_path / "task_runs.db"  # == _db_path() under this STUDIO_WORKSPACE_ROOT
    store = TaskRunStore(db_path=db)
    req = "compare redis and postgres for caching"
    th = task_hash(req)
    cfg = {"auto_improve": True, "max_epochs": 4, "min_improvement": 0.03}
    store.record(TaskRun(
        task_hash=th, session_id="s1", version=1, score=0.5, weaknesses=[],
        artifact_path="", requirement=req, result_text="x", config=cfg,
    ))
    assert store.latest_config(th) == cfg
    # A later non-hill-climb run (empty config) must NOT clobber the snapshot.
    store.record(TaskRun(
        task_hash=th, session_id="s2", version=2, score=0.6, weaknesses=[],
        artifact_path="", requirement=req, result_text="y", config={},
    ))
    assert store.latest_config(th) == cfg  # still v1's non-empty config

    # A fresh runner with NO session hill_climb_config seeds from the store.
    session = _make_session()
    runner = Runner(session, lambda _e: None, embedder=None, workspace_root=tmp_path)
    eff = runner._resolve_hc_config(req)
    assert eff.get("max_epochs") == 4 and eff.get("auto_improve") is True

    # Session config always WINS over the persisted snapshot.
    session2 = _make_session()
    session2.hill_climb_config = {"auto_improve": True, "max_epochs": 2}
    runner2 = Runner(session2, lambda _e: None, embedder=None, workspace_root=tmp_path)
    assert runner2._resolve_hc_config(req).get("max_epochs") == 2


def test_latest_with_content_falls_back_to_result_text(tmp_path, monkeypatch) -> None:
    """The hill-climb seed survives a missing artifact.md by falling back to the
    DB-persisted result_text. Studio workspaces are ephemeral AND a raw-synthesis
    run never writes artifact.md at all — keying the seed on the file alone left
    most priors seedless, which SKIPPED the keep/discard gate and let a regressed
    epoch overwrite the served deliverable (the hill-climb regression, DESIGN §14.6).
    """
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    from studio.task_runs import TaskRun, TaskRunStore, task_hash

    ws_root = tmp_path / "ws"
    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    req = "study how to use Pi and Craft to develop an agent"
    th = task_hash(req)
    # A prior run that finalized result.md (→ result_text) but NEVER wrote an
    # artifact.md to disk — exactly the failing real-world run (score 0.41, no file).
    store.record(TaskRun(
        task_hash=th, session_id="s_prior", version=1, score=0.41, weaknesses=[],
        artifact_path="", requirement=req, result_text="# Good Report\nbody",
    ))
    assert not (ws_root / "s_prior" / "artifact.md").exists()  # precondition

    prior = store.latest_with_content(th, ws_root=ws_root)
    assert prior is not None  # was None before the fix → cold start → gate skipped
    assert prior.session_id == "s_prior"
    assert prior.result_text == "# Good Report\nbody"


def test_task_run_store_persists_evidence_rows(tmp_path, monkeypatch) -> None:
    """Evidence extracted during finalization survives the task history store.

    The reducer keeps using the full scoring/evidence context in-process, while later
    inspection/export paths need the same typed evidence rows after restart.
    """
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    from studio.task_runs import TaskRun, TaskRunStore, task_hash

    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    req = "compare agent frameworks"
    th = task_hash(req)
    evidence = [{
        "id": "ev1",
        "section": "## Findings",
        "claim": "Framework A has built-in tracing",
        "url": "https://example.com/framework-a",
        "source": "Example",
    }]
    store.record(TaskRun(
        task_hash=th, session_id="s1", version=1, score=0.7, weaknesses=[],
        artifact_path="", requirement=req, result_text="# Report\nbody",
        evidence=evidence,
    ))

    latest = store.latest(th)
    best = store.best(th)
    all_runs = store.all_runs(th)
    seeded = store.latest_with_content(th, ws_root=tmp_path / "ws")

    assert latest is not None and latest.evidence == evidence
    assert best is not None and best.evidence == evidence
    assert all_runs[0].evidence == evidence
    assert seeded is not None and seeded.evidence == evidence


def test_merge_creates_missing_sections_addresses_all_weaknesses() -> None:
    """Inject weaknesses (two required sections absent from the seed) and verify the
    merge ADDRESSES ALL of them (DESIGN §14.6). A seeded hill-climb run keeps the
    seed's structure — the reducer patches existing headings but never injects a
    missing one, so a required section absent from the seed recurs as a weakness
    forever. _merge_missing_sections lays down a heading for each, closing the loop.

    Pins three behaviors: (1) every missing template section gets created; (2) after
    the merge ZERO template sections are missing — all 'missing-section' weaknesses
    addressed; (3) a renamed-but-present section is matched, not duplicated.
    """
    from studio.runner import _merge_missing_sections
    from studio.rubric import sections_present

    # Seed == the real failing case: has Exec Summary + a renamed Conclusion, but is
    # MISSING "Key Findings" and "Limitations and Open Questions" (the recurring
    # weaknesses from the screenshot).
    seed = (
        "# Report\n\n## Executive Summary\nx\n\n"
        "## Conclusion and Best Practices\ny\n"
    )
    tmpl = [
        "Executive Summary",
        "Key Findings",
        "Limitations and Open Questions",
        "Conclusion and Recommendations",
    ]
    before_missing = [
        s for s in tmpl
        if s.lower() not in {p.lower() for p in sections_present(seed, tmpl)}
    ]
    assert before_missing == ["Key Findings", "Limitations and Open Questions"]

    merged = _merge_missing_sections(seed, tmpl)

    # (1) each injected weakness now has a heading to fill
    assert "## Key Findings" in merged
    assert "## Limitations and Open Questions" in merged
    # (2) ALL missing-section weaknesses addressed — nothing left missing
    after_missing = [
        s for s in tmpl
        if s.lower() not in {p.lower() for p in sections_present(merged, tmpl)}
    ]
    assert after_missing == []
    # (3) renamed-but-present section matched, not duplicated
    assert merged.count("## Conclusion") == 1
    # idempotent: a second pass adds nothing
    assert _merge_missing_sections(merged, tmpl) == merged


def test_max_epochs_counts_per_run_not_cumulative_version(
    fake_client_factory, tmp_path, monkeypatch
) -> None:
    """max_epochs is the epoch budget for THIS run, not an all-time version ceiling.

    Bug (§14.8): the stop status is `_version >= max_epochs` where `_version` is the
    CUMULATIVE version (MAX(version)+1 over every prior run). On a task with history
    the first epoch already exceeds the cap → 'converged' → the loop ran once, so
    max_epochs=2 did ONE pass while two manual runs did two. With the fix the loop
    stops only on a real plateau, so both epochs run despite prior history.
    """
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    from studio.task_runs import TaskRun, TaskRunStore, task_hash

    req = "1. compare redis and postgres 2. write a recommendation"
    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    for v in range(1, 6):  # prior history: cumulative version already at 5 (>= max_epochs)
        store.record(TaskRun(
            task_hash=task_hash(req), session_id=f"s{v}", version=v, score=0.5,
            weaknesses=[], artifact_path="", requirement=req, result_text="x",
        ))

    events: list[StudioEvent] = []
    session = _make_session()
    # min_improvement 0 so constant fake scores never count as a plateau — isolates the
    # converged-vs-per-run-count behavior we are testing.
    session.hill_climb_config = {
        "auto_improve": True, "max_epochs": 2, "min_improvement": 0.0,
    }
    runner = Runner(
        session, events.append, client_factory=fake_client_factory,
        embedder=None, workspace_root=tmp_path,
    )
    runner.run(req)

    phase_ids = [e.step_id for e in events if e.EVENT_TYPE == "phase_start"]
    e1 = {s for s in phase_ids if s.startswith("e1:")}
    e2 = {s for s in phase_ids if s.startswith("e2:")}
    assert e1, phase_ids   # epoch 1 ran
    assert e2, phase_ids   # epoch 2 ALSO ran (before the fix it stopped after e1)


def test_seed_path_only_seeds_first_epoch_not_every_epoch(
    fake_client_factory, tmp_path, monkeypatch
) -> None:
    """hill_climb_config.seed_path must seed epoch 1 only. Epoch 2+ must carry
    forward THIS RUN's own prior epoch (via TaskRunStore.latest_with_content,
    which _store.record already persisted at the end of the prior epoch) — not
    re-pin to the same static file every epoch. Bug: the seed_path branch had no
    epoch guard, so a multi-epoch hill-climb discarded every epoch's own
    progress and restarted from the same seed each time (verified live: both
    epoch 1 and epoch 2 logged identical "seed via explicit seed_path (21899
    chars)", epoch 1's grown 29931-char result was never seen by epoch 2).
    """
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    import studio.runner as runner_mod
    from studio.task_runs import TaskRunStore

    seed_file = tmp_path / "seed.md"
    seed_file.write_text("## Section\n\nseed body\n")

    seed_calls: list[int] = []
    orig_seed_from_path = runner_mod._seed_prior_from_path

    def spy_seed_from_path(*args, **kwargs):
        seed_calls.append(1)
        return orig_seed_from_path(*args, **kwargs)

    monkeypatch.setattr(runner_mod, "_seed_prior_from_path", spy_seed_from_path)

    carry_forward_calls: list[int] = []
    orig_latest = TaskRunStore.latest_with_content

    def spy_latest(self, *args, **kwargs):
        carry_forward_calls.append(1)
        return orig_latest(self, *args, **kwargs)

    monkeypatch.setattr(TaskRunStore, "latest_with_content", spy_latest)

    req = "1. research widgets 2. write the report"
    session = _make_session()
    session.hill_climb_config = {
        "auto_improve": True, "max_epochs": 2, "min_improvement": 0.0,
        "seed_path": str(seed_file),
    }
    runner = Runner(
        session, lambda _e: None, client_factory=fake_client_factory,
        embedder=None, workspace_root=tmp_path,
    )
    runner.run(req)

    assert len(seed_calls) == 1, (
        f"seed_path must only be consulted on epoch 1, got {len(seed_calls)} calls"
    )
    assert carry_forward_calls, (
        "epoch 2 must fall back to latest_with_content (this run's own prior "
        "epoch), not re-seed from seed_path"
    )


def test_bad_mermaid_seed_reaches_reducer_with_repair_instruction(
    tmp_path, monkeypatch
) -> None:
    """Inject a malformed mermaid into the seed and verify the run (a) DETECTS it and
    (b) tells the reducer to REPAIR it in place (DESIGN §14.6). Before this, the
    reducer prompt was strictly additive ('never a rewriter', 'VERBATIM') so a broken
    diagram was immortal. We capture the prompts the model receives and assert the
    repair exception + the exact bad line are present.

    Note: actual repair needs a real model — the fake client returns a fixed string
    and cannot rewrite. This pins detection + instruction-delivery (the parts that
    were missing); end-to-end correction is a live-backend check.
    """
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
    from studio.artifact_lint import lint_artifact
    from studio.runner import Runner
    from studio.task_runs import TaskRun, TaskRunStore, task_hash

    bad_seed = (
        "# Report\n\n## Design Architecture\n\n```mermaid\ngraph TD\n"
        "    ToolSelector -->|Search| WebTool\n"
        "    ToolSelector|Read| ReadTool\n```\n\n## Conclusion\nbody\n"
    )
    assert lint_artifact(bad_seed)  # sanity: the seed IS malformed

    req = "1. research widgets 2. write the report"
    # The runner's store lives at _db_path() == workspace_root().parent/task_runs.db
    # (one level ABOVE STUDIO_WORKSPACE_ROOT=.../ws). Record the seed there so the run
    # reads the SAME db — else it cold-starts and the seed never loads.
    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    store.record(TaskRun(
        task_hash=task_hash(req), session_id="s_prior", version=1, score=0.4,
        weaknesses=[], artifact_path="", requirement=req, result_text=bad_seed,
    ))

    class _Capturing:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.n_calls = 0
            self.total_tokens = 0

        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            self.n_calls += 1
            self.total_tokens += 5
            self.prompts.append(str(messages))
            return ChatResult(text="The answer is 42.", total_tokens=5)

    cap = _Capturing()
    session = _make_session()
    session.hill_climb_config = {"auto_improve": True, "max_epochs": 1}
    runner = Runner(
        session, lambda _e: None, client_factory=lambda _o: cap,
        embedder=None, workspace_root=tmp_path / "ws",
    )
    runner.run(req)

    blob = "\n".join(cap.prompts)
    # (a) the reducer was given the malformed seed and (b) told to repair it
    assert "repair" in blob.lower(), "reducer never told to repair"
    assert "Malformed mermaid edge" in blob, "the lint weakness never reached the model"
    assert "ToolSelector|Read|" in blob, "the exact bad line was not named for repair"


class _ConstVectorEmbedder:
    """Constant-vector embedder — every text maps to the same vector, so any two
    requirements are maximally 'similar' (cosine=1.0). Used only to deterministically
    force the R10 semantic seed-fallback path (studio.task_runs.similar_runs) in this
    test; NOT a claim about real cosine behavior (the real calibration test in
    tests/test_relevance.py is exactly why the LLM relevance check exists instead of
    thresholding cosine — cosine could NOT separate the real contaminated case)."""

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def test_cross_task_seed_reaches_worker_with_relevance_repair_instruction(
    tmp_path, monkeypatch
) -> None:
    """Cross-task R10 seed (a DIFFERENT task_hash, carried forward only via semantic
    similarity) with an off-topic section must (a) get DETECTED by the binary LLM
    relevance check and (b) get a NARROW repair-in-place exception injected into the
    final-step worker prompt (mirrors test_bad_mermaid_seed_reaches_reducer_with_
    repair_instruction's structure exactly, for the relevance repair-clause instead
    of the lint repair-clause). A SAME-task continuation seed is covered separately —
    this test pins the cross-task gate (_seed_cross_task) specifically."""
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
    from studio.runner import Runner
    from studio.task_runs import TaskRun, TaskRunStore, task_hash

    req_current = "1. research widget benchmarking practices 2. write the report"
    req_prior = "1. research catalog governance for agent skills 2. write the report"
    prior_seed = (
        "# Report\n\n"
        "## Executive Summary\n\n"
        + "This catalog management report examines skill registry governance "
        "for agent loops. " * 12
        + "\n\n"
        "## Conclusion\n\n"
        + "In conclusion, catalog lifecycle governance requires careful "
        "curation. " * 12
        + "\n"
    )
    assert len(prior_seed) >= 500  # clears _seed_carry_forward's _MIN_SEED_CHARS

    # The runner's store lives at _db_path() == workspace_root().parent/task_runs.db
    # (one level ABOVE STUDIO_WORKSPACE_ROOT=.../ws) — same convention as the mermaid
    # test above. Record the prior under a DIFFERENT task_hash (req_prior != req_current)
    # so the exact-hash lookup misses and the semantic (R10) path is exercised.
    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    store.record(TaskRun(
        task_hash=task_hash(req_prior), session_id="s_prior_catalog", version=1,
        score=0.4, weaknesses=[], artifact_path="", requirement=req_prior,
        result_text=prior_seed,
    ))

    class _RelevanceAwareClient:
        """Answers the binary relevance judge's distinctive prompt (mentions
        'PARAGRAPH:' + 'VERDICT:') with IRRELEVANT whenever the section text
        mentions 'catalog' — otherwise a generic filler reply for every other
        prompt type (planner/hub/worker/reducer), mirroring _Capturing in the
        mermaid test."""

        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.n_calls = 0
            self.total_tokens = 0

        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            self.n_calls += 1
            self.total_tokens += 5
            blob = str(messages)
            self.prompts.append(blob)
            if "PARAGRAPH:" in blob and "VERDICT:" in blob:
                verdict = "IRRELEVANT" if "catalog" in blob.lower() else "RELEVANT"
                return ChatResult(
                    text=f"QUOTE: NONE\nVERDICT: {verdict}", total_tokens=5
                )
            return ChatResult(text="The answer is 42.", total_tokens=5)

    cap = _RelevanceAwareClient()
    session = _make_session()
    session.hill_climb_config = {"auto_improve": True, "max_epochs": 1}
    runner = Runner(
        session, lambda _e: None, client_factory=lambda _o: cap,
        embedder=_ConstVectorEmbedder(), workspace_root=tmp_path / "ws",
    )
    runner.run(req_current)

    blob = "\n".join(cap.prompts)
    assert "relevance-repair-in-place" in blob, "worker never told to repair relevance"
    assert "unrelated to the current task" in blob, "the relevance issue never reached the model"
    # Wording tightened (2026-07): "DROP the original then WRITE NEW" — an explicit
    # two-step instruction beats the softer "REPLACE/adapt" that let the model just
    # reword the same off-topic substance.
    assert "DROP that content entirely" in blob
    assert "WRITE NEW content addressing the CURRENT task" in blob


def test_cold_start_phase1_gets_proactive_requirement_notice(
    tmp_path,
) -> None:
    """A genuine COLD START (no prior seed) with a section template must (a) extract
    the task's explicit requirements BEFORE the phase loop and (b) inject the full
    proactive requirement list into phase 1's REDUCER prompt. This pins the earliest-
    stage per-phase wiring on the very first run of a task (not just a hill-climb
    continuation). Mirrors the mermaid/relevance integration tests' capturing-client
    structure, for the cold-start proactive path instead of a seed repair-clause."""
    from studio.runner import Runner

    class _Capturing:
        """Answers the requirement EXTRACTOR prompt with a two-item list; generic
        filler for every other prompt (planner/hub/worker/reducer)."""

        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.n_calls = 0
            self.total_tokens = 0

        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            self.n_calls += 1
            self.total_tokens += 5
            blob = str(messages)
            self.prompts.append(blob)
            if "You extract the EXPLICIT, CHECKABLE requirements" in blob:
                return ChatResult(
                    text="include a diagram\ncite at least 3 sources", total_tokens=5
                )
            return ChatResult(text="The answer is 42.", total_tokens=5)

    cap = _Capturing()
    session = _make_session(mode="llm")
    # A section template makes the cold-start skeleton bootstrap fire (§14.1), so the
    # section-aware reducer runs every phase even with no prior artifact.
    session.rubric_config = {
        "weights": None,
        "template": ["Executive Summary", "Source References"],
    }
    session.hill_climb_config = {"auto_improve": True, "max_epochs": 1}
    runner = Runner(
        session, lambda _e: None, client_factory=lambda _o: cap,
        embedder=None, workspace_root=tmp_path,
    )
    runner.run("write a sourced report on agent loops")

    # The extractor ran (before the phase loop) and the proactive notice — with the
    # full requirement list — reached a reducer prompt on this cold-start epoch.
    assert any("You extract the EXPLICIT" in p for p in cap.prompts), "extractor never ran"
    blob = "\n".join(cap.prompts)
    assert "STATED TASK REQUIREMENTS" in blob, "phase-1 proactive notice never injected"
    assert "include a diagram" in blob
    assert "cite at least 3 sources" in blob


def test_cross_task_seed_injects_unconditional_adaptation_notice(
    tmp_path, monkeypatch
) -> None:
    """Fix 1 (defense-in-depth): whenever a run is seeded from a cross-task R10 seed,
    the worker/reducer prompt gets the UNCONDITIONAL 'CROSS-TASK SEED' adaptation notice
    — independent of whether the LLM relevance classifier flagged any section. Here the
    classifier votes RELEVANT for everything (never fires the conditional repair clause),
    yet the unconditional notice must still appear. A NON-cross-task (cold-start) run must
    NOT get it."""
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
    from studio.runner import Runner
    from studio.task_runs import TaskRun, TaskRunStore, task_hash

    req_current = "1. research widget benchmarking practices 2. write the report"
    req_prior = "1. research gadget calibration methods 2. write the report"
    prior_seed = (
        "# Report\n\n## Executive Summary\n\n"
        + "This report covers benchmarking methodology in depth. " * 12
        + "\n\n## Conclusion\n\n"
        + "In conclusion the methodology is sound. " * 12
        + "\n"
    )
    assert len(prior_seed) >= 500

    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    store.record(TaskRun(
        task_hash=task_hash(req_prior), session_id="s_prior_gadget", version=1,
        score=0.4, weaknesses=[], artifact_path="", requirement=req_prior,
        result_text=prior_seed,
    ))

    class _AlwaysRelevantClient:
        """Every relevance verdict is RELEVANT, so the conditional repair clause never
        fires — isolating the unconditional notice."""

        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.n_calls = 0
            self.total_tokens = 0

        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            self.n_calls += 1
            self.total_tokens += 5
            blob = str(messages)
            self.prompts.append(blob)
            if "PARAGRAPH:" in blob and "VERDICT:" in blob:
                return ChatResult(text="QUOTE: NONE\nVERDICT: RELEVANT", total_tokens=5)
            return ChatResult(text="The answer is 42.", total_tokens=5)

    cap = _AlwaysRelevantClient()
    session = _make_session()
    session.hill_climb_config = {"auto_improve": True, "max_epochs": 1}
    runner = Runner(
        session, lambda _e: None, client_factory=lambda _o: cap,
        embedder=_ConstVectorEmbedder(), workspace_root=tmp_path / "ws",
    )
    runner.run(req_current)

    blob = "\n".join(cap.prompts)
    assert "CROSS-TASK SEED" in blob, "unconditional cross-task notice never reached the worker"
    assert "relevance-repair-in-place" not in blob, "conditional clause should NOT fire when all sections are relevant"

    # A cold-start run (empty store, nothing to seed from) must NOT get the notice.
    # Use a FRESH parent dir so the store (workspace_root().parent/task_runs.db) is
    # empty — the constant embedder would otherwise make the gadget prior look similar.
    fresh_ws = tmp_path / "fresh" / "ws2"
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(fresh_ws))
    fresh_ws.mkdir(parents=True, exist_ok=True)
    cap2 = _AlwaysRelevantClient()
    session2 = _make_session()
    session2.hill_climb_config = {"auto_improve": True, "max_epochs": 1}
    runner2 = Runner(
        session2, lambda _e: None, client_factory=lambda _o: cap2,
        embedder=_ConstVectorEmbedder(), workspace_root=fresh_ws,
    )
    runner2.run("1. research a wholly novel unrelated subject 2. write the report")
    assert "CROSS-TASK SEED" not in "\n".join(cap2.prompts), (
        "cold-start run must not get the cross-task adaptation notice"
    )


def test_step_ids_namespaced_across_epochs(
    fake_client_factory, tmp_path, monkeypatch
) -> None:
    """Across in-process epochs the real pipeline's emitted step ids are prefixed
    per epoch (e1:*, e2:*) so each pass is a distinct, collision-free sub-DAG."""
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    events: list[StudioEvent] = []
    session = _make_session()
    # Constant fake scores plateau at v2 → loop runs exactly 2 passes (e1, e2).
    session.hill_climb_config = {"auto_improve": True, "max_epochs": 2}
    runner = Runner(
        session, events.append, client_factory=fake_client_factory,
        embedder=None, workspace_root=tmp_path,
    )
    runner.run("1. compare redis and postgres 2. write a recommendation")

    phase_ids = [e.step_id for e in events if e.EVENT_TYPE == "phase_start"]
    e1 = {s for s in phase_ids if s.startswith("e1:")}
    e2 = {s for s in phase_ids if s.startswith("e2:")}
    assert e1, phase_ids                 # epoch 1 emitted prefixed ids
    assert e2, phase_ids                 # epoch 2 emitted prefixed ids
    assert e1.isdisjoint(e2)             # no collision across epochs
    assert len([e for e in events if e.EVENT_TYPE == "done"]) == 1


def test_parse_findings_accepts_json_format() -> None:
    """oMLX local models (qwen) emit the finding as JSON, not the plain
    ARTICLE_TITLE:/URL: lines — which the line-parser misses (the Pi/Craft run
    fetched but parsed 0 findings). JSON-wrapped findings must parse too; a plain
    code fence must NOT false-positive."""
    from studio.tools import _fetch_cache
    from studio.runner import _parse_findings
    _fetch_cache.clear()  # empty cache → a valid-URL finding is kept (isolate parsing)

    qwen = (
        '```json\n{ "RESEARCH_FINDING": {'
        ' "ARTICLE_TITLE": "Custom Agent Framework with PI",'
        ' "URL": "https://nader.substack.com/p/pi",'
        ' "QUOTE": "PI is a TypeScript toolkit.", "WHY": "Explains PI." } }\n```'
    )
    fs = _parse_findings(qwen)
    assert len(fs) == 1
    assert fs[0].url == "https://nader.substack.com/p/pi"
    assert fs[0].title == "Custom Agent Framework with PI"

    plain = (
        "RESEARCH_FINDING:\nARTICLE_TITLE: Custom Agent Framework with PI\n"
        "URL: https://nader.substack.com/p/pi\nQUOTE: PI is a TypeScript toolkit."
    )
    assert len(_parse_findings(plain)) == 1  # plain-text path unchanged

    # a non-finding code fence must not become a finding
    assert _parse_findings("see ```python\nprint('hi')\n```") == []


def test_dedupe_plan_steps_collapses_duplicate_phases() -> None:
    """Any planner (seeded / LLM-epic / deterministic) can emit the same phase twice (the
    Pi/Craft run showed 'craft agent' and 'create a report' duplicated). The path-agnostic
    _dedupe_plan_steps collapses content-duplicates and remaps depends_on so the DAG has no
    duplicate phases and no dangling deps — applied to the FINAL plan, not one planner."""
    from agentkit.planner.core import Plan, PlanStep
    from studio.runner import _dedupe_plan_steps

    steps = (
        PlanStep(id="s1", description="study pi", depends_on=()),
        PlanStep(id="s2", description="craft agent", depends_on=("s1",)),
        PlanStep(id="s3", description="create a report", depends_on=("s2",)),
        PlanStep(id="s4", description="craft agent", depends_on=("s1",)),       # dup of s2
        PlanStep(id="s5", description="create a report", depends_on=("s4",)),   # dup of s3
    )
    out = _dedupe_plan_steps(Plan(task="t", steps=steps))
    descs = [s.description for s in out.steps]
    assert descs.count("craft agent") == 1         # duplicate phase collapsed
    assert descs.count("create a report") == 1
    assert len(out.steps) == 3                       # study pi, craft agent, create a report
    kept_ids = {s.id for s in out.steps}
    for s in out.steps:
        assert all(d in kept_ids for d in s.depends_on)  # no dangling deps (s4 remapped → s2)

    # no-op when there are no duplicates (returns the plan unchanged)
    clean = _dedupe_plan_steps(Plan(task="t", steps=steps[:3]))
    assert len(clean.steps) == 3


# --- semantic seed fallback: local/remote loop-seed carries forward a real prior ---

def test_pick_seed_with_content_skips_empty_and_prefers_latest(tmp_path):
    from studio.runner import _pick_seed_with_content
    from types import SimpleNamespace as NS

    sims = [
        (NS(session_id="empty1", result_text="", score=0.0, task_hash="h1"), 0.98),
        (NS(session_id="empty2", result_text="x", score=0.0, task_hash="h2"), 0.97),
        (NS(session_id="old", result_text="C" * 800, score=0.92, task_hash="h3"), 0.94),
        (NS(session_id="new", result_text="C" * 800, score=0.67, task_hash="h4"), 0.90),
    ]
    # no recency → closest-similarity that has content (skips the two empties)
    r = _pick_seed_with_content(sims, tmp_path, 500)
    assert r and r[0].session_id == "old"
    # multiple content-bearing + recency → the LATEST wins
    rec = {"old": 10, "new": 20}
    r2 = _pick_seed_with_content(sims, tmp_path, 500, recency_fn=lambda s: rec.get(s, 0))
    assert r2 and r2[0].session_id == "new"
    # nothing has content → genuine cold start
    assert _pick_seed_with_content(sims[:2], tmp_path, 500, recency_fn=lambda s: 0) is None


def test_seed_prior_from_path_reads_file_and_falls_back(tmp_path):
    from studio.runner import _seed_prior_from_path

    seed = tmp_path / "seed.md"
    seed.write_text("# Strong seed\n" + "content " * 100)
    # explicit file → synthetic prior carrying the file body as result_text
    p = _seed_prior_from_path(str(seed), "thash1", "req")
    assert p is not None
    assert p.task_hash == "thash1"
    assert p.result_text.startswith("# Strong seed")
    # session_id must NOT resolve to a real artifact.md → forces result_text seeding
    assert p.session_id.startswith("__seedfile__")
    # blank / missing / empty → None (caller falls back to DB seeding)
    assert _seed_prior_from_path("", "t", "r") is None
    assert _seed_prior_from_path(str(tmp_path / "nope.md"), "t", "r") is None
    empty = tmp_path / "empty.md"; empty.write_text("   \n")
    assert _seed_prior_from_path(str(empty), "t", "r") is None


def test_seed_sync_writes_section_files_preserving_full_seed(tmp_path):
    """Seeding must split the seed into section files, not just write artifact.md.

    Regression: a 22K seed collapsed to ~5K in epoch 1 because the seed path
    wrote artifact.md only and left the section files (the source of truth the
    phase loop reassembles from) as stale scaffold. The first assemble then
    silently gutted the seed. Guard the load-bearing property: sync + assemble
    round-trips the FULL seed, including a heading absent from the template.
    """
    from studio.runner import _sync_section_workspace
    from studio.section_workspace import assemble_artifact_from_sections, SECTIONS_DIR

    session = _make_session(mode="llm")
    # template covers only two of the seed's three sections; "Custom Deep Dive"
    # is NOT in the template — it must still survive the split (no gutting).
    session.rubric_config = {"active_template": ["Executive Summary", "References"]}

    seed = (
        "# Report\n\n"
        "## Executive Summary\n\n" + "summary body. " * 40 + "\n\n"
        "## Custom Deep Dive\n\n" + "deep analysis body. " * 40 + "\n\n"
        "## References\n\n- [1] https://example.com\n"
    )
    (tmp_path / session.session_id).mkdir(parents=True)

    _sync_section_workspace(session, tmp_path, seed)

    # section files were actually written (the bug: they weren't)
    assert (tmp_path / session.session_id / SECTIONS_DIR).is_dir()
    assembled = assemble_artifact_from_sections(tmp_path / session.session_id)
    # full seed preserved — no round-1 shrink, non-template section kept
    assert "Custom Deep Dive" in assembled
    assert "deep analysis body." in assembled
    assert len(assembled) >= len(seed) - 50  # lossless (title/whitespace tolerance)


def test_session_recency_returns_max_row_id(tmp_path):
    from studio.task_runs import TaskRun, TaskRunStore
    st = TaskRunStore(db_path=tmp_path / "tr.db")
    st.record(TaskRun("h", "s1", 1, 0.5, [], "", "r", "body"))
    st.record(TaskRun("h", "s2", 2, 0.6, [], "", "r", "body2"))
    assert st.session_recency("s2") > st.session_recency("s1")
    assert st.session_recency("nope") == 0


# --- Editor phase (goal-aware final quality pass) ---------------------------

_EDITOR_MARKER = "PENDING-DETAIL"
_EDITOR_REPLACEMENT = "grounded detail with a citation https://x.test/e"


def _editor_artifact_text() -> str:
    return (
        "# Agent Loops Report\n\n"
        "## Executive Summary\n\n"
        f"Summary body. {_EDITOR_MARKER}\n\n"
        "## Key Findings\n\n"
        "Findings body with detail.\n\n"
        "## References\n\n"
        "- https://x.test/e\n"
    )


def _editor_session():
    session = _make_session()  # tools_enabled defaults True
    session.rubric_config = {
        "weights": None,
        "template": ["Executive Summary", "Key Findings", "References"],
        "scoring_template": ["Executive Summary", "Key Findings", "References"],
        "scoring_matrix": [
            {"category": "Scope", "points": 50, "signal": "structure"},
            {"category": "Citation integrity", "points": 50, "signal": "verification"},
        ],
    }
    return session


def _build_editor_ws(tmp_path, session, text):
    """Write artifact.md + sections/ (the pre-editor state), return (root, art_file)."""
    from studio.section_workspace import write_section_workspace
    root = tmp_path / session.session_id
    root.mkdir(parents=True, exist_ok=True)
    write_section_workspace(root, text, ["Executive Summary", "Key Findings", "References"])
    return root, root / "artifact.md"


class _EditorPatchClient:
    """A base client that, once per pass, reads the artifact hash then applies ONE
    patch_artifact find/replace. Subsequent turns are no-ops. Records the top-level
    prompt of each turn so batching structure can be asserted.

    ``prompts`` dedups by exact text across the WHOLE run (legacy "was this prompt
    ever sent" checks). ``turn_prompts`` is run-length collapsed instead (a new
    entry only when the text differs from the IMMEDIATELY PRECEDING one) — this
    correctly counts one entry per ``_editor_drive_round`` turn even when a turn's
    text is byte-identical across round 1 and round 2 (same outline/requirement),
    since those two turns are never adjacent (other distinct turns run between).
    """

    def __init__(self, find=_EDITOR_MARKER, replace=_EDITOR_REPLACEMENT) -> None:
        self.find, self.replace = find, replace
        self.patched = False
        self.prompts: list[str] = []
        self.turn_prompts: list[str] = []

    def chat(self, messages, tools=None):
        from agentkit.types import ChatResult
        first = messages[0].get("content") if messages else ""
        if isinstance(first, str) and messages[-1].get("role") == "user":
            if first not in self.prompts:
                self.prompts.append(first)
            if not self.turn_prompts or self.turn_prompts[-1] != first:
                self.turn_prompts.append(first)
        doc_hash = ""
        for m in messages:
            content = m.get("content")
            if isinstance(content, str) and '"doc_hash"' in content:
                try:
                    doc_hash = json.loads(content).get("doc_hash", "")
                except Exception:
                    pass
        if not self.patched and not doc_hash:
            return ChatResult(text="", total_tokens=1, tool_calls=[("read_artifact", {})])
        if not self.patched and doc_hash:
            self.patched = True
            return ChatResult(text="", total_tokens=1, tool_calls=[
                ("patch_artifact", {"find": self.find, "replace": self.replace, "expected_hash": doc_hash})
            ])
        return ChatResult(text="done", total_tokens=1)


def _run_editor(tmp_path, session, client, scores, monkeypatch):
    """Invoke _run_editor_pass with a scripted _editor_scored_issues sequence.

    NOTE on ``scores`` length: a round that ACCEPTS consumes 2 mocked calls (cur,
    new). A round that REVERTS consumes 3 (cur, new, PLUS the fresh post-revert
    recompute that feeds the emitted detail/feedback/returned weaknesses)."""
    from studio import runner as _runner_mod
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    seq = iter(scores)
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: next(seq))
    events: list = []
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=client,
        scored_text=art_file.read_text(encoding="utf-8"),
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
    )
    return root, art_file, final, weaknesses, events


def test_editor_offers_six_file_tools(tmp_path, monkeypatch) -> None:
    """The editor pass must offer exactly the 6-tool set: the original 4 plus the
    two file primitives wired in this session (edit_file, glob)."""
    from studio import runner as _runner_mod
    captured: dict = {}
    real_cls = _runner_mod.ToolAugmentedClient

    def _spy(*args, **kwargs):
        captured["offer_tools"] = kwargs.get("offer_tools")
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(_runner_mod, "ToolAugmentedClient", _spy)
    session = _editor_session()
    # Empty issue list on the first score → loop breaks before any LLM turn, but
    # the editor_client (and thus offer_tools) is still constructed.
    _run_editor(tmp_path, session, _EditorPatchClient(), [(0.0, [])], monkeypatch)
    assert captured["offer_tools"] == {
        "read_file", "search_evidence", "read_artifact",
        "patch_artifact", "edit_file", "glob",
    }


def test_editor_round1_improves_round2_still_runs_and_batches_turns(tmp_path, monkeypatch) -> None:
    """Round 1 IMPROVES (score up AND fewer weaknesses — but only PARTIALLY: 4
    issues -> 1, not all the way to zero) → accepted as the new baseline, NOT an
    early stop. Round 2 still runs (budget remains, one issue remains) and keeps
    improving. Within each round the issues are batched into small fix-turns plus
    a ToC turn and a self-eval turn (weak-model batching)."""
    session = _editor_session()
    client = _EditorPatchClient()
    # Round 1: 4 issues, _EDITOR_CHUNK == 3 → 2 fix-turns; partial improvement to 1
    # issue (NOT zero) — proves "improved" only requires strictly-better-on-both-axes,
    # not full clearance. Round 2: cur re-check sees the 1 remaining issue, drives
    # ONE more fix-turn (+ ToC + self-eval), fully resolves it.
    root, art_file, final, weaknesses, events = _run_editor(
        tmp_path, session, client,
        scores=[
            (0.4, ["w1", "w2", "w3", "w4"]),  # round1 cur
            (0.7, ["w4"]),                     # round1 new: partial improve, 1 left → accept
            (0.7, ["w4"]),                     # round2 cur: re-derived from accepted baseline
            (0.95, []),                        # round2 new: fully resolved → accept
        ],
        monkeypatch=monkeypatch,
    )
    fix_turns = [p for p in client.turn_prompts if "Fix ONLY these specific issues" in p]
    toc_turns = [p for p in client.turn_prompts if "table of contents" in p]
    eval_turns = [p for p in client.turn_prompts if "SCORING STANDARD" in p]
    assert len(fix_turns) == 3  # round1: 2 batched fix-turns (4/3) + round2: 1 fix-turn
    assert len(toc_turns) == 2 and len(eval_turns) == 2  # one ToC + one self-eval PER round
    accepts = [e for e in events if getattr(e, "name", "") == "editor_round" and e.outcome == "accept"]
    assert len(accepts) == 2  # BOTH rounds ran and both accepted — no early stop on round-1 success
    assert "round 1 improved" in accepts[0].detail
    assert "round 2 improved" in accepts[1].detail
    # No feedback text: round 1 was a success, not a revert, so nothing to ingest.
    assert not any("PRIOR ROUND FEEDBACK" in p for p in client.turn_prompts)
    # Returned weaknesses is the FRESH post-round-2 recompute (round2's "new_issues"),
    # not round 1's stale partial list — round 1's ["w4"] was replaced, not merged.
    assert weaknesses == []


def test_editor_round1_reverts_feeds_forward_to_round2_then_hard_stops(tmp_path, monkeypatch) -> None:
    """Round 1 CANNOT improve (regresses/flat) → full revert (artifact.md + every
    section file + active_outline.json) + reject GateEvent, exactly as before —
    but round 2 still runs (does NOT stop after the revert), with round 1's
    failure ingested as feedback in round 2's fix-turn prompt so it isn't blindly
    repeated. Round 2 ALSO cannot improve here → its own revert fires and the
    hard round cap (2) ends the loop — no round 3 (only 6 scripted score-calls
    exist; a round 3 attempt would raise StopIteration and fail the test). A
    reverting round consumes 3 mocked calls (cur, new, PLUS the fresh post-revert
    recompute) — never 2 — because weaknesses are ALWAYS freshly recomputed
    against the actually-current (here: just-restored) state, never reused from
    before the round ran."""
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    from studio.section_workspace import SECTIONS_DIR
    sections_dir = root / SECTIONS_DIR
    before_art = art_file.read_text(encoding="utf-8")
    before_files = {p.name: p.read_text(encoding="utf-8") for p in sections_dir.iterdir() if p.is_file()}

    from studio import runner as _runner_mod
    client = _EditorPatchClient()
    seq = iter([
        (0.6, ["w1"]),               # round1 cur
        (0.3, ["w1", "w2", "w3"]),   # round1 new: regression
        (0.6, ["w1"]),               # round1 fresh post-revert recompute → feeds feedback
        (0.6, ["w1"]),               # round2 cur: matches round1's post-revert state
        (0.5, ["w1", "w2"]),         # round2 new: regression again
        (0.6, ["w1"]),               # round2 fresh post-revert recompute → hard stop
    ])
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: next(seq))
    events: list = []
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=client,
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
    )

    # Full revert (both rounds regressed): artifact.md, every section file, and
    # active_outline.json all restored to the ORIGINAL pre-editor snapshot.
    assert art_file.read_text(encoding="utf-8") == before_art
    after_files = {p.name: p.read_text(encoding="utf-8") for p in sections_dir.iterdir() if p.is_file()}
    assert after_files == before_files
    assert _EDITOR_REPLACEMENT not in art_file.read_text(encoding="utf-8")
    assert final == before_art
    # Returned weaknesses is the FRESH recompute after round 2's revert (== ["w1"],
    # matching the restored state) — never round 1's or round 2's stale pre-round list.
    assert weaknesses == ["w1"]

    rejects = [e for e in events if getattr(e, "name", "") == "editor_round" and e.outcome == "reject"]
    assert len(rejects) == 2  # round 1 AND round 2 both reverted — round 2 was reached
    assert "reverted" in rejects[0].detail and "reverted" in rejects[1].detail
    # Round 1's failure (its FRESH post-revert weaknesses + the score/weakness delta) was
    # ingested into round 2's fix-turn prompt so round 2 doesn't blindly repeat the attempt.
    feedback_turns = [p for p in client.turn_prompts if "PRIOR ROUND FEEDBACK" in p]
    assert feedback_turns, "round 2 must see round 1's feedback"
    assert "did NOT improve" in feedback_turns[0] and "w1" in feedback_turns[0]


def test_editor_noop_when_no_issues(tmp_path, monkeypatch) -> None:
    """No weaknesses and no lint issues → the loop breaks immediately: no LLM turn,
    no gate event, artifact unchanged."""
    session = _editor_session()
    client = _EditorPatchClient()
    root, art_file, final, weaknesses, events = _run_editor(
        tmp_path, session, client,
        scores=[(0.95, [])],  # cur has no issues → break before any editing
        monkeypatch=monkeypatch,
    )
    assert client.prompts == []  # editor never called the model
    assert not [e for e in events if getattr(e, "name", "") == "editor_round"]
    assert final == art_file.read_text(encoding="utf-8")
    assert _EDITOR_REPLACEMENT not in final
    assert weaknesses == []  # fresh (empty) list, not None — the editor DID run and check


def test_editor_round2_skipped_when_round1_resolves_everything(tmp_path, monkeypatch) -> None:
    """Round 1 clears every weakness/lint issue (down to zero) → round 2's
    top-of-loop "nothing left to do" check short-circuits it: no second drive
    round, no second gate event, no LLM turns beyond round 1's."""
    session = _editor_session()
    client = _EditorPatchClient()
    root, art_file, final, weaknesses, events = _run_editor(
        tmp_path, session, client,
        scores=[
            (0.4, ["w1", "w2"]),  # round1 cur
            (0.8, []),             # round1 new: fully resolved → accept
            (0.8, []),             # round2 cur: zero issues → break, no round-2 drive
        ],
        monkeypatch=monkeypatch,
    )
    assert _EDITOR_REPLACEMENT in final
    assert _EDITOR_REPLACEMENT in art_file.read_text(encoding="utf-8")
    gates = [e for e in events if getattr(e, "name", "") == "editor_round"]
    assert len(gates) == 1 and gates[0].outcome == "accept"  # round 2 never even started
    assert weaknesses == []  # fresh recompute at round 2's top-of-loop, still empty


def test_editor_skipped_without_scoring_matrix(tmp_path, monkeypatch) -> None:
    """No rubric scoring_matrix → the editor is a no-op (nothing to optimize)."""
    session = _make_session()  # no rubric_config
    client = _EditorPatchClient()
    from studio import runner as _runner_mod
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    # If it ran, this would raise (empty iterator) — proves the gate short-circuits.
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=client,
        scored_text=art_file.read_text(encoding="utf-8"),
        verified_urls=[],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write a report",
        emit=lambda _e: None,
        workspace_root=tmp_path,
    )
    assert client.prompts == []
    assert final == art_file.read_text(encoding="utf-8")
    assert weaknesses is None  # editor never ran → caller must leave its own list untouched


# --- relevance check (studio.relevance): editor 4th action ------------------

def test_editor_scored_issues_unions_extra_issues_without_new_llm_call() -> None:
    """``extra_issues`` (studio.relevance, precomputed ONCE upstream per epoch)
    unions into ``_editor_scored_issues``'s returned issue list exactly like
    ``lint_artifact``'s output already does — a pure ADDITION, no re-derivation,
    no new I/O inside this function (it must stay cheap: called several times
    per editor round)."""
    from studio.runner import _editor_scored_issues
    session = _editor_session()
    text = _editor_artifact_text()
    extra = ["section 'Executive Summary' appears unrelated to the current task (relevance check: NO)"]
    _, issues_without = _editor_scored_issues(session, text, ["https://x.test/e"])
    _, issues_with = _editor_scored_issues(session, text, ["https://x.test/e"], extra_issues=extra)
    assert extra[0] in issues_with
    assert extra[0] not in issues_without
    # dedup: an issue already present (mined by rubric/lint) is not duplicated.
    _, issues_dup = _editor_scored_issues(
        session, text, ["https://x.test/e"], extra_issues=issues_with[:1]
    )
    assert issues_dup.count(issues_with[0]) == 1


def test_editor_pass_threads_relevance_extra_issues_into_every_round_call(
    tmp_path, monkeypatch
) -> None:
    """The relevance issues/penalty computed ONCE per epoch upstream (in
    _run_phase_loop, studio.relevance) must reach EVERY ``_editor_scored_issues``
    call inside a round — confirming the 4th weakness type flows into the round
    loop as a pure union. Round mechanics (<=2 rounds, revert-on-regression,
    fresh-recompute) are already pinned by test_editor_round1_*; this test only
    verifies the NEW threading, mirroring the existing scripted-sequence mocking
    style used throughout this file."""
    from studio import runner as _runner_mod
    session = _editor_session()
    client = _EditorPatchClient()
    calls: list[tuple] = []
    seq = iter([
        (0.4, ["w1", "relevance issue"]),  # round1 cur
        (0.9, []),                          # round1 new: fully resolved → accept
        (0.9, []),                          # round2 cur: zero issues → break, no round-2 drive
    ])

    def _spy(*a, **k):
        calls.append(a)
        return next(seq)

    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", _spy)
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    my_extra = [
        "section 'Executive Summary' appears unrelated to the current task (relevance check: NO)"
    ]
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=client,
        scored_text=art_file.read_text(encoding="utf-8"),
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=lambda _e: None,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        extra_issues=my_extra,
        relevance_penalty=0.5,
    )
    assert calls, "editor never scored"
    for c in calls:
        # positional call shape: (session, text, verified_urls, extra_issues, relevance_penalty)
        assert c[3] == my_extra
        assert c[4] == 0.5


# --- soft quality-opportunity tie-breaker (studio.requirement_compliance) ----

def test_editor_soft_opportunity_accept_keeps_flat_round_that_added_the_alternative(
    tmp_path, monkeypatch
) -> None:
    """A round with a FLAT hard score (no rubric/weakness improvement) that would
    normally be reverted is KEPT when it strictly reduced the outstanding OR-sibling
    opportunity count (e.g. it added the optional diagram). This is the narrow
    accept-path Codex asked for so a successfully-added requested visual is not
    discarded purely because the hard score did not move."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    # Flat score AND flat weakness count round-over-round → normal gate reverts.
    seq = iter([
        (0.5, ["w1"]),  # round1 cur
        (0.5, ["w1"]),  # round1 new: flat score, flat weaknesses → normal REVERT trigger
        (0.9, []),      # round2 cur: nothing left → break
    ])
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: next(seq))
    # Opportunity present pre-round (count 1); the edit adds the alternative, so the
    # patched text (which contains _EDITOR_REPLACEMENT) recounts to 0.
    recount = lambda t: 0 if _EDITOR_REPLACEMENT in t else 1
    events: list = []
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=_EditorPatchClient(),
        scored_text=art_file.read_text(encoding="utf-8"),
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        quality_opportunities=["Explicit alternative not included: include a cost-benefit analysis"],
        opportunity_recount=recount,
    )
    # KEPT, not reverted: the round-1 edit survives in the final artifact.
    assert _EDITOR_REPLACEMENT in final
    assert _EDITOR_REPLACEMENT in art_file.read_text(encoding="utf-8")
    accepts = [e for e in events if getattr(e, "name", "") == "editor_round" and e.outcome == "accept"]
    rejects = [e for e in events if getattr(e, "name", "") == "editor_round" and e.outcome == "reject"]
    assert len(accepts) == 1 and not rejects
    assert "reduced optional opportunities" in accepts[0].detail


def test_editor_flat_round_still_reverts_when_no_opportunity_reduced(
    tmp_path, monkeypatch
) -> None:
    """Negative control: identical flat-score round, but the opportunity count does
    NOT drop → the soft accept-path does NOT fire and the existing
    revert-on-regression protection stands. Proves the tie-breaker did not weaken
    the hard gate — it only rescues rounds that genuinely reduced opportunities."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    seq = iter([
        (0.5, ["w1"]),  # round1 cur
        (0.5, ["w1"]),  # round1 new: flat → REVERT
        (0.5, ["w1"]),  # round1 fresh post-revert recompute
        (0.6, []),      # round2 cur: nothing left → break
    ])
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: next(seq))
    recount = lambda t: 1  # opportunity count never drops → no soft accept
    events: list = []
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=_EditorPatchClient(),
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        quality_opportunities=["Explicit alternative not included: include a cost-benefit analysis"],
        opportunity_recount=recount,
    )
    # Reverted: the round-1 edit is gone, artifact restored to the pre-editor snapshot.
    assert _EDITOR_REPLACEMENT not in final
    assert final == before_art
    rejects = [e for e in events if getattr(e, "name", "") == "editor_round" and e.outcome == "reject"]
    assert len(rejects) == 1


def test_recount_returns_none_when_compliance_check_fail_opens(monkeypatch) -> None:
    """Bug 1: a FAILED compliance re-check (client down / LLM error / unparseable
    reply) must surface as UNKNOWN (None), not a spurious 0. Otherwise the
    tie-breaker reads "0 opportunities remaining" as "the opportunity was fulfilled"
    and soft-accepts a round nothing actually verified. The recount wrapper now
    calls ``requirement_compliance_issues(strict=True)``, which raises on the
    fail-open paths instead of returning ``(0.0, [], [])``."""
    import types as _types
    from agentkit.types import ChatResult
    from studio.runner import Runner

    class _FailOpenClient:
        # A reply with NO parseable "REQUIREMENT n: SATISFIED/NOT" verdicts →
        # requirement_compliance_issues would normally fail-open to (0.0, [], []).
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(text="the model said something unparseable", total_tokens=1)

    fake_self = _types.SimpleNamespace(_task_requirements=["include code || include a diagram"])
    recount = Runner._make_opportunity_recount(fake_self, _FailOpenClient())
    # UNKNOWN, not 0 — the empty opportunity list a fail-open would have produced must
    # NEVER be read as a real "zero remaining".
    assert recount("some candidate artifact text with real content") is None


def test_editor_soft_accept_does_not_fire_when_recount_fail_opens(
    tmp_path, monkeypatch
) -> None:
    """Bug 1 end-to-end: when the opportunity recount is UNAVAILABLE (returns None
    because the compliance re-check fail-opened), a flat-score round that would be
    reverted stays reverted — the soft accept-path must NOT fire on an unknown
    recount, or a silently-failed verification gets misread as success."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    seq = iter([
        (0.5, ["w1"]),  # round1 cur
        (0.5, ["w1"]),  # round1 new: flat → REVERT unless soft-accept rescues it
        (0.5, ["w1"]),  # round1 fresh post-revert recompute
        (0.6, []),      # round2 cur: nothing left → break
    ])
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: next(seq))
    recount = lambda t: None  # compliance re-check unavailable → UNKNOWN for every text
    events: list = []
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=_EditorPatchClient(),
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        quality_opportunities=["Explicit alternative not included: include a cost-benefit analysis"],
        opportunity_recount=recount,
    )
    # Reverted, NOT soft-accepted: an unknown recount can never manufacture success.
    assert _EDITOR_REPLACEMENT not in final
    assert final == before_art
    rejects = [e for e in events if getattr(e, "name", "") == "editor_round" and e.outcome == "reject"]
    accepts = [e for e in events if getattr(e, "name", "") == "editor_round" and e.outcome == "accept"]
    assert len(rejects) == 1 and not accepts


def test_editor_soft_accept_does_not_fire_on_partial_parse_recount(
    tmp_path, monkeypatch
) -> None:
    """Bug 3 end-to-end: the recount's verifier RESPONDS but omits a per-branch
    verdict line (a genuine PARTIAL parse — not a total failure, not a complete
    reply). ``strict=True`` now raises on that partial parse, so
    ``_make_opportunity_recount`` returns None (UNKNOWN) and the editor tie-breaker
    must STILL refuse to soft-accept — closing the exact exploit Codex described,
    where a dropped OR-sibling verdict fabricates a "0 opportunities remaining"
    success. Distinct from ``..._when_recount_fail_opens`` (a WHOLE-response
    failure): here the model answered, just incompletely."""
    import types as _types
    from agentkit.types import ChatResult
    from studio import runner as _runner_mod
    from studio.runner import Runner

    class _PartialParseClient:
        # The OR group has 2 branches; the verifier answers branch 1 only and DROPS
        # branch 2 — a real partial parse (the reply is non-empty and parseable).
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(text="REQUIREMENT 1: SATISFIED", total_tokens=1)

    fake_self = _types.SimpleNamespace(
        _task_requirements=[["include code", "include a design architecture"]]
    )
    recount = Runner._make_opportunity_recount(fake_self, _PartialParseClient())
    # Directly: the partial parse → strict raise → UNKNOWN (None), NOT a spurious 0.
    assert recount("some candidate artifact text with real content") is None

    # End-to-end through the editor pass: a flat-score round that would be reverted
    # stays reverted — the UNKNOWN recount can never manufacture a soft accept.
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    seq = iter([
        (0.5, ["w1"]),  # round1 cur
        (0.5, ["w1"]),  # round1 new: flat → REVERT unless soft-accept rescues it
        (0.5, ["w1"]),  # round1 fresh post-revert recompute
        (0.6, []),      # round2 cur: nothing left → break
    ])
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: next(seq))
    events: list = []
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=_EditorPatchClient(),
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        quality_opportunities=["Explicit alternative not included: include a cost-benefit analysis"],
        opportunity_recount=recount,
    )
    # Reverted, NOT soft-accepted: a partial-parse recount is UNKNOWN, never success.
    assert _EDITOR_REPLACEMENT not in final
    assert final == before_art
    rejects = [e for e in events if getattr(e, "name", "") == "editor_round" and e.outcome == "reject"]
    accepts = [e for e in events if getattr(e, "name", "") == "editor_round" and e.outcome == "accept"]
    assert len(rejects) == 1 and not accepts


def test_editor_soft_accept_rejects_weakness_swap_at_equal_count(
    tmp_path, monkeypatch
) -> None:
    """Bug 2: a round that removes one distinct weakness but introduces a DIFFERENT
    new one keeps the count equal, reduces the opportunity count, and — under the
    old count-only ``len(new) <= len(cur)`` check — would be soft-accepted, silently
    admitting a net-new weakness. The check is now an IDENTITY comparison on
    normalized weaknesses (no net-new distinct weakness allowed), so this round is
    REVERTED even though the opportunity count dropped."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    seq = iter([
        (0.5, ["w1"]),  # round1 cur
        (0.5, ["w2"]),  # round1 new: SWAP — flat score, EQUAL count, but a distinct new weakness
        (0.5, ["w1"]),  # round1 fresh post-revert recompute
        (0.6, []),      # round2 cur: nothing left → break
    ])
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: next(seq))
    # The patched text (containing _EDITOR_REPLACEMENT) recounts to 0 < baseline 1 —
    # so ONLY the weakness-identity check stands between this round and a false accept.
    recount = lambda t: 0 if _EDITOR_REPLACEMENT in t else 1
    events: list = []
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=_EditorPatchClient(),
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        quality_opportunities=["Explicit alternative not included: include a cost-benefit analysis"],
        opportunity_recount=recount,
    )
    # Reverted: the net-new weakness "w2" was never present before, so the round is
    # rejected despite the opportunity-count drop.
    assert _EDITOR_REPLACEMENT not in final
    assert final == before_art
    rejects = [e for e in events if getattr(e, "name", "") == "editor_round" and e.outcome == "reject"]
    accepts = [e for e in events if getattr(e, "name", "") == "editor_round" and e.outcome == "accept"]
    assert len(rejects) == 1 and not accepts


# --------------------------------------------------------------------------- #
# Editor opportunity-turn prompt: STRUCTURAL gaps (a diagram/table/code example  #
# the reducer can never produce) get genuine-attempt guidance; plain ones keep   #
# the soft "only if cheap" qualifier. Pure string construction — no LLM.         #
# --------------------------------------------------------------------------- #


def test_editor_opportunity_prompt_encourages_attempt_for_structural_gaps():
    from studio.runner import _editor_opportunity_prompt
    structural = [
        "Explicit alternative not included: add an architecture diagram",
        "Include a mermaid flowchart of the tool-selection loop",
        "Provide a comparison table of the two approaches",
        "Add a summary matrix of the frameworks",
        "Include a code example showing the retry wrapper",
        "Add a visual of the pipeline stages",
        "Include a graph of latency over time",
        # Real observed case (session s_196ef7b0cd4b, task_hash 39ee3efddbd9): the
        # task phrased its diagram alternative as "...or design architecture", and
        # `extract_requirements` embeds that branch text VERBATIM — no "diagram"
        # word anywhere. Without "architecture" in the keyword list, this exact
        # opportunity — from the task that started this whole thread — would
        # misclassify as plain and never reach the structural retry at all.
        "Explicit alternative not included: design architecture",
    ]
    for opp in structural:
        out = _editor_opportunity_prompt("write an agent loops report", [opp])
        assert "STRUCTURAL CONTENT" in out, opp
        assert "genuine attempt" in out, opp
        assert "read_artifact" in out, opp  # told to ground in existing content first
        assert opp in out
        # Structural bullet must NOT be under the soft "only if cheap" header.
        assert "OPTIONAL POLISH" not in out, opp


def test_editor_opportunity_prompt_keeps_soft_qualifier_for_plain_gaps():
    from studio.runner import _editor_opportunity_prompt
    plain = [
        "Explicit alternative not included: add a cost-benefit analysis section",
        "Cover the security implications of the approach",
        "Include a paragraph on rollback strategy",  # 'paragraph' must not trip \bgraph
    ]
    for opp in plain:
        out = _editor_opportunity_prompt("write an agent loops report", [opp])
        assert "OPTIONAL POLISH" in out, opp
        assert "ONLY if" in out, opp  # soft "add only if cheap" qualifier retained
        assert "STRUCTURAL CONTENT" not in out, opp
        assert opp in out


def test_editor_opportunity_prompt_partitions_mixed_opportunities():
    """Both headers appear when a batch mixes structural and plain gaps, each
    bullet routed under the header matching its own shape."""
    from studio.runner import _editor_opportunity_prompt
    struct = "Add an architecture diagram"
    plain = "Add a cost-benefit analysis"
    out = _editor_opportunity_prompt("t", [struct, plain])
    assert "STRUCTURAL CONTENT" in out and "OPTIONAL POLISH" in out
    assert out.index("STRUCTURAL CONTENT") < out.index(struct)
    assert out.index("OPTIONAL POLISH") < out.index(plain)


# --------------------------------------------------------------------------- #
# Bounded structural-opportunity retry (HANDOFF-requirement-compliance-diagram- #
# reliability.md's "best tradeoff" follow-up to entry 172): up to 3 candidate  #
# attempts, each from the SAME pre-attempt snapshot, first non-regressing      #
# candidate that strictly reduces the opportunity count wins.                  #
# --------------------------------------------------------------------------- #


class _StructuralRetryClient:
    """Patches a mermaid block starting from the Nth EXTERNAL ``editor_client.
    chat()`` call (1-indexed across the whole pass — fix/toc/selfeval turns each
    count too); a no-op before that. Mirrors ``_EditorPatchClient``'s
    read_artifact -> patch_artifact protocol but tracks external-call count via
    the fresh-turn marker (no ``doc_hash`` yet in the message list)."""

    def __init__(self, patch_on_call: int, find="References", replace=(
        "References\n\n```mermaid\ngraph TD; A-->B\n```"
    )) -> None:
        self.patch_on_call = patch_on_call
        self.find, self.replace = find, replace
        self.call_count = 0
        self.prompts: list[str] = []

    def chat(self, messages, tools=None):
        from agentkit.types import ChatResult
        doc_hash = ""
        for m in messages:
            content = m.get("content")
            if isinstance(content, str) and '"doc_hash"' in content:
                try:
                    doc_hash = json.loads(content).get("doc_hash", "")
                except Exception:
                    pass
        if not doc_hash:
            self.call_count += 1
            first = messages[0].get("content") if messages else ""
            if isinstance(first, str):
                self.prompts.append(first)
            return ChatResult(text="", total_tokens=1, tool_calls=[("read_artifact", {})])
        if self.call_count == self.patch_on_call:
            return ChatResult(text="", total_tokens=1, tool_calls=[
                ("patch_artifact", {"find": self.find, "replace": self.replace, "expected_hash": doc_hash})
            ])
        return ChatResult(text="done", total_tokens=1)


def test_editor_structural_retry_accepts_first_qualifying_attempt(tmp_path, monkeypatch) -> None:
    """Attempt 1 patches nothing useful (opportunity count unchanged) -> rejected
    and the snapshot is restored; attempt 2 adds the real mermaid block ->
    accepted. Score/weaknesses never move (a realistic case — adding a diagram
    doesn't touch the rubric's other issues) so ONLY the opportunity-count drop
    justifies acceptance, exactly like the existing single-shot soft-accept path,
    just with a second try available. Also pins the targeted retry-feedback text
    reaching attempt 2's prompt but not attempt 1's."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, ["w1"]))
    client = _StructuralRetryClient(patch_on_call=5)  # fix+toc+selfeval=1..3, retry attempt2=5
    recount = lambda t: 0 if "mermaid" in t else 1
    events: list = []
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=client,
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        # A non-diagram structural opportunity (a table) so this test exercises the
        # tool-augmented fallback LOOP, not the A2 deterministic diagram path (which
        # only intercepts diagram-shaped opportunities — see the A2 tests below).
        quality_opportunities=["Explicit alternative not included: add a comparison table"],
        opportunity_recount=recount,
        max_rounds=1,
    )
    assert "mermaid" in final
    assert "mermaid" in art_file.read_text(encoding="utf-8")
    assert weaknesses == ["w1"]
    retry_events = [e for e in events if getattr(e, "name", "") == "editor_structural_retry"]
    accepts = [e for e in retry_events if e.outcome == "accept"]
    rejects = [e for e in retry_events if e.outcome == "reject"]
    assert len(accepts) == 1 and len(rejects) == 1
    assert "attempt 2/3" in accepts[0].detail
    assert "attempt 1/3" in rejects[0].detail
    retry_prompts = [p for p in client.prompts if "STRUCTURAL CONTENT" in p]
    assert len(retry_prompts) == 2
    assert "grounded structural block" not in retry_prompts[0]
    assert "grounded structural block" in retry_prompts[1]  # feedback ingested after attempt 1


def test_editor_structural_retry_restores_when_no_attempt_qualifies(tmp_path, monkeypatch) -> None:
    """Every bounded attempt fails to reduce the opportunity count -> the artifact
    is restored to the untouched pre-retry snapshot, not left mid-attempt."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, ["w1"]))
    client = _StructuralRetryClient(patch_on_call=9999)  # never patches
    events: list = []
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=client,
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        # Non-diagram structural opportunity → tool-augmented fallback loop (not A2).
        quality_opportunities=["Explicit alternative not included: add a comparison table"],
        opportunity_recount=lambda t: 1,  # never drops
        max_rounds=1,
    )
    assert final == before_art
    assert art_file.read_text(encoding="utf-8") == before_art
    retry_events = [e for e in events if getattr(e, "name", "") == "editor_structural_retry"]
    assert len([e for e in retry_events if e.outcome == "reject"]) == 3  # all 3 bounded attempts
    assert not [e for e in retry_events if e.outcome == "accept"]


# --------------------------------------------------------------------------- #
# A2 deterministic diagram path (Bug A): for a DIAGRAM-shaped structural         #
# opportunity, the BARE base_client emits plain COMPONENT/EDGE lines and         #
# studio.diagram_render renders + grounds + inserts a mermaid block by direct    #
# file write (no model tool call). These drive the REAL _run_editor_pass so the  #
# section-split round-trip that preserves the block is exercised, not stubbed.   #
# --------------------------------------------------------------------------- #


class _A2Client:
    """Bare client: returns COMPONENT/EDGE lines ONLY for the A2 components prompt
    (identified by its '=== REPORT ===' + 'COMPONENT:' markers); every other turn
    (the fallback loop's fix/toc/selfeval/retry turns) is an inert no-op with no
    tool calls, so nothing but the A2 path can mutate the artifact."""

    def __init__(self, components_text: str) -> None:
        self.components_text = components_text
        self.saw_components_prompt = False

    def chat(self, messages, tools=None):
        from agentkit.types import ChatResult
        content = messages[0].get("content", "") if messages else ""
        if isinstance(content, str) and "=== REPORT ===" in content and "COMPONENT:" in content:
            self.saw_components_prompt = True
            return ChatResult(text=self.components_text, total_tokens=1)
        return ChatResult(text="", total_tokens=1)


#: Component names whose key terms all appear in _editor_artifact_text() (so the
#: grounding guard passes) — "Agent"/"Loops", "Executive"/"Summary", "Findings",
#: "References". Four grounded components + edges → a valid grounded diagram.
_A2_GROUNDED_LINES = (
    "COMPONENT: Agent Loops | the system under study\n"
    "COMPONENT: Executive Summary | the intro section\n"
    "COMPONENT: Key Findings | the results\n"
    "COMPONENT: References | the sources\n"
    "EDGE: Executive Summary -> Agent Loops | frames\n"
    "EDGE: Agent Loops -> Key Findings | produces\n"
)


def _run_a2(tmp_path, session, art_file, before_art, base_client, recount, events, embedder=None):
    from studio import runner as _runner_mod
    return _runner_mod._run_editor_pass(
        session=session,
        base_client=base_client,
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report with an architecture diagram",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        quality_opportunities=["Explicit alternative not included: add an architecture diagram"],
        opportunity_recount=recount,
        max_rounds=1,
        embedder=embedder,
    )


def test_a2_diagram_lands_and_is_accepted(tmp_path, monkeypatch) -> None:
    """The deterministic path inserts a real ```mermaid block (grounded in the
    report's own entities) via direct file write, it survives the section-split
    round-trip, and the accept gate keeps it because the structural-opportunity
    count strictly drops. This is the production-path analog of the live check."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, ["w1"]))
    recount = lambda t: 0 if "mermaid" in t else 1  # diagram present ⇒ opportunity resolved
    client = _A2Client(_A2_GROUNDED_LINES)
    events: list = []
    final, weaknesses = _run_a2(tmp_path, session, art_file, before_art, client, recount, events)
    assert client.saw_components_prompt  # the A2 components prompt actually fired
    assert "```mermaid" in final, "mermaid block did not land in the returned artifact"
    assert "```mermaid" in art_file.read_text(encoding="utf-8"), "block not persisted on disk"
    assert "flowchart TD" in final
    assert weaknesses == ["w1"]  # score/weaknesses untouched — only the opp count moved
    accepts = [
        e for e in events
        if getattr(e, "name", "") == "editor_structural_retry" and e.outcome == "accept"
    ]
    assert len(accepts) == 1 and "A2 deterministic diagram" in accepts[0].detail


def test_a2_grounding_guard_adds_nothing_for_ungrounded_components(tmp_path, monkeypatch) -> None:
    """When the model names only invented components, the grounding guard yields no
    diagram → A2 adds nothing (no file write, no accept) and the fallback loop (inert
    client) leaves the artifact byte-for-byte unchanged."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, ["w1"]))
    ungrounded = "\n".join(f"COMPONENT: Zorptron{i} | invented" for i in range(6))
    client = _A2Client(ungrounded)
    # Literal-token grounding: no >=4-char token of any invented "Zorptron" label
    # appears in the report prose → all dropped → below _MIN_NODES → no diagram.
    events: list = []
    final, _ = _run_a2(
        tmp_path, session, art_file, before_art, client, lambda t: 1, events
    )
    assert "mermaid" not in final
    assert final == before_art
    assert art_file.read_text(encoding="utf-8") == before_art
    assert not [
        e for e in events
        if getattr(e, "name", "") == "editor_structural_retry" and e.outcome == "accept"
    ]


def test_a2_accept_gate_restores_on_score_regression(tmp_path, monkeypatch) -> None:
    """The gate is NOT weakened for A2: a grounded, opportunity-reducing diagram is
    still REJECTED and the snapshot restored if it regresses the rubric score —
    proving A2 reuses the same non-regression bounds as the tool-augmented loop."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    # Candidate (contains mermaid) scores LOWER than the baseline → regression.
    def _scored(session_, text, *a, **k):
        return (0.3, ["w1"]) if "mermaid" in text else (0.5, ["w1"])
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", _scored)
    recount = lambda t: 0 if "mermaid" in t else 1  # opp WOULD drop, but score blocks accept
    client = _A2Client(_A2_GROUNDED_LINES)
    events: list = []
    final, _ = _run_a2(tmp_path, session, art_file, before_art, client, recount, events)
    assert "mermaid" not in final, "regressing diagram must be reverted, not kept"
    assert final == before_art
    assert art_file.read_text(encoding="utf-8") == before_art
    retry = [e for e in events if getattr(e, "name", "") == "editor_structural_retry"]
    assert any(e.outcome == "reject" and "A2 deterministic diagram" in e.detail for e in retry)
    assert not [e for e in retry if e.outcome == "accept"]


# --------------------------------------------------------------------------- #
# Per-section presentation pass (Follow-up #2): after the round loop, runs      #
# independent of cur_issues / requirement opportunities (codex M2) — a section  #
# that WARRANTS a diagram gets one even on an otherwise-clean report.           #
# --------------------------------------------------------------------------- #

_PS_ARTIFACT = (
    "# Agent Loops Report\n\n"
    "## Executive Summary\n\nA short narrative overview of the study and its aims.\n\n"
    "## Key Findings\n\nThe Planner builds a plan. The Executor runs tools. The Memory "
    "store persists state. The Scorer grades the artifact.\n\n"
    "## References\n\n- https://x.test/e\n"
)
_PS_COMPONENTS = (
    "COMPONENT: Planner | builds plan\nCOMPONENT: Executor | runs tools\n"
    "COMPONENT: Memory | persists state\nCOMPONENT: Scorer | grades output\n"
    "EDGE: Planner -> Executor\nEDGE: Executor -> Scorer\n"
)


class _PSClient:
    """Detector says DIAGRAM only for ``diagram_heading``; component extraction (prompt
    has '=== REPORT ===') returns fixed grounded lines; every other turn is inert."""

    def __init__(self, diagram_heading: str, components: str = _PS_COMPONENTS) -> None:
        self.diagram_heading = diagram_heading
        self.components = components
        self.saw_extraction = False

    def chat(self, messages, tools=None):
        import re

        from agentkit.types import ChatResult
        content = messages[0].get("content", "") if messages else ""
        if "=== REPORT ===" in content and "COMPONENT:" in content:
            self.saw_extraction = True
            return ChatResult(text=self.components, total_tokens=1)
        if "DIAGRAM or PROSE" in content:
            m = re.search(r"SECTION HEADING: (.+)", content)
            head = m.group(1).strip() if m else ""
            return ChatResult(
                text="DIAGRAM" if self.diagram_heading in head else "PROSE", total_tokens=1
            )
        return ChatResult(text="", total_tokens=1)


def _run_ps(tmp_path, session, art_file, before_art, client, events):
    from studio import runner as _runner_mod
    return _runner_mod._run_editor_pass(
        session=session,
        base_client=client,
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        max_rounds=1,
    )


def test_section_presentation_adds_diagram_to_clean_report(tmp_path, monkeypatch) -> None:
    """A report with NO weaknesses (the round loop breaks immediately on empty
    cur_issues) still gets a diagram in the warranting section via the post-loop
    presentation pass — the exact M2 case the old opportunity-gated path skipped."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _PS_ARTIFACT)
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, []))
    client = _PSClient("Key Findings")
    events: list = []
    final, _ = _run_ps(tmp_path, session, art_file, before_art, client, events)
    assert client.saw_extraction  # the components prompt actually fired
    assert "```mermaid" in final and "flowchart TD" in final
    assert "*Figure:" in final  # caption (C8)
    assert "```mermaid" in art_file.read_text(encoding="utf-8")  # persisted on disk
    accepts = [
        e for e in events
        if getattr(e, "name", "") == "section_presentation" and e.outcome == "accept"
    ]
    assert len(accepts) == 1 and "Key Findings" in accepts[0].detail
    assert "remaining=0" in accepts[0].detail  # M1 telemetry: 1 warranted, 1 satisfied


def test_section_presentation_reverts_on_score_regression(tmp_path, monkeypatch) -> None:
    """The presentation pass reuses the non-regression gate: a diagram that drops the
    score is reverted, artifact byte-identical, a reject event emitted."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _PS_ARTIFACT)
    before_art = art_file.read_text(encoding="utf-8")

    def _scored(session_, text, *a, **k):
        return (0.3, []) if "mermaid" in text else (0.5, [])
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", _scored)
    client = _PSClient("Key Findings")
    events: list = []
    final, _ = _run_ps(tmp_path, session, art_file, before_art, client, events)
    assert "mermaid" not in final and final == before_art
    assert "mermaid" not in art_file.read_text(encoding="utf-8")
    ps = [e for e in events if getattr(e, "name", "") == "section_presentation"]
    assert any(e.outcome == "reject" for e in ps)
    assert not [e for e in ps if e.outcome == "accept"]


def test_section_presentation_noop_when_nothing_warrants(tmp_path, monkeypatch) -> None:
    """Detector says PROSE for every section → no diagram, no events, artifact untouched."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _PS_ARTIFACT)
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, []))
    client = _PSClient("__none__")  # no heading matches → all PROSE
    events: list = []
    final, _ = _run_ps(tmp_path, session, art_file, before_art, client, events)
    assert "mermaid" not in final and final == before_art
    assert not [e for e in events if getattr(e, "name", "") == "section_presentation"]


def test_section_presentation_rolls_back_disk_on_midsync_failure(tmp_path, monkeypatch) -> None:
    """codex [P2]: if section-sync raises AFTER the candidate diagram is written to disk,
    the best-effort except must roll artifact.md back so on-disk state matches the returned
    (old) scored_text — not leave a half-applied diagram for the next epoch to read."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _PS_ARTIFACT)
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, []))

    def _boom(*a, **k):  # the write-through step fails mid-sync (IO / workspace error)
        raise RuntimeError("section sync blew up")
    monkeypatch.setattr(_runner_mod, "_write_artifact_through_sections", _boom)
    client = _PSClient("Key Findings")
    events: list = []
    final, _ = _run_ps(tmp_path, session, art_file, before_art, client, events)
    # In-memory result is the old text AND disk was rolled back to match it byte-for-byte.
    assert final == before_art
    assert art_file.read_text(encoding="utf-8") == before_art
    assert "mermaid" not in art_file.read_text(encoding="utf-8")
    # A swallowed failure emits neither accept nor reject.
    assert not [e for e in events if getattr(e, "name", "") == "section_presentation"]


# --------------------------------------------------------------------------- #
# Content presentation pass (Phase 1): table / list / format-fix. Sibling of    #
# the diagram pass, same gate + rollback contract. Runs after the round loop on  #
# base_client (Phase 1: judge == generator).                                     #
# --------------------------------------------------------------------------- #

_CP_TABLE = (
    "| Attribute | Redis | Postgres |\n| --- | --- | --- |\n"
    "| Latency | low | high |\n| Durability | weak | strong |\n"
)


class _CPClient:
    """Fake client for the content pass. The 5-way adjudicator prompt (has 'BULLETED_LIST'
    + 'SECTION HEADING:') returns TABLE only for ``table_heading``; the table-generation
    prompt ('comparison table' + '=== SECTION ===') returns fixed grounded rows. Every
    other turn (incl. the diagram detector) is inert, so the diagram pass no-ops."""

    def __init__(self, table_heading: str, table_md: str = _CP_TABLE) -> None:
        self.table_heading = table_heading
        self.table_md = table_md
        self.saw_table_gen = False

    def chat(self, messages, tools=None):
        import re

        from agentkit.types import ChatResult
        content = messages[0].get("content", "") if messages else ""
        if "comparison table" in content and "=== SECTION ===" in content:
            self.saw_table_gen = True
            return ChatResult(text=self.table_md, total_tokens=1)
        if "BULLETED_LIST" in content and "SECTION HEADING:" in content:
            m = re.search(r"SECTION HEADING: (.+)", content)
            head = m.group(1).strip() if m else ""
            return ChatResult(
                text="TABLE" if self.table_heading in head else "PARAGRAPH", total_tokens=1
            )
        return ChatResult(text="", total_tokens=1)


# A comparison section ("Compared to" + Redis/Postgres) deterministically recommends TABLE;
# the grounded cells (latency/durability/volatile/strong) all appear in the body.
_CP_COMPARE_ARTIFACT = (
    "# Datastore Report\n\n"
    "## Executive Summary\n\nA short narrative overview of the datastore study.\n\n"
    "## Key Findings\n\nCompared to Redis, Postgres differs across latency and durability: "
    "Redis offers low latency but weak durability, whereas Postgres has high latency but "
    "strong durability.\n\n"
    "## References\n\n- https://x.test/e\n"
)


def test_content_presentation_adds_table_to_comparison_section(tmp_path, monkeypatch) -> None:
    """A comparison section (prose that should be a table) gets a grounded markdown table
    via the post-loop content pass, kept on score/weakness non-regression + realized form."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _CP_COMPARE_ARTIFACT)
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, []))
    client = _CPClient("Key Findings")
    events: list = []
    final, _ = _run_ps(tmp_path, session, art_file, before_art, client, events)
    assert client.saw_table_gen  # the table prompt actually fired
    assert "| Redis | Postgres |" in final and "*Table:" in final  # table + caption landed
    assert "| Redis | Postgres |" in art_file.read_text(encoding="utf-8")  # persisted
    accepts = [
        e for e in events
        if getattr(e, "name", "") == "content_presentation" and e.outcome == "accept"
    ]
    assert len(accepts) == 1 and "table added to" in accepts[0].detail


def test_content_presentation_reverts_on_score_regression(tmp_path, monkeypatch) -> None:
    """A table that drops the score is reverted; artifact byte-identical; reject emitted."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _CP_COMPARE_ARTIFACT)
    before_art = art_file.read_text(encoding="utf-8")

    def _scored(session_, text, *a, **k):
        return (0.3, []) if "| Redis | Postgres |" in text else (0.5, [])
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", _scored)
    client = _CPClient("Key Findings")
    events: list = []
    final, _ = _run_ps(tmp_path, session, art_file, before_art, client, events)
    assert "| Redis | Postgres |" not in final and final == before_art
    assert art_file.read_text(encoding="utf-8") == before_art
    cp = [e for e in events if getattr(e, "name", "") == "content_presentation"]
    assert any(e.outcome == "reject" for e in cp)
    assert not [e for e in cp if e.outcome == "accept"]


def test_content_presentation_repairs_format_defect(tmp_path, monkeypatch) -> None:
    """An unclosed code fence (no form change) is repaired deterministically and kept via
    the same gate — a format-only accept (heading None)."""
    from studio import runner as _runner_mod
    session = _editor_session()
    broken = (
        "# R\n\n## Executive Summary\n\nOverview.\n\n"
        "## Key Findings\n\n```python\n\nprose that the unclosed fence swallows as code\n\n"
        "## References\n\n- https://x.test/e\n"
    )
    root, art_file = _build_editor_ws(tmp_path, session, broken)
    before_art = art_file.read_text(encoding="utf-8")
    assert before_art.count("```") == 1  # genuinely unclosed
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, []))
    client = _CPClient("__none__")  # adjudicator says PARAGRAPH everywhere → no form change
    events: list = []
    final, _ = _run_ps(tmp_path, session, art_file, before_art, client, events)
    assert final.count("```") == 2  # fence now closed
    assert art_file.read_text(encoding="utf-8").count("```") == 2  # persisted
    accepts = [
        e for e in events
        if getattr(e, "name", "") == "content_presentation" and e.outcome == "accept"
    ]
    assert len(accepts) == 1 and "format defects repaired" in accepts[0].detail


def test_content_presentation_rolls_back_disk_on_midsync_failure(tmp_path, monkeypatch) -> None:
    """codex-[P2] parity for the content pass: a mid-sync failure after the candidate hit
    disk rolls artifact.md back so disk matches the returned (old) scored_text."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _CP_COMPARE_ARTIFACT)
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, []))
    _orig = _runner_mod._write_artifact_through_sections
    calls = {"n": 0}

    def _boom_on_content(session_, ws_root, text, req, *a, **k):
        # The diagram pass runs first (no-op here → never syncs); fail the content sync.
        if "| Redis | Postgres |" in text:
            raise RuntimeError("content sync blew up")
        return _orig(session_, ws_root, text, req, *a, **k)
    monkeypatch.setattr(_runner_mod, "_write_artifact_through_sections", _boom_on_content)
    client = _CPClient("Key Findings")
    events: list = []
    final, _ = _run_ps(tmp_path, session, art_file, before_art, client, events)
    assert final == before_art
    assert art_file.read_text(encoding="utf-8") == before_art
    assert "| Redis | Postgres |" not in art_file.read_text(encoding="utf-8")
    assert not [e for e in events if getattr(e, "name", "") == "content_presentation"]


def test_editor_structural_retry_skips_when_baseline_recount_unavailable_or_zero(
    tmp_path, monkeypatch
) -> None:
    """A None (fail-open) or 0 (nothing outstanding) baseline recount must skip the
    retry entirely — no LLM turn, no GateEvent, text untouched."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, ["w1"]))
    for recount_fn in (lambda t: None, lambda t: 0):
        client = _StructuralRetryClient(patch_on_call=9999)
        events: list = []
        final, _ = _runner_mod._run_editor_pass(
            session=session,
            base_client=client,
            scored_text=before_art,
            verified_urls=["https://x.test/e"],
            effective_ws_root=tmp_path,
            art_file=art_file,
            original_requirement="write an agent loops report",
            emit=events.append,
            workspace_root=tmp_path,
            step_id_getter=lambda: "editor",
            quality_opportunities=["Explicit alternative not included: add an architecture diagram"],
            opportunity_recount=recount_fn,
            max_rounds=1,
        )
        assert final == before_art
        assert not [e for e in events if getattr(e, "name", "") == "editor_structural_retry"]


def test_editor_structural_retry_survives_raising_recount_after_mutation(
    tmp_path, monkeypatch
) -> None:
    """Codex review finding: a custom ``opportunity_recount`` that RAISES (not the
    production ``_make_opportunity_recount``, which already fail-opens
    internally) must never skip the post-candidate restore. Attempt 1's patch
    lands, then the recount raises on that candidate — the retry must swallow
    it as UNKNOWN (treated like a None recount: reject + restore), NOT propagate
    the exception and leave the mutated artifact.md behind."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, ["w1"]))
    client = _StructuralRetryClient(patch_on_call=4)  # fix+toc+selfeval=1..3, retry attempt1=4
    _recount_calls = {"n": 0}

    def _raising_recount(text: str) -> int:
        # First call is the BASE recount (must succeed so the retry proceeds to
        # attempt 1); every call after that (the post-candidate recount, right
        # after attempt 1's patch lands) raises.
        _recount_calls["n"] += 1
        if _recount_calls["n"] == 1:
            return 1
        raise RuntimeError("compliance backend unreachable")

    events: list = []
    final, weaknesses = _runner_mod._run_editor_pass(
        session=session,
        base_client=client,
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=events.append,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        # Non-diagram structural opportunity → tool-augmented fallback loop (not A2).
        quality_opportunities=["Explicit alternative not included: add a comparison table"],
        opportunity_recount=_raising_recount,
        max_rounds=1,
    )
    # No exception propagated (the call above completing at all proves it), and the
    # mutated candidate never leaked into the returned/on-disk state.
    assert final == before_art
    assert art_file.read_text(encoding="utf-8") == before_art
    assert weaknesses == ["w1"]


def test_editor_structural_retry_not_invoked_for_plain_only_opportunities(
    tmp_path, monkeypatch
) -> None:
    """Regression guard for the split itself: an opportunity list with NO
    structural-shaped item must never reach ``_editor_structural_retry`` — this
    is what keeps the existing plain-opportunity soft-accept tests (which pass
    "include a cost-benefit analysis" — no structural keyword) behaving exactly
    as before the retry was added."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, ["w1"]))
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return a[4] if len(a) > 4 else k.get("scored_text"), []  # unused: gate should skip entirely

    monkeypatch.setattr(_runner_mod, "_editor_structural_retry", _spy)
    client = _StructuralRetryClient(patch_on_call=9999)
    _runner_mod._run_editor_pass(
        session=session,
        base_client=client,
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=lambda _e: None,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        quality_opportunities=["Explicit alternative not included: include a cost-benefit analysis"],
        opportunity_recount=lambda t: 1,
        max_rounds=1,
    )
    assert called["n"] == 0


# --------------------------------------------------------------------------- #
# LLM-fallback structural classification (`_classify_structural_opportunity` / #
# `_is_structural_opportunity`) — a regex keyword list is whack-a-mole         #
# ("blueprint", "wireframe", "topology map" all miss it no matter how many     #
# keywords get added); the fallback asks the SAME base_client the genuinely    #
# open-ended question a fixed vocabulary structurally cannot answer.           #
# --------------------------------------------------------------------------- #

class _ScriptedClassifyClient:
    """Returns a fixed one-word verdict for every ``chat`` call; counts calls."""

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.calls = 0

    def chat(self, messages, tools=None):
        from agentkit.types import ChatResult
        self.calls += 1
        return ChatResult(text=self.verdict, total_tokens=1)


def test_classify_structural_opportunity_prompt_is_hardened(monkeypatch):
    """Codex design-review hardening: the opportunity text is delimited and
    explicitly marked as untrusted data (not instructions to follow), and a
    handful of few-shot examples anchor the verdict for a weak local model —
    all real content the prompt must actually contain, not just described in
    a comment."""
    from studio.runner import _classify_structural_opportunity
    captured: dict = {}

    class _CaptureClient:
        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            captured["prompt"] = messages[0]["content"]
            return ChatResult(text="PLAIN", total_tokens=1)

    _classify_structural_opportunity(_CaptureClient(), "add a blueprint of the system")
    prompt = captured["prompt"]
    assert "untrusted data" in prompt
    assert '"""add a blueprint of the system"""' in prompt
    assert "blueprint" in prompt and "STRUCTURAL" in prompt  # few-shot example present
    assert "PLAIN" in prompt


def test_classify_structural_opportunity_true_on_structural_verdict():
    from studio.runner import _classify_structural_opportunity
    client = _ScriptedClassifyClient("STRUCTURAL")
    assert _classify_structural_opportunity(client, "add a blueprint of the pipeline") is True


def test_classify_structural_opportunity_false_on_plain_verdict():
    from studio.runner import _classify_structural_opportunity
    client = _ScriptedClassifyClient("PLAIN")
    assert _classify_structural_opportunity(client, "cover the security tradeoffs") is False


def test_classify_structural_opportunity_fails_open_on_client_error():
    from studio.runner import _classify_structural_opportunity

    class _RaisingClient:
        def chat(self, messages, tools=None):
            raise RuntimeError("backend unreachable")

    assert _classify_structural_opportunity(_RaisingClient(), "add a wireframe") is False


def test_classify_structural_opportunity_false_on_no_client_or_empty_text():
    from studio.runner import _classify_structural_opportunity
    assert _classify_structural_opportunity(None, "add a blueprint") is False
    assert _classify_structural_opportunity(_ScriptedClassifyClient("STRUCTURAL"), "") is False


def test_is_structural_opportunity_regex_fast_path_skips_llm_call():
    """Cost consciousness: a keyword match must NOT spend an LLM call at all —
    the fallback classifier is never even constructed a client call for it."""
    from studio.runner import _is_structural_opportunity
    client = _ScriptedClassifyClient("PLAIN")  # would say PLAIN if asked — must never be asked
    assert _is_structural_opportunity(client, "add an architecture diagram") is True
    assert client.calls == 0


def test_is_structural_opportunity_llm_fallback_catches_non_keyword_phrasing():
    """The actual generalization this fallback exists for: phrasings the fixed
    keyword vocabulary was never going to enumerate ("blueprint", "topology
    map", "wireframe") still classify correctly via genuine LLM judgment."""
    from studio.runner import _is_structural_opportunity
    for phrase in (
        "add a blueprint of the retry pipeline",
        "include a topology map of the services",
        "provide a wireframe of the dashboard",
    ):
        client = _ScriptedClassifyClient("STRUCTURAL")
        assert _is_structural_opportunity(client, phrase) is True, phrase
        assert client.calls == 1


def test_is_structural_opportunity_llm_fallback_stays_plain_when_llm_says_so():
    from studio.runner import _is_structural_opportunity
    client = _ScriptedClassifyClient("PLAIN")
    assert _is_structural_opportunity(client, "expand on the cost tradeoffs") is False
    assert client.calls == 1


def test_editor_structural_retry_fires_for_non_keyword_opportunity_via_llm_classification(
    tmp_path, monkeypatch
) -> None:
    """End-to-end: an opportunity with NO regex keyword ("blueprint") still
    reaches `_editor_structural_retry` when the classification call says
    STRUCTURAL — proving the fallback actually wires into the real split, not
    just the two helper functions in isolation."""
    from studio import runner as _runner_mod
    session = _editor_session()
    root, art_file = _build_editor_ws(tmp_path, session, _editor_artifact_text())
    before_art = art_file.read_text(encoding="utf-8")
    monkeypatch.setattr(_runner_mod, "_editor_scored_issues", lambda *a, **k: (0.5, ["w1"]))
    captured: dict = {}

    def _spy(*, structural_opportunities, scored_text, **_k):
        captured["structural_opportunities"] = structural_opportunities
        return scored_text, ["w1"]

    monkeypatch.setattr(_runner_mod, "_editor_structural_retry", _spy)

    class _ClassifyThenNoopClient:
        """First call (the classification probe) says STRUCTURAL; every call
        after that (fix/toc/selfeval turns) is a no-op read_artifact loop."""
        def __init__(self):
            self.n = 0

        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            self.n += 1
            if self.n == 1:
                return ChatResult(text="STRUCTURAL", total_tokens=1)
            return ChatResult(text="done", total_tokens=1)

    _runner_mod._run_editor_pass(
        session=session,
        base_client=_ClassifyThenNoopClient(),
        scored_text=before_art,
        verified_urls=["https://x.test/e"],
        effective_ws_root=tmp_path,
        art_file=art_file,
        original_requirement="write an agent loops report",
        emit=lambda _e: None,
        workspace_root=tmp_path,
        step_id_getter=lambda: "editor",
        quality_opportunities=["Explicit alternative not included: add a blueprint of the pipeline"],
        opportunity_recount=lambda t: 1,
        max_rounds=1,
    )
    assert captured["structural_opportunities"] == [
        "Explicit alternative not included: add a blueprint of the pipeline"
    ]


# --------------------------------------------------------------------------- #
# Regression: the keep/discard gate must compare against the ACTUAL epoch seed #
# (the "prior best to beat"), never against this epoch's own fresh output.     #
# --------------------------------------------------------------------------- #


def test_epoch_gate_baseline_is_the_seed_not_this_epochs_own_output(
    tmp_path, monkeypatch
) -> None:
    """Fix 1: `_seed_text` (the Phase-1 keep/discard baseline) reaching
    ``epoch_gate.accept_epoch`` must be the ACTUAL carried-forward seed that
    started this epoch — not the current on-disk artifact.md, which by the last
    phase already contains THIS epoch's freshly-generated findings. The old code
    rebound `_seed_text` to that on-disk read mid-epoch, so the gate compared the
    epoch's output against itself and could never reject a regression.

    We seed a distinctive prior, drive a full run whose fresh output injects
    content the seed never had, and capture the `prior_text` the gate actually
    receives.
    """
    import studio.epoch_gate as _gate_mod
    from agentkit.types import ChatResult
    from studio.rubric import DEFAULT_TEMPLATE, default_scoring_matrix
    from studio.task_runs import TaskRun, TaskRunStore, base_identity, task_hash
    from studio.tools import _fetch_cache

    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path))
    requirement = (
        "1. Gather evidence about report quality.\n"
        f"2. Write a research report about report quality. Run id: {tmp_path.name}."
    )
    thash = task_hash(base_identity(requirement))
    # Distinctive seed: SEED_SENTINEL only ever appears in the seed, never in the
    # fresh epoch output below.
    seed_text = (
        "# Seed Report\n\n## Executive Summary\n"
        "SEED_SENTINEL_PRIOR_BASELINE — the prior report body."
    )
    TaskRunStore().record(TaskRun(
        task_hash=thash,
        session_id="prior",
        version=1,
        score=0.4,
        weaknesses=["[document] Missing limitations."],
        artifact_path="",
        requirement=requirement,
        result_text=seed_text,
        config={"auto_improve": True, "max_epochs": 1},
    ))
    # Ground the fresh finding so it survives the grounding guard and is folded
    # into artifact.md DURING the run (before the last-phase seed read). Without
    # this the finding is dropped as unfetched and the on-disk artifact never
    # differs from the seed — so the clobber bug would not manifest.
    _fetch_cache["https://example.com/fresh|"] = (
        "FRESH_EPOCH_ONLY new sentence found on the page", 48
    )

    class _FreshFindingClient:
        # Emits a finding whose content (FRESH_EPOCH_ONLY) the seed never contained,
        # so it is folded into the on-disk artifact this epoch — the value the buggy
        # code would have leaked to the gate.
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(
                text=(
                    "RESEARCH_FINDING:\n"
                    "ARTICLE_TITLE: Fresh\n"
                    "URL: https://example.com/fresh\n"
                    "PATCH_TARGET: ## Executive Summary\n"
                    "QUOTE: FRESH_EPOCH_ONLY new sentence\n"
                    "WHY: Adds detail this epoch.\n"
                ),
                total_tokens=5,
            )

    captured: dict[str, str] = {}

    def _spy_accept_epoch(new_text, prior_text, prefer):
        captured["prior"] = prior_text
        captured["new"] = new_text
        return True  # accept — behavior of the gate is irrelevant to this assertion

    monkeypatch.setattr(_gate_mod, "accept_epoch", _spy_accept_epoch)

    session = _make_session()
    session.tools_enabled = False
    session.hill_climb_config = {"auto_improve": True, "max_epochs": 1}
    session.rubric_config = {
        "template": DEFAULT_TEMPLATE,
        "scoring_template": DEFAULT_TEMPLATE,
        "scoring_matrix": default_scoring_matrix("general", DEFAULT_TEMPLATE),
    }
    events: list[StudioEvent] = []
    runner = Runner(
        session,
        events.append,
        client_factory=lambda _on_usage: _FreshFindingClient(),
        embedder=None,
        workspace_root=tmp_path,
    )
    runner.run(requirement)

    assert "prior" in captured, "keep/discard gate never ran on the seeded run"
    # The baseline the gate compared against is the SEED, not this epoch's output.
    assert "SEED_SENTINEL_PRIOR_BASELINE" in captured["prior"]
    assert "FRESH_EPOCH_ONLY" not in captured["prior"]


# --------------------------------------------------------------------------- #
# Regression: post-loop patch-apply uses the section-granular accept_rewrite   #
# guard, not a whole-doc length floor — a shorter-but-better rewrite is kept.  #
# --------------------------------------------------------------------------- #


def test_postloop_guard_accepts_shorter_but_improved_rewrite() -> None:
    """Fix 2: the post-loop patch-apply path swapped its crude
    ``len(new) >= len(seed)`` floor for ``accept_rewrite`` (the same guard the
    per-phase writeback uses). This proves the behavioral change: a legitimately
    shorter rewrite (dedup / synthesis replacing verbose quote-dumping) that keeps
    every content-bearing section is now ACCEPTED, whereas the old length floor
    would have REJECTED it purely for being shorter.
    """
    from agentkit.artifacts.sections import accept_rewrite

    old = (
        "# Report\n\n"
        "## Findings\n"
        "Redis is fast. Redis is fast. Redis is fast (verbose duplicated quote-dump).\n\n"
        "## Recommendation\n"
        "Use Redis for the cache tier because of its latency profile.\n"
    )
    # Dedup + synthesis: shorter, but no section is gutted — both headings keep content.
    new = (
        "# Report\n\n"
        "## Findings\n"
        "Redis is fast.\n\n"
        "## Recommendation\n"
        "Use Redis for the cache tier.\n"
    )

    assert len(new) < len(old)
    # Old crude guard would have rejected the shorter doc:
    assert not (len(new) >= len(old))
    # New guard keeps it — no content-bearing section was deleted or gutted:
    assert accept_rewrite(old, new) is True

    # And it still blocks a real regression (a section gutted to its bare heading):
    gutted = "# Report\n\n## Findings\nRedis is fast.\n\n## Recommendation\n"
    assert accept_rewrite(old, gutted) is False
