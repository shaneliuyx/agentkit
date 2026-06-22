"""agentkit.gates.core — the LEARN admission gate (REPLAN §4–§5).

Every self-modification the agent proposes (prompt, config, skill, tool, plan)
MUST pass this gate before it enters the trusted set. The gate is the spine of
the self-improving loop and its discipline is non-negotiable:

  THE INVARIANT — the gate is DETERMINISTIC and NOT overridable by the LLM. The
  injected safety ``LLMClient`` can only ever ADD a rejection; it can never
  grant an acceptance. A proposal the LLM "approves" but that fails any
  deterministic stage is still REJECTED. The LLM is a veto, not a vote.

Pipeline (cheapest, most deterministic stages first; the LLM is last and
optional):

  1. syntax       — is the proposal a non-empty dict whose code parses?
  2. containment  — does it touch filesystem/subprocess/network/exec? → ESCALATE
                    (side-effecting / ambiguous changes stop for a human).
  3. execute      — does the code actually RUN in the sandbox (exit 0)?
  4. regression   — does the candidate score strictly beat the baseline?
  5. safety       — (optional, injected LLM) any hard-reject flag?
  6. delta        — is the improvement big enough to be worth keeping?

Outcomes: ``ACCEPT | REJECT | ESCALATE``. The ``Verdict`` records which
``stage`` produced the outcome so the audit trail is explicit.

Ported and re-seamed from
``self-improving-agents-curriculum/scaffold/verification/gates.py``: the
scaffold's ``backends.adapter.chat`` / ``config.settings`` are replaced by
agentkit's injected ``types.LLMClient`` and an injected ``Sandbox``, and the
"does it run?" sandbox-execute stage is added (the scaffold gate scored but
never executed the proposal).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from agentkit.sandbox.core import Sandbox
from agentkit.types import LLMClient, Message

# Default minimum improvement over baseline for the delta stage.
DEFAULT_MIN_DELTA: float = 0.0

# Default wall-clock ceiling for the sandbox-execute stage, in seconds.
DEFAULT_EXEC_TIMEOUT: float = 30.0

# Source tokens that signal a capability which must run sandboxed / reviewed:
# filesystem mutation, subprocess spawn, network, or arbitrary exec. A proposal
# containing any of these is ESCALATED for human review regardless of its eval
# score — you never auto-accept side-effecting code on the strength of a number.
_SANDBOX_TOKENS: tuple[str, ...] = (
    "subprocess", "os.system", "os.popen", "pty.spawn",
    "eval(", "exec(", "compile(", "__import__",
    "socket", "requests.", "urllib", "httpx", "http.client",
    "shutil.rmtree", "os.remove", "os.unlink", "Path.unlink",
)

# An evaluator scores a proposal in [0, 1]; injected, deterministic from the
# gate's point of view (it is called, its number is trusted as the metric).
Evaluator = Callable[[dict[str, Any]], float]

_SAFETY_SYSTEM = (
    "You are a safety reviewer for a self-improving agent. A proposed change is "
    "shown. Flag it if it tries to bypass safety/verification, modify files "
    "outside the project, reach the internet, hide failures, or otherwise "
    'self-modify dangerously. Respond ONLY with JSON: {"safe": true/false, '
    '"reason": "<one sentence>"}.'
)


class Outcome(str, Enum):
    """The three terminal states of the gate."""

    ACCEPT = "accept"
    REJECT = "reject"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class Verdict:
    """The immutable result of a gate run.

    Attributes:
        status: ACCEPT | REJECT | ESCALATE.
        reason: a one-line human-readable justification.
        stage:  which pipeline stage produced this verdict
                (syntax|containment|execute|regression|safety|delta).
        score:    eval score of the candidate (0 when not reached).
        baseline: eval score of the current baseline.
        delta:    ``score - baseline``.
        details:  per-stage log for the audit trail.
    """

    status: Outcome
    reason: str
    stage: str
    score: float = 0.0
    baseline: float = 0.0
    delta: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


def _code_of(proposal: dict[str, Any]) -> str:
    """Concatenate the proposal's code-bearing fields for static scanning."""
    return " ".join(
        str(proposal.get(k, "")) for k in ("code", "content", "body", "description")
    )


