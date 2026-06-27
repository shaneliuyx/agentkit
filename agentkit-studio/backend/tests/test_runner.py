"""Milestone-1 smoke: the runner drives end-to-end on a FAKE client, offline.

Asserts the SPEC §4 ordering guarantee and the token-honesty behavior. No API
key, no running services.
"""

from __future__ import annotations

from typing import Callable

from agentkit.types import LLMClient
from studio.events import StudioEvent
from studio.runner import Runner
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
    saved = Path(done.result_path)
    assert saved.is_file()
    assert saved.read_text(encoding="utf-8") == done.result


def test_event_order(fake_client_factory: Callable[..., LLMClient]) -> None:
    """session → plan → topology → graph → (per phase ...) → budget? → verify → done."""
    events = _run(fake_client_factory)
    types = [e.EVENT_TYPE for e in events]

    # Prefix is exact.
    assert types[:4] == ["session", "plan", "topology", "graph"], types

    # Terminal: done is last. Ordering: verify → loopdoctor → hill_climb → done.
    assert types[-1] == "done", types
    assert types[-2] == "hill_climb", types
    # The Loop Doctor audit is emitted exactly once, after verify, before done.
    assert types.count("loopdoctor") == 1
    assert types.index("verify") < types.index("loopdoctor") < types.index("hill_climb") < types.index("done")

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


def test_section_reducer_merges_with_artifact_and_weaknesses() -> None:
    """_make_section_reducer (DESIGN §4.5) builds a run_plan reducer hook that
    feeds the current artifact + section weaknesses + worker drafts to the LLM
    and returns its merged output + token count."""
    from agentkit.types import ChatResult
    from studio.runner import _make_section_reducer

    captured: dict = {}

    class _C:
        def chat(self, messages, tools=None) -> ChatResult:
            captured["prompt"] = messages[-1]["content"]
            return ChatResult(text="  MERGED ARTIFACT  ", total_tokens=12)

    reduce = _make_section_reducer(
        _C(), "## Intro\nseed text", ["## Intro: missing citation"]
    )
    text, tok = reduce(["worker found source X", "worker found source Y"])

    assert text == "MERGED ARTIFACT"        # stripped
    assert tok == 12
    p = captured["prompt"]
    assert "## Intro" in p and "missing citation" in p   # artifact + weakness checklist
    assert "worker found source X" in p                  # worker drafts included
    assert "PRESERVE every existing section" in p        # additive contract present


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


def test_hill_climb_forces_star_topology(fake_client_factory, tmp_path) -> None:
    """auto_improve on → every phase forced to STAR (DESIGN §11.4), overriding
    auto-derived topology. 'compare ...' would normally classify to MESH; under
    hill-climb it must become STAR so the section-aware reducer runs."""
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
    assert tops == {"star"}, tops


def test_no_hill_climb_keeps_auto_topology(fake_client_factory) -> None:
    """Without auto_improve, topology stays auto-derived (regression guard for
    the force-STAR override — it must not fire when hill-climb is off)."""
    events = _run(fake_client_factory)  # no hill_climb_config
    topo = [e for e in events if e.EVENT_TYPE == "topology"][0]
    tops = {s["topology"] for s in topo.steps}
    # "compare redis and postgres" → MESH; not forced to STAR.
    assert "mesh" in tops, tops


def test_tool_loop_emits_tool_events(fake_client) -> None:
    """With tools enabled + a mocked search_fn, a tool-calling client fires
    tool_call/tool_result during a phase (web_search runs, no network)."""
    from agentkit.types import ChatResult

    # A client that requests web_search once, then answers.
    class _ToolClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools=None) -> ChatResult:
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
