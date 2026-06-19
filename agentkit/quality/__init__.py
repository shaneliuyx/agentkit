"""agentkit.quality — a source-grounding / verification pass.

Deterministic-first: pure claim/citation extraction + uncited detection always
run; URL-liveness (I/O) and claim-vs-source support (LLM) are injected and
optional.
"""

from agentkit.quality.claim_classifier import (
    ClaimClassifier,
    EmbeddingPrototypeClassifier,
    LLMClaimClassifier,
)
from agentkit.quality.claimbuster import (
    claimbuster_classifier,
    load_claimbuster_exemplars,
    parse_exemplars,
)
from agentkit.quality.verify import (
    Claim,
    HttpUrlChecker,
    UrlChecker,
    VerifyFinding,
    check_links,
    check_support,
    extract_claims,
    find_uncited,
    verify,
)

__all__ = [
    "verify",
    "VerifyFinding",
    "Claim",
    "extract_claims",
    "find_uncited",
    "check_links",
    "check_support",
    "HttpUrlChecker",
    "UrlChecker",
    "ClaimClassifier",
    "EmbeddingPrototypeClassifier",
    "LLMClaimClassifier",
    "parse_exemplars",
    "load_claimbuster_exemplars",
    "claimbuster_classifier",
]
