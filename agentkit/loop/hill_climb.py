"""agentkit.loop.hill_climb — end-to-end self-improvement pipeline.

The primitives in agentkit/evolve/core.py (DGM mutations, GRPO distillation,
RHO self-preference, Gate admission) are complete. What was missing is the
TRACE INTAKE: nothing read production AgentResult.trajectory objects and mined
weaknesses from them. This module closes that gap.

Pipeline::

  1. mine_weaknesses()    — LLM scans trajectories, extracts repeated failure
                             patterns (Self-Harness technique)
  2. make_llm_proposer()  — already in evolve/core.py; targeted by weaknesses
  3. evolve_prompt()      — already in evolve/core.py; runs DGM keep/discard
  4. Gate admission       — already in gates/core.py; every variant must pass

Design discipline:
  - The gate is NEVER bypassed; mine_weaknesses is the only new LLM call.
  - mine_weaknesses never raises — broken trajectories are skipped.
  - The control loop (keep/discard, strictly-improving check) is model-free.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentkit.agent.loop import AgentResult
from agentkit.evolve.core import OptimizeResult, evolve_prompt, make_llm_proposer
from agentkit.gates.core import Gate
from agentkit.types import LLMClient, Message


@dataclass(frozen=True)
class TraceWeakness:
    """One recurring failure pattern extracted from agent trajectories.

    Attributes:
        pattern:   One-sentence description of the failure pattern.
        frequency: How many trajectories exhibited this pattern.
        example:   Short verbatim excerpt from one trajectory showing the
                   pattern (for steering the proposer, not for display).
    """

    pattern: str
    frequency: int
    example: str


_WEAKNESS_SYSTEM = (
    "You are a meta-analyst reviewing AI agent execution traces. For each "
    "trajectory, identify ONE recurring failure pattern — something the agent "
    "did that a better agent would not. Be specific. "
    'Respond with ONLY a JSON array of objects: '
    '[{{"pattern": "<one sentence>", "example": "<short verbatim excerpt>"}}]. '
    "Return at most {top_k} entries. If no clear failures exist, return []."
)


def mine_weaknesses(
    trajectories: list[AgentResult],
    client: LLMClient,
    top_k: int = 5,
) -> list[TraceWeakness]:
    """LLM scans trajectories, extracts repeated failure patterns.

    This is the Self-Harness weakness-targeting link: observed failure patterns
    from production traces are distilled into a compact list that steers the
    mutation proposer toward fixing real-world breakdowns rather than
    speculating about hypothetical improvements.

    Never raises — any LLM error or parse failure returns an empty list so the
    pipeline degrades gracefully.

    Args:
        trajectories: Completed agent runs (AgentResult objects from agent/loop).
        client:       Injected LLMClient; the only LLM call in this module.
        top_k:        Maximum number of weakness patterns to return.

    Returns:
        A list of TraceWeakness, deduplicated and sorted by frequency desc.
    """
    if not trajectories:
        return []

    trace_summaries: list[str] = []
    for result in trajectories:
        if not hasattr(result, "trajectory") or not result.trajectory:
            continue
        lines: list[str] = [f"task: {result.task[:200]}"]
        for step in result.trajectory[:20]:  # cap to keep context manageable
            role = getattr(step, "role", "?")
            if role == "assistant":
                content = str(getattr(step, "content", ""))[:300]
                lines.append(f"  agent: {content}")
            elif role == "tool":
                tool = getattr(step, "tool_name", "tool")
                result_text = str(getattr(step, "tool_result", ""))[:200]
                lines.append(f"  {tool} -> {result_text}")
        lines.append(f"  success={result.success}  rounds={result.rounds_used}")
        trace_summaries.append("\n".join(lines))

    if not trace_summaries:
        return []

    system = _WEAKNESS_SYSTEM.format(top_k=top_k)
    body = "\n\n---\n\n".join(trace_summaries[:20])
    messages: list[Message] = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Agent trajectories to analyze:\n\n{body}"},
    ]

    try:
        response = client.chat(messages)
        raw = (getattr(response, "text", "") or "").strip()
        if "```" in raw:
            raw = "\n".join(
                ln for ln in raw.splitlines() if not ln.strip().startswith("```")
            ).strip()
        items: list[dict[str, Any]] = json.loads(raw)
        if not isinstance(items, list):
            return []
    except Exception:  # noqa: BLE001
        return []

    seen: dict[str, TraceWeakness] = {}
    for item in items[:top_k]:
        if not isinstance(item, dict):
            continue
        pattern = str(item.get("pattern", "")).strip()
        example = str(item.get("example", "")).strip()
        if not pattern:
            continue
        if pattern in seen:
            old = seen[pattern]
            seen[pattern] = TraceWeakness(
                pattern=pattern,
                frequency=old.frequency + 1,
                example=old.example or example,
            )
        else:
            seen[pattern] = TraceWeakness(pattern=pattern, frequency=1, example=example)

    return sorted(seen.values(), key=lambda w: -w.frequency)


def hill_climb_from_traces(
    baseline_prompt: str,
    trajectories: list[AgentResult],
    gate: Gate,
    client: LLMClient,
    *,
    baseline_score: float,
    evaluate: Any,
    epochs: int = 10,
    min_delta: float = 0.0,
    cwd: str = ".",
    top_k_weaknesses: int = 5,
) -> OptimizeResult:
    """End-to-end self-improvement: trace intake -> weakness mining -> evolve.

    Mines weaknesses from production trajectories, builds a weakness-targeted
    proposer, then runs evolve_prompt(). The gate is never bypassed.

    Args:
        baseline_prompt:   The system prompt to optimize.
        trajectories:      Recent agent runs (source of failure patterns).
        gate:              The LEARN Gate every candidate must pass.
        client:            Injected LLMClient (weakness mining + mutation).
        baseline_score:    The evaluator score of baseline_prompt.
        evaluate:          Injected Evaluator (same contract as evolve/core.Evaluator).
        epochs:            Number of mutation/keep-discard rounds.
        min_delta:         Minimum score improvement required by the gate.
        cwd:               Jailed working directory for the gate's sandbox.
        top_k_weaknesses:  Number of weakness patterns to mine and target.

    Returns:
        An OptimizeResult with the gate-admitted best prompt + delta.
    """
    weaknesses = mine_weaknesses(trajectories, client, top_k=top_k_weaknesses)
    weakness_text = "\n".join(f"- {w.pattern}" for w in weaknesses) if weaknesses else None

    proposer = make_llm_proposer(client, weaknesses=weakness_text)

    return evolve_prompt(
        baseline_prompt,
        propose=proposer,
        evaluate=evaluate,
        gate=gate,
        baseline_score=baseline_score,
        epochs=epochs,
        min_delta=min_delta,
        cwd=cwd,
    )


if __name__ == "__main__":
    results: list[AgentResult] = []

    class _BadClient:
        def chat(self, _msgs):
            raise RuntimeError("no network in self-check")

    assert mine_weaknesses(results, _BadClient()) == []  # type: ignore

    class _FakeResult:
        task = "Do thing"
        trajectory = []
        success = False
        rounds_used = 1

    assert mine_weaknesses([_FakeResult()], _BadClient()) == []  # type: ignore

    print("loop.hill_climb self-check OK")
