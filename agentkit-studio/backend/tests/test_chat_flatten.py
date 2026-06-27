"""Tests for flatten_chat_to_requirement (DESIGN §6.2).

The ChatPanel replaces the single task textarea with a multi-turn thread; this
helper flattens that thread into one requirement string for the planner, keeping
every user/assistant refinement and dropping runtime-only roles.
"""

from studio.session import flatten_chat_to_requirement


def test_flattens_user_and_assistant_turns_in_order():
    msgs = [
        {"role": "user", "content": "research agent loops"},
        {"role": "assistant", "content": "do you want a report?"},
        {"role": "user", "content": "yes, with citations"},
    ]
    out = flatten_chat_to_requirement(msgs)
    assert out == (
        "[USER]: research agent loops\n\n"
        "[ASSISTANT]: do you want a report?\n\n"
        "[USER]: yes, with citations"
    )


def test_drops_system_and_tool_roles():
    msgs = [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "tool noise"},
    ]
    out = flatten_chat_to_requirement(msgs)
    assert out == "[USER]: hi"
    assert "system" not in out.lower()
    assert "tool noise" not in out


def test_empty_history_returns_empty_string():
    assert flatten_chat_to_requirement([]) == ""


def test_role_is_uppercased():
    out = flatten_chat_to_requirement([{"role": "user", "content": "x"}])
    assert out.startswith("[USER]:")


def test_missing_role_key_is_skipped_not_crash():
    msgs = [{"content": "no role"}, {"role": "user", "content": "kept"}]
    out = flatten_chat_to_requirement(msgs)
    assert out == "[USER]: kept"


def test_preserves_full_context_not_just_last_message():
    """The whole point (DESIGN §6.2): planner sees ALL refinements."""
    msgs = [
        {"role": "user", "content": "FIRST-IDEA"},
        {"role": "assistant", "content": "MIDDLE-CLARIFY"},
        {"role": "user", "content": "FINAL-ASK"},
    ]
    out = flatten_chat_to_requirement(msgs)
    assert "FIRST-IDEA" in out and "MIDDLE-CLARIFY" in out and "FINAL-ASK" in out
