"""agentkit.backends.openai_compat — the STANDARD OpenAI-compatible adapter.

Two concrete adapters satisfying the ``agentkit.types`` Protocols, so callers
stop hand-rolling the toy ``MyClient`` / ``MyEmbedder`` fakes from the README:

  - ``OpenAIChatClient``  → ``LLMClient``  (``.chat(messages, tools=None) -> ChatResult``)
  - ``OpenAIEmbedder``    → ``Embedder``   (``.embed(texts) -> list[list[float]]``)

Both wrap an ``openai.OpenAI`` client and target ANY OpenAI-compatible endpoint:
a local oMLX on ``:8000`` (no key), a VibeProxy bridge to Claude, or hosted
OpenAI. The robustness (env-chain defaults, the non-empty key sentinel, resilient
retry through transient connection drops) is ported from the lab reference,
``agent-prep/shared/llm.py``, and supersedes the example-local ``OMLXClient``
in ``examples/dynamic_topology_e2e.py``.

``openai`` is an OPTIONAL dependency (the ``[openai]`` extra). It is imported
LAZILY so ``import agentkit`` never hard-fails without it; constructing an
adapter without ``openai`` installed raises a clear ``pip install agentkit[openai]``
message instead.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

from agentkit.types import ChatResult, Message

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import
    from openai import OpenAI


# ── endpoint defaults: walk an env chain so every host's names resolve ────────
# (ported from shared/llm.py: LLM_BASE_URL → OMLX_BASE_URL → local oMLX :8000)
def _default_base() -> str:
    return (
        os.getenv("LLM_BASE_URL")
        or os.getenv("OMLX_BASE_URL")
        or "http://localhost:8000/v1"
    )


# Non-empty sentinel: the OpenAI SDK rejects an empty api_key at construction;
# local endpoints (oMLX) authenticate per-request, so a placeholder lets the
# client build and fail gracefully at call time rather than crashing at
# construction when no .env is loaded.
_KEY_SENTINEL = "EMPTY"


def _default_key() -> str:
    return os.getenv("LLM_API_KEY") or os.getenv("OMLX_API_KEY") or _KEY_SENTINEL


_INSTALL_HINT = (
    "the OpenAI-compatible adapter needs the 'openai' package — "
    "install it with: pip install agentkit[openai]"
)


def make_client(base_url: str | None = None, api_key: str | None = None) -> "OpenAI":
    """An ``openai.OpenAI`` client; unset args fall back through the env chain.

    ``openai`` is imported lazily so this is the ONLY place a missing optional
    dependency surfaces — and it surfaces as a clear install hint, not an opaque
    ``ModuleNotFoundError``.
    """
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - exercised only without openai
        raise ImportError(_INSTALL_HINT) from exc
    return OpenAI(base_url=base_url or _default_base(), api_key=api_key or _default_key())


# ── resilient calls (flaky local servers drop connections under load) ─────────
class LLMUnavailable(RuntimeError):
    """Endpoint refused after retries — raised by ``.chat`` once retries exhaust."""


def _resilient_with(
    fn,
    error_types: tuple[type[Exception], ...],
    *args,
    retries: int = 4,
    backoff: float = 2.0,
):
    """Retry ``fn`` through the given transient ``error_types``; raise
    ``LLMUnavailable`` once retries exhaust. Vendor-agnostic so every adapter
    (OpenAI, Anthropic, ...) shares one retry policy with its own error set."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return fn(*args)
        except error_types as exc:
            last_exc = exc
            if attempt == retries - 1:
                raise LLMUnavailable(str(exc)) from exc
            time.sleep(backoff * (attempt + 1))
    raise LLMUnavailable(str(last_exc) if last_exc else "unreachable")


def _resilient(fn, *args, retries: int = 4, backoff: float = 2.0):
    """Retry through transient OpenAI API errors; raise ``LLMUnavailable`` if it never recovers."""
    try:
        from openai import APIConnectionError, APIError
    except ImportError as exc:  # pragma: no cover - guarded by adapter construction
        raise ImportError(_INSTALL_HINT) from exc
    return _resilient_with(
        fn, (APIConnectionError, APIError), *args, retries=retries, backoff=backoff
    )


def _extract_tool_calls(message: Any) -> list[tuple[str, dict[str, Any]]]:
    """Map native OpenAI ``tool_calls`` → agentkit ``[(name, arguments), ...]``.

    Returns ``[]`` when the model emitted plain text (the common case). The agent
    loop parses text-encoded tool-calls as a fallback elsewhere, so we only pass
    through what the endpoint structured natively.
    """
    import json

    raw = getattr(message, "tool_calls", None) or []
    calls: list[tuple[str, dict[str, Any]]] = []
    for tc in raw:
        fn = getattr(tc, "function", None)
        if fn is None:
            continue
        name = getattr(fn, "name", "") or ""
        args_raw = getattr(fn, "arguments", "") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
        except (ValueError, TypeError):
            args = {}
        if name:
            calls.append((name, args))
    return calls


class OpenAIChatClient:
    """STANDARD ``LLMClient`` over any OpenAI-compatible chat endpoint.

    Construct once and pass anywhere an ``LLMClient`` is expected (``run_agent``,
    ``run_plan``, role dispatch). Defaults target a local oMLX on ``:8000`` with
    no API key; point it at Claude/OpenAI by passing ``base_url`` / ``api_key``
    or via the ``LLM_BASE_URL`` / ``LLM_API_KEY`` env chain.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
        retries: int = 4,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.retries = retries
        self._client = make_client(base_url, api_key)
        self.n_calls = 0
        self.total_tokens = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """One completion → ``ChatResult(text, total_tokens, tool_calls)``.

        Forwards ``tools`` (native tool_calls) when provided. Retries through
        transient connection drops; raises ``LLMUnavailable`` after ``retries``.
        """

        def _call() -> ChatResult:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
            }
            if tools:
                kwargs["tools"] = tools
            r = self._client.chat.completions.create(**kwargs)
            message = r.choices[0].message
            text = (getattr(message, "content", None) or "").strip()
            usage = getattr(r, "usage", None)
            total = getattr(usage, "total_tokens", 0) or 0
            self.n_calls += 1
            self.total_tokens += total
            return ChatResult(
                text=text,
                total_tokens=total,
                tool_calls=_extract_tool_calls(message),
            )

        return _resilient(_call, retries=self.retries)


class OpenAIEmbedder:
    """STANDARD ``Embedder`` over any OpenAI-compatible embeddings endpoint."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self._client = make_client(base_url, api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Map a batch of texts → a batch of float vectors (input order preserved)."""
        if not texts:
            return []
        r = self._client.embeddings.create(model=self.model, input=texts)
        return [list(item.embedding) for item in r.data]


if __name__ == "__main__":  # pragma: no cover - runnable Protocol self-check
    from agentkit.types import Embedder, LLMClient

    # Construction works offline (oMLX defaults, sentinel key); no call is made.
    chat_client = OpenAIChatClient(model="Qwen2.5-Coder-7B-Instruct-MLX-4bit")
    embedder = OpenAIEmbedder(model="bge-m3-mlx-fp16")
    assert isinstance(chat_client, LLMClient), "OpenAIChatClient must satisfy LLMClient"
    assert isinstance(embedder, Embedder), "OpenAIEmbedder must satisfy Embedder"
    assert _default_base().endswith("/v1")
    print("backends.openai_compat self-check OK")
