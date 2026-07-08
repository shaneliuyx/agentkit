from studio.report_quality import (
    build_review_status,
    build_publish_revision_prompt,
    build_revision_evidence_text,
    combined_publish_issues,
    evaluate_publish_readiness,
    synthesis_depth_issues,
)


def test_non_report_request_passes_publish_gate() -> None:
    result = evaluate_publish_readiness("compare redis and postgres", "Redis is faster.")

    assert result.publish_ready is True
    assert result.issues == ()


def test_review_status_requires_review_for_high_impact_or_weak_evidence() -> None:
    high = build_review_status(
        "Write a policy research report about patient privacy compliance.",
        evidence_count=0,
        weak_evidence_count=1,
        scorecard={"score": 70, "max_score": 100, "categories": []},
        publish_issues=["[publish-gate] Final output has URLs but none were verified."],
        loopdoctor_checks=[{"name": "safe_actions", "status": "pass"}],
    )

    assert high["required"] is True
    assert high["status"] == "REVIEW_REQUIRED"
    assert high["publish_decision"] == "REVIEW_REQUIRED"
    assert "high-impact topic" in high["reasons"]
    assert "no accepted evidence matrix rows" in high["reasons"]
    assert "weak or uncorroborated evidence" in high["reasons"]
    assert "publish/readiness issues" in high["reasons"]


def test_review_status_does_not_block_low_risk_generic_tasks() -> None:
    low = build_review_status(
        "compare redis and postgres",
        evidence_count=0,
        scorecard={"score": 20, "max_score": 100, "categories": []},
    )

    assert low == {
        "required": False,
        "status": "NOT_REQUIRED",
        "publish_decision": "PUBLISH_READY",
        "reviewed": False,
        "reasons": [],
    }


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


def test_internal_pipeline_markers_fail_publish_gate() -> None:
    requirement = "Write a research report about catalog management. Use citations."
    text = """
## Executive Summary

Catalog management needs versioned evidence, source tracking, and governance
controls for every generated report (https://example.com/catalog).

## Evidence and Analysis

### RESEARCH_FINDING

The report should not expose internal pipeline scaffolding to readers. Published
research output must contain synthesized prose rather than worker metadata
(https://example.com/catalog).

## References

- https://example.com/catalog
"""

    result = evaluate_publish_readiness(
        requirement,
        text,
        verified_urls=["https://example.com/catalog"],
    )

    assert result.publish_ready is False
    assert any("internal pipeline markers" in issue for issue in result.issues)


def test_publish_gate_allows_search_as_normal_report_word() -> None:
    requirement = "Write a research report about catalog management. Use citations."
    text = """
## Executive Summary

Catalog search improves local and remote catalog management when it records which
source produced each result and keeps validation metadata with every imported
entry (https://example.com/catalog).

## Evidence and Analysis

The search workflow should remain auditable: each catalog query needs a source
URL, a normalized title, and a rollback path for stale assets. These controls
support report generation without exposing internal worker scaffolding
(https://example.com/catalog).

Teams should also preserve reviewer notes, import timestamps, and the selected
report profile so later runs can explain why one catalog entry was used instead
of another. That evidence trail lets operators compare local fallback entries
with remote updates, reject stale assets, and keep generated reports tied to the
same cited source rather than drifting into unsupported prose.

## References

- https://example.com/catalog
"""

    result = evaluate_publish_readiness(
        requirement,
        text,
        verified_urls=["https://example.com/catalog"],
    )

    assert result.publish_ready is True


