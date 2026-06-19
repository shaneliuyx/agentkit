"""agentkit.quality.claim_classifier — pluggable claim/non-claim classifier.

`verify.extract_claims` filters non-claims (headings, questions, labels)
*structurally* and for free (the always-on cheap tier). This module adds the
OPTIONAL residual-tail catcher: prose sentences that carry no structural marker
yet are not assertions ("Here are the findings.", "Let me explain."). It is an
injected ``ClaimClassifier`` seam — exactly like ``Embedder`` / ``LLMClient`` /
``UrlChecker`` — so an LLM is just one possible adapter, not the only path.

The default non-LLM adapter is ``EmbeddingPrototypeClassifier``: it reuses the
``Embedder`` the library already depends on. Embed a handful of "claim" vs
"non-claim" exemplars once, average each set into a centroid, then classify a
sentence by whichever centroid it is closer to (cosine). This is:

  * non-LLM            — no generation, just embeddings
  * keyword-free       — no language-specific word lists
  * multilingual       — inherits the embedder's cross-lingual space (bge-m3)
  * training-free      — a few exemplars, no fitted model file
  * cheap              — one embedding per sentence, batchable, local ms

Classical alternatives (ClaimBuster's SVM over POS/NER/TF-IDF; spaCy
dependency rules) are also non-LLM but drag in per-language NLP models or a
training pipeline; the embedding-prototype route fits agentkit's lean, seam-
based, multilingual goals best. Any of them can implement ``ClaimClassifier``.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from agentkit.types import Embedder, LLMClient, Message


@runtime_checkable
class ClaimClassifier(Protocol):
    """Decides whether a sentence is a factual ASSERTION worth a citation."""

    def is_claim(self, sentence: str) -> bool:
        ...


# Default exemplars. English, but bge-m3 maps cross-lingually, so a Chinese or
# German claim still lands nearer the claim centroid. Override per domain.
DEFAULT_CLAIM_EXAMPLES = (
    "The service runs in the ap-southeast-1 region.",
    "Python 3.12 was released in October 2023.",
    "The capital of Australia is Canberra.",
    "The function returns a list of integers.",
    "Water boils at 100 degrees Celsius at sea level.",
)
DEFAULT_NONCLAIM_EXAMPLES = (
    "Here are the findings from my research.",
    "Let me explain the details below.",
    "In summary, this is what I found.",
    "The following section covers the results.",
    "Let's take a closer look at this topic.",
)


def _cosine(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine (keeps this module numpy-free)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _centroid(vecs: list[list[float]]) -> list[float]:
    """Element-wise mean of vectors (the prototype)."""
    if not vecs:
        return []
    n = len(vecs)
    return [sum(col) / n for col in zip(*vecs)]


class EmbeddingPrototypeClassifier:
    """Non-LLM ``ClaimClassifier`` over an injected ``Embedder``.

    Builds a claim centroid and a non-claim centroid from exemplars at
    construction, then classifies by nearest centroid. ``margin`` biases the
    decision toward KEEPING claims (conservative): a sentence is a claim unless
    it is closer to the non-claim centroid by more than ``margin``.
    """

    def __init__(
        self,
        embedder: Embedder,
        claim_examples: tuple[str, ...] = DEFAULT_CLAIM_EXAMPLES,
        nonclaim_examples: tuple[str, ...] = DEFAULT_NONCLAIM_EXAMPLES,
        margin: float = 0.05,
    ) -> None:
        self._embedder = embedder
        self.margin = margin
        # One batched embed call for all exemplars; split into the two centroids.
        vecs = embedder.embed(list(claim_examples) + list(nonclaim_examples))
        k = len(claim_examples)
        self._claim_c = _centroid(vecs[:k])
        self._nonclaim_c = _centroid(vecs[k:])

    def is_claim(self, sentence: str) -> bool:
        """Closer to the claim centroid (within ``margin``) ⇒ a claim. On any
        embedding failure, default to True — never silently drop a real claim."""
        try:
            v = self._embedder.embed([sentence])[0]
        except Exception:
            return True
        sim_claim = _cosine(v, self._claim_c)
        sim_non = _cosine(v, self._nonclaim_c)
        return sim_claim >= sim_non - self.margin


class LLMClaimClassifier:
    """feynman-style ``ClaimClassifier``: an injected LLM judges, in context,
    whether a sentence is a citable assertion. Same seam as the embedding
    adapter — but it spends ONE LLM call per sentence (the cost feynman pays for
    not having a deterministic segmenter). Use when judgement quality matters
    more than cost; prefer ``EmbeddingPrototypeClassifier`` otherwise.
    """

    _SYSTEM = (
        "Decide whether the sentence is a factual ASSERTION that should be "
        "backed by a citation (a claim), versus a question, heading, opinion, "
        "or meta/framing sentence. Answer with exactly one word: 'claim' or "
        "'non-claim'."
    )

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def is_claim(self, sentence: str) -> bool:
        """One LLM call → claim/non-claim. Conservative: any failure or unclear
        verdict defaults to True, so a real claim is never silently dropped."""
        msgs: list[Message] = [
            {"role": "system", "content": self._SYSTEM},
            {"role": "user", "content": sentence},
        ]
        try:
            out = (self._client.chat(msgs).text or "").strip().lower()
        except Exception:
            return True
        return "non-claim" not in out and "nonclaim" not in out


def _demo() -> None:
    """Self-check with a transparent fake embedder (no network, no LLM).

    The fake encodes each text as [claim-signal, nonclaim-signal] counts, so the
    centroid + cosine logic is verifiable deterministically."""
    claim_kw = {"is", "runs", "released", "returns", "boils", "capital", "region"}
    non_kw = {"here", "let", "summary", "following", "look", "findings", "explain"}

    class _FakeEmbedder:
        def embed(self, texts: list[str]) -> list[list[float]]:
            out: list[list[float]] = []
            for t in texts:
                toks = set(t.lower().replace(".", " ").split())
                out.append([float(len(toks & claim_kw)) + 0.1,
                            float(len(toks & non_kw)) + 0.1])
            return out

    clf = EmbeddingPrototypeClassifier(_FakeEmbedder())
    assert clf.is_claim("The capital of France is Paris.") is True
    assert clf.is_claim("Here are the findings let me explain summary") is False
    # `margin` is the conservatism knob: a large margin keeps even a clearly
    # non-claim-leaning sentence as a claim (bias toward never dropping a claim).
    safe = EmbeddingPrototypeClassifier(_FakeEmbedder(), margin=2.0)
    assert safe.is_claim("Here are the findings let me explain summary") is True
    assert isinstance(clf, ClaimClassifier)

    # LLM adapter (feynman-style) with a keyed fake client — no network.
    from agentkit.types import ChatResult

    class _FakeLLM:
        def chat(self, messages, tools=None):
            u = messages[-1]["content"].lower()
            verdict = "non-claim" if ("findings" in u or "let me" in u) else "claim"
            return ChatResult(text=verdict, tool_calls=[], total_tokens=1)

    llm_clf = LLMClaimClassifier(_FakeLLM())
    assert isinstance(llm_clf, ClaimClassifier)
    assert llm_clf.is_claim("The capital of France is Paris.") is True
    assert llm_clf.is_claim("Here are the findings.") is False
    print("claim_classifier._demo OK")


if __name__ == "__main__":
    _demo()
