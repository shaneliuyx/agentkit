"""studio.requirement_compliance — generic explicit-requirement checker.

Unit tests pin the module's own logic (extraction line-parsing, per-requirement
verdict aggregation, penalty scale, fail-open) with a scripted client. The REAL
question — can a live model extract genuine requirements from arbitrary task
text and verify them against an artifact — is answered by the two
``@pytest.mark.integration`` tests at the bottom (deselected by default), which
run the deployed gemma model over the real Pi/Craft task and a second,
different-requirement (citations) task from task_runs.db. See the WORKLOG entry
for the full calibration trace, including why the naive verifier needed
evidence-grounding.
"""
from __future__ import annotations

import pathlib
import re
import sqlite3

import pytest

FIXTURES_ROOT = pathlib.Path(__file__).parent.parent / "tmp"

from studio.requirement_compliance import (
    _extract_prompt,
    extract_requirements,
    requirement_compliance_issues,
)


class _ScriptedExtractor:
    """Fake client returning a fixed extractor reply (newline-listed requirements)."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.n_calls = 0
        self.last_prompt = ""

    def chat(self, messages, tools=None):
        from agentkit.types import ChatResult
        self.n_calls += 1
        self.last_prompt = str(messages[-1].get("content", ""))
        return ChatResult(text=self.reply, total_tokens=5)


class _ScriptedVerifier:
    """Fake client returning a fixed verifier reply (REQUIREMENT n: SATISFIED/...)."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.n_calls = 0
        self.last_prompt = ""

    def chat(self, messages, tools=None):
        from agentkit.types import ChatResult
        self.n_calls += 1
        self.last_prompt = str(messages[-1].get("content", ""))
        return ChatResult(text=self.reply, total_tokens=5)


# --- extraction ------------------------------------------------------------

def test_extract_requirements_parses_listed_requirements() -> None:
    """Each line without a ' || ' delimiter is a single-branch (mandatory) group."""
    client = _ScriptedExtractor(
        "1. include example code\n"
        "2. cite at least 3 primary sources\n"
    )
    reqs = extract_requirements(client, "study X and write a report, include code and cite sources")
    assert reqs == [
        ["include example code"],
        ["cite at least 3 primary sources"],
    ]
    assert client.n_calls == 1
    assert "study X" in client.last_prompt


def test_extract_requirements_parses_or_alternatives_into_one_group() -> None:
    """A ' || '-delimited line becomes ONE group with multiple interchangeable
    branches (the OR structure the checker must preserve, not flatten)."""
    client = _ScriptedExtractor(
        "1. include example code || include a design architecture\n"
        "2. cite at least 3 primary sources\n"
    )
    reqs = extract_requirements(client, "study Pi and Craft; include example code or design architecture")
    assert reqs == [
        ["include example code", "include a design architecture"],
        ["cite at least 3 primary sources"],
    ]


def test_extract_prompt_contains_and_or_contrast_rule() -> None:
    """Regression (root cause of a live diagram-requirement miss): the prompt's OWN
    OR-example used to read 'include example code OR a design architecture', which
    collided with a live task phrased as 'include example code AND design
    architecture' — the model pattern-matched the AND onto the OR example and
    emitted one OR group instead of two mandatory ones. The example must use
    generic, task-unrelated content, and the prompt must explicitly contrast
    'and' vs 'or'."""
    prompt = _extract_prompt("some task")
    # The OR-alternative EXAMPLE no longer pairs "example code" with "design
    # architecture" — the exact collision that made a live AND-task's wording
    # pattern-match onto the OR example.
    assert "design architecture" not in prompt.lower()
    assert "'and' vs 'or'" in prompt
    assert "TWO SEPARATE lines" in prompt


def test_extract_requirements_parses_two_separate_lines_for_and_conjunction() -> None:
    """Parse behavior is UNCHANGED by the prompt fix: two lines with no ' || '
    delimiter still become two separate single-branch (mandatory) groups, never
    merged into one OR group — this is what makes the AND/OR fix effective."""
    client = _ScriptedExtractor(
        "1. include example code\n"
        "2. include a design architecture\n"
    )
    reqs = extract_requirements(client, "include example code and a design architecture")
    assert reqs == [
        ["include example code"],
        ["include a design architecture"],
    ]