def test_publish_gate_allows_reader_facing_url_and_search_headings() -> None:
    requirement = "Write a research report about catalog management. Use citations."
    text = """
## Executive Summary

Catalog management needs versioned evidence, source tracking, and governance
controls for every generated report (https://example.com/catalog).

## URL: Evidence Index

URL governance can be reader-facing when the report explains how source
locations are normalized and audited (https://example.com/catalog).

URL: Evidence Index

## Search: Method

Search methodology can be a normal section heading when it describes how
catalog entries are discovered, deduplicated, reviewed, and retained for later
comparison (https://example.com/catalog).

SEARCH: Method

The method records query terms, source URLs, import timestamps, and reviewer
decisions so later report runs can explain why one catalog entry was selected
over another. It also distinguishes local fallback records from remote updates,
which helps operators reject stale assets without hiding useful provenance
(https://example.com/catalog).

## Limitations

This report assumes the catalog keeps source timestamps and reviewer notes
available for follow-up validation. It does not prove that every remote source
will stay available, so future runs still need freshness checks before publishing.

## References

- https://example.com/catalog
"""

    result = evaluate_publish_readiness(
        requirement,
        text,
        verified_urls=["https://example.com/catalog"],
    )

    assert result.publish_ready is True


def test_publish_gate_ignores_internal_structure_instruction_terms() -> None:
    requirement = (
        "Write a research report about catalog management for local and remote agent loops "
        "and skills in a generic research report generator. Include citations, "
        "implementation risks, and actionable recommendations.\n\n"
        "Structure the deliverable with these sections (use them as top-level headings, "
        "in order):\n- Executive Summary\n- References\n\n"
        "Focus specifically on: ASSIGNED SECTIONS:\n- ## Executive Summary\n\n"
        "Unified scoring requirements for this task:\n"
        "- The original deterministic rubric signals are the base measurements.\n"
        "- The frozen scoring matrix below defines the profile/template-specific scorecard.\n"
        "- Scope and research framing (10.5 pts): satisfy via structure\n"
        "- Citation integrity (14.7 pts): satisfy via verification"
    )
    text = """
## Executive Summary

Catalog management for local and remote agent loops needs versioned skill
registries, implementable validation controls, and concrete recommendations for
rollback and permissions. The report cites a fetched source for the central
claim (https://example.com/catalog).
In a generic research report generator, that catalog also needs to preserve
which loop, skill, and source produced each section so reducers can verify the
final report instead of trusting free-form prose.

## Implementation Risks

Implementation risks include stale remote skills, local permission drift,
schema incompatibility, and missing audit records. Teams should validate imports,
pin versions, and keep a rollback path for every catalog entry.
Recommended next steps are to sign remote entries, keep local fallbacks for
offline execution, and reject skills whose declared inputs or permissions do not
match the selected report template.

## References

- https://example.com/catalog
"""

    result = evaluate_publish_readiness(
        requirement,
        text,
        verified_urls=["https://example.com/catalog"],
    )

    assert result.publish_ready is True


def test_combined_publish_issues_include_artifact_lints() -> None:
    requirement = (
        "Write a research report about catalog management for agent loops. "
        "Use citations and fetched evidence."
    )
    long_uncited_section = " ".join(
        f"catalog management evidence for agent loops statement {i}" for i in range(170)
    )
    text = f"""
## Evidence and Analysis

{long_uncited_section}

## References

- https://example.com/catalog
"""

    publish = evaluate_publish_readiness(
        requirement,
        text,
        verified_urls=["https://example.com/catalog"],
    )

    issues = combined_publish_issues(publish, text)

    assert publish.publish_ready is True
    assert any("Long evidence-bearing section has no citation URL" in issue for issue in issues)


def test_duplicate_report_sections_fail_publish_gate() -> None:
    requirement = "Write a research report about catalog management. Use citations."
    text = """
## Executive Summary

Catalog management needs citations and governance (https://example.com/a).

## Key Findings

The catalog needs versioning, validation, and audit trails (https://example.com/a).

## Executive Summary

This duplicate section indicates the report outline drifted during generation.
"""

    result = evaluate_publish_readiness(
        requirement,
        text,
        verified_urls=["https://example.com/a"],
    )

    assert result.publish_ready is False
    assert any("repeats section headings" in issue for issue in result.issues)


