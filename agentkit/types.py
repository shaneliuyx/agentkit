"""agentkit.types — Protocol seams for pluggable dependencies.

The design rule: pluggable deps are Protocols here, NEVER concrete vendors.
The lab hardcoded ``openai.OpenAI`` + a local oMLX endpoint; agentkit inverts
that via dependency injection so the same code runs on oMLX, Claude, or a fake.

  - ``Embedder``   — anything that turns texts into vectors (memory tier).
  - ``LLMClient``  — anything that returns a chat completion (agent loop).
  - ``ChatResponse`` — the shape the agent loop reads back from a client.
  - ``ChatResult`` — a concrete dataclass implementing ``ChatResponse``.

Concrete adapters (OpenAI, Claude, oMLX) live OUTSIDE this module — they are
constructed by the caller and injected. agentkit never imports a vendor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, runtime_checkable

# An OpenAI-style chat message: {"role": ..., "content": ..., optional tool_calls/name}.
Message = dict[str, Any]


@runtime_checkable
class Embedder(Protocol):
    """Turns a batch of texts into a batch of float vectors.

    Implementations may call a local model, a remote API, or hash text in a
    test fake. The memory store treats embedding failures as non-fatal, so an
    implementation MAY raise — the store will degrade gracefully.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


@runtime_checkable
class ChatResponse(Protocol):
    """The minimal shape the agent loop reads from a chat completion.

    ``tool_calls`` is a list of ``(tool_name, arguments)`` pairs. An empty list
    means the model produced a final text answer.
    """

    text: str
    tool_calls: list[tuple[str, dict[str, Any]]]
    total_tokens: int


@runtime_checkable
class LLMClient(Protocol):
    """Anything that can take messages (+ optional tool schemas) and reply.

    ``tools`` is an optional list of OpenAI-style tool schema dicts. The return
    value must satisfy ``ChatResponse``.
    """

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        ...


@dataclass
class ChatResult:
    """Concrete ``ChatResponse`` for convenience and for test fakes."""

    text: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    total_tokens: int = 0


# ---------------------------------------------------------------------------
# P43 — Stream Partial Output (optimize TTFT, not just total latency).
#
# An OPTIONAL streaming seam: a client MAY expose ``stream_chat`` to yield
# partial output as it is produced. The seam is back-compatible — clients that
# only implement ``chat`` keep working, and ``stream_chat`` (the helper) wraps
# their single ``.chat()`` result as a one-shot terminal chunk so the consuming
# side (``run_agent_stream``) sees a uniform iterator regardless of capability.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChatChunk:
    """One partial piece of a streamed chat completion.

    ``done`` marks the terminal chunk of a single ``stream_chat`` turn; the
    final chunk carries the turn's ``total_tokens`` (and any structured
    ``tool_calls`` recovered for the turn). Intermediate chunks carry text only.
    """

    text: str = ""
    done: bool = False
    total_tokens: int = 0
    tool_calls: tuple[tuple[str, dict[str, Any]], ...] = ()


@runtime_checkable
class StreamingLLMClient(Protocol):
    """An ``LLMClient`` that ALSO streams partial output via ``stream_chat``.

    Optional refinement of ``LLMClient``: an implementer keeps ``chat`` (for
    callers that want the assembled result) and adds ``stream_chat`` yielding
    ``ChatChunk`` objects, the last with ``done=True``.
    """

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        ...

    def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[ChatChunk]:
        ...


def supports_streaming(client: Any) -> bool:
    """True iff ``client`` exposes a callable ``stream_chat`` (P43 capability)."""
    return callable(getattr(client, "stream_chat", None))


def stream_chat(
    client: Any,
    messages: list[Message],
    tools: list[dict[str, Any]] | None = None,
) -> Iterator[ChatChunk]:
    """Uniform streaming view over any ``LLMClient``.

    If the client streams (``supports_streaming``), delegate to its
    ``stream_chat``. Otherwise call ``.chat()`` once and wrap the assembled
    ``ChatResponse`` as a single terminal ``ChatChunk`` — so the consuming side
    never has to branch on client capability (back-compat with non-streaming
    clients is the whole point of the seam).
    """
    if supports_streaming(client):
        yield from client.stream_chat(messages, tools=tools)
        return
    result = client.chat(messages, tools=tools)
    yield ChatChunk(
        text=getattr(result, "text", "") or "",
        done=True,
        total_tokens=getattr(result, "total_tokens", 0) or 0,
        tool_calls=tuple(getattr(result, "tool_calls", ()) or ()),
    )


if __name__ == "__main__":
    # Runnable self-check: ChatResult satisfies the ChatResponse protocol, and
    # the protocols are runtime-checkable.
    r = ChatResult(text="hi", tool_calls=[("calc", {"x": 1})], total_tokens=7)
    assert isinstance(r, ChatResponse), "ChatResult must satisfy ChatResponse"
    assert r.text == "hi"
    assert r.tool_calls == [("calc", {"x": 1})]
    assert r.total_tokens == 7

    class _FakeEmbedder:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[float(len(t))] for t in texts]

    assert isinstance(_FakeEmbedder(), Embedder)
    assert _FakeEmbedder().embed(["ab", "cde"]) == [[2.0], [3.0]]

    # P43 streaming seam: non-streaming client wraps as a single terminal chunk.
    class _Plain:
        def chat(self, messages, tools=None):
            return ChatResult(text="done", total_tokens=3)

    assert supports_streaming(_Plain()) is False
    chunks = list(stream_chat(_Plain(), [{"role": "user", "content": "hi"}]))
    assert len(chunks) == 1 and chunks[0].text == "done" and chunks[0].done
    assert chunks[0].total_tokens == 3

    # A streaming client delegates straight through and is detected.
    class _Streamer:
        def chat(self, messages, tools=None):
            return ChatResult(text="ab")

        def stream_chat(self, messages, tools=None):
            yield ChatChunk(text="a")
            yield ChatChunk(text="b", done=True, total_tokens=2)

    assert supports_streaming(_Streamer()) is True
    assert isinstance(_Streamer(), StreamingLLMClient)
    sc = list(stream_chat(_Streamer(), [{"role": "user", "content": "hi"}]))
    assert "".join(c.text for c in sc) == "ab" and sc[-1].done and sc[-1].total_tokens == 2

    frozen = ChatChunk(text="x")
    try:
        frozen.text = "y"  # type: ignore[misc]
    except Exception:
        pass
    else:  # pragma: no cover - frozen dataclass must reject mutation
        raise AssertionError("ChatChunk must be frozen")

    print("types self-check OK")
