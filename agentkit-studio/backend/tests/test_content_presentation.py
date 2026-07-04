"""Unit tests for the generalized presentation pass (TABLE + LIST + format-fix).

Pure/deterministic: clients are faked (judge keyed on SECTION HEADING; generator keyed on
'=== SECTION ==='). Everything asserted is code THIS module owns — table parse+grounding,
deterministic list extraction, format repair+idempotency, dispatch, ranking, telemetry.
"""
from __future__ import annotations

from studio import content_presentation as cp

# A comparison section whose attribute/entity/value words appear literally in the prose,
# so a grounded table can be built from it. "compared to" triggers the deterministic TABLE
# recommendation; Redis + Postgres give the >=2 components the ladder needs.
_SECTION = (
    "We compare two databases. Redis offers low latency and high volatility. Postgres "
    "offers strong durability and higher latency. Compared to Redis, Postgres trades "
    "latency for durability."
)
_GOOD_TABLE = (
    "| Attribute | Redis | Postgres |\n| --- | --- | --- |\n"
    "| Latency | low | higher |\n| Durability | volatility | strong |\n"
)


class _R:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeGen:
    """Generator: returns a fixed table for the table prompt ('=== SECTION ===')."""

    def __init__(self, table: str = _GOOD_TABLE) -> None:
        self.table = table

    def chat(self, messages, tools=None):
        return _R(self.table)


class _BoomGen:
    def chat(self, messages, tools=None):
        raise RuntimeError("generator down")


# ------------------------------------------------------------------------- table

def test_render_grounded_table_accepts_grounded_comparison() -> None:
    t = cp.render_grounded_table(_GOOD_TABLE, _SECTION)
    assert t is not None
    assert "Redis" in t and "Postgres" in t
    assert t.count("\n") == 3  # header + separator + 2 data rows


def test_render_grounded_table_rejects_ungrounded_rows() -> None:
    """Entities/values absent from the section → every data row dropped → None."""
    bogus = "| Attribute | Zorptron | Quixfoo |\n| --- | --- | --- |\n| Latency | blazing | glacial |\n"
    assert cp.render_grounded_table(bogus, _SECTION) is None


def test_render_grounded_table_rejects_generic_headers() -> None:
    generic = "| Item | Value |\n| --- | --- |\n| Redis | latency |\n| Postgres | durability |\n"
    assert cp.render_grounded_table(generic, _SECTION) is None


def test_render_grounded_table_rejects_too_few_columns() -> None:
    onecol = "| Redis |\n| --- |\n| latency |\n| durability |\n"
    assert cp.render_grounded_table(onecol, _SECTION) is None


def test_render_grounded_table_rejects_single_data_row() -> None:
    onerow = "| Attribute | Redis | Postgres |\n| --- | --- | --- |\n| Latency | low | higher |\n"
    assert cp.render_grounded_table(onerow, _SECTION) is None


def test_render_grounded_table_rejects_fabricated_headers() -> None:
    """codex [P2]: grounded DATA rows under INVENTED entity headers must still be rejected —
    the header carries the entity names, so ungrounded headers = a fabricated comparison."""
    fab = (
        "| Attribute | Zorptron | Quixfoo |\n| --- | --- | --- |\n"
        "| Latency | low | higher |\n| Durability | volatility | strong |\n"
    )
    assert cp.render_grounded_table(fab, _SECTION) is None


def test_render_grounded_table_needs_a_separator() -> None:
    no_sep = "| Attribute | Redis | Postgres |\n| Latency | low | higher |\n"
    assert cp.render_grounded_table(no_sep, _SECTION) is None


# -------------------------------------------------------------------------- list

def test_render_list_bulleted_from_comma_series() -> None:
    body = "The stack uses Python, JavaScript, Go, and Rust for its services."
    bl = cp.render_list(body, numbered=False)
    assert bl is not None and bl.startswith("- ")
    assert bl.count("\n") == 3  # 4 parallel items → 4 lines
    # Intro clause and trailing modifier stripped → clean items, not sentence fragments.
    assert bl == "- Python\n- JavaScript\n- Go\n- Rust"
    assert "stack" not in bl and "services" not in bl


def test_render_list_strips_intro_and_trailing_modifier() -> None:
    """codex [P2]: 'X uses A, B, and C for Y' → items A/B/C, not 'X uses A'/'C for Y'."""
    body = "The pipeline includes ingestion, ranking, and delivery for each request."
    bl = cp.render_list(body, numbered=False)
    assert bl == "- ingestion\n- ranking\n- delivery"


def test_render_list_bails_on_clause_fragments() -> None:
    """A comma series whose fragments are still clauses (not short parallel items) yields
    no list — a missing list beats a malformed one."""
    body = ("The system works because it caches aggressively, and it also retries failed "
            "calls, and it logs everything that happens during a run.")
    assert cp.render_list(body, numbered=False) is None


