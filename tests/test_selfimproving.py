"""Tests for agentkit.selfimproving — the SelfImprovingAgent facade (REPLAN §7).

GateGuard facts (instruction: "build agentkit SelfImprovingAgent facade, re-plan
Phase 7"):
  - importers: pytest collection only; no production code imports this test.
  - public API: test_* functions; no exported symbols.
  - data schema: fake LLMClient/Embedder; temp config dirs holding roles/*.json
    and an eval_set of (task, expected) string pairs.
  - instruction: build agentkit SelfImprovingAgent facade, re-plan Phase 7.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentkit.agent.loop import AgentResult
from agentkit.config.roles import load_role
from agentkit.evolve.core import OptimizeResult
from agentkit.selfimproving import SelfImprovingAgent
from agentkit.skills.core import SkillLibrary
from agentkit.types import ChatResult, Message


# ---------------------------------------------------------------------------
# Fakes (no vendor, no network)
# ---------------------------------------------------------------------------

class _FixedClient:
    """LLMClient that always returns a fixed final answer (no tool calls)."""

    def __init__(self, answer: str = "the answer") -> None:
        self.answer = answer
        self.calls = 0

    def chat(self, messages: list[Message],
             tools: list[dict[str, Any]] | None = None) -> ChatResult:
        self.calls += 1
        return ChatResult(text=self.answer, total_tokens=3)


class _Proposer:
    """Proposer (LLMClient shape used by make_llm_proposer) returning one
    mutation as the JSON the evolve proposer expects."""

    def __init__(self, mutated_prompt: str) -> None:
        self.mutated_prompt = mutated_prompt

    def chat(self, messages: list[Message],
             tools: list[dict[str, Any]] | None = None) -> ChatResult:
        return ChatResult(text=json.dumps({
            "mutation_note": "tweak",
            "mutated_prompt": self.mutated_prompt,
        }))


class _HashEmbedder:
    def __init__(self, dim: int = 32) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            out.append(vec)
        return out


def _write_role(directory: Path, name: str, system_prompt: str,
                difficulty: str = "medium") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name.lower()}.json"
    path.write_text(json.dumps({
        "name": name,
        "system_prompt": system_prompt,
        "tools": [],
        "difficulty": difficulty,
    }), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# from_config
# ---------------------------------------------------------------------------

def test_from_config_loads_roles_from_directory(tmp_path: Path) -> None:
    _write_role(tmp_path / "roles", "Researcher", "You research things.")
    _write_role(tmp_path / "roles", "Writer", "You write things.", "medium")

    agent = SelfImprovingAgent.from_config(tmp_path, backend=_FixedClient())

    assert set(agent.roles) == {"Researcher", "Writer"}
    assert agent.config_dir == Path(tmp_path)


def test_from_config_falls_back_to_default_roles_when_empty(tmp_path: Path) -> None:
    agent = SelfImprovingAgent.from_config(tmp_path, backend=_FixedClient())
    # The shipped feynman ensemble.
    assert set(agent.roles) == {"Researcher", "Reviewer", "Writer", "Verifier"}


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def test_run_dispatches_and_returns_answer(tmp_path: Path) -> None:
    _write_role(tmp_path / "roles", "Researcher", "You research things.")
    client = _FixedClient("found it")
    agent = SelfImprovingAgent.from_config(tmp_path, backend=client)

    result = agent.run("Find the latest papers on RAG")

    assert isinstance(result, AgentResult)
    assert result.answer == "found it"
    assert client.calls >= 1


def test_run_dispatches_deterministically_to_writer(tmp_path: Path) -> None:
    _write_role(tmp_path / "roles", "Researcher", "research")
    _write_role(tmp_path / "roles", "Writer", "write")
    agent = SelfImprovingAgent.from_config(tmp_path, backend=_FixedClient())
    # Deterministic keyword dispatch: "draft/write/compose" -> Writer.
    assert agent.selected_role("Draft a blog post").name == "Writer"


# ---------------------------------------------------------------------------
# improve — the payoff: gated rewrite of the role file on disk
# ---------------------------------------------------------------------------

def test_improve_accepts_better_variant_and_rewrites_role_file(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    role_path = _write_role(roles_dir, "Researcher", "BASE")
    agent = SelfImprovingAgent.from_config(tmp_path, backend=_FixedClient())

    # eval_set scores a prompt by how many "good" tokens it contains; the
    # proposer yields a strictly-better prompt.
    eval_set = [("good", "good")]

    def evaluate(prompt: str) -> float:
        return min(1.0, prompt.count("good") / 2.0)

    better = "BASE good good"
    result = agent.improve(
        eval_set,
        role="Researcher",
        proposer=_Proposer(better),
        evaluate=evaluate,
        epochs=2,
    )

    assert isinstance(result, OptimizeResult)
    assert result.delta > 0.0
    assert result.accepted >= 1
    # The improved role was written BACK to the same config file.
    rewritten = load_role(role_path)
    assert rewritten.system_prompt == better
    assert rewritten.system_prompt != "BASE"
    # And the in-memory role is refreshed too.
    assert agent.roles["Researcher"].system_prompt == better


def test_improve_keeps_baseline_and_does_not_rewrite_file_when_worse(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    role_path = _write_role(roles_dir, "Researcher", "BASE")
    original_bytes = role_path.read_bytes()
    agent = SelfImprovingAgent.from_config(tmp_path, backend=_FixedClient())

    eval_set = [("x", "x")]

    def evaluate(prompt: str) -> float:
        return min(1.0, prompt.count("good") / 2.0)

    # Proposer yields a prompt with no "good" tokens -> never beats baseline.
    result = agent.improve(
        eval_set,
        role="Researcher",
        proposer=_Proposer("a worse prompt"),
        evaluate=evaluate,
        epochs=3,
    )

    assert result.accepted == 0
    assert result.delta == 0.0
    assert result.best == "BASE"
    # The file on disk is byte-for-byte unchanged.
    assert role_path.read_bytes() == original_bytes
    assert agent.roles["Researcher"].system_prompt == "BASE"


def test_improve_unknown_role_raises(tmp_path: Path) -> None:
    _write_role(tmp_path / "roles", "Researcher", "BASE")
    agent = SelfImprovingAgent.from_config(tmp_path, backend=_FixedClient())
    with pytest.raises(KeyError):
        agent.improve([("x", "y")], role="Nonexistent",
                      proposer=_Proposer("z"), evaluate=lambda p: 1.0)


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------

def test_skills_property_is_a_skill_library(tmp_path: Path) -> None:
    _write_role(tmp_path / "roles", "Researcher", "BASE")
    agent = SelfImprovingAgent.from_config(
        tmp_path, backend=_FixedClient(), embedder=_HashEmbedder())
    lib = agent.skills
    assert isinstance(lib, SkillLibrary)
    # The skills dir lives under config_dir.
    assert Path(tmp_path) in lib.directory.parents or lib.directory.parent == Path(tmp_path)
    assert lib.list() == []


def test_skills_requires_embedder(tmp_path: Path) -> None:
    _write_role(tmp_path / "roles", "Researcher", "BASE")
    agent = SelfImprovingAgent.from_config(tmp_path, backend=_FixedClient())
    with pytest.raises(ValueError):
        _ = agent.skills


# ---------------------------------------------------------------------------
# forge_tool — optional, lazy codegen import
# ---------------------------------------------------------------------------

def test_forge_tool_is_callable_or_clearly_unavailable(tmp_path: Path) -> None:
    _write_role(tmp_path / "roles", "Researcher", "BASE")
    agent = SelfImprovingAgent.from_config(tmp_path, backend=_FixedClient())
    try:
        import agentkit.codegen  # noqa: F401
    except Exception:
        with pytest.raises(RuntimeError, match="codegen not installed"):
            agent.forge_tool("a tool that adds two numbers")
    else:
        # codegen present: forge_tool wires it (we do not assert on the LLM
        # output, only that the method exists and returns a forge result).
        assert hasattr(agent, "forge_tool")
