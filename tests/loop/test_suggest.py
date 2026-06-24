"""Tests for agentkit.loop.suggest — GoalSuggestion + suggest_goal_params."""
import json
import pytest

from agentkit.loop.suggest import GoalSuggestion, suggest_goal_params


# ── Minimal stub LLM client ───────────────────────────────────────────────────

class _ChatResult:
    def __init__(self, text: str):
        self.text = text

class _FakeClient:
    def __init__(self, response: str):
        self._response = response

    def chat(self, messages):
        return _ChatResult(self._response)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_returns_defaults_on_empty_end_state():
    client = _FakeClient("{}")
    s = suggest_goal_params("", client)
    assert isinstance(s, GoalSuggestion)
    assert s.max_turns == 25

def test_parses_valid_json_response():
    payload = json.dumps({
        "evidence_cmd": "pytest -q",
        "success_pattern": r"\d+ passed",
        "max_turns": 20,
        "max_tokens": 50000,
        "timeout_s": 600,
        "constraints": ["no new deps"],
    })
    s = suggest_goal_params("All tests pass", _FakeClient(payload))
    assert s.evidence_cmd == "pytest -q"
    assert s.success_pattern == r"\d+ passed"
    assert s.max_turns == 20
    assert s.max_tokens == 50000
    assert s.timeout_s == 600.0
    assert s.constraints == ("no new deps",)

def test_extracts_json_from_markdown_fenced_response():
    wrapped = "Sure! Here you go:\n```json\n" + json.dumps({
        "evidence_cmd": "curl -sf http://localhost/health",
        "success_pattern": '"ok"',
        "max_turns": 10,
        "max_tokens": 20000,
        "timeout_s": 120,
        "constraints": [],
    }) + "\n```"
    s = suggest_goal_params("Service healthy", _FakeClient(wrapped))
    assert s.evidence_cmd == "curl -sf http://localhost/health"

def test_graceful_fallback_on_invalid_json():
    s = suggest_goal_params("Something", _FakeClient("not json at all"))
    assert isinstance(s, GoalSuggestion)
    assert s.max_turns == 25  # default

def test_graceful_fallback_on_client_exception():
    class _BrokenClient:
        def chat(self, messages):
            raise RuntimeError("network error")

    s = suggest_goal_params("Something", _BrokenClient())
    assert isinstance(s, GoalSuggestion)

def test_frozen_dataclass():
    s = GoalSuggestion()
    with pytest.raises(Exception):
        s.max_turns = 99  # type: ignore[misc]

def test_task_context_included_in_suggestion():
    captured: list[str] = []
    class _CapturingClient:
        def chat(self, messages):
            captured.extend(m["content"] for m in messages)
            return _ChatResult(json.dumps({"evidence_cmd": "", "success_pattern": "",
                                           "max_turns": 10, "max_tokens": 10000,
                                           "timeout_s": 60, "constraints": []}))

    suggest_goal_params("Tests pass", _CapturingClient(), task="build billing service")
    assert any("billing service" in c for c in captured)

def test_min_bounds_enforced():
    payload = json.dumps({
        "evidence_cmd": "echo hi",
        "success_pattern": "",
        "max_turns": 0,       # below min
        "max_tokens": 100,    # below min
        "timeout_s": 0,       # below min
        "constraints": [],
    })
    s = suggest_goal_params("Something", _FakeClient(payload))
    assert s.max_turns >= 1
    assert s.max_tokens >= 1000
    assert s.timeout_s >= 10.0
