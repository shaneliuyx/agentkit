from __future__ import annotations

import json

import pytest

from studio import research_quality_mvp as rf_mvp
from studio.research_quality_mvp import evaluate, evaluate_gate_health


def test_summary_mechanism_words_ignores_substring_subject_match() -> None:
    # 'pi' is a substring of 'apis' — a Craft-only sentence must NOT be scanned as
    # a Pi+Craft relationship sentence and have its words harvested as ungrounded
    # mechanism. Word-boundary matching fixes the false positive.
    artifact = "## Executive Summary\n\nCraft connects to external APIs and a Chromium browser."
    assert rf_mvp._summary_mechanism_words(artifact, ["Pi", "Craft"]) == set()


def test_mvp_flags_current_recovery_blockers() -> None:
    artifact = """
# Report

## Executive Summary

Alpha and Beta integrate through a hidden adapter.

```mermaid
flowchart TD
  A["Alpha"] --> B["The"]
```

### RESEARCH_FINDING

SEARCH: leaked
"""
    claims = [
        {"claim": "Alpha exposes an API.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta exposes a CLI.", "subjects": ["Beta"], "url": "https://example.com/b"},
    ]

    result = evaluate(
        artifact=artifact,
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha", "Beta"],
    )

    assert result["passed"] is False
    failed = {check["name"] for check in result["checks"] if not check["passed"]}
    assert failed == {
        "relationship_evidence",
        "diagram_label_quality",
        "summary_mechanism_grounding",
        "internal_marker_leaks",
    }


def test_mvp_accepts_evidence_backed_report() -> None:
    artifact = """
# Report

## Executive Summary

Alpha and Beta are connected by a documented API bridge.

```mermaid
flowchart TD
  A["Alpha"] --> B["API Bridge"]
  B --> C["Beta"]
```
"""
    claims = [
        {"claim": "Alpha exposes an API.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta exposes a CLI.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {
            "claim": "Alpha and Beta integrate through an API bridge.",
            "subjects": ["Alpha", "Beta"],
            "url": "https://example.com/ab",
        },
    ]

    result = evaluate(
        artifact=artifact,
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha", "Beta"],
    )

    assert result["passed"] is True
    assert {check["name"]: check["passed"] for check in result["checks"]} == {
        "subject_evidence": True,
        "relationship_evidence": True,
        "n_subject_relationship_evidence": True,
        "diagram_label_quality": True,
        "summary_mechanism_grounding": True,
        "internal_marker_leaks": True,
    }


def test_mvp_rejects_inline_internal_markers() -> None:
    claims = [
        {"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"},
    ]

    result = evaluate(
        artifact="The report leaked RESEARCH_FINDING inline.",
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha"],
    )

    assert _passed(result, "internal_marker_leaks") is False


def test_mvp_requires_structured_claim_url() -> None:
    claims = [
        {"claim": "Alpha source is https://example.com/a.", "subjects": ["Alpha"]},
    ]

    result = evaluate(
        artifact="Alpha has evidence.",
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha"],
    )

    assert _passed(result, "subject_evidence") is False


def test_mvp_rejects_specific_but_uncited_diagram_labels() -> None:
    claims = [
        {"claim": "Alpha exposes an API.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta exposes a CLI.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {
            "claim": "Alpha and Beta integrate through an API bridge.",
            "subjects": ["Alpha", "Beta"],
            "url": "https://example.com/ab",
        },
    ]

    result = evaluate(
        artifact='```mermaid\nflowchart TD\n  A["Alpha"] --> X["Uncited Architecture Layer"] --> B["Beta"]\n```',
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha", "Beta"],
    )

    assert _passed(result, "diagram_label_quality") is False


def test_mvp_rejects_filler_diagram_labels_even_when_claim_words_overlap() -> None:
    claims = [
        {
            "claim": "The repository contains over 100 files and various examples.",
            "subjects": ["Alpha"],
            "url": "https://example.com/a",
        }
    ]

    result = evaluate(
        artifact=(
            "```mermaid\n"
            "flowchart TD\n"
            '  A["Alpha"] --> B["Contains"] --> C["Over"]\n'
            "```"
        ),
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha"],
    )

    assert _passed(result, "diagram_label_quality") is False


