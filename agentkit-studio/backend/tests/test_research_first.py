"""Tests for studio.research_first — the research-first generation pipeline.

Deterministic, no network: LLM calls are faked with literal strings (mirrors
test_structural_producer.py / test_diagram_render.py). Fetching/caching is not
exercised here (that's studio.tools' job, already covered by test_tools.py /
test_fetch_cache.py); these tests cover FRAME extraction, CLAIMS grounding,
per-section writing, summary-last ordering, and final assembly cleanliness.
"""
from __future__ import annotations

from types import SimpleNamespace

from studio import artifact_lint, research_first as rf


def _client(text: str) -> SimpleNamespace:
    return SimpleNamespace(chat=lambda messages, tools=None: SimpleNamespace(text=text))


# ---------------------------------------------------------------------------
# FRAME
# ---------------------------------------------------------------------------


def test_frame_extracts_subjects_and_sections_in_order() -> None:
    groups = [
        ["covers Pi"],
        ["covers Craft"],
        ["include a Risk Assessment section"],
        ["include example code"],
    ]
    subjects = rf._extract_subjects(groups)
    assert subjects == ["Pi", "Craft"]

    sections = rf._build_sections(groups)
    assert "Risk Assessment" in sections
    # inserted before References, template order otherwise preserved
    assert sections.index("Risk Assessment") < sections.index("References")
    assert sections[0] == "Executive Summary"
    assert sections[-1] == "References"


def test_frame_subjects_empty_when_no_subject_named() -> None:
    assert rf._extract_subjects([["include a diagram"]]) == []


def test_base_task_text_strips_template_and_scoring_suffixes() -> None:
    # REBUILD-LESSONS §2: template/scoring boilerplate dilutes domain-word density.
    requirement = (
        "Study how to use Pi and Craft to develop agents.\n\n"
        "Structure the deliverable with these sections (use them as top-level "
        "headings, in order):\n- Executive Summary\n- References\n\n"
        "Unified scoring requirements for this task:\n- rows..."
    )
    base = rf._base_task_text(requirement)
    assert base == "Study how to use Pi and Craft to develop agents."
    assert "Structure the deliverable" not in base
    assert "scoring requirements" not in base


def test_disambiguate_subject_fails_open_without_judge() -> None:
    descriptor, anchors = rf._disambiguate_subject("Craft", {"agents"}, "study Craft", None)
    assert (descriptor, anchors) == ("Craft", ["Craft"])


def test_disambiguate_subject_parses_descriptor_and_anchors(monkeypatch) -> None:
    monkeypatch.setattr(rf, "_search", lambda query, results=5: [SimpleNamespace(title="Craft docs", snippet="an agent tool")])
    judge = _client("DESCRIPTOR: Craft agent tool\nANCHORS: craft.do, agents, notes")
    descriptor, anchors = rf._disambiguate_subject("Craft", {"agents", "develop"}, "study Craft", judge)
    assert descriptor == "Craft agent tool"
    assert anchors == ["craft.do", "agents", "notes"]


def test_disambiguate_subject_fails_open_on_no_search_results(monkeypatch) -> None:
    monkeypatch.setattr(rf, "_search", lambda query, results=5: [])
    descriptor, anchors = rf._disambiguate_subject("Craft", {"agents"}, "study Craft", _client("DESCRIPTOR: x\nANCHORS: y"))
    assert (descriptor, anchors) == ("Craft", ["Craft"])


def test_anchor_hits_matches_on_constituent_words_not_exact_phrase() -> None:
    # Live regression: a real README paraphrases a judge-invented descriptive
    # phrase rather than repeating it verbatim — exact-phrase matching dropped
    # the actual target repo over this.
    content = "Craft Agents connects to MCP servers and local filesystems."
    assert rf._anchor_hits(content, ["API/MCP server connections"]) == 1
    assert rf._anchor_hits(content, ["notes app", "boat"]) == 0
    assert rf._anchor_hits(content, ["open source agent interface", "API/MCP server connections"]) == 1


def test_fenced_code_compiles_rejects_python_syntax_error() -> None:
    good = "```python\nprint('hi')\n```"
    bad = "```python\nx = 1 — comment without hash\n```"
    non_python = "```text\nnot code at all — fine\n```"
    assert rf._fenced_code_compiles(good) is True
    assert rf._fenced_code_compiles(bad) is False
    assert rf._fenced_code_compiles(non_python) is True


# ---------------------------------------------------------------------------
# CLAIMS
# ---------------------------------------------------------------------------