def test_render_list_numbered_from_sequence() -> None:
    body = "First, the planner drafts. Then, the executor runs. Finally, the scorer grades."
    nl = cp.render_list(body, numbered=True)
    assert nl is not None
    assert nl.startswith("1. ") and "\n2. " in nl and "\n3. " in nl
    assert "First" not in nl  # leading sequence marker stripped


def test_render_list_returns_none_below_three_items() -> None:
    assert cp.render_list("Python and Go are supported.", numbered=False) is None


# LLM-assisted grounded list fallback (mirrors the table path) --------------------

# Prose whose points are clause fragments (>5 words each) — deterministic render_list bails,
# so the LLM fallback is the only path. Every real point's tokens appear literally in it.
_LIST_SECTION = (
    "The framework provides caching to cut latency, retry logic to survive failures, "
    "and structured logging to make every run auditable."
)


def test_render_grounded_list_keeps_grounded_drops_ungrounded() -> None:
    """A model list whose items are token-grounded in the section survives; an item whose
    tokens are absent (a fabrication) is dropped — the same guard render_grounded_table uses."""
    assert cp.render_list(_LIST_SECTION, numbered=False) is None  # deterministic can't extract
    reply = (
        "- caching cuts latency\n- retry logic survives failures\n"
        "- structured logging audits runs\n- teleportation warp drive"
    )
    out = cp.render_grounded_list(reply, _LIST_SECTION, numbered=False)
    assert out is not None
    assert out.count("\n") == 2  # 3 grounded items kept, 1 ungrounded dropped
    assert "caching" in out and "retry" in out and "logging" in out
    assert "teleportation" not in out  # ungrounded item dropped (fabrication guard)


def test_render_grounded_list_drops_fabricated_clause_sharing_one_token() -> None:
    """codex [P2]: an LLM list item that shares ONE source token but bolts on a fabricated
    claim must be dropped — single-token overlap (_cell_grounded) is too weak for a clause.
    '- caching leaks customer secrets' shares only 'caching' with the section, so it is a
    fabrication and must not survive; the faithful items do."""
    reply = (
        "- caching cuts latency\n"
        "- caching leaks customer secrets\n"  # shares 'caching' only — fabricated claim
        "- structured logging audits runs"
    )
    out = cp.render_grounded_list(reply, _LIST_SECTION, numbered=False)
    assert out is not None
    assert "leaks customer secrets" not in out  # fabricated clause dropped
    assert "caching cuts latency" in out and "structured logging" in out


def test_render_grounded_table_allows_inferred_dimension_labels() -> None:
    """codex [P2]: a prose comparison ('LangGraph faster/pricier, CrewAI slower/cheaper')
    yields a table whose ROW-LABEL cells (Speed/Price) are inferred dimensions not literally
    in the prose. The majority-grounded row rule keeps such rows (data cells are grounded)
    so the broadened TABLE recommendation is not a no-op — but a fully/mostly fabricated row
    is still dropped."""
    sec = "LangGraph is faster but pricier, whereas CrewAI is slower but cheaper."
    good = ("| Attribute | LangGraph | CrewAI |\n| --- | --- | --- |\n"
            "| Speed | faster | slower |\n| Price | pricier | cheaper |")
    out = cp.render_grounded_table(good, sec)
    assert out is not None and "Speed" in out and "faster" in out  # inferred labels allowed

    # a fabricated row (no grounded data) drops -> only 1 real row survives -> None
    bad = ("| Attribute | LangGraph | CrewAI |\n| --- | --- | --- |\n"
           "| Speed | faster | slower |\n| Warp | teleports | vanishes |")
    assert cp.render_grounded_table(bad, sec) is None


def test_render_grounded_list_rejects_below_two_grounded() -> None:
    section = "The system uses caching to reduce latency."
    reply = "- caching reduces latency\n- teleportation warp drive\n- quantum flux capacitor"
    assert cp.render_grounded_list(reply, section, numbered=False) is None  # only 1 grounded


def test_plan_presentation_list_uses_llm_fallback_when_deterministic_bails() -> None:
    """End-to-end: a list-recommended section deterministic render_list can't parse falls
    through to the grounded LLM fallback, and the block lands in the section."""
    report = f"# R\n\n## Capabilities\n\n{_LIST_SECTION}\n\n## Intro\n\nAn overview paragraph.\n"

    class _ListGen:
        def chat(self, messages, tools=None):
            return _R(
                "- caching cuts latency\n- retry logic survives failures\n"
                "- structured logging audits runs"
            )

    new_text, heading, telem = cp.plan_presentation(None, _ListGen(), report)
    assert heading == "## Capabilities" and new_text is not None
    assert telem["kind"] == "list" and telem["satisfied"] == 1
    assert "- caching cuts latency" in new_text