def test_extract_prompt_contains_subject_coverage_rule() -> None:
    """Regression (run 1534): 'Study how to use Pi and Craft' produced a report
    covering ONLY Pi — extraction never emitted a 'covers Craft' line, so the
    gap was invisible to compliance and hill-climb carry-forward locked in the
    one-sided artifact. The prompt must tell the model to split named subjects
    into separate 'covers <subject>' lines, generically (no task words)."""
    prompt = _extract_prompt("some task")
    assert "SUBJECT COVERAGE" in prompt
    assert "covers <subject>" in prompt
    assert "Pi" not in prompt and "Craft" not in prompt  # no task-specific hardcoding


def test_extract_requirements_parses_subject_coverage_lines_separately() -> None:
    """Parse behavior is UNCHANGED: two 'covers X' lines with no ' || ' stay two
    separate mandatory groups, exactly like any other unrelated pair of lines."""
    client = _ScriptedExtractor(
        "1. covers Pi\n"
        "2. covers Craft\n"
    )
    reqs = extract_requirements(client, "study how to use Pi and Craft to develop agents")
    assert reqs == [["covers Pi"], ["covers Craft"]]


def test_extract_prompt_names_generic_role_word_guardrail_examples() -> None:
    """The SUBJECT COVERAGE guardrail must name concrete generic-role examples
    (not just an abstract warning) so a weak model has something to pattern
    against — 'agents' and 'the system' are the two the reviewer asked to pin."""
    prompt = _extract_prompt("some task")
    assert "'agents'" in prompt
    assert "'the system'" in prompt


def test_extract_requirements_generic_nouns_produce_no_covers_group() -> None:
    """A task naming only generic role words, correctly NOT split into 'covers X'
    lines by the model, must parse with zero coverage groups — pins the parser
    half of the guardrail contract (the prompt half is covered by the live probe)."""
    client = _ScriptedExtractor(
        "1. build the system\n"
        "2. write a research report\n"
        "3. improve agents\n"
    )
    reqs = extract_requirements(client, "build the system, write a research report, improve agents")
    assert not any(b.lower().startswith("covers ") for group in reqs for b in group)


def test_extract_requirements_strips_bullet_markers() -> None:
    client = _ScriptedExtractor("- include a comparison table\n* keep it under 800 words")
    assert extract_requirements(client, "some task") == [
        ["include a comparison table"],
        ["keep it under 800 words"],
    ]


def test_extract_requirements_none_sentinel_returns_empty() -> None:
    client = _ScriptedExtractor("NONE")
    assert extract_requirements(client, "just research the topic thoroughly") == []


def test_extract_requirements_empty_inputs_no_call() -> None:
    client = _ScriptedExtractor("x")
    assert extract_requirements(None, "task") == []
    assert extract_requirements(client, "") == []
    assert extract_requirements(client, "   ") == []
    assert client.n_calls == 0


def test_extract_requirements_fail_open_on_client_error() -> None:
    class _Boom:
        def chat(self, *a, **k):
            raise RuntimeError("down")

    assert extract_requirements(_Boom(), "a real task with requirements") == []


# --- verification ----------------------------------------------------------

def test_verification_flags_unsatisfied_mandatory_requirement() -> None:
    """Two SEPARATE mandatory items (a flat list, normalized to single-branch
    groups): one satisfied, one not → one hard issue, half penalty, no opportunity."""
    client = _ScriptedVerifier(
        "REQUIREMENT 1: SATISFIED\nREQUIREMENT 2: NOT_SATISFIED"
    )
    penalty, hard, opps = requirement_compliance_issues(
        client,
        ["include example code", "include a design architecture diagram"],
        "Here is a report.\n```python\nprint('hi')\n```\nNo diagram at all.\n" * 5,
    )
    assert penalty == 0.5
    assert len(hard) == 1
    assert "design architecture diagram" in hard[0]
    assert "not satisfied" in hard[0].lower()
    assert opps == []


