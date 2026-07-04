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

    def with_options(self, **_kwargs: Any) -> "_StubOpenAI":
        return self


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


# ── deterministic context compaction (compact_messages) ──────────────────────

from studio.client import compact_messages  # noqa: E402


def _tool_loop(rounds: int, page_chars: int, marker_last: str = "") -> list[dict]:
    """A ToolAugmentedClient-shaped conversation: ONE user turn, then
    assistant(tool_calls)/tool alternation — the shape compactor's own
    user-turn cut no-ops on."""
    msgs: list[dict] = [
        {"role": "system", "content": "You are a research spoke."},
        {"role": "user", "content": "ASSIGNMENT: research the Pi agent framework."},
    ]
    for i in range(rounds):
        msgs.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": "web_fetch",
                                         "arguments": f'{{"url": "https://x.test/{i}"}}'}}],
        })
        body = f"page {i} " + ("lorem " * (page_chars // 6))
        if marker_last and i == rounds - 1:
            body += " THE QUOTABLE SENTENCE."
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": body})
    return msgs


def test_compact_small_conversation_is_untouched() -> None:
    msgs = _tool_loop(rounds=2, page_chars=500)
    assert compact_messages(msgs) == msgs


def test_compact_tool_loop_shrinks_and_keeps_assignment_verbatim() -> None:
    msgs = _tool_loop(rounds=20, page_chars=8000)
    out = compact_messages(msgs)
    assert len(out) < len(msgs)
    assert out[0] == msgs[0]          # system verbatim
    assert out[1] == msgs[1]          # assignment verbatim
    assert "[Earlier turns compacted" in out[2]["content"]
    total = lambda ms: sum(len(str(m.get("content") or "")) for m in ms)  # noqa: E731
    assert total(out) < total(msgs) / 2


def test_compact_tail_never_starts_on_orphan_tool_message() -> None:
    """An orphaned role=tool message (no preceding assistant tool_calls) is an
    API 400 — the verbatim tail must start on assistant or user."""
    for rounds in (15, 20, 33, 50):
        out = compact_messages(_tool_loop(rounds=rounds, page_chars=8000))
        bridge_idx = next(
            (i for i, m in enumerate(out)
             if "[Earlier turns compacted" in str(m.get("content") or "")),
            None,
        )
        assert bridge_idx is not None, f"no compaction happened at rounds={rounds}"
        assert out[bridge_idx + 1]["role"] != "tool", f"orphan tool at rounds={rounds}"


def test_compact_keeps_recent_fetched_page_verbatim_for_citation() -> None:
    """The tail budget exists so the model can still QUOTE recent pages."""
    msgs = _tool_loop(rounds=20, page_chars=8000, marker_last="x")
    out = compact_messages(msgs)
    joined = "\n".join(str(m.get("content") or "") for m in out)
    assert "THE QUOTABLE SENTENCE" in joined


def test_compact_is_deterministic() -> None:
    msgs = _tool_loop(rounds=20, page_chars=8000)
    assert compact_messages(msgs) == compact_messages(msgs)


def test_chat_applies_compaction_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """StudioChatClient.chat is the single choke point — oversized message
    lists must reach the endpoint already compacted."""
    seen: list[list[dict]] = []

    class _RecordingCompletions:
        def create(self, **kwargs: Any) -> Any:
            seen.append(kwargs["messages"])
            message = SimpleNamespace(content="ok", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                                   usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))

    class _RecordingOpenAI:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=_RecordingCompletions())

        def with_options(self, **_kw: Any) -> "_RecordingOpenAI":
            return self

    monkeypatch.setattr(client_mod, "make_client", lambda base, key: _RecordingOpenAI())
    c = StudioChatClient("m", base_url=None, api_key=None, on_usage=lambda _u: None)
    msgs = _tool_loop(rounds=20, page_chars=8000)
    c.chat(msgs)
    assert len(seen) == 1
    assert len(seen[0]) < len(msgs)
    assert any("[Earlier turns compacted" in str(m.get("content") or "") for m in seen[0])
