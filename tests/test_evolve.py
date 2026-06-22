"""Tests for agentkit.evolve — the shared text-space optimizer + prompt target.

The load-bearing properties, all asserted without a network:
  - the keep/discard CONTROL is deterministic and model-free (the LLM is only
    the injected proposer);
  - a candidate is admitted ONLY via the gate — REJECT/ESCALATE are discarded,
    never auto-kept;
  - only strictly-improving accepted variants update the best + enter the archive;
  - the result reports a baseline-vs-best delta;
  - the RHO label-free mode keeps on self-preference while the gate still applies.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from agentkit.evolve import (
    OptimizeResult,
    Variant,
    evolve_prompt,
    evolve_prompt_rho,
    make_llm_proposer,
    optimize_text,
    self_preference,
)
from agentkit.gates import Gate
from agentkit.sandbox import SubprocessSandbox
from agentkit.types import ChatResult, Message


# ---------------------------------------------------------------------------
# Fakes — deterministic, no network.
# ---------------------------------------------------------------------------

def _count_scorer(text: str) -> float:
    """Score by how many 'good' tokens the text contains (capped at 1.0)."""
    return min(1.0, text.count("good") / 5.0)


def _append_proposer(best: str, _history: tuple[Variant, ...]) -> str | None:
    """A proposer that monotonically improves the artifact each epoch."""
    return best + " good"


def _flat_proposer(_best: str, _history: tuple[Variant, ...]) -> str | None:
    """A proposer whose candidate never beats the baseline."""
    return "no signal at all"


def _text_gate(tmp_path: Path, scorer=_count_scorer, client=None) -> Gate:
    """A gate whose evaluator agrees with the loop's scorer over proposal content."""
    return Gate(
        sandbox=SubprocessSandbox(),
        evaluator=lambda p: scorer(p["content"]),
        client=client,
        cwd=tmp_path,
    )


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_optimize_result_is_frozen():
    r = OptimizeResult(best="x", best_score=0.5, baseline_score=0.2, delta=0.3)
    with pytest.raises(FrozenInstanceError):
        r.best = "y"  # type: ignore[misc]


@pytest.mark.unit
def test_variant_is_frozen():
    v = Variant(text="t", score=0.5, epoch=1)
    with pytest.raises(FrozenInstanceError):
        v.score = 0.9  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Core keep/discard loop
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_monotonic_proposer_climbs_and_archives(tmp_path: Path):
    result = optimize_text(
        "seed",
        propose=_append_proposer,
        evaluate=_count_scorer,
        gate=_text_gate(tmp_path),
        baseline_score=_count_scorer("seed"),
        epochs=4,
    )
    assert result.best.count("good") == 4
    assert result.accepted == 4
    assert len(result.archive) == 4
    assert all(v.status == "accept" for v in result.archive)


@pytest.mark.unit
def test_delta_is_best_minus_baseline(tmp_path: Path):
    result = optimize_text(
        "seed",
        propose=_append_proposer,
        evaluate=_count_scorer,
        gate=_text_gate(tmp_path),
        baseline_score=_count_scorer("seed"),
        epochs=2,
    )
    assert result.delta == pytest.approx(result.best_score - result.baseline_score)
    assert result.delta > 0


@pytest.mark.unit
def test_non_improving_proposer_keeps_baseline(tmp_path: Path):
    result = optimize_text(
        "seed",
        propose=_flat_proposer,
        evaluate=_count_scorer,
        gate=_text_gate(tmp_path),
        baseline_score=_count_scorer("seed"),
        epochs=3,
    )
    assert result.accepted == 0
    assert result.best == "seed"
    assert result.delta == 0.0
    assert result.archive == ()


@pytest.mark.unit
def test_empty_proposal_epoch_is_skipped(tmp_path: Path):
    """A proposer returning None/empty does not crash and accepts nothing."""
    result = optimize_text(
        "seed good good good good good",  # already at score 1.0
        propose=lambda b, h: None,
        evaluate=_count_scorer,
        gate=_text_gate(tmp_path),
        baseline_score=1.0,
        epochs=3,
    )
    assert result.accepted == 0
    assert result.epochs == 3


@pytest.mark.unit
def test_evaluator_exception_discards_candidate(tmp_path: Path):
    def _boom(_text: str) -> float:
        raise ValueError("eval blew up")

    result = optimize_text(
        "seed",
        propose=_append_proposer,
        evaluate=_boom,
        gate=_text_gate(tmp_path),
        baseline_score=0.0,
        epochs=2,
    )
    assert result.accepted == 0
    assert result.best == "seed"


