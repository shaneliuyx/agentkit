"""agentkit.loop.suggest — LLM-assisted LoopGoal parameter inference.

The core function ``suggest_goal_params()`` takes a plain-English ``end_state``
description and an injected ``LLMClient``, then returns a ``GoalSuggestion``
dataclass with ready-to-use parameter values. No HTTP, no Studio dependency.

Usage from any codebase::

    from agentkit.loop.suggest import suggest_goal_params
    suggestion = suggest_goal_params("All billing tests pass", client=my_client)
    goal = LoopGoal(
        end_state="All billing tests pass",
        evidence_cmd=suggestion.evidence_cmd,
        success_pattern=suggestion.success_pattern,
        max_turns=suggestion.max_turns,
    )
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from agentkit.types import LLMClient

_SUGGEST_PROMPT = """\
Given this loop stop condition:
"{end_state}"{task_line}

Suggest LoopGoal parameters. Return ONLY valid JSON, no prose, no markdown:
{{
  "evidence_cmd": "<shell command that verifies the goal; empty string if none applies>",
  "success_pattern": "<regex applied to stdout; empty string if exit code 0 suffices>",
  "max_turns": <int 5-50>,
  "max_tokens": <int 10000-200000>,
  "timeout_s": <int 60-7200>,
  "constraints": ["<constraint1>", "<constraint2>"]
}}

Rules:
- evidence_cmd: prefer pytest for test goals, curl for HTTP health, grep for \
file flags, wc -c for sizes, git log for commits. Leave empty if purely descriptive.
- success_pattern: e.g. \\d+ passed for pytest, "status".*"ok" for HTTP JSON. \
Empty if exit code 0 is sufficient.
- max_turns: 10 for simple/focused tasks, 25 for medium, 40 for complex multi-step.
- constraints: infer implicit invariants ("do not change public API", \
"no new dependencies"). Empty list if none obvious.
"""


@dataclass(frozen=True)
class GoalSuggestion:
    """Suggested LoopGoal parameters from an LLM inference pass."""

    evidence_cmd: str = ""
    success_pattern: str = ""
    max_turns: int = 25
    max_tokens: int = 100_000
    timeout_s: float = 1800.0
    constraints: tuple[str, ...] = field(default_factory=tuple)


def suggest_goal_params(
    end_state: str,
    client: LLMClient,
    *,
    task: str = "",
    temperature: float = 0.2,
) -> GoalSuggestion:
    """Infer LoopGoal parameters from a plain-English stop condition.

    Args:
        end_state:   Human-readable description of what "done" means.
        client:      Any ``LLMClient`` — the same one the agent uses.
        task:        Optional broader task description for extra context.
        temperature: Passed to the client if supported; low = more deterministic.

    Returns:
        A ``GoalSuggestion`` with ready-to-use parameter values. Falls back to
        safe defaults on any parse failure — never raises.
    """
    if not end_state.strip():
        return GoalSuggestion()

    task_line = f'\nThe agent\'s broader task is: "{task.strip()}"' if task.strip() else ""
    prompt = _SUGGEST_PROMPT.format(end_state=end_state.strip(), task_line=task_line)

    try:
        result = client.chat([{"role": "user", "content": prompt}])
        text = result.text
    except Exception:  # noqa: BLE001
        return GoalSuggestion()

    json_match = re.search(r"\{[\s\S]*\}", text)
    if not json_match:
        return GoalSuggestion()

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        return GoalSuggestion()

    raw_constraints = data.get("constraints") or []
    constraints: tuple[str, ...] = tuple(str(c) for c in raw_constraints if c)

    return GoalSuggestion(
        evidence_cmd=str(data.get("evidence_cmd") or ""),
        success_pattern=str(data.get("success_pattern") or ""),
        max_turns=max(1, int(data.get("max_turns") or 25)),
        max_tokens=max(1000, int(data.get("max_tokens") or 100_000)),
        timeout_s=max(10.0, float(data.get("timeout_s") or 1800.0)),
        constraints=constraints,
    )


if __name__ == "__main__":
    # Self-check: GoalSuggestion is frozen, fields have sensible defaults.
    s = GoalSuggestion()
    assert s.max_turns == 25
    assert s.constraints == ()
    try:
        s.max_turns = 99  # type: ignore[misc]
        raise AssertionError("should be frozen")
    except Exception:
        pass
    print("loop.suggest self-check OK")


_CHAIN_PROMPT = """Decompose this task into a LoopChain DAG of named steps.
Task: "{task}"

Return ONLY valid JSON, no prose, no markdown:
{{
  "specs": [
    {{"name": "<snake_case_id>", "description": "<what this step does>", "depends_on": []}},
    {{"name": "<snake_case_id>", "description": "<what this step does>", "depends_on": ["<prior_step_name>"]}}
  ],
  "initial_ctx": {{"task": "{task}"}}
}}

Rules:
- 2-5 specs. Each spec is one focused sub-task.
- depends_on: list names of specs that must finish before this one. Use [] for the first step.
- Names: lowercase, underscores, no spaces (e.g. "research", "synthesize", "write_report").
- Make the DAG linear (pipeline) unless parallel execution genuinely makes sense.
- initial_ctx must contain "task" with the original task string.
"""


@dataclass(frozen=True)
class ChainSpecSuggestion:
    """Suggested LoopChain spec from an LLM decomposition pass."""

    specs: tuple[dict, ...] = field(default_factory=tuple)
    initial_ctx: dict = field(default_factory=dict)


def suggest_chain_spec(
    task: str,
    client: LLMClient,
    *,
    temperature: float = 0.3,
) -> ChainSpecSuggestion:
    """Decompose a task into a LoopChain DAG spec using an LLM.

    Returns a ``ChainSpecSuggestion`` with ``specs`` and ``initial_ctx``.
    Falls back to a minimal single-step spec on any parse failure — never raises.
    """
    if not task.strip():
        return ChainSpecSuggestion(
            specs=({"name": "run", "description": task or "run", "depends_on": []},),
            initial_ctx={"task": task},
        )

    prompt = _CHAIN_PROMPT.format(task=task.strip())

    try:
        result = client.chat([{"role": "user", "content": prompt}])
        text = result.text
    except Exception:  # noqa: BLE001
        return ChainSpecSuggestion(
            specs=({"name": "run", "description": task, "depends_on": []},),
            initial_ctx={"task": task},
        )

    json_match = re.search(r"{[\s\S]*}", text)
    if not json_match:
        return ChainSpecSuggestion(
            specs=({"name": "run", "description": task, "depends_on": []},),
            initial_ctx={"task": task},
        )

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        return ChainSpecSuggestion(
            specs=({"name": "run", "description": task, "depends_on": []},),
            initial_ctx={"task": task},
        )

    raw_specs = data.get("specs") or []
    specs = tuple(
        {
            "name": str(s.get("name") or f"step_{i}"),
            "description": str(s.get("description") or ""),
            "depends_on": [str(d) for d in (s.get("depends_on") or [])],
        }
        for i, s in enumerate(raw_specs)
        if isinstance(s, dict)
    )
    if not specs:
        specs = ({"name": "run", "description": task, "depends_on": []},)

    initial_ctx = data.get("initial_ctx") or {"task": task}

    return ChainSpecSuggestion(specs=specs, initial_ctx=initial_ctx)
