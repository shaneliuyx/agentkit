"""Execute every ```python block in README.md so the usage examples cannot
silently drift from the API.

Drift — a renamed parameter, a changed return shape, a moved symbol — makes the
relevant block raise, and this test fails. The blocks reference a few names
illustratively (``messages``, ``items``, ``eval_set`` …); those are seeded in a
shared namespace so the deterministic, zero-LLM blocks run with no network.

Blocks that reach a real backend construct the shipped ``OpenAIChatClient`` /
``OpenAIEmbedder`` adapters and carry a ``# readme-skip-exec`` marker: every
block is still compiled (syntax-checked), but a marked block is not executed
offline — it is exercised only against a live endpoint.

GateGuard facts: importers — none (test only); public API — exercises the
documented surface of every module; data schema — none persisted (tmp cwd);
instruction — "guard README examples from drift".
"""

from __future__ import annotations

import pathlib
import re

import pytest

README = pathlib.Path(__file__).resolve().parents[1] / "README.md"


def _python_blocks() -> list[str]:
    text = README.read_text(encoding="utf-8")
    return re.findall(r"```python\n(.*?)```", text, re.DOTALL)


def _namespace() -> dict:
    from agentkit.gates import Gate
    from agentkit.loop.goal import LoopGoal, check_goal
    from agentkit.sandbox import SubprocessSandbox

    # Safe goal with no evidence_cmd — check_goal returns met=False immediately.
    _goal = LoopGoal(end_state="test placeholder", max_turns=1)

    msgs = [
        {"role": "user", "content": "hello there, this is a longer message"},
        {"role": "assistant", "content": "hi back, here is some content"},
    ]
    return {
        "__name__": "readme_example",
        # illustrative placeholders referenced but not defined in their block
        "messages": list(msgs),
        "later_messages": list(msgs),
        "long_message_history": msgs * 4,
        "items": ["alpha", "beta"],
        "text": "The sky is blue [1].",
        "eval_set": [("what color?", "blue")],
        "registry": {},
        "my_proposer": (lambda current, history: current + " (improved)"),
        "my_scorer": (lambda proposal: 1.0),
        "my_gate": Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 1.0),
        # LoopGoal / check_goal — used in the goal-check README block
        "LoopGoal": LoopGoal,
        "check_goal": check_goal,
        "goal": _goal,
        # Illustrative runner stubs for LoopChain README examples
        "run_research": lambda ctx: {"findings": "stub"},
        "run_synthesize": lambda ctx: {"report": "stub"},
        "run_deploy": lambda ctx: {},
        "run_build": lambda ctx: {},
        "run_web_research": lambda ctx: {},
        "run_code_research": lambda ctx: {},
        "run_merge": lambda ctx: {},
        # GraphStore stub for Scheduler README examples (temp file — :memory: loses schema across connections)
        "gs": __import__("agentkit.runtime.graph_store", fromlist=["GraphStore"]).GraphStore(__import__("tempfile").mktemp(suffix=".db")),
        # Stub chain registry for scheduler examples
        "registered_chains": {},
    }


_BLOCKS = _python_blocks()


def test_readme_has_blocks():
    # Guard the harness itself: if extraction silently returns nothing, every
    # parametrized case would vacuously pass.
    assert len(_BLOCKS) >= 10, f"expected many python blocks, got {len(_BLOCKS)}"


@pytest.mark.parametrize("idx", range(len(_BLOCKS)))
def test_readme_example_runs(idx, tmp_path, monkeypatch):
    block = _BLOCKS[idx]
    monkeypatch.chdir(tmp_path)

    # The selfimproving example loads/rewrites roles under ./agent_config — seed
    # it so from_config finds on-disk roles and improve() can write back.
    from agentkit.config import dump_role, load_default_roles

    roles_dir = tmp_path / "agent_config" / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)
    for name, role in load_default_roles().items():
        dump_role(role, roles_dir / f"{name.lower()}.json")

    compiled = compile(block, f"<README block {idx}>", "exec")  # syntax-check every block
    if "readme-skip-exec" in block:
        return  # block runs against a live backend; syntax-checked only, not executed offline
    exec(compiled, _namespace())