def test_mvp_requires_all_subject_relationship_evidence_for_three_subjects() -> None:
    claims = [
        {"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta has evidence.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {"claim": "Gamma has evidence.", "subjects": ["Gamma"], "url": "https://example.com/c"},
        {"claim": "Alpha and Beta share an API.", "subjects": ["Alpha", "Beta"], "url": "https://example.com/ab"},
        {"claim": "Alpha and Gamma share a queue.", "subjects": ["Alpha", "Gamma"], "url": "https://example.com/ac"},
        {"claim": "Beta and Gamma share events.", "subjects": ["Beta", "Gamma"], "url": "https://example.com/bc"},
    ]

    result = evaluate(
        artifact="Alpha, Beta, and Gamma operate together.",
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha", "Beta", "Gamma"],
    )

    assert _passed(result, "relationship_evidence") is True
    assert _passed(result, "n_subject_relationship_evidence") is False


def test_mvp_rejects_uncorroborated_summary_relationship_mechanism() -> None:
    claims = [
        {"claim": "Alpha exposes an API.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta exposes a CLI.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {
            "claim": "Alpha and Beta are discussed together in deployment notes.",
            "subjects": ["Alpha", "Beta"],
            "url": "https://example.com/ab",
        },
    ]

    result = evaluate(
        artifact="## Executive Summary\n\nAlpha and Beta integrate through a hidden adapter.",
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha", "Beta"],
    )

    assert _passed(result, "relationship_evidence") is True
    assert _passed(result, "summary_mechanism_grounding") is False


def test_mvp_rejects_partially_grounded_summary_relationship_mechanism() -> None:
    claims = [
        {"claim": "Alpha exposes an API.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta exposes a CLI.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {
            "claim": "Alpha and Beta integrate through an API bridge.",
            "subjects": ["Alpha", "Beta"],
            "url": "https://example.com/ab",
        },
    ]

    result = evaluate(
        artifact="## Executive Summary\n\nAlpha and Beta integrate through a hidden adapter.",
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha", "Beta"],
    )

    assert _passed(result, "summary_mechanism_grounding") is False


def test_mvp_rejects_uncited_diagram_edge_labels() -> None:
    claims = [
        {"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta has evidence.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {
            "claim": "Alpha and Beta share an API bridge.",
            "subjects": ["Alpha", "Beta"],
            "url": "https://example.com/ab",
        },
    ]

    result = evaluate(
        artifact='```mermaid\nflowchart TD\n  A["Alpha"] -->|Hidden Adapter| B["Beta"]\n```',
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha", "Beta"],
    )

    assert _passed(result, "diagram_label_quality") is False


def test_mvp_rejects_diagram_labels_supported_only_by_uncited_claims() -> None:
    claims = [
        {"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta has evidence.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {
            "claim": "Alpha and Beta share an API bridge.",
            "subjects": ["Alpha", "Beta"],
            "url": "https://example.com/ab",
        },
        {"claim": "Alpha and Beta use a hidden adapter.", "subjects": ["Alpha", "Beta"]},
    ]

    result = evaluate(
        artifact='```mermaid\nflowchart TD\n  A["Alpha"] --> X["Hidden Adapter"] --> B["Beta"]\n```',
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha", "Beta"],
    )

    assert _passed(result, "diagram_label_quality") is False


def test_mvp_rejects_partially_grounded_compound_diagram_labels() -> None:
    claims = [
        {"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta has evidence.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {
            "claim": "Alpha and Beta share an API bridge.",
            "subjects": ["Alpha", "Beta"],
            "url": "https://example.com/ab",
        },
    ]

    result = evaluate(
        artifact='```mermaid\nflowchart TD\n  A["Alpha"] --> X["API Adapter"] --> B["Beta"]\n```',
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha", "Beta"],
    )

    assert _passed(result, "diagram_label_quality") is False


def test_mvp_accepts_summary_relationship_terms_fully_grounded_in_claims() -> None:
    claims = [
        {"claim": "Alpha exposes an API.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta exposes a CLI.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {
            "claim": "Alpha and Beta integrate through an API bridge.",
            "subjects": ["Alpha", "Beta"],
            "url": "https://example.com/ab",
        },
    ]

    result = evaluate(
        artifact="## Executive Summary\n\nAlpha and Beta integrate through an API bridge.",
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha", "Beta"],
    )

    assert _passed(result, "summary_mechanism_grounding") is True


def test_mvp_accepts_pairwise_summary_mechanism_with_all_subject_claim_present() -> None:
    claims = [
        {"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta has evidence.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {"claim": "Gamma has evidence.", "subjects": ["Gamma"], "url": "https://example.com/c"},
        {
            "claim": "Alpha and Beta integrate through an API bridge.",
            "subjects": ["Alpha", "Beta"],
            "url": "https://example.com/ab",
        },
        {
            "claim": "Alpha, Beta, and Gamma share deployment evidence.",
            "subjects": ["Alpha", "Beta", "Gamma"],
            "url": "https://example.com/abc",
        },
    ]

    result = evaluate(
        artifact="## Executive Summary\n\nAlpha and Beta integrate through an API bridge.",
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=["Alpha", "Beta", "Gamma"],
    )

    assert _passed(result, "summary_mechanism_grounding") is True


@pytest.mark.parametrize(
    ("artifact", "claims", "subjects", "failed_gate"),
    [
        (
            "Alpha report.",
            [{"claim": "Alpha source is https://example.com/a.", "subjects": ["Alpha"]}],
            ["Alpha"],
            "subject_evidence",
        ),
        (
            "Alpha and Beta are related.",
            [
                {"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"},
                {"claim": "Beta has evidence.", "subjects": ["Beta"], "url": "https://example.com/b"},
            ],
            ["Alpha", "Beta"],
            "relationship_evidence",
        ),
        (
            '```mermaid\nflowchart TD\n  A["Alpha"] --> X["Uncited Layer"] --> B["Beta"]\n```',
            [
                {"claim": "Alpha exposes an API.", "subjects": ["Alpha"], "url": "https://example.com/a"},
                {"claim": "Beta exposes a CLI.", "subjects": ["Beta"], "url": "https://example.com/b"},
                {
                    "claim": "Alpha and Beta integrate through an API bridge.",
                    "subjects": ["Alpha", "Beta"],
                    "url": "https://example.com/ab",
                },
            ],
            ["Alpha", "Beta"],
            "diagram_label_quality",
        ),
        (
            "Alpha evidence. SEARCH leaked inline.",
            [{"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"}],
            ["Alpha"],
            "internal_marker_leaks",
        ),
    ],
)
def test_gate_canaries_fail_when_a_gate_is_too_loose(
    artifact: str,
    claims: list[dict],
    subjects: list[str],
    failed_gate: str,
) -> None:
    result = evaluate(
        artifact=artifact,
        claims_jsonl="\n".join(json.dumps(c) for c in claims),
        subjects=subjects,
    )

    assert _passed(result, failed_gate) is False


def test_gate_health_reports_default_canaries_as_healthy() -> None:
    result = evaluate_gate_health()

    assert result["passed"] is True
    assert result["checks"]


def test_gate_health_detects_too_loose_gate() -> None:
    def always_pass(*, artifact: str, claims_jsonl: str, subjects: list[str]) -> dict:
        return {
            "passed": True,
            "checks": [
                {"name": name, "passed": True, "detail": "forced"}
                for name in (
                    "subject_evidence",
                    "relationship_evidence",
                    "n_subject_relationship_evidence",
                    "diagram_label_quality",
                    "summary_mechanism_grounding",
                    "internal_marker_leaks",
                )
            ],
        }

    result = evaluate_gate_health(evaluator=always_pass)

    assert result["passed"] is False
    assert any("unexpected pass" in check["detail"] for check in result["checks"])


def test_gate_health_detects_too_strict_gate() -> None:
    def always_fail(*, artifact: str, claims_jsonl: str, subjects: list[str]) -> dict:
        return {
            "passed": False,
            "checks": [
                {"name": name, "passed": False, "detail": "forced"}
                for name in (
                    "subject_evidence",
                    "relationship_evidence",
                    "n_subject_relationship_evidence",
                    "diagram_label_quality",
                    "summary_mechanism_grounding",
                    "internal_marker_leaks",
                )
            ],
        }

    result = evaluate_gate_health(evaluator=always_fail)

    assert result["passed"] is False
    assert any("unexpected fail" in check["detail"] for check in result["checks"])


def _passed(result: dict, name: str) -> bool:
    return next(check["passed"] for check in result["checks"] if check["name"] == name)
