from studio.report_quality import (
    build_publish_revision_prompt,
    build_revision_evidence_text,
    evaluate_publish_readiness,
)


def test_non_report_request_passes_publish_gate() -> None:
    result = evaluate_publish_readiness("compare redis and postgres", "Redis is faster.")

    assert result.publish_ready is True
    assert result.issues == ()


def test_report_without_requested_evidence_or_topic_fails() -> None:
    requirement = (
        "Write a concise generic research report about catalog management for agent loops "
        "and skills. Cover local and remote catalogs, operational risks, and concrete "
        "implementation steps. Use fetched evidence when tools are available."
    )
    text = (
        "Based on the research provided, workflows are deterministic systems while agents "
        "are non-deterministic systems. Workflows have higher auditability."
    )

    result = evaluate_publish_readiness(requirement, text)

    assert result.publish_ready is False
    assert any("no source URL" in issue for issue in result.issues)
    assert any("too short" in issue for issue in result.issues)
    assert any("misses important request terms" in issue for issue in result.issues)


def test_cited_on_topic_report_passes_publish_gate() -> None:
    requirement = (
        "Write a concise research report about catalog management for agent loops and skills. "
        "Use fetched evidence."
    )
    text = """
## Executive Summary

Catalog management for agent loops and skills should separate local catalogs from
remote catalogs, keep signed version metadata, and record which loop or skill was
used for every run. The local catalog gives operators deterministic fallback and
reviewed assets, while a remote catalog allows teams to distribute updated loops
and skills across workspaces. Evidence from the fetched source supports using a
managed catalog rather than ad hoc prompt snippets (https://example.com/catalog).

## Risks and Recommendations

Operational risks include version drift, stale skills, unreviewed remote entries,
and missing provenance. Recommended implementation steps are to validate imported
assets, require enable or disable state, audit source URLs, and expose catalog
selection in the UI. These controls keep agent loops and skills discoverable while
preserving governance and rollback paths.
"""

    result = evaluate_publish_readiness(
        requirement,
        text,
        verified_urls=["https://example.com/catalog"],
    )

    assert result.publish_ready is True


def test_publish_revision_prompt_is_generic_and_evidence_bounded() -> None:
    prompt = build_publish_revision_prompt(
        "Write a research report about battery recycling policy. Use fetched evidence.",
        "Short draft.",
        ["[publish-gate] Final output is too short."],
        "Evidence excerpt with https://example.com/battery-policy " + ("x" * 20_000),
        max_chars=200,
    )

    assert "battery recycling policy" in prompt
    assert "[publish-gate] Final output is too short." in prompt
    assert "https://example.com/battery-policy" in prompt
    assert "Do not invent source URLs" in prompt
    assert "specific to the user's task" in prompt
    assert "[truncated]" in prompt
    assert "Catalog management for agent loops" not in prompt
    assert "local catalogs are best suited" not in prompt


def test_revision_evidence_text_uses_only_url_bearing_outputs() -> None:
    evidence = build_revision_evidence_text(
        {
            "scope": "No source here.",
            "fetch": "Useful quote from https://example.com/source.",
            "empty": "",
            "synthesis": "Another source http://example.org/ref.",
        }
    )

    assert "[fetch]" in evidence
    assert "https://example.com/source" in evidence
    assert "[synthesis]" in evidence
    assert "http://example.org/ref" in evidence
    assert "[scope]" not in evidence


def test_revision_evidence_text_is_bounded() -> None:
    evidence = build_revision_evidence_text(
        {"fetch": "https://example.com/source " + ("x" * 1000)},
        max_chars=120,
    )

    assert evidence.endswith("[truncated]")
