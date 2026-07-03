"""studio.relevance — narrow binary LLM relevance check for seeded sections.

Calibration story (see WORKLOG-research-report-generator-plan.md): embedding
cosine similarity was tried FIRST and REJECTED on real data — the
contaminated catalog-management content scored HIGHER (0.887) against the
WRONG (coding-agent) requirement than against its OWN (0.869), because both
requirements share templated research-report framing that saturates cosine
at every granularity (section, sentence, topic-phrase).

PROMPT calibration (real fixture, deployed gemma model, see module docstring
of studio/relevance.py): the winning prompt is a DYNAMIC-EXEMPLAR few-shot +
evidence-extraction judge (VERDICT: RELEVANT/IRRELEVANT) — 7/8 recall, 1/8
false-flag, with pure reference/bibliography sections skipped to remove the
one residual structural false-flag. The integration test below re-runs that
SAME real-data check against the ACTUAL deployed model (the `gemma` profile,
NOT the earlier-conflated `qwen` fallback).
"""
from __future__ import annotations

import pathlib
import re
import sqlite3

import pytest

from studio.relevance import relevance_issues

FIXTURES_ROOT = pathlib.Path(__file__).parent.parent / "tmp"


class _ScriptedClient:
    """Fake LLMClient: emits the VERDICT format by keyword — deterministic.

    For the SYNTHETIC unit tests only, to pin relevance_issues' own logic
    (aggregation, skip-short, references-skip, dynamic exemplar, fail-open).
    Records the last prompt so exemplar wiring can be asserted. The REAL
    calibration question — does an LLM classifier actually separate real
    same-domain contamination cosine could not — can only be answered by a
    real model; see the integration test below."""

    def __init__(self, irrelevant_keywords: tuple[str, ...]) -> None:
        self.irrelevant_keywords = irrelevant_keywords
        self.n_calls = 0
        self.total_tokens = 0
        self.last_prompt = ""

    def chat(self, messages, tools=None):
        from agentkit.types import ChatResult
        self.n_calls += 1
        self.total_tokens += 5
        self.last_prompt = str(messages[-1].get("content", ""))
        content = self.last_prompt.lower()
        if any(k in content for k in self.irrelevant_keywords):
            reply = "QUOTE: NONE\nVERDICT: IRRELEVANT"
        else:
            reply = "QUOTE: a real on-topic sentence.\nVERDICT: RELEVANT"
        return ChatResult(text=reply, total_tokens=5)


def test_relevance_issues_flags_synthetic_off_topic_section() -> None:
    client = _ScriptedClient(irrelevant_keywords=("cookie recipe",))
    sections = {
        "Executive Summary": (
            "This report examines SWE-Bench and how autonomous coding agents "
            "are benchmarked against real GitHub issues in production."
        ),
        "Recipe Notes": (
            "Cream the butter and sugar, then fold in chocolate chips for a "
            "classic cookie recipe baked at 350F for twelve minutes."
        ),
    }
    penalty, issues = relevance_issues(
        client, sections, "Write a report on benchmarking autonomous coding agents."
    )
    assert penalty == 0.5
    assert len(issues) == 1
    assert "Recipe Notes" in issues[0]
    assert "unrelated to the current task" in issues[0]


def test_relevance_issues_no_penalty_when_all_relevant() -> None:
    client = _ScriptedClient(irrelevant_keywords=("nothing-matches-xyz",))
    sections = {
        "A": "SWE-Bench Verified measures agent task success on real GitHub issues.",
        "B": "Pass@k and resolution rate are the primary metrics for coding agents.",
    }
    penalty, issues = relevance_issues(client, sections, "benchmarking coding agents")
    assert penalty == 0.0
    assert issues == []


def test_relevance_issues_skips_short_sections_without_flagging() -> None:
    """A stub/placeholder section is too short to meaningfully judge — skipped,
    not flagged (and not counted in the denominator)."""
    client = _ScriptedClient(irrelevant_keywords=("anything",))
    penalty, issues = relevance_issues(client, {"Stub": "_(pending)_"}, "some task")
    assert penalty == 0.0
    assert issues == []
    assert client.n_calls == 0


