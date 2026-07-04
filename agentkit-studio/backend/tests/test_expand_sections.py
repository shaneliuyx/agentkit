"""§9 step 5 — guarded depth expansion (studio.expand_sections)."""
from __future__ import annotations

from pathlib import Path

import pytest

from studio.expand_sections import expand_underdeveloped_sections
from studio.rubric import rubric_score

FIX = Path(__file__).parent / "fixtures" / "publish_revision"
GOOD = (FIX / "pre_revision_21k.md").read_text(encoding="utf-8")      # well-developed
THIN = (FIX / "shrunk_revision_9k.md").read_text(encoding="utf-8")    # underdeveloped
SECTIONS = ["Executive Summary", "Scope and Research Questions", "Background and Context",
            "Key Findings", "Evidence and Analysis", "Implications or Recommendations",
            "Limitations and Uncertainty", "References"]

UNUSED_URL = "https://unused-source.example.com/deep-dive"
EVIDENCE = {"source-x": f"Detailed material about Pi and Craft agent loops. See {UNUSED_URL}\n" * 5}


def _grounded_chat(_prompt: str) -> str:
    # 40-word grounded paragraph ending in the source URL (keeps prose-per-url up).
    return (
        "Pi and Craft expose complementary primitives for agent construction, and the "
        "source details how loop orchestration, tool routing, and state handling compose "
        "into a working development workflow that scales across tasks and remains "
        f"debuggable in production settings for real teams ({UNUSED_URL})."
    )


def test_noop_on_well_developed_report():
    out, stats = expand_underdeveloped_sections(
        text=GOOD, requirement="req", evidence_outputs=EVIDENCE,
        verified_urls=None, required_sections=SECTIONS,
        chat=_grounded_chat, rubric_score=rubric_score,
    )
    assert out == GOOD and stats["added"] == 0  # nothing under the floor → no-op


def test_fires_on_thin_report_with_unused_source():
    out, stats = expand_underdeveloped_sections(
        text=THIN, requirement="develop agents with Pi and Craft", evidence_outputs=EVIDENCE,
        verified_urls=None, required_sections=SECTIONS,
        chat=_grounded_chat, rubric_score=rubric_score,
    )
    assert stats["underdeveloped"], "thin report must have sub-floor sections"
    assert stats["unused_sources"] == ["source-x"]
    assert stats["added"] >= 1
    assert len(out.split()) > len(THIN.split())        # grew
    assert UNUSED_URL in out                            # grounded
    assert stats["rejected_reason"] is None


def test_paragraph_without_url_is_dropped():
    out, stats = expand_underdeveloped_sections(
        text=THIN, requirement="req", evidence_outputs=EVIDENCE,
        verified_urls=None, required_sections=SECTIONS,
        chat=lambda _p: "A paragraph with no citation url at all.", rubric_score=rubric_score,
    )
    assert stats["added"] == 0 and out == THIN  # URL guard drops it → no-op


def test_fabricated_url_in_paragraph_is_rejected():
    # P1-c: a paragraph that carries the source URL but ALSO a fabricated URL not
    # in the source's evidence block must be dropped, not merged into the report.
    def _chat_with_fabrication(_p: str) -> str:
        return f"Grounded claim ({UNUSED_URL}) plus a bogus figure (https://fabricated.evil.example/x)."

    out, stats = expand_underdeveloped_sections(
        text=THIN, requirement="req", evidence_outputs=EVIDENCE,
        verified_urls=None, required_sections=SECTIONS,
        chat=_chat_with_fabrication, rubric_score=rubric_score,
    )
    assert stats["added"] == 0 and out == THIN


def test_noop_when_all_sources_already_cited():
    # Evidence whose URL is already cited >=2x in the report → nothing unused.
    already = list(__import__("re").findall(r"https?://[^\s)]+", THIN))
    if len(already) < 1:
        pytest.skip("fixture has no citations")
    ev = {"source-y": f"text {already[0]} and again {already[0]}"}
    out, stats = expand_underdeveloped_sections(
        text=THIN, requirement="req", evidence_outputs=ev,
        verified_urls=None, required_sections=SECTIONS,
        chat=_grounded_chat, rubric_score=rubric_score,
    )
    assert stats["unused_sources"] == [] and out == THIN


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
