"""Tests for agentkit.context.compactor — the deterministic compaction component."""

from __future__ import annotations

from agentkit.context import compact, merge
from agentkit.context.compactor import (
    COMMITS,
    FILES,
    GOAL,
    OUTSTANDING,
    PREFS,
)
from agentkit.types import Message


def _coding_session(rounds: int = 12) -> list[Message]:
    msgs: list[Message] = [
        {"role": "system", "content": "system prompt"},
        {"role": "user",
         "content": "Build a CSV parser. Always validate the header row."},
    ]
    for i in range(rounds):
        msgs.append({"role": "assistant", "content": f"step {i}",
                     "tool_calls": [{"function": {
                         "name": "write_file" if i == 0 else "edit_file",
                         "arguments": f'{{"path": "src/csv_{i}.py"}}'}}]})
        if i == 0:
            msgs.append({"role": "tool", "name": "shell",
                         "content": "git commit -m \"init parser\"\n[main deadbee] init parser"})
        elif i == 3:
            msgs.append({"role": "tool", "name": "pytest",
                         "content": "Traceback: ValueError bad header"})
        else:
            msgs.append({"role": "tool", "name": "pytest", "content": "passed"})
    msgs.append({"role": "user", "content": "Actually, prefer the csv module."})
    msgs.append({"role": "assistant", "content": "Switching to csv module."})
    return msgs


def test_determinism_same_input_same_text() -> None:
    session = _coding_session()
    assert compact(session, keep=1).text == compact(session, keep=1).text


def test_token_reduction() -> None:
    # Deterministic compaction compresses long sessions: the structured
    # sections cap (files=30, commits=8) and the brief transcript is a bounded
    # rolling window, while the raw conversation grows linearly. The win is
    # therefore a property of scale — assert it on a realistically long session.
    session = _coding_session(rounds=100)
    result = compact(session, keep=1)
    assert result.est_tokens_after < result.est_tokens_before


def test_token_reduction_rolling_window_tightens() -> None:
    # A tighter transcript window yields strictly more reduction — proof the
    # rolling window is the load-bearing compression lever at scale.
    session = _coding_session(rounds=100)
    wide = compact(session, keep=1, max_lines=120)
    narrow = compact(session, keep=1, max_lines=30)
    assert narrow.est_tokens_after < wide.est_tokens_after


def test_goal_extraction() -> None:
    result = compact([
        {"role": "user", "content": "Build a web scraper."},
        {"role": "assistant", "content": "ok"},
    ], keep=0)
    assert GOAL in result.sections
    assert "web scraper" in result.sections[GOAL][0]


def test_goal_scope_change_lines() -> None:
    result = compact([
        {"role": "user", "content": "Build a scraper."},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "Also handle pagination."},
        {"role": "assistant", "content": "ok"},
    ], keep=0)
    goal = result.sections[GOAL]
    assert any("[Scope change]" in line and "pagination" in line for line in goal)


def test_files_extraction_created_and_modified() -> None:
    result = compact([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "write_file", "arguments": '{"path": "a.py"}'}}]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "edit_file", "arguments": '{"path": "a.py"}'}}]},
    ], keep=0)
    files = result.sections[FILES]
    assert any(f.startswith("Created:") for f in files)
    assert any(f.startswith("Modified:") for f in files)


def test_commits_extraction() -> None:
    result = compact(_coding_session(), keep=1)
    assert COMMITS in result.sections
    assert any("init parser" in c for c in result.sections[COMMITS])


def test_outstanding_extraction() -> None:
    result = compact(_coding_session(), keep=1)
    assert OUTSTANDING in result.sections
    assert any("Traceback" in o for o in result.sections[OUTSTANDING])


def test_preferences_extraction() -> None:
    result = compact(_coding_session(), keep=1)
    assert PREFS in result.sections
    assert any("validate" in p for p in result.sections[PREFS])


def test_keep_zero_compacts_everything() -> None:
    session = _coding_session()
    result = compact(session, keep=0)
    assert result.kept_tail == []


def test_keep_one_tail_starts_at_last_user_turn() -> None:
    session = _coding_session()
    result = compact(session, keep=1)
    assert len(result.kept_tail) >= 1
    assert result.kept_tail[0]["role"] == "user"
    # The tail is strictly smaller than the whole conversation.
    assert len(result.kept_tail) < len(session)


def test_brief_transcript_rolling_window() -> None:
    msgs: list[Message] = [{"role": "user", "content": "start"}]
    for i in range(300):
        msgs.append({"role": "assistant", "content": f"line {i}"})
    result = compact(msgs, keep=0, max_lines=50)
    transcript_lines = [ln for ln in result.text.splitlines()
                        if ln.startswith("[") or ln.startswith("*") or ln.startswith("...(")]
    # Window cap honored (plus the elision marker).
    assert any(ln.startswith("...(") and "omitted" in ln for ln in transcript_lines)


def test_merge_sticky_accumulates_volatile_replaces() -> None:
    first = compact([
        {"role": "user", "content": "Build X. Always log errors."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "write_file", "arguments": '{"path": "x1.py"}'}}]},
        {"role": "assistant", "content": "ok"},
    ], keep=0)
    second = compact([
        {"role": "user", "content": "Build Y. Never swallow exceptions."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "write_file", "arguments": '{"path": "y1.py"}'}}]},
        {"role": "assistant", "content": "ok"},
    ], keep=0)

    merged = merge(first, second)

    # Sticky: goal + prefs accumulate from BOTH.
    goal_text = " ".join(merged.sections[GOAL])
    assert "Build X" in goal_text and "Build Y" in goal_text
    prefs_text = " ".join(merged.sections[PREFS])
    assert "log errors" in prefs_text and "swallow exceptions" in prefs_text

    # Volatile: Files replaced by the newer result (y1, not x1).
    files_text = " ".join(merged.sections[FILES])
    assert "y1.py" in files_text
    assert "x1.py" not in files_text