def test_relevance_issues_skips_references_section() -> None:
    """A pure references/bibliography section has no topical sentence to quote —
    every candidate prompt false-flags it. Skip by heading name (not counted,
    not flagged) rather than build a general classifier."""
    client = _ScriptedClient(irrelevant_keywords=("http",))  # would flag the URLs
    sections = {
        "Key Findings": "Autonomous coding agents resolve real GitHub issues on SWE-Bench.",
        "References": (
            "1. https://example.com/swe-bench\n"
            "2. https://example.com/agents\n"
            "3. https://example.com/benchmark"
        ),
    }
    penalty, issues = relevance_issues(client, sections, "benchmarking coding agents")
    assert penalty == 0.0        # References skipped; Key Findings judged RELEVANT
    assert issues == []
    assert client.n_calls == 1   # only Key Findings hit the model


def test_relevance_issues_uses_seed_topic_in_dynamic_exemplar() -> None:
    """When a cross-task seed's original topic is supplied, EXAMPLE A names that
    real subject; otherwise it falls back to the generic phrase."""
    client = _ScriptedClient(irrelevant_keywords=("nothing-matches-xyz",))
    section = {"A": "SWE-Bench measures coding-agent task success on GitHub issues." * 2}

    relevance_issues(
        client, section, "benchmarking coding agents",
        seed_topic="catalog management for local agent loops",
    )
    assert "catalog management for local agent loops" in client.last_prompt
    assert "EXAMPLE A" in client.last_prompt
    assert "VERDICT:" in client.last_prompt

    relevance_issues(client, section, "benchmarking coding agents")  # no seed_topic
    assert "a different specific subject in the same broad field" in client.last_prompt
    assert "catalog management for local agent loops" not in client.last_prompt


def test_relevance_issues_fail_open_on_client_error() -> None:
    """A single bad classification must never block the run — fail open."""
    class _Boom:
        def chat(self, *a, **k):
            raise RuntimeError("down")

    penalty, issues = relevance_issues(_Boom(), {"A": "x" * 100}, "some task")
    assert (penalty, issues) == (0.0, [])


def test_relevance_issues_fail_open_on_unparseable_reply() -> None:
    """A reply with no VERDICT line is unparseable → do NOT flag (fail-open)."""
    class _Garbage:
        def chat(self, *a, **k):
            from agentkit.types import ChatResult
            return ChatResult(text="I'm not sure, maybe?", total_tokens=1)

    penalty, issues = relevance_issues(_Garbage(), {"A": "x" * 100}, "some task")
    assert (penalty, issues) == (0.0, [])


def test_relevance_issues_empty_inputs_are_no_ops() -> None:
    client = _ScriptedClient(irrelevant_keywords=("x",))
    assert relevance_issues(None, {"A": "x" * 100}, "task") == (0.0, [])
    assert relevance_issues(client, {}, "task") == (0.0, [])
    assert relevance_issues(client, {"A": "x" * 100}, "") == (0.0, [])
    assert client.n_calls == 0