# ------------------------------------------------------------------------ format

def test_fix_format_errors_closes_unclosed_fence() -> None:
    from studio.presentation_classifier import find_format_errors

    text = "# R\n\n## A\n\n```python\n\nprose that is never closed\n\n## B\n\ndone.\n"
    fixed = cp.fix_format_errors(text)
    assert find_format_errors(fixed) == []
    assert "## B" in fixed  # the following section is preserved, not swallowed


def test_fix_format_errors_removes_empty_fence() -> None:
    from studio.presentation_classifier import find_format_errors

    text = "# R\n\n## A\n\n```python\n```\n\nbody.\n"
    fixed = cp.fix_format_errors(text)
    assert find_format_errors(fixed) == []
    assert "```" not in fixed  # empty fence pair removed entirely


def test_fix_format_errors_leaves_wellformed_unchanged() -> None:
    text = "## A\n\n```py\nx = 1\n```\n"
    assert cp.fix_format_errors(text) == text


def test_fix_format_errors_is_idempotent() -> None:
    text = "# R\n\n## A\n\n```python\n\nswallowed\n\n## B\n\nend.\n"
    once = cp.fix_format_errors(text)
    assert cp.fix_format_errors(once) == once


# ---------------------------------------------------------------- plan_presentation

def test_plan_presentation_renders_table_for_comparison() -> None:
    report = f"# Report\n\n## Datastore Comparison\n\n{_SECTION}\n\n## Intro\n\nAn overview paragraph.\n"
    new_text, heading, telem = cp.plan_presentation(None, _FakeGen(), report)
    assert heading == "## Datastore Comparison"
    assert new_text is not None and "| Redis | Postgres |" in new_text
    assert "*Table:" in new_text  # table caption present
    # Block landed INSIDE the section, before the next heading.
    assert new_text.index("## Datastore Comparison") < new_text.index("| Attribute") < new_text.index("## Intro")
    assert telem == {
        "format_fixed": False, "form_debt_total": 1, "attempted": 1, "satisfied": 1, "kind": "table",
    }


def test_plan_presentation_format_only_returns_fixed_text() -> None:
    from studio.presentation_classifier import find_format_errors

    report = "# R\n\n## A\n\n```python\n\nswallowed prose\n\n## B\n\nend.\n"
    new_text, heading, telem = cp.plan_presentation(None, _FakeGen(), report)
    assert new_text is not None and heading is None
    assert find_format_errors(new_text) == []
    assert telem["format_fixed"] is True
    assert telem["form_debt_total"] == 0 and telem["satisfied"] == 0 and telem["kind"] is None


def test_plan_presentation_clean_report_returns_none() -> None:
    report = "# R\n\n## Why\n\nThis matters because grounding is the substrate of trust in a report.\n"
    new_text, heading, telem = cp.plan_presentation(None, _FakeGen(), report)
    assert new_text is None and heading is None
    assert telem == {
        "format_fixed": False, "form_debt_total": 0, "attempted": 0, "satisfied": 0, "kind": None,
    }


def test_plan_presentation_skips_diagram_improvements() -> None:
    """A section the classifier recommends as DIAGRAM is NOT handled here (owned by
    section_presentation) — no block is rendered and the generator is never called."""
    report = (
        "# R\n\n## Architecture\n\nThe Planner calls the Executor, which feeds the Memory "
        "and sends results to the Scorer.\n"
    )
    new_text, heading, telem = cp.plan_presentation(None, _BoomGen(), report)  # would raise if called
    assert new_text is None and heading is None
    assert telem["form_debt_total"] == 0 and telem["satisfied"] == 0


def test_plan_presentation_generator_failure_is_fail_closed() -> None:
    """A generator that raises → the improvement is skipped, no crash, satisfied stays 0."""
    report = f"# Report\n\n## Datastore Comparison\n\n{_SECTION}\n\n## Intro\n\nAn overview.\n"
    new_text, heading, telem = cp.plan_presentation(None, _BoomGen(), report)
    assert new_text is None and heading is None  # table failed, no format fix
    assert telem["attempted"] == 1 and telem["satisfied"] == 0 and telem["kind"] is None


def test_plan_presentation_ranks_table_above_list() -> None:
    """With both a list-worthy and a table-worthy section, the higher-value TABLE is chosen."""
    report = (
        "# R\n\n"
        f"## Comparison\n\n{_SECTION}\n\n"
        "## Languages\n\nThe stack uses Python, JavaScript, Go, and Rust for its services.\n"
    )
    _, heading, telem = cp.plan_presentation(None, _FakeGen(), report)
    assert heading == "## Comparison"       # TABLE outranks the bulleted list
    assert telem["form_debt_total"] == 2     # both counted as debt
    assert telem["satisfied"] == 1 and telem["kind"] == "table"
