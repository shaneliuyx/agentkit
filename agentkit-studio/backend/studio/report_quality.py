"""Deterministic publish-readiness checks for research reports.

This module is intentionally narrower than ``artifact_lint``. Lints catch
structural defects inside the artifact; the publish gate checks whether a
report-like answer is aligned with the user's request and evidence policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from studio.report_profiles import is_report_request


_STOPWORDS = {
    "about",
    "above",
    "action",
    "actionable",
    "available",
    "blocks",
    "brief",
    "citation",
    "citations",
    "cite",
    "cited",
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

_REVISION_PROMPT_MAX_CHARS = 12_000
_STRUCTURE_INSTRUCTION_RE = re.compile(
    r"(?is)structure\s+the\s+deliverable\s+with\s+these\s+sections.*?"
    r"(?=\n\s*\n|focus\s+specifically\s+on:|$)"
)
_FOCUS_ASSIGNMENT_RE = re.compile(r"(?is)focus\s+specifically\s+on:.*$")
_SCORING_INSTRUCTION_RE = re.compile(
    r"(?is)\n\s*unified\s+scoring\s+requirements\s+for\s+this\s+task:.*$"
)
_ANALYSIS_RE = re.compile(
    r"(?i)\b("
    r"analysis|analy[sz]e|synthesis|synthesi[sz]e|implication|trade-?off|"
    r"pattern|compare|contrast|why\s+it\s+matters|therefore|because|"
    r"recommendation|next\s+step"
    r")\b"
)
_REFLECTION_RE = re.compile(
    r"(?i)\b("
    r"limitation|reflection|open\s+question|uncertain|unverified|caveat|"
    r"constraint|assumption|further\s+research|not\s+yet\s+verified"
    r")\b"
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


_HIGH_IMPACT_RE = re.compile(
    r"(?i)\b("
    r"medical|medicine|clinical|health|diagnosis|treatment|patient|"
    r"legal|law|lawsuit|contract|compliance|regulatory|policy|"
    r"financial|finance|investment|investor|tax|insurance|"
    r"security|privacy|safety|risk|incident"
    r")\b"
)


def build_review_status(
    requirement: str,
    *,
    evidence_count: int = 0,
    weak_evidence_count: int = 0,
    scorecard: dict[str, Any] | None = None,
    publish_issues: tuple[str, ...] | list[str] = (),
    loopdoctor_checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the minimal human-review state for a finished report."""
    reasons: list[str] = []
    report_like = is_report_request(requirement)
    if _HIGH_IMPACT_RE.search(requirement or ""):
        reasons.append("high-impact topic")
    if publish_issues:
        reasons.append("publish/readiness issues")
    if report_like and evidence_count <= 0:
        reasons.append("no accepted evidence matrix rows")
    if report_like and weak_evidence_count > 0:
        reasons.append("weak or uncorroborated evidence")
    if any(c.get("status") != "pass" for c in (loopdoctor_checks or [])):
        reasons.append("non-passing loop-doctor checks")

    if report_like and scorecard:
        total = scorecard.get("score", scorecard.get("total"))
        max_score = scorecard.get("max_score", 100)
        if isinstance(total, (int, float)) and isinstance(max_score, (int, float)) and max_score:
            if float(total) / float(max_score) < 0.8:
                reasons.append("scorecard below review threshold")
        for row in scorecard.get("categories", []) if isinstance(scorecard, dict) else []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("category", "")).lower()
            if not any(k in name for k in ("source", "citation", "evidence")):
                continue
            score = row.get("score")
            points = row.get("points")
            if isinstance(score, (int, float)) and isinstance(points, (int, float)) and points:
                if float(score) / float(points) < 0.75:
                    reasons.append(f"weak {row.get('category')} score")
                    break

    return {
        "required": bool(reasons),
        "status": "REVIEW_REQUIRED" if reasons else "NOT_REQUIRED",
        "publish_decision": "REVIEW_REQUIRED" if reasons else "PUBLISH_READY",
        "reviewed": False,
        "reasons": reasons,
    }


def _normalize_token(token: str) -> str:
    token = token.lower()
    if token.startswith("implement"):
        return "implement"
    if len(token) > 4 and token.endswith("s"):
        token = token[:-1]
    return token


