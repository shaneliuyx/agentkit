"""Tests for studio.research_first — the research-first generation pipeline.

Deterministic, no network: LLM calls are faked with literal strings (mirrors
test_structural_producer.py / test_diagram_render.py). Fetching/caching is not
exercised here (that's studio.tools' job, already covered by test_tools.py /
test_fetch_cache.py); these tests cover FRAME extraction, CLAIMS grounding,
per-section writing, summary-last ordering, and final assembly cleanliness.
"""
from __future__ import annotations

from pathlib import Path
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


def test_ensure_integration_section_added_for_multi_subject() -> None:
    sections = ["Executive Summary", "Key Findings", "References"]
    out = rf._ensure_integration_section(sections, ["Pi", "Craft"])
    home = rf._pick_integration_home(out)
    assert home is not None
    assert out.index(home) < out.index("References")


def test_ensure_integration_section_noop_for_single_subject() -> None:
    sections = ["Executive Summary", "Key Findings", "References"]
    out = rf._ensure_integration_section(sections, ["Pi"])
    assert out == sections
    assert rf._pick_integration_home(out) is None


def test_ensure_integration_section_noop_when_already_present() -> None:
    sections = ["Executive Summary", "Pi and Craft Integration", "References"]
    out = rf._ensure_integration_section(sections, ["Pi", "Craft"])
    assert out == sections


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


def test_resolve_relationship_fails_open_without_judge() -> None:
    mechanism, verify_terms = rf._resolve_relationship(["Pi", "Craft"], {"Pi": "Pi", "Craft": "Craft"}, "study Pi and Craft", None)
    assert mechanism == "composed side-by-side, mechanism unknown"
    assert verify_terms == []


def test_resolve_relationship_skipped_for_single_subject() -> None:
    mechanism, verify_terms = rf._resolve_relationship(["Pi"], {"Pi": "Pi"}, "study Pi", _client("MECHANISM: x\nVERIFY: y"))
    assert mechanism == "composed side-by-side, mechanism unknown"
    assert verify_terms == []


def test_resolve_relationship_parses_mechanism_and_verify_terms() -> None:
    judge = _client("MECHANISM: Pi calls Craft via an MCP server\nVERIFY: MCP, plugin, extension")
    mechanism, verify_terms = rf._resolve_relationship(
        ["Pi", "Craft"], {"Pi": "Pi Agent Framework", "Craft": "Craft Agents"}, "study Pi and Craft", judge
    )
    assert mechanism == "Pi calls Craft via an MCP server"
    assert verify_terms == ["MCP", "plugin", "extension"]


def test_research_joint_queries_and_assumptions_derive_from_relationship(monkeypatch, tmp_path) -> None:
    # Team-lead rule: joint queries come FROM the relationship hypothesis
    # (mechanism + verify terms), not generic subject-A+subject-B
    # concatenation — and the hypothesis is visible in the Scope Assumptions.
    queries_seen: list[str] = []
    monkeypatch.setattr(rf, "_search", lambda query, results=5: queries_seen.append(query) or [])
    judge = _client(
        "DESCRIPTOR: X\nANCHORS: a, b\n"
        "MECHANISM: Pi calls Craft via an MCP server\nVERIFY: MCP, plugin"
    )
    _ledger, assumptions, mechanism, verify_terms = rf._research(
        ["Pi", "Craft"], tmp_path, "study Pi and Craft", judge, emit=None
    )
    assert mechanism == "Pi calls Craft via an MCP server"
    assert verify_terms == ["MCP", "plugin"]
    assert any("Hypothesized relationship: Pi calls Craft via an MCP server." in a for a in assumptions)
    joint_queries = queries_seen[-2:]  # the 2 joint queries are issued last
    assert any("MCP" in q for q in joint_queries)


def test_research_joint_loop_drops_source_matching_no_subject_anchor(monkeypatch, tmp_path) -> None:
    # The joint loop had NO anchor gate before this round — only the generic
    # offtopic floor. Gate on the UNION of every subject's anchors (a page
    # only needs to connect to one side to be a real integration source).
    monkeypatch.setattr(rf, "_search", lambda query, results=5: [SimpleNamespace(url="https://joint.test/bad")])
    monkeypatch.setattr(rf, "_is_offtopic", lambda *a, **k: False)
    monkeypatch.setattr(rf, "_fetch_and_store", lambda url, evidence_dir, idx: "generic unrelated filler content")
    judge = _client(
        "DESCRIPTOR: X\nANCHORS: pi-ai, pi-agent-core\n"
        "MECHANISM: Pi calls Craft via an MCP server\nVERIFY: MCP, plugin"
    )
    ledger, _assumptions, _mechanism, _verify_terms = rf._research(
        ["Pi", "Craft"], tmp_path, "study Pi and Craft", judge, emit=None
    )
    assert ledger["__joint__"] == []


