"""agentkit.quality.verify — a source-grounding / verification pass.

This implements the feynman Verifier as a reusable, deterministic-first pass
(the library thesis): cheap pure checks always run and need no model; an I/O
tier (URL liveness) and an LLM tier (claim-vs-source support) are injected and
optional.

Tiers:
  1. PURE (no I/O): ``extract_claims`` + ``find_uncited``. Claim/citation
     extraction and uncited detection are deterministic string work.
  2. I/O (injected checker): ``check_links``. URL-liveness uses an injected
     ``UrlChecker``; the default ``HttpUrlChecker`` does a real HEAD request,
     but tests inject a fake so no network is touched.
  3. LLM (injected client): ``check_support``. Whether a source actually
     supports a claim is asked of an injected ``LLMClient``; skipped if None.

Severity grades: ``critical | high | medium | low``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Protocol, runtime_checkable

from agentkit.quality.claim_classifier import ClaimClassifier
from agentkit.types import LLMClient, Message

# Severity ordering for sorting findings (critical first -> low last).
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# A URL with an http/https scheme.
_URL_RE = re.compile(r"https?://[^\s\]\)]+", re.IGNORECASE)
# A bracketed numeric citation marker, e.g. [1] or [12].
_NUM_MARKER_RE = re.compile(r"\[\d+\]")
# A parenthetical (Author, year) marker, e.g. (Smith, 2020) or (Smith et al., 2020).
_AUTHOR_YEAR_RE = re.compile(r"\([A-Z][^()]*,\s*\d{4}[a-z]?\)")
# Segmentation below is STRUCTURAL (markdown + punctuation) with NO
# language-specific keyword lists, so it generalizes to any language's prose.
# A markdown heading line (## ...) — not a factual claim.
_HEADING_RE = re.compile(r"^\s*#{1,6}\s")
# Markdown link text [..], inline decoration, and leading list numbering —
# stripped to expose a line's bare structure.
_MD_LINK_TEXT_RE = re.compile(r"\[[^\]]*\]")
_DECOR_RE = re.compile(r"[*_`#>()\[\]\-]")
_LEAD_LIST_RE = re.compile(r"^[\s\d.)]+")
# A citation line's residual prose (after removing the citation) is at most this
# many words — enough for a label like "Source:" / "来源:" / "Quelle:", far fewer
# than a real assertion. A structural threshold, not a vocabulary.
_CITE_LINE_MAX_WORDS = 4


@dataclass(frozen=True)
class Claim:
    """A single claim extracted from a text, with its citation (if any).

    ``citation`` is a URL or a citation marker, or "" when the claim is
    uncited.
    """

    text: str
    citation: str = ""


@dataclass(frozen=True)
class VerifyFinding:
    """An immutable verification finding.

    Attributes:
        severity: one of critical | high | medium | low.
        claim:    the claim text the finding is about.
        issue:    a short description of the problem.
        url:      the offending URL, when applicable ("" otherwise).
    """

    severity: str
    claim: str
    issue: str
    url: str = ""


@runtime_checkable
class UrlChecker(Protocol):
    """Anything that can report whether a URL is live."""

    def is_live(self, url: str) -> bool:
        ...


class HttpUrlChecker:
    """Default ``UrlChecker``: a short-timeout HTTP HEAD request.

    Any exception, or a 4xx/5xx status, is treated as "not live" (False). This
    is the real-I/O default; tests and self-checks inject a fake instead so no
    network call is made.
    """

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout

    def is_live(self, url: str) -> bool:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return 200 <= int(status) < 400
        except urllib.error.HTTPError as exc:
            return 200 <= int(exc.code) < 400
        except Exception:
            # DNS failure, timeout, connection refused, bad URL, etc.
            return False


# ---------------------------------------------------------------------------
# PURE tier: extraction + uncited detection (no I/O).
# ---------------------------------------------------------------------------

def _find_citation(sentence: str) -> str:
    """Return the first citation found in ``sentence`` (URL or marker), else ""."""
    m = _URL_RE.search(sentence)
    if m:
        return m.group(0).rstrip(".,;")
    m = _NUM_MARKER_RE.search(sentence)
    if m:
        return m.group(0)
    m = _AUTHOR_YEAR_RE.search(sentence)
    if m:
        return m.group(0)
    return ""


def _is_citation_line(part: str) -> bool:
    """True when ``part`` is a citation carrier, not a claim: a "Source:"/"See:"
    label line, or a line that is essentially just a (markdown) link or URL.

    This is what lets a citation placed on its OWN line bind to the claim above
    it, instead of the claim being flagged uncited (the common chat-model shape
    "<claim>.\\n  Source: <url>").
    """
    cite = _find_citation(part)
    if not cite:
        return False
    # Remove the citation + markdown link text + decoration, then ask whether
    # what remains is "mostly nothing": a bare link, or a short "Label:" prefix.
    rest = _MD_LINK_TEXT_RE.sub(" ", part.replace(cite, " "))
    rest = _LEAD_LIST_RE.sub("", _DECOR_RE.sub(" ", rest)).strip()
    if not rest.strip(":").strip():          # nothing but the link (+ maybe ':')
        return True
    head, sep, tail = rest.rpartition(":")    # "<Label>: <link>" in any language
    return bool(sep) and not tail.strip() and len(head.split()) <= _CITE_LINE_MAX_WORDS


def _is_claim(sentence: str) -> bool:
    """Is this a factual ASSERTION worth a citation? STRUCTURAL and
    language-agnostic — uses only markdown/punctuation, no word lists: drops
    markdown headings, questions ("...?"), and bare label/heading lines
    ("...:"). Conservative: anything plainly assertive stays a claim.
    """
    if _HEADING_RE.match(sentence):
        return False
    core = _DECOR_RE.sub("", _LEAD_LIST_RE.sub("", sentence)).strip()
    if not core:                              # pure decoration / blank
        return False
    return not core.endswith(("?", ":"))      # question or bare label → not a claim


def extract_claims(
    text: str, classifier: ClaimClassifier | None = None
) -> list[Claim]:
    """Split ``text`` into sentence-ish claims, detecting a citation.

    A claim's citation is the first http/https URL, ``[n]`` marker, or
    ``(Author, year)`` marker found in the sentence; "" if none is present. A
    citation placed on a SEPARATE line (a "Source:" line or a bare/markdown
    link) binds to the preceding claim rather than becoming a claim of its own —
    so well-cited multi-line answers are not misread as uncited.

    Non-claims are filtered in a deterministic-first cascade: the cheap
    structural ``_is_claim`` (markdown/punctuation, always) runs first, then —
    only on what survives — an optional injected ``classifier`` catches the
    residual tail of marker-less prose non-claims. Pure when ``classifier`` is
    None or pure; otherwise its cost is whatever the adapter is (embeddings,
    etc.).
    """
    if not text or not text.strip():
        return []
    # Split on sentence terminators AND newlines, so a citation on its own line
    # is a separate part we can bind back. Simple and deterministic, not perfect.
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    claims: list[Claim] = []
    for part in parts:
        sentence = part.strip()
        if not sentence:
            continue
        citation = _find_citation(sentence)
        # A pure citation line attaches its URL to the previous claim instead of
        # standing alone (and never becomes a spurious "uncited" claim itself).
        if _is_citation_line(sentence):
            if citation and claims and not claims[-1].citation:
                claims[-1] = replace(claims[-1], citation=citation)
            continue
        if not _is_claim(sentence):
            continue  # cheap structural drop: heading / question / label
        if classifier is not None and not classifier.is_claim(sentence):
            continue  # residual tail: injected (non-LLM) classifier says non-claim
        claims.append(Claim(text=sentence, citation=citation))
    return claims


def find_uncited(claims: list[Claim]) -> list[VerifyFinding]:
    """Flag claims with no citation as ``high`` severity findings. Pure."""
    return [
        VerifyFinding(severity="high", claim=c.text, issue="no citation")
        for c in claims
        if not c.citation
    ]


# ---------------------------------------------------------------------------
# I/O tier: URL liveness via an injected checker.
# ---------------------------------------------------------------------------

def check_links(claims: list[Claim], checker: UrlChecker) -> list[VerifyFinding]:
    """For claims whose citation is a URL, flag dead links as ``critical``.

    URL-liveness is delegated to the injected ``checker`` (real HTTP by
    default, a fake in tests).
    """
    findings: list[VerifyFinding] = []
    for c in claims:
        if c.citation.lower().startswith(("http://", "https://")):
            if not checker.is_live(c.citation):
                findings.append(VerifyFinding(
                    severity="critical",
                    claim=c.text,
                    issue="dead link",
                    url=c.citation,
                ))
    return findings


# ---------------------------------------------------------------------------
# LLM tier: claim-vs-source support via an injected client.
# ---------------------------------------------------------------------------

def check_support(
    claim: Claim,
    source_text: str,
    client: LLMClient,
) -> VerifyFinding | None:
    """Ask the injected ``client`` whether ``source_text`` supports ``claim``.

    Returns a ``high`` severity finding when the source does NOT support the
    claim, or None when it does. The client is injected; this tier is never
    reached when no client is provided (see ``verify``).
    """
    messages: list[Message] = [
        {
            "role": "system",
            "content": (
                "You check whether a SOURCE supports a CLAIM. Answer with a "
                "single word: 'supported' or 'unsupported'."
            ),
        },
        {
            "role": "user",
            "content": (
                f"CLAIM:\n{claim.text}\n\nSOURCE:\n{source_text}\n\n"
                "Does the SOURCE support the CLAIM? Answer 'supported' or "
                "'unsupported'."
            ),
        },
    ]
    response = client.chat(messages)
    verdict = (getattr(response, "text", "") or "").strip().lower()
    if "unsupported" in verdict or verdict.startswith("no"):
        return VerifyFinding(
            severity="high",
            claim=claim.text,
            issue="source does not support claim",
            url=claim.citation,
        )
    return None


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

def verify(
    text: str,
    checker: UrlChecker | None = None,
    client: LLMClient | None = None,
    sources: dict[str, str] | None = None,
    classifier: ClaimClassifier | None = None,
) -> list[VerifyFinding]:
    """Run the source-grounding pass over ``text``.

    Tiers run in order of cost:
      - PURE always: extract claims + flag uncited ones.
      - I/O if ``checker`` given: flag dead links.
      - LLM if ``client`` AND ``sources`` given: for each claim whose citation
        maps to a source in ``sources``, flag unsupported claims.

    Findings are returned severity-graded (critical -> low).

    Args:
        text:    The text to verify.
        checker: Optional injected ``UrlChecker`` (enables the link tier).
        client:  Optional injected ``LLMClient`` (enables the support tier).
        sources: Optional map of {citation -> source_text} for the support
                 tier. Required (with ``client``) for support checks.

    Returns:
        A severity-sorted list of ``VerifyFinding``.
    """
    claims = extract_claims(text, classifier=classifier)
    findings: list[VerifyFinding] = list(find_uncited(claims))

    if checker is not None:
        findings.extend(check_links(claims, checker))

    if client is not None and sources:
        for c in claims:
            if not c.citation:
                continue
            source_text = sources.get(c.citation)
            if source_text is None:
                continue
            finding = check_support(c, source_text, client)
            if finding is not None:
                findings.append(finding)

    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 99))
    return findings


if __name__ == "__main__":
    from agentkit.types import ChatResult

    # PURE: a text with one uncited claim is flagged.
    one_uncited = "The sky is blue. Water boils at 100C [1]."
    findings = verify(one_uncited)
    assert any(f.issue == "no citation" and f.severity == "high"
               for f in findings), findings

    # I/O: a claim citing a URL a FAKE checker marks dead -> critical.
    class _FakeChecker:
        def __init__(self, live: bool) -> None:
            self.live = live

        def is_live(self, url: str) -> bool:
            return self.live

    dead = "Agents are useful https://example.com/dead."
    findings = verify(dead, checker=_FakeChecker(live=False))
    assert any(f.issue == "dead link" and f.severity == "critical"
               and f.url == "https://example.com/dead" for f in findings), findings

    # All cited + all live -> no deterministic findings.
    clean = "Claim one https://a.test/x. Claim two https://b.test/y."
    findings = verify(clean, checker=_FakeChecker(live=True))
    assert findings == [], findings

    # LLM tier: a fake client returning "unsupported" -> high finding.
    class _UnsupportiveClient:
        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            return ChatResult(text="unsupported", total_tokens=2)

    txt = "Cats can fly https://src.test/cats."
    findings = verify(
        txt,
        checker=_FakeChecker(live=True),
        client=_UnsupportiveClient(),
        sources={"https://src.test/cats": "Cats are mammals that cannot fly."},
    )
    assert any(f.issue == "source does not support claim" and f.severity == "high"
               for f in findings), findings

    # Severity sort: critical before high.
    mixed = "Uncited claim. Dead-cite https://example.com/dead."
    findings = verify(mixed, checker=_FakeChecker(live=False))
    severities = [f.severity for f in findings]
    assert severities == sorted(severities, key=lambda s: _SEVERITY_ORDER[s]), severities
    assert severities[0] == "critical", severities

    print("verify self-check OK")
