"""agentkit.loop.goal — LoopGoal: a verifiable stop condition spec.

The key insight from loop-engineering research: stall-based stopping (N stale
rounds) detects effort plateau, not goal achievement. LoopGoal makes the
termination criterion explicit and machine-verifiable.

Design: check_goal() is PURE SUBPROCESS — no LLM, no network, no mutation.
It runs evidence_cmd, matches success_pattern, and returns a StopVerdict.
This is the Ralph-technique pattern (`while ! grep -q "DONE" STATUS.md`)
lifted into a first-class dataclass. The simplest goal = one grep; the
richest = a full test-suite command.

Usage in an orchestrator loop::

    goal = LoopGoal(
        end_state="All billing tests pass",
        evidence_cmd="pytest tests/billing -q",
        success_pattern=r"\\d+ passed",
        max_turns=25,
        max_tokens=100_000,
    )
    verdict = check_goal(goal, cwd=state_dir)
    if verdict.met:
        break  # done — evidence is in verdict.evidence
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LoopGoal:
    """First-class verifiable stop condition.

    Attributes:
        end_state:       Natural-language description of the goal (for LLM
                         prompts and UI display).
        evidence_cmd:    Shell command to run as objective evidence. If None,
                         check_goal returns not-met (cannot verify).
        success_pattern: Regex matched against combined stdout+stderr of
                         evidence_cmd. A match means goal is met. If None,
                         success is inferred from exit code 0.
        constraints:     Guardrails the loop must not violate (surfaced in
                         system prompts; not enforced programmatically here).
        max_turns:       Hard turn ceiling.
        max_tokens:      Token ceiling. 0 means no ceiling.
        timeout_s:       Wall-clock ceiling in seconds.
    """

    end_state: str
    evidence_cmd: str | None = None
    success_pattern: str | None = None
    constraints: tuple[str, ...] = ()
    max_turns: int = 25
    max_tokens: int = 100_000
    timeout_s: float = 1800.0

    def __post_init__(self) -> None:
        if not self.end_state.strip():
            raise ValueError("end_state must be a non-empty description")
        if self.max_turns < 1:
            raise ValueError("max_turns must be >= 1")


@dataclass(frozen=True)
class StopVerdict:
    """Immutable result of a single check_goal() call.

    Attributes:
        met:      True when the goal's termination criterion is satisfied.
        evidence: Raw stdout+stderr from evidence_cmd (empty when no cmd).
        reason:   Human-readable explanation of the verdict.
    """

    met: bool
    evidence: str
    reason: str


def check_goal(
    goal: LoopGoal,
    cwd: str | Path = ".",
    timeout_per_cmd: float = 30.0,
) -> StopVerdict:
    """Run evidence_cmd and check success_pattern — PURE SUBPROCESS, no LLM.

    Any subprocess error produces a NOT-met verdict rather than raising.
    A broken evidence command surfaces clearly in the verdict reason.

    Args:
        goal:            The LoopGoal to evaluate.
        cwd:             Working directory for the evidence command.
        timeout_per_cmd: Per-command subprocess timeout in seconds.

    Returns:
        A frozen StopVerdict.
    """
    if not goal.evidence_cmd:
        return StopVerdict(
            met=False,
            evidence="",
            reason="no evidence_cmd configured — cannot verify programmatically",
        )

    cwd_path = Path(cwd).resolve()
    try:
        args = shlex.split(goal.evidence_cmd)
        proc = subprocess.run(
            args,
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            timeout=timeout_per_cmd,
        )
        evidence = (proc.stdout + proc.stderr).strip()
    except FileNotFoundError as exc:
        return StopVerdict(met=False, evidence="", reason=f"evidence_cmd not found: {exc}")
    except subprocess.TimeoutExpired:
        return StopVerdict(
            met=False, evidence="", reason=f"evidence_cmd timed out after {timeout_per_cmd}s"
        )
    except Exception as exc:  # noqa: BLE001
        return StopVerdict(met=False, evidence="", reason=f"evidence_cmd failed: {exc}")

    if goal.success_pattern is not None:
        if re.search(goal.success_pattern, evidence):
            return StopVerdict(
                met=True,
                evidence=evidence[:2000],
                reason=f"pattern {goal.success_pattern!r} matched in evidence",
            )
        return StopVerdict(
            met=False,
            evidence=evidence[:2000],
            reason=f"pattern {goal.success_pattern!r} not found in evidence",
        )

    # No pattern → infer from exit code
    if proc.returncode == 0:
        return StopVerdict(met=True, evidence=evidence[:2000], reason="evidence_cmd exited 0")
    return StopVerdict(
        met=False, evidence=evidence[:2000], reason=f"evidence_cmd exited {proc.returncode}"
    )


if __name__ == "__main__":
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        flag = os.path.join(d, "STATUS.md")
        with open(flag, "w") as f:
            f.write("ALL TASKS DONE\n")

        goal = LoopGoal(
            end_state="Status file says done",
            evidence_cmd=f"grep -c 'ALL TASKS DONE' {flag}",
            success_pattern=r"[1-9]",
        )
        v = check_goal(goal, cwd=d)
        assert v.met, v

        goal2 = LoopGoal(
            end_state="Status file says FAILED",
            evidence_cmd=f"grep -c 'FAILED' {flag}",
            success_pattern=r"[1-9]",
        )
        assert not check_goal(goal2, cwd=d).met

        assert not check_goal(LoopGoal(end_state="Build passes")).met

        goal4 = LoopGoal(end_state="Impossible", evidence_cmd="this-cmd-does-not-exist-xyzzy")
        assert not check_goal(goal4, cwd=d).met

    print("loop.goal self-check OK")