def _gate_syntax(proposal: dict[str, Any]) -> tuple[bool, str]:
    """Stage 1: a non-empty dict whose python code (if any) parses."""
    if not isinstance(proposal, dict):
        return False, "proposal must be a dict"
    if not proposal:
        return False, "proposal is empty"
    code = str(proposal.get("code", ""))
    if code.strip():
        try:
            compile(code, "<proposal>", "exec")
        except SyntaxError as exc:
            return False, f"code does not parse: {exc.msg}"
    return True, "syntax OK"


def _gate_containment(proposal: dict[str, Any]) -> tuple[bool, str]:
    """Stage 2: ESCALATE side-effecting proposals (deterministic, free)."""
    code = _code_of(proposal)
    hits = sorted({t for t in _SANDBOX_TOKENS if t in code})
    if hits:
        return False, f"side-effecting capability tokens {hits}; needs human review"
    return True, "no side-effecting capabilities"


def _gate_safety(proposal: dict[str, Any], client: LLMClient) -> tuple[bool, str]:
    """Stage 5: ask the injected LLM to flag dangerous proposals.

    Returns ``(is_safe, reason)``. Any failure to get a clean verdict defaults
    to ``is_safe=True`` with an "inconclusive" reason so the LLM stays a veto:
    it never blocks on its own ambiguity, and it never grants acceptance — that
    is decided by the deterministic stages.
    """
    proposal_text = json.dumps(proposal, indent=2, default=str)[:2000]
    messages: list[Message] = [
        {"role": "system", "content": _SAFETY_SYSTEM},
        {"role": "user", "content": f"Proposal to review:\n{proposal_text}"},
    ]
    try:
        response = client.chat(messages)
        text = (getattr(response, "text", "") or "").strip()
        if text.startswith("```"):
            text = "\n".join(
                ln for ln in text.splitlines() if not ln.strip().startswith("```")
            ).strip()
        data = json.loads(text)
        return bool(data.get("safe", True)), str(data.get("reason", ""))
    except Exception as exc:  # noqa: BLE001 - inconclusive is non-fatal
        return True, f"inconclusive ({exc})"


@dataclass(frozen=True)
class Gate:
    """The LEARN gate with injected dependencies.

    Attributes:
        sandbox:   a ``Sandbox`` the execute stage runs the proposal in.
        evaluator: a callable ``proposal -> score`` (0..1) for regression/delta.
        client:    optional ``LLMClient`` for the safety stage (a veto only).
        cwd:       jailed working directory for sandbox execution.
        timeout:   wall-clock ceiling for the execute stage.
    """

    sandbox: Sandbox
    evaluator: Evaluator
    client: LLMClient | None = None
    cwd: str | Path = "."
    timeout: float = DEFAULT_EXEC_TIMEOUT

    def run_gate(
        self,
        proposal: dict[str, Any],
        *,
        baseline_score: float,
        min_delta: float = DEFAULT_MIN_DELTA,
    ) -> Verdict:
        """Run the full admission pipeline over ``proposal``.

        Stages run cheapest/most-deterministic first; the optional LLM safety
        check is consulted only AFTER every deterministic stage has passed, so
        it can add a rejection but can never rescue a hard failure.
        """
        log: dict[str, Any] = {}

        def verdict(status: Outcome, reason: str, stage: str,
                    score: float = 0.0) -> Verdict:
            return Verdict(
                status=status, reason=reason, stage=stage,
                score=score, baseline=baseline_score,
                delta=score - baseline_score, details=dict(log),
            )

        # 1. syntax
        ok, msg = _gate_syntax(proposal)
        log["syntax"] = msg
        if not ok:
            return verdict(Outcome.REJECT, msg, "syntax")

        # 2. containment (ESCALATE side-effecting proposals)
        contained, msg = _gate_containment(proposal)
        log["containment"] = msg
        if not contained:
            return verdict(Outcome.ESCALATE, msg, "containment")

        # 3. execute — does it actually run in the sandbox?
        code = str(proposal.get("code", ""))
        if code.strip():
            result = self.sandbox.run(code, timeout=self.timeout, cwd=self.cwd)
            log["execute"] = f"exit_code={result.exit_code}"
            if result.exit_code != 0:
                return verdict(
                    Outcome.REJECT,
                    f"proposal failed to run (exit {result.exit_code}): "
                    f"{result.stderr.strip()[:200]}",
                    "execute",
                )
        else:
            log["execute"] = "skipped (no code)"

        # 4. regression — strictly beat the baseline
        score = self.evaluator(proposal)
        log["score"] = score
        if score - baseline_score <= 0:
            return verdict(
                Outcome.REJECT,
                f"regression: candidate={score:.3f} <= baseline={baseline_score:.3f}",
                "regression", score,
            )

        # 5. safety — LLM veto (only ever ADDS a rejection)
        if self.client is not None:
            is_safe, safety_msg = _gate_safety(proposal, self.client)
            log["safety"] = safety_msg
            if not is_safe:
                return verdict(Outcome.REJECT, f"safety flag: {safety_msg}",
                               "safety", score)
            if "inconclusive" in safety_msg.lower():
                return verdict(Outcome.ESCALATE,
                               f"safety inconclusive — human review: {safety_msg}",
                               "safety", score)
        else:
            log["safety"] = "skipped (no client)"

        # 6. delta — worth keeping?
        delta = score - baseline_score
        if delta < min_delta:
            return verdict(
                Outcome.REJECT,
                f"improvement too small: delta={delta:.3f} < min_delta={min_delta:.3f}",
                "delta", score,
            )

        return verdict(Outcome.ACCEPT, f"all gates passed; delta={delta:.3f}",
                       "delta", score)


