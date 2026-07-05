"""Tests for the section-ownership / deterministic-assembly / verification work
(PLAN-section-ownership-architecture.md): N1–N4, G3, §4b reducer contract, §4d windowed
synthesis, and E2/E3 topology selection.
"""
from __future__ import annotations

import pytest

from agentkit.planner.core import Plan, PlanStep, plan as make_plan
from agentkit.topology.core import MAP, MESH, SINGLE, STAR
from agentkit.topology.dynamic import (
    assign_topologies_with_choices,
    classify_step_topology,
    run_plan,
)
from studio.artifact_text import (
    _detect_gaps,
    _merge_missing_sections,
    _synthesize_analysis,
    resolve_report_title,
    strip_satisfied_placeholders,
)
from studio.planning import (
    build_section_assignment_rows,
    build_section_assignment_queue,
    build_section_worker_foci,
    verify_assignment_coverage,
)
from studio.rubric import DEFAULT_TEMPLATE, mask_fenced_code, sections_present
from studio.task_runs import _section_ends_cleanly, refute_false_weaknesses


class FakeChat:
    """Minimal LLMClient stub: echoes the DRAFT back with an analysis sentence appended
    (preserves URLs, comes back longer → the synthesis anti-regression guard accepts it)."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None):
        from agentkit.types import ChatResult
        self.calls += 1
        content = messages[-1]["content"]
        draft = content.split("DRAFT:\n", 1)[1] if "DRAFT:\n" in content else content
        return ChatResult(
            text=draft + "\n\nIn contrast, this implies a clear trade-off (analysis added).",
            total_tokens=7,
        )


class RecordingChat:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def chat(self, messages, tools=None):
        from agentkit.types import ChatResult
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        return ChatResult(text="ok", total_tokens=1)


def test_section_reducer_receives_full_scoring_rules() -> None:
    from studio.findings import _make_section_reducer
    from studio.rubric import format_scoring_rules

    client = RecordingChat()
    rules = format_scoring_rules(
        [
            {"category": "Scope and research framing", "points": 40, "signal": "structure"},
            {"category": "Citation integrity", "points": 30, "signal": "verification"},
            {"category": "Readability and formatting", "points": 30, "signal": "structure"},
        ]
    )
    reducer = _make_section_reducer(
        client,
        "## Executive Summary\n\nDraft.",
        weaknesses=[],
        scoring_rules=rules,
    )

    reducer([""])

    prompt = client.prompts[-1]
    assert "FULL SCORING RULES" in prompt
    assert "Scope and research framing" in prompt
    assert "Citation integrity" in prompt
    assert "Readability and formatting" in prompt


# --- N4: code-fence comments are not headings -------------------------------

def test_n4_mask_hides_incode_hash_lines():
    doc = "## Design\n\n```python\n# --- MOCK ---\n# config\nx = 1\n```\n\nbody\n"
    masked = mask_fenced_code(doc)
    assert "# --- MOCK ---" not in masked
    assert "## Design" in masked


def test_n4_sections_present_ignores_incode_heading():
    # "## Conclusion" only appears INSIDE a code block → not a real section.
    doc = "# R\n\n```text\n## Conclusion\n```\n"
    assert sections_present(doc, ["Conclusion"]) == []


def test_n4_detect_gaps_skips_code_comments():
    doc = "## Real\n\ncontent\n\n```python\n# Heading-looking comment\n```\n"
    gaps = _detect_gaps(doc)
    assert not any("comment" in g[0].lower() for g in gaps)


# --- N1: no Frankenstein doubled outline ------------------------------------

def test_n1_no_double_outline_when_concepts_present_under_other_names():
    # Agent organized the report under its OWN names; every template concept is covered
    # in headings or body → the template skeleton must NOT be appended in parallel.
    doc = (
        "# Report\n## Design Architecture\nbackground and scope covered here.\n"
        "## Example Code\n```python\ncode\n```\n## Key Findings\nx\n"
        "## Evidence and Analysis\ny\n## Methodology\nm\n"
        "## Limitations and Open Questions\nl\n## Conclusion and Recommendations\nc\n"
        "## Source References\n- http://e.com\n## Executive Summary\nsummary present\n"
    )
    assert _merge_missing_sections(doc, DEFAULT_TEMPLATE) == doc


def test_n1_sparse_seed_still_gets_missing_sections():
    sparse = "# Report\n## Intro\ntext\n"
    out = _merge_missing_sections(sparse, DEFAULT_TEMPLATE)
    assert len(out) > len(sparse)
    assert "Executive Summary" in out


# --- N2 / N3: refute false missing/truncation weaknesses --------------------

def test_n2_n3_refutes_false_claims_keeps_real_ones():
    doc = (
        "# R\n## Executive Summary\nComplete summary sentence.\n"
        "## Design\n```python\nx = 1\n```\n## Key Findings\nThing.\n"
        "## Conclusion\nWe conclude clearly.\n"
    )
    ws = [
        "[## Design] lack of example code in the report",
        "[document] no conclusion section / ends abruptly",
        "[## Executive Summary] Executive Summary truncated mid-word",
        "[## Key Findings] sources lack URLs",
    ]
    kept = refute_false_weaknesses(ws, doc)
    assert kept == ["[## Key Findings] sources lack URLs"]


def test_refute_false_weaknesses_drops_stale_placeholder_claims():
    doc = (
        "# R\n"
        "## Executive Summary\nSubstantive final content with a cited source https://example.com.\n"
        "## Evidence and Analysis\nDetailed analysis is now present and complete.\n"
    )
    ws = [
        "[document] Placeholder text remains in report: no specific urls were provided",
        "[Executive Summary] assigned this phase but still empty/placeholder (not addressed)",
        "[Evidence and Analysis] assigned this phase but still empty/placeholder (not addressed)",
        "[## Evidence and Analysis] sources lack URLs",
    ]

    kept = refute_false_weaknesses(ws, doc)

    assert kept == ["[## Evidence and Analysis] sources lack URLs"]


def test_n3_section_ends_cleanly():
    doc = "## Summary\nA full sentence.\n## Open\nthis trails off and is cut mid"
    assert _section_ends_cleanly(doc, "Summary") is True
    assert _section_ends_cleanly(doc, "Open") is False
    assert _section_ends_cleanly(doc, "Nonexistent") is None


def test_worker_foci_include_assigned_sections_weaknesses_and_create_guidance():
    foci = build_section_worker_foci(
        ["Executive Summary", "Limitations"],
        ["[## Executive Summary] missing concrete findings", "[document] missing citations"],
        max_sections_per_agent=1,
    )
    assert len(foci) == 2
    assert "ASSIGNED SECTIONS" in foci[0]
    assert "## Executive Summary" in foci[0]
    assert "missing concrete findings" in foci[0]
    assert "missing citations" in foci[0]
    assert "create and populate" in foci[0]
    assert "Do not patch or write content for sections assigned to other agents" in foci[0]


def test_multi_section_worker_focus_keeps_distinct_file_targets():
    foci = build_section_worker_foci(
        ["Executive Summary", "References"],
        ["[document] missing citations"],
        max_sections_per_agent=2,
        section_files={
            "Executive Summary": "sections/001-executive-summary.md",
            "References": "sections/002-references.md",
        },
    )

    assert len(foci) == 1
    assert "## Executive Summary -> sections/001-executive-summary.md" in foci[0]
    assert "## References -> sections/002-references.md" in foci[0]
    assert "do not combine multiple assigned sections into one file" in foci[0]


def test_one_section_file_per_worker_focus_assigns_all_files():
    foci = build_section_assignment_queue(
        ["Executive Summary", "References", "Risks"],
        [],
        section_files={
            "Executive Summary": "sections/001-executive-summary.md",
            "References": "sections/002-references.md",
            "Risks": "sections/003-risks.md",
        },
        agent_slots=2,
    )

    assert len(foci) == 3
    assert "AGENT ID: agent-001" in foci[0]
    assert "sections/001-executive-summary.md" in foci[0]
    assert "sections/002-references.md" not in foci[0]
    assert "sections/003-risks.md" not in foci[0]
    assert "AGENT ID: agent-002" in foci[1]
    assert "sections/002-references.md" in foci[1]
    assert "sections/001-executive-summary.md" not in foci[1]
    assert "sections/003-risks.md" not in foci[1]
    assert "AGENT ID: agent-001" in foci[2]
    assert "sections/003-risks.md" in foci[2]
    assert "sections/001-executive-summary.md" not in foci[2]
    assert "sections/002-references.md" not in foci[2]


def test_section_worker_focus_requires_grounded_reducer_inputs():
    foci = build_section_assignment_queue(
        ["References"],
        ["[## References] missing grounded source URLs"],
        section_files={"References": "sections/008-references.md"},
        scoring_matrix=[
            {"category": "Citation integrity", "points": 20, "signal": "verification"},
        ],
    )

    assert len(foci) == 1
    prompt = foci[0]
    assert "WORKER OUTPUT CONTRACT:" in prompt
    assert "RESEARCH_FINDING" in prompt
    assert "ARTICLE_TITLE" in prompt
    assert "URL" in prompt
    assert "PATCH_TARGET" in prompt
    assert "QUOTE" in prompt
    assert "WHY" in prompt
    assert '{"op":"insert_after","anchor":"## Exact Assigned Heading","content":' in prompt
    assert "op/anchor/content only" in prompt
    assert "do not use legacy PATCH_TARGET/CONTENT patch objects" in prompt
    assert "must not contain markdown headings or full-section prose" in prompt
    assert "never invent bibliography entries" in prompt
    assert "Do not return plain markdown section prose" in prompt
    assert "References section" in prompt


def test_section_assignment_rows_are_atomic_agent_file_pairs():
    rows = build_section_assignment_rows(
        ["Executive Summary", "References", "Risks"],
        ["[## Executive Summary] missing takeaway", "[document] missing citations"],
        section_files={
            "Executive Summary": "sections/001-executive-summary.md",
            "References": "sections/002-references.md",
            "Risks": "sections/003-risks.md",
        },
        agent_slots=2,
    )

    assert [(r["agent_id"], r["section"], r["file"], r["status"]) for r in rows] == [
        ("agent-001", "## Executive Summary", "sections/001-executive-summary.md", "queued"),
        ("agent-002", "## References", "sections/002-references.md", "queued"),
        ("agent-001", "## Risks", "sections/003-risks.md", "queued"),
    ]
    assert "ASSIGNMENT QUEUE FETCH:" in rows[0]["assignment"]
    assert "sections/001-executive-summary.md" in rows[0]["assignment"]
    assert "missing takeaway" in rows[0]["assignment"]
    assert "missing citations" in rows[0]["assignment"]
    assert "sections/002-references.md" not in rows[0]["assignment"]


def test_strip_satisfied_placeholders_keeps_empty_sections_only():
    doc = (
        "## Executive Summary\nReal cited content (https://example.com).\n"
        "_(pending - needs sourced content)_\n\n"
        "## Methodology\n_(pending - needs sourced content)_\n"
    )

    out = strip_satisfied_placeholders(doc)

    assert "## Executive Summary\nReal cited content" in out
    assert "## Methodology\n_(pending - needs sourced content)_" in out
    assert out.count("_(pending - needs sourced content)_") == 1


def test_strip_satisfied_placeholders_replaces_reference_placeholder_from_urls():
    doc = (
        "## Executive Summary\nGrounded claim https://example.com/a.\n\n"
        "## Evidence\nMore evidence https://example.com/b.\n\n"
        "## References\n"
        "*(Note: As no specific URLs were provided in the original draft, this section "
        "serves as a placeholder for the required citations.)*\n"
    )

    out = strip_satisfied_placeholders(doc)

    assert "no specific URLs were provided" not in out
    assert "placeholder" not in out.lower()
    assert "## References\n\n- https://example.com/a\n- https://example.com/b" in out


def test_star_workers_use_explicit_section_foci_not_generic_facets():
    client = RecordingChat()
    step = PlanStep(
        id="s1",
        description="Improve the report",
        topology=STAR,
        worker_foci=(
            "ASSIGNED SECTIONS:\n- ## Executive Summary",
            "ASSIGNED SECTIONS:\n- ## Limitations",
        ),
    )
    result = run_plan(Plan(task="write report", steps=(step,)), client, max_agents=5)

    prompts = "\n\n".join(client.prompts)
    assert "Focus specifically on: ASSIGNED SECTIONS:\n- ## Executive Summary" in prompts
    assert "Focus specifically on: ASSIGNED SECTIONS:\n- ## Limitations" in prompts
    assert "the strongest case for it" not in prompts
    assert result.runs[0].n_agents == 3


def test_map_workers_use_section_foci_when_no_upstream_items():
    client = RecordingChat()
    step = PlanStep(
        id="s1",
        description="Improve mapped report sections",
        topology=MAP,
        worker_foci=(
            "ASSIGNED SECTIONS:\n- ## Evidence and Analysis",
            "ASSIGNED SECTIONS:\n- ## Recommendations",
        ),
    )
    result = run_plan(Plan(task="write report", steps=(step,)), client, max_agents=5)

    prompts = "\n\n".join(client.prompts)
    assert "Focus specifically on: ASSIGNED SECTIONS:\n- ## Evidence and Analysis" in prompts
    assert "Focus specifically on: ASSIGNED SECTIONS:\n- ## Recommendations" in prompts
    assert "Items:" not in prompts
    assert result.runs[0].n_agents == 3


# --- §4d: windowed synthesis no longer silently no-ops on a large doc -------

def test_s4d_windowed_synthesis_large_doc_preserves_urls_and_changes():
    big_section = "para line with detail. " * 60
    doc = "".join(
        f"## Section {i}\n{big_section}\nSee http://src{i}.example/page\n\n"
        for i in range(6)
    )
    assert len(doc) > 8000  # forces the windowed path
    fake = FakeChat()
    out, changed = _synthesize_analysis(doc, fake, "study a topic")
    assert changed is True
    assert fake.calls >= 2  # per-section calls, not a single whole-doc echo
    for i in range(6):
        assert f"http://src{i}.example/page" in out  # every citation preserved


def test_refine_readability_keeps_urls_and_rewrites():
    from studio.artifact_text import _refine_readability
    src = "## Background\nDense note. See http://a.example/x and http://b.example/y.\n"
    out, changed = _refine_readability(src, FakeChat(), "study a topic")
    assert changed is True
    assert "http://a.example/x" in out and "http://b.example/y" in out  # citations preserved


def test_refine_readability_rejects_url_drop():
    from studio.artifact_text import _refine_readability
    class DropURL:
        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            return ChatResult(text="readable prose but no links at all, much longer text here " * 5,
                              total_tokens=1)
    src = "## B\nbody with http://x.com cited.\n"
    out, changed = _refine_readability(src, DropURL(), "t")
    assert changed is False and out == src      # citation-dropping rewrite rejected


class _ScriptedText:
    def __init__(self, text: str) -> None:
        self._text = text

    def chat(self, messages, tools=None):
        from agentkit.types import ChatResult
        return ChatResult(text=self._text, total_tokens=1)


# --- P2-8: References = deterministic bibliography of body-cited URLs -------

def test_references_rebuilt_from_body_citations_drops_refs_only_junk():
    # Live junk (runs 1527/hybrid/current): an off-topic π-Wikipedia finding
    # lived ONLY in References, self-rationalizing its relevance. It earned no
    # body citation, so the rebuild drops it with the prose.
    from studio.artifact_text import rebuild_references_section
    doc = (
        "# T\n\n## Key Findings\n"
        "The toolkit layers packages ([Pi toolkit](https://github.com/earendil-works/pi)).\n"
        "Loop details at https://nader.substack.com/p/how-to-build-a-custom-agent-framework.\n\n"
        "## References\n\n"
        "A foundational mathematical definition of Pi, which serves as a baseline for "
        "any computational or algorithmic agent development involving geometric "
        "calculations ([Pi - Wikipedia](https://en.wikipedia.org/wiki/Pi)).\n"
    )
    out = rebuild_references_section(doc)
    refs = out.split("## References", 1)[1]
    assert "wikipedia.org/wiki/Pi" not in refs
    assert "- [Pi toolkit](https://github.com/earendil-works/pi)" in refs
    assert "- https://nader.substack.com/p/how-to-build-a-custom-agent-framework" in refs
    assert "geometric calculations" not in out


def test_references_rebuild_is_fail_open():
    from studio.artifact_text import rebuild_references_section
    no_heading = "# T\n\n## Key Findings\nSee https://a.test/x.\n"
    assert rebuild_references_section(no_heading) == no_heading
    no_body_urls = "# T\n\n## Key Findings\nUncited prose.\n\n## References\nJunk prose.\n"
    assert rebuild_references_section(no_body_urls) == no_body_urls


def test_references_rebuild_preserves_content_after_the_section():
    from studio.artifact_text import rebuild_references_section
    doc = (
        "# T\n\n## Body\nSee https://a.test/x.\n\n"
        "## References\nOld prose.\n\n## Appendix\nKept.\n"
    )
    out = rebuild_references_section(doc)
    assert "- https://a.test/x" in out
    assert "## Appendix\nKept." in out
    assert "Old prose" not in out


def test_synthesis_rejects_refusal_prose_on_citation_free_section():
    # Live run 1530 (score 0.97 → 0.087): a URL-free window makes the URL guard
    # vacuous (∅ == ∅), so gemma's meta-refusal — which QUOTED heading names —
    # was accepted and its quoted headings became fake sections after References.
    from studio.artifact_text import _synthesize_analysis
    refusal = (
        'I understand the hard rules (e.g. "The result must be at least as long '
        'as the draft.", never drop content after "## Implications or '
        'Recommendations").\n\nAnd never reorder anything after "## References"). '
        "Once you provide the text containing the citations and information, I "
        "will immediately transform it into the flowing, synthesized prose you "
        "described while adhering to all your hard rules."
    )
    src = "## Implications or Recommendations\nShort uncited paragraph.\n"
    out, changed = _synthesize_analysis(src, _ScriptedText(refusal), "study a topic")
    assert changed is False and out == src


def test_synthesis_rejects_invented_headings():
    from studio.artifact_text import _synthesize_analysis
    src = "## Key Findings\nFinding text citing http://x.com here.\n"
    invented = (
        "## Key Findings\nFinding text citing http://x.com here, now analyzed at length "
        "with cross-source comparison and additional interpretation of trade-offs.\n"
        "## Bonus Section\nInvented structure.\n"
    )
    out, changed = _synthesize_analysis(src, _ScriptedText(invented), "t")
    # Contract update (2026-07-05 echo-salvage): an invented heading must never
    # ENTER the document. The rewrite's own section is salvaged (it is genuine
    # analysis), the invented section is discarded — strictly better than the
    # old wholesale reject, same invariant.
    assert "Bonus Section" not in out
    assert changed is True and out.startswith("## Key Findings")
    assert "http://x.com" in out


def test_synthesis_accepts_real_rewrite_on_citation_free_section():
    # The overlap guard must not over-trigger: genuine analysis prose that
    # paraphrases but keeps the draft's topic vocabulary is still accepted.
    from studio.artifact_text import _synthesize_analysis
    src = (
        "## Implications or Recommendations\nThe toolkit separates the agent loop "
        "from session persistence, and adoption depends on operational simplicity.\n"
    )
    rewrite = (
        "## Implications or Recommendations\nTaken together, the findings suggest a "
        "layered adoption path: the toolkit's agent loop comes first, with session "
        "persistence added once needed. The trade-off is flexibility against "
        "operational simplicity, and the sources consistently favour starting simple.\n"
    )
    out, changed = _synthesize_analysis(src, _ScriptedText(rewrite), "t")
    assert changed is True and "layered adoption path" in out


def test_s4d_small_doc_unchanged_when_client_noop():
    # Below the window: single block. A client that drops a URL → rejected (unchanged).
    class DropURL:
        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            return ChatResult(text="short", total_tokens=1)
    src = "## A\nbody with http://x.com here.\n"
    out, changed = _synthesize_analysis(src, DropURL(), "t")
    assert changed is False and out == src


# --- §4b: the injected reducer is honored by MESH (not STAR-only) ------------

def test_s4b_reducer_used_by_mesh():
    seen = {}

    def reducer(drafts):
        seen["drafts"] = list(drafts)
        return "REDUCED-OUTPUT", 3

    p = make_plan("compare X and Y")
    p, _ = assign_topologies_with_choices(p)
    assert p.steps[0].topology == MESH
    res = run_plan(p, FakeChat(), reducer=reducer)
    assert res.runs[0].output == "REDUCED-OUTPUT"
    assert seen.get("drafts")  # the reducer actually received the peer drafts


def test_s4b_reducer_used_by_single_identity_fold():
    # E1: SINGLE now routes its lone draft through the injected reducer (identity-fold),
    # so selection can be honored under hill-climb without force-STAR.
    seen = {}

    def reducer(drafts):
        seen["drafts"] = list(drafts)
        return "FOLDED", 2

    p = make_plan("write a short recommendation")
    p, _ = assign_topologies_with_choices(p)
    assert p.steps[0].topology == SINGLE
    res = run_plan(p, FakeChat(), reducer=reducer)
    assert res.runs[0].output == "FOLDED"
    assert len(seen["drafts"]) == 1  # the single draft is folded


def test_s4b_no_reducer_single_unchanged():
    # CLI default (reducer=None) → SINGLE returns the bare text, unchanged.
    p = make_plan("write a short recommendation")
    p, _ = assign_topologies_with_choices(p)
    res = run_plan(p, FakeChat())
    assert "analysis added" in res.runs[0].output  # the raw chat output, not folded


def test_s4b_reducer_used_by_pipeline_terminal_capture():
    seen = {"calls": 0}

    def reducer(drafts):
        seen["calls"] += 1
        seen["drafts"] = list(drafts)
        return "TERMINAL", 1

    p = make_plan("first do X then do Y as a pipeline")
    p, _ = assign_topologies_with_choices(p)
    assert p.steps[0].topology == "pipeline"
    res = run_plan(p, FakeChat(), reducer=reducer)
    assert res.runs[0].output == "TERMINAL"
    assert seen["calls"] == 1 and len(seen["drafts"]) == 1  # only the terminal stage folded


# --- E2 / E3: selection surfaces rationale; mapping is correct ---------------

def test_e2_assign_with_choices_returns_rationale():
    p = make_plan("gather sources on agents")
    p2, choices = assign_topologies_with_choices(p)
    assert p2.steps[0].id in choices
    ch = choices[p2.steps[0].id]
    assert ch.topology == STAR
    assert ch.rationale  # non-empty WHY string
    assert ch.questions_fired


@pytest.mark.parametrize(
    "desc, expected",
    [
        ("Compare vector RAG and GraphRAG", MESH),
        ("gather sources on the topic", STAR),
        ("search the web and collect findings", STAR),
        ("write a short recommendation", SINGLE),
    ],
)
def test_e3_selection_quality_classify(desc, expected):
    assert classify_step_topology(desc) == expected


# --- G3: reduce-time assignment verification --------------------------------

def test_g3_unmet_assignment_flagged():
    assigned = {"agent-1": ["## Key Findings"], "agent-2": ["## Methodology"]}
    doc = "# R\n## Key Findings\nReal content here.\n## Methodology\n_(to be completed)_\n"
    unmet = verify_assignment_coverage(assigned, doc)
    assert len(unmet) == 1
    assert "Methodology" in unmet[0]


def test_g3_all_covered_returns_empty():
    assigned = {"agent-1": ["## Key Findings"]}
    doc = "# R\n## Key Findings\nReal populated content.\n"
    assert verify_assignment_coverage(assigned, doc) == []


def test_g3_absent_section_flagged():
    assigned = {"agent-1": ["## Nowhere"]}
    doc = "# R\n## Something Else\ntext\n"
    unmet = verify_assignment_coverage(assigned, doc)
    assert len(unmet) == 1 and "absent" in unmet[0]


# --- G4: new-section assignment shape ---------------------------------------

def test_g4_parse_assigned_handles_create_jobs():
    from studio.planning import _parse_assigned
    text = (
        'ASSIGNED:\n```json\n'
        '{"agent-1": {"sections": ["## Key Findings"], "create": ["## Limitations"]},\n'
        ' "agent-2": ["## Methodology"]}\n```\n'
    )
    a = _parse_assigned(text)
    # create jobs flatten into the agent's section list so G3 verifies them too
    assert a["agent-1"] == ["## Key Findings", "## Limitations"]
    assert a["agent-2"] == ["## Methodology"]


# --- E4: planner topology intent --------------------------------------------

def test_e4_plan_from_epics_honors_topology_intent():
    from studio.planning import _plan_from_epics

    class EpicClient:
        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            return ChatResult(text=(
                'EPIC_PLAN:\n```json\n{"epics":['
                '{"id":"e1","description":"compare A and B","topology":"mesh"},'
                '{"id":"e2","description":"gather sources","topology":"star"},'
                '{"id":"e3","description":"write summary"}]}\n```'
            ), total_tokens=5)

    plan = _plan_from_epics("task", EpicClient())
    by_id = {s.id: s.topology for s in plan.steps}
    assert by_id["e1"] == MESH          # explicit intent honored
    assert by_id["e2"] == STAR          # explicit intent honored
    assert by_id["e3"] is None          # no intent → left for the selector to fill


# --- E3: selection-quality (the §2.7 rules fire correctly) ------------------

@pytest.mark.parametrize("spec_kwargs, expected", [
    ({"subtasks": ("a", "b"), "subtasks_independent": True}, STAR),
    ({"subtasks": ("a", "b"), "subtasks_independent": True, "workers_challenge": True}, MESH),
    ({"subtasks": ("a", "b"), "subtasks_independent": False}, "pipeline"),
    ({"single_agent_sufficient": True}, SINGLE),
])
def test_e3_select_topology_rules(spec_kwargs, expected):
    from agentkit.topology.core import TaskSpec, select_topology
    assert select_topology(TaskSpec("t", **spec_kwargs)).topology == expected


def test_e3_assign_with_choices_uses_infer_spec_under_llm():
    # the llm path must route through infer_spec → select_topology (not the keyword classifier)
    calls = {"n": 0}

    class SpyClient:
        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            calls["n"] += 1
            return ChatResult(text="{}", total_tokens=1)

    p = make_plan("do the thing")
    _p, choices = assign_topologies_with_choices(p, client=SpyClient(), llm=True)
    assert calls["n"] >= 1                      # infer_spec was actually consulted
    assert all(c.rationale for c in choices.values())


# --- §6: deterministic section assembly (no LLM) ----------------------------

def test_s6_deterministic_concat_assembles_sections_no_llm():
    from agentkit.artifacts.patcher import DocPatch, reduce_patches
    seed = "# Report\n\n## Key Findings\n\n## Methodology\n"
    patches = [
        DocPatch(op="insert_after", anchor="## Key Findings",
                 content="\nFinding one (http://a.example).\n", source="t"),
        DocPatch(op="insert_after", anchor="## Methodology",
                 content="\nWe surveyed sources.\n", source="t"),
    ]
    rr = reduce_patches(seed, [patches])  # NO llm_refine_fn → pure deterministic
    # every seed section survives, in order, with its addition; nothing truncated
    assert "## Key Findings" in rr.text and "## Methodology" in rr.text
    assert "Finding one" in rr.text and "We surveyed sources" in rr.text
    assert rr.text.index("Key Findings") < rr.text.index("Methodology")  # document order
    assert len(rr.text) >= len(seed)  # additive, never shorter


# --- §4c: per-spoke I/O trail -----------------------------------------------

def test_s4c_star_run_surfaces_per_spoke_agent_io():
    p = make_plan("gather sources on agents")
    p, _ = assign_topologies_with_choices(p)
    assert p.steps[0].topology == STAR
    res = run_plan(p, FakeChat())
    io = res.runs[0].agent_io
    assert len(io) >= 2  # per-spoke agent records + a reducer record
    assert all({"role", "prompt", "output", "tokens"} <= set(r) for r in io)
    assert any(r["role"] == "reducer" for r in io)


# --- N1 reconcile + §4d dedup (unit) ----------------------------------------

def test_n1_reconcile_drops_empty_template_duplicate():
    from studio.artifact_text import reconcile_outline
    doc = ("# R\n## Key Insights\nReal populated findings here.\n"
           "## Key Findings\n_(to be completed)_\n## Source References\n- http://a.com\n")
    out = reconcile_outline(doc, DEFAULT_TEMPLATE)
    assert "_(to be completed)_" not in out          # empty dup dropped
    assert "Source References" in out                # populated section kept


def test_s4d_dedup_drops_verbatim_paragraph_including_url_bearing():
    """§14 slate B review fix: an EXACT duplicate paragraph is dropped regardless
    of URL — an identical copy loses nothing, so keeping it only multiplies the
    same citation (was: URL-bearing paragraphs were never deduped, even
    byte-identical ones, which let 3 identical echoes triple a citation)."""
    from studio.artifact_text import _dedup_paragraphs
    text = "Same para.\n\nSame para.\n\nSee http://x.com\n\nSee http://x.com"
    out = _dedup_paragraphs(text)
    assert out.count("Same para.") == 1             # verbatim dup dropped
    assert out.count("http://x.com") == 1           # exact-duplicate URL para also dropped


def test_s4d_dedup_keeps_similar_but_different_url_paragraphs():
    """The never-drop protection is for DISTINCT citations, not identical
    copies — two different sentences sharing a URL are never deduped away."""
    from studio.artifact_text import _dedup_paragraphs
    text = "See http://x.com for the first point.\n\nSee http://x.com for a second, different point."
    out = _dedup_paragraphs(text)
    assert out.count("http://x.com") == 2


# --- N1 anti-accumulation: collapse duplicate section headings ---------------

def test_dedupe_sections_collapses_repeated_headings_keeps_richest():
    import re
    from studio.artifact_text import dedupe_sections
    # the echo-accumulation shape: a section stacked 3× with bodies of different richness
    doc = (
        "# Report\n\n## Key Findings\nthin\n\n## Methodology\nm-body\n\n"
        "## Key Findings\nmuch richer body with detail and a source http://a.example\n\n"
        "## Key Findings\nmid\n"
    )
    out = dedupe_sections(doc)
    heads = re.findall(r"(?m)^#{1,4}\s+(.+)$", out)
    assert heads.count("Key Findings") == 1                 # collapsed to one
    assert "much richer body" in out                        # richest body kept
    assert "## Methodology" in out and "m-body" in out      # other section untouched


def test_normalize_artifact_unglues_headings_and_dedupes():
    from studio.artifact_text import normalize_artifact
    import re
    # the real "wrong format": a heading glued to the next heading on one line, plus a dup
    doc = (
        "# R\n\n## Design Architecture### Implementation Methodology\nbody1\n\n"
        "## Key Findings\nfindings\n\n## Key Findings\nfindings\n\n"
        "```python\nx = 1  # not a heading\n```\n"
    )
    out = normalize_artifact(doc)
    # glued '### Implementation' is split onto its own line
    assert re.search(r"(?m)^### Implementation Methodology", out)
    assert "Architecture###" not in out
    # the duplicate '## Key Findings' collapsed to one
    assert len(re.findall(r"(?m)^## Key Findings", out)) == 1
    # the in-code '# comment' is untouched (single # not split)
    assert "x = 1  # not a heading" in out
    assert normalize_artifact(out) == out          # idempotent


def test_resolve_report_title_replaces_template_placeholder() -> None:
    doc = "# _(report title - generated from the findings below)_\n\n## Executive Summary\nBody.\n"
    out = resolve_report_title(
        doc,
        "Write a research report about catalog management for local and remote agent loops. Include citations.",
    )

    assert out.startswith("# Catalog management for local and remote agent loops\n\n")
    assert "report title" not in out.lower()
    assert "## Executive Summary" in out


def test_resolve_report_title_preserves_model_title() -> None:
    doc = "# Existing Model Title\n\n## Executive Summary\nBody.\n"

    assert resolve_report_title(doc, "Write a research report about anything.") == doc


def test_resolve_report_title_replaces_generic_model_title() -> None:
    doc = "# Research Report\n\n## Executive Summary\nBody.\n"
    out = resolve_report_title(
        doc,
        "Write a research report about remote skill catalog governance and rollback.",
    )

    assert out.startswith("# Remote skill catalog governance and rollback\n\n")
    assert "# Research Report" not in out


def test_resolve_report_title_keeps_use_as_core_topic_word() -> None:
    # Real live bug (session s_9b7bacfdc703): "use" inside the actual topic
    # ("how to USE Pi and Craft") was matched by the old _TITLE_STOP_RE as a
    # trailing-instruction marker, deleting the entire rest of the title down
    # to "# Study how to". The stop-word must only fire on a genuine trailing
    # instruction clause (comma-anchored), not a verb that's part of the topic.
    doc = "# Research Report\n\n## Executive Summary\nBody.\n"
    out = resolve_report_title(
        doc,
        "Study how to use Pi and Craft to develop agents and create a research "
        "report, need to include example code or design architecture.",
    )

    # The stop-clause drop leaves a clean 15-word topic; the old 14-word cap
    # then chopped "report" off mid-noun-phrase (live H1, 2026-07-05). The cap
    # is now 18 words and must never cut inside the topic phrase.
    assert out.startswith(
        "# Study how to use Pi and Craft to develop agents and create a research report\n\n"
    )
    assert "example code" not in out.splitlines()[0]


def test_resolve_report_title_cut_never_ends_on_dangling_word() -> None:
    doc = "# Research Report\n\n## Executive Summary\nBody.\n"
    out = resolve_report_title(
        doc,
        "Evaluate the throughput scaling limits observed when running large distributed "
        "training clusters across heterogeneous accelerator fleets and summarize the "
        "impact of interconnect topology and the",
    )
    title = out.splitlines()[0]
    assert len(title.split()) <= 19  # "# " + max 18 words
    assert title.split()[-1].lower() not in {"a", "an", "the", "and", "or", "to", "of"}


def test_resolve_report_title_drops_long_context_clause() -> None:
    doc = "# Research Report\n\n## Executive Summary\nBody.\n"
    out = resolve_report_title(
        doc,
        "Write a research report about catalog management for local and remote agent loops "
        "and skills in a generic research report generator. Include citations.",
    )

    assert out.startswith("# Catalog management for local and remote agent loops and skills\n\n")
    assert "generic research" not in out.splitlines()[0]


def test_dedupe_sections_noop_on_clean_doc_and_enumerator_match():
    from studio.artifact_text import dedupe_sections
    clean = "# R\n\n## A\nbody a\n\n## B\nbody b\n"
    assert dedupe_sections(clean).strip() == clean.strip()  # no dup → unchanged
    # '## 2. Design' and '## Design' are the same section (enumerator-insensitive)
    dupe = "## 2. Design\nshort\n\n## Design\nlonger richer body here\n"
    out = dedupe_sections(dupe)
    import re
    assert len(re.findall(r"(?m)^#{1,4}\s", out)) == 1
    assert "longer richer body" in out


def test_normalize_artifact_dedupes_repeated_long_sentences_when_url_seen() -> None:
    from studio.artifact_text import normalize_artifact
    import re

    sentence = (
        "Detailed documentation can be found in the references page "
        "for this multi-agent architecture, including component responsibilities, "
        "interaction rules, and implementation details "
        "(https://example.com/reference-architecture.html)."
    )
    doc = f"# R\n\n## Evidence\n{sentence}\n\n## References\n{sentence}\n"

    out = normalize_artifact(doc)

    assert len(re.findall(re.escape(sentence), out)) == 1
    assert "## References" in out


# --- F6: reducer-side finding consolidation (thin the quote-wall at source) ---

def test_strip_scaffold_removes_boilerplate_lead_only():
    from agentkit.artifacts.dedup import _strip_scaffold
    assert _strip_scaffold("This defines: the agent loop") == "The agent loop"
    assert _strip_scaffold("This section provides an overview") == "An overview"
    assert _strip_scaffold("Agents iterate over tools") == "Agents iterate over tools"
    assert _strip_scaffold("") == ""


def test_consolidate_findings_same_url_merge_keeps_richest():
    from agentkit.artifacts.dedup import consolidate_findings, _default_norm_url
    from agentkit.artifacts.types import Finding
    fs = [
        Finding(url="https://a.com/x", why="thin", patch_target="## A"),
        Finding(url="http://a.com/x/", why="much richer framing", quote="q",
                quote_verified=True, patch_target="## A"),
        Finding(url="https://b.com", why="other", patch_target="## B"),
    ]
    kept, st = consolidate_findings(fs, norm_url=_default_norm_url)
    assert len(kept) == 2 and st["url_merged"] == 1
    acom = next(k for k in kept if "a.com" in k.url)
    assert acom.quote_verified  # http/https + trailing-slash collapsed, richest survived


def test_consolidate_findings_density_cap_per_target():
    from agentkit.artifacts.dedup import consolidate_findings, _default_norm_url
    from agentkit.artifacts.types import Finding
    fs = [Finding(url=f"https://s{i}.com", why=f"w{i}", patch_target="## T") for i in range(5)]
    kept, st = consolidate_findings(fs, norm_url=_default_norm_url, max_per_target=2)
    assert len(kept) == 2 and st["capped"] == 3


# --- reducer injects FULL scoring + weaknesses + fetched evidence, and is inspectable ---

class _RRes:
    text = "PATCHES:\n```json\n[]\n```"
    total_tokens = 0


class _RClient:
    def __init__(self):
        self.prompts = []

    def chat(self, msgs):
        self.prompts.append(msgs[0]["content"])
        return _RRes()


def test_reducer_prompt_injects_scoring_weaknesses_and_fetched_evidence():
    from studio.findings import _make_section_reducer
    c = _RClient()
    ev = lambda _t: "- evidence/source-001.md — https://a.com/doc"
    red = _make_section_reducer(
        c, "## Key Findings\nbody", ["add sources"],
        scoring_rules="- Source quality (14.7 pts): satisfy via sourcing",
        evidence_fn=ev,
    )
    red(["[worker 1]\nURL: https://a.com/doc"])
    p = c.prompts[0]
    assert "FULL SCORING RULES" in p and "Source quality (14.7" in p
    assert "SECTION WEAKNESSES" in p and "add sources" in p
    assert "FETCHED EVIDENCE FILES" in p and "source-001.md" in p
    # evidence precedes the artifact body section
    assert p.index("FETCHED EVIDENCE FILES") < p.index("CURRENT ARTIFACT:\n--- BEGIN")
    # captured for io/<step>.reducer.in.md persistence
    assert red._io_capture.get("prompt")


def test_reducer_omits_evidence_block_when_no_fetch_fn():
    from studio.findings import _make_section_reducer
    c = _RClient()
    _make_section_reducer(c, "## A\nx", [], scoring_rules="- rule")(["[worker 1]\nx"])
    assert "FETCHED EVIDENCE FILES" not in c.prompts[0]
