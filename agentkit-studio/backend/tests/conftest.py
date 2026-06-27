"""Shared test fixtures: a network-free fake LLMClient + collecting emit sink."""

from __future__ import annotations

from typing import Any, Callable

import pytest

from agentkit.types import ChatResult, LLMClient


class FakeClient:
    """A canned ``LLMClient`` — returns a fixed ChatResult, never touches the net.

    Satisfies ``agentkit.types.LLMClient``; ``n_calls`` lets tests assert fan-out.
    The fixed text contains a bare uncited claim so the verify panel produces a
    finding offline.
    """

    def __init__(self, text: str = "The answer is 42.") -> None:
        self.text = text
        self.n_calls = 0
        self.total_tokens = 0

    def chat(self, messages: list[dict[str, Any]], tools=None) -> ChatResult:
        self.n_calls += 1
        self.total_tokens += 5
        return ChatResult(text=self.text, total_tokens=5)


@pytest.fixture(autouse=True)
def _clear_fetch_cache() -> Any:
    """Isolate the process-global web-fetch cache between tests.

    ``studio.tools._fetch_cache`` persists for the life of the process (correct in
    production — it caches fetched pages within a run). In tests that global leaks
    across cases: a real-fetch test populates it, and the grounding guard in
    ``_research_findings_to_patches`` (which reads it) then drops legitimate findings
    in a later, unrelated test. Clear it before and after every test so each starts
    from a clean, empty cache."""
    from studio.tools import _fetch_cache
    _fetch_cache.clear()
    yield
    _fetch_cache.clear()


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def fake_client_factory(fake_client: FakeClient) -> Callable[..., LLMClient]:
    """A client_factory that ignores on_usage and returns the shared fake."""

    def _factory(_on_usage) -> LLMClient:  # type: ignore[no-untyped-def]
        return fake_client

    return _factory
