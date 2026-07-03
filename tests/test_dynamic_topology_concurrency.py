"""Finding 2 (Codex adversarial review, 2026-07-02): run_plan() used module-level
globals (_POOL_WORKERS/_MAX_SPOKES/_REDUCER) for per-call config. Two concurrent
run_plan() calls on different threads (e.g. concurrent Studio sessions) could
overwrite each other's reducer/sizing mid-run. They are now contextvars — isolated
per call/thread — which this test proves.
"""
from __future__ import annotations

import threading

import pytest

from agentkit.planner.core import Plan, PlanStep
from agentkit.topology import STAR, run_plan
from agentkit.types import ChatResult, Message


class _FakeClient:
    def chat(self, messages: list[Message], tools=None) -> ChatResult:
        return ChatResult(text="draft", total_tokens=5)


@pytest.mark.unit
def test_concurrent_run_plan_calls_do_not_cross_contaminate():
    # Two runs, each with its OWN reducer + breadth cap, forced to be mid-run at the
    # same time via a barrier inside each reducer. With module globals, whichever run
    # entered run_plan() last would win for BOTH → both outputs carry the same tag and
    # both use the same cap. With contextvars, each run sees only its own config.
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    # 6 enumerated topics so max_agents actually clamps breadth differently per run.
    desc = "gather sources on " + ", ".join(f"topic{i}" for i in range(6))

    def make_reducer(tag: str):
        def reducer(drafts):
            barrier.wait()  # hold both runs inside their reducer simultaneously
            return (f"{tag}::" + "|".join(drafts), 3)
        return reducer

    def run(tag: str, max_agents: int) -> None:
        plan = Plan(task="t", steps=(PlanStep(id="s1", description=desc, topology=STAR),))
        res = run_plan(
            plan, _FakeClient(), max_agents=max_agents, reducer=make_reducer(tag)
        )
        results[tag] = {
            "output": res.runs[0].output,
            "n_agents": res.runs[0].n_agents,
        }

    ta = threading.Thread(target=run, args=("A", 2))
    tb = threading.Thread(target=run, args=("B", 5))
    ta.start(); tb.start(); ta.join(); tb.join()

    # Each run's own reducer produced its own output (no reducer bleed).
    assert results["A"]["output"].startswith("A::")
    assert results["B"]["output"].startswith("B::")
    # Each run's own breadth cap held (no _MAX_SPOKES bleed): A ≤ 2 workers + reduce,
    # B ≤ 5 workers + reduce.
    assert results["A"]["n_agents"] <= 2 + 1
    assert results["B"]["n_agents"] <= 5 + 1
    assert results["B"]["n_agents"] > results["A"]["n_agents"]
