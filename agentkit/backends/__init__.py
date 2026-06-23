"""agentkit.backends — concrete LLMClient adapters (process / CLI / HTTP backends)."""

from agentkit.backends.cli import CliLLMClient

__all__ = ["CliLLMClient"]

# The standard OpenAI-compatible adapter depends on the optional ``openai``
# package. Guard the re-export so importing this package never hard-fails when
# ``openai`` is absent — the clear install hint surfaces only on construction.
try:
    from agentkit.backends.openai_compat import (
        OpenAIChatClient,
        OpenAIEmbedder,
        make_client,
    )

    __all__ += ["OpenAIChatClient", "OpenAIEmbedder", "make_client"]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass

# The native Claude adapter — same LLMClient seam, native ``anthropic`` SDK.
# Guarded so importing this package never hard-fails when ``anthropic`` is
# absent; the install hint surfaces only on construction.
try:
    from agentkit.backends.anthropic_client import AnthropicChatClient

    __all__ += ["AnthropicChatClient"]
except ImportError:  # pragma: no cover - defensive optional-dep guard
    pass
