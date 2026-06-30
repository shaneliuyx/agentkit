"""Deterministic publish-readiness checks for research reports.

This module is intentionally narrower than ``artifact_lint``. Lints catch
structural defects inside the artifact; the publish gate checks whether a
report-like answer is aligned with the user's request and evidence policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from studio.report_profiles import is_report_request


_STOPWORDS = {
    "about",
    "above",
    "action",
    "available",
    "blocks",
    "brief",
    "code",
    "concise",
    "concrete",
    "cover",
    "covering",
    "essential",
    "evidence",
    "fetched",
    "generic",
    "include",
    "invented",
    "keep",
    "markdown",
    "model",
    "output",
    "placeholder",
    "placeholders",
    "publishable",
    "report",
    "research",
    "section",
    "sections",
    "source",
    "sources",
    "steps",
    "under",
    "urls",
    "when",
    "with",
    "word",
    "words",
    "write",
}

_EVIDENCE_MARKERS = (
    "citation",
    "citations",
    "cite",
    "cited",
    "url",
    "urls",
    "source",
    "sources",
    "fetched evidence",
    "evidence",
)


@dataclass(frozen=True)
class PublishGateResult:
    publish_ready: bool
    issues: tuple[str, ...] = ()

    @property
    def outcome(self) -> str:
        return "pass" if self.publish_ready else "fail"

    @property
    def detail(self) -> str:
        if self.publish_ready:
            return "Report passed deterministic publish-readiness checks."
        return "; ".join(self.issues)


def _normalize_token(token: str) -> str:
    token = token.lower()
    if len(token) > 4 and token.endswith("s"):
        token = token[:-1]
    return token


def _important_terms(requirement: str) -> list[str]:
    terms: list[str] = []
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", requirement or ""):
        term = _normalize_token(raw.replace("-", ""))
        if term in _STOPWORDS:
            continue
        if term.isdigit():
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _missing_terms(requirement: str, text: str) -> list[str]:
    terms = _important_terms(requirement)
    if not terms:
        return []
    doc = " ".join(_normalize_token(t) for t in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text or ""))
    missing = [term for term in terms if term not in doc]
    # Require enough misses to avoid failing a report for one synonym.
    if len(missing) >= 3 and len(missing) >= max(3, len(terms) // 3):
        return missing
    return []


def evaluate_publish_readiness(
    requirement: str,
    text: str,
    *,
    verified_urls: list[str] | None = None,
) -> PublishGateResult:
    """Return deterministic publish readiness for report-like outputs.

    Non-report tasks pass through. Report tasks fail when they ignore explicit
    evidence/citation requirements, omit too many important request terms, or
    are too thin to be a publishable report.
    """
    if not is_report_request(requirement):
        return PublishGateResult(True)

    body = (text or "").strip()
    issues: list[str] = []
    low_req = (requirement or "").lower()

    wants_evidence = any(marker in low_req for marker in _EVIDENCE_MARKERS)
    has_url = "http://" in body or "https://" in body
    if wants_evidence and not has_url:
        issues.append("[publish-gate] Report requested evidence/citations but final output has no source URL.")
    if verified_urls is not None and has_url and not verified_urls:
        issues.append("[publish-gate] Final output has URLs but none were verified through fetched evidence.")

    words = re.findall(r"\w+", body)
    if len(words) < 120:
        issues.append("[publish-gate] Final output is too short to be a complete research report.")

    missing = _missing_terms(requirement, body)
    if missing:
        shown = ", ".join(missing[:8])
        suffix = "..." if len(missing) > 8 else ""
        issues.append(f"[publish-gate] Final output misses important request terms: {shown}{suffix}.")

    return PublishGateResult(not issues, tuple(issues))
