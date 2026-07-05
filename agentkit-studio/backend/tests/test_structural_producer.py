"""Tests for L0 (PLAN §8): the deterministic finalize-time structural producer.

Deterministic, no network: LLM calls are faked with literal strings (mirrors
test_diagram_render.py); acceptance is judged on structural validity only, never
score/weak-count — these tests prove that gate, not the rubric.
"""
from __future__ import annotations

from types import SimpleNamespace

import studio.structural_producer as sp

_REPORT_WITH_ARCH = (
    "# Agent Loops Report\n\n"
    "## Architecture\n\n"
    "The Planner builds a plan. The Executor runs tools. The Memory store "
    "persists state. The Scorer grades the artifact.\n\n"
    "## References\n\n- https://x.test/e\n"
)

_TS_SNIPPET = (
    "export function buildClient(config: Config): Client {\n"
    "  const client = new Client(config);\n"
    "  client.on(\"connect\", () => {\n"
    "    console.log(\"connected\");\n"
    "  });\n"
    "  return client;\n"
    "}\n"
    "\n"
    "export function closeClient(client: Client): void {\n"
    "  client.close();\n"
    "}\n"
)

_DIAGRAM_REPLY = (
    "COMPONENT: Planner | builds plan\n"
    "COMPONENT: Executor | runs tools\n"
    "COMPONENT: Memory | persists state\n"
    "COMPONENT: Scorer | grades output\n"
    "EDGE: Planner -> Executor\n"
    "EDGE: Executor -> Scorer\n"
)


def _client(text: str):
    return SimpleNamespace(chat=lambda messages, tools=None: SimpleNamespace(text=text))


def test_code_branch_grounds_from_evidence_and_places_under_designated_section(tmp_path):
    text = (
        "# Report\n\n## Example Code\n\n_(pending)_\n\n"
        "## References\n\n- https://real.example/doc\n"
    )
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "source-001.md").write_text(
        f"URL: https://good.example/src\n\n{_TS_SNIPPET}", encoding="utf-8"
    )

    out, stats = sp.produce_missing_structures(
        text,
        [["include example code"]],
        client=None,
        evidence_dir=evidence_dir,
        dyn_sections={"code": "Example Code"},
    )

    assert stats["code"] == "inserted"
    assert "```ts" in out
    assert "[source](https://good.example/src)" in out
    assert "https://real.example/doc" in out  # original citation preserved
    assert out.index("## Example Code") < out.index("```ts") < out.index("## References")


def test_diagram_branch_uses_a2_machinery_and_inserts_mermaid():
    out, stats = sp.produce_missing_structures(
        _REPORT_WITH_ARCH,
        [["include a design architecture diagram"]],
        client=_client(_DIAGRAM_REPLY),
        evidence_dir=None,
    )

    assert stats["diagram"] == "inserted"
    assert "```mermaid" in out
    assert out.index("## Architecture") < out.index("```mermaid") < out.index("## References")


def test_both_branches_already_satisfied_is_a_byte_identical_noop():
    text = (
        "# Report\n\n## Body\n\n```python\nprint('hi')\n```\n\n"
        "```mermaid\nflowchart TD\n    N0[\"X\"]\n```\n\n"
        "## References\n\n- https://x.test/e\n"
    )
    out, stats = sp.produce_missing_structures(
        text,
        [["include example code", "design architecture diagram"]],
        client=_client(_DIAGRAM_REPLY),
        evidence_dir=None,
    )
    assert out == text
    assert stats["code"] == "skipped"
    assert stats["diagram"] == "skipped"


def test_acceptance_gate_reverts_an_insertion_that_drops_a_url(monkeypatch):
    text = "# Report\n\n## Body\n\nSee https://real.example/doc for detail.\n"

    def _bad_producer(*_args, **_kwargs):
        return "# Report\n\n## Body\n\nNo citation left here at all.\n"

    monkeypatch.setattr(sp, "_produce_code", _bad_producer)

    out, stats = sp.produce_missing_structures(
        text,
        [["include example code"]],
        client=_client("```text\nirrelevant\n```"),
        evidence_dir=None,
    )

    assert out == text  # reverted — the citation would have been lost
    assert stats["code"] == "failed"
    assert stats["reason"]["code"] == "acceptance gate rejected insertion"


def test_prose_mentioning_code_words_is_not_mistaken_for_code(tmp_path):
    """Fix (HIGH review finding): plain English sentences that incidentally use
    'function'/'class'/'return'/'import'/'const' as ordinary words (plus one stray
    semicolon) must not be excerpted as a code block — the anchor/continuation
    heuristic alone is fooled by this; the density+hard-punctuation gate is not."""
    prose = (
        "The function of the ribosome is to synthesize proteins from messenger RNA.\n"
        "Biologists classify enzymes by function, and this class of proteins never "
        "ceases to amaze researchers.\n"
        "A return to first principles helps explain how cells regulate gene expression.\n"
        "The import of these findings for medicine cannot be overstated in research.\n"
        "Consider a simple const observation from field biology; small variations matter.\n"
        "This class of algorithms return approximate answers, much like biology does.\n"
        "Researchers often import methods from physics to model biological systems.\n"
        "The overall function here is purely descriptive, not computational at all.\n"
        "No import, class, or function keyword here changes this plain sentence.\n"
        "In conclusion, the ribosome's function remains central to molecular biology.\n"
    )
    text = "# Report\n\n## Example Code\n\n_(pending)_\n\n## References\n\n- https://real.example/doc\n"
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "source-001.md").write_text(
        f"URL: https://prose.example/doc\n\n{prose}", encoding="utf-8"
    )

    out, stats = sp.produce_missing_structures(
        text, [["include example code"]], client=None, evidence_dir=evidence_dir
    )

    assert stats["code"] != "inserted"
    assert "```" not in out
    assert out == text


def test_fail_open_with_no_evidence_and_no_client_is_a_noop():
    text = "# Report\n\n## Body\n\nSome prose, no code fence.\n"
    out, stats = sp.produce_missing_structures(
        text,
        [["include example code"]],
        client=None,
        evidence_dir=None,
    )
    assert out == text
    assert stats["code"] == "skipped"
