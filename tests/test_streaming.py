"""Tests for P43 — Stream Partial Output (optimize TTFT).

These exercise the OPTIONAL streaming seam added to the LLMClient protocol:
  - agentkit.types.ChatChunk           — frozen partial-output chunk.
  - agentkit.types.supports_streaming  — capability probe (does a client stream?).
  - agentkit.types.stream_chat         — uniform iterator: stream if the client
                                         can, else wrap one .chat() as a single chunk.
  - agentkit.agent.loop.run_agent_stream — consume the stream, yield partials,
                                         still assemble the final AgentResult.

The whole point of P43 is back-compat: an existing NON-streaming LLMClient (only
.chat) must keep working through stream_chat and run_agent_stream unchanged.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from agentkit.agent.loop import AgentResult, run_agent, run_agent_stream
from agentkit.types import (
    ChatChunk,
    ChatResult,
    LLMClient,
    Message,
    stream_chat,
    supports_streaming,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _NonStreamingClient:
    """An existing-style client: only .chat(), no stream_chat. Back-compat case."""

    def __init__(self, text: str = "final answer") -> None:
        self.text = text

    def chat(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> ChatResult:
        return ChatResult(text=self.text, total_tokens=5)


class _StreamingClient:
    """A streaming-capable client: emits partial chunks, then a terminal chunk."""

    def __init__(self, pieces: list[str]) -> None:
        self.pieces = pieces

    def chat(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> ChatResult:
        return ChatResult(text="".join(self.pieces), total_tokens=9)

    def stream_chat(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ):
        for piece in self.pieces:
            yield ChatChunk(text=piece, done=False)
        yield ChatChunk(text="", done=True, total_tokens=9)


# ---------------------------------------------------------------------------
# ChatChunk
# ---------------------------------------------------------------------------

def test_chatchunk_is_frozen():
    chunk = ChatChunk(text="hi", done=False)
    assert chunk.text == "hi"
    assert chunk.done is False
    assert chunk.total_tokens == 0
    assert chunk.tool_calls == ()
    with pytest.raises(FrozenInstanceError):
        chunk.text = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# supports_streaming
# ---------------------------------------------------------------------------

def test_supports_streaming_true_for_streaming_client():
    assert supports_streaming(_StreamingClient(["a", "b"])) is True


def test_supports_streaming_false_for_plain_client():
    assert supports_streaming(_NonStreamingClient()) is False


# ---------------------------------------------------------------------------
# stream_chat helper
# ---------------------------------------------------------------------------

def test_stream_chat_yields_pieces_from_streaming_client():
    client = _StreamingClient(["Hel", "lo ", "wld"])
    chunks = list(stream_chat(client, [{"role": "user", "content": "hi"}]))
    texts = [c.text for c in chunks]
    assert "".join(texts) == "Hello wld"
    assert chunks[-1].done is True
    assert chunks[-1].total_tokens == 9


def test_stream_chat_wraps_nonstreaming_client_as_single_chunk():
    """Back-compat: a plain .chat() client still flows through stream_chat."""
    client = _NonStreamingClient(text="one shot")
    chunks = list(stream_chat(client, [{"role": "user", "content": "hi"}]))
    assert len(chunks) == 1
    assert chunks[0].text == "one shot"
    assert chunks[0].done is True
    assert chunks[0].total_tokens == 5


# ---------------------------------------------------------------------------
# run_agent_stream
# ---------------------------------------------------------------------------

def test_run_agent_stream_yields_partials_then_result():
    client = _StreamingClient(["The ", "answer ", "is 4."])
    events = list(run_agent_stream("q", client=client))

    # All but the last event are partial text chunks; last is the AgentResult.
    partials = events[:-1]
    final = events[-1]
    assert isinstance(final, AgentResult)
    assert "".join(p.text for p in partials if isinstance(p, ChatChunk)) == "The answer is 4."
    assert final.answer == "The answer is 4."
    assert final.success is True
    assert final.stop_reason == "answer"


def test_run_agent_stream_back_compat_nonstreaming_client():
    """A non-streaming client must still produce a correct AgentResult."""
    client = _NonStreamingClient(text="plain result")
    events = list(run_agent_stream("q", client=client))
    final = events[-1]
    assert isinstance(final, AgentResult)
    assert final.answer == "plain result"
    assert final.success is True
    # Streaming path and non-streaming run_agent agree on the answer.
    assert run_agent("q", client=_NonStreamingClient(text="plain result")).answer == final.answer


def test_run_agent_stream_streams_tool_then_answer():
    """A tool round followed by an answer round, both consumed via the stream."""
    class _ToolThenAnswer:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            self.calls += 1
            if self.calls == 1:
                return ChatResult(text="", tool_calls=[("add", {"a": 2, "b": 2})],
                                  total_tokens=10)
            return ChatResult(text="It is 4.", total_tokens=4)

    def _add(args: dict[str, Any]) -> dict[str, Any]:
        return {"sum": args.get("a", 0) + args.get("b", 0)}

    events = list(run_agent_stream("2+2?", client=_ToolThenAnswer(), tools={"add": _add}))
    final = events[-1]
    assert isinstance(final, AgentResult)
    assert final.answer == "It is 4."
    assert final.total_tokens == 14
    assert any(s.role == "tool" and s.tool_result == {"sum": 4} for s in final.trajectory)


# ---------------------------------------------------------------------------
# CliLLMClient streaming seam (single-chunk fallback)
# ---------------------------------------------------------------------------

def test_cli_client_stream_chat_single_chunk(monkeypatch):
    from agentkit.backends.cli import CliLLMClient

    client = CliLLMClient(cmd="echo")

    def _fake_run(argv, capture_output, text, timeout):
        class _P:
            returncode = 0
            stdout = "cli output"
            stderr = ""
        return _P()

    monkeypatch.setattr("agentkit.backends.cli.subprocess.run", _fake_run)
    chunks = list(client.stream_chat([{"role": "user", "content": "hi"}]))
    assert len(chunks) == 1
    assert chunks[0].text == "cli output"
    assert chunks[0].done is True
    # Streaming-capable per the protocol probe.
    assert supports_streaming(client) is True
