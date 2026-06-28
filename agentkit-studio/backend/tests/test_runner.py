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
    assert "## Intro" in p and "missing citation" in p     # artifact + weakness checklist
    assert "worker found source X" in p                    # worker drafts included


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
        assert "real.example" in text                # finding appended cleanly
        assert "## Intro\nbody" in text              # seed intact
    finally:
        tools._fetch_cache.clear()


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
    bounded by the limit. No-op when the cache is empty (nothing to ground → offline)."""
    from studio import tools
    from studio.runner import _prefetch_cited
    tools._fetch_cache.clear()
    drafts = ["RESEARCH_FINDING:\nURL: https://x.example\nURL: https://y.example\n"]
    assert _prefetch_cited(drafts) == 0                    # empty cache → no-op (offline)
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
    the planner framing was the score ceiling (§11.10)."""
    from studio.runner import _build_executor_prompt
    p = _build_executor_prompt("find popular articles", "# Doc\n## Sources\nx",
                               "- [## Sources] missing url")
    assert "RESEARCH EXECUTOR" in p
    assert "TASK_LIST" in p and "ASSIGNED" in p          # explicitly FORBIDDEN
    assert "RESEARCH_FINDING" in p and "URL:" in p       # the execute output format
    assert "find popular articles" in p and "missing url" in p
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