def test_or_group_satisfied_by_one_branch_is_opportunity_not_penalty() -> None:
    """THE Codex-specified pinning case. Task requirement 'include example code
    OR design architecture' (one OR group), artifact has real code but no
    architecture. The OR is HONESTLY satisfied by the code:

      * compliance_penalty == 0
      * hard_issues == []
      * quality_opportunities has EXACTLY one entry naming the unfulfilled
        alternative (the missing architecture branch)."""
    client = _ScriptedVerifier(
        "REQUIREMENT 1: SATISFIED\nREQUIREMENT 2: NOT_SATISFIED"
    )
    penalty, hard, opps = requirement_compliance_issues(
        client,
        [["include example code", "include design architecture"]],
        "A report with real code.\n```python\nrun()\n```\nNo architecture diagram.\n" * 5,
    )
    assert penalty == 0.0
    assert hard == []
    assert len(opps) == 1
    assert "include design architecture" in opps[0]


def test_or_group_no_branch_satisfied_is_hard_issue() -> None:
    """An OR group where NEITHER branch is met is a genuine hard miss (penalty +
    hard issue), NOT a soft opportunity — proves the split does not swallow real
    failures under 'opportunity'."""
    client = _ScriptedVerifier(
        "REQUIREMENT 1: NOT_SATISFIED\nREQUIREMENT 2: NOT_SATISFIED"
    )
    penalty, hard, opps = requirement_compliance_issues(
        client, [["include code", "include architecture"]], "empty prose " * 30
    )
    assert penalty == 1.0
    assert len(hard) == 1
    assert "none of the stated alternatives" in hard[0]
    assert opps == []


def test_diagram_shaped_or_branch_satisfied_without_mermaid_becomes_opportunity() -> None:
    """Real live root cause (session s_791e2db70e88, task_hash 39ee3efddbd9): the
    verifier marked 'design architecture' SATISFIED on a table + prose alone (no
    diagram present), so the structural-editor retry never even got a chance to
    fire — the opportunity never existed upstream. A diagram-shaped branch's
    SATISFIED verdict must be deterministically downgraded when the artifact has
    no real mermaid block, so the (still code-satisfied) OR group correctly
    surfaces the missing diagram as a quality opportunity instead of silence."""
    client = _ScriptedVerifier(
        "REQUIREMENT 1: SATISFIED\nREQUIREMENT 2: SATISFIED"
    )
    penalty, hard, opps = requirement_compliance_issues(
        client,
        [["include example code", "include design architecture"]],
        "Real code:\n```python\nloop()\n```\nand a Modular Architecture Overview "
        "table with prose about the agentic loop. No diagram anywhere.\n" * 3,
    )
    assert penalty == 0.0  # OR still honestly satisfied by the code branch
    assert hard == []
    assert len(opps) == 1
    assert "design architecture" in opps[0]


def test_diagram_shaped_branch_satisfied_with_real_mermaid_is_not_downgraded() -> None:
    """No over-triggering: when a real mermaid block IS present, the SATISFIED
    verdict must stand — proves the gate only fires on the genuine gap."""
    client = _ScriptedVerifier(
        "REQUIREMENT 1: SATISFIED\nREQUIREMENT 2: SATISFIED"
    )
    penalty, hard, opps = requirement_compliance_issues(
        client,
        [["include example code", "include design architecture"]],
        "Code:\n```python\nx()\n```\n```mermaid\ngraph TD\nA-->B\n```\narchitecture.\n" * 3,
    )
    assert penalty == 0.0
    assert hard == []
    assert opps == []


def test_mandatory_diagram_requirement_satisfied_without_mermaid_is_hard_issue() -> None:
    """A single-branch (mandatory, no OR) diagram-shaped requirement must become
    a genuine hard miss when downgraded, not merely an opportunity — matches the
    module's existing group-verdict-aggregation semantics for a group of one."""
    client = _ScriptedVerifier("REQUIREMENT 1: SATISFIED")
    penalty, hard, opps = requirement_compliance_issues(
        client,
        [["include an architecture diagram"]],
        "Prose describing the architecture in words only, no diagram block." * 3,
    )
    assert penalty == 1.0
    assert len(hard) == 1
    assert opps == []


