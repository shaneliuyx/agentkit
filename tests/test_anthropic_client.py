"""Offline tests for the native Claude adapter (``AnthropicChatClient``).

No network: a fake ``anthropic.Anthropic`` is injected so we exercise the native
mapping (content blocks → text + tool_calls; input+output tokens → total_tokens),
system-message splitting, and the clear missing-dependency error.

GateGuard facts: importers — none (test only); public API — exercises
``AnthropicChatClient`` (.chat, system split, missing-dep hint); data schema —
ChatResult(text, total_tokens, tool_calls); instruction — native Claude adapter
for the LLMClient seam.
"""

from __future__ import annotations

import sys

import pytest

from agentkit.backends import anthropic_client
from agentkit.types import LLMClient


# ── fakes imitating the slice of the anthropic SDK the adapter touches ────────
class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeToolUseBlock:
    def __init__(self, name: str, inp: dict) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = inp


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, content, input_tokens, output_tokens) -> None:
        self.content = content
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _FakeMessagesNS:
    def __init__(self, response) -> None:
        self._response = response
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response) -> None:
        self.messages = _FakeMessagesNS(response)


@pytest.fixture
def patch_make_client(monkeypatch):
    """Replace ``_make_anthropic_client`` so the adapter wraps a fake."""

    def _install(fake: _FakeAnthropicClient):
        monkeypatch.setattr(
            anthropic_client, "_make_anthropic_client", lambda *a, **k: fake
        )
        # Errors tuple must not require the real SDK either.
        monkeypatch.setattr(
            anthropic_client, "_anthropic_errors", lambda: (RuntimeError,)
        )
        return fake

    return _install


def test_satisfies_llmclient_protocol(patch_make_client):
    patch_make_client(_FakeAnthropicClient(_FakeResponse([_FakeTextBlock("hi")], 1, 1)))
    client = anthropic_client.AnthropicChatClient(model="claude-sonnet-4-5")
    assert isinstance(client, LLMClient)


def test_chat_maps_text_blocks_and_token_usage(patch_make_client):
    response = _FakeResponse(
        content=[_FakeTextBlock("The answer "), _FakeTextBlock("is 4.")],
        input_tokens=30,
        output_tokens=12,
    )
    fake = patch_make_client(_FakeAnthropicClient(response))
    client = anthropic_client.AnthropicChatClient(model="claude-sonnet-4-5")

    result = client.chat([{"role": "user", "content": "2+2?"}])

    assert result.text == "The answer is 4."  # text blocks concatenated + stripped
    assert result.total_tokens == 42  # input_tokens + output_tokens
    assert result.tool_calls == []
    assert client.n_calls == 1
    assert client.total_tokens == 42
    # max_tokens is REQUIRED by the Messages API — default 1024 forwarded.
    assert fake.messages.last_kwargs["max_tokens"] == 1024
    # no system message → no system kwarg
    assert "system" not in fake.messages.last_kwargs


def test_system_message_is_split_out(patch_make_client):
    fake = patch_make_client(
        _FakeAnthropicClient(_FakeResponse([_FakeTextBlock("ok")], 5, 5))
    )
    client = anthropic_client.AnthropicChatClient(model="claude-sonnet-4-5")

    client.chat(
        [
            {"role": "system", "content": "be terse"},
            {"role": "system", "content": "use bullet points"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
    )

    kwargs = fake.messages.last_kwargs
    assert kwargs["system"] == "be terse\n\nuse bullet points"  # joined
    # system messages removed from the messages list; user/assistant preserved
    assert kwargs["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


def test_tool_use_blocks_mapped_and_tools_forwarded(patch_make_client):
    response = _FakeResponse(
        content=[
            _FakeTextBlock("calling tool"),
            _FakeToolUseBlock("add", {"a": 1, "b": 2}),
        ],
        input_tokens=8,
        output_tokens=4,
    )
    fake = patch_make_client(_FakeAnthropicClient(response))
    client = anthropic_client.AnthropicChatClient(model="claude-sonnet-4-5")

    tool_schema = [{"name": "add", "input_schema": {}}]
    result = client.chat([{"role": "user", "content": "add"}], tools=tool_schema)

    assert fake.messages.last_kwargs["tools"] == tool_schema  # forwarded
    assert result.tool_calls == [("add", {"a": 1, "b": 2})]
    assert result.text == "calling tool"
    assert result.total_tokens == 12


def test_max_tokens_overridable(patch_make_client):
    fake = patch_make_client(
        _FakeAnthropicClient(_FakeResponse([_FakeTextBlock("ok")], 1, 1))
    )
    client = anthropic_client.AnthropicChatClient(
        model="claude-sonnet-4-5", max_tokens=256
    )
    client.chat([{"role": "user", "content": "hi"}])
    assert fake.messages.last_kwargs["max_tokens"] == 256


def test_clear_error_when_anthropic_absent(monkeypatch):
    """Constructing without ``anthropic`` raises a clear install hint.

    ``anthropic`` is installed here, so we simulate absence by blocking the
    import (``sys.modules["anthropic"] = None`` makes ``import anthropic`` raise).
    """
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(ImportError, match=r"pip install agentkit\[anthropic\]"):
        anthropic_client.AnthropicChatClient(model="claude-sonnet-4-5", api_key="x")


def test_module_import_does_not_require_anthropic():
    """The adapter references anthropic only inside functions — importing the
    module (done at top of file) never hard-fails without anthropic installed."""
    assert hasattr(anthropic_client, "AnthropicChatClient")