# ---------------------------------------------------------------------------
# Gate discipline — the LLM is a veto, not a vote.
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_side_effecting_candidate_is_escalated_not_kept(tmp_path: Path):
    """A proposer that emits side-effecting code is ESCALATED -> never kept."""
    def _danger(best: str, _h: tuple[Variant, ...]) -> str | None:
        return "import subprocess  # good good good good good"

    result = optimize_text(
        "seed",
        propose=_danger,
        evaluate=_count_scorer,
        gate=_text_gate(tmp_path),
        baseline_score=0.0,
        epochs=1,
        proposal_code=lambda text: text,  # route body through the gate's execute stage
    )
    assert result.accepted == 0
    assert result.best == "seed"


@pytest.mark.unit
def test_llm_safety_veto_blocks_high_scoring_candidate(tmp_path: Path):
    """Even a strictly-improving candidate is discarded if the safety LLM flags it."""
    class _Reject:
        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            return ChatResult(text='{"safe": false, "reason": "nope"}')

    result = optimize_text(
        "seed",
        propose=_append_proposer,
        evaluate=_count_scorer,
        gate=_text_gate(tmp_path, client=_Reject()),
        baseline_score=0.0,
        epochs=2,
    )
    assert result.accepted == 0
    assert result.best == "seed"


# ---------------------------------------------------------------------------
# LLM proposer seam (still no network — a fake client)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_make_llm_proposer_extracts_mutated_prompt(tmp_path: Path):
    class _MutatingClient:
        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            return ChatResult(
                text='{"mutation_note": "n", "mutated_prompt": "seed good good good good good"}'
            )

    proposer = make_llm_proposer(_MutatingClient())
    candidate = proposer("seed", ())
    assert candidate == "seed good good good good good"


@pytest.mark.unit
def test_make_llm_proposer_returns_none_on_unparseable(tmp_path: Path):
    class _Garbage:
        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            return ChatResult(text="I am a chatty model with no JSON here")

    proposer = make_llm_proposer(_Garbage())
    assert proposer("seed", ()) is None


@pytest.mark.unit
def test_make_llm_proposer_injects_weaknesses(tmp_path: Path):
    seen = {"user": ""}

    class _Capturing:
        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            for m in messages:
                if m.get("role") == "user":
                    seen["user"] = m.get("content", "")
            return ChatResult(text='{"mutated_prompt": "x good"}')

    proposer = make_llm_proposer(_Capturing(), weaknesses="forgets to cite sources")
    proposer("seed", ())
    assert "forgets to cite sources" in seen["user"]


# ---------------------------------------------------------------------------
# evolve_prompt target
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_evolve_prompt_uses_shared_core(tmp_path: Path):
    result = evolve_prompt(
        "base good good",
        propose=_append_proposer,
        evaluate=_count_scorer,
        gate=_text_gate(tmp_path),
        baseline_score=_count_scorer("base good good"),
        epochs=2,
    )
    assert isinstance(result, OptimizeResult)
    assert result.best_score >= result.baseline_score


# ---------------------------------------------------------------------------
# RHO label-free mode
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_self_preference_counts_net_wins():
    class _PrefersA:
        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            return ChatResult(text='{"winner": "A"}')

    net = self_preference(_PrefersA(), "cand", "base", judge_inputs=["t1", "t2", "t3"])
    assert net == 3


@pytest.mark.unit
def test_self_preference_tie_on_unparseable():
    class _Mush:
        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            return ChatResult(text="no json")

    assert self_preference(_Mush(), "a", "b", judge_inputs=["t1"]) == 0


@pytest.mark.unit
def test_evolve_prompt_rho_keeps_on_self_preference(tmp_path: Path):
    class _PrefersCandidate:
        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            # propose -> mutated prompt; judge -> winner A (the candidate)
            joined = " ".join(str(m.get("content", "")) for m in messages)
            if "Which is better" in joined:
                return ChatResult(text='{"winner": "A"}')
            return ChatResult(text='{"mutated_prompt": "improved artifact"}')

    client = _PrefersCandidate()
    result = evolve_prompt_rho(
        "baseline artifact",
        propose=make_llm_proposer(client),
        gate=Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 1.0, cwd=tmp_path),
        client=client,
        judge_inputs=["task one", "task two"],
        epochs=1,
    )
    assert result.best == "improved artifact"
    assert result.accepted == 1
    assert result.delta > 0
