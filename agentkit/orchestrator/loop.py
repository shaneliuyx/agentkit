"""agentkit.orchestrator.loop — the autonomous orchestration loop (integration).

This is the integration layer that composes the pure decision modules
(stall / diversity / select) with the durable state layer (state.py) and the
deterministic context compactor (agentkit.context.compact).

Design ideas it encodes:

  - FRESH-SESSION + FILE-STATE INJECTION: each spawned worker is NOT handed the
    full run history. Instead the orchestrator renders the accumulated findings
    + iteration log as messages, runs the deterministic ``compact`` over them,
    and injects only that curated brief. Workers start fresh with a small,
    relevant context — the compactor is the tier that makes this cheap.
  - EXECUTION != EVALUATION: ``spawn`` does the work and returns findings + a
    progress metric. The PURE ``assess`` (stall.py) judges whether that work
    was productive and decides continue / pivot / escalate.
  - PIVOT = STRUCTURE, NOT TACTICS: on a pivot the orchestrator logs the
    decision and relies on ``candidate_directions`` to supply a structurally
    different next direction; it does not silently retry the same shape.
  - INJECTED CLOCK: wall-clock budget uses an injected ``clock`` (default
    ``time.perf_counter``) so tests run with a fake deterministic clock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from agentkit.context import compact
from agentkit.orchestrator.diversity import is_novel
from agentkit.orchestrator.fanout import BudgetExceeded, FanoutBudget
from agentkit.orchestrator.stall import (
    ESCALATE,
    PIVOT,
    assess,
    exceeds_budget,
)
from agentkit.orchestrator.state import (
    DECISION,
    INFO,
    Finding,
    ProgressState,
    append_direction,
    append_finding,
    append_iteration_log,
    load_progress,
    log_event,
    read_directions,
    read_findings,
    save_progress,
)
from agentkit.types import Message


@runtime_checkable
class Spawn(Protocol):
    """A worker spawner.

    Given a chosen direction, an injected (compacted) context brief, and the
    state directory, it does the work and returns ``(new_findings, metric)``
    where ``metric`` is a single progress number the orchestrator can compare
    across rounds.
    """

    def __call__(
        self, direction: str, injected_context: str, state_dir: str
    ) -> tuple[list[Finding], float]:
        ...


@dataclass(frozen=True)
class OrchestratorConfig:
    """Tunable knobs for an orchestration run."""

    max_rounds: int = 15
    max_seconds: float = 1800.0
    pivot_at: int = 2
    escalate_at: int = 4
    diversity_threshold: float = 0.6
    transcript_lines: int = 120
    # P39: aggregate per-round (fan-out) token spend and ABORT when the running
    # sum crosses this ceiling. None (default) = no token ceiling, so existing
    # callers are unaffected — rounds/wall-seconds remain the only bounds.
    max_fanout_tokens: float | None = None


def _render_findings_as_messages(findings: list[Finding]) -> list[Message]:
    """Render accumulated findings + iteration history as OpenAI-style messages.

    These feed the deterministic compactor, which distills them into the small
    curated brief each fresh worker receives — not the full raw history.
    """
    messages: list[Message] = []
    for f in findings:
        messages.append({"role": "user", "content": f"Direction: {f.direction}"})
        body = f.summary
        if f.evidence:
            body = f"{body}\nEvidence: {f.evidence}"
        messages.append({"role": "assistant", "content": body})
    return messages


def _build_injected_context(state_dir: str, transcript_lines: int) -> str:
    """Compact the accumulated findings into a fresh-session brief."""
    findings = read_findings(state_dir)
    if not findings:
        return ""
    messages = _render_findings_as_messages(findings)
    # keep=0 compacts everything (no verbatim tail) — the worker only needs the
    # distilled brief, not the most-recent turn echoed back.
    result = compact(messages, keep=0, max_lines=transcript_lines)
    return result.text


def run(
    state_dir: str,
    spawn: Spawn,
    candidate_directions: Callable[[ProgressState, list[str]], list[str]],
    config: OrchestratorConfig = OrchestratorConfig(),
    clock: Callable[[], float] = time.perf_counter,
    cost_of_round: Callable[[list[Finding], float], int] = lambda findings, metric: 0,
) -> ProgressState:
    """Drive the autonomous orchestration loop until it stops.

    The loop stops when: status leaves "running" (escalated / aborted), the
    round or wall-clock budget is exceeded, or no novel direction remains.

    Args:
        state_dir:            Durable state directory (already init_task'd).
        spawn:                Worker spawner (does the work; returns findings + metric).
        candidate_directions: Pure-ish supplier of candidate directions given the
                              current progress + the directions already tried. On a
                              pivot it is expected to offer a structurally different
                              option.
        config:               Budgets + thresholds (incl. ``max_fanout_tokens``).
        clock:                Injected monotonic clock (default time.perf_counter).
        cost_of_round:        Maps a round's ``(findings, metric)`` to its token
                              cost. The default (0) means "no token accounting".
                              When ``config.max_fanout_tokens`` is set, these
                              per-round costs are SUMMED into a parent-level
                              ``FanoutBudget`` and the loop aborts the instant
                              the running sum crosses the ceiling (P39).

    Returns:
        The final ProgressState.
    """
    log_file = f"{state_dir}/logs/orchestrator.jsonl"
    start = clock()
    rounds = 0
    prev_metric: float | None = None

    # P39: parent-level running token sum. Only enforced when a ceiling is set.
    budget: FanoutBudget | None = (
        FanoutBudget(ceiling=config.max_fanout_tokens)
        if config.max_fanout_tokens is not None
        else None
    )

    progress = load_progress(state_dir)

    while progress.status == "running":
        # Fresh-session brief: compact the accumulated findings, inject only that.
        injected_context = _build_injected_context(
            state_dir, config.transcript_lines
        )

        tried = read_directions(state_dir)
        candidates = candidate_directions(progress, tried)
        chosen = next(
            (c for c in candidates if is_novel(c, tried, config.diversity_threshold)),
            None,
        )

        if chosen is None:
            # No structurally-new direction remains → escalate.
            progress.status = "escalated"
            log_event(
                log_file, source="orchestrator", level=DECISION,
                event="escalate",
                detail="no novel direction remains; handing off",
                clock=clock,
            )
            save_progress(state_dir, progress)
            break

        # EXECUTION: the worker does the work.
        new_findings, metric = spawn(chosen, injected_context, state_dir)
        for f in new_findings:
            append_finding(state_dir, f)
        append_direction(state_dir, chosen)

        # EVALUATION: the pure assessor judges productivity.
        verdict = assess(
            new_findings=len(new_findings),
            stale_count=progress.stale_count,
            metric_prev=prev_metric,
            metric_new=metric,
            pivot_at=config.pivot_at,
            escalate_at=config.escalate_at,
        )

        rounds += 1
        progress.iteration += 1
        progress.total_findings += len(new_findings)
        progress.stale_count = verdict.stale_count

        log_event(
            log_file, source="orchestrator", level=INFO,
            event="round",
            detail=(f"round={rounds} direction={chosen!r} "
                    f"findings={len(new_findings)} action={verdict.action}"),
            clock=clock,
        )
        append_iteration_log(state_dir, {
            "round": rounds,
            "direction": chosen,
            "new_findings": len(new_findings),
            "metric": metric,
            "action": verdict.action,
            "stale_count": verdict.stale_count,
        })

        if verdict.action == PIVOT:
            log_event(
                log_file, source="orchestrator", level=DECISION,
                event="pivot",
                detail="pivot structure not tactics: " + verdict.reason,
                clock=clock,
            )
        elif verdict.action == ESCALATE:
            progress.status = "escalated"
            log_event(
                log_file, source="orchestrator", level=DECISION,
                event="escalate", detail=verdict.reason, clock=clock,
            )
            save_progress(state_dir, progress)
            break

        save_progress(state_dir, progress)
        prev_metric = metric

        # P39: aggregate this round's fan-out token cost to the parent budget and
        # ABORT the whole loop the instant the running sum crosses the ceiling —
        # per-round caps never bound the total.
        if budget is not None:
            try:
                budget.add(cost_of_round(new_findings, metric))
            except BudgetExceeded as exc:
                progress.status = "aborted"
                log_event(
                    log_file, source="orchestrator", level=DECISION,
                    event="fanout_budget_exceeded",
                    detail=(f"summed fan-out tokens {exc.spent} exceeded ceiling "
                            f"{exc.ceiling}; aborting"),
                    clock=clock,
                )
                save_progress(state_dir, progress)
                break

        if exceeds_budget(rounds, clock() - start,
                          config.max_rounds, config.max_seconds):
            log_event(
                log_file, source="orchestrator", level=INFO,
                event="budget_exceeded",
                detail=f"rounds={rounds} elapsed={clock() - start:.3f}s",
                clock=clock,
            )
            break

    return progress


if __name__ == "__main__":
    import tempfile

    from agentkit.orchestrator.state import init_task

    tmp = tempfile.mkdtemp(prefix="agentkit_orch_")
    init_task(tmp, task_spec="Maximize cache hit rate.")

    # A fake clock that advances 1.0 per read.
    _t = [0.0]

    def _clock() -> float:
        _t[0] += 1.0
        return _t[0]

    # candidate_directions always offers a fresh, token-DISJOINT option, so each
    # one is novel vs the diversity threshold (no shared tokens → Jaccard 0).
    _words = [
        "alpha bravo charlie delta",
        "echo foxtrot golf hotel",
        "india juliet kilo lima",
        "mike november oscar papa",
        "quebec romeo sierra tango",
        "uniform victor whiskey xray",
        "yankee zulu apple banana",
    ]
    _counter = [0]

    def _candidates(progress: ProgressState, tried: list[str]) -> list[str]:
        phrase = _words[_counter[0] % len(_words)]
        _counter[0] += 1
        return [phrase]

    # A fake spawn: productive for the first 2 rounds, then 0 findings forever,
    # which drives stale up → pivot at >=2 → escalate at >=4.
    _round = [0]

    def _spawn(direction: str, injected_context: str,
               state_dir: str) -> tuple[list[Finding], float]:
        _round[0] += 1
        if _round[0] <= 2:
            return ([Finding(direction=direction, summary=f"insight {_round[0]}")],
                    float(_round[0]))
        return ([], 0.0)

    final = run(
        tmp, spawn=_spawn, candidate_directions=_candidates,
        config=OrchestratorConfig(max_rounds=15, max_seconds=1e9),
        clock=_clock,
    )

    # The run escalated once the stall ladder hit escalate_at.
    assert final.status == "escalated", final
    # Two productive rounds → two findings.
    assert final.total_findings == 2, final

    # All chosen directions stayed novel (no duplicates recorded).
    tried = read_directions(tmp)
    assert len(tried) == len(set(tried)), tried

    # The orchestrator log recorded a pivot before the escalate.
    log_text = open(f"{tmp}/logs/orchestrator.jsonl", encoding="utf-8").read()
    assert "pivot" in log_text and "escalate" in log_text, log_text

    # max_rounds is respected: a never-escalating run stops at the round budget.
    tmp2 = tempfile.mkdtemp(prefix="agentkit_orch2_")
    init_task(tmp2, task_spec="Endless productive run.")
    _c2 = [0]

    def _cand2(progress: ProgressState, tried: list[str]) -> list[str]:
        phrase = _words[_c2[0] % len(_words)]
        _c2[0] += 1
        return [phrase]

    def _spawn2(direction: str, injected_context: str,
                state_dir: str) -> tuple[list[Finding], float]:
        # Always productive (rising metric) so it never stalls/escalates.
        return ([Finding(direction=direction, summary="ok")], float(len(direction)))

    final2 = run(
        tmp2, spawn=_spawn2, candidate_directions=_cand2,
        config=OrchestratorConfig(max_rounds=3, max_seconds=1e9),
        clock=_clock,
    )
    assert final2.iteration == 3, final2  # stopped exactly at max_rounds

    # P39: a fan-out token ceiling aborts the loop on the running SUM. Each round
    # "costs" 100 tokens; ceiling 350 → aborts on round 4 (sum 400 > 350).
    tmp3 = tempfile.mkdtemp(prefix="agentkit_orch3_")
    init_task(tmp3, task_spec="Runaway fan-out.")
    _c3 = [0]

    def _cand3(progress: ProgressState, tried: list[str]) -> list[str]:
        phrase = _words[_c3[0] % len(_words)]
        _c3[0] += 1
        return [phrase]

    def _spawn3(direction: str, injected_context: str,
                state_dir: str) -> tuple[list[Finding], float]:
        return ([Finding(direction=direction, summary="ok")], 1.0)

    final3 = run(
        tmp3, spawn=_spawn3, candidate_directions=_cand3,
        config=OrchestratorConfig(max_rounds=999, max_seconds=1e9,
                                  max_fanout_tokens=350.0),
        clock=_clock,
        cost_of_round=lambda findings, metric: 100,
    )
    assert final3.status == "aborted", final3
    assert final3.iteration == 4, final3  # 100+200+300+400 trips on round 4
    log3 = open(f"{tmp3}/logs/orchestrator.jsonl", encoding="utf-8").read()
    assert "fanout_budget_exceeded" in log3 and "400" in log3, log3

    print("loop self-check OK")
