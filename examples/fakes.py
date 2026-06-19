"""examples/fakes.py — deterministic test doubles for the reference agent.

No network, no randomness, no clock. These fakes let the whole agentkit stack
(orchestrator + roles + agent loop + memory + verify + compactor) run end-to-end
OFFLINE so the composition can be proved and structural numbers (call counts,
token estimates) can be computed deterministically.

  - ``FakeEmbedder``   — bag-of-words hashing embedder (the tests/test_memory.py
                         pattern) satisfying the ``Embedder`` Protocol.
  - ``FakeLLMClient``  — deterministic ``LLMClient``: it echoes a "finding"
                         derived from the last user message plus a fake citation
                         URL, and reports a token count that is a deterministic
                         function of the prompt length. It counts its own calls
                         and accumulated tokens on public attributes so a tiered
                         run can be compared against an all-LLM baseline.

Swapping these for a real client/embedder (oMLX, Claude) turns the SAME agent
into a measured run — that is the whole point of the dependency-injection seams.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from agentkit.types import ChatResult, Message

# A non-alphanumeric run; collapsed to single hyphens to slugify a topic.
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# A fixed, substantial evidence body appended to every canned answer. Keeping it
# constant keeps the client deterministic while giving each finding realistic
# bulk — so the accumulated research transcript is large enough that (a) the
# deterministic compactor genuinely compresses it and (b) the all-LLM baseline's
# full-transcript re-send each round visibly dominates the tiered run's bounded
# compacted brief. A trivial one-line answer would make both paths look equal
# and hide the architecture's real cost behavior (the compactor's documented
# scale-dependence: it only reduces once messages carry meaningful content).
_FINDING_BODY = (
    "Background and context: external memory lets a long-horizon agent persist "
    "intermediate conclusions, retrieved evidence, and prior decisions beyond a "
    "single context window, so coherence survives across many reasoning steps. "
    "Method and mechanism: relevant prior entries are recalled by similarity and "
    "injected before the agent acts, and the deterministic compactor distills the "
    "accumulated history into a small curated brief instead of re-sending it "
    "verbatim. Evidence and citations are preserved so later steps can verify "
    "earlier claims rather than re-deriving them from scratch. This is the tier "
    "that makes a long autonomous run affordable."
)


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder (no network).

    Each lowercased token sets one dimension (token hash -> index), so texts
    that share vocabulary get a positive cosine similarity and unrelated texts
    stay near-orthogonal. The same text always yields the same vector, so memory
    ranking is stable and reproducible. Mirrors the FakeEmbedder used in
    ``tests/test_memory.py``.
    """

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            out.append(vec)
        return out


def _last_user_message(messages: list[Message]) -> str:
    """Return the content of the last user message (empty if none)."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content")
            return content if isinstance(content, str) else ""
    return ""


def _topic_of(text: str) -> str:
    """Derive a short, deterministic topic from a user message.

    Uses the first line, trimmed, capped — enough to make the canned answer
    vary per direction without any randomness.
    """
    first_line = text.strip().splitlines()[0] if text.strip() else "topic"
    return first_line.strip()[:80] or "topic"


def _slug(text: str) -> str:
    """Slugify a topic for the fake citation URL (deterministic)."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:48] or "topic"


class FakeLLMClient:
    """Deterministic ``LLMClient`` that drives the loop to a final answer.

    ``chat`` never emits a tool call, so ``run_agent`` finishes in one round
    with a canned final answer derived from the last user message. The answer is
    a single sentence carrying a fake-but-well-formed citation URL
    (``https://example.org/<slug>``) so the downstream ``verify`` pass has a
    citation to extract and a link to check.

    Token accounting is a deterministic function of prompt length
    (``sum(len(content) // 4 for each message)``), mirroring the compactor's
    ~4-chars/token heuristic, so token cost is computable entirely offline. The
    client tracks its own ``n_calls`` and ``total_tokens`` on public attributes
    so callers can compare a tiered run against an all-LLM baseline.
    """

    def __init__(self) -> None:
        self.n_calls: int = 0
        self.total_tokens: int = 0

    @staticmethod
    def _estimate_tokens(messages: list[Message]) -> int:
        """~4 chars/token over every message's content (deterministic)."""
        total = 0
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                total += len(content) // 4
        return total

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        self.n_calls += 1
        tokens = self._estimate_tokens(messages)
        self.total_tokens += tokens

        topic = _topic_of(_last_user_message(messages))
        url = f"https://example.org/{_slug(topic)}"
        answer = (
            f"finding: {topic} {url}. {_FINDING_BODY}"
        )
        return ChatResult(text=answer, tool_calls=[], total_tokens=tokens)


class FakeUrlChecker:
    """Deterministic ``UrlChecker``: treats fake citation URLs as live.

    Used as the "fake live checker" in the verification pass so the offline run
    exercises the I/O tier of ``verify`` without touching the network. Any
    ``https://example.org/...`` URL produced by ``FakeLLMClient`` is reported
    live; everything else is reported dead so dead-link detection still works.
    """

    def is_live(self, url: str) -> bool:
        return url.startswith("https://example.org/")


if __name__ == "__main__":
    # FakeEmbedder: shared vocabulary -> shared dimensions.
    emb = FakeEmbedder()
    vecs = emb.embed(["cache eviction policy", "cache eviction tuning"])
    assert len(vecs) == 2 and len(vecs[0]) == 64
    shared = sum(1 for a, b in zip(vecs[0], vecs[1]) if a > 0 and b > 0)
    assert shared >= 1, "texts sharing tokens must share dimensions"

    # FakeLLMClient: deterministic answer + a well-formed citation + accounting.
    client = FakeLLMClient()
    msgs: list[Message] = [
        {"role": "system", "content": "You are a Researcher."},
        {"role": "user", "content": "How do LRU caches evict entries?"},
    ]
    r1 = client.chat(msgs)
    assert r1.text.startswith("finding:")
    assert "https://example.org/" in r1.text
    assert r1.tool_calls == []
    assert r1.total_tokens > 0
    assert client.n_calls == 1 and client.total_tokens == r1.total_tokens

    # Deterministic: same messages -> identical text + token count.
    r2 = FakeLLMClient().chat(msgs)
    assert r2.text == r1.text and r2.total_tokens == r1.total_tokens

    # FakeUrlChecker: fake citation URLs are live, others dead.
    checker = FakeUrlChecker()
    assert checker.is_live("https://example.org/lru-caches") is True
    assert checker.is_live("https://nope.test/x") is False

    print("fakes self-check OK")
