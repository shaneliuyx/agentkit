"""Tests for agentkit.topology.dynamic — Phase 8 per-step topology.

All offline (no network, no LLM): the step→topology classifier is pure, and
``run_plan`` dispatch is exercised with a deterministic fake client that counts
its calls. Mirrors the conventions in test_topology.py / test_planner.py.
"""

from __future__ import annotations

import pytest

from agentkit.orchestrator.fanout import BudgetExceeded, FanoutBudget
from agentkit.planner.core import Plan, PlanStep, plan as make_plan
from agentkit.topology import (
    MESH,
    PIPELINE,
    SINGLE,
    STAR,
    DynamicPlanResult,
    StepRun,
    assign_topologies,
    classify_step_topology,
    run_plan,
)
from agentkit.types import ChatResult, Message


# ---------------------------------------------------------------------------
# Fakes — deterministic LLM client, no network.
# ---------------------------------------------------------------------------

class FakeClient:
    """Counts calls and returns a fixed-token reply (so budgets are testable)."""

    def __init__(self, tokens: int = 5) -> None:
        self.n_calls = 0
        self.tokens = tokens
        self.prompts: list[str] = []

    def chat(self, messages: list[Message], tools=None) -> ChatResult:
        self.n_calls += 1
        self.prompts.append(messages[-1]["content"])
        return ChatResult(text=f"reply#{self.n_calls}", total_tokens=self.tokens)


class SpecClient:
    """Returns a canned JSON TaskSpec so the llm=True auto path is deterministic."""

    def __init__(self, json_text: str) -> None:
        self.json_text = json_text
        self.n_calls = 0

    def chat(self, messages: list[Message], tools=None) -> ChatResult:
        self.n_calls += 1
        return ChatResult(text=self.json_text, total_tokens=3)


# ---------------------------------------------------------------------------
# 1. classify_step_topology — one assertion per keyword class.
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("description,expected", [
    ("Compare vector RAG and GraphRAG", MESH),
    ("contrast the two approaches", MESH),
    ("debate which database is better", MESH),
    ("redis vs postgres", MESH),
    ("evaluate the tradeoffs between A and B", MESH),
    ("gather sources on the topic", STAR),
    ("search the web for references", STAR),
    ("survey the existing literature", STAR),
    ("collect findings from multiple papers", STAR),
    ("find relevant prior art", STAR),
    ("first do X then do Y", PIPELINE),
    ("run the stages of the pipeline", PIPELINE),
    ("process the data, then summarise", PIPELINE),
    ("write a short recommendation", SINGLE),
    ("rename a variable", SINGLE),
    ("fix the typo", SINGLE),
])
def test_classify_step_topology_per_keyword_class(description, expected):
    assert classify_step_topology(description) == expected


@pytest.mark.unit
def test_classify_step_topology_default_is_single():
    # No cue at all → conservative single-agent default.
    assert classify_step_topology("update the config value") == SINGLE


@pytest.mark.unit
def test_classify_mesh_beats_star_when_both_present():
    # "compare" (MESH) is checked before "gather" (STAR): a debate, not a survey.
    assert classify_step_topology("compare and gather the options") == MESH


# ---------------------------------------------------------------------------
# 2. PlanStep.topology — back-compat (default None, optional).
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_planstep_topology_defaults_none():
    s = PlanStep(id="s1", description="do a thing")
    assert s.topology is None


@pytest.mark.unit
def test_planstep_topology_settable_and_immutable():
    from dataclasses import replace
    s = PlanStep(id="s1", description="do a thing")
    s2 = replace(s, topology=MESH)
    assert s2.topology == MESH
    assert s.topology is None  # original unchanged (frozen, copy-on-write)


@pytest.mark.unit
def test_plan_round_trips_without_topology():
    # Existing planner callers that never touch topology keep working.
    p = make_plan("1. collect data 2. analyze results 3. write report")
    assert all(st.topology is None for st in p.steps)


# ---------------------------------------------------------------------------
# 3. assign_topologies — manual vs auto-deterministic.
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_assign_manual_all_single_by_default():
    p = make_plan("1. compare A and B 2. write a recommendation")
    out = assign_topologies(p, mode="manual")
    assert all(st.topology == SINGLE for st in out.steps)


