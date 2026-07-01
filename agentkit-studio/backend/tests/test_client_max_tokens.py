from agentkit.types import ChatResult

from studio.client import MaxTokensClient


def test_max_tokens_client_passes_completion_cap() -> None:
    captured = {}

    class Inner:
        n_calls = 0
        total_tokens = 0

        def chat(self, messages, tools=None, max_tokens=None):
            captured["max_tokens"] = max_tokens
            return ChatResult(text="ok", total_tokens=1)

    result = MaxTokensClient(Inner(), 123).chat([{"role": "user", "content": "x"}])

    assert result.text == "ok"
    assert captured["max_tokens"] == 123


def test_max_tokens_client_falls_back_for_plain_clients() -> None:
    class Inner:
        def chat(self, messages, tools=None):
            return ChatResult(text="ok", total_tokens=1)

    result = MaxTokensClient(Inner(), 123).chat([{"role": "user", "content": "x"}])

    assert result.text == "ok"
