"""Tests for agentkit.orchestrator.fanout — P39 fan-out cost ceiling + atomic writes.

Two EDP compliance fixes are exercised here:

  - P39 Fan-out Cost Aggregation + Parent-Level Ceiling: a FanoutBudget SUMS
    child token usage (read off ChatResult.total_tokens) and aborts the whole
    fan-out the instant the running sum crosses a configurable ceiling. A
    per-child cap never bounds the total; the parent-level sum does.
  - P42 Atomic-Publish: every full-rewrite JSON/text artifact is written via a
    temp file + os.replace, so a crash mid-write never truncates the live file.

Deterministic + model-free: fake ChatResults carry a fixed token cost.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agentkit.orchestrator import (
    BudgetExceeded,
    FanoutBudget,
    Finding,
    OrchestratorConfig,
    ProgressState,
    append_direction,
    cost_of,
    exceeds_fanout_budget,
    init_task,
    load_progress,
    read_directions,
    run,
    save_progress,
)
from agentkit.orchestrator.state import _atomic_write
from agentkit.types import ChatResult


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


def _fake_clock():
    t = [0.0]

    def clock() -> float:
        t[0] += 1.0
        return t[0]

    return clock


# ---------------------------------------------------------------------------
# P39 — fan-out cost aggregation + parent-level ceiling
# ---------------------------------------------------------------------------

def test_cost_of_reads_total_tokens():
    # ChatResult exposes total_tokens; cost_of sums that field, model-free.
    assert cost_of(ChatResult(text="hi", total_tokens=7)) == 7
    assert cost_of(ChatResult(text="", total_tokens=0)) == 0


def test_fanout_budget_sums_child_token_usage():
    budget = FanoutBudget(ceiling=1000)
    budget.add(ChatResult(text="a", total_tokens=100))
    budget.add(ChatResult(text="b", total_tokens=250))
    assert budget.spent() == 350
    assert budget.exceeds(1000) is False


def test_fanout_over_ceiling_aborts_and_reports_summed_cost():
    # N=10 fake children each "cost" k=100 tokens; N*k = 1000 > ceiling 350.
    N, K, CEILING = 10, 100, 350
    budget = FanoutBudget(ceiling=CEILING)
    aborted_at = None
    for i in range(N):
        try:
            budget.add(ChatResult(text=f"c{i}", total_tokens=K))
        except BudgetExceeded as exc:
            aborted_at = i
            # The reported spend is the SUMMED child cost at the abort point.
            assert exc.spent == budget.spent()
            assert exc.ceiling == CEILING
            break
    # 100+200+300+400 → trips on the 4th child (sum 400 > 350).
    assert aborted_at == 3
    assert budget.spent() == 400
    assert budget.exceeds() is True


def test_fanout_under_ceiling_completes():
    # 3 children at 100 each = 300 < ceiling 350: no abort.
    budget = FanoutBudget(ceiling=350)
    for i in range(3):
        budget.add(ChatResult(text=f"c{i}", total_tokens=100))
    assert budget.spent() == 300
    assert budget.exceeds() is False


def test_fanout_budget_accepts_injected_int_cost():
    # When a result does not expose usage, a plain int cost is accepted too.
    budget = FanoutBudget(ceiling=50)
    budget.add(30)
    with pytest.raises(BudgetExceeded):
        budget.add(30)  # 60 > 50
    assert budget.spent() == 60


def test_exceeds_fanout_budget_sibling_of_exceeds_budget():
    assert exceeds_fanout_budget(spent=400, ceiling=350) is True
    assert exceeds_fanout_budget(spent=350, ceiling=350) is False
    assert exceeds_fanout_budget(spent=10, ceiling=350) is False


# ---------------------------------------------------------------------------
# P42 — atomic-publish for full-file artifact writes
# ---------------------------------------------------------------------------

def test_atomic_write_round_trips_and_leaves_no_temp():
    d = Path(tempfile.mkdtemp(prefix="agentkit_atomic_"))
    target = d / "artifact.json"
    _atomic_write(target, json.dumps({"k": "v"}))
    assert json.loads(target.read_text(encoding="utf-8")) == {"k": "v"}
    # No leftover .tmp siblings after a successful publish.
    leftover = [p.name for p in d.iterdir() if p.name != "artifact.json"]
    assert leftover == [], leftover


def test_atomic_write_overwrite_is_clean():
    d = Path(tempfile.mkdtemp(prefix="agentkit_atomic_ow_"))
    target = d / "a.txt"
    _atomic_write(target, "first")
    _atomic_write(target, "second")
    assert target.read_text(encoding="utf-8") == "second"
    assert [p.name for p in d.iterdir()] == ["a.txt"]


def test_atomic_write_no_partial_file_when_serialization_raises():
    d = Path(tempfile.mkdtemp(prefix="agentkit_atomic_raise_"))
    target = d / "a.json"
    _atomic_write(target, "good")

    class _Boom:
        def __str__(self) -> str:  # json.dumps default= path won't save us
            raise ValueError("cannot serialize")

    with pytest.raises((TypeError, ValueError)):
        _atomic_write(target, json.dumps({"bad": _Boom()}))
    # The original file is intact; no half-written temp left behind.
    assert target.read_text(encoding="utf-8") == "good"
    assert [p.name for p in d.iterdir()] == ["a.json"]


def test_save_progress_is_atomic_no_temp_remains():
    tmp = tempfile.mkdtemp(prefix="agentkit_state_atomic_")
    init_task(tmp, task_spec="atomic spec")
    save_progress(tmp, ProgressState(iteration=2, total_findings=4))
    p = load_progress(tmp)
    assert p.iteration == 2 and p.total_findings == 4
    state_dir = Path(tmp) / "state"
    temps = [f.name for f in state_dir.iterdir() if f.name.endswith(".tmp")]
    assert temps == [], temps


def test_append_direction_is_atomic_no_temp_remains():
    tmp = tempfile.mkdtemp(prefix="agentkit_dir_atomic_")
    init_task(tmp, task_spec="dirs")
    append_direction(tmp, "alpha")
    append_direction(tmp, "bravo")
    assert read_directions(tmp) == ["alpha", "bravo"]
    state_dir = Path(tmp) / "state"
    temps = [f.name for f in state_dir.iterdir() if f.name.endswith(".tmp")]
    assert temps == [], temps


# ---------------------------------------------------------------------------
# P39 integration — the loop aborts when summed child tokens cross the ceiling
# ---------------------------------------------------------------------------

def test_loop_aborts_when_fanout_tokens_exceed_ceiling():
    tmp = tempfile.mkdtemp(prefix="agentkit_loop_ceiling_")
    init_task(tmp, task_spec="Runaway fan-out.")

    # Every productive round "costs" 100 tokens; ceiling 350 → aborts when the
    # summed spend crosses (after round 4: 400 > 350).
    def spawn(direction, injected_context, state_dir):
        return ([Finding(direction=direction, summary="ok")], 1.0)

    final = run(
        tmp, spawn=spawn, candidate_directions=_disjoint_candidates(),
        config=OrchestratorConfig(
            max_rounds=999, max_seconds=1e9, max_fanout_tokens=350.0,
        ),
        clock=_fake_clock(),
        cost_of_round=lambda findings, metric: 100,
    )

    assert final.status == "aborted"
    assert final.iteration == 4  # 100+200+300+400 → trips on round 4
    log_text = open(f"{tmp}/logs/orchestrator.jsonl", encoding="utf-8").read()
    assert "fanout_budget_exceeded" in log_text
    assert "400" in log_text  # the summed cost is reported in the terminal reason


def test_loop_completes_under_fanout_ceiling():
    tmp = tempfile.mkdtemp(prefix="agentkit_loop_underceiling_")
    init_task(tmp, task_spec="Bounded fan-out.")

    def spawn(direction, injected_context, state_dir):
        return ([Finding(direction=direction, summary="ok")], 1.0)

    # 3 rounds × 100 = 300 < 350: never aborts; stops at max_rounds instead.
    final = run(
        tmp, spawn=spawn, candidate_directions=_disjoint_candidates(),
        config=OrchestratorConfig(
            max_rounds=3, max_seconds=1e9, max_fanout_tokens=350.0,
        ),
        clock=_fake_clock(),
        cost_of_round=lambda findings, metric: 100,
    )
    assert final.status == "running"  # budget never tripped
    assert final.iteration == 3


def test_loop_without_fanout_ceiling_is_unbounded_by_tokens():
    # Default config: max_fanout_tokens is None → no token ceiling, behaves as
    # before (stops at max_rounds), so existing callers are unaffected.
    tmp = tempfile.mkdtemp(prefix="agentkit_loop_notokceiling_")
    init_task(tmp, task_spec="No token ceiling.")

    def spawn(direction, injected_context, state_dir):
        return ([Finding(direction=direction, summary="ok")], 1.0)

    final = run(
        tmp, spawn=spawn, candidate_directions=_disjoint_candidates(),
        config=OrchestratorConfig(max_rounds=3, max_seconds=1e9),
        clock=_fake_clock(),
    )
    assert final.status == "running"
    assert final.iteration == 3
