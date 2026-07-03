from __future__ import annotations

import json
import re

from studio.section_workspace import (
    PLACEHOLDER,
    active_outline_titles,
    assemble_artifact_from_sections,
    clear_completed_assignments,
    load_assignment_queue,
    load_active_outline,
    section_file_map,
    slugify_section,
    split_artifact_to_sections,
    write_section_workspace,
    write_assignment_queue,
)


def test_slugify_section_is_filesystem_safe() -> None:
    assert slugify_section("Scope & Research Questions!") == "scope-research-questions"
    assert slugify_section("  ") == "section"


def test_split_appends_new_sections_after_initial_outline() -> None:
    artifact = "# Report\n\n## Executive Summary\nDone.\n\n## New Risk Analysis\nRisk.\n"

    outline, files = split_artifact_to_sections(
        artifact, ["Executive Summary", "References"]
    )

    titles = [s["title"] for s in outline["sections"]]
    assert titles == ["Executive Summary", "References", "New Risk Analysis"]
    assert any("## New Risk Analysis" in body for body in files.values())
    assert any(PLACEHOLDER in body and "## References" in body for body in files.values())


def test_write_and_assemble_section_workspace_roundtrips_one_outline(tmp_path) -> None:
    artifact = (
        "# Report\n\n"
        "## Executive Summary\nDone.\n\n"
        "## Key Findings\nFinding.\n"
    )

    outline = write_section_workspace(
        tmp_path, artifact, ["Executive Summary", "Key Findings", "References"]
    )
    assembled = assemble_artifact_from_sections(tmp_path)

    assert (tmp_path / "artifact.md").read_text(encoding="utf-8") == assembled
    assert [s["title"] for s in outline["sections"]] == [
        "Executive Summary",
        "Key Findings",
        "References",
    ]
    assert len(re.findall(r"(?m)^## Executive Summary$", assembled)) == 1
    assert len(re.findall(r"(?m)^## Key Findings$", assembled)) == 1
    assert len(re.findall(r"(?m)^## References$", assembled)) == 1

    active = json.loads(
        (tmp_path / "sections" / "active_outline.json").read_text(encoding="utf-8")
    )
    assert load_active_outline(tmp_path) == active
    assert active_outline_titles(tmp_path) == [
        "Executive Summary",
        "Key Findings",
        "References",
    ]
    files = section_file_map(tmp_path)
    assert files["Executive Summary"].startswith("sections/001-executive-summary")
    assert files["Key Findings"].startswith("sections/002-key-findings")
    assert files["References"].startswith("sections/003-references")
    refs_file = next(s["file"] for s in active["sections"] if s["title"] == "References")
    assert PLACEHOLDER in (tmp_path / "sections" / refs_file).read_text(encoding="utf-8")


def test_write_section_workspace_reorders_to_active_outline(tmp_path) -> None:
    artifact = (
        "# Report\n\n"
        "## New Section\nNew.\n\n"
        "## Executive Summary\nSummary.\n\n"
        "## References\nRefs.\n"
    )

    outline = write_section_workspace(
        tmp_path, artifact, ["Executive Summary", "References"]
    )
    assembled = (tmp_path / "artifact.md").read_text(encoding="utf-8")

    assert [s["title"] for s in outline["sections"]] == [
        "Executive Summary",
        "References",
        "New Section",
    ]
    assert assembled.index("## Executive Summary") < assembled.index("## References")
    assert assembled.index("## References") < assembled.index("## New Section")


def test_write_section_workspace_drops_stray_h1_inside_sections(tmp_path) -> None:
    artifact = (
        "# Report\n\n"
        "## Executive Summary\nSummary.\n\n"
        "## References\n- https://example.com\n\n"
        "# Model Emitted Duplicate Title\n\n"
        "# Another Stray Title\n"
    )

    write_section_workspace(tmp_path, artifact, ["Executive Summary", "References"])
    assembled = (tmp_path / "artifact.md").read_text(encoding="utf-8")

    assert "# Report" in assembled
    assert "## References\n- https://example.com" in assembled
    assert "# Model Emitted Duplicate Title" not in assembled
    assert "# Another Stray Title" not in assembled


def test_reducer_h1_dump_folds_into_single_sections(tmp_path) -> None:
    """Real fixture (session s_089481ef5161, e1:s1.spoke8.out.md): a section reducer
    emitted a full report at '#' (H1) for the first four sections instead of patching the
    '##' scaffold. Before the fold-boundary heading normalization, split_sections keyed only
    on '##', so the rich H1 blocks became '(intro)' preamble and duplicated a placeholder H2
    of the same name. After the fix, each title resolves to exactly ONE section and the
    reducer's rich content supersedes the thin scaffold body (not discarded to preamble)."""
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "reducer_h1_dump.md"
    artifact = fixture.read_text(encoding="utf-8")
    outline = [
        "Executive Summary",
        "Scope and Research Questions",
        "Background and Context",
        "Key Findings",
        "Evidence and Analysis",
        "Implications or Recommendations",
        "Limitations and Uncertainty",
        "References",
    ]

    write_section_workspace(tmp_path, artifact, outline)
    assembled = assemble_artifact_from_sections(tmp_path)

    # Every title appears exactly once, all at the canonical '##' level (no orphan '#').
    for title in outline:
        assert len(re.findall(rf"(?mi)^#{{1,6}}\s+{re.escape(title)}\s*$", assembled)) == 1
        assert len(re.findall(rf"(?m)^## {re.escape(title)}$", assembled)) == 1

    # The reducer's rich synthesis is preserved, not left as a placeholder.
    files = section_file_map(tmp_path)
    es_body = (tmp_path / files["Executive Summary"]).read_text(encoding="utf-8")
    assert PLACEHOLDER not in es_body
    assert "Pi agent toolkit" in es_body or "Agent Loop" in es_body

    # Legitimate '###' subsections inside a section body keep their level (not demoted).
    assert "### 1. The Power of the Agent Loop" in assembled


def test_assignment_queue_clears_only_completed_rows(tmp_path) -> None:
    rows = (
        {"agent_id": "agent-001", "section": "## A", "file": "sections/001-a.md", "status": "queued"},
        {"agent_id": "agent-002", "section": "## B", "file": "sections/002-b.md", "status": "queued"},
    )

    write_assignment_queue(tmp_path, rows)
    assert load_assignment_queue(tmp_path) == list(rows)

    clear_completed_assignments(tmp_path, 1)
    assert load_assignment_queue(tmp_path) == [rows[1]]