def test_mandatory_code_requirement_satisfied_without_fence_is_hard_issue() -> None:
    """P2-8b (run 1531): 'include example code' scored SATISFIED on prose
    DESCRIBING code with zero fenced blocks. A code-shaped SATISFIED verdict must
    be deterministically downgraded when no real (non-mermaid) fence exists."""
    client = _ScriptedVerifier("REQUIREMENT 1: SATISFIED")
    penalty, hard, opps = requirement_compliance_issues(
        client,
        [["include example code"]],
        "A minimal setup can be achieved using the pi-ai package to initialize "
        "a model and perform completions, e.g. completeSimple." * 3,
    )
    assert penalty == 1.0
    assert len(hard) == 1
    assert "fenced" in hard[0]
    assert opps == []


def test_code_requirement_with_real_fence_is_not_downgraded() -> None:
    """No over-triggering: a real fenced block (labeled or not) keeps SATISFIED."""
    client = _ScriptedVerifier("REQUIREMENT 1: SATISFIED")
    penalty, hard, opps = requirement_compliance_issues(
        client,
        [["include example code"]],
        "Setup:\n```ts\nconst m = completeSimple('claude')\n```\ndone.\n" * 3,
    )
    assert penalty == 0.0
    assert hard == []
    assert opps == []


def test_mermaid_only_artifact_does_not_count_as_code_fence() -> None:
    """A mermaid diagram is not code: a code-shaped SATISFIED verdict on a
    mermaid-only artifact is still downgraded."""
    client = _ScriptedVerifier("REQUIREMENT 1: SATISFIED")
    penalty, hard, opps = requirement_compliance_issues(
        client,
        [["include a code snippet"]],
        "Only a diagram:\n```mermaid\ngraph TD\nA-->B\n```\nprose.\n" * 3,
    )
    assert penalty == 1.0
    assert len(hard) == 1


def test_non_diagram_shaped_requirement_is_never_downgraded() -> None:
    """The keyword gate must stay narrow: a requirement with no diagram-shaped
    phrasing is untouched by the mermaid check even when no mermaid exists
    anywhere in the artifact — proves no false positives on unrelated asks."""
    client = _ScriptedVerifier("REQUIREMENT 1: SATISFIED")
    penalty, hard, opps = requirement_compliance_issues(
        client, [["cite at least 3 sources"]], "Prose with three citations." * 3
    )
    assert penalty == 0.0
    assert hard == []
    assert opps == []


def test_single_mandatory_unmet_still_hard_issue_not_opportunity() -> None:
    """Regression proof that OR-handling did not weaken real-miss detection: a
    genuinely single-mandatory (non-OR) requirement, unmet, is still a hard issue
    with a penalty — never demoted to an opportunity."""
    client = _ScriptedVerifier("REQUIREMENT 1: NOT_SATISFIED")
    penalty, hard, opps = requirement_compliance_issues(
        client, [["cite at least 3 sources"]], "prose with no citations " * 30
    )
    assert penalty == 1.0
    assert len(hard) == 1 and "cite at least 3 sources" in hard[0]
    assert opps == []


def test_verification_no_penalty_when_all_satisfied() -> None:
    client = _ScriptedVerifier(
        "REQUIREMENT 1: SATISFIED\nREQUIREMENT 2: SATISFIED"
    )
    penalty, hard, opps = requirement_compliance_issues(
        client, ["include code", "cite sources"], "a full report" * 10
    )
    assert penalty == 0.0
    assert hard == []
    assert opps == []


def test_verification_penalty_scale_matches_unsatisfied_group_fraction() -> None:
    client = _ScriptedVerifier(
        "REQUIREMENT 1: NOT_SATISFIED\n"
        "REQUIREMENT 2: NOT_SATISFIED\n"
        "REQUIREMENT 3: SATISFIED"
    )
    penalty, hard, opps = requirement_compliance_issues(
        client, ["a", "b", "c"], "artifact text here" * 10
    )
    assert penalty == round(2 / 3, 4)
    assert len(hard) == 2
    assert opps == []


