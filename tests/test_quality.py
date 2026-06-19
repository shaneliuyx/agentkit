"""Tests for agentkit.quality.verify — source-grounding pass (no network, no LLM)."""

from __future__ import annotations

from typing import Any

import pytest

from agentkit.quality.verify import (
    Claim,
    VerifyFinding,
    check_support,
    extract_claims,
    find_uncited,
    verify,
)
from agentkit.types import ChatResult, Message


class _FakeChecker:
    """A fake UrlChecker with a fixed liveness answer (no network)."""

    def __init__(self, live: bool) -> None:
        self.live = live
        self.checked: list[str] = []

    def is_live(self, url: str) -> bool:
        self.checked.append(url)
        return self.live


class _FixedClient:
    """A fake LLMClient returning a fixed verdict string."""

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict

    def chat(self, messages: list[Message],
             tools: list[dict[str, Any]] | None = None) -> ChatResult:
        return ChatResult(text=self.verdict, total_tokens=2)


@pytest.mark.unit
def test_uncited_claim_is_high_finding():
    findings = verify("The sky is blue.")
    assert len(findings) == 1
    assert findings[0].severity == "high"
    assert findings[0].issue == "no citation"


@pytest.mark.unit
def test_dead_link_is_critical_finding():
    text = "Agents are useful https://example.com/dead."
    findings = verify(text, checker=_FakeChecker(live=False))
    dead = [f for f in findings if f.issue == "dead link"]
    assert len(dead) == 1
    assert dead[0].severity == "critical"
    assert dead[0].url == "https://example.com/dead"


@pytest.mark.unit
def test_cited_and_live_text_has_no_deterministic_findings():
    text = "Claim one https://a.test/x. Claim two https://b.test/y."
    findings = verify(text, checker=_FakeChecker(live=True))
    assert findings == []


@pytest.mark.unit
def test_check_support_unsupported_is_high_finding():
    claim = Claim(text="Cats can fly.", citation="https://src.test/cats")
    finding = check_support(claim, "Cats are mammals.", _FixedClient("unsupported"))
    assert finding is not None
    assert finding.severity == "high"
    assert finding.issue == "source does not support claim"


@pytest.mark.unit
def test_check_support_supported_returns_none():
    claim = Claim(text="Cats are mammals.", citation="https://src.test/cats")
    finding = check_support(claim, "Cats are mammals.", _FixedClient("supported"))
    assert finding is None


@pytest.mark.unit
def test_verify_runs_llm_support_tier_when_client_and_sources_given():
    text = "Cats can fly https://src.test/cats."
    findings = verify(
        text,
        checker=_FakeChecker(live=True),
        client=_FixedClient("unsupported"),
        sources={"https://src.test/cats": "Cats cannot fly."},
    )
    assert any(f.issue == "source does not support claim" and f.severity == "high"
               for f in findings)


@pytest.mark.unit
def test_severity_sort_order_critical_first():
    text = "An uncited claim. A dead one https://example.com/dead."
    findings = verify(text, checker=_FakeChecker(live=False))
    severities = [f.severity for f in findings]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    assert severities == sorted(severities, key=lambda s: order[s])
    assert severities[0] == "critical"


@pytest.mark.unit
def test_extract_claims_detects_url_marker_and_author_year():
    claims = extract_claims(
        "A url claim https://a.test/x. A numeric claim [1]. "
        "An author claim (Smith, 2020). An uncited claim."
    )
    citations = [c.citation for c in claims]
    assert "https://a.test/x" in citations
    assert "[1]" in citations
    assert "(Smith, 2020)" in citations
    assert "" in citations


@pytest.mark.unit
def test_find_uncited_is_pure_and_flags_empty_citation():
    claims = [
        Claim(text="cited", citation="[1]"),
        Claim(text="uncited", citation=""),
    ]
    findings = find_uncited(claims)
    assert len(findings) == 1
    assert findings[0].claim == "uncited"
    assert findings[0].severity == "high"


@pytest.mark.unit
def test_findings_are_frozen():
    f = VerifyFinding(severity="low", claim="c", issue="i")
    with pytest.raises(Exception):
        f.severity = "high"  # type: ignore[misc]


# --- ClaimClassifier seam + embedding-prototype adapter (non-LLM) ---

from agentkit.quality.claim_classifier import (  # noqa: E402
    ClaimClassifier,
    EmbeddingPrototypeClassifier,
)

_CLAIM_KW = {"is", "runs", "released", "returns", "boils", "capital", "region"}
_NON_KW = {"here", "let", "summary", "following", "look", "findings", "explain"}


