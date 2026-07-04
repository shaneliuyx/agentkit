"""§9 step 1 — publish-revision accept guard (`_publish_revision_regressed`).

Pins Bug B: a whole-doc publish rewrite that shrinks / de-cites / regresses the
score must be REJECTED, keeping the pre-revision text. Fixtures are the REAL
production before/after pair from session s_0a3669434b76 (21.4KB pre-revision
vs the 9.5KB damaged final that shipped).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from studio.runner import _publish_revision_regressed

FIX = Path(__file__).parent / "fixtures" / "publish_revision"
PRE = (FIX / "pre_revision_21k.md").read_text(encoding="utf-8")
SHRUNK = (FIX / "shrunk_revision_9k.md").read_text(encoding="utf-8")
SECTIONS = ["Executive Summary", "Scope and Research Questions", "Background and Context",
            "Key Findings", "Evidence and Analysis", "Implications or Recommendations",
            "Limitations and Uncertainty", "References"]


def test_real_shrinking_rewrite_is_rejected():
    # Arrange: the actual production pre/post pair. Act:
    reasons = _publish_revision_regressed(
        PRE, SHRUNK, required_sections=SECTIONS, verified_pre=None, verified_rev=None
    )
    # Assert: rejected, and the shrink is named.
    assert reasons, "the 21.4KB→9.5KB rewrite must be rejected"
    assert "shrink-words" in reasons
    assert "shrink-bytes" in reasons


def test_identical_text_is_accepted():
    assert _publish_revision_regressed(
        PRE, PRE, required_sections=SECTIONS, verified_pre=None, verified_rev=None
    ) == []


def test_de_citation_is_rejected():
    # Same length, but a citation URL removed → de-cite guard fires.
    pre = "Intro para one two three.\nA claim (https://example.com/a).\nAnother (https://example.com/b).\n"
    rev = "Intro para one two three.\nA claim (https://example.com/a).\nAnother claim here now.\n"
    reasons = _publish_revision_regressed(
        pre, rev, required_sections=None, verified_pre=None, verified_rev=None
    )
    assert "de-cite-urls" in reasons


def test_verified_url_drop_is_rejected():
    reasons = _publish_revision_regressed(
        "same text", "same text",
        required_sections=None,
        verified_pre=["https://a.com", "https://b.com"],
        verified_rev=["https://a.com"],
    )
    assert "de-cite-verified" in reasons


def test_clean_additive_revision_is_accepted():
    # A revision that GROWS with a new grounded citation must pass all guards.
    rev = PRE + "\n\nAdded grounded analysis paragraph (https://newsource.example.com/x).\n"
    assert _publish_revision_regressed(
        rev, rev, required_sections=SECTIONS, verified_pre=None, verified_rev=None
    ) == []


def test_gate_error_fails_closed(monkeypatch):
    # A raising rubric_score must NO-OP (reject), never fail-open-accept (§9 risk 4).
    import studio.rubric

    def boom(*a, **k):
        raise RuntimeError("scorer down")

    monkeypatch.setattr(studio.rubric, "rubric_score", boom)
    reasons = _publish_revision_regressed(
        PRE, PRE, required_sections=SECTIONS, verified_pre=None, verified_rev=None
    )
    assert reasons == ["gate-error"]


def test_cfp_in_structural_retry_prompt():
    # §9 step 4: the structural-retry prompt now carries Mermaid Safe Mode.
    from studio.runner import _editor_structural_retry_prompt

    p = _editor_structural_retry_prompt("build a diagram", ["add an architecture diagram"])
    assert "MERMAID SAFE MODE" in p
    assert "flowchart TD" in p
    assert "classDef" in p  # the no-styling ban is present


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
