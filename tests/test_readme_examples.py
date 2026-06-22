"""Execute every ```python block in README.md so the usage examples cannot
silently drift from the API.

Drift — a renamed parameter, a changed return shape, a moved symbol — makes the
relevant block raise, and this test fails. The blocks reference a few names
illustratively (``messages``, ``items``, ``eval_set`` …); those are seeded in a
shared namespace, alongside the ``MyClient`` / ``MyEmbedder`` fakes the README's
Quickstart defines, so the literal documentation runs with no network.

GateGuard facts: importers — none (test only); public API — exercises the
documented surface of every module; data schema — none persisted (tmp cwd);
instruction — "guard README examples from drift".
"""

from __future__ import annotations

import pathlib
import re

import pytest

from agentkit.types import ChatResult

README = pathlib.Path(__file__).resolve().parents[1] / "README.md"

# A reply that is harmless as a generic agent answer AND parses as a valid
# codegen proposal (SCHEMA: marker + a fenced read-only python tool), so the
# `codegen` / `forge_tool` examples reach ACCEPT instead of erroring.
_FORGE_REPLY = (
    'SCHEMA: {"name": "f", "description": "demo", "parameters": {}}\n'
    "```python\n"
    "def f():\n"
    "    return 1\n"
    "```"
)


class _Embedder:
    def embed(self, texts):
        return [[float(len(t)), 1.0] for t in texts]


class _Client:
    def chat(self, messages, tools=None):
        return ChatResult(text=_FORGE_REPLY)


def _python_blocks() -> list[str]:
    text = README.read_text(encoding="utf-8")
    return re.findall(r"```python\n(.*?)```", text, re.DOTALL)


def _namespace() -> dict:
    from agentkit.gates import Gate
    from agentkit.sandbox import SubprocessSandbox

    msgs = [
        {"role": "user", "content": "hello there, this is a longer message"},
        {"role": "assistant", "content": "hi back, here is some content"},
    ]
    return {
        "__name__": "readme_example",
        "MyEmbedder": _Embedder,
        "MyClient": _Client,
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

    exec(compile(block, f"<README block {idx}>", "exec"), _namespace())
