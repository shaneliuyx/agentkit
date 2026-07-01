"""Report profile presets for the generic research report generator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentkit.planner.core import Plan, PlanStep
from agentkit.topology.core import SINGLE


ReportType = Literal[
    "general",
    "technical",
    "deep_technical",
    "market",
    "policy",
    "academic",
    "literature_review",
    "competitive",
    "product",
]


@dataclass(frozen=True)
class ReportProfile:
    report_type: ReportType
    title: str
    sections: tuple[str, ...]
    code_default: bool = False
    diagrams_default: bool = False
    tables_default: bool = True


GENERIC_RESEARCH_PROFILE = ReportProfile(
    report_type="general",
    title="Generic Research Report",
    sections=(
        "Executive Summary",
        "Scope and Research Questions",
        "Background and Context",
        "Key Findings",
        "Evidence and Analysis",
        "Implications or Recommendations",
        "Limitations and Uncertainty",
        "References",
    ),
)

TECHNICAL_REPORT_PROFILE = ReportProfile(
    report_type="technical",
    title="Technical Research Report",
    sections=(
        "Executive Summary",
        "Background and Scope",
        "Key Concepts",
        "Architecture Overview or Current State",
        "Methodology",
        "Implementation Blueprint",
        "Practical Code or Example Walkthrough",
        "Governance and Security",
        "Evaluation and Metrics",
        "Limitations and Open Questions",
        "Reflection and Lessons Learned",
        "Appendix: Code, Diagrams, Glossary, References",
    ),
    code_default=True,
    diagrams_default=True,
)

DEEP_TECHNICAL_REPORT_PROFILE = ReportProfile(
    report_type="deep_technical",
    title="Deep Technical Research Report",
    sections=(
        "Executive Summary",
        "Background and Context",
        "Research Questions",
        "Methodology",
        "Current State / Landscape",
        "Architecture / Conceptual Model",
        "Deep Analysis",
        "Practical Implementation",
        "Case Study or Scenario Walkthrough",
        "Quality, Evaluation, and Governance",
        "Reflection and Lessons Learned",
        "Risks, Limitations, and Open Questions",
        "Recommendations and Roadmap",
        "Conclusion",
        "Appendix A. Glossary",
        "Appendix B. Evidence Matrix",
        "Appendix C. Full Source List",
        "Appendix D. Code Listings",
        "Appendix E. Quality Scorecard",
    ),
    code_default=True,
    diagrams_default=True,
)

MARKET_RESEARCH_PROFILE = ReportProfile(
    report_type="market",
    title="Market Research Report",
    sections=(
        "Executive Summary",
        "Market Definition and Scope",
        "Customer Segments and Needs",
        "Market Size and Growth Signals",
        "Competitive Landscape",
        "Trends and Drivers",
        "Risks and Uncertainties",
        "Strategic Recommendations",
        "References",
    ),
)

POLICY_RESEARCH_PROFILE = ReportProfile(
    report_type="policy",
    title="Policy Analysis",
    sections=(
        "Executive Summary",
        "Policy Context and Scope",
        "Stakeholders",
        "Evidence Base",
        "Policy Options",
        "Impacts and Tradeoffs",
        "Legal or Regulatory Considerations",
        "Risks, Uncertainty, and Equity Considerations",
        "Recommendations",
        "References",
    ),
)

LITERATURE_REVIEW_PROFILE = ReportProfile(
    report_type="literature_review",
    title="Literature Review",
    sections=(
        "Abstract or Executive Summary",
        "Research Questions",
        "Search and Inclusion Method",
        "Prior Work Overview",
        "Evidence Themes",
        "Agreements, Disagreements, and Gaps",
        "Quality and Limitations of Evidence",
        "Future Research Directions",
        "References",
    ),
)

COMPETITIVE_ANALYSIS_PROFILE = ReportProfile(
    report_type="competitive",
    title="Competitive Analysis",
    sections=(
        "Executive Summary",
        "Scope and Comparison Criteria",
        "Competitor Profiles",
        "Capability Matrix",
        "Positioning and Differentiation",
        "Customer or Market Signals",
        "Risks and Open Questions",
        "Recommendations",
        "References",
    ),
)

PRODUCT_RESEARCH_PROFILE = ReportProfile(
    report_type="product",
    title="Product Research Report",
    sections=(
        "Executive Summary",
        "Product Context and Scope",
        "User Needs and Jobs To Be Done",
        "Evidence and Insights",
        "Feature or Solution Options",
        "Risks and Tradeoffs",
        "Recommendations",
        "References",
    ),
)

ACADEMIC_PROFILE = ReportProfile(
    report_type="academic",
    title="Academic Research Report",
    sections=LITERATURE_REVIEW_PROFILE.sections,
)

REPORT_PROFILES: dict[str, ReportProfile] = {
    p.report_type: p
    for p in (
        GENERIC_RESEARCH_PROFILE,
        TECHNICAL_REPORT_PROFILE,
        DEEP_TECHNICAL_REPORT_PROFILE,
        MARKET_RESEARCH_PROFILE,
        POLICY_RESEARCH_PROFILE,
        LITERATURE_REVIEW_PROFILE,
        COMPETITIVE_ANALYSIS_PROFILE,
        PRODUCT_RESEARCH_PROFILE,
        ACADEMIC_PROFILE,
    )
}


def profile_template_presets() -> list[dict[str, object]]:
    """Return built-in report profile presets for API/UI consumers."""
    return [
        {
            "report_type": profile.report_type,
            "title": profile.title,
            "sections": list(profile.sections),
            "code_default": profile.code_default,
            "diagrams_default": profile.diagrams_default,
            "tables_default": profile.tables_default,
        }
        for profile in REPORT_PROFILES.values()
    ]


def resolve_report_profile(report_type: str | None) -> ReportProfile:
    """Return a profile preset, defaulting to the generic report profile."""
    key = (report_type or "general").strip().lower().replace("-", "_")
    return REPORT_PROFILES.get(key, GENERIC_RESEARCH_PROFILE)


_REPORT_REQUEST_MARKERS = (
    "research report",
    "report",
    "market research",
    "literature review",
    "competitive analysis",
    "policy analysis",
)


def is_report_request(requirement: str) -> bool:
    """Best-effort guard for routing only report-like tasks to report scaffolds."""
    text = " ".join((requirement or "").lower().split())
    return any(marker in text for marker in _REPORT_REQUEST_MARKERS)


def build_methodology_report_prompt(
    requirement: str,
    profile: ReportProfile | None = None,
) -> str:
    """Final-stage report prompt for the explicit methodology scaffold.

    This is used as the final stage in the fixed methodology-derived report
    loop. Earlier stages produce config, source plan, and evidence notes.
    """
    profile = profile or GENERIC_RESEARCH_PROFILE
    sections = "\n".join(f"- {section}" for section in profile.sections)
    code_policy = (
        "Code blocks are allowed only when the topic explicitly requires implementation detail."
        if profile.code_default
        else "Do not include code blocks unless the user explicitly asked for code."
    )
    return (
        "You are writing a generic research report, not planning a workflow.\n"
        "Use the upstream ResearchConfig, source plan, and evidence notes. Do not restart planning.\n\n"
        f"USER REQUEST:\n{requirement.strip()}\n\n"
        "REQUIRED SECTIONS, IN THIS ORDER:\n"
        f"{sections}\n\n"
        "REPORT RULES:\n"
        "- Stay on the user's subject; do not replace it with a nearby generic topic.\n"
        "- Attach source URLs to factual claims when fetched evidence supports them.\n"
        "- Never invent a URL, source title, metric, quote, or citation.\n"
        "- If evidence is thin, say so briefly in Limitations; do not add placeholders.\n"
        "- Keep the report concise and publishable; no TODOs, no 'source references pending'.\n"
        f"- {code_policy}\n"
        "- Output Markdown only. Do not include EPIC_PLAN, TASK_LIST, ASSIGNED, JSON, or tool notes.\n"
    )


def build_methodology_report_plan(
    requirement: str,
    profile: ReportProfile | None = None,
) -> Plan:
    """Return the fixed methodology-derived report loop.

    Based on ``ref/.../02_methodology/agent_loop.md``: intake/profile, source
    plan, retrieval, verification/evidence matrix, deterministic-style assembly,
    section rewrite, lint, and publish. Use this when a user or catalog seed
    explicitly chooses the research-report methodology.
    """
    profile = profile or GENERIC_RESEARCH_PROFILE
    sections = "\n".join(f"- {section}" for section in profile.sections)
    section_list = ", ".join(profile.sections)
    return Plan(
        task=requirement,
        steps=(
            PlanStep(
                id="intake-profile",
                description=(
                    "Stage: Intake + Select Report Profile.\n"
                    "Output ONLY compact JSON named ResearchConfig with keys: topic, audience, "
                    "purpose, report_type, required_sections, source_policy, output_policy, "
                    "constraints.\n"
                    f"User request: {requirement.strip()}\n"
                    f"Use report profile: {profile.title}. Required sections: {section_list}.\n"
                    "Hard gate: do not force technical/code sections onto non-technical reports."
                ),
                depends_on=(),
                topology=SINGLE,
            ),
            PlanStep(
                id="source-plan",
                description=(
                    "Stage: Section + Source Plan.\n"
                    "Use the upstream ResearchConfig. Produce a section-to-query plan as a "
                    "Markdown table with columns: section, search_query, required_source_type, "
                    "success_criteria.\n"
                    "Max one high-signal query per section for weak-model mode. Keep queries "
                    "anchored to the user's exact topic."
                ),
                depends_on=("intake-profile",),
                topology=SINGLE,
            ),
            PlanStep(
                id="retrieve-verify",
                description=(
                    "Stage: Retrieve Sources + Validate Evidence.\n"
                    "Run at most one web_search and fetch 1-2 relevant URLs. Produce SourceNotes "
                    "as bullet lines with: title, URL, date if available, claim, relevance, "
                    "section. Then produce an Evidence Matrix table: section, claim, URL, "
                    "source_status, confidence, conflict_or_limit.\n"
                    "Hard gate: no invented URLs; weak or irrelevant sources must be flagged."
                ),
                depends_on=("source-plan",),
                topology=SINGLE,
            ),
            PlanStep(
                id="assemble-rewrite",
                description=(
                    "Stage: Deterministic Section Assembly + Section Rewrite.\n"
                    "Use the Evidence Matrix. Build the report using exactly these sections, in "
                    f"order:\n{sections}\n"
                    "Do not concatenate worker prose or append evidence walls. Rewrite one section "
                    "at a time in concise publishable prose. Preserve every URL next to the claim "
                    "it supports. If evidence is missing, say so in Limitations rather than using "
                    "placeholders."
                ),
                depends_on=("retrieve-verify",),
                topology=SINGLE,
            ),
            PlanStep(
                id="lint-publish",
                description=(
                    "Stage: Report Lints + Publish Gate + Packaging.\n"
                    f"{build_methodology_report_prompt(requirement, profile)}\n"
                    "Before final output, check: no duplicate headings, no placeholders, no "
                    "unverified/broken links, no citation walls, no orphaned code, and citations "
                    "preserved after rewrite. If a hard gate fails, include a short 'Publish gate' "
                    "section listing the blocker instead of pretending the report is complete."
                ),
                depends_on=("assemble-rewrite",),
                topology=SINGLE,
            ),
        ),
    )
