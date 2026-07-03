from studio.app import registry, rubric_defaults, set_rubric


def test_rubric_defaults_expose_profile_templates() -> None:
    defaults = rubric_defaults()

    assert defaults["report_type"] == "general"
    assert defaults["scoring_template"] == defaults["template"]
    default_categories = {row["category"] for row in defaults["scoring_matrix"]}
    assert "Code quality / examples" not in default_categories
    assert "Diagrams and tables" not in default_categories
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
    assert cfg["scoring_template"] == cfg["template"]
    assert len(cfg["scoring_matrix"]) == 12
    assert sum(float(row["points"]) for row in cfg["scoring_matrix"]) == 100.0


def test_set_rubric_preserves_explicit_scoring_template() -> None:
    session = registry.create(
        llm_spec={"name": "haiku", "model": "m", "endpoint": "e"},
        embed_spec={},
        llm_info={},
        embed_info={},
        mode="llm",
        budget_ceiling=None,
    )

    out = set_rubric(
        session.session_id,
        {
            "template": ["Live Section"],
            "scoring_template": ["References"],
            "scoring_matrix": [{"category": "Citation integrity", "points": 12}],
        },
    )

    cfg = out["rubric_config"]
    assert cfg["template"] == ["Live Section"]
    assert cfg["scoring_template"] == ["References"]
    assert sum(1 for row in cfg["scoring_matrix"] if row["points"]) == 1
    assert cfg["scoring_matrix"][0]["category"] == "Citation integrity"
    assert cfg["scoring_matrix"][0]["points"] == 100.0
