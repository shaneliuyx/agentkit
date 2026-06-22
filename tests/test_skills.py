"""Tests for agentkit.skills — gate-verified skill library + SkillOpt target.

Asserted without a network (fake LLMClient + hashing Embedder):
  - propose -> verify -> save: a skill the gate did NOT accept is never saved;
  - side-effecting skill bodies ESCALATE and stay out of the library;
  - retrieval is semantic over the injected embedder, with a keyword fallback;
  - optimize_skill runs the shared optimizer over the body, deploys the best
    artifact to disk, and reports the baseline-vs-optimized delta.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from agentkit.gates import Gate, Outcome
from agentkit.sandbox import SubprocessSandbox
from agentkit.skills import Skill, SkillLibrary, optimize_skill
from agentkit.types import ChatResult, Message


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _HashEmbedder:
    """Deterministic bag-of-words hashing embedder (shared tokens -> similar)."""

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            out.append(vec)
        return out


class _SkillProposingClient:
    """A fake LLM that returns one skill as JSON."""

    def chat(self, messages: list[Message],
             tools: list[dict[str, Any]] | None = None) -> ChatResult:
        return ChatResult(text=json.dumps({
            "name": "binary_search",
            "description": "Find an element in a sorted list in logarithmic time.",
            "trigger": "searching a sorted collection efficiently",
            "steps": ["set lo and hi", "loop while lo <= hi", "compare midpoint"],
        }))


def _lib(tmp_path: Path) -> SkillLibrary:
    return SkillLibrary(_HashEmbedder(), tmp_path / "SKILLS")


# ---------------------------------------------------------------------------
# Skill value type
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_skill_is_frozen():
    s = Skill(name="n", description="d", body="b")
    with pytest.raises(FrozenInstanceError):
        s.body = "x"  # type: ignore[misc]


@pytest.mark.unit
def test_skill_roundtrips_through_dict():
    s = Skill(name="n", description="d", body="b", trigger="t", eval_score=0.7)
    restored = Skill.from_dict(s.to_dict())
    assert restored == s


@pytest.mark.unit
def test_with_body_returns_new_skill():
    s = Skill(name="n", description="d", body="old")
    s2 = s.with_body("new", eval_score=0.9)
    assert s.body == "old"  # original unchanged (immutable)
    assert s2.body == "new" and s2.eval_score == 0.9


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_propose_extracts_skill_from_trajectory(tmp_path: Path):
    lib = _lib(tmp_path)
    skill = lib.propose(_SkillProposingClient(), "agent solved a sorted-search task")
    assert skill is not None
    assert skill.name == "binary_search"
    assert "set lo and hi" in skill.body


@pytest.mark.unit
def test_propose_returns_none_on_no_skill(tmp_path: Path):
    class _NoneClient:
        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            return ChatResult(text='{"name": "none"}')

    lib = _lib(tmp_path)
    assert lib.propose(_NoneClient(), "nothing reusable here") is None


@pytest.mark.unit
def test_propose_returns_none_on_garbage(tmp_path: Path):
    class _Garbage:
        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            return ChatResult(text="not json at all")

    assert _lib(tmp_path).propose(_Garbage(), "x") is None


# ---------------------------------------------------------------------------
# add — propose -> verify -> save discipline
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_add_saves_when_gate_accepts(tmp_path: Path):
    lib = _lib(tmp_path)
    skill = Skill(name="ok_skill", description="d", body="print('ok')")
    gate = Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9, cwd=tmp_path)
    verdict, path = lib.add(skill, gate=gate, baseline_score=0.5)
    assert verdict.status is Outcome.ACCEPT
    assert path is not None and path.exists()
    assert "ok_skill" in lib.list()


@pytest.mark.unit
def test_add_does_not_save_on_regression(tmp_path: Path):
    lib = _lib(tmp_path)
    skill = Skill(name="weak", description="d", body="print('ok')")
    gate = Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.1, cwd=tmp_path)
    verdict, path = lib.add(skill, gate=gate, baseline_score=0.5)
    assert verdict.status is Outcome.REJECT
    assert path is None
    assert "weak" not in lib.list()


@pytest.mark.unit
def test_add_escalates_side_effecting_skill(tmp_path: Path):
    lib = _lib(tmp_path)
    danger = Skill(name="reaper", description="x", body="import subprocess")
    gate = Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9, cwd=tmp_path)
    verdict, path = lib.add(danger, gate=gate, baseline_score=0.0)
    assert verdict.status is Outcome.ESCALATE
    assert path is None
    assert "reaper" not in lib.list()


@pytest.mark.unit
def test_saved_skill_persists_eval_score_from_gate(tmp_path: Path):
    lib = _lib(tmp_path)
    skill = Skill(name="scored", description="d", body="print('ok')")
    gate = Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.8, cwd=tmp_path)
    lib.add(skill, gate=gate, baseline_score=0.1)
    loaded = lib.load("scored")
    assert loaded is not None and loaded.eval_score == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# save / load / list
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_save_writes_paired_json_and_md(tmp_path: Path):
    lib = _lib(tmp_path)
    skill = Skill(name="paired", description="d", body="step one", trigger="t")
    json_path = lib.save(skill)
    assert json_path.exists()
    assert (lib.directory / "paired.md").exists()
    assert "# Skill: paired" in (lib.directory / "paired.md").read_text()


@pytest.mark.unit
def test_load_missing_returns_none(tmp_path: Path):
    assert _lib(tmp_path).load("ghost") is None


# ---------------------------------------------------------------------------
# retrieve — semantic, with keyword fallback
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_retrieve_ranks_by_semantic_similarity(tmp_path: Path):
    lib = _lib(tmp_path)
    lib.save(Skill(name="search", description="binary search sorted array",
                   trigger="searching a sorted array", body="b"))
    lib.save(Skill(name="cook", description="boil pasta water salt",
                   trigger="cooking pasta dinner", body="b"))
    hits = lib.retrieve("how do I search a sorted array efficiently", k=2)
    assert hits[0].name == "search"


@pytest.mark.unit
def test_retrieve_empty_library_returns_empty(tmp_path: Path):
    assert _lib(tmp_path).retrieve("anything", k=3) == []


@pytest.mark.unit
def test_retrieve_falls_back_to_keyword_when_embedder_fails(tmp_path: Path):
    class _BrokenEmbedder:
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("embedder down")

    lib = SkillLibrary(_BrokenEmbedder(), tmp_path / "SKILLS")
    lib.save(Skill(name="parser", description="parse json payloads",
                   trigger="parsing structured data", body="b"))
    hits = lib.retrieve("parse", k=3)
    assert hits and hits[0].name == "parser"


# ---------------------------------------------------------------------------
# optimize_skill — SkillOpt loop on the gate
# ---------------------------------------------------------------------------

def _step_scorer(body: str) -> float:
    return min(1.0, body.count("step") / 3.0)


def _grow_proposer(best: str, _history) -> str | None:  # type: ignore[no-untyped-def]
    return best.replace("print('step", "print('step step")


@pytest.mark.unit
def test_optimize_skill_deploys_best_and_reports_delta(tmp_path: Path):
    seed = Skill(name="grow", description="d", body="print('step')")
    out = tmp_path / "DEPLOY"
    gate = Gate(
        sandbox=SubprocessSandbox(),
        evaluator=lambda p: _step_scorer(p["content"]),
        cwd=tmp_path,
    )
    best, result = optimize_skill(
        seed,
        propose=_grow_proposer,
        evaluate=_step_scorer,
        gate=gate,
        baseline_score=_step_scorer(seed.body),
        epochs=3,
        out_dir=out,
    )
    assert (out / "grow.json").exists()
    assert (out / "grow.md").exists()
    assert best.eval_score == pytest.approx(result.best_score)
    assert result.delta >= 0.0
    assert result.best_score >= result.baseline_score


@pytest.mark.unit
def test_optimize_skill_no_improvement_keeps_baseline_body(tmp_path: Path):
    seed = Skill(name="flat", description="d", body="print('step step step')")
    out = tmp_path / "DEPLOY"
    gate = Gate(
        sandbox=SubprocessSandbox(),
        evaluator=lambda p: _step_scorer(p["content"]),
        cwd=tmp_path,
    )
    best, result = optimize_skill(
        seed,
        propose=lambda b, h: "print('nothing useful')",
        evaluate=_step_scorer,
        gate=gate,
        baseline_score=_step_scorer(seed.body),
        epochs=2,
        out_dir=out,
    )
    assert result.accepted == 0
    assert best.body == seed.body
    assert result.delta == 0.0
