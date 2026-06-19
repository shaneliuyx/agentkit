"""Tests for agentkit.agent.roles — role specialization (no network, no LLM)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from agentkit.agent.loop import AgentResult
from agentkit.agent.roles import (
    DEFAULT_ROLES,
    REVIEWER,
    RESEARCHER,
    VERIFIER,
    WRITER,
    AgentRole,
    dispatch,
    run_role,
)
from agentkit.types import ChatResult, Message


class _CapturingClient:
    """A fake LLMClient that records the system prompt and returns a fixed answer."""

    def __init__(self) -> None:
        self.system_seen = ""

    def chat(self, messages: list[Message],
             tools: list[dict[str, Any]] | None = None) -> ChatResult:
        for m in messages:
            if m.get("role") == "system":
                self.system_seen = m.get("content", "")
        return ChatResult(text="final answer", total_tokens=5)


@pytest.mark.unit
def test_dispatch_routes_review_to_reviewer():
    assert dispatch("Please review and audit this design").name == "Reviewer"
    assert dispatch("Critique my approach").name == "Reviewer"


@pytest.mark.unit
def test_dispatch_routes_draft_to_writer():
    assert dispatch("Draft a summary").name == "Writer"
    assert dispatch("Write an article").name == "Writer"
    assert dispatch("Compose a report").name == "Writer"


@pytest.mark.unit
def test_dispatch_routes_verify_to_verifier():
    assert dispatch("Verify these claims").name == "Verifier"
    assert dispatch("Check the citation list").name == "Verifier"
    assert dispatch("Please check links in the doc").name == "Verifier"


@pytest.mark.unit
def test_dispatch_defaults_to_researcher():
    assert dispatch("Find the latest papers on RAG").name == "Researcher"
    assert dispatch("Gather evidence about agents").name == "Researcher"


@pytest.mark.unit
def test_dispatch_uses_injected_classifier():
    chosen = dispatch("anything at all", classifier=lambda t, rs: WRITER)
    assert chosen.name == "Writer"


@pytest.mark.unit
def test_all_four_presets_present():
    names = {r.name for r in DEFAULT_ROLES}
    assert names == {"Researcher", "Reviewer", "Writer", "Verifier"}
    assert len(DEFAULT_ROLES) == 4


@pytest.mark.unit
def test_presets_are_frozen():
    for role in (RESEARCHER, REVIEWER, WRITER, VERIFIER):
        with pytest.raises(FrozenInstanceError):
            role.name = "mutated"  # type: ignore[misc]


@pytest.mark.unit
def test_role_default_difficulty_is_valid_router_label():
    valid = {"trivial", "easy", "medium", "hard", "critical"}
    for role in DEFAULT_ROLES:
        assert role.difficulty in valid


@pytest.mark.unit
def test_run_role_builds_prompt_with_role_system_prompt():
    client = _CapturingClient()
    result = run_role(RESEARCHER, "Find sources on X", client=client)
    assert isinstance(result, AgentResult)
    assert result.answer == "final answer"
    assert RESEARCHER.system_prompt in client.system_seen


@pytest.mark.unit
def test_run_role_returns_agent_result_for_each_role():
    for role in DEFAULT_ROLES:
        client = _CapturingClient()
        result = run_role(role, "do the task", client=client)
        assert isinstance(result, AgentResult)
        assert role.system_prompt in client.system_seen


@pytest.mark.unit
def test_agent_role_is_constructible_and_frozen():
    r = AgentRole(name="Custom", system_prompt="be custom")
    assert r.tools == ()
    assert r.difficulty == "medium"
    assert r.output_schema is None
    with pytest.raises(FrozenInstanceError):
        r.system_prompt = "x"  # type: ignore[misc]
