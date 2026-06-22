"""Tests for agentkit.gates — the LEARN admission gate (deterministic).

The load-bearing invariant: the gate is deterministic and NOT overridable by
the LLM. The injected safety LLM can only ADD a rejection, never grant an
acceptance — so a proposal the LLM "approves" but that fails regression is
still REJECTED. Each pipeline stage is also tested in isolation, plus the
ESCALATE path for side-effecting proposals.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from agentkit.gates import (
    Gate,
    Outcome,
    Verdict,
    run_gate,
)
from agentkit.sandbox import SubprocessSandbox
from agentkit.types import ChatResult, Message


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _ApprovingClient:
    """A safety LLM that always says the proposal is safe."""

    def chat(self, messages: list[Message],
             tools: list[dict[str, Any]] | None = None) -> ChatResult:
        return ChatResult(text='{"safe": true, "reason": "looks fine"}')


class _RejectingClient:
    """A safety LLM that always flags the proposal as unsafe."""

    def chat(self, messages: list[Message],
             tools: list[dict[str, Any]] | None = None) -> ChatResult:
        return ChatResult(text='{"safe": false, "reason": "dangerous"}')


def _good_evaluator(_proposal: dict[str, Any]) -> float:
    return 0.9


def _bad_evaluator(_proposal: dict[str, Any]) -> float:
    return 0.1


# A proposal whose code runs cleanly in the sandbox.
_RUNNABLE = {"type": "skill", "code": "print('ok')"}
# A proposal whose code is syntactically broken.
_BROKEN = {"type": "skill", "code": "def f(:\n  pass"}


def _gate(tmp_path: Path, evaluator=_good_evaluator, client=None) -> Gate:
    return Gate(sandbox=SubprocessSandbox(), evaluator=evaluator, client=client, cwd=tmp_path)


# ---------------------------------------------------------------------------
# Verdict value type
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_verdict_is_frozen():
    v = Verdict(status=Outcome.ACCEPT, reason="ok", stage="delta")
    assert v.status is Outcome.ACCEPT
    with pytest.raises(FrozenInstanceError):
        v.reason = "x"  # type: ignore[misc]


@pytest.mark.unit
def test_outcome_enum_has_three_states():
    assert {o for o in Outcome} == {Outcome.ACCEPT, Outcome.REJECT, Outcome.ESCALATE}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_runnable_improving_proposal_is_accepted(tmp_path: Path):
    v = _gate(tmp_path).run_gate(_RUNNABLE, baseline_score=0.5)
    assert v.status is Outcome.ACCEPT


@pytest.mark.unit
def test_run_gate_convenience_function(tmp_path: Path):
    v = run_gate(
        _RUNNABLE,
        baseline_score=0.5,
        sandbox=SubprocessSandbox(),
        evaluator=_good_evaluator,
        cwd=tmp_path,
    )
    assert v.status is Outcome.ACCEPT


# ---------------------------------------------------------------------------
# Stage 1: syntax
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_syntactically_broken_proposal_is_rejected_at_syntax(tmp_path: Path):
    v = _gate(tmp_path).run_gate(_BROKEN, baseline_score=0.0)
    assert v.status is Outcome.REJECT
    assert v.stage == "syntax"


@pytest.mark.unit
def test_empty_proposal_is_rejected_at_syntax(tmp_path: Path):
    v = _gate(tmp_path).run_gate({}, baseline_score=0.0)
    assert v.status is Outcome.REJECT
    assert v.stage == "syntax"


# ---------------------------------------------------------------------------
# Stage 2: sandbox-execute
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_proposal_that_crashes_in_sandbox_is_rejected_at_execute(tmp_path: Path):
    crashes = {"type": "skill", "code": "raise RuntimeError('boom')"}
    v = _gate(tmp_path).run_gate(crashes, baseline_score=0.0)
    assert v.status is Outcome.REJECT
    assert v.stage == "execute"


# ---------------------------------------------------------------------------
# Stage 3: regression
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_proposal_below_baseline_is_rejected_at_regression(tmp_path: Path):
    v = _gate(tmp_path, evaluator=_bad_evaluator).run_gate(_RUNNABLE, baseline_score=0.5)
    assert v.status is Outcome.REJECT
    assert v.stage == "regression"


@pytest.mark.unit
def test_tie_with_baseline_is_rejected_at_regression(tmp_path: Path):
    v = _gate(tmp_path, evaluator=lambda p: 0.5).run_gate(_RUNNABLE, baseline_score=0.5)
    assert v.status is Outcome.REJECT
    assert v.stage == "regression"


# ---------------------------------------------------------------------------
# Stage 4: safety (LLM) — can only ADD a rejection
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_llm_flag_hard_rejects_even_when_score_is_high(tmp_path: Path):
    v = _gate(tmp_path, client=_RejectingClient()).run_gate(_RUNNABLE, baseline_score=0.5)
    assert v.status is Outcome.REJECT
    assert v.stage == "safety"


@pytest.mark.unit
def test_llm_approval_cannot_rescue_a_regression(tmp_path: Path):
    """THE INVARIANT: LLM 'approve' never grants acceptance over a hard failure."""
    v = _gate(tmp_path, evaluator=_bad_evaluator, client=_ApprovingClient()).run_gate(
        _RUNNABLE, baseline_score=0.5
    )
    assert v.status is Outcome.REJECT
    assert v.stage == "regression"  # failed BEFORE the LLM was even consulted


@pytest.mark.unit
def test_safety_runs_only_after_deterministic_stages_pass(tmp_path: Path):
    """The LLM is a veto, not a vote: a clean proposal it approves is accepted."""
    v = _gate(tmp_path, client=_ApprovingClient()).run_gate(_RUNNABLE, baseline_score=0.5)
    assert v.status is Outcome.ACCEPT


# ---------------------------------------------------------------------------
# Stage 5: delta
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_improvement_too_small_is_rejected_at_delta(tmp_path: Path):
    # 0.51 over 0.50 beats baseline (passes regression) but is below min_delta.
    v = _gate(tmp_path, evaluator=lambda p: 0.51).run_gate(
        _RUNNABLE, baseline_score=0.5, min_delta=0.05
    )
    assert v.status is Outcome.REJECT
    assert v.stage == "delta"


# ---------------------------------------------------------------------------
# ESCALATE path: side-effecting / ambiguous proposals stop for a human
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_side_effecting_proposal_escalates(tmp_path: Path):
    side_effecting = {"type": "tool", "code": "import subprocess  # spawns processes"}
    v = _gate(tmp_path).run_gate(side_effecting, baseline_score=0.0)
    assert v.status is Outcome.ESCALATE
    assert v.stage == "containment"