@pytest.mark.unit
def test_assign_manual_fixed_topology():
    p = make_plan("1. compare A and B 2. write a recommendation")
    out = assign_topologies(p, mode="manual", fixed=STAR)
    assert all(st.topology == STAR for st in out.steps)


@pytest.mark.unit
def test_assign_manual_does_not_mutate_input():
    p = make_plan("1. compare A and B 2. write a recommendation")
    out = assign_topologies(p, mode="manual")
    assert p is not out
    assert all(st.topology is None for st in p.steps)  # input untouched


@pytest.mark.unit
def test_assign_auto_deterministic_derives_per_step():
    p = make_plan("1. compare vector RAG and GraphRAG "
                  "2. write a short recommendation")
    out = assign_topologies(p, mode="auto")
    tops = [st.topology for st in out.steps]
    assert MESH in tops      # the "compare ..." step
    assert SINGLE in tops    # the "write a recommendation" step


@pytest.mark.unit
def test_assign_auto_deterministic_is_zero_llm():
    # No client passed and llm not set → never calls a model.
    p = make_plan("1. gather sources 2. compare the options")
    out = assign_topologies(p, mode="auto")  # must not raise (no client needed)
    assert [st.topology for st in out.steps] == [STAR, MESH]


@pytest.mark.unit
def test_assign_auto_llm_uses_infer_spec():
    # llm=True + client → routes via infer_spec → select_topology. The canned
    # JSON enumerates two independent subtasks → STAR (Q3 fan-out).
    spec_json = (
        '{"subtasks": ["a", "b"], "subtasks_independent": true, '
        '"single_agent_sufficient": false}'
    )
    client = SpecClient(spec_json)
    p = make_plan("research the landscape")
    out = assign_topologies(p, mode="auto", client=client, llm=True)
    assert out.steps[0].topology == STAR
    assert client.n_calls == len(p.steps)  # one infer call per step


@pytest.mark.unit
def test_assign_auto_llm_requires_client():
    p = make_plan("do a thing")
    with pytest.raises(ValueError):
        assign_topologies(p, mode="auto", llm=True)  # no client


@pytest.mark.unit
def test_assign_unknown_mode_raises():
    p = make_plan("do a thing")
    with pytest.raises(ValueError):
        assign_topologies(p, mode="bogus")


# ---------------------------------------------------------------------------
# 4. run_plan — single-step vs fan-out dispatch.
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_run_plan_single_step_is_one_call():
    p = assign_topologies(make_plan("write a summary"), mode="manual")
    client = FakeClient()
    result = run_plan(p, client)
    assert isinstance(result, DynamicPlanResult)
    assert len(result.runs) == 1
    run0 = result.runs[0]
    assert isinstance(run0, StepRun)
    assert run0.topology == SINGLE
    assert run0.n_agents == 1
    assert client.n_calls == 1


@pytest.mark.unit
def test_run_plan_mesh_step_fans_out():
    p = assign_topologies(make_plan("compare X and Y"), mode="auto")
    assert p.steps[0].topology == MESH
    client = FakeClient()
    result = run_plan(p, client)
    run0 = result.runs[0]
    assert run0.topology == MESH
    # MESH = N draft + N revise + 1 reduce > a single call.
    assert run0.n_agents > 1
    assert client.n_calls > 1


@pytest.mark.unit
def test_run_plan_star_step_fans_out_and_reduces():
    p = assign_topologies(make_plan("gather sources on RAG"), mode="auto")
    assert p.steps[0].topology == STAR
    client = FakeClient()
    result = run_plan(p, client)
    run0 = result.runs[0]
    assert run0.topology == STAR
    # STAR = N workers + 1 reduce.
    assert run0.n_agents > 1
    assert client.n_calls == run0.n_agents


@pytest.mark.unit
def test_run_plan_pipeline_step_runs_stages():
    p = assign_topologies(make_plan("first stage then later stage"),
                          mode="manual", fixed=PIPELINE)
    client = FakeClient()
    result = run_plan(p, client)
    run0 = result.runs[0]
    assert run0.topology == PIPELINE
    assert run0.n_agents >= 2  # ordered stages
    assert client.n_calls == run0.n_agents