def test_verification_empty_inputs_are_no_ops() -> None:
    client = _ScriptedVerifier("REQUIREMENT 1: NOT_SATISFIED")
    assert requirement_compliance_issues(None, ["a"], "art") == (0.0, [], [])
    assert requirement_compliance_issues(client, [], "art") == (0.0, [], [])
    assert requirement_compliance_issues(client, ["a"], "") == (0.0, [], [])
    assert client.n_calls == 0


def test_verification_fail_open_on_client_error() -> None:
    class _Boom:
        def chat(self, *a, **k):
            raise RuntimeError("down")

    assert requirement_compliance_issues(_Boom(), ["a"], "x" * 100) == (0.0, [], [])


def test_verification_fail_open_on_unparseable_reply() -> None:
    client = _ScriptedVerifier("I think the document looks pretty good overall.")
    assert requirement_compliance_issues(client, ["a"], "x" * 100) == (0.0, [], [])


def test_strict_mode_raises_instead_of_fail_open() -> None:
    """Bug 1 substrate: in ``strict=True`` mode the SAME fail-open paths that
    default to ``(0.0, [], [])`` instead raise ``ComplianceCheckUnavailable`` — so
    the opportunity-recount can tell "verified: nothing" from "could not verify"
    and never misread a silent failure as a real zero. Default (non-strict) callers
    are unaffected — the returns above still fail-open."""
    from studio.requirement_compliance import ComplianceCheckUnavailable

    class _Boom:
        def chat(self, *a, **k):
            raise RuntimeError("down")

    # 1) client error, 2) unparseable reply, 3) empty artifact — all raise under strict.
    for client, reqs, art in [
        (_Boom(), ["a"], "x" * 100),
        (_ScriptedVerifier("no verdicts here"), ["a"], "x" * 100),
        (_ScriptedVerifier("REQUIREMENT 1: NOT_SATISFIED"), ["a"], ""),
    ]:
        with pytest.raises(ComplianceCheckUnavailable):
            requirement_compliance_issues(client, reqs, art, strict=True)


def test_strict_mode_raises_on_partial_parse_of_or_branches() -> None:
    """Bug 3 (finer-grained variant of Bug 1): the verifier RESPONDS but omits the
    per-branch verdict line for SOME branch it was asked to verify. The whole reply
    is NOT empty (Bug 1's zero-verdict case is already handled), yet one OR-sibling
    was never actually checked. In strict mode this must raise — a silently-skipped
    branch is exactly the one the opportunity-recount would count, so an
    artificially-low (0) count would misread "never verified" as "satisfied"."""
    from studio.requirement_compliance import ComplianceCheckUnavailable

    # One OR group, TWO branches — the verifier answers branch 1 only, DROPS branch 2.
    client = _ScriptedVerifier("REQUIREMENT 1: SATISFIED")
    reqs = [["include example code", "include a design architecture"]]
    art = "A report with real code.\n```python\ngo()\n```\n" * 5

    # CONTROL (default strict=False): behavior is UNCHANGED — no raise. The dropped
    # branch is silently skipped per-group, the satisfied branch carries the OR
    # group, and opportunities is EMPTY. That empty result is the fabricated "zero
    # opportunities remaining" the exploit relies on — pinned here as exactly the
    # pre-fix behavior the strict raise now guards against (the strict call below
    # would ALSO have returned this same non-raising result before the fix).
    assert requirement_compliance_issues(client, reqs, art) == (0.0, [], [])

    # STRICT: the partial parse (1 of 2 branch verdicts) now raises instead of
    # returning that fabricated-empty result.
    with pytest.raises(ComplianceCheckUnavailable):
        requirement_compliance_issues(client, reqs, art, strict=True)