def test_claims_grounding_drops_unverifiable_quotes() -> None:
    content = "Pi is a minimal agent harness. It ships a planner and an executor loop."
    reply_text = (
        "CLAIM: Pi ships a planner and executor loop.\n"
        "QUOTE: It ships a planner and an executor loop.\n"
        "SUBJECTS: Pi\n"
        "CLAIM: Pi was created by a Roman emperor.\n"
        "QUOTE: this sentence never appears anywhere in the source text.\n"
        "SUBJECTS: Pi\n"
    )
    claims = rf._extract_claims_from_source("https://x.test/pi", content, ["Pi"], _client(reply_text))
    assert len(claims) == 1
    assert claims[0]["claim"] == "Pi ships a planner and executor loop."
    assert claims[0]["url"] == "https://x.test/pi"
    assert claims[0]["subjects"] == ["Pi"]


def test_claims_for_section_round_robins_across_subjects() -> None:
    # Live regression: "names two subjects, covers one" reborn at WRITE stage —
    # claims[:cap] first-N let subject-1's larger claim count starve subject-2
    # out of every non-Evidence section entirely.
    claims = [{"claim": f"pi claim {i}", "subjects": ["Pi"]} for i in range(10)]
    claims += [{"claim": f"craft claim {i}", "subjects": ["Craft"]} for i in range(2)]
    selected = rf._claims_for_section(claims, "Key Findings")  # non-Evidence cap = 6
    subjects_seen = {c["subjects"][0] for c in selected}
    assert "Pi" in subjects_seen and "Craft" in subjects_seen
    assert len([c for c in selected if c["subjects"] == ["Craft"]]) == 2  # both got in


def test_claims_clamp_mistagged_subject_to_fetch_loop() -> None:
    # Live regression: a source fetched under the "Pi" loop, but the LLM's own
    # SUBJECTS guess named "Craft" instead — source selection was gated, the
    # per-claim tag was not. Clamp to the loop that actually fetched it.
    content = "Pi is a minimal agent harness. It ships a planner and an executor loop."
    reply_text = (
        "CLAIM: Pi ships a planner and executor loop.\n"
        "QUOTE: It ships a planner and an executor loop.\n"
        "SUBJECTS: Craft\n"
    )
    claims = rf._extract_claims_from_source(
        "https://x.test/pi", content, ["Pi", "Craft"], _client(reply_text), loop_subject="Pi"
    )
    assert claims[0]["subjects"] == ["Pi"]


def test_claims_clamp_drops_spurious_extra_subject() -> None:
    # Live shape: the LLM tagged BOTH subjects (['Pi', 'Craft']) even though the
    # source was only ever vetted against Pi's anchors — a bare "not in" check
    # misses this since the loop's own subject IS present.
    content = "Pi is a minimal agent harness. It ships a planner and an executor loop."
    reply_text = (
        "CLAIM: Pi ships a planner and executor loop.\n"
        "QUOTE: It ships a planner and an executor loop.\n"
        "SUBJECTS: Pi, Craft\n"
    )
    claims = rf._extract_claims_from_source(
        "https://x.test/pi", content, ["Pi", "Craft"], _client(reply_text), loop_subject="Pi"
    )
    assert claims[0]["subjects"] == ["Pi"]


def test_claims_no_clamp_for_joint_loop_or_matching_tag() -> None:
    content = "Pi is a minimal agent harness. It ships a planner and an executor loop."
    reply_text = (
        "CLAIM: Pi ships a planner and executor loop.\n"
        "QUOTE: It ships a planner and an executor loop.\n"
        "SUBJECTS: Craft\n"
    )
    # joint-fetched source (loop_subject=None) legitimately spans subjects — untouched
    joint = rf._extract_claims_from_source(
        "https://x.test/pi", content, ["Pi", "Craft"], _client(reply_text), loop_subject=None
    )
    assert joint[0]["subjects"] == ["Craft"]
    # a tag matching the fetch loop is untouched
    matching = rf._extract_claims_from_source(
        "https://x.test/pi", content, ["Pi", "Craft"],
        _client(reply_text.replace("Craft", "Pi")), loop_subject="Pi",
    )
    assert matching[0]["subjects"] == ["Pi"]