@pytest.mark.unit
def test_run_plan_respects_dependency_order():
    # s2 depends on s1; s1's output must be threaded into s2's prompt.
    p = Plan(task="ordered", steps=(
        PlanStep(id="s1", description="produce a fact", topology=SINGLE),
        PlanStep(id="s2", description="use the prior fact",
                 depends_on=("s1",), topology=SINGLE),
    ))
    client = FakeClient()
    result = run_plan(p, client)
    assert [r.step_id for r in result.runs] == ["s1", "s2"]
    # s2's prompt should carry s1's output (reply#1) as upstream context.
    s2_prompt = client.prompts[-1]
    assert "reply#1" in s2_prompt


@pytest.mark.unit
def test_run_plan_unassigned_step_defaults_single():
    # A hand-built plan with no topology assignment still runs (defaults SINGLE).
    p = Plan(task="t", steps=(PlanStep(id="s1", description="x"),))
    client = FakeClient()
    result = run_plan(p, client)
    assert result.runs[0].topology == SINGLE
    assert client.n_calls == 1


@pytest.mark.unit
def test_run_plan_aggregates_tokens():
    p = assign_topologies(make_plan("write a summary"), mode="manual")
    client = FakeClient(tokens=11)
    result = run_plan(p, client)
    assert result.total_tokens == 11
    assert result.runs[0].tokens == 11


@pytest.mark.unit
def test_run_plan_by_id_lookup():
    p = assign_topologies(make_plan("write a summary"), mode="manual")
    result = run_plan(p, FakeClient())
    assert "s1" in result.by_id
    assert result.by_id["s1"].step_id == "s1"


# ---------------------------------------------------------------------------
# 5. Optional FanoutBudget bound (default OFF; cloud opt-in).
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_run_plan_no_budget_is_unbounded():
    # Default: no ceiling, fan-out completes regardless of token count.
    p = assign_topologies(make_plan("compare X and Y"), mode="auto")
    result = run_plan(p, FakeClient(tokens=100))  # no budget
    assert result.total_tokens > 100  # many children, none capped


@pytest.mark.unit
def test_run_plan_budget_aborts_fanout():
    p = assign_topologies(make_plan("compare X and Y"), mode="auto")
    client = FakeClient(tokens=100)
    # First two fan-out children = 200 tokens > ceiling 150 → abort.
    with pytest.raises(BudgetExceeded):
        run_plan(p, client, budget=FanoutBudget(ceiling=150))


@pytest.mark.unit
def test_run_plan_budget_under_ceiling_completes():
    # A single-step plan charges nothing to the budget (no fan-out children).
    p = assign_topologies(make_plan("write a summary"), mode="manual")
    result = run_plan(p, FakeClient(tokens=5), budget=FanoutBudget(ceiling=10_000))
    assert result.runs[0].topology == SINGLE


# ---------------------------------------------------------------------------
# 6. Fan-out BREADTH cap (2026-06-27 gap-flood fix) — max_agents bounds the
#    spoke COUNT, not just concurrency. Regression guard for the 18-spoke /
#    790K-token explosion: a capped max_workers did NOT stop it.
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_facets_n_is_a_hard_cap_not_a_floor():
    # The bug: parts[:max(n, len(parts))] returned ALL enumerated parts, so a
    # description listing 18 subjects yielded 18 facets and n was a no-op.
    from agentkit.topology.dynamic import _facets
    comma_desc = "cover " + ", ".join(f"section {i}" for i in range(18))
    assert len(_facets(comma_desc, 3)) == 3
    enum_desc = " and ".join(f"topic{i}" for i in range(12))
    assert len(_facets(enum_desc, 4)) == 4
    # Fallback (no enumeration) still honours n.
    assert len(_facets("gather sources on the topic", 5)) == 5