class _CountEmbedder:
    """Deterministic 2-D embedder: [claim-signal, nonclaim-signal] keyword counts.
    No network, no LLM — makes the centroid/cosine logic verifiable exactly."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            toks = set(t.lower().replace(".", " ").split())
            out.append([float(len(toks & _CLAIM_KW)) + 0.1,
                        float(len(toks & _NON_KW)) + 0.1])
        return out


@pytest.mark.unit
def test_embedding_prototype_classifier_satisfies_protocol_and_separates():
    clf = EmbeddingPrototypeClassifier(_CountEmbedder())
    assert isinstance(clf, ClaimClassifier)
    assert clf.is_claim("The capital of Australia is Canberra.") is True
    assert clf.is_claim("Here are the findings let me explain summary") is False


@pytest.mark.unit
def test_extract_claims_classifier_drops_markerless_nonclaim():
    # A framing sentence with NO structural marker (#, ?, :) survives the cheap
    # structural filter, but the injected classifier catches it.
    text = ("Here are the findings let me explain summary\n"
            "The capital of Australia is Canberra.")
    without = [c.text for c in extract_claims(text)]
    with_clf = [c.text for c in
                extract_claims(text, classifier=EmbeddingPrototypeClassifier(_CountEmbedder()))]
    assert any("findings" in c for c in without)       # structural keeps it
    assert not any("findings" in c for c in with_clf)  # classifier drops it
    assert any("Canberra" in c for c in with_clf)      # real claim kept


@pytest.mark.unit
def test_classifier_is_optional_default_unchanged():
    text = "The capital of Australia is Canberra. An uncited claim."
    assert extract_claims(text) == extract_claims(text, classifier=None)


@pytest.mark.unit
def test_classifier_margin_is_conservative():
    # A large margin keeps even a non-claim-leaning sentence (never drop a claim).
    safe = EmbeddingPrototypeClassifier(_CountEmbedder(), margin=2.0)
    assert safe.is_claim("Here are the findings let me explain summary") is True


# --- ClaimBuster exemplar loader (parse core; no network) ---

from agentkit.quality.claimbuster import parse_exemplars  # noqa: E402

_CB_FIXTURE = (
    "Sentence_id,Text,Speaker,Verdict\n"
    '1,"We consume 50 percent of the world cocaine.",A,1\n'
    '2,"That answer was as clear as Boston harbor.",B,-1\n'
    '3,"The deficit grew by 12 percent last year.",A,1\n'
    '4,"I think you are a wonderful person.",B,-1\n'
    '5,"We met on a Tuesday in spring.",A,0\n'        # UFS — ignored
    '6,"",A,1\n'                                       # blank — skipped
)


@pytest.mark.unit
def test_parse_exemplars_maps_verdict_to_classes(tmp_path):
    p = tmp_path / "gt.csv"
    p.write_text(_CB_FIXTURE, encoding="utf-8")
    claims, nonclaims = parse_exemplars(p, n=20)
    assert claims == ("We consume 50 percent of the world cocaine.",
                      "The deficit grew by 12 percent last year.")
    assert nonclaims == ("That answer was as clear as Boston harbor.",
                         "I think you are a wonderful person.")


@pytest.mark.unit
def test_parse_exemplars_respects_n_cap(tmp_path):
    p = tmp_path / "gt.csv"
    p.write_text(_CB_FIXTURE, encoding="utf-8")
    claims, nonclaims = parse_exemplars(p, n=1)
    assert len(claims) == 1 and len(nonclaims) == 1


@pytest.mark.unit
def test_llm_claim_classifier_is_a_seam_adapter():
    from agentkit.quality.claim_classifier import LLMClaimClassifier

    class _KeyedLLM:
        """feynman-style judge faked deterministically (no network)."""
        def chat(self, messages: list[Message], tools: Any = None) -> ChatResult:
            u = messages[-1]["content"].lower()
            verdict = "non-claim" if ("findings" in u or "let me" in u) else "claim"
            return ChatResult(text=verdict, tool_calls=[], total_tokens=1)

    clf = LLMClaimClassifier(_KeyedLLM())
    assert isinstance(clf, ClaimClassifier)
    assert clf.is_claim("The capital of Australia is Canberra.") is True
    assert clf.is_claim("Here are the findings let me explain.") is False
    # plugs into the SAME extract_claims path as the embedding adapter
    text = "Here are the findings let me explain\nThe capital of Australia is Canberra."
    kept = [c.text for c in extract_claims(text, classifier=clf)]
    assert not any("findings" in c for c in kept)
    assert any("Canberra" in c for c in kept)


@pytest.mark.unit
def test_claimbuster_exemplars_feed_the_classifier(tmp_path):
    # The loaded exemplars build a working classifier (with the fake embedder).
    p = tmp_path / "gt.csv"
    p.write_text(_CB_FIXTURE, encoding="utf-8")
    claims, nonclaims = parse_exemplars(p, n=20)
    clf = EmbeddingPrototypeClassifier(_CountEmbedder(),
                                       claim_examples=claims,
                                       nonclaim_examples=nonclaims)
    assert isinstance(clf, ClaimClassifier)
