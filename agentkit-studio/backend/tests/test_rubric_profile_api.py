from studio.app import registry, rubric_defaults, set_rubric


def test_rubric_defaults_expose_profile_templates() -> None:
    defaults = rubric_defaults()

    assert defaults["report_type"] == "general"
    presets = {p["report_type"]: p for p in defaults["template_presets"]}
    assert "general" in presets
    assert "deep_technical" in presets
    assert "Practical Implementation" in presets["deep_technical"]["sections"]


def test_set_rubric_can_select_profile_template() -> None:
    session = registry.create(
        llm_spec={"name": "haiku", "model": "m", "endpoint": "e"},
        embed_spec={},
        llm_info={},
        embed_info={},
        mode="llm",
        budget_ceiling=None,
    )

    out = set_rubric(session.session_id, {"report_type": "deep_technical"})

    cfg = out["rubric_config"]
    assert cfg["report_type"] == "deep_technical"
    assert "Practical Implementation" in cfg["template"]
    assert "Appendix B. Evidence Matrix" in cfg["template"]
