"""Tests for StudioChatClient usage capture (client.py).

These use a stubbed openai client object (no network) by monkeypatching the
``make_client`` the StudioChatClient calls, so we exercise the usage-split
capture + the estimated flag without an endpoint.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import studio.client as client_mod
from agentkit.types import LLMClient
from studio.client import StudioChatClient
from studio.shared_bridge import UsageReport


class _StubCompletions:
    def __init__(self, usage: Any) -> None:
        self._usage = usage

    def create(self, **kwargs: Any) -> Any:
        message = SimpleNamespace(content="hello", tool_calls=None)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice], usage=self._usage)


class _StubOpenAI:
    def __init__(self, usage: Any) -> None:
        self.chat = SimpleNamespace(completions=_StubCompletions(usage))


def _patch_client(monkeypatch: pytest.MonkeyPatch, usage: Any) -> None:
    monkeypatch.setattr(client_mod, "make_client", lambda base, key: _StubOpenAI(usage))


def test_satisfies_llmclient(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    c = StudioChatClient("m", base_url=None, api_key=None, on_usage=lambda _u: None)
    assert isinstance(c, LLMClient)


def test_captures_in_out_split(monkeypatch: pytest.MonkeyPatch) -> None:
    """prompt/completion tokens are reported exactly, estimated=False."""
    _patch_client(monkeypatch, SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10))
    captured: list[UsageReport] = []
    c = StudioChatClient("m", base_url=None, api_key=None, on_usage=captured.append)
    res = c.chat([{"role": "user", "content": "hi"}])
    assert res.text == "hello"
    assert res.total_tokens == 10
    assert len(captured) == 1
    assert captured[0].input_tokens == 7
    assert captured[0].output_tokens == 3
    assert captured[0].estimated is False


def test_estimated_when_usage_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No usage object → estimated=True (the sticky-~ trigger, SPEC §7)."""
    _patch_client(monkeypatch, None)
    captured: list[UsageReport] = []
    c = StudioChatClient("m", base_url=None, api_key=None, on_usage=captured.append)
    c.chat([{"role": "user", "content": "hi"}])
    assert captured[0].estimated is True
    assert captured[0].input_tokens == 0
    assert captured[0].output_tokens == 0
