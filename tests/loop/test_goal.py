"""Tests for agentkit.loop.goal — LoopGoal + check_goal()."""
import pytest
from pathlib import Path

from agentkit.loop.goal import LoopGoal, StopVerdict, check_goal


def test_loop_goal_requires_end_state():
    with pytest.raises(ValueError, match="end_state"):
        LoopGoal(end_state="")


def test_loop_goal_requires_positive_max_turns():
    with pytest.raises(ValueError, match="max_turns"):
        LoopGoal(end_state="done", max_turns=0)


def test_check_goal_no_evidence_cmd():
    goal = LoopGoal(end_state="Build passes")
    verdict = check_goal(goal)
    assert not verdict.met
    assert "no evidence_cmd" in verdict.reason


def test_check_goal_pattern_matched(tmp_path: Path):
    flag = tmp_path / "STATUS.md"
    flag.write_text("ALL TASKS DONE\n")
    goal = LoopGoal(
        end_state="Status done",
        evidence_cmd=f"grep ALL {flag}",
        success_pattern=r"ALL",
    )
    verdict = check_goal(goal, cwd=tmp_path)
    assert verdict.met
    assert verdict.evidence


def test_check_goal_pattern_not_matched(tmp_path: Path):
    flag = tmp_path / "STATUS.md"
    flag.write_text("IN PROGRESS\n")
    goal = LoopGoal(
        end_state="Status done",
        evidence_cmd=f"grep ALL {flag}",
        success_pattern=r"ALL",
    )
    verdict = check_goal(goal, cwd=tmp_path)
    assert not verdict.met


def test_check_goal_exit_code_zero(tmp_path: Path):
    goal = LoopGoal(end_state="Echo works", evidence_cmd="echo hello")
    verdict = check_goal(goal, cwd=tmp_path)
    assert verdict.met
    assert "exited 0" in verdict.reason


def test_check_goal_exit_code_nonzero(tmp_path: Path):
    goal = LoopGoal(
        end_state="Grep finds pattern",
        evidence_cmd="grep -q NONEXISTENT_XYZ /dev/null",
    )
    verdict = check_goal(goal, cwd=tmp_path)
    assert not verdict.met


def test_check_goal_bad_command(tmp_path: Path):
    goal = LoopGoal(
        end_state="Impossible",
        evidence_cmd="this-command-does-not-exist-xyzzy-abc",
    )
    verdict = check_goal(goal, cwd=tmp_path)
    assert not verdict.met
    assert "not found" in verdict.reason


def test_stop_verdict_is_frozen():
    v = StopVerdict(met=True, evidence="out", reason="ok")
    with pytest.raises(Exception):
        v.met = False  # type: ignore[misc]


def test_loop_goal_constraints_stored():
    goal = LoopGoal(end_state="done", constraints=("no mutation", "max 3 files"))
    assert goal.constraints == ("no mutation", "max 3 files")