@pytest.mark.integration
def test_relevance_issues_flags_real_contaminated_seed() -> None:
    """CALIBRATION (the check that matters most): re-run the real
    contaminated-run fixtures that REFUTED embedding cosine against the
    binary LLM relevance judge — the DYNAMIC-EXEMPLAR few-shot + evidence
    prompt now wired into relevance_issues — on the ACTUAL deployed model.

    The catalog-management source content (task_hash 1697bb0a06df, the seed
    R10 carried — sim=0.957 — into session s_98742f3026ee's "benchmarking
    autonomous coding agents" task) scored HIGHER under cosine against the
    WRONG requirement (0.887) than against its OWN (0.869) — a signal
    pointing the wrong way at every granularity tried. Calibration on the
    deployed gemma model (`gemma-4-26B-A4B-it-heretic-4bit`) scored 7/8 recall
    on this exact data (the References section is skipped). This test confirms
    the wired prompt actually flags the MAJORITY of the contaminated sections.

    NOTE the model correction: an earlier version of this test used the `qwen`
    profile (Qwen2.5-Coder-14B, a VibeProxy fallback) and recorded a 1/8 recall
    figure. That is NOT the deployed model. This test uses the `gemma` profile.

    Requires a live local chat backend (oMLX :8000, the 'gemma' profile) —
    skips cleanly when unavailable, matching the existing live-service test
    convention. Deselected by default via the repo's `-m 'not integration'`
    addopts.
    """
    from studio.backends import build_chat_client, resolve_backend

    db_path = FIXTURES_ROOT / "task_runs.db"
    if not db_path.exists():
        pytest.skip("real fixture task_runs.db not present in this checkout")
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT requirement FROM task_runs WHERE session_id = ?",
        ("s_98742f3026ee",),
    ).fetchone()
    if row is None:
        pytest.skip("real fixture session s_98742f3026ee not present in task_runs.db")
    coding_requirement = row[0]

    prior = conn.execute(
        "SELECT requirement, result_text FROM task_runs WHERE task_hash = ? "
        "ORDER BY score DESC LIMIT 1",
        ("1697bb0a06df",),
    ).fetchone()
    if prior is None or not (prior[1] or "").strip():
        pytest.skip("real fixture catalog-management source run not present in task_runs.db")
    catalog_requirement, catalog_text = prior[0], prior[1]

    try:
        backend = resolve_backend({"profile": "gemma"})
        client = build_chat_client(backend, on_usage=lambda _u: None)
        client.chat([{
            "role": "user",
            "content": "Answer with exactly one word: YES or NO.\nping",
        }])
    except Exception as exc:  # noqa: BLE001 - a down service is a skip, not a fail
        pytest.skip(f"local chat backend unavailable: {exc}")

    from agentkit.artifacts.sections import split_sections
    sections = {
        re.sub(r"^#{1,6}\s*", "", h).strip(): b
        for h, b in split_sections(catalog_text)
        if h != "(intro)" and (b or "").strip()
    }
    assert sections, "fixture has no sections to classify"

    # The seed's OWN requirement is the real dynamic negative exemplar, exactly
    # as runner.py threads it when a cross-task R10 seed fires.
    penalty, issues = relevance_issues(
        client, sections, coding_requirement, seed_topic=catalog_requirement
    )

    # THE calibration assertion: this real catalog-management content — proven
    # UNDETECTABLE by embedding cosine (0.887 FOR the wrong requirement, 0.869
    # for its own — the wrong direction) — must be flagged for the MAJORITY of
    # its sections by the LLM classifier (gemma calibration: 7/8 recall, one
    # skipped References section). A weak/inert prompt would score ~0-1/8.
    assert penalty >= 0.5, (
        f"LLM relevance check flagged too few contaminated sections "
        f"(penalty={penalty}, issues={len(issues)}); the deployed-model calibration "
        f"is 7/8 recall — a penalty this low means the wired prompt regressed"
    )
    assert len(issues) >= 4


# ---------------------------------------------------------------------------
# Coarse whole-doc seed gate (studio.relevance.seed_doc_relevance)
# ---------------------------------------------------------------------------
from studio.relevance import seed_doc_relevance


class _VerdictClient:
    """Fake LLMClient emitting a RELATED/NOT_RELATED verdict (seed-gate format)."""

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.n_calls = 0
        self.last_prompt = ""

    def chat(self, messages, tools=None):
        from agentkit.types import ChatResult
        self.n_calls += 1
        self.last_prompt = str(messages[-1].get("content", ""))
        # model states summaries first, verdict on the last line
        return ChatResult(
            text=f"Doc is about X. Task is about Y.\n{self.verdict}",
            total_tokens=5,
        )


def test_seed_gate_keeps_seed_on_related_verdict() -> None:
    client = _VerdictClient("RELATED")
    assert seed_doc_relevance(client, "a" * 800, "some task about X") is True
    assert client.n_calls == 1


def test_seed_gate_drops_seed_on_not_related_verdict() -> None:
    client = _VerdictClient("NOT_RELATED")
    assert seed_doc_relevance(client, "a" * 800, "some task about X") is False
    assert client.n_calls == 1


def test_seed_gate_empty_inputs_skip_llm_call() -> None:
    client = _VerdictClient("NOT_RELATED")
    assert seed_doc_relevance(None, "seed", "task") is True
    assert seed_doc_relevance(client, "", "task") is True
    assert seed_doc_relevance(client, "seed", "") is True
    assert client.n_calls == 0


def test_seed_gate_fail_open_on_client_error() -> None:
    """A gate failure must never silently blank a doc — keep the seed."""
    class _Boom:
        def chat(self, *a, **k):
            raise RuntimeError("down")

    assert seed_doc_relevance(_Boom(), "a" * 800, "some task") is True


def test_seed_gate_fail_open_on_unparseable_reply() -> None:
    """No RELATED/NOT_RELATED token → unparseable → keep the seed (fail-open)."""
    class _Garbage:
        def chat(self, *a, **k):
            from agentkit.types import ChatResult
            return ChatResult(text="I'm not sure, hard to say.", total_tokens=1)

    assert seed_doc_relevance(_Garbage(), "a" * 800, "some task") is True
