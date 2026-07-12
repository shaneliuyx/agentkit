from studio.report_profiles import (
    build_methodology_report_prompt,
    profile_template_presets,
    resolve_report_profile,
)


def test_general_profile_is_generic_and_omits_code_by_default() -> None:
    profile = resolve_report_profile(None)

    assert profile.report_type == "general"
    assert "Evidence and Analysis" in profile.sections
    assert not any("Code" in section for section in profile.sections)
    assert profile.code_default is False


def test_technical_profile_allows_code_and_diagrams() -> None:
    profile = resolve_report_profile("technical")

    assert profile.code_default is True
    assert profile.diagrams_default is True
    assert any("Code" in section for section in profile.sections)


def test_market_profile_does_not_inherit_technical_sections() -> None:
    profile = resolve_report_profile("market")

    assert "Competitive Landscape" in profile.sections
    assert not any("Code" in section for section in profile.sections)
    assert not any("Architecture" in section for section in profile.sections)


def test_deep_technical_profile_matches_implementation_report_needs() -> None:
    profile = resolve_report_profile("deep-technical")

    assert profile.report_type == "deep_technical"
    assert profile.code_default is True
    assert "Architecture / Conceptual Model" in profile.sections
    assert "Practical Implementation" in profile.sections
    assert "Appendix B. Evidence Matrix" in profile.sections


def test_profile_template_presets_are_api_ready() -> None:
    presets = profile_template_presets()
    by_type = {str(p["report_type"]): p for p in presets}

    assert "general" in by_type
    assert "deep_technical" in by_type
    assert "sections" in by_type["general"]
    assert "Practical Implementation" in by_type["deep_technical"]["sections"]


def test_methodology_report_prompt_is_evidence_first() -> None:
    prompt = build_methodology_report_prompt(
        "Write a generic research report about agent loop skill catalogs."
    )

    assert "Use the upstream ResearchConfig" in prompt
    assert "Executive Summary" in prompt
    assert "References" in prompt
    assert "Do not include EPIC_PLAN" in prompt
    assert "Do not include code blocks" in prompt