def _important_terms(requirement: str) -> list[str]:
    terms: list[str] = []
    source = _STRUCTURE_INSTRUCTION_RE.sub(" ", requirement or "")
    source = _FOCUS_ASSIGNMENT_RE.sub(" ", source)
    source = _SCORING_INSTRUCTION_RE.sub(" ", source)
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", source):
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


def _duplicate_headings(text: str) -> list[str]:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for heading in re.findall(r"(?m)^##\s+(.+)$", text or ""):
        key = re.sub(r"\s+", " ", heading).strip().lower()
        if key in seen and seen[key] not in duplicates:
            duplicates.append(seen[key])
        else:
            seen[key] = heading.strip()
    return duplicates


def _extra_report_titles(text: str) -> list[str]:
    titles = [h.strip() for h in re.findall(r"(?m)^#\s+(.+)$", text or "")]
    return titles[1:]


def _unresolved_report_title(text: str) -> bool:
    first = re.search(r"(?m)^#\s+(.+)$", text or "")
    if not first:
        return False
    title = re.sub(r"[_()\-—]+", " ", first.group(1)).strip().lower()
    if title in {"research report", "technical report", "final report", "report", "deliverable"}:
        return True
    return "title" in title and ("generated" in title or "report" in title or "deliverable" in title)


