"""agentkit.topology.infer — optional LLM front-end: free-text task → TaskSpec.

The rule tree (`core.select_topology`) is pure and needs the §2.7 answers as
structured booleans. Usually the operator supplies them. This optional adapter
spends ONE LLM call to infer them from a plain task description — the same
"LLM is an optional front-end, rules are the core" pattern as the ClaimClassifier
seam. Conservative on any parse failure: returns a spec with no sub-tasks, which
the rule tree resolves to `Single` (never an unjustified fan-out).
"""

from __future__ import annotations

import json

from agentkit.topology.core import TaskSpec
from agentkit.types import LLMClient, Message

_SYSTEM = (
    "Analyse the task for multi-agent planning. Respond with ONLY a JSON object "
    "with these keys: "
    "subtasks (list of short strings — the independent or ordered work units; "
    "[] if a single agent should do it), "
    "single_agent_sufficient, subtasks_independent, needs_subdecomposition, "
    "workers_challenge, cross_session, needs_human_in_loop, needs_recovery, "
    "multiple_entry_points (all booleans). No prose, JSON only."
)
_BOOL_KEYS = (
    "single_agent_sufficient", "subtasks_independent", "needs_subdecomposition",
    "workers_challenge", "cross_session", "needs_human_in_loop",
    "needs_recovery", "multiple_entry_points",
)


def _extract_json(text: str) -> dict:
    """Best-effort: parse the first balanced {...} object. {} on failure."""
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    return {}


def infer_spec(task: str, client: LLMClient) -> TaskSpec:
    """One LLM call → the §2.7 answers → a `TaskSpec`. Conservative on failure."""
    msgs: list[Message] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": task},
    ]
    try:
        data = _extract_json(client.chat(msgs).text or "")
    except Exception:
        data = {}
    subs = data.get("subtasks") or []
    subtasks = tuple(str(s).strip() for s in subs if str(s).strip())
    kwargs = {k: bool(data.get(k, False)) for k in _BOOL_KEYS}
    # Consistency coercion: a model often sets single_agent_sufficient=True while
    # also listing several independent work units — a contradiction that would
    # short-circuit Q1 to Single and ignore the fan-out. Two-plus subtasks means
    # one agent is NOT sufficient; trust the enumerated decomposition.
    if len(subtasks) >= 2:
        kwargs["single_agent_sufficient"] = False
    return TaskSpec(task=task, subtasks=subtasks, **kwargs)
