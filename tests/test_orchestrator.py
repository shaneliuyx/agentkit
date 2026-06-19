"""Tests for agentkit.orchestrator — stall, diversity, select, and loop."""

from __future__ import annotations

import tempfile

import pytest

from agentkit.orchestrator import (
    Dimension,
    Finding,
    OrchestratorConfig,
    ProgressState,
    Rubric,
    assess,
    cascade,
    exceeds_budget,
    init_task,
    is_novel,
    prefilter,
    read_directions,
    run,
    score_and_rank,
    similarity,
)
from agentkit.orchestrator.stall import CONTINUE, ESCALATE, PIVOT


# ---------------------------------------------------------------------------
# stall
# ---------------------------------------------------------------------------

def test_assess_reset_on_productive_round():
    a = assess(new_findings=3, stale_count=2)
    assert a.action == CONTINUE
    assert a.stale_count == 0


def test_assess_continue_below_pivot():
    a = assess(new_findings=0, stale_count=0)
    assert a.action == CONTINUE
    assert a.stale_count == 1


def test_assess_pivot_at_threshold():
    a = assess(new_findings=0, stale_count=1, pivot_at=2, escalate_at=4)
    assert a.action == PIVOT
    assert a.stale_count == 2
    assert "structural" in a.reason


def test_assess_escalate_at_threshold():
    a = assess(new_findings=0, stale_count=3, pivot_at=2, escalate_at=4)
    assert a.action == ESCALATE
    assert a.stale_count == 4


def test_assess_metric_regression_is_unproductive():
    a = assess(new_findings=5, stale_count=0, metric_prev=0.9, metric_new=0.4)
    assert a.action == CONTINUE
    assert a.stale_count == 1


def test_exceeds_budget():
    assert exceeds_budget(rounds=15, elapsed_s=0.0, max_rounds=15) is True
    assert exceeds_budget(rounds=0, elapsed_s=1800.0, max_seconds=1800.0) is True
    assert exceeds_budget(rounds=2, elapsed_s=5.0) is False


# ---------------------------------------------------------------------------
# diversity
# ---------------------------------------------------------------------------

def test_similarity_bounds():
    assert similarity("a b c", "a b c") == 1.0
    assert similarity("a b c", "x y z") == 0.0
    assert similarity("", "") == 1.0


def test_is_novel():
    tried = ["optimize the database index", "add a redis cache"]
    assert is_novel("redesign the frontend routing layer", tried) is True
    assert is_novel("optimize the database index strategy", tried, threshold=0.6) is False


# ---------------------------------------------------------------------------
# select cascade
# ---------------------------------------------------------------------------

def _rubric() -> Rubric:
    return Rubric(
        dimensions=(
            Dimension(key="impact", name="Impact", weight=3.0),
            Dimension(key="effort", name="Low effort", weight=1.0),
        )
    )


def test_rubric_weighted_aggregate():
    rb = _rubric()
    assert abs(rb.aggregate({"impact": 1.0, "effort": 0.0}) - 0.75) < 1e-9
    assert abs(rb.aggregate({"impact": 0.5}) - 0.375) < 1e-9  # missing dim → 0


def test_rubric_zero_weight_guard():
    rb = Rubric(dimensions=(Dimension("x", "X", 0.0),))
    assert rb.aggregate({"x": 1.0}) == 0.0


def test_prefilter_cuts_count():
    items = [{"ok": True}, {"ok": False}, {"ok": True}]
    assert len(prefilter(items, lambda it: it["ok"])) == 2


def test_cascade_orders_by_weighted_aggregate():
    items = [
        {"name": "a", "impact": 0.9, "effort": 0.1, "ok": True},
        {"name": "b", "impact": 0.2, "effort": 0.9, "ok": True},
        {"name": "c", "impact": 1.0, "effort": 1.0, "ok": False},
    ]

    def scorer(it, rb):
        return {"impact": it["impact"], "effort": it["effort"]}

    ranked = cascade(items, lambda it: it["ok"], _rubric(), scorer)
    assert len(ranked) == 2  # c prefiltered
    assert ranked[0][0]["name"] == "a"  # impact-weighted winner
    assert ranked[0][1] > ranked[1][1]


def test_score_and_rank_is_stable_on_ties():
    items = [{"n": 1}, {"n": 2}]
    rb = Rubric(dimensions=(Dimension("k", "K", 1.0),))
    ranked = score_and_rank(items, rb, lambda it, r: {"k": 0.5})
    assert [t[0]["n"] for t in ranked] == [1, 2]  # original order preserved


