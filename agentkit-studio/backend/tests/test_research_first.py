"""Tests for studio.research_first — the research-first generation pipeline.

Deterministic, no network: LLM calls are faked with literal strings (mirrors
test_structural_producer.py / test_diagram_render.py). Fetching/caching is not
exercised here (that's studio.tools' job, already covered by test_tools.py /
test_fetch_cache.py); these tests cover FRAME extraction, CLAIMS grounding,
per-section writing, summary-last ordering, and final assembly cleanliness.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from studio import artifact_lint, diagram_render, research_first as rf


def _client(text: str) -> SimpleNamespace:
    return SimpleNamespace(chat=lambda messages, tools=None: SimpleNamespace(text=text))


def _ref_urls(refs_block: str) -> set[str]:
    """URLs from a rendered References block, format-agnostic: handles both the
    numbered-titled ``N. [title](url)`` and bare ``N. url`` / ``- url`` shapes."""
    urls: set[str] = set()
    for ln in refs_block.strip().splitlines():
        if not ln.strip():
            continue
        m = re.search(r"\((https?://[^)]+)\)", ln) or re.search(r"(https?://\S+)", ln)
        if m:
            urls.add(m.group(1))
    return urls


def _rel(
    kind: str = "cooperates",
    descriptor: str = "Integration",
    mechanism: str = "",
    terms: list[str] | None = None,
) -> rf.Relationship:
    return rf.Relationship(kind, descriptor, mechanism, terms or [])


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


def test_ensure_relationship_section_added_for_multi_subject() -> None:
    sections = ["Executive Summary", "Key Findings", "References"]
    rel = _rel(descriptor="Integration")
    out = rf._ensure_relationship_section(sections, ["Pi", "Craft"], rel)
    home = rf._pick_relationship_home(out, rel)
    assert home is not None
    # named from the per-task descriptor, not the hardcoded word "Integration"
    assert home == "Pi and Craft: Integration"
    assert out.index(home) < out.index("References")


def test_ensure_relationship_section_named_from_descriptor() -> None:
    # R3: the section title comes from the LLM-derived descriptor per task.
    sections = ["Executive Summary", "Key Findings", "References"]
    rel = _rel(kind="extends", descriptor="Extension model")
    out = rf._ensure_relationship_section(sections, ["Pi", "Craft"], rel)
    assert "Pi and Craft: Extension model" in out
    assert rf._pick_relationship_home(out, rel) == "Pi and Craft: Extension model"


def test_ensure_relationship_section_noop_for_single_subject() -> None:
    sections = ["Executive Summary", "Key Findings", "References"]
    rel = _rel()
    out = rf._ensure_relationship_section(sections, ["Pi"], rel)
    assert out == sections
    assert rf._pick_relationship_home(out, rel) is None


def test_ensure_relationship_section_noop_for_independent() -> None:
    # independent subjects → no forced relationship section.
    sections = ["Executive Summary", "Key Findings", "References"]
    rel = _rel(kind="independent", descriptor="Relationship")
    out = rf._ensure_relationship_section(sections, ["Pi", "Craft"], rel)
    assert out == sections
    assert rf._pick_relationship_home(out, rel) is None


def test_ensure_relationship_section_noop_when_already_present() -> None:
    sections = ["Executive Summary", "Pi and Craft Integration", "References"]
    rel = _rel(descriptor="Integration")
    out = rf._ensure_relationship_section(sections, ["Pi", "Craft"], rel)
    assert out == sections


def test_frame_subjects_empty_when_no_subject_named() -> None:
    assert rf._extract_subjects([["include a diagram"]]) == []


def test_frame_drops_whole_task_subject_coverage_branch() -> None:
    requirement = "Study how to use Pi and Craft to develop agents and create a research report."
    groups = [
        ["covers Study how to use Pi and Craft to develop agents and create a research report"],
        ["covers Pi"],
        ["covers Craft"],
    ]
    assert rf._extract_subjects(groups, requirement=requirement) == ["Pi", "Craft"]


def test_subjects_from_requirement_fallback_validates_candidates() -> None:
    # v39 regression class: covers-extraction degenerated to the whole task and
    # the [title] fallback reinstated a whole-task-shaped single subject. The
    # LLM fallback proposes; deterministic validation gates.
    requirement = (
        "Study how to use Pi and Craft to develop agents and create a research "
        "report, need to include example code and design architecture."
    )
    client = _client("SUBJECTS: Pi, Craft")
    assert rf._subjects_from_requirement(client, requirement) == ["Pi", "Craft"]


def test_subjects_from_requirement_rejects_whole_task_and_hallucinations() -> None:
    requirement = "Study how to use Pi and Craft to develop agents."
    echo = _client(
        "SUBJECTS: Study how to use Pi and Craft to develop agents, LangChain"
    )
    # whole-task echo normalizes to the base task -> rejected; LangChain is not
    # a verbatim substring of the requirement -> rejected.
    assert rf._subjects_from_requirement(echo, requirement) == []


def test_subjects_from_requirement_splits_compound_and_candidate() -> None:
    requirement = "Study how to use Pi and Craft to develop agents."
    client = _client("SUBJECTS: Pi and Craft")
    assert rf._subjects_from_requirement(client, requirement) == ["Pi", "Craft"]


def test_subjects_from_requirement_fails_open() -> None:
    requirement = "Study how to use Pi and Craft to develop agents."

    def _boom(messages, tools=None):  # noqa: ANN001, ANN202
        raise RuntimeError("llm down")

    assert rf._subjects_from_requirement(SimpleNamespace(chat=_boom), requirement) == []
    assert rf._subjects_from_requirement(None, requirement) == []
    assert rf._subjects_from_requirement(_client("no marker here"), requirement) == []


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


def test_write_section_drops_unprompted_fences_and_patch_metadata() -> None:
    client = _client(
        "Useful prose with a citation (https://example.com/source).\n\n"
        "```python\nprint('ungated example')\n```\n\n"
        "ARTICLE_TITLE: leaked search record\n"
        "### RESEARCH_FINDING\n"
        "- PATCH_TARGET: Key Findings\n"
        "  * SEARCH: ok\n"
        "1. ARTICLE_TITLE: numbered leak\n"
        "+ PATCH_TARGET: plus leak\n"
        "> SEARCH: error quoted leak"
    )
    text = rf._write_section(
        "Scope",
        "study subject",
        [{"claim": "Useful prose exists.", "quote": "Useful prose", "url": "https://example.com/source"}],
        client,
    )
    assert "```" not in text
    assert "ARTICLE_TITLE" not in text
    assert "RESEARCH_FINDING" not in text
    assert "PATCH_TARGET" not in text
    assert "SEARCH:" not in text
    assert "numbered leak" not in text
    assert "plus leak" not in text
    assert "quoted leak" not in text


def test_write_section_preserves_reader_facing_url_and_search_headings() -> None:
    body = (
        "## URL: Evidence Index\n\n"
        "This section explains URL governance.\n\n"
        "## Search: Method\n\n"
        "This section explains search method design.\n\n"
        "URL: Evidence Index\n"
        "SEARCH: Method"
    )

    out = rf._strip_patch_metadata_lines(body)

    assert "## URL: Evidence Index" in out
    assert "## Search: Method" in out
    assert "URL: Evidence Index" in out
    assert "SEARCH: Method" in out


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


def test_classify_relationship_cooperates() -> None:
    judge = _client(
        "KIND: cooperates\nDESCRIPTOR: Integration\n"
        "MECHANISM: Pi calls Craft via an MCP server\nTERMS: MCP, plugin"
    )
    rel = rf._classify_relationship(
        ["Pi", "Craft"], {"Pi": "Pi Agent Framework", "Craft": "Craft Agents"},
        "study Pi and Craft", judge,
    )
    assert rel.kind == "cooperates"
    assert rel.descriptor == "Integration"
    assert rel.mechanism == "Pi calls Craft via an MCP server"
    assert rel.mechanism_terms == ["MCP", "plugin"]


def test_classify_relationship_competes_has_no_integration_mechanism() -> None:
    judge = _client(
        "KIND: competes\nDESCRIPTOR: Comparison\n"
        "MECHANISM: they target the same problem with different trade-offs\n"
        "TERMS: latency, cost"
    )
    rel = rf._classify_relationship(
        ["Redis", "Memcached"], {"Redis": "Redis", "Memcached": "Memcached"},
        "compare Redis and Memcached", judge,
    )
    assert rel.kind == "competes"
    assert rel.descriptor == "Comparison"
    assert rel.mechanism_terms == ["latency", "cost"]


def test_classify_relationship_unknown_kind_falls_back_to_unknown() -> None:
    # A KIND the model invents outside the allowed set is not trusted.
    judge = _client("KIND: entangled\nDESCRIPTOR: Whatever\nMECHANISM: m\nTERMS: t")
    rel = rf._classify_relationship(
        ["A", "B"], {"A": "A", "B": "B"}, "study A and B", judge
    )
    assert rel.kind == "unknown"


def test_classify_relationship_fails_open_without_judge() -> None:
    rel = rf._classify_relationship(
        ["Pi", "Craft"], {"Pi": "Pi", "Craft": "Craft"}, "study Pi and Craft", None
    )
    assert rel.kind == "unknown"
    assert rel.mechanism == ""
    assert rel.mechanism_terms == []
    assert rel.descriptor  # non-empty neutral descriptor for section naming


def test_classify_relationship_skipped_for_single_subject() -> None:
    rel = rf._classify_relationship(
        ["Pi"], {"Pi": "Pi"}, "study Pi", _client("KIND: cooperates\nDESCRIPTOR: X")
    )
    assert rel.kind == "unknown"


def test_classify_relationship_fails_open_on_exception() -> None:
    def _boom(messages, tools=None):  # noqa: ANN001, ANN202
        raise RuntimeError("judge down")

    rel = rf._classify_relationship(
        ["A", "B"], {"A": "A", "B": "B"}, "study A and B", SimpleNamespace(chat=_boom)
    )
    assert rel.kind == "unknown"


def test_research_joint_queries_and_assumptions_derive_from_relationship(monkeypatch, tmp_path) -> None:
    # Team-lead rule: joint queries come FROM the classified relationship
    # (mechanism + terms), not generic subject-A+subject-B concatenation — and
    # the hypothesis is visible in the Scope Assumptions.
    queries_seen: list[str] = []
    monkeypatch.setattr(rf, "_search", lambda query, results=5: queries_seen.append(query) or [])
    judge = _client(
        "KIND: cooperates\nDESCRIPTOR: X\nANCHORS: a, b\n"
        "MECHANISM: Pi calls Craft via an MCP server\nTERMS: MCP, plugin"
    )
    _ledger, assumptions, relationship, _anchors = rf._research(
        ["Pi", "Craft"], tmp_path, "study Pi and Craft", judge, emit=None
    )
    assert relationship.kind == "cooperates"
    assert relationship.mechanism == "Pi calls Craft via an MCP server"
    assert relationship.mechanism_terms == ["MCP", "plugin"]
    assert any("Hypothesized relationship: Pi calls Craft via an MCP server." in a for a in assumptions)
    joint_queries = queries_seen[-2:]  # the 2 joint queries are issued last
    assert any("MCP" in q for q in joint_queries)


def test_research_independent_notes_independence_in_assumptions(monkeypatch, tmp_path) -> None:
    # independent kind → the Scope Assumptions note independence, not a
    # fabricated relationship mechanism.
    monkeypatch.setattr(rf, "_search", lambda query, results=5: [])
    judge = _client("KIND: independent\nDESCRIPTOR: Relationship\nANCHORS: a, b")
    _ledger, assumptions, relationship, _anchors = rf._research(
        ["Pi", "Craft"], tmp_path, "study Pi and Craft", judge, emit=None
    )
    assert relationship.kind == "independent"
    assert any("independent" in a.lower() for a in assumptions)


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
    ledger, _assumptions, _relationship, _anchors = rf._research(
        ["Pi", "Craft"], tmp_path, "study Pi and Craft", judge, emit=None
    )
    assert ledger["__joint__"] == []


def test_research_joint_loop_keeps_source_matching_union_anchor(monkeypatch, tmp_path) -> None:
    # A joint-ONLY integration page (named by no subject, so the new subject-url
    # guard doesn't reclaim it as a subject homepage) still lands in __joint__
    # when it hits a union anchor.
    monkeypatch.setattr(rf, "_search", lambda query, results=5: [SimpleNamespace(url="https://joint.test/good")])
    monkeypatch.setattr(rf, "_is_offtopic", lambda *a, **k: False)
    monkeypatch.setattr(
        rf, "_fetch_and_store", lambda url, evidence_dir, idx: "This page discusses mcp-bridge tool-calling integration."
    )
    judge = _client(
        "DESCRIPTOR: X\nANCHORS: mcp-bridge, tool-calling\n"
        "MECHANISM: A calls B via an MCP server\nVERIFY: MCP, plugin"
    )
    ledger, _assumptions, _relationship, _anchors = rf._research(
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


def test_claims_for_section_leads_with_different_claim_per_section() -> None:
    # Live regression (deterministic, 3/3 byte-identical, judge synthesis 0.0):
    # every same-cap section got the identical claim slice, so gemma opened each
    # with the same lead claim → "nearly identical paragraphs". Distinct sections
    # must lead with distinct claims (per-subject bucket rotation), while subject
    # coverage is preserved regardless of section name.
    claims = [{"claim": f"pi claim {i}", "subjects": ["Pi"]} for i in range(10)]
    claims += [{"claim": f"craft claim {i}", "subjects": ["Craft"]} for i in range(2)]
    # seq = the section's outline index (distinct per section); with buckets larger
    # than the section count each leads with a different claim.
    leads = {
        seq: rf._claims_for_section(claims, name, seq)[0]["claim"]
        for seq, name in enumerate(("Background and Context", "Key Findings", "Implications"))
    }
    assert len(set(leads.values())) == 3, leads  # all three sections lead differently
    for seq in leads:  # coverage still holds for each
        subs = {c["subjects"][0] for c in rf._claims_for_section(claims, "x", seq)}
        assert subs == {"Pi", "Craft"}


def test_plainer_markdown_renders_link_and_table_to_quotable_text() -> None:
    # Live regression: the Pi README states its architecture as `[@earendil-works/
    # pi-ai](url): Unified LLM API` rows; the package NAME lives inside link syntax,
    # so a verbatim quote could never name it (0/4 packages extracted). Rendering the
    # markup down makes the name+role a clean, quotable substring.
    md = "| **[@earendil-works/pi-ai](/pkgs/ai)** | Unified multi-provider LLM API |"
    out = rf._plainer_markdown(md)
    assert "@earendil-works/pi-ai" in out and "Unified multi-provider LLM API" in out
    assert "](" not in out and "**" not in out and "|" not in out


def test_strip_boilerplate_keeps_content_table_drops_nav_menu() -> None:
    # The nav-chrome heuristic must not eat a link-heavy CONTENT table (a package
    # list is links WITH descriptions); it should still drop a bare link menu (links
    # with no prose). Live: the package table was scored as chrome by raw link-char
    # density and stripped before extraction → 0/4 packages.
    nav = "[Home](/) [Docs](/docs) [About](/about) [Sign in](/login)"
    # CONCISE row (codex regression): a short `[name](url): role` line where the label
    # is >50% of the visible text must still survive — a ratio test wrongly stripped it.
    concise = "[pi-ai](/pkgs/ai): Unified LLM API"
    table = "| [pi-agent-core](/pkgs/agent) | Agent runtime with tool calling and state |"
    kept = rf._strip_boilerplate(nav + "\n\n" + concise + "\n\n" + table)
    assert "pi-ai" in kept and "Unified LLM API" in kept  # concise row survives
    assert "pi-agent-core" in kept and "Agent runtime with tool calling" in kept
    assert "Sign in" not in kept  # bare link menu dropped


def test_relation_triple_grounds_direction_and_rejects_reversed_or_invented() -> None:
    # KGGen/GraphRAG pattern: LLM emits an ordered SPO triple, code assembles the
    # edge. Both guardrails must hold: relation grounded in a joint claim (CON-1) and
    # direction confirmed by source-before-target in an active-voice claim (CON-2).
    joint = [{"claim": "Craft Agents utilizes both the Claude Agent SDK and the Pi SDK.",
              "subjects": ["Craft", "Pi"]}]

    class _C:
        def __init__(self, line): self._line = line
        def chat(self, _m):
            class _R: pass
            r = _R(); r.text = self._line; return r

    subjects = ["Craft", "Pi"]
    # Good: active direction, relation grounded in the cited claim → 4-tuple w/ claim.
    ok = diagram_render._extract_relation_triple(subjects, joint, _C("Craft | utilizes | Pi | 1"))
    assert ok[:3] == ("Craft", "utilizes", "Pi") and "utilizes" in ok[3]
    # CON-2: reversed direction rejected.
    assert diagram_render._extract_relation_triple(subjects, joint, _C("Pi | utilizes | Craft | 1")) is None
    # CON-1: fully invented relation rejected.
    assert diagram_render._extract_relation_triple(subjects, joint, _C("Craft | orchestrates delegation | Pi | 1")) is None
    # CON-1 (codex HIGH): partially-grounded invented phrase — 'sdk' is in the claim but
    # 'delegation' is not, so one grounded token must NOT launder the invented phrase.
    assert diagram_render._extract_relation_triple(subjects, joint, _C("Craft | sdk delegation | Pi | 1")) is None


def test_relation_triple_confirms_passive_voice_direction() -> None:
    # codex HIGH: 'B is used by A' → A->B. Positional order alone would reverse it;
    # the active/passive check against the cited claim resolves it correctly.
    joint = [{"claim": "The Pi SDK is used by Craft Agents as one of two backends.",
              "subjects": ["Craft", "Pi"]}]

    class _C:
        def chat(self, _m):
            class _R:
                text = "Craft | used by | Pi | 1"
            return _R()

    ok = diagram_render._extract_relation_triple(["Craft", "Pi"], joint, _C())
    assert ok and ok[0] == "Craft" and ok[2] == "Pi"  # passive resolved to Craft->Pi


def test_subject_code_drops_invented_sdk_when_no_evidenced_code() -> None:
    # Live regression: the Craft example invented `from craft_agents import Agent`
    # (a Python SDK in NO source) because the prompt demanded 'runnable' code from
    # prose claims naming no API. With no evidenced code for the subject, a synthesised
    # block that imports anything is a fabricated SDK → must be dropped, not shipped.
    class _FakeClient:
        def chat(self, _msgs):
            class _R:
                text = "```python\nfrom craft_agents import Agent\nAgent().run()\n```"
            return _R()

    claims = [{"claim": "Craft connects to any API via natural language.",
               "quote": "add Linear as a source", "url": "https://agents.craft.do/",
               "subjects": ["Craft"]}]
    out = rf._splice_subject_code("Body.", "Craft", claims, _FakeClient(), evidence_dir=None)
    assert "craft_agents" not in out and out == "Body."  # fabricated import dropped


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


def test_claims_joint_source_tagged_by_literal_cooccurrence() -> None:
    # Codex P1: a joint-fetched source (incl. per-side joint probes) is NOT
    # guaranteed to name every subject. Tagging is grounded in literal
    # co-occurrence, not a blanket list(subjects): a page naming only ONE subject
    # is demoted to that subject (never a fabricated joint), and the LLM's own
    # SUBJECTS guess is ignored.
    pi_only = "Pi is a minimal agent harness. It ships a planner and an executor loop."
    reply_text = (
        "CLAIM: Pi ships a planner and executor loop.\n"
        "QUOTE: It ships a planner and an executor loop.\n"
        "SUBJECTS: Craft\n"  # over-claims Craft; page never names Craft
    )
    single = rf._extract_claims_from_source(
        "https://x.test/pi", pi_only, ["Pi", "Craft"], _client(reply_text), loop_subject=None
    )
    assert single[0]["subjects"] == ["Pi"]  # demoted, not fabricated joint

    both = "Pi connects to Craft: Pi plans and Craft executes the tools."
    reply2 = (
        "CLAIM: Pi plans and Craft executes.\n"
        "QUOTE: Pi plans and Craft executes the tools\n"
        "SUBJECTS: Pi\n"  # under-claims; co-occurrence still earns the joint tag
    )
    joint = rf._extract_claims_from_source(
        "https://x.test/both", both, ["Pi", "Craft"], _client(reply2), loop_subject=None
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


def test_mechanism_terms_grounded_true_when_named_in_claims() -> None:
    claims = [{"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u"}]
    block = "```python\nclient.call_mcp_server()\n```"
    assert rf._mechanism_terms_grounded(block, claims, ["MCP"]) is True


def test_mechanism_terms_grounded_falls_open_when_term_absent_from_code() -> None:
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"], "url": "u"}]
    block = "```python\nclient.call_grpc_endpoint()\n```"
    # "mcp" is not in the code at all → nothing to check, falls open.
    assert rf._mechanism_terms_grounded(block, claims, ["MCP"]) is True


def test_mechanism_terms_grounded_false_when_named_but_ungrounded() -> None:
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"], "url": "u"}]
    block = "```python\nclient.register_webhook_url()\n```"
    # the code names "webhook", which no claim documents → rejected.
    assert rf._mechanism_terms_grounded(block, claims, ["webhook"]) is False


def test_splice_integration_code_captions_as_proposed_not_source() -> None:
    claims = [{"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u"}]
    reply = "```python\ndef call_mcp_server():\n    pass\n```"
    rel = _rel(descriptor="Integration", mechanism="Pi calls Craft via MCP", terms=["MCP"])
    out = rf._splice_integration_code("Some prose.", ["Pi", "Craft"], claims, rel, _client(reply))
    assert "Proposed usage" in out
    assert "not quoted source code" in out
    assert "def call_mcp_server" in out


def test_splice_integration_code_rejects_ungrounded_term_in_code() -> None:
    # A grounded term ("MCP") steers the prompt, but the generated code names a
    # DIFFERENT term ("webhook") no claim documents → the post-check rejects it.
    claims = [{"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u"}]
    reply = "```python\nclient.register_webhook_url()\n```"
    rel = _rel(terms=["MCP", "webhook"])
    out = rf._splice_integration_code("Some prose.", ["Pi", "Craft"], claims, rel, _client(reply))
    assert out == "Some prose."


def test_splice_integration_code_skips_when_no_grounded_term() -> None:
    # No mechanism term appears in the claims → no groundable example → skip.
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"], "url": "u"}]
    rel = _rel(terms=["webhook"])
    out = rf._splice_integration_code("Some prose.", ["Pi", "Craft"], claims, rel, _client("```python\npass\n```"))
    assert out == "Some prose."


def test_splice_integration_code_noop_for_single_subject() -> None:
    out = rf._splice_integration_code("Some prose.", ["Pi"], [], _rel(terms=["MCP"]), _client("```python\npass\n```"))
    assert out == "Some prose."


# ---------------------------------------------------------------------------
# COMPARISON — competes/alternative branch (R3): a grounded table, no integration
# ---------------------------------------------------------------------------


def test_splice_comparison_table_builds_grounded_table() -> None:
    claims = [
        {"claim": "Redis keeps data structures in memory.", "subjects": ["Redis"], "url": "u1"},
        {"claim": "Redis persists to disk with snapshots.", "subjects": ["Redis"], "url": "u2"},
        {"claim": "Memcached is a volatile key-value cache.", "subjects": ["Memcached"], "url": "u3"},
    ]
    out = rf._splice_comparison_table("Some prose.", ["Redis", "Memcached"], claims)
    assert "| Redis | Memcached |" in out
    assert "| --- | --- |" in out
    # cells are drawn only from each subject's own claims (grounded)
    assert "Redis keeps data structures in memory." in out
    assert "Memcached is a volatile key-value cache." in out


def test_splice_comparison_table_fails_open_when_a_subject_is_thin() -> None:
    # Memcached has no claim → nothing grounded to compare → unchanged text.
    claims = [{"claim": "Redis keeps data structures in memory.", "subjects": ["Redis"], "url": "u1"}]
    out = rf._splice_comparison_table("Some prose.", ["Redis", "Memcached"], claims)
    assert out == "Some prose."


def test_splice_comparison_table_noop_for_single_subject() -> None:
    claims = [{"claim": "Redis keeps data structures in memory.", "subjects": ["Redis"], "url": "u1"}]
    assert rf._splice_comparison_table("Some prose.", ["Redis"], claims) == "Some prose."


def test_splice_comparison_table_escapes_pipes_in_cells() -> None:
    claims = [
        {"claim": "Redis supports strings | lists | sets.", "subjects": ["Redis"], "url": "u1"},
        {"claim": "Memcached stores flat strings.", "subjects": ["Memcached"], "url": "u2"},
    ]
    out = rf._splice_comparison_table("Some prose.", ["Redis", "Memcached"], claims)
    assert "strings \\| lists \\| sets" in out


# ---------------------------------------------------------------------------
# DIAGRAM — Pi<->Craft integration clusters (user emphasis: show integration)
# ---------------------------------------------------------------------------


def test_integration_label_grounded_in_joint_claim_from_per_task_term() -> None:
    # R3: the label is a per-task mechanism term corroborated in a joint claim,
    # never matched against a hardcoded interface enum.
    claims = [{"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u"}]
    assert rf._integration_label(["Pi", "Craft"], claims, _rel(terms=["MCP"])) == "mcp"


def test_integration_label_generic_from_descriptor_when_no_term_grounded() -> None:
    # A joint claim exists but no per-task term is corroborated → generic label
    # from the descriptor, never the raw hypothesis sentence.
    claims = [{"claim": "Pi and Craft are both used to build agents.", "subjects": ["Pi", "Craft"], "url": "u"}]
    label = rf._integration_label(["Pi", "Craft"], claims, _rel(descriptor="Integration", terms=["MCP"]))
    assert label == "integration"


def test_integration_label_from_each_sides_own_grounded_term() -> None:
    claims = [
        {"claim": "Pi exposes a CLI for automation.", "subjects": ["Pi"], "url": "u1"},
        {"claim": "Craft supports API access for extensions.", "subjects": ["Craft"], "url": "u2"},
    ]
    label = rf._integration_label(["Pi", "Craft"], claims, _rel(terms=["CLI", "API"]))
    assert label is not None and "cli" in label and "api" in label


def test_integration_label_none_when_ungrounded() -> None:
    # Neither a joint claim nor a per-side grounded term — must not invent one.
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"], "url": "u"}]
    assert rf._integration_label(["Pi", "Craft"], claims, _rel(terms=["MCP"])) is None


def test_grounded_mechanism_terms_from_claims_only() -> None:
    # R3: the terms an example MAY use come from the per-task terms corroborated
    # in the claims — a term the hypothesis proposed but no claim documents is
    # dropped (v43 defect: an ungrounded "SDK" term made every sample fail).
    claims = [
        {"claim": "Craft connects to REST APIs and MCP servers.", "subjects": ["Craft"], "url": "u1"},
        {"claim": "Pi is a minimal harness.", "subjects": ["Pi"], "url": "u2"},
    ]
    terms = rf._grounded_mechanism_terms(claims, ["API", "MCP", "SDK"])
    assert "api" in terms and "mcp" in terms  # plural "APIs"/"servers" still grounds the singular
    assert "sdk" not in terms


def test_grounded_mechanism_terms_empty_when_none_documented() -> None:
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"], "url": "u"}]
    assert rf._grounded_mechanism_terms(claims, ["API", "MCP"]) == []


def test_fallback_cluster_diagram_none_without_grounding() -> None:
    claims = [{"claim": "Pi is a minimal agent harness.", "subjects": ["Pi"], "url": "u"}]
    assert rf._fallback_cluster_diagram(["Pi", "Craft"], claims, _rel(terms=["MCP"])) is None


def test_fallback_cluster_diagram_has_subgraphs_and_cross_edge() -> None:
    claims = [
        {"claim": "Pi exposes an Agent Loop and a CLI.", "subjects": ["Pi"], "url": "u1"},
        {"claim": "Craft supports MCP servers and browser automation.", "subjects": ["Craft"], "url": "u2"},
        {"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u3"},
    ]
    body = rf._fallback_cluster_diagram(["Pi", "Craft"], claims, _rel(terms=["MCP"]))
    assert body is not None
    assert 'subgraph' in body and '["Pi"]' in body and '["Craft"]' in body
    assert "mcp" in body.lower()


def test_fallback_cluster_diagram_preserves_cross_edge_when_feature_labels_collide() -> None:
    claims = [
        {"claim": "Alpha Product exposes an API and task runner.", "subjects": ["Alpha Product"], "url": "u1"},
        {"claim": "Beta Product exposes an API and browser automation.", "subjects": ["Beta Product"], "url": "u2"},
        {
            "claim": "Alpha Product and Beta Product integrate through an API bridge.",
            "subjects": ["Alpha Product", "Beta Product"],
            "url": "u3",
        },
    ]
    body = rf._fallback_cluster_diagram(["Alpha Product", "Beta Product"], claims, _rel(terms=["API"]))
    assert body is not None
    assert body.count('["API"]') == 2
    cross_lines = [line for line in body.splitlines() if "-->|" in line]
    assert cross_lines
    assert len(cross_lines) == 1
    assert cross_lines[0].split()[0] != cross_lines[0].split()[-1]


def test_subject_feature_labels_ignore_joint_only_other_subject_names() -> None:
    claims = [
        {
            "claim": "Alpha Product and Beta Product integrate through an API bridge.",
            "subjects": ["Alpha Product", "Beta Product"],
            "url": "u1",
        }
    ]
    labels = rf._subject_feature_labels(
        "Alpha Product",
        claims,
        all_subjects=["Alpha Product", "Beta Product"],
    )
    assert labels == []


def test_fallback_cluster_diagram_does_not_put_other_subject_in_feature_node() -> None:
    claims = [
        {
            "claim": "Alpha Product and Beta Product integrate through an API bridge.",
            "subjects": ["Alpha Product", "Beta Product"],
            "url": "u1",
        }
    ]
    body = rf._fallback_cluster_diagram(["Alpha Product", "Beta Product"], claims, _rel(terms=["API"]))
    assert body is not None
    alpha_cluster = body.split('subgraph Alpha_Product["Alpha Product"]', 1)[1].split("    end", 1)[0]
    assert '["Beta Product"]' not in alpha_cluster


def test_cross_cluster_edges_rejects_ambiguous_duplicate_labels() -> None:
    components = [("API", "Alpha Product"), ("API", "Beta Product")]
    edges = [("API", "API", "API bridge")]
    assert diagram_render._cross_cluster_edges(components, edges) == []


def test_splice_diagram_falls_back_when_model_returns_single_subject_architecture() -> None:
    # v7 failure mode: the model names components for only ONE subject.
    reply = (
        "COMPONENT: Planner | Pi | plans steps\n"
        "COMPONENT: Executor | Pi | runs tools\n"
        "EDGE: Planner -> Executor\n"
    )
    claims = [
        {"claim": "Pi exposes an Agent Loop and a CLI.", "subjects": ["Pi"], "url": "u1"},
        {"claim": "Craft supports MCP servers and browser automation.", "subjects": ["Craft"], "url": "u2"},
        {"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u3"},
    ]
    section_text = "Pi ships a Planner and an Executor. Craft is an agent tool."
    out = rf._splice_diagram(section_text, ["Pi", "Craft"], claims, _client(reply), _rel(terms=["MCP"]))
    assert "```mermaid" in out
    assert out.count("subgraph") == 2
    assert "mcp" in out.lower()


def test_splice_diagram_falls_back_when_model_uses_unknown_subject() -> None:
    reply = (
        "COMPONENT: Foreign Planner | Other System | plans steps\n"
        "COMPONENT: Foreign Executor | Other System | runs tools\n"
        "EDGE: Foreign Planner -> Foreign Executor\n"
    )
    claims = [
        {"claim": "Pi exposes an Agent Loop and a CLI.", "subjects": ["Pi"], "url": "u1"},
        {"claim": "Craft supports MCP servers and browser automation.", "subjects": ["Craft"], "url": "u2"},
        {"claim": "Craft connects to Pi via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u3"},
    ]
    out = rf._splice_diagram("Pi and Craft integration overview.", ["Pi", "Craft"], claims, _client(reply), _rel(terms=["MCP"]))
    assert "```mermaid" in out
    assert "Other System" not in out
    assert "mcp" in out.lower()


def test_splice_diagram_accepts_grounded_two_cluster_diagram() -> None:
    reply = (
        "COMPONENT: Pi Agent Loop | Pi | runs the agent loop\n"
        "COMPONENT: Craft Agents | Craft | open source agent interface\n"
        "EDGE: Pi Agent Loop -> Craft Agents | MCP\n"
    )
    claims = [{"claim": "Craft Agents connects to the Pi Agent Loop via an MCP server.", "subjects": ["Pi", "Craft"], "url": "u"}]
    section_text = "Pi Agent Loop runs the agent loop. Craft Agents is an open source agent interface."
    out = rf._splice_diagram(section_text, ["Pi", "Craft"], claims, _client(reply), _rel(terms=["MCP"]))
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


def test_subject_diagram_falls_back_to_grounded_features() -> None:
    claims = [
        {
            # Real component-shaped labels (acronyms + a hyphenated identifier),
            # NOT bare prose words — the fallback grounds on named components.
            "claim": "Alpha Product exposes an API, an MCP server, and a plugin-loader module.",
            "subjects": ["Alpha Product"],
            "url": "u1",
        }
    ]
    out = rf._splice_subject_diagram(
        "Alpha Product overview.",
        "Alpha Product",
        claims,
        _client("not parseable as components"),
    )
    assert "```mermaid" in out
    assert "Alpha Product" in out
    assert "API" in out or "MCP" in out or "plugin-loader" in out


def test_subject_feature_labels_reject_sentence_start_filler_words() -> None:
    claims = [
        {
            "claim": "The repository contains over 100 files and various examples.",
            "subjects": ["Alpha Product"],
            "url": "u1",
        }
    ]

    assert rf._subject_feature_labels("Alpha Product", claims) == []


def test_splice_subject_diagram_rejects_filler_model_labels() -> None:
    text = "Alpha Product has a documented architecture."
    claims = [
        {
            "claim": "The repository contains over 100 files and various examples.",
            "subjects": ["Alpha Product"],
            "url": "u1",
        }
    ]
    reply = (
        "COMPONENT: The | article word\n"
        "COMPONENT: Repository | article word\n"
        "COMPONENT: Contains | article word\n"
        "COMPONENT: Over | article word\n"
        "EDGE: The -> Repository\nEDGE: Repository -> Contains\nEDGE: Contains -> Over\n"
    )

    assert rf._splice_subject_diagram(text, "Alpha Product", claims, _client(reply)) == text


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


def test_summary_prompt_states_cooperation_conclusion() -> None:
    captured: dict[str, list] = {}

    def _capture_chat(messages, tools=None):
        captured["messages"] = messages
        return SimpleNamespace(text="summary")

    rf._write_summary(
        "study Pi and Craft", {"Key Findings": "text"}, SimpleNamespace(chat=_capture_chat),
        relationship=_rel(kind="cooperates", mechanism="Pi calls Craft via MCP"),
    )
    prompt = captured["messages"][0]["content"]
    assert "conclusion on how the subjects relate" in prompt
    assert "Pi calls Craft via MCP" in prompt


def test_summary_prompt_states_comparison_conclusion_for_competes() -> None:
    captured: dict[str, list] = {}

    def _capture_chat(messages, tools=None):
        captured["messages"] = messages
        return SimpleNamespace(text="summary")

    rf._write_summary(
        "compare Redis and Memcached", {"Key Findings": "text"}, SimpleNamespace(chat=_capture_chat),
        relationship=_rel(kind="competes", descriptor="Comparison"),
    )
    prompt = captured["messages"][0]["content"]
    assert "trade-offs" in prompt


def test_summary_prompt_notes_independence() -> None:
    captured: dict[str, list] = {}

    def _capture_chat(messages, tools=None):
        captured["messages"] = messages
        return SimpleNamespace(text="summary")

    rf._write_summary(
        "study X and Y", {"Key Findings": "text"}, SimpleNamespace(chat=_capture_chat),
        relationship=_rel(kind="independent", descriptor="Relationship"),
    )
    prompt = captured["messages"][0]["content"]
    assert "independent concerns" in prompt


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


def test_enforce_references_last_moves_trailing_section_before_references() -> None:
    # v44 defect: a section trailed References. It must be moved to before it,
    # References ends up last, other sections keep their relative order.
    text = (
        "# Report\n\n"
        "## Key Findings\n\nfindings body\n\n"
        "## References\n\n- https://x.test/a\n\n"
        "## Pi and Craft: Integration\n\nrelationship body\n"
    )
    out = rf._enforce_references_last(text)
    headings = [h for h in re.findall(r"(?m)^## (.+)$", out)]
    assert headings[-1] == "References"
    assert headings.index("Key Findings") < headings.index("Pi and Craft: Integration")
    # content moved with its heading
    assert "relationship body" in out


def test_enforce_references_last_noop_when_already_last() -> None:
    text = "# Report\n\n## Key Findings\n\nbody\n\n## References\n\n- https://x.test/a\n"
    assert rf._enforce_references_last(text) == text


def test_enforce_references_last_ignores_hash_inside_code_fence() -> None:
    # A ``## `` inside a code fence is not a heading — must not be reordered.
    text = (
        "# Report\n\n"
        "## References\n\n- https://x.test/a\n\n"
        "## Example\n\n```python\n## not a heading\nx = 1\n```\n"
    )
    out = rf._enforce_references_last(text)
    top_headings = [h for h in re.findall(r"(?m)^## (.+)$", out)]
    assert top_headings[-1] == "References"
    assert "## not a heading" in out  # fence content preserved intact


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
    entries = _ref_urls(refs)
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
    ref_urls = _ref_urls(refs)
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


def test_sanitize_section_headings_drops_inline_sibling_heading_echo() -> None:
    text = "Summary prose ends here.## Scope and Research Questions\n\nDuplicated scope body."
    out = rf._sanitize_section_headings(text, ["Executive Summary", "Scope and Research Questions"])
    assert "## Scope and Research Questions" not in out
    assert "Summary prose ends here." in out


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


# ---------------------------------------------------------------------------
# Joint-research strengthening + coverage gate (subject names are arbitrary
# placeholders here, not production literals)
# ---------------------------------------------------------------------------


def test_joint_research_finds_integration_evidence_via_per_side_query(monkeypatch, tmp_path) -> None:
    # A both-names blob query only finds SEO junk + an already-fetched single-side
    # homepage; the per-side `{descriptor} {terms}` query is what surfaces the real
    # integration page. Before the per-side queries existed, __joint__ ended empty
    # and no claim ever carried both subjects.
    A, B = "Alpha", "Beta"
    seo_junk = "https://spam.test/finance"
    alpha_home = "https://alpha.test/home"
    beta_home = "https://beta.test/home"
    integ_url = "https://beta.test/docs/mcp"
    contents = {
        seo_junk: "stock market nikkei finance agent loop tool calling spam",
        alpha_home: "Alpha is an orchestration framework. mcp bridge.",
        beta_home: "Beta provides task execution. mcp connector.",
        integ_url: "Alpha and Beta connect together via an MCP server for tool calling.",
    }

    def fake_search(query, results=5):
        q = query.lower()
        has_mcp, has_a, has_b = "mcp" in q, "alpha" in q, "beta" in q
        if has_mcp and has_a and has_b:  # both-names joint blob → junk + single-side
            return [SimpleNamespace(url=seo_junk), SimpleNamespace(url=beta_home)]
        if has_mcp and has_b and not has_a:  # per-side Beta probe → integration page
            return [SimpleNamespace(url=integ_url)]
        if has_a and not has_b:
            return [SimpleNamespace(url=alpha_home)]
        if has_b and not has_a:
            return [SimpleNamespace(url=beta_home)]
        return [SimpleNamespace(url=alpha_home)]  # disambiguation query naming both

    monkeypatch.setattr(rf, "_search", fake_search)
    monkeypatch.setattr(rf, "_fetch_and_store", lambda url, ev, idx: contents.get(url, ""))
    monkeypatch.setattr(rf, "_is_offtopic", lambda url, *a, **k: url == seo_junk)

    def judge_chat(messages, tools=None):
        prompt = messages[0]["content"]
        if "Classify how A and B relate" in prompt:
            return SimpleNamespace(
                text="KIND: cooperates\nDESCRIPTOR: Integration\n"
                "MECHANISM: A and B connect via MCP\nTERMS: mcp, tool calling"
            )
        m = re.search(r'interpretation of "([^"]+)"', prompt)
        subj = m.group(1) if m else A
        return SimpleNamespace(text=f"DESCRIPTOR: {subj}\nANCHORS: mcp, tool calling")

    judge = SimpleNamespace(chat=judge_chat)
    client = _client(
        "CLAIM: Alpha and Beta connect via an MCP server.\n"
        "QUOTE: connect together via an MCP server\n"
        "SUBJECTS: Alpha, Beta\n"
    )
    ledger, _assumptions, _relationship, all_anchors = rf._research(
        [A, B], tmp_path, "compare Alpha and Beta agent frameworks", judge, emit=None
    )
    assert ledger["__joint__"], "per-side query should have surfaced the integration page"
    claims = rf._build_claims(ledger, [A, B], client, tmp_path, all_anchors)
    assert any(len(c["subjects"]) > 1 for c in claims)


def test_build_claims_subject_page_names_other_via_anchors_grounds_joint(tmp_path) -> None:
    # The generic form of the Pi/Craft fix (mirrors a human researcher): a page
    # fetched under ONE subject's loop that genuinely documents ANOTHER subject —
    # confirmed by that subject's DISTINCTIVE anchors (pi-ai/pi-agent-core) — grounds
    # a JOINT claim. A page whose only mention of the other subject is an ambiguous
    # same-token collision ("Inflection Pi") does NOT: the anchors aren't present, so
    # the page isn't confirmed to discuss it, so bare "Pi" can't cross-tag.
    subjects = ["Pi", "Craft"]
    all_anchors = {"Pi": ["pi-ai", "pi-agent-core"], "Craft": ["Claude Agent SDK", "Craft Agents"]}
    readme = (
        "Craft Agents uses the Claude Agent SDK and the Pi SDK side by side. "
        "It depends on pi-ai and pi-agent-core for provider routing."
    )
    homepage = "Craft Agents connects to providers including Inflection Pi and Moonshot."
    ledger = {"Craft": [
        {"url": "https://github.com/x/craft", "content": readme},
        {"url": "https://craft.example/", "content": homepage},
    ]}

    def chat(messages, tools=None):
        if "pi-ai" in messages[0]["content"]:  # extracting from the README page
            return SimpleNamespace(text=(
                "CLAIM: Craft Agents uses the Pi SDK as a backend.\n"
                "QUOTE: uses the Claude Agent SDK and the Pi SDK side by side\n"
                "SUBJECTS: Craft\n"))
        return SimpleNamespace(text=(  # extracting from the homepage
            "CLAIM: Craft connects to Inflection Pi.\n"
            "QUOTE: connects to providers including Inflection Pi and Moonshot\n"
            "SUBJECTS: Craft\n"))

    claims = rf._build_claims(ledger, subjects, SimpleNamespace(chat=chat), tmp_path, all_anchors)
    joint = [c for c in claims if len(c["subjects"]) >= 2]
    assert joint and all(set(c["subjects"]) == {"Pi", "Craft"} for c in joint)
    infl = [c for c in claims if "Inflection" in c["claim"]]
    assert infl and all(c["subjects"] == ["Craft"] for c in infl)  # collision never joint


def test_build_claims_targeted_relationship_extraction_surfaces_joint(tmp_path) -> None:
    # Live-observed gap: a README anchor-confirmed for both subjects still yields 0
    # joint claims because generic per-page extraction skips the integration
    # sentence. The targeted relationship pass (fired on any >=2-subject page) pulls
    # it — mirroring a researcher reading the README specifically for how A relates B.
    subjects = ["Pi", "Craft"]
    all_anchors = {"Pi": ["pi-ai", "pi-agent-core"], "Craft": ["Claude Agent SDK", "Craft Agents"]}
    readme = (
        "Craft Agents is a desktop app. It uses the Claude Agent SDK and the Pi SDK "
        "side by side. It depends on pi-ai and pi-agent-core for provider routing."
    )
    ledger = {"Craft": [{"url": "https://github.com/x/craft", "content": readme}]}

    def chat(messages, tools=None):
        p = messages[0]["content"]
        if "relate" in p and "VERBATIM" in p:  # the targeted relationship prompt
            return SimpleNamespace(text=(
                "CLAIM: Craft uses the Pi SDK alongside the Claude Agent SDK.\n"
                "QUOTE: uses the Claude Agent SDK and the Pi SDK side by side\n"))
        return SimpleNamespace(text=(  # generic per-page extraction: Craft feature only
            "CLAIM: Craft Agents is a desktop app.\n"
            "QUOTE: Craft Agents is a desktop app\nSUBJECTS: Craft\n"))

    claims = rf._build_claims(ledger, subjects, SimpleNamespace(chat=chat), tmp_path, all_anchors)
    joint = [c for c in claims if len(c["subjects"]) >= 2]
    assert any("Pi SDK" in c["claim"] and set(c["subjects"]) == {"Pi", "Craft"} for c in joint)


def test_mentions_subject_rejects_compound_proper_noun_collision() -> None:
    a = ["pi-ai", "pi-agent-core"]
    assert rf._mentions_subject("Pi", a, "It uses the Pi SDK side by side.")     # genuine
    assert rf._mentions_subject("Pi", a, "Google Gemini and Pi are supported.")  # 'and Pi' genuine
    assert not rf._mentions_subject("Pi", a, "Compatible with Inflection Pi.")   # collision
    assert not rf._mentions_subject("Pi", a, "Runs on a Raspberry Pi board.")    # collision


def test_build_claims_targeted_extraction_rejects_same_page_collision(tmp_path) -> None:
    # Live-observed fabrication: on a page confirmed for both subjects, the targeted
    # relationship pass grabbed the "Inflection Pi" provider-list co-mention and
    # minted a FALSE joint. The collision guard must drop it — a coincidental
    # same-token co-mention is not a relationship.
    subjects = ["Pi", "Craft"]
    all_anchors = {"Pi": ["pi-ai", "pi-agent-core"], "Craft": ["Claude Agent SDK", "Craft Agents"]}
    content = (
        "Craft Agents uses the Claude Agent SDK. It depends on pi-ai and pi-agent-core. "
        "Compatible models include Inflection Pi and Moonshot."
    )
    ledger = {"Craft": [{"url": "u", "content": content}]}

    def chat(messages, tools=None):
        p = messages[0]["content"]
        if "relate" in p and "VERBATIM" in p:  # targeted pass returns the collision
            return SimpleNamespace(text=(
                "CLAIM: Inflection Pi is a compatible model with Craft Agents.\n"
                "QUOTE: Compatible models include Inflection Pi and Moonshot\n"))
        return SimpleNamespace(text=(
            "CLAIM: Craft Agents is an app.\n"
            "QUOTE: Craft Agents uses the Claude Agent SDK\nSUBJECTS: Craft\n"))

    claims = rf._build_claims(ledger, subjects, SimpleNamespace(chat=chat), tmp_path, all_anchors)
    assert [c for c in claims if len(c["subjects"]) >= 2] == []  # no fabricated joint


def test_sanitize_section_headings_preserves_h3_subheadings() -> None:
    # Task 3 (H3 sub-structure): _write_section now licenses `###` sub-topics. The G6
    # heading-leak guards must keep them — they demote only leaked `#`/`##`, never a
    # legitimate level-3 subheading. Without this, the prompt change would be silently
    # defeated by the sanitizer.
    body = (
        "Intro paragraph.\n\n"
        "### Architecture\nPi has packages.\n\n"
        "### Usage\nInstall and run.\n\n"
        "## Leaked H2\nx\n"
    )
    out = rf._demote_stray_h1(rf._sanitize_section_headings(body, ["Overview", "Findings"]))
    assert "### Architecture" in out and "### Usage" in out  # intentional H3 kept
    assert "\n## " not in out and not out.startswith("## ")   # leaked H2 demoted


def test_parse_relation_triples_grounds_endpoints_in_quote() -> None:
    # KGGen shape (task 2 / provider-routing): the model may name the relation verb
    # freely, but BOTH endpoints must appear in the verbatim quote — a fabricated tail
    # and a self-edge are dropped, directed pairs de-dup by (head, tail). No provider/
    # verb lexicon in code: grounding is purely 'is this token in the quote'.
    quote = "The Pi SDK manages connections for Google AI Studio and OpenAI"
    nq = " ".join(quote.split()).lower()
    block = (
        f"CLAIM: x\nQUOTE: {quote}\nRELATIONS:\n"
        "Pi SDK | routes to | Google AI Studio\n"
        "Pi SDK | routes to | OpenAI\n"
        "Pi SDK | routes to | Anthropic\n"          # tail absent from quote -> dropped
        "Pi SDK | routes to | Pi SDK\n"             # self-edge -> dropped
        "Pi SDK | routes to | Google AI Studio\n"   # duplicate -> dropped
    )
    assert rf._parse_relation_triples(block, nq) == [
        {"head": "Pi SDK", "rel": "routes to", "tail": "Google AI Studio"},
        {"head": "Pi SDK", "rel": "routes to", "tail": "OpenAI"},
    ]


def test_parse_relation_triples_rejects_substring_collision() -> None:
    # Reviewer catch: grounding is whole-token (word boundary), NOT substring — a short
    # product name must not ground on being a substring of an unrelated word. Here "Pi"
    # is a substring of "shipping" and "Go" of "Google", but neither is a real token in
    # the quote, so both fabricated endpoints are dropped.
    quote = "for shipping and mapping to Google Cloud, Craft handles the rest"
    nq = " ".join(quote.split()).lower()
    block = (
        f"CLAIM: x\nQUOTE: {quote}\nRELATIONS:\n"
        "Pi | routes to | Craft\n"   # "pi" only inside "shipping" -> dropped
        "Go | routes to | Craft\n"   # "go" only inside "Google" -> dropped
        "Craft | maps to | Google Cloud\n"  # both whole tokens -> kept
    )
    assert rf._parse_relation_triples(block, nq) == [
        {"head": "Craft", "rel": "maps to", "tail": "Google Cloud"},
    ]


def test_splice_relationship_table_tabulates_subject_grounded_fanout() -> None:
    # Task 2 render: a subject-grounded fan-out (one package/SDK -> several targets)
    # becomes a deterministic table; de-dups by (source, target). A source NOT grounded
    # to a subject/anchor ("extensions") is dropped even when it fans out — that is the
    # selection fix that turns a noisy grab-bag into the real architecture story.
    subjects = ["Pi", "Craft"]
    anchors = {"Pi": ["pi-ai", "pi-agent-core"], "Craft": ["Claude Agent SDK"]}
    claims = [
        {"claim": "x", "relations": [
            {"head": "Pi SDK", "rel": "uses", "tail": "pi-ai"},
            {"head": "Pi SDK", "rel": "uses", "tail": "pi-agent-core"},
            {"head": "Pi SDK", "rel": "uses", "tail": "pi-agent-core"},  # dup
        ]},
        {"claim": "y", "relations": [   # NOT subject-grounded -> whole head dropped
            {"head": "extensions", "rel": "access", "tail": "tools"},
            {"head": "extensions", "rel": "access", "tail": "commands"},
        ]},
    ]
    out = rf._splice_relationship_table("Body prose.", subjects, anchors, claims)
    assert "| Source | Relationship | Target |" in out
    assert out.count("| Pi SDK |") == 2       # two distinct targets, dup collapsed
    assert "extensions" not in out            # ungrounded source dropped


def test_splice_relationship_table_fail_open_without_grounded_fanout() -> None:
    subjects = ["Pi", "Craft"]
    anchors = {"Pi": ["pi-ai"], "Craft": []}
    # A grounded source with only ONE target is not a fan-out -> no table.
    assert rf._splice_relationship_table(
        "Body.", subjects, anchors, [{"claim": "x", "relations": [
            {"head": "Pi SDK", "rel": "uses", "tail": "pi-ai"}]}]) == "Body."
    # A fan-out whose source is NOT subject-grounded -> filtered out -> no table.
    assert rf._splice_relationship_table(
        "Body.", subjects, anchors, [{"claim": "x", "relations": [
            {"head": "AppMenu", "rel": "has", "tail": "MobileAppMenu"},
            {"head": "AppMenu", "rel": "has", "tail": "DesktopAppMenu"}]}]) == "Body."
    # No relations at all -> unchanged.
    assert rf._splice_relationship_table("Body.", subjects, anchors, [{"claim": "x"}]) == "Body."


def test_parse_relation_triples_absent_when_no_relations_line() -> None:
    # Additive contract: a plain claim block (no RELATIONS line) yields no triples —
    # the field is optional, so the GS2-hardened extraction path is unperturbed.
    assert rf._parse_relation_triples("CLAIM: x\nQUOTE: a desktop app", "a desktop app") == []


def test_claims_extraction_attaches_grounded_routing_relations() -> None:
    # Provider-routing gold-parity (task 2): the routing fan-out is already present as a
    # prose claim (the main extractor's descriptive enumeration); the RELATIONS line
    # lifts it into grounded (head, rel, tail) triples so ASSEMBLE can tabulate. The
    # fabricated provider (not in the quote) must NOT enter the graph.
    subjects = ["Pi", "Craft"]
    all_anchors = {"Pi": ["pi-ai", "pi-agent-core"], "Craft": ["Claude Agent SDK", "Craft Agents"]}
    readme = "The Pi SDK manages connections for Google AI Studio and OpenAI."

    def chat(messages, tools=None):
        return SimpleNamespace(text=(  # main extractor: the provider enumeration + triples
            "CLAIM: The Pi SDK routes to multiple providers.\n"
            "QUOTE: The Pi SDK manages connections for Google AI Studio and OpenAI\n"
            "SUBJECTS: Pi\n"
            "RELATIONS:\n"
            "Pi SDK | routes to | Google AI Studio\n"
            "Pi SDK | routes to | OpenAI\n"
            "Pi SDK | routes to | Anthropic\n"))  # not in quote -> dropped

    rows = rf._extract_claims_from_source(
        "u", readme, subjects, SimpleNamespace(chat=chat), loop_subject=None, all_anchors=all_anchors
    )
    rels = [r for c in rows for r in c.get("relations", [])]
    tails = {r["tail"] for r in rels if r["head"] == "Pi SDK"}
    assert "Google AI Studio" in tails and "OpenAI" in tails
    assert "Anthropic" not in tails  # ungrounded tail dropped


def test_coverage_gate_recovers_joint_claim(monkeypatch, tmp_path) -> None:
    # CLAIMS produced 0 joint claims (both starting claims tagged one subject) so
    # the integration diagram could not ground. The gate fires ONE joint-recovery
    # fetch against a page that literally names BOTH subjects, earning a REAL joint
    # tag grounded in co-occurrence (present == both subjects) — via the actual
    # _extract_claims_from_source, not a stubbed empty-tags return.
    content = "Craft connects together with Pi via an MCP server for tool calling."
    monkeypatch.setattr(rf, "_search", lambda q, results=5: [SimpleNamespace(url="http://x/integration")])
    monkeypatch.setattr(rf, "_fetch_and_store", lambda url, ev, idx: content)
    monkeypatch.setattr(rf, "_is_offtopic", lambda *a, **k: False)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    client = _client(
        "CLAIM: Craft connects to Pi via MCP.\n"
        "QUOTE: connects together with Pi via an MCP server\n"
        "SUBJECTS: Pi, Craft\n"
    )
    claims = [
        {"claim": "p", "quote": "q", "url": "u", "subjects": ["Pi"]},
        {"claim": "p2", "quote": "q", "url": "u", "subjects": ["Pi"]},
    ]
    rel = rf.Relationship("cooperates", "Integration", "Pi and Craft compose", ["compose"])
    out = rf._coverage_gate(
        claims, ["Pi", "Craft"], rel, {}, evidence_dir, "compare Pi and Craft",
        client=client, judge_client=None, ws_dir=tmp_path, emit=None,
    )
    assert sum(1 for c in out if len(c["subjects"]) >= 2) >= 1
    assert sum(1 for c in out if "Craft" in c["subjects"]) >= 1


def test_coverage_gate_joint_recovery_rejects_single_subject_page(monkeypatch, tmp_path) -> None:
    # Regression guard (the CRITICAL): a joint-recovery fetch whose page names only
    # ONE subject must NOT mint a joint claim. Before the present>=2 grounding gate,
    # _recover_claims stamped list(subjects) on every claim, fabricating a joint tag
    # from a single-subject page and grounding a fake integration diagram. The model
    # here over-claims "SUBJECTS: Pi, Craft" but the page never names Pi — the gate
    # must not trust it.
    content = "Craft provides task execution with a built-in browser and API connectors."
    monkeypatch.setattr(rf, "_search", lambda q, results=5: [SimpleNamespace(url="http://x/craft-only")])
    monkeypatch.setattr(rf, "_fetch_and_store", lambda url, ev, idx: content)
    monkeypatch.setattr(rf, "_is_offtopic", lambda *a, **k: False)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    client = _client(
        "CLAIM: Craft runs tasks with a browser.\n"
        "QUOTE: task execution with a built-in browser\n"
        "SUBJECTS: Pi, Craft\n"
    )
    claims = [
        {"claim": "p", "quote": "q", "url": "u", "subjects": ["Pi"]},
        {"claim": "p2", "quote": "q", "url": "u", "subjects": ["Pi"]},
    ]
    rel = rf.Relationship("cooperates", "Integration", "Pi and Craft compose", ["compose"])
    out = rf._coverage_gate(
        claims, ["Pi", "Craft"], rel, {}, evidence_dir, "compare Pi and Craft",
        client=client, judge_client=None, ws_dir=tmp_path, emit=None,
    )
    # No page named both subjects → no joint claim may be fabricated.
    assert sum(1 for c in out if len(c["subjects"]) >= 2) == 0


def test_coverage_gate_per_subject_recovery_requires_anchor_not_bare_name(monkeypatch, tmp_path) -> None:
    # Codex P1: per-subject recovery must ANCHOR-CONFIRM the thin subject, not just
    # match its bare name. A Craft page mentioning "Inflection Pi" (bare 'pi', none
    # of Pi's distinctive anchors) must NOT pass as a Pi-recovery source and then be
    # paired with anchor-confirmed Craft into a fabricated ['Pi','Craft'] joint.
    all_anchors = {"Pi": ["pi-ai", "pi-agent-core"], "Craft": ["Craft Agents", "Claude Agent SDK"]}
    content = "Craft Agents connects to providers including Inflection Pi via the Claude Agent SDK."
    monkeypatch.setattr(rf, "_search", lambda q, results=5: [SimpleNamespace(url="http://x/craft")])
    monkeypatch.setattr(rf, "_fetch_and_store", lambda url, ev, idx: content)
    monkeypatch.setattr(rf, "_is_offtopic", lambda *a, **k: False)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    client = _client(
        "CLAIM: Craft connects to Inflection Pi.\n"
        "QUOTE: connects to providers including Inflection Pi via the Claude Agent SDK\n"
        "SUBJECTS: Pi, Craft\n"
    )
    # Pi is the THIN subject (0 claims) → recovery queries for Pi and fetches this page.
    claims = [
        {"claim": "c", "quote": "q", "url": "u", "subjects": ["Craft"]},
        {"claim": "c2", "quote": "q", "url": "u", "subjects": ["Craft"]},
    ]
    rel = rf.Relationship("cooperates", "Integration", "compose", ["compose"])
    out = rf._coverage_gate(
        claims, ["Pi", "Craft"], rel, all_anchors, evidence_dir, "compare Pi and Craft",
        client=client, judge_client=None, ws_dir=tmp_path, emit=None,
    )
    assert sum(1 for c in out if len(c["subjects"]) >= 2) == 0  # collision never fabricates joint


def test_coverage_gate_joint_recovery_requires_literal_naming(monkeypatch, tmp_path) -> None:
    # Codex P1: two MULTI-WORD subjects that merely share a generic word ("agent",
    # "sdk") must not both count as "named" on a page that names NEITHER product —
    # else joint recovery mints an ungrounded joint claim. _subject_named requires
    # every significant word, so a page missing the distinctive product token
    # ("Alpha"/"Beta") is dropped before any tag is stamped.
    content = "Modern agent frameworks and sdk tooling are widely discussed in the ecosystem."
    monkeypatch.setattr(rf, "_search", lambda q, results=5: [SimpleNamespace(url="http://x/generic")])
    monkeypatch.setattr(rf, "_fetch_and_store", lambda url, ev, idx: content)
    monkeypatch.setattr(rf, "_is_offtopic", lambda *a, **k: False)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    claims = [
        {"claim": "p", "quote": "q", "url": "u", "subjects": ["Alpha Agent SDK"]},
        {"claim": "p2", "quote": "q", "url": "u", "subjects": ["Alpha Agent SDK"]},
    ]
    rel = rf.Relationship("cooperates", "Integration", "compose", ["agent"])
    out = rf._coverage_gate(
        claims, ["Alpha Agent SDK", "Beta Agent SDK"], rel, {}, evidence_dir,
        "compare Alpha Agent SDK and Beta Agent SDK",
        client=_client(""), judge_client=None, ws_dir=tmp_path, emit=None,
    )
    assert sum(1 for c in out if len(c["subjects"]) >= 2) == 0


def test_tag_claim_joins_on_quote_not_paraphrased_claim() -> None:
    # G1-noise: the LLM can paraphrase "Inflection Pi" -> bare "Pi is a compatible
    # model", defeating the lexical collision guard on the CLAIM text. Joining on the
    # verbatim QUOTE (which still carries "Inflection Pi") keeps it out of a joint tag.
    anchors = {"Pi": ["pi-ai", "pi-agent-core"], "Craft": ["Craft Agents"]}
    tags = rf._tag_claim(
        "Pi is one of the compatible models Craft Agents can use.",  # paraphrased (bare Pi)
        "compatible models include Inflection Pi and Moonshot",       # verbatim quote
        "Craft", ["Pi", "Craft"], anchors,
    )
    assert tags == ["Craft"]  # Pi not joined — the quote shows it is Inflection Pi


def test_tag_claim_joins_real_pi_via_quote() -> None:
    anchors = {"Pi": ["pi-ai", "pi-agent-core"], "Craft": ["Craft Agents"]}
    tags = rf._tag_claim(
        "Craft utilizes both SDKs.",
        "It uses the Claude Agent SDK and the Pi SDK side by side",  # quote genuinely names Pi
        "Craft", ["Pi", "Craft"], anchors,
    )
    assert set(tags) == {"Pi", "Craft"}


def test_base_client_unwraps_tool_augmented_for_deterministic_renders() -> None:
    """ASSEMBLE-phase diagram renders must run on the BARE client, not inside the
    web-search tool loop (which degrades the plain SOURCE|RELATION|TARGET output).
    ``base_client`` unwraps a ToolAugmentedClient to its inner client and is a
    no-op on a bare client."""
    from studio.tools import ToolAugmentedClient, base_client

    inner = _client("SOURCE | RELATION | TARGET")
    wrapped = ToolAugmentedClient(inner, search_fn=lambda *a, **k: [], fetch_fn=lambda *a, **k: "")
    assert base_client(wrapped) is inner
    assert base_client(inner) is inner
    # double-wrap unwinds fully
    assert base_client(ToolAugmentedClient(wrapped, search_fn=lambda *a, **k: [])) is inner


def test_references_are_numbered_titled_entries() -> None:
    """References render as a NUMBERED, TITLED list from the fetched-evidence title
    map; a URL with no known title falls back to a bare-URL bullet (never invented)."""
    titles = {  # clean titles (as _url_title_map already produced them)
        "https://github.com/earendil-works/pi": "GitHub - earendil-works/pi: agent toolkit",
        "https://pi.dev/": "Pi Coding Agent",
    }
    claims = [
        {"claim": "a", "quote": "q", "url": "https://github.com/earendil-works/pi", "subjects": ["Pi"]},
        {"claim": "b", "quote": "q", "url": "https://pi.dev/", "subjects": ["Pi"]},
        {"claim": "c", "quote": "q", "url": "https://untitled.example/x", "subjects": ["Pi"]},
        {"claim": "d", "quote": "q", "url": "https://github.com/earendil-works/pi", "subjects": ["Pi"]},  # dup URL
    ]
    out = rf._rebuild_references_from_claims("## References\n\n- old\n", claims, titles)
    # numbered, titled, brand-tail stripped, deduped, bare fallback for the untitled URL
    assert "1. [GitHub - earendil-works/pi: agent toolkit](https://github.com/earendil-works/pi)" in out
    assert "2. [Pi Coding Agent](https://pi.dev/)" in out
    assert "3. https://untitled.example/x" in out  # no title → bare, not invented
    assert "4." not in out  # dup URL collapsed


def test_clean_source_title_strips_brand_tails_and_caps_length() -> None:
    assert rf._clean_source_title("pi-agent-core: Agent Framework | badlogic/pi-mono | DeepWiki") == \
        "pi-agent-core: Agent Framework | badlogic/pi-mono"
    assert rf._clean_source_title("Repo desc · GitHub") == "Repo desc"
    assert rf._clean_source_title("  a   b\n c ") == "a b c"
    assert rf._clean_source_title("x" * 200).endswith("…") and len(rf._clean_source_title("x" * 200)) <= rf._MAX_TITLE_LEN


def test_citation_markers_map_inline_urls_to_reference_numbers() -> None:
    """The writer cites claim URLs inline; ASSEMBLE renders each as its numbered
    [N] marker (N = that URL's reference position). Deterministic — a marker points
    at reference N by construction; a non-claim URL is left untouched (never a wrong
    marker); code fences and the References section are never rewritten."""
    claims = [
        {"claim": "a", "quote": "q", "url": "https://a.example/", "subjects": ["Pi"]},
        {"claim": "b", "quote": "q", "url": "https://b.example/x", "subjects": ["Craft"]},
    ]
    text = (
        "## Body\n\n"
        "Pi ships a CLI (https://a.example/). Craft uses [the SDK](https://b.example/x). "
        "See https://a.example/ too.\n"
        "A non-claim url https://c.example/ stays raw.\n\n"
        "```python\n# https://a.example/ inside a fence stays raw\n```\n\n"
        "## References\n\n1. [A](https://a.example/)\n2. [B](https://b.example/x)\n"
    )
    out = rf._apply_citation_markers(text, claims)
    body = out.split("## References")[0]
    assert "Pi ships a CLI [1]." in body            # (url) -> [N]
    assert "Craft uses the SDK [2]." in body         # [label](url) -> label [N]
    assert "See too." in body                        # bare url -> [N], deduped within paragraph (G4)
    # non-claim URL is a fabricated citation → STRIPPED (never a wrong marker), prose kept
    assert "A non-claim url stays raw." in body
    assert "c.example" not in body
    assert "# https://a.example/ inside a fence stays raw" in out  # fence untouched
    assert "1. [A](https://a.example/)" in out       # References section untouched
    # every [N] in body maps to a real reference (<= max ref)
    import re as _re
    assert all(int(n) <= 2 for n in _re.findall(r"\[(\d+)\]", body))


def test_citation_markers_collapse_adjacent_duplicates() -> None:
    claims = [{"claim": "a", "quote": "q", "url": "https://a.example/", "subjects": ["Pi"]}]
    text = "## B\n\nX (https://a.example/) (https://a.example/).\n\n## References\n\n1. [A](https://a.example/)\n"
    out = rf._apply_citation_markers(text, claims)
    assert "X [1]." in out and "[1][1]" not in out and "[1] [1]" not in out


def test_citation_markers_dedup_is_paragraph_scoped() -> None:
    """G4 caps over-citation per paragraph: a same-paragraph re-cite of [1] is
    dropped, but a blank line resets the seen-set so a later paragraph may cite
    [1] again (each paragraph carries its own attribution)."""
    claims = [{"claim": "a", "quote": "q", "url": "https://a.example/", "subjects": ["Pi"]}]
    text = (
        "## B\n\n"
        "One (https://a.example/) two https://a.example/ done.\n\n"  # same para -> second dropped
        "Fresh paragraph (https://a.example/) here.\n\n"             # new para -> [1] survives
        "## References\n\n1. [A](https://a.example/)\n"
    )
    body = rf._apply_citation_markers(text, claims).split("## References")[0]
    assert "One [1] two done." in body           # in-paragraph repeat collapsed, no orphan space
    assert "Fresh paragraph [1] here." in body   # blank line reset -> marker re-emitted


def test_demote_stray_h1_keeps_title_demotes_leaks_skips_fences() -> None:
    """Exactly one H1 (title) survives; a later body H1 (a leaked heading) demotes to
    H3; a `#` comment inside a code fence is never touched; `##` sections stay."""
    text = (
        "# Report Title\n\n## Section A\n\nBody.\n\n"
        "# To run the project\n\nSteps.\n\n"          # leaked body H1 -> ###
        "## Section B\n\n"                             # H2 section stays
        "```python\n# this is a code comment\n```\n\n"  # fence H1 untouched
        "# Minimal example\n\nCode below.\n"           # another leaked H1 -> ###
    )
    out = rf._demote_stray_h1(text)
    # exactly one H1 OUTSIDE fences (the title); the fence's `# comment` is not an H1
    non_fence = "".join(s for i, s in enumerate(rf._FENCE_SPLIT_RE.split(out)) if not i % 2)
    assert len(re.findall(r"(?m)^#[^\S\n]+", non_fence)) == 1
    assert "# Report Title" in out and out.index("# Report Title") == 0
    assert "### To run the project" in out
    assert "### Minimal example" in out
    assert "## Section A" in out and "## Section B" in out  # sections untouched
    assert "# this is a code comment" in out               # fence untouched
