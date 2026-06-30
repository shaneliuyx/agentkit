from dataclasses import dataclass
from typing import Dict, List


WEIGHTS = {
    "scope_and_framing": 8,
    "toc_completeness": 7,
    "source_quality": 12,
    "citation_integrity": 12,
    "evidence_synthesis": 12,
    "analytical_depth": 10,
    "practical_usefulness": 10,
    "code_quality": 8,
    "diagrams_and_tables": 6,
    "readability": 6,
    "reflection_limitations": 5,
    "governance_safety": 4,
}


HARD_FAILS = {
    "missing_citations",
    "major_unverified_claim",
    "misrepresented_source",
    "broken_code_not_disclosed",
    "missing_limitations",
    "unsafe_tool_action",
    "does_not_answer_question",
    "excessive_copied_text",
}


@dataclass
class ScoreResult:
    total_score: float
    decision: str
    hard_fails: List[str]
    category_scores: Dict[str, float]


def score_report(category_scores_0_to_1: Dict[str, float], hard_fails: List[str]) -> ScoreResult:
    """
    category_scores_0_to_1:
        Each category should be scored from 0.0 to 1.0.
        Example: {"source_quality": 0.8, "citation_integrity": 0.9}

    hard_fails:
        List of hard-fail keys, such as ["missing_citations"].
    """
    unknown = set(category_scores_0_to_1) - set(WEIGHTS)
    if unknown:
        raise ValueError(f"Unknown score categories: {unknown}")

    total = 0.0
    weighted_scores = {}

    for category, weight in WEIGHTS.items():
        raw = category_scores_0_to_1.get(category, 0.0)
        raw = max(0.0, min(1.0, raw))
        weighted = raw * weight
        weighted_scores[category] = weighted
        total += weighted

    invalid_hard_fails = set(hard_fails) - HARD_FAILS
    if invalid_hard_fails:
        raise ValueError(f"Unknown hard fail types: {invalid_hard_fails}")

    if hard_fails:
        decision = "BLOCKED: hard fail exists"
    elif total >= 90:
        decision = "PUBLISH_READY"
    elif total >= 80:
        decision = "MINOR_REVISION_REQUIRED"
    elif total >= 70:
        decision = "MAJOR_REVISION_REQUIRED"
    else:
        decision = "REWRITE_REQUIRED"

    return ScoreResult(
        total_score=round(total, 2),
        decision=decision,
        hard_fails=hard_fails,
        category_scores=weighted_scores,
    )


if __name__ == "__main__":
    example_scores = {
        "scope_and_framing": 1.0,
        "toc_completeness": 0.9,
        "source_quality": 0.85,
        "citation_integrity": 0.9,
        "evidence_synthesis": 0.8,
        "analytical_depth": 0.85,
        "practical_usefulness": 0.9,
        "code_quality": 0.8,
        "diagrams_and_tables": 0.9,
        "readability": 0.95,
        "reflection_limitations": 0.8,
        "governance_safety": 0.75,
    }

    result = score_report(example_scores, hard_fails=[])
    print(result)