def test_research_joint_loop_keeps_source_matching_union_anchor(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(rf, "_search", lambda query, results=5: [SimpleNamespace(url="https://joint.test/good")])
    monkeypatch.setattr(rf, "_is_offtopic", lambda *a, **k: False)
    monkeypatch.setattr(
        rf, "_fetch_and_store", lambda url, evidence_dir, idx: "This page discusses pi-agent-core integration."
    )
    judge = _client(
        "DESCRIPTOR: X\nANCHORS: pi-ai, pi-agent-core\n"
        "MECHANISM: Pi calls Craft via an MCP server\nVERIFY: MCP, plugin"
    )
    ledger, _assumptions, _mechanism, _verify_terms = rf._research(
        ["Pi", "Craft"], tmp_path, "study Pi and Craft", judge, emit=None
    )
    assert len(ledger["__joint__"]) > 0


def test_anchor_hits_matches_on_constituent_words_not_exact_phrase() -> None:
    # Live regression: a real README paraphrases a judge-invented descriptive
    # phrase rather than repeating it verbatim — exact-phrase matching dropped
    # the actual target repo over this.
    content = "Craft Agents connects to MCP servers and local filesystems."
    assert rf._anchor_hits(content, ["API/MCP server connections"]) == 1
    assert rf._anchor_hits(content, ["notes app", "boat"]) == 0
    assert rf._anchor_hits(content, ["open source agent interface", "API/MCP server connections"]) == 1


def test_subject_name_absent_true_for_wrong_referent_source() -> None:
    # Live regression: pypi's (Microsoft's) "agent-framework" page passed Pi's
    # anchor gate on generic "agent"/"tool"/"calling" overlap while never
    # naming "Pi" itself anywhere on the page.
    content = "agent-framework: a Python library for building AI agents with tool calling."
    assert rf._subject_name_absent("Pi", content) is True


def test_subject_name_absent_false_when_subject_named() -> None:
    content = "Pi is a minimal agent harness with a built-in agent loop."
    assert rf._subject_name_absent("Pi", content) is False


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


def test_claims_for_section_joint_claims_get_own_bucket() -> None:
    # A joint claim (subjects=["Pi", "Craft"], post the "never a single-
    # subject tag" clamp) must not inflate subjects[0]'s ("Pi") round-robin
    # bucket — that would starve Pi's own real per-subject claims of cap
    # headroom purely because of list order.
    claims = [{"claim": f"pi claim {i}", "subjects": ["Pi"]} for i in range(6)]
    claims += [{"claim": f"joint claim {i}", "subjects": ["Pi", "Craft"]} for i in range(6)]
    claims += [{"claim": f"craft claim {i}", "subjects": ["Craft"]} for i in range(2)]
    selected = rf._claims_for_section(claims, "Key Findings")  # non-Evidence cap = 6
    pi_only = [c for c in selected if c["subjects"] == ["Pi"]]
    craft_only = [c for c in selected if c["subjects"] == ["Craft"]]
    joint = [c for c in selected if c["subjects"] == ["Pi", "Craft"]]
    assert pi_only and craft_only and joint  # all three buckets represented


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


def test_claims_joint_loop_always_tagged_all_subjects() -> None:
    # Live shape: a joint-fetched source got tagged ['Pi'] on 3 of its 4
    # extracted claims and ['Pi', 'Craft'] on the 4th — an LLM extraction call
    # with nothing to clamp it isn't even self-consistent within one source.
    # A joint source was found via the RELATIONSHIP query, not any one
    # subject's anchors — it should never carry a single-subject tag,
    # regardless of what the extraction call guessed.
    content = "Pi is a minimal agent harness. It ships a planner and an executor loop."
    reply_text = (
        "CLAIM: Pi ships a planner and executor loop.\n"
        "QUOTE: It ships a planner and an executor loop.\n"
        "SUBJECTS: Craft\n"
    )
    joint = rf._extract_claims_from_source(
        "https://x.test/pi", content, ["Pi", "Craft"], _client(reply_text), loop_subject=None
    )
    assert joint[0]["subjects"] == ["Pi", "Craft"]


def test_claims_matching_tag_untouched() -> None:
    content = "Pi is a minimal agent harness. It ships a planner and an executor loop."
    reply_text = (
        "CLAIM: Pi ships a planner and executor loop.\n"
        "QUOTE: It ships a planner and an executor loop.\n"
        "SUBJECTS: Pi\n"
    )
    matching = rf._extract_claims_from_source(
        "https://x.test/pi", content, ["Pi", "Craft"], _client(reply_text), loop_subject="Pi",
    )
    assert matching[0]["subjects"] == ["Pi"]


def test_discard_evidence_file_removes_rejected_source(tmp_path) -> None:
    # Live regression: _find_evidence_code (used by _splice_code) scans
    # evidence/ directly, independent of the claims ledger — a source rejected
    # by the offtopic/anchor gates still had its raw page on disk and got
    # spliced in as "example code" (a Wikipedia math page's LaTeX markup).
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    path = evidence_dir / "source-001.md"
    path.write_text("URL: https://en.wikipedia.org/wiki/Pi\n\nsome content", encoding="utf-8")

    rf._discard_evidence_file(evidence_dir, 1)

    assert not path.exists()


def test_discard_evidence_file_is_a_noop_when_missing(tmp_path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    rf._discard_evidence_file(evidence_dir, 99)  # must not raise


def test_discard_evidence_file_logs_swallowed_oserror(tmp_path, monkeypatch) -> None:
    # LOW (rf-reviewer): a silently-failed delete re-opens the exact leak this
    # function exists to close — the rejected file survives for
    # _find_evidence_code to pick up later. The failure must be traceable.
    log = tmp_path / "debug.log"
    monkeypatch.setenv("OMC_THROUGHPUT_DEBUG", str(log))
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "source-001.md").write_text("URL: https://x.test\n\ncontent", encoding="utf-8")

    def _boom(self, missing_ok=False):
        raise OSError("simulated permission denied")

    monkeypatch.setattr(Path, "unlink", _boom)

    rf._discard_evidence_file(evidence_dir, 1)  # must not raise

    logged = log.read_text()
    assert "_discard_evidence_file: unlink failed idx=1" in logged


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


def test_claims_extraction_rejects_oversized_quote() -> None:
    # Hard backstop against a model that copies a whole raw block instead of
    # a 10-40 word excerpt (B: "embedded quotes get a hard length cap").
    content = "Pi ships a planner. " + ("filler word " * 100) + "and an executor loop."
    reply_text = (
        "CLAIM: Pi ships a planner and executor loop.\n"
        f"QUOTE: {('filler word ' * 100).strip()}\n"
        "SUBJECTS: Pi\n"
    )
    claims = rf._extract_claims_from_source("https://x.test/pi", content, ["Pi"], _client(reply_text))
    assert claims == []


def test_is_markup_dense_quote_rejects_scraped_math_links() -> None:
    quote = "[{eq1}](https://wikimedia.org/api/rest_v1/media/math/render/svg/a)"
    assert rf._is_markup_dense_quote(quote) is True


def test_is_markup_dense_quote_accepts_real_prose() -> None:
    quote = "Pi is a minimal agent harness designed to stay small at the core."
    assert rf._is_markup_dense_quote(quote) is False


def test_claims_extraction_rejects_markup_dense_quote() -> None:
    # B: "a quote that is mostly markup/URLs (non-prose density) should be
    # rejected as an embed candidate entirely" — even when it IS a verbatim
    # substring of the page (a real LaTeX math-render dump quotes cleanly).
    markup_quote = "[{eq1}](https://wikimedia.org/api/rest_v1/media/math/render/svg/a)"
    content = f"Pi is a minimal agent harness. {markup_quote} describes a formula."
    reply_text = f"CLAIM: Pi is described with a formula.\nQUOTE: {markup_quote}\nSUBJECTS: Pi\n"
    claims = rf._extract_claims_from_source("https://x.test/pi", content, ["Pi"], _client(reply_text))
    assert claims == []


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


def test_drop_ungrounded_sentences_drops_fabricated_citation() -> None:
    # Live regression: the model cited https://www.piday.org/million/ from
    # training knowledge while writing a "results are insufficient" aside —
    # that URL was never in the claims it was given.
    claims = [{"claim": "Pi ships a CLI.", "url": "https://x.test/pi"}]
    text = (
        "Pi ships a CLI (https://x.test/pi).\n\n"
        "The current search results are insufficient to analyze Craft "
        "(https://www.piday.org/million/)."
    )
    out = rf._drop_ungrounded_sentences(text, claims)
    assert "piday.org" not in out
    assert "https://x.test/pi" in out


def test_drop_ungrounded_sentences_tolerates_same_domain_path_mangle() -> None:
    # Team-lead ruling: DOMAIN-level, not exact-URL, for body prose — live
    # data showed 23/23 real drops were the SAME domain with a truncated
    # path (a weak model citing a real claim's URL slightly wrong), never an
    # actual fabrication. Exact matching was over-strict and cost real
    # content for zero grounding gain; cross-domain fabrication still dies
    # (see test_drop_ungrounded_sentences_drops_fabricated_citation above).
    claims = [{"claim": "Pi exposes an SDK.", "url": "https://pi.dev/docs/latest/sdk"}]
    text = "Pi exposes an SDK for programmatic use (https://pi.dev/docs/latest)."
    out = rf._drop_ungrounded_sentences(text, claims)
    assert out == text


def test_drop_ungrounded_sentences_keeps_uncited_prose() -> None:
    # The invariant is "no URL outside claims" — it says nothing about a
    # sentence with NO url at all; that's the refusal-prose concern, handled
    # by the section prompt (C), not this mechanical check.
    claims = [{"claim": "Pi ships a CLI.", "url": "https://x.test/pi"}]
    text = "Pi ships a CLI (https://x.test/pi). The evidence here is otherwise thin."
    out = rf._drop_ungrounded_sentences(text, claims)
    assert out == text


def test_drop_ungrounded_sentences_only_removes_the_bad_sentence() -> None:
    claims = [{"claim": "Pi ships a CLI.", "url": "https://x.test/pi"}]
    text = "Pi ships a CLI (https://x.test/pi). Craft is unrelated (https://fabricated.example/)."
    out = rf._drop_ungrounded_sentences(text, claims)
    assert "https://x.test/pi" in out
    assert "fabricated.example" not in out


def test_drop_ungrounded_sentences_never_touches_an_embedded_code_fence() -> None:
    # Live regression: a weak model's raw section response embedded an
    # unprompted Python demo containing a placeholder URL
    # (https://api.example.com/data) — sentence-splitting the whole code
    # block as if it were prose silently discarded it wholesale over that
    # one ungrounded URL. This call runs on RAW pre-splice section text, so
    # it must not assume the model's own response is fence-free.
    claims = [{"claim": "Pi ships a CLI.", "url": "https://x.test/pi"}]
    code = '```python\ndata = fetch("https://api.example.com/data")\nprint(data.strip())\n```'
    text = f"Pi ships a CLI (https://x.test/pi).\n\n{code}"
    out = rf._drop_ungrounded_sentences(text, claims)
    assert code in out


def test_drop_ungrounded_sentences_artifact_wide_catches_exec_summary() -> None:
    # SCOPE NOTE 1 (team-lead): the per-section application only sees each
    # section's own claims slice; a stray fabricated URL in the Executive
    # Summary (gemma's π-drift, model-memory citation) must still be caught
    # by the final whole-document sweep.
    claims = [{"claim": "Pi ships a CLI.", "url": "https://x.test/pi"}]
    text = (
        "# Report\n\n## Executive Summary\n\n"
        "Pi ships a CLI (https://x.test/pi). The constant pi is well-studied "
        "(https://en.wikipedia.org/wiki/Pi).\n\n"
        "## References\n\n- https://x.test/pi\n"
    )
    out = rf._drop_ungrounded_sentences_artifact_wide(text, claims)
    assert "https://x.test/pi" in out
    assert "wikipedia.org" not in out


def test_drop_ungrounded_sentences_artifact_wide_never_touches_code_fences() -> None:
    # A stray period inside a code fence (a method call, a comment) is not a
    # sentence boundary, and code is not prose to citation-check — fenced
    # blocks must survive byte-for-byte even when they "cite" no URL at all.
    claims = [{"claim": "Pi ships a CLI.", "url": "https://x.test/pi"}]
    code = "```python\nimport os. path\nprint('hello.')\n```"
    text = f"# Report\n\n## Body\n\nPi ships a CLI (https://x.test/pi).\n\n{code}\n"
    out = rf._drop_ungrounded_sentences_artifact_wide(text, claims)
    assert code in out


def test_drop_ungrounded_sentences_artifact_wide_skips_references_list() -> None:
    # _rebuild_references_from_claims already guarantees References ⊆ claims;
    # the sweep must not touch that section at all (it's a bullet list, not
    # prose, and re-processing it is pure risk with no benefit).
    claims = [{"claim": "Pi ships a CLI.", "url": "https://x.test/pi"}]
    text = "# Report\n\n## Body\n\nPi ships a CLI (https://x.test/pi).\n\n## References\n\n- https://x.test/pi\n"
    out = rf._drop_ungrounded_sentences_artifact_wide(text, claims)
    assert out == text


def test_section_writer_drops_trailing_unclosed_fence() -> None:
    # Live gemma failure mode: a plain-prose reply ends with a bare opening fence
    # and no body/closer.
    claims = [{"claim": "Pi ships a planner.", "quote": "q", "url": "https://x.test/pi", "subjects": ["Pi"]}]
    reply = "Pi ships a planner (https://x.test/pi).\n\n```python"
    body = rf._write_section("Key Findings", "study Pi", claims, _client(reply))
    assert "```" not in body
    assert body.count("```") == 0


def test_section_writer_fails_open_without_client() -> None:
    body = rf._write_section("Key Findings", "study Pi", [], None)
    assert body.startswith("_(")


def test_structure_bearing_quote_becomes_blockquote_not_a_heading() -> None:
    # Live bug: a raw multi-line README quote with markdown structure rendered
    # as a giant mid-document heading.
    quote = "Example extensions demonstrating:\n# Lifecycle event handlers\n* item one"
    claims = [{"claim": "Pi ships extensions.", "quote": quote, "url": "https://x.test/pi", "subjects": ["Pi"]}]
    reply = f'Pi ships extensions (https://x.test/pi): "{quote}"'
    body = rf._write_section("Evidence and Analysis", "study Pi", claims, _client(reply))
    assert "\n# Lifecycle event handlers" not in body  # raw heading line gone
    assert "> # Lifecycle event handlers" in body  # neutralized into a blockquote
    assert artifact_lint.lint_artifact(f"## Evidence and Analysis\n\n{body}") == []


def test_shared_quote_across_claims_embedded_only_once() -> None:
    quote = "a shared verbatim sentence from the source"
    claims = [
        {"claim": "Claim A.", "quote": quote, "url": "https://x.test/a", "subjects": ["Pi"]},
        {"claim": "Claim B.", "quote": quote, "url": "https://x.test/b", "subjects": ["Pi"]},
    ]
    reply = f'First mention: "{quote}" (https://x.test/a). Restated again: "{quote}" (https://x.test/b).'
    body = rf._write_section("Key Findings", "study Pi", claims, _client(reply))
    assert body.count(quote) == 1


def test_splice_code_drops_unbalanced_fence_from_llm_fallback(tmp_path) -> None:
    # No evidence/*.md files → _find_evidence_code finds nothing → falls back to
    # the LLM call, which here (as observed live with gemma) returns an opening
    # fence with no closer.
    section_text = "Some section prose."
    out = rf._splice_code(section_text, tmp_path, _client("```python"))
    assert out == section_text
    assert "```" not in out


# ---------------------------------------------------------------------------
# CODE EXAMPLES — per-subject + integration (user: "sample code also needs
# to show the integration")
# ---------------------------------------------------------------------------


def test_splice_subject_code_grounded_in_only_that_subjects_claims() -> None:
    claims = [
        {"claim": "Pi ships a CLI.", "quote": "q", "url": "https://x.test/pi", "subjects": ["Pi"]},
        {"claim": "Craft ships an API.", "quote": "q", "url": "https://x.test/craft", "subjects": ["Craft"]},
    ]
    out = rf._splice_subject_code("Some prose.", "Pi", claims, _client("```python\nprint('pi cli')\n```"))
    assert "Example: Pi." in out
    assert "print('pi cli')" in out


def test_splice_subject_code_noop_without_subject_claims() -> None:
    claims = [{"claim": "Craft ships an API.", "quote": "q", "url": "u", "subjects": ["Craft"]}]
    out = rf._splice_subject_code("Some prose.", "Pi", claims, _client("```python\nprint(1)\n```"))
    assert out == "Some prose."


def test_splice_subject_code_rejects_syntax_error() -> None:
    claims = [{"claim": "Pi ships a CLI.", "quote": "q", "url": "u", "subjects": ["Pi"]}]
    bad = "```python\nx = 1 — not valid python\n```"
    out = rf._splice_subject_code("Some prose.", "Pi", claims, _client(bad))
    assert out == "Some prose."


def test_integration_interfaces_grounded_true_when_named_in_claims() -> None:
    claims = [{"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u"}]
    block = "```python\nclient.call_mcp_server()\n```"
    assert rf._integration_interfaces_grounded(block, claims) is True


def test_integration_interfaces_grounded_false_when_invented() -> None:
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"], "url": "u"}]
    block = "```python\nclient.call_grpc_endpoint()\n```"
    assert rf._integration_interfaces_grounded(block, claims) is True  # "grpc"/"endpoint" aren't interface words — nothing to check, falls open


def test_integration_interfaces_grounded_false_when_named_but_ungrounded() -> None:
    # "webhook" is snake_case-embedded (realistic generated code) — the tokenizer
    # must isolate it despite the underscore, unlike a bare \b regex which treats
    # "_" as a word char and would never see it.
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"], "url": "u"}]
    block = "```python\nclient.register_webhook_url()\n```"
    assert rf._integration_interfaces_grounded(block, claims) is False


def test_splice_integration_code_captions_as_proposed_not_source() -> None:
    claims = [{"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u"}]
    reply = "```python\ndef call_mcp_server():\n    pass\n```"
    out = rf._splice_integration_code("Some prose.", ["Pi", "Craft"], claims, "Pi calls Craft via MCP", _client(reply))
    assert "Proposed usage" in out
    assert "not quoted source code" in out
    assert "def call_mcp_server" in out


def test_splice_integration_code_rejects_invented_interface() -> None:
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"], "url": "u"}]
    reply = "```python\nclient.register_webhook_url()\n```"
    out = rf._splice_integration_code("Some prose.", ["Pi", "Craft"], claims, "unknown", _client(reply))
    assert out == "Some prose."


def test_splice_integration_code_noop_for_single_subject() -> None:
    out = rf._splice_integration_code("Some prose.", ["Pi"], [], "x", _client("```python\npass\n```"))
    assert out == "Some prose."


# ---------------------------------------------------------------------------
# DIAGRAM — Pi<->Craft integration clusters (user emphasis: show integration)
# ---------------------------------------------------------------------------


def test_integration_label_grounded_in_joint_claim_with_mechanism() -> None:
    claims = [{"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u"}]
    assert rf._integration_label(["Pi", "Craft"], claims) == "mcp"


def test_integration_label_generic_when_joint_claim_names_no_mechanism() -> None:
    claims = [{"claim": "Pi and Craft are both used to build agents.", "subjects": ["Pi", "Craft"], "url": "u"}]
    assert rf._integration_label(["Pi", "Craft"], claims) == "integration"


def test_integration_label_upgrades_to_mechanism_when_corroborated() -> None:
    # Team-lead correction: the mechanism HYPOTHESIS may upgrade a generic
    # "integration" label only when independently corroborated — its own
    # words show up in the joint claims' text, not just asserted.
    claims = [{"claim": "Pi and Craft are both used to build agents via a delegation pattern.",
               "subjects": ["Pi", "Craft"], "url": "u"}]
    label = rf._integration_label(["Pi", "Craft"], claims, "Pi calls Craft via a delegation pattern")
    assert label == "Pi calls Craft via a delegation pattern"


def test_integration_label_stays_generic_when_mechanism_uncorroborated() -> None:
    # The hypothesis directs search/prompts but never becomes content by
    # itself — an uncorroborated mechanism must not print as the edge label.
    claims = [{"claim": "Pi and Craft are both used to build agents.", "subjects": ["Pi", "Craft"], "url": "u"}]
    label = rf._integration_label(["Pi", "Craft"], claims, "Pi calls Craft via an obscure webhook relay")
    assert label == "integration"


def test_integration_label_excludes_multi_word_subject_tokens() -> None:
    claims = [{
        "claim": "Alpha Product and Beta Product are both used for operations.",
        "subjects": ["Alpha Product", "Beta Product"],
        "url": "u",
    }]
    label = rf._integration_label(
        ["Alpha Product", "Beta Product"],
        claims,
        "Alpha Product calls Beta Product via gRPC",
    )
    assert label == "integration"


def test_integration_label_upgrades_via_verify_terms_too() -> None:
    # No interface word here (that path is checked first, unchanged) — only
    # the verify term "bridging" ties the hypothesis to real evidence.
    claims = [{"claim": "Pi and Craft share skills through a bridging layer.",
               "subjects": ["Pi", "Craft"], "url": "u"}]
    label = rf._integration_label(
        ["Pi", "Craft"], claims, "some untested hypothesis", ["bridging"]
    )
    assert label == "some untested hypothesis"


def test_integration_label_from_each_sides_own_documented_interface() -> None:
    claims = [
        {"claim": "Pi exposes a CLI for automation.", "subjects": ["Pi"], "url": "u1"},
        {"claim": "Craft supports API access for extensions.", "subjects": ["Craft"], "url": "u2"},
    ]
    label = rf._integration_label(["Pi", "Craft"], claims)
    assert label is not None and "cli" in label and "api" in label


def test_integration_label_none_when_ungrounded() -> None:
    # Neither a joint claim nor a per-side documented interface — must not invent one.
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"], "url": "u"}]
    assert rf._integration_label(["Pi", "Craft"], claims) is None


def test_fallback_cluster_diagram_none_without_grounding() -> None:
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"], "url": "u"}]
    assert rf._fallback_cluster_diagram(["Pi", "Craft"], claims) is None


def test_fallback_cluster_diagram_has_subgraphs_and_cross_edge() -> None:
    claims = [{"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u"}]
    body = rf._fallback_cluster_diagram(["Pi", "Craft"], claims)
    assert body is not None
    assert 'subgraph' in body and '["Pi"]' in body and '["Craft"]' in body
    assert "mcp" in body.lower()


def test_splice_diagram_rejects_pi_only_architecture() -> None:
    # v7 failure mode: the model names components for only ONE subject.
    reply = (
        "COMPONENT: Planner | Pi | plans steps\n"
        "COMPONENT: Executor | Pi | runs tools\n"
        "EDGE: Planner -> Executor\n"
    )
    claims = [{"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u"}]
    section_text = "Pi ships a Planner and an Executor. Craft is an agent tool."
    out = rf._splice_diagram(section_text, ["Pi", "Craft"], claims, _client(reply))
    # No grounded LLM cluster diagram (Craft-side has zero components), and no
    # fallback to the bare-subject-name 2-node diagram either (user review:
    # a trivial "Pi -->|integrates with| Craft" diagram fails acceptance) —
    # no diagram beats a trivial one, section text ships unchanged.
    assert out == section_text


def test_splice_diagram_accepts_grounded_two_cluster_diagram() -> None:
    reply = (
        "COMPONENT: Pi Agent Loop | Pi | runs the agent loop\n"
        "COMPONENT: Craft Agents | Craft | open source agent interface\n"
        "EDGE: Pi Agent Loop -> Craft Agents | MCP\n"
    )
    claims = [{"claim": "Craft Agents connects to the Pi Agent Loop via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u"}]
    section_text = "Pi Agent Loop runs the agent loop. Craft Agents is an open source agent interface."
    out = rf._splice_diagram(section_text, ["Pi", "Craft"], claims, _client(reply))
    assert "```mermaid" in out
    assert out.count("subgraph") == 2
    assert "-->" in out and "MCP" in out


def test_pick_subject_home_picks_section_mentioning_subject_most() -> None:
    written = {
        "Key Findings": "Pi ships a planner. Pi also ships an executor.",
        "Evidence and Analysis": "Craft is an agent tool.",
    }
    assert rf._pick_subject_home(written, "Pi", exclude=set()) == "Key Findings"
    assert rf._pick_subject_home(written, "Craft", exclude=set()) == "Evidence and Analysis"


def test_pick_subject_home_respects_exclude() -> None:
    written = {"Key Findings": "Pi Pi Pi", "Background": "Pi"}
    assert rf._pick_subject_home(written, "Pi", exclude={"Key Findings"}) == "Background"


def test_splice_subject_diagram_adds_captioned_grounded_diagram() -> None:
    # Team-lead ruling (option b): grounded against the subject's OWN claims,
    # not the home section's rendered prose — this text deliberately does NOT
    # repeat the component names, proving the section text isn't what grounds it.
    text = "Pi has a well-documented internal design."
    claims = [
        {"claim": "The Planner builds a plan and the Executor runs tools.", "subjects": ["Pi"]},
        {"claim": "The Memory store persists state for the Scorer to grade output.", "subjects": ["Pi"]},
    ]
    reply = (
        "COMPONENT: Planner | builds plan\nCOMPONENT: Executor | runs tools\n"
        "COMPONENT: Memory | persists state\nCOMPONENT: Scorer | grades output\n"
        "EDGE: Planner -> Executor\nEDGE: Executor -> Scorer\n"
    )
    out = rf._splice_subject_diagram(text, "Pi", claims, _client(reply))
    assert "```mermaid" in out
    assert "Architecture: Pi." in out
    assert artifact_lint.lint_artifact(out) == []


def test_splice_subject_diagram_ignores_other_subjects_claims() -> None:
    # Grounding is scoped to THIS subject's claims — a component only
    # mentioned in Craft's claims must not ground a Pi diagram.
    text = "Pi has a well-documented internal design."
    claims = [{"claim": "Craft has a Planner and an Executor.", "subjects": ["Craft"]}]
    reply = "COMPONENT: Planner | builds plan\nCOMPONENT: Executor | runs tools\nEDGE: Planner -> Executor\n"
    out = rf._splice_subject_diagram(text, "Pi", claims, _client(reply))
    assert out == text


def test_splice_subject_diagram_noop_when_ungrounded() -> None:
    text = "Some short prose."
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"]}]
    out = rf._splice_subject_diagram(text, "Pi", claims, _client("COMPONENT: Zorptron | invented\n"))
    assert out == text


def test_splice_subject_diagram_prompt_is_fed_claims_not_home_section_prose() -> None:
    # Option-b was HALF-applied (v35, team-lead-verified): the grounding CHECK
    # read claims, but the PROMPT still asked the LLM to invent components
    # from home-section prose. This home-section text deliberately contains
    # NEITHER real component name so the prompt content can only have come
    # from claims, not from ``text``, if the fix is in place.
    text = "This section briefly mentions the subject in passing."
    claims = [
        {"claim": "The Planner builds a plan and the Executor runs tools.", "subjects": ["Pi"]},
    ]
    captured: dict[str, list] = {}

    def _capture_chat(messages, tools=None):
        captured["messages"] = messages
        return SimpleNamespace(text="COMPONENT: Planner | builds plan\nCOMPONENT: Executor | runs tools\n")

    rf._splice_subject_diagram(text, "Pi", claims, SimpleNamespace(chat=_capture_chat))
    prompt = captured["messages"][0]["content"]
    assert "Planner" in prompt and "Executor" in prompt  # claims text made it in
    assert "This section briefly mentions" not in prompt  # home-section prose did not


def test_splice_subject_diagram_no_llm_call_when_subject_has_no_claims() -> None:
    calls: list[object] = []

    def _capture_chat(messages, tools=None):
        calls.append(messages)
        return SimpleNamespace(text="COMPONENT: Planner | builds plan\n")

    text = "Pi has a well-documented internal design."
    claims = [{"claim": "Craft has a Planner.", "subjects": ["Craft"]}]
    out = rf._splice_subject_diagram(text, "Pi", claims, SimpleNamespace(chat=_capture_chat))
    assert out == text
    assert calls == []  # no claims for "Pi" — never worth an LLM round trip


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


def test_summary_prompt_includes_relationship_instruction_when_mechanism_given() -> None:
    captured: dict[str, list] = {}

    def _capture_chat(messages, tools=None):
        captured["messages"] = messages
        return SimpleNamespace(text="summary")

    rf._write_summary(
        "study Pi and Craft", {"Key Findings": "text"}, SimpleNamespace(chat=_capture_chat),
        mechanism="Pi calls Craft via MCP",
    )
    prompt = captured["messages"][0]["content"]
    assert "conclusion on how the subjects relate" in prompt
    assert "Pi calls Craft via MCP" in prompt


# ---------------------------------------------------------------------------
# ASSEMBLE
# ---------------------------------------------------------------------------


def test_find_duplicate_headings_empty_when_none() -> None:
    text = "# Report\n\n## Executive Summary\n\nbody\n\n## Key Findings\n\nbody\n"
    assert rf._find_duplicate_headings(text) == []


def test_find_duplicate_headings_flags_a_repeat() -> None:
    # dedupe_sections is deliberately gone (team-lead ruling: duplicate
    # headings are impossible by construction in this linear pipeline) — this
    # is the tripwire that would catch it if that guarantee ever breaks.
    text = "# Report\n\n## Key Findings\n\nbody\n\n## Key Findings\n\nechoed body\n"
    assert rf._find_duplicate_headings(text) == ["Key Findings"]


def test_rebuild_references_from_claims_ignores_body_scraped_links() -> None:
    # Live regression: the old pipeline's scraper (rebuild_references_section)
    # harvests ANY URL-shaped string out of the rendered body, including ones
    # embedded inside quote content — LaTeX math-render SVG URLs, relative
    # wiki links, bare "source" placeholders. References must be built from
    # claims (ground truth of what was actually cited), not re-harvested from
    # rendered text — none of that junk can exist in claims.
    claims = [
        {"claim": "Pi ships a CLI.", "url": "https://pi.dev/"},
        {"claim": "Craft connects to APIs.", "url": "https://agents.craft.do/"},
    ]
    text = (
        "# Report\n\n## Body\n\n"
        "Pi ships a CLI (https://pi.dev/). "
        "[source](https://en.wikipedia.org/wiki/Pi) "
        "[{eq1}](https://wikimedia.org/api/rest_v1/media/math/render/svg/a) "
        "[{eq2}](https://wikimedia.org/api/rest_v1/media/math/render/svg/b) "
        "[{eq3}](https://wikimedia.org/api/rest_v1/media/math/render/svg/c)\n\n"
        "## References\n\n- placeholder\n"
    )
    out = rf._rebuild_references_from_claims(text, claims)
    refs = out.rsplit("## References", 1)[1]
    assert "wikimedia.org" not in refs
    assert "wikipedia.org" not in refs
    assert "placeholder" not in refs
    entries = {ln.removeprefix("- ") for ln in refs.strip().splitlines()}
    assert entries == {"https://pi.dev/", "https://agents.craft.do/"}


def test_references_never_contain_markdown_link_syntax() -> None:
    # Deterministic regression check (team-lead, v9 line 109): a References
    # entry must never be markdown-link syntax — the old scraper's
    # "[source](url)" captions (from _splice_code's own caption text) leaked
    # through as literal References entries; claims-driven building can't
    # produce that shape, it only ever emits bare URLs.
    claims = [{"claim": "Pi ships a CLI.", "url": "https://pi.dev/"}]
    text = (
        "# Report\n\nPi ships a CLI (https://pi.dev/). "
        "[source](https://nader.substack.com/p/how-to-build-a-custom-agent-framework)\n\n"
        "## References\n\n- [source](https://nader.substack.com/p/how-to-build-a-custom-agent-framework)\n"
    )
    out = rf._rebuild_references_from_claims(text, claims)
    refs = out.rsplit("## References", 1)[1]
    assert "[" not in refs and "](" not in refs


def test_references_url_set_never_exceeds_claims() -> None:
    # Deterministic regression check (team-lead): no References URL may be
    # absent from claims.jsonl — References == claim URLs, both directions.
    claims = [{"claim": "Pi ships a CLI.", "url": "https://pi.dev/"}]
    text = (
        "# Report\n\nPi ships a CLI (https://pi.dev/), plus wrong-referent "
        "noise (https://pypi.org/project/agent-framework/) "
        "(https://www.piday.org/million/) (https://en.wikipedia.org/wiki/Pi).\n\n"
        "## References\n\n- https://pypi.org/project/agent-framework/\n"
        "- https://www.piday.org/million/\n"
    )
    out = rf._rebuild_references_from_claims(text, claims)
    refs = out.rsplit("## References", 1)[1]
    ref_urls = {ln.removeprefix("- ").strip() for ln in refs.strip().splitlines() if ln.strip()}
    assert ref_urls == {c["url"] for c in claims}


def test_rebuild_references_from_claims_noop_without_heading() -> None:
    text = "# Report\n\nNo references heading here.\n"
    assert rf._rebuild_references_from_claims(text, [{"url": "https://x.test"}]) == text


def test_rebuild_references_from_claims_noop_without_claims() -> None:
    text = "# Report\n\n## References\n\n- old\n"
    assert rf._rebuild_references_from_claims(text, []) == text


def test_sanitize_section_headings_strips_leading_self_heading() -> None:
    # gemma echoes the section's own title as a leading '## ...'; _assemble adds
    # the '## name' itself, so the echo must go or it duplicates.
    text = "## What is Pi\n\nPi is an agent runtime (https://pi.dev)."
    out = rf._sanitize_section_headings(text, ["What is Pi", "References"])
    assert not out.lstrip().startswith("#")
    assert "Pi is an agent runtime" in out


def test_sanitize_section_headings_drops_sibling_echo_and_demotes_unknown() -> None:
    text = (
        "Body opens with prose.\n\n"
        "## References\n\n"          # sibling-section echo → dropped
        "More prose here.\n\n"
        "## Key Features\n\n"        # unknown heading → demoted to ###
        "Detail."
    )
    out = rf._sanitize_section_headings(text, ["What is Pi", "References"])
    lines = out.splitlines()
    assert "## References" not in out
    assert "### Key Features" in lines
    assert "## Key Features" not in lines


def test_sanitize_section_headings_is_fence_aware() -> None:
    # '#'-comment lines inside a code fence are NOT markdown headings.
    text = (
        "Prose before.\n\n"
        "```python\n"
        "# this is a comment, not a heading\n"
        "def f():\n    return 1\n"
        "```\n\n"
        "## Extra\n"                 # real heading after the fence → demoted
    )
    out = rf._sanitize_section_headings(text, ["Intro"])
    lines = out.splitlines()
    assert "# this is a comment, not a heading" in out  # fence interior untouched
    assert "### Extra" in lines
    assert "## Extra" not in lines


def test_sanitize_section_headings_keeps_deep_headings_after_leading() -> None:
    # a second consecutive leading heading is real sub-structure, not the echo.
    text = "## Overview\n\n### Details\n\nProse."
    out = rf._sanitize_section_headings(text, ["Overview"])
    assert "## Overview" not in out          # leading echo stripped
    assert "### Details" in out              # deeper heading preserved


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
