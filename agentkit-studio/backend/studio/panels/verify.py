"""studio.panels.verify — verification panel (SPEC §5.5 #6).

Runs ``agentkit.quality.verify`` over the final output. The PURE tier
(extract_claims + find_uncited) needs no network, so this panel always produces
a result. Each ``VerifyFinding`` becomes ``{claim, supported, sources}``;
``supported`` is False for any finding (a finding IS a problem); uncited claims
are surfaced separately as bare claim strings.
"""

from __future__ import annotations

from agentkit.quality.verify import VerifyFinding, extract_claims, find_uncited, verify

from studio.events import VerifyEvent


def build_verify_event(final_output: str) -> VerifyEvent:
    """Verify ``final_output`` and pack a ``VerifyEvent`` (pure tier only)."""
    findings: list[VerifyFinding] = verify(final_output)
    finding_dicts = [
        {
            "claim": f.claim,
            "supported": False,  # a finding always flags a problem
            "issue": f.issue,
            "severity": f.severity,
            "sources": [f.url] if f.url else [],
        }
        for f in findings
    ]

    # Surface uncited claims as bare strings (drawn from the pure extractor so the
    # panel shows them even when verify() returns other-severity findings too).
    claims = extract_claims(final_output)
    uncited = [fnd.claim for fnd in find_uncited(claims)]

    return VerifyEvent(findings=finding_dicts, uncited=uncited)
