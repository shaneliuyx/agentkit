"""Research-report rubric: scoring standard + GUI-tunable weights + deliverable template
(DESIGN §11.6). The headline guarantee: the rubric SEPARATES the real good vs thin report
that the live LLM judge tied (haiku AND sonnet) — deterministically and reproducibly.
"""
import pathlib

from studio.rubric import (
    DEFAULT_TEMPLATE,
    rubric_score,
    score_breakdown,
    resolve_weights,
    sections_present,
)


def test_sections_present_is_concept_aware_over_full_text() -> None:
    """Full-text, concept-aware section presence (the source of truth the windowed miner
    can't compute): a synonym heading counts, a genuinely absent section does not."""
    doc = "# R\n## Verified Sources\nx\n## Methodology\ny\n## Conclusion\nz"
    got = {
        s.lower()
        for s in sections_present(
            doc, ["Source References", "Methodology", "Conclusion", "Key Findings"]
        )
    }
    assert "methodology" in got and "conclusion" in got
    assert "source references" in got       # matches "Verified Sources" via shared 'source'
    assert "key findings" not in got        # genuinely absent → correctly not present

FX = pathlib.Path(__file__).parent / "fixtures"


def _good() -> str:
    return (FX / "report_good.md").read_text()


def _thin() -> str:
    return (FX / "report_thin.md").read_text()


def test_rubric_separates_what_the_llm_judge_tied() -> None:
    sg = rubric_score(_good(), required_sections=DEFAULT_TEMPLATE)
    st = rubric_score(_thin(), required_sections=DEFAULT_TEMPLATE)
    assert sg > st, (sg, st)          # live haiku & sonnet both said "tie"; rubric must not
    assert sg > 0.8 and st < 0.6, (sg, st)


def test_rubric_is_deterministic() -> None:
    good = _good()
    assert rubric_score(good) == rubric_score(good)   # model-free, no version drift


def test_resolve_weights_normalizes_and_drops_unknown() -> None:
    w = resolve_weights({"sourcing": 3, "bogus": 9})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert "bogus" not in w
    assert w["sourcing"] == max(w.values())           # boosted criterion dominates


def test_template_coverage_drives_structure_score() -> None:
    doc = "## Executive Summary\nx\n## Conclusion\ny"
    full = score_breakdown(doc, required_sections=["Executive Summary", "Conclusion"])["structure"]
    half = score_breakdown(
        doc, required_sections=["Executive Summary", "Methodology", "Conclusion", "Findings"]
    )["structure"]
    assert full == 1.0 and half == 0.5


def test_gui_weights_shift_the_score() -> None:
    thin = _thin()
    base = rubric_score(thin, required_sections=DEFAULT_TEMPLATE)
    # Down-weight the criteria the thin report fails (verification/evidence) → score rises.
    up = rubric_score(
        thin,
        weights={"sourcing": 1, "verification": 0, "evidence_depth": 0,
                 "structure": 1, "methodology": 1},
        required_sections=DEFAULT_TEMPLATE,
    )
    assert up > base, (base, up)