def test_verification_ignores_out_of_range_indices() -> None:
    """A verdict referencing a branch index that does not exist is dropped; only
    in-range verdicts count toward the group/penalty denominator."""
    client = _ScriptedVerifier(
        "REQUIREMENT 1: NOT_SATISFIED\nREQUIREMENT 5: NOT_SATISFIED"
    )
    penalty, hard, opps = requirement_compliance_issues(client, ["only one req"], "art" * 20)
    assert penalty == 1.0          # 1 of 1 in-range groups unsatisfied
    assert len(hard) == 1
    assert opps == []


# --- rubric penalty threading (pure/deterministic) -------------------------

def test_rubric_compliance_penalty_is_pure_and_subtracts() -> None:
    from studio.rubric import rubric_score

    text = "# Report\n\n" + ("word " * 2000)
    base = rubric_score(text)
    penalised = rubric_score(text, compliance_penalty=0.25)
    assert penalised == round(max(0.0, base - 0.25), 4)
    # no I/O, deterministic: same inputs → same output
    assert penalised == rubric_score(text, compliance_penalty=0.25)


def test_rubric_relevance_and_compliance_penalties_stack() -> None:
    from studio.rubric import rubric_score

    text = "# Report\n\n" + ("word " * 2000)
    base = rubric_score(text)
    both = rubric_score(text, relevance_penalty=0.1, compliance_penalty=0.2)
    assert both == round(max(0.0, base - 0.3), 4)


def test_scorecard_compliance_penalty_derates_total() -> None:
    from studio.rubric import rubric_scorecard_100

    text = "# Report\n\n## Findings\n\n" + ("word " * 2000)
    clean = rubric_scorecard_100(text)
    derated = rubric_scorecard_100(text, compliance_penalty=0.5)
    assert derated["score"] <= clean["score"]


def test_quality_opportunities_never_affect_the_rubric_penalty() -> None:
    """The score must be driven ONLY by hard misses. An OR group satisfied by one
    branch produces quality_opportunities but penalty 0.0 — so feeding that
    penalty to rubric_score leaves the score identical to the no-penalty base.
    This pins that opportunities are decorative for scoring, never a derate."""
    from studio.rubric import rubric_score

    client = _ScriptedVerifier("REQUIREMENT 1: SATISFIED\nREQUIREMENT 2: NOT_SATISFIED")
    penalty, hard, opps = requirement_compliance_issues(
        client, [["include code", "include architecture"]], "a code report " * 50
    )
    assert opps and not hard and penalty == 0.0  # opportunity present, no hard miss
    text = "# Report\n\n" + ("word " * 2000)
    # Opportunities exist, yet the penalty is 0 → the rubric score is unchanged.
    assert rubric_score(text, compliance_penalty=penalty) == rubric_score(text)


# --- real-model calibration (deselected by default via -m 'not integration') ---

def _gemma_client_or_skip():
    from studio.backends import build_chat_client, resolve_backend
    try:
        client = build_chat_client(resolve_backend({"profile": "gemma"}), on_usage=lambda _u: None)
        client.chat([{"role": "user", "content": "Answer with one word: OK"}])
        return client
    except Exception as exc:  # noqa: BLE001 — a down service is a skip, not a fail
        pytest.skip(f"local chat backend unavailable: {exc}")