@pytest.mark.unit
def test_run_plan_max_agents_caps_star_spokes():
    # A STAR description enumerating 18 items would (pre-fix) fan out to 18
    # workers + 1 reduce. max_agents=5 must clamp it to <=5 workers (+1 reduce).
    desc = "gather sources on " + ", ".join(f"topic{i}" for i in range(18))
    p = Plan(task="t", steps=(PlanStep(id="s1", description=desc, topology=STAR),))
    client = FakeClient()
    result = run_plan(p, client, max_workers=5, max_agents=5)
    assert result.runs[0].n_agents <= 6  # <=5 workers + 1 reduce
    assert client.n_calls == result.runs[0].n_agents


@pytest.mark.unit
def test_run_plan_max_agents_caps_mesh_spokes():
    # MESH = N draft + N revise + 1 reduce; N (facets) capped at max_agents.
    desc = "compare " + ", ".join(f"option{i}" for i in range(18))
    p = Plan(task="t", steps=(PlanStep(id="s1", description=desc, topology=MESH),))
    client = FakeClient()
    result = run_plan(p, client, max_workers=4, max_agents=4)
    assert result.runs[0].n_agents <= 2 * 4 + 1


@pytest.mark.unit
def test_run_plan_max_agents_caps_map_buckets():
    # Upstream emits 30 URLs; a MAP step would (pre-fix) spawn 30 workers.
    # max_agents=5 must bucket them into <=5 workers (+1 reduce).
    from agentkit.topology.core import MAP
    urls = "\n".join(f"https://example.com/{i}" for i in range(30))

    class UrlClient(FakeClient):
        def chat(self, messages, tools=None):
            self.n_calls += 1
            # s1 (first call) emits the URL list; MAP workers echo.
            text = urls if self.n_calls == 1 else f"reply#{self.n_calls}"
            return ChatResult(text=text, total_tokens=self.tokens)

    p = Plan(task="t", steps=(
        PlanStep(id="s1", description="produce links", topology=SINGLE),
        PlanStep(id="s2", description="summarise each", depends_on=("s1",), topology=MAP),
    ))
    result = run_plan(p, UrlClient(), max_workers=5, max_agents=5)
    s2 = result.by_id["s2"]
    assert s2.topology == MAP
    assert s2.n_agents <= 6  # <=5 buckets + 1 reduce


@pytest.mark.unit
def test_run_plan_star_uses_injected_reducer():
    """run_plan(reducer=fn) replaces STAR's generic synthesis with fn(drafts)
    (DESIGN §4.5 — the orchestrator injects a section-aware merge/refine/review
    without agentkit core learning about sections/weaknesses)."""
    seen = {}

    def my_reducer(drafts):
        seen["drafts"] = list(drafts)
        return ("MERGED::" + " | ".join(drafts), 9)

    p = Plan(task="t", steps=(PlanStep(id="s1", description="gather sources on RAG", topology=STAR),))
    result = run_plan(p, FakeClient(), reducer=my_reducer)
    assert result.runs[0].output.startswith("MERGED::")
    assert len(seen["drafts"]) >= 1               # reducer got the worker drafts
    assert result.runs[0].tokens >= 9             # reducer tokens added to the sum


@pytest.mark.unit
def test_run_plan_reducer_default_is_generic_synthesis():
    """No reducer → unchanged generic STAR synthesis (back-compat)."""
    p = Plan(task="t", steps=(PlanStep(id="s1", description="gather sources on RAG", topology=STAR),))
    result = run_plan(p, FakeClient())
    assert not result.runs[0].output.startswith("MERGED::")


@pytest.mark.unit
def test_run_plan_no_max_agents_preserves_cli_map_one_per_item():
    # Back-compat: with no cap, MAP keeps one-worker-per-item (CLI behaviour).
    from agentkit.topology.core import MAP
    urls = "\n".join(f"https://example.com/{i}" for i in range(4))

    class UrlClient(FakeClient):
        def chat(self, messages, tools=None):
            self.n_calls += 1
            text = urls if self.n_calls == 1 else f"reply#{self.n_calls}"
            return ChatResult(text=text, total_tokens=self.tokens)

    p = Plan(task="t", steps=(
        PlanStep(id="s1", description="produce links", topology=SINGLE),
        PlanStep(id="s2", description="summarise each", depends_on=("s1",), topology=MAP),
    ))
    result = run_plan(p, UrlClient())  # no max_agents
    assert result.by_id["s2"].n_agents == 4 + 1  # one per item + reduce
