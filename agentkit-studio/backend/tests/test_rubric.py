"""Research-report rubric: scoring standard + GUI-tunable weights + deliverable template
(DESIGN §11.6). The headline guarantee: the rubric SEPARATES the real good vs thin report
that the live LLM judge tied (haiku AND sonnet) — deterministically and reproducibly.
"""
import pathlib

from studio.rubric import (
    DEFAULT_TEMPLATE,
    SCORING_CATEGORIES,
    default_scoring_matrix,
    rubric_score,
    rubric_scorecard_100,
    remaining_scoring_matrix,
    scorecard_weaknesses,
    score_breakdown,
    resolve_scoring_matrix,
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


def test_scoring_matrix_uses_shared_category_vocabulary() -> None:
    matrix = default_scoring_matrix("deep_technical")

    assert set(row["category"] for row in matrix) <= set(SCORING_CATEGORIES)
    assert abs(sum(float(row["points"]) for row in matrix) - 100.0) < 1e-9
    assert len({row["category"] for row in matrix}) == 12
    assert next(row for row in matrix if row["category"] == "Code quality / examples")["points"] == 8.0


def test_scoring_matrix_omits_categories_unrelated_to_template() -> None:
    matrix = default_scoring_matrix("deep_technical", ["Executive Summary", "References"])
    categories = {row["category"] for row in matrix}

    assert "Code quality / examples" not in categories
    assert "Diagrams and tables" not in categories
    assert "Citation integrity" in categories
    assert sum(float(row["points"]) for row in matrix) == 100.0


def test_scoring_matrix_normalizes_unknown_payloads() -> None:
    matrix = resolve_scoring_matrix(
        [
            {"category": "Citation integrity", "points": 12, "signal": "verification"},
            {"category": "bogus", "weight": 99, "signal": "sourcing"},
        ]
    )

    assert [row["category"] for row in matrix] == ["Citation integrity"]
    assert sum(float(row["points"]) for row in matrix) == 100.0
    assert next(row for row in matrix if row["category"] == "Citation integrity")["applicable"] is True


def test_explicit_scoring_matrix_drops_unrelated_template_items() -> None:
    matrix = resolve_scoring_matrix(
        [{"category": "Code quality / examples", "points": 8}],
        template=["Executive Summary", "References"],
    )

    assert matrix == []


def test_scorecard_100_is_projection_not_optimizer_replacement() -> None:
    text = _good()
    score = rubric_score(text, required_sections=DEFAULT_TEMPLATE)
    scorecard = rubric_scorecard_100(
        text,
        required_sections=DEFAULT_TEMPLATE,
        scoring_matrix=default_scoring_matrix("general", DEFAULT_TEMPLATE),
    )

    assert 0 <= score <= 1
    assert scorecard["base_score"] == score
    assert set(scorecard["signals"]) == {
        "sourcing",
        "verification",
        "evidence_depth",
        "analysis",
        "structure",
        "methodology",
    }
    assert 0 <= scorecard["score"] <= 100
    assert 0 < len(scorecard["categories"]) < 12


def test_practical_usefulness_requires_concrete_actions_not_just_analysis() -> None:
    matrix = [{"category": "Practical usefulness", "points": 10, "signal": "analysis"}]
    analytical = (
        "## Analysis\n\n"
        "However, this suggests an important implication. Compared with the baseline, "
        "the trade-off is clearer; therefore the report explains what the findings "
        "mean together but gives no operational plan."
    )
    practical = (
        "## Implementation Risks\n\n"
        "Recommendations: pilot the change first, assign an owner, measure adoption "
        "with a metric, track cost and dependency risk, and keep a rollback checklist "
        "for mitigation."
    )

    weak = rubric_scorecard_100(analytical, scoring_matrix=matrix)
    strong = rubric_scorecard_100(practical, scoring_matrix=matrix)

    assert weak["categories"][0]["signal_score"] < 0.75
    assert strong["categories"][0]["signal_score"] >= 0.75
    assert "implementation risks" in scorecard_weaknesses(
        weak, ["Implementation Risks"], min_signal_score=0.75
    )[0]


def test_scorecard_weaknesses_are_section_routable() -> None:
    scorecard = {
        "categories": [
            {
                "category": "Citation integrity",
                "points": 20.0,
                "score": 5.0,
                "signal_score": 0.25,
            },
            {
                "category": "Readability and formatting",
                "points": 10.0,
                "score": 4.0,
                "signal_score": 0.4,
            },
        ]
    }

    weaknesses = scorecard_weaknesses(
        scorecard,
        ["Executive Summary", "References"],
        min_signal_score=0.75,
    )

    assert any(w.startswith("[## References]") and "Citation integrity" in w for w in weaknesses)
    assert any(w.startswith("[## Executive Summary]") and "Readability" in w for w in weaknesses)


def test_remaining_scoring_matrix_drops_achieved_rows() -> None:
    scorecard = {
        "categories": [
            {
                "category": "Citation integrity",
                "points": 20.0,
                "signal": "verification",
                "signal_score": 0.25,
            },
            {
                "category": "Readability and formatting",
                "points": 10.0,
                "signal": "structure",
                "signal_score": 0.95,
            },
        ]
    }

    remaining = remaining_scoring_matrix(scorecard, min_signal_score=0.75)

    assert [row["category"] for row in remaining] == ["Citation integrity"]


# --- relevance_penalty (studio.relevance) — a plain precomputed float --------
#
# rubric_score/rubric_scorecard_100 make NO network call themselves — the LLM
# binary classification happens once, upstream (studio.relevance), by whoever
# already holds a live client. These tests only pin the pure arithmetic: the
# penalty derates the score and defaults to a no-op for existing callers.
# Reuses the module-level ``_good()`` fixture helper defined above.

def test_relevance_penalty_defaults_to_no_penalty() -> None:
    text = _good()
    assert rubric_score(text) == rubric_score(text, relevance_penalty=0.0)


def test_relevance_penalty_derates_rubric_score() -> None:
    text = _good()
    base = rubric_score(text)
    penalized = rubric_score(text, relevance_penalty=0.5)
    assert penalized < base
    assert penalized == round(max(0.0, base - 0.5), 4)


def test_relevance_penalty_clamped_never_negative() -> None:
    text = _good()
    assert rubric_score(text, relevance_penalty=10.0) == 0.0
    # A negative penalty (malformed upstream input) must not BOOST the score.
    assert rubric_score(text, relevance_penalty=-1.0) == rubric_score(text)


def test_relevance_penalty_derates_scorecard_100_total_proportionally() -> None:
    text = _good()
    matrix = default_scoring_matrix()
    base_card = rubric_scorecard_100(text, scoring_matrix=matrix)
    penalized_card = rubric_scorecard_100(text, scoring_matrix=matrix, relevance_penalty=0.5)
    assert penalized_card["max_score"] == base_card["max_score"]  # ceiling unchanged
    assert penalized_card["score"] < base_card["score"]
    expected = max(0.0, base_card["score"] - 0.5 * base_card["max_score"])
    assert abs(penalized_card["score"] - round(expected, 2)) < 0.01
    assert penalized_card["base_score"] < base_card["base_score"]