def test_extra_report_titles_fail_publish_gate() -> None:
    requirement = "Write a research report about catalog management. Use citations."
    text = """
# Catalog Management Report

## Executive Summary

Catalog management needs citations and governance (https://example.com/a).

## References

- https://example.com/a

# Duplicate Model Title
"""

    result = evaluate_publish_readiness(
        requirement,
        text,
        verified_urls=["https://example.com/a"],
    )

    assert result.publish_ready is False
    assert any("extra report titles" in issue for issue in result.issues)


def test_placeholder_report_title_fails_publish_gate() -> None:
    requirement = "Write a research report about catalog management. Use citations."
    text = """
# _(report title - generated from the findings below)_

## Executive Summary

Catalog management needs citations and governance (https://example.com/a).

## References

- https://example.com/a
"""

    result = evaluate_publish_readiness(
        requirement,
        text,
        verified_urls=["https://example.com/a"],
    )

    assert result.publish_ready is False
    assert any("placeholder report title" in issue for issue in result.issues)


def test_generic_report_title_fails_publish_gate() -> None:
    requirement = "Write a research report about catalog management. Use citations."
    text = """
# Research Report

## Executive Summary

Catalog management needs citations and governance (https://example.com/a).

## References

- https://example.com/a
"""

    result = evaluate_publish_readiness(
        requirement,
        text,
        verified_urls=["https://example.com/a"],
    )

    assert result.publish_ready is False
    assert any("placeholder report title" in issue for issue in result.issues)


def test_missing_active_outline_section_fails_publish_gate() -> None:
    requirement = "Write a research report about catalog management. Use citations."
    text = """
## Executive Summary

Catalog management needs citations and governance (https://example.com/a).

## Key Findings

The catalog needs versioning, validation, and audit trails (https://example.com/a).
"""

    result = evaluate_publish_readiness(
        requirement,
        text,
        verified_urls=["https://example.com/a"],
        required_sections=["Executive Summary", "Key Findings", "References"],
    )

    assert result.publish_ready is False
    assert any("misses active outline sections: References" in issue for issue in result.issues)


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


def test_synthesis_depth_flags_citation_only_report() -> None:
    evidence = """
RESEARCH_FINDING:
ARTICLE_TITLE: Agent Registry
URL: https://example.com/registry
QUOTE: Registry storage keeps agent metadata and capabilities.
WHY: Explains the catalog management substrate.
"""
    text = """
## Executive Summary

Catalogs need a registry. Source: https://example.com/registry

## References

- https://example.com/registry
"""

    issues = synthesis_depth_issues(text, evidence)

    assert any("lacks analysis" in issue for issue in issues)
    assert any("lacks limitations" in issue for issue in issues)


def test_combined_publish_issues_include_synthesis_depth() -> None:
    text = """
## Executive Summary

Catalog management should track loop and skill entries using a registry. The registry source
describes metadata and capabilities, so this report cites it as evidence for the catalog need.
Additional prose keeps the artifact long enough to be structurally complete while still avoiding
any real interpretation of what the evidence changes for implementation. https://example.com/registry

## References

- https://example.com/registry
"""
    publish = evaluate_publish_readiness(
        "Write a concise research report about catalog management. Use fetched evidence.",
        text,
        verified_urls=["https://example.com/registry"],
    )
    evidence = "RESEARCH_FINDING:\nURL: https://example.com/registry\nQUOTE: metadata and capabilities"

    issues = combined_publish_issues(publish, text, evidence)

    assert any("lacks analysis" in issue for issue in issues)
    assert any("lacks limitations" in issue for issue in issues)


def test_publish_revision_prompt_requires_synthesis_and_reflection() -> None:
    prompt = build_publish_revision_prompt(
        "Write a report. Use fetched evidence.",
        "Draft with https://example.com/source",
        ["[publish-gate] Final report lacks analysis, implications, or recommendations."],
        "RESEARCH_FINDING:\nURL: https://example.com/source\nQUOTE: useful detail",
    )

    assert "Evidence-Backed Analysis" in prompt
    assert "trade-offs" in prompt
    assert "Limitations, Caveats, or Reflection" in prompt
    assert "Use every relevant fetched finding" in prompt
