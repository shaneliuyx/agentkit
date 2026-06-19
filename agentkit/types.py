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
from typing import Any, Protocol, runtime_checkable

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
    print("types self-check OK")
