"""Offline tests for the standard OpenAI-compatible adapter.

No network: a fake ``openai.OpenAI`` is injected so we exercise the mapping
(completion → ``ChatResult``; embeddings → ``list[list[float]]``) and the
Protocol conformance without a live endpoint.

GateGuard facts: importers — none (test only); public API — exercises
``OpenAIChatClient`` / ``OpenAIEmbedder`` / ``make_client``; data schema —
asserts ChatResult(text, total_tokens, tool_calls) + list[list[float]];
instruction — "ship agentkit standard OpenAI-compatible adapter".
"""

from __future__ import annotations

import sys

import pytest

from agentkit.backends import openai_compat
from agentkit.types import Embedder, LLMClient


# ── fakes that imitate the small slice of the openai SDK the adapter touches ──
class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeChoice:
    def __init__(self, message) -> None:
        self.message = message


class _FakeUsage:
    def __init__(self, total_tokens) -> None:
        self.total_tokens = total_tokens


class _FakeCompletion:
    def __init__(self, content, total_tokens, tool_calls=None) -> None:
        self.choices = [_FakeChoice(_FakeMessage(content, tool_calls))]
        self.usage = _FakeUsage(total_tokens)


class _FakeEmbeddingItem:
    def __init__(self, embedding) -> None:
        self.embedding = embedding


class _FakeEmbeddingResponse:
    def __init__(self, vectors) -> None:
        self.data = [_FakeEmbeddingItem(v) for v in vectors]


class _FakeChatNS:
    def __init__(self, completion) -> None:
        self.completions = self
        self._completion = completion
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._completion


class _FakeEmbeddingsNS:
    def __init__(self, vectors) -> None:
        self._vectors = vectors
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeEmbeddingResponse(self._vectors)


class _FakeOpenAIClient:
    """Stand-in for ``openai.OpenAI`` with just ``.chat`` and ``.embeddings``."""

    def __init__(self, *, completion=None, vectors=None) -> None:
        self.chat = _FakeChatNS(completion)
        self.embeddings = _FakeEmbeddingsNS(vectors or [])


@pytest.fixture
def patch_make_client(monkeypatch):
    """Replace ``make_client`` so adapters wrap a fake instead of a real client."""

    def _install(fake: _FakeOpenAIClient):
        monkeypatch.setattr(
            openai_compat, "make_client", lambda *a, **k: fake
        )
        return fake

    return _install


def test_chat_client_satisfies_llmclient_protocol(patch_make_client):
    patch_make_client(_FakeOpenAIClient(completion=_FakeCompletion("hi", 0)))
    client = openai_compat.OpenAIChatClient(model="m")
    assert isinstance(client, LLMClient)


def test_chat_maps_completion_to_chatresult(patch_make_client):
    fake = patch_make_client(
        _FakeOpenAIClient(completion=_FakeCompletion("  The answer is 4.  ", 42))
    )
    client = openai_compat.OpenAIChatClient(model="m")

    result = client.chat([{"role": "user", "content": "2+2?"}])

    assert result.text == "The answer is 4."  # content stripped
    assert result.total_tokens == 42
    assert result.tool_calls == []
    # accounting bookkeeping mirrors the lab adapter
    assert client.n_calls == 1
    assert client.total_tokens == 42
    # tools omitted when not provided
    assert "tools" not in fake.chat.last_kwargs


def test_chat_forwards_tools_and_parses_native_tool_calls(patch_make_client):
    completion = _FakeCompletion(
        content="",
        total_tokens=7,
        tool_calls=[_FakeToolCall("add", '{"a": 1, "b": 2}')],
    )
    fake = patch_make_client(_FakeOpenAIClient(completion=completion))
    client = openai_compat.OpenAIChatClient(model="m")

    tool_schema = [{"type": "function", "function": {"name": "add"}}]
    result = client.chat([{"role": "user", "content": "add"}], tools=tool_schema)

    assert fake.chat.last_kwargs["tools"] == tool_schema  # forwarded
    assert result.tool_calls == [("add", {"a": 1, "b": 2})]
    assert result.total_tokens == 7


def test_embedder_satisfies_protocol_and_maps_vectors(patch_make_client):
    fake = patch_make_client(
        _FakeOpenAIClient(vectors=[[0.1, 0.2], [0.3, 0.4, 0.5]])
    )
    embedder = openai_compat.OpenAIEmbedder(model="bge")
    assert isinstance(embedder, Embedder)

    vecs = embedder.embed(["a", "bb"])

    assert vecs == [[0.1, 0.2], [0.3, 0.4, 0.5]]
    assert all(isinstance(v, list) for v in vecs)
    assert fake.embeddings.last_kwargs["input"] == ["a", "bb"]


def test_embedder_empty_input_short_circuits(patch_make_client):
    fake = patch_make_client(_FakeOpenAIClient(vectors=[]))
    embedder = openai_compat.OpenAIEmbedder(model="bge")
    assert embedder.embed([]) == []
    assert fake.embeddings.last_kwargs is None  # no network call for empty batch


def test_env_chain_defaults(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OMLX_BASE_URL", raising=False)
    assert openai_compat._default_base() == "http://localhost:8000/v1"
    monkeypatch.setenv("OMLX_BASE_URL", "http://localhost:9001/v1")
    assert openai_compat._default_base() == "http://localhost:9001/v1"
    monkeypatch.setenv("LLM_BASE_URL", "http://example/v1")
    assert openai_compat._default_base() == "http://example/v1"  # LLM_BASE_URL wins

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OMLX_API_KEY", raising=False)
    assert openai_compat._default_key() == "EMPTY"  # non-empty sentinel


def test_clear_error_when_openai_absent(monkeypatch):
    """Constructing without ``openai`` raises a clear install hint, not opaque noise.

    ``openai`` is installed in this env, so we simulate its absence by blocking
    the import (``sys.modules["openai"] = None`` makes ``import openai`` raise
    ImportError). The lazy seam must turn that into the install-hint message.
    """
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ImportError, match=r"pip install agentkit\[openai\]"):
        openai_compat.make_client()


def test_module_import_does_not_require_openai():
    """The adapter module references openai only inside functions, never at module
    scope — so importing it (done at top of file) never hard-fails without openai."""
    assert hasattr(openai_compat, "OpenAIChatClient")
    assert hasattr(openai_compat, "OpenAIEmbedder")
    assert hasattr(openai_compat, "make_client")