@pytest.mark.integration
def test_extract_and_verify_picraft_real_task() -> None:
    """CALIBRATION (primary case): the real Pi/Craft task text extracts a
    code/architecture requirement, and verification against a real code-bearing
    artifact does NOT fabricate a miss.

    HONEST OUTCOME (documented in the WORKLOG): the task says "include example
    code OR design architecture" — an OR literally satisfied by the code the
    artifact already has. The generic checker therefore marks it SATISFIED and
    does NOT force a diagram. This is the correct generic behaviour, not a bug:
    forcing a diagram here would require a keyword-specific hack the design
    forbids. Diagram reliability only follows when a task actually MANDATES a
    diagram (an AND requirement)."""
    db = FIXTURES_ROOT / "task_runs.db"
    if not db.exists():
        pytest.skip("task_runs.db fixture not present")
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT requirement, result_text FROM task_runs "
        "WHERE lower(requirement) LIKE '%pi%craft%' AND length(result_text) > 500 "
        "ORDER BY score DESC LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("Pi/Craft fixture not present in task_runs.db")
    requirement, artifact = row
    client = _gemma_client_or_skip()

    reqs = extract_requirements(client, requirement)
    assert reqs, "extractor returned nothing for a task with explicit requirements"
    joined = " ".join(b for group in reqs for b in group).lower()
    assert ("code" in joined or "architecture" in joined), (
        f"extractor missed the explicit code/architecture requirement: {reqs}"
    )
    # The real artifact carries example code → the OR requirement is satisfied and
    # must NOT be a HARD miss (no fabricated diagram demand). If the model extracted
    # code/architecture as an OR clause, a missing diagram may surface as a
    # (non-blocking) quality opportunity — never a hard issue / penalty.
    penalty, hard, opps = requirement_compliance_issues(client, reqs, artifact)
    code_arch_hard = any(
        "code" in i.lower() or "architecture" in i.lower() for i in hard
    )
    assert not code_arch_hard, (
        f"code/architecture wrongly flagged as a HARD miss on a code-bearing "
        f"artifact (penalty={penalty}): hard={hard}"
    )


@pytest.mark.integration
def test_verify_citations_requirement_generalizes() -> None:
    """CALIBRATION (proof of generality): a DIFFERENT explicit requirement —
    "Include citations." on a real catalog-management task — is flagged when the
    artifact genuinely has no citations and NOT flagged when it does. This is the
    evidence the mechanism is not secretly diagram-specific.

    NOTE: the naive verifier prompt passed a citation-STRIPPED doc because
    parenthetical source attributions survived a crude strip; reading the model's
    quoted evidence showed it was honest, and evidence-grounding is what let the
    calibration converge. The negative fixture below removes URLs, markdown links,
    ALL parentheticals, and quoted excerpts to be genuinely citation-free."""
    db = FIXTURES_ROOT / "task_runs.db"
    if not db.exists():
        pytest.skip("task_runs.db fixture not present")
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT requirement, result_text FROM task_runs "
        "WHERE lower(requirement) LIKE '%catalog%include citations%' "
        "AND length(result_text) > 500 ORDER BY score DESC LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("catalog citations fixture not present in task_runs.db")
    requirement, cited = row
    client = _gemma_client_or_skip()

    reqs = extract_requirements(client, requirement)
    assert any("citation" in b.lower() for group in reqs for b in group), (
        f"extractor missed the explicit citations requirement: {reqs}"
    )
    # REAL-DATA POSITIVE: the real cited artifact must NOT false-flag citations.
    # This is the load-bearing generality proof — a DIFFERENT (non-diagram)
    # requirement, verified correctly on real data.
    _, hard_with, _ = requirement_compliance_issues(client, reqs, cited)
    assert not any("citation" in i.lower() for i in hard_with), (
        f"citations wrongly flagged on a cited artifact: {hard_with}"
    )
    # DECISIVE NEGATIVE (controlled input): the "flags when genuinely absent"
    # property. A real-data citation-free fixture proved impractical — every real
    # research report in task_runs.db is saturated with citation structures
    # (References sections, parenthetical source attributions), and the verifier
    # CORRECTLY finds them (it consistently quoted the real citations that crude
    # strips left behind — see WORKLOG calibration trace). So the absent-case is
    # pinned on an unambiguous plain-prose doc with zero citation forms.
    plain = (
        "# Catalog Management\n\n## Overview\n\n"
        "Agent loops and skills can live in local or remote catalogs. This report "
        "describes how they are organized and the operational risks involved. "
        "Local catalogs are simple to reason about. Remote catalogs add network "
        "dependencies and versioning concerns. Teams should weigh convenience "
        "against reliability when choosing where catalogs live.\n\n"
        "## Recommendations\n\nStart local, move remote when scale demands it.\n"
    )
    _, hard_no, _ = requirement_compliance_issues(client, ["Include citations."], plain)
    assert any("citation" in i.lower() for i in hard_no), (
        f"citations NOT flagged on an unambiguously citation-free doc: {hard_no}"
    )