def test_is_offtopic_wraps_findings_offtopic_url(monkeypatch) -> None:
    # REBUILD-LESSONS §2: grounding != relevance. _is_offtopic must reuse
    # findings._offtopic_url's calibrated density+judge check, not re-derive one.
    calls = []

    def _fake_offtopic_url(url, req_words, requirement, judge):
        calls.append((url, req_words, requirement, judge))
        return True

    monkeypatch.setattr("studio.findings._offtopic_url", _fake_offtopic_url)
    result = rf._is_offtopic("https://x.test", {"pi"}, "study Pi", "judge-sentinel")
    assert result is True
    assert calls == [("https://x.test", {"pi"}, "study Pi", "judge-sentinel")]


def test_claims_extraction_fails_open_on_bad_client() -> None:
    bad_client = SimpleNamespace(chat=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert rf._extract_claims_from_source("https://x.test", "some content", ["Pi"], bad_client) == []
    assert rf._extract_claims_from_source("https://x.test", "content", ["Pi"], None) == []


def test_claims_extraction_collapses_escape_flood_in_quote() -> None:
    # REBUILD-LESSONS §2: quotes can arrive with literal "\n" text instead of a
    # real newline; normalize before the verbatim-substring check.
    content = "Pi ships a planner and an executor loop for agents."
    reply_text = (
        "CLAIM: Pi ships a planner and executor loop.\n"
        "QUOTE: " + r"Pi ships a planner\n\nand an executor loop" + "\n"
        "SUBJECTS: Pi\n"
    )
    claims = rf._extract_claims_from_source("https://x.test/pi", content, ["Pi"], _client(reply_text))
    assert len(claims) == 1
    assert "\\n" not in claims[0]["quote"]


# ---------------------------------------------------------------------------
# WRITE
# ---------------------------------------------------------------------------


def test_section_writer_includes_citations_from_claims() -> None:
    claims = [
        {"claim": "Pi ships a planner.", "quote": "a planner", "url": "https://x.test/pi", "subjects": ["Pi"]},
    ]
    body = rf._write_section("Key Findings", "study Pi", claims, _client("Pi ships a planner (https://x.test/pi)."))
    assert "https://x.test/pi" in body


def test_section_writer_drops_trailing_unclosed_fence() -> None:
    # Live gemma failure mode: a plain-prose reply ends with a bare opening fence
    # and no body/closer.
    reply = "Pi ships a planner (https://x.test/pi).\n\n```python"
    body = rf._write_section("Key Findings", "study Pi", [], _client(reply))
    assert "```" not in body
    assert body.count("```") == 0


def test_section_writer_fails_open_without_client() -> None:
    body = rf._write_section("Key Findings", "study Pi", [], None)
    assert body.startswith("_(")


def test_splice_code_drops_unbalanced_fence_from_llm_fallback(tmp_path) -> None:
    # No evidence/*.md files → _find_evidence_code finds nothing → falls back to
    # the LLM call, which here (as observed live with gemma) returns an opening
    # fence with no closer.
    section_text = "Some section prose."
    out = rf._splice_code(section_text, tmp_path, _client("```python"))
    assert out == section_text
    assert "```" not in out


def test_summary_written_from_all_body_sections_last() -> None:
    written = {
        "Key Findings": "Pi ships a planner (https://x.test/pi).",
        "Evidence and Analysis": "Craft is a writing tool (https://x.test/craft).",
    }
    captured: dict[str, list] = {}

    def _capture_chat(messages, tools=None):
        captured["messages"] = messages
        return SimpleNamespace(text="Pi and Craft were both covered above.")

    summary = rf._write_summary("study Pi and Craft", written, SimpleNamespace(chat=_capture_chat))
    prompt = captured["messages"][0]["content"]
    # the summary prompt is built FROM the already-written sections, in order
    assert prompt.index("Key Findings") < prompt.index("Evidence and Analysis")
    assert "Pi ships a planner" in prompt
    assert summary == "Pi and Craft were both covered above."


# ---------------------------------------------------------------------------
# ASSEMBLE
# ---------------------------------------------------------------------------


def test_assembly_passes_artifact_lint_clean() -> None:
    from studio.artifact_text import rebuild_references_section

    sections = list(rf.GENERIC_RESEARCH_PROFILE.sections)
    written = {
        name: f"{name} discusses Pi (https://x.test/pi) and Craft (https://x.test/craft) briefly."
        for name in sections
        if name.lower() not in ("executive summary", "references")
    }
    summary = "This report covers Pi (https://x.test/pi) and Craft (https://x.test/craft)."
    text = rf._assemble("Pi and Craft", summary, sections, written)
    text = rebuild_references_section(text)

    assert text.startswith("# Pi and Craft")
    assert "## References" in text
    assert "https://x.test/pi" in text.rsplit("## References", 1)[1]
    assert artifact_lint.lint_artifact(text) == []