def evaluate_publish_readiness(
    requirement: str,
    text: str,
    *,
    verified_urls: list[str] | None = None,
    required_sections: list[str] | tuple[str, ...] | None = None,
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

    if re.search(r"(?i)_\((?:pending|to be completed)\s*[-—][^)]*\)_", body):
        issues.append("[publish-gate] Final output still contains unfinished section placeholders.")

    if _unresolved_report_title(body):
        issues.append("[publish-gate] Final output still contains an unresolved placeholder report title.")

    if required_sections:
        from studio.rubric import sections_present
        present = {s.lower() for s in sections_present(body, required_sections)}
        missing = [s for s in required_sections if str(s).lower() not in present]
        if missing:
            shown = ", ".join(str(s) for s in missing[:5])
            suffix = "..." if len(missing) > 5 else ""
            issues.append(f"[publish-gate] Final output misses active outline sections: {shown}{suffix}.")

    duplicate_headings = _duplicate_headings(body)
    if duplicate_headings:
        shown = ", ".join(duplicate_headings[:5])
        suffix = "..." if len(duplicate_headings) > 5 else ""
        issues.append(f"[publish-gate] Final output repeats section headings: {shown}{suffix}.")

    extra_titles = _extra_report_titles(body)
    if extra_titles:
        shown = ", ".join(extra_titles[:5])
        suffix = "..." if len(extra_titles) > 5 else ""
        issues.append(f"[publish-gate] Final output contains extra report titles: {shown}{suffix}.")

    missing = _missing_terms(requirement, body)
    if missing:
        shown = ", ".join(missing[:8])
        suffix = "..." if len(missing) > 8 else ""
        issues.append(f"[publish-gate] Final output misses important request terms: {shown}{suffix}.")

    return PublishGateResult(not issues, tuple(issues))


def combined_publish_issues(
    publish: PublishGateResult,
    artifact_text: str,
    evidence_text: str = "",
) -> tuple[str, ...]:
    """Return publish gate issues plus deterministic artifact/depth issues."""
    from studio.artifact_lint import lint_artifact

    return tuple(dict.fromkeys((
        *publish.issues,
        *lint_artifact(artifact_text or ""),
        *synthesis_depth_issues(artifact_text or "", evidence_text or ""),
    )))


def _unique_evidence_urls(evidence_text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for raw in re.findall(r"https?://[^\s)>\]\"']+", evidence_text or ""):
        url = raw.rstrip(".,;:")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def synthesis_depth_issues(text: str, evidence_text: str) -> tuple[str, ...]:
    """Detect cited-but-shallow reports before they are marked publish-ready.

    This is intentionally task-neutral. When fetched evidence exists, the final
    report must do more than preserve URLs: it needs a summary, analysis, and an
    explicit limitations/reflection surface so later phases know what remains
    unresolved.
    """
    if not evidence_text or ("http://" not in evidence_text and "https://" not in evidence_text):
        return ()
    body = (text or "").strip()
    if not body:
        return ("[publish-gate] Final output did not synthesize fetched evidence.",)

    issues: list[str] = []
    low = body.lower()
    headings = [h.lower() for h in re.findall(r"(?m)^#{1,3}\s+(.+)$", body)]
    has_summary = any("summary" in h or "overview" in h for h in headings)
    if not has_summary:
        issues.append("[publish-gate] Final report lacks a summary section for synthesized findings.")
    if not _ANALYSIS_RE.search(body):
        issues.append("[publish-gate] Final report lacks analysis, implications, or recommendations.")
    has_reflection = any(
        any(word in h for word in ("limitation", "reflection", "open question", "caveat"))
        for h in headings
    ) or bool(_REFLECTION_RE.search(body))
    if not has_reflection:
        issues.append("[publish-gate] Final report lacks limitations, caveats, or reflection.")

    evidence_urls = _unique_evidence_urls(evidence_text)
    if evidence_urls:
        cited_urls = set(_unique_evidence_urls(body))
        used = sum(1 for u in evidence_urls if u in cited_urls)
        # Same-page multi-finding runs should not be failed for having one URL,
        # but multi-source evidence should not collapse to a single token citation.
        if len(evidence_urls) >= 2 and used < max(2, (len(evidence_urls) + 1) // 2):
            issues.append("[publish-gate] Final report uses too little of the fetched evidence.")
    if "research_finding" in low:
        issues.append("[publish-gate] Final report leaked internal RESEARCH_FINDING scaffolding.")
    return tuple(issues)


def _clip_block(text: str, max_chars: int = _REVISION_PROMPT_MAX_CHARS) -> str:
    body = (text or "").strip()
    if len(body) <= max_chars:
        return body
    return body[:max_chars].rstrip() + "\n\n[truncated]"


def build_revision_evidence_text(
    outputs: dict[str, str],
    *,
    max_chars: int = _REVISION_PROMPT_MAX_CHARS,
) -> str:
    """Return URL-bearing phase outputs for a publish-revision prompt."""
    chunks: list[str] = []
    for step_id, output in outputs.items():
        if not isinstance(output, str):
            continue
        if "http://" not in output and "https://" not in output:
            continue
        chunks.append(f"[{step_id}]\n{output.strip()}")
    return _clip_block("\n\n".join(chunks), max_chars)


def build_publish_revision_prompt(
    requirement: str,
    draft: str,
    issues: tuple[str, ...] | list[str],
    evidence: str,
    *,
    max_chars: int = _REVISION_PROMPT_MAX_CHARS,
) -> str:
    """Build a task-neutral prompt template for a publish-gate revision pass.

    The Studio source template stays generic, but the generated report must be
    specific to the user's request and the evidence already available to the run.
    """
    issue_text = "\n".join(f"- {issue}" for issue in issues) or "- No explicit issue text."
    evidence_text = _clip_block(evidence, max_chars)
    draft_text = _clip_block(draft, max_chars)
    req_text = _clip_block(requirement, max_chars // 2)

    return (
        "You are revising a research report after a publish-readiness gate failed.\n"
        "Use only the user's request, the existing draft, and the evidence excerpts below.\n"
        "Do not invent source URLs, citations, quotes, data, or named sources. Cite only URLs "
        "that appear verbatim in the evidence excerpts. If the evidence is insufficient, say "
        "what remains unverified in a Limitations or Open Questions section.\n"
        "Make the report specific to the user's task, topic, audience, and evidence. Do not "
        "apply any fixed conclusion, fixed fallback answer, or example-specific prose from "
        "the report-generator implementation.\n\n"
        "Required output: publishable Markdown with clear sections:\n"
        "- Executive Summary: answer the user's request using the evidence as a whole.\n"
        "- Evidence-Backed Analysis: synthesize the fetched findings into patterns, "
        "trade-offs, and implications; do not merely list sources.\n"
        "- Recommendations or Next Steps when useful for the request.\n"
        "- Limitations, Caveats, or Reflection: state what remains uncertain, missing, "
        "or not fully verified.\n"
        "- References: include only source URLs present in the evidence excerpts.\n\n"
        "Use every relevant fetched finding. If multiple findings come from the same URL, "
        "cover their distinct claims in the analysis rather than citing the URL once and "
        "moving on.\n\n"
        f"USER REQUEST:\n{req_text}\n\n"
        f"PUBLISH-GATE ISSUES:\n{issue_text}\n\n"
        f"FAILED DRAFT:\n{draft_text}\n\n"
        f"EVIDENCE EXCERPTS:\n{evidence_text}\n\n"
        "Return only the revised Markdown report."
    )
