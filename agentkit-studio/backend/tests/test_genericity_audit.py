from pathlib import Path

from studio.genericity_audit import audit_genericity


def test_audit_flags_fixed_report_prose_in_production(tmp_path: Path) -> None:
    prod = tmp_path / "backend" / "studio" / "bad.py"
    prod.parent.mkdir(parents=True)
    prod.write_text(
        'REPORT = """\n'
        "## Executive Summary\n\n"
        "Battery recycling policy should prioritize producer responsibility, collection "
        "targets, mineral recovery standards, import controls, public procurement, "
        "safety requirements, and market incentives. These conclusions are presented "
        "as a complete fallback answer instead of being generated from user evidence.\n\n"
        "## Recommendations\n\n"
        "Governments should fund recycling hubs, require battery passports, subsidize "
        "hydrometallurgical facilities, penalize informal disposal, and publish "
        "quarterly compliance tables for manufacturers and recyclers.\n\n"
        "## References\n\n"
        "- https://example.com/battery-policy\n"
        '"""\n',
        encoding="utf-8",
    )

    issues = audit_genericity([prod])

    assert any("fixed report draft" in issue.reason for issue in issues)


def test_audit_ignores_examples_in_allowed_paths(tmp_path: Path) -> None:
    fixture = tmp_path / "ref" / "bad.py"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        'REPORT = "Catalog management for agent loops and skills should separate local catalogs."\n',
        encoding="utf-8",
    )

    assert audit_genericity([fixture]) == []


def test_audit_allows_generic_report_generator_terms(tmp_path: Path) -> None:
    prod = tmp_path / "backend" / "studio" / "generic.py"
    prod.parent.mkdir(parents=True)
    prod.write_text(
        'PROMPT = "Use source evidence, citations, sections, templates, catalog loops, and skills."\n',
        encoding="utf-8",
    )

    assert audit_genericity([prod]) == []