# ---------------------------------------------------------------------------
# loop integration (fake spawn + fake clock)
# ---------------------------------------------------------------------------

def _fake_clock():
    t = [0.0]

    def clock() -> float:
        t[0] += 1.0
        return t[0]

    return clock


# Token-DISJOINT phrases so each successive direction is novel (Jaccard 0 vs the
# others); avoids the diversity guard rejecting near-duplicate fixture strings.
_DISJOINT = [
    "alpha bravo charlie delta",
    "echo foxtrot golf hotel",
    "india juliet kilo lima",
    "mike november oscar papa",
    "quebec romeo sierra tango",
    "uniform victor whiskey xray",
    "yankee zulu apple banana",
]


def _disjoint_candidates():
    n = [0]

    def candidates(progress: ProgressState, tried: list[str]) -> list[str]:
        phrase = _DISJOINT[n[0] % len(_DISJOINT)]
        n[0] += 1
        return [phrase]

    return candidates


def test_loop_escalates_after_stall_and_keeps_directions_novel():
    tmp = tempfile.mkdtemp(prefix="agentkit_test_orch_")
    init_task(tmp, task_spec="Maximize hit rate.")

    rnd = [0]

    def spawn(direction, injected_context, state_dir):
        rnd[0] += 1
        if rnd[0] <= 2:
            return ([Finding(direction=direction, summary=f"insight {rnd[0]}")],
                    float(rnd[0]))
        return ([], 0.0)  # 0 findings → drives stall up

    final = run(
        tmp, spawn=spawn, candidate_directions=_disjoint_candidates(),
        config=OrchestratorConfig(max_rounds=15, max_seconds=1e9),
        clock=_fake_clock(),
    )

    assert final.status == "escalated"
    assert final.total_findings == 2  # only the two productive rounds

    tried = read_directions(tmp)
    assert len(tried) == len(set(tried))  # all directions stayed novel/unique

    log_text = open(f"{tmp}/logs/orchestrator.jsonl", encoding="utf-8").read()
    assert "pivot" in log_text  # pivot fired before escalate
    assert "escalate" in log_text


def test_loop_escalates_when_no_novel_direction():
    tmp = tempfile.mkdtemp(prefix="agentkit_test_orch_nonovel_")
    init_task(tmp, task_spec="Stuck.")

    def candidates(progress: ProgressState, tried: list[str]) -> list[str]:
        # Always the SAME direction → after the first round it is non-novel.
        return ["one fixed direction that never changes ever"]

    def spawn(direction, injected_context, state_dir):
        return ([Finding(direction=direction, summary="ok")], 1.0)

    final = run(
        tmp, spawn=spawn, candidate_directions=candidates,
        config=OrchestratorConfig(max_rounds=15, max_seconds=1e9),
        clock=_fake_clock(),
    )
    assert final.status == "escalated"
    # Exactly one productive round before the repeated direction is rejected.
    assert final.iteration == 1


def test_loop_respects_max_rounds():
    tmp = tempfile.mkdtemp(prefix="agentkit_test_orch_maxr_")
    init_task(tmp, task_spec="Endless.")

    def spawn(direction, injected_context, state_dir):
        # Always productive with a rising metric → never stalls.
        return ([Finding(direction=direction, summary="ok")], float(len(direction)))

    final = run(
        tmp, spawn=spawn, candidate_directions=_disjoint_candidates(),
        config=OrchestratorConfig(max_rounds=3, max_seconds=1e9),
        clock=_fake_clock(),
    )
    assert final.iteration == 3
    assert final.status == "running"  # stopped by budget, not escalation


def test_loop_respects_time_budget():
    tmp = tempfile.mkdtemp(prefix="agentkit_test_orch_time_")
    init_task(tmp, task_spec="Time bound.")

    def spawn(direction, injected_context, state_dir):
        return ([Finding(direction=direction, summary="ok")], float(len(direction)))

    # Clock jumps 100s per read → time budget of 1s is exceeded immediately.
    def big_clock(t=[0.0]):
        t[0] += 100.0
        return t[0]

    final = run(
        tmp, spawn=spawn, candidate_directions=_disjoint_candidates(),
        config=OrchestratorConfig(max_rounds=999, max_seconds=1.0),
        clock=big_clock,
    )
    assert final.iteration == 1  # one round then budget breaks the loop
