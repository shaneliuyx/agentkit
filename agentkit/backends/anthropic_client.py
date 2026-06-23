"""agentkit.backends.anthropic_client — a NATIVE Claude adapter.

``AnthropicChatClient`` satisfies the same ``agentkit.types.LLMClient`` Protocol
as ``OpenAIChatClient`` — that IS the point of the seam: multiple vendor
adapters behind one ``.chat(messages, tools=None) -> ChatResult`` interface, so
``run_agent`` / ``MemoryStore`` / ``SelfImprovingAgent`` take any of them
unchanged.

Unlike the OpenAI-compatible path, this uses the native ``anthropic`` SDK shape:
  - Anthropic separates the system prompt from the message list, so any
    ``role == "system"`` messages are pulled out and passed as ``system=``.
  - ``max_tokens`` is REQUIRED by the Messages API (default 1024, overridable).
  - The response is a list of content blocks; text is the concatenation of the
    ``type == "text"`` blocks, and ``type == "tool_use"`` blocks map to
    agentkit's ``(name, arguments)`` tool-call shape.
  - Token usage is ``usage.input_tokens + usage.output_tokens``.

``anthropic`` is an OPTIONAL extra (``pip install agentkit[anthropic]``),
imported LAZILY so ``import agentkit`` never hard-fails without it; the clear
install hint surfaces only on construction.
"""

from __future__ import annotations

import os
from typing import Any

from agentkit.backends.openai_compat import LLMUnavailable, _resilient_with
from agentkit.types import ChatResult, Message

_INSTALL_HINT = (
    "the native Claude adapter needs the 'anthropic' package — "
    "install it with: pip install agentkit[anthropic]"
)

_DEFAULT_MAX_TOKENS = 1024


def _make_anthropic_client(api_key: str | None, base_url: str | None) -> Any:
    """An ``anthropic.Anthropic`` client; lazy-imported so a missing optional
    dependency surfaces here as a clear install hint, not an opaque error."""
    try:
        from anthropic import Anthropic
    except ImportError as exc:  # pragma: no cover - exercised only without anthropic
        raise ImportError(_INSTALL_HINT) from exc
    kwargs: dict[str, Any] = {}
    # Anthropic reads ANTHROPIC_API_KEY from env when api_key is None; pass an
    # explicit key only when given (or via the env chain) to keep it overridable.
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if key:
        kwargs["api_key"] = key
    if base_url:
        kwargs["base_url"] = base_url
    return Anthropic(**kwargs)


def _anthropic_errors() -> tuple[type[Exception], ...]:
    """The transient anthropic error types to retry through (lazy-imported)."""
    try:
        from anthropic import APIConnectionError, APIError
    except ImportError as exc:  # pragma: no cover - guarded by construction
        raise ImportError(_INSTALL_HINT) from exc
    return (APIConnectionError, APIError)


def _split_system(messages: list[Message]) -> tuple[str | None, list[Message]]:
    """Pull ``role == "system"`` messages into a single ``system=`` string and
    return the remaining user/assistant messages for the Anthropic messages list."""
    system_parts: list[str] = []
    convo: list[Message] = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content")
            if isinstance(content, str) and content:
                system_parts.append(content)
        else:
            convo.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
    system = "\n\n".join(system_parts) if system_parts else None
    return system, convo


def _extract_text_and_tools(content: Any) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    """Map Anthropic content blocks → (text, [(tool_name, arguments), ...])."""
    texts: list[str] = []
    tool_calls: list[tuple[str, dict[str, Any]]] = []
    for block in content or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            texts.append(getattr(block, "text", "") or "")
        elif btype == "tool_use":
            name = getattr(block, "name", "") or ""
            args = getattr(block, "input", None) or {}
            if name:
                tool_calls.append((name, dict(args) if isinstance(args, dict) else {}))
    return "".join(texts).strip(), tool_calls


class AnthropicChatClient:
    """NATIVE Claude ``LLMClient`` over the anthropic Messages API.

    Construct once and pass anywhere an ``LLMClient`` is expected. ``api_key``
    falls back to ``ANTHROPIC_API_KEY``; ``base_url`` lets you point at a proxy.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float = 0.0,
        retries: int = 4,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.retries = retries
        self._client = _make_anthropic_client(api_key, base_url)
        self.n_calls = 0
        self.total_tokens = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """One completion → ``ChatResult(text, total_tokens, tool_calls)``.

        Splits out system messages, forwards ``tools`` when provided, retries
        through transient drops, and raises ``LLMUnavailable`` after ``retries``.
        """
        system, convo = _split_system(messages)

        def _call() -> ChatResult:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": convo,
                "temperature": self.temperature,
            }
            if system is not None:
                kwargs["system"] = system
            if tools:
                kwargs["tools"] = tools
            r = self._client.messages.create(**kwargs)
            text, tool_calls = _extract_text_and_tools(getattr(r, "content", None))
            usage = getattr(r, "usage", None)
            total = (getattr(usage, "input_tokens", 0) or 0) + (
                getattr(usage, "output_tokens", 0) or 0
            )
            self.n_calls += 1
            self.total_tokens += total
            return ChatResult(text=text, total_tokens=total, tool_calls=tool_calls)

        return _resilient_with(_call, _anthropic_errors(), retries=self.retries)


if __name__ == "__main__":  # pragma: no cover - runnable Protocol self-check
    from agentkit.types import LLMClient

    client = AnthropicChatClient(model="claude-sonnet-4-5", api_key="sk-test")
    assert isinstance(client, LLMClient), "AnthropicChatClient must satisfy LLMClient"
    sys_str, convo = _split_system(
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert sys_str == "be terse" and convo == [{"role": "user", "content": "hi"}]
    assert LLMUnavailable is not None
    print("backends.anthropic_client self-check OK")