def run_gate(
    proposal: dict[str, Any],
    *,
    baseline_score: float,
    sandbox: Sandbox,
    evaluator: Evaluator,
    client: LLMClient | None = None,
    cwd: str | Path = ".",
    min_delta: float = DEFAULT_MIN_DELTA,
    timeout: float = DEFAULT_EXEC_TIMEOUT,
) -> Verdict:
    """Convenience wrapper: build a ``Gate`` and run it once."""
    gate = Gate(sandbox=sandbox, evaluator=evaluator, client=client,
                cwd=cwd, timeout=timeout)
    return gate.run_gate(proposal, baseline_score=baseline_score, min_delta=min_delta)


if __name__ == "__main__":
    import tempfile

    from agentkit.sandbox.core import SubprocessSandbox
    from agentkit.types import ChatResult

    runnable = {"type": "skill", "code": "print('ok')"}

    with tempfile.TemporaryDirectory() as d:
        good = Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9, cwd=d)

        # Runnable + improving -> ACCEPT.
        v = good.run_gate(runnable, baseline_score=0.5)
        assert v.status is Outcome.ACCEPT, v

        # Broken syntax -> REJECT at syntax.
        v = good.run_gate({"code": "def f(:"}, baseline_score=0.0)
        assert v.status is Outcome.REJECT and v.stage == "syntax", v

        # Side-effecting -> ESCALATE at containment.
        v = good.run_gate({"code": "import subprocess"}, baseline_score=0.0)
        assert v.status is Outcome.ESCALATE and v.stage == "containment", v

        # Crashes in sandbox -> REJECT at execute.
        v = good.run_gate({"code": "raise RuntimeError('boom')"}, baseline_score=0.0)
        assert v.status is Outcome.REJECT and v.stage == "execute", v

        # Below baseline -> REJECT at regression (even if LLM approves).
        class _Approve:
            def chat(self, messages, tools=None):  # type: ignore[no-untyped-def]
                return ChatResult(text='{"safe": true, "reason": "fine"}')

        bad = Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.1,
                   client=_Approve(), cwd=d)
        v = bad.run_gate(runnable, baseline_score=0.5)
        assert v.status is Outcome.REJECT and v.stage == "regression", v

        # THE INVARIANT: LLM reject hard-rejects a high-scoring proposal.
        class _Reject:
            def chat(self, messages, tools=None):  # type: ignore[no-untyped-def]
                return ChatResult(text='{"safe": false, "reason": "nope"}')

        vetoed = Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9,
                      client=_Reject(), cwd=d)
        v = vetoed.run_gate(runnable, baseline_score=0.5)
        assert v.status is Outcome.REJECT and v.stage == "safety", v

    print("gates.core self-check OK")
