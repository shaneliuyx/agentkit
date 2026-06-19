"""Tests for agentkit.backends.cli — CliLLMClient (no real CLI invoked)."""

from __future__ import annotations

import subprocess

import pytest

from agentkit.backends import CliLLMClient
from agentkit.types import LLMClient, Message


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_satisfies_llmclient_protocol():
    assert isinstance(CliLLMClient(), LLMClient)


def test_prompt_rendering_contains_content_and_tool_note():
    client = CliLLMClient(cmd="codex exec")
    messages: list[Message] = [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello there"},
    ]
    tools = [{"type": "function", "function": {"name": "search"}}]
    prompt = client._render_prompt(messages, tools)
    assert "hello there" in prompt
    assert "[system]" in prompt
    assert "Available tools: search" in prompt


def test_argv_construction_no_shell(monkeypatch):
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeProc(returncode=0, stdout="final answer")

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = CliLLMClient(cmd="codex exec")
    res = client.chat([{"role": "user", "content": "rm -rf / ; echo $(whoami)"}])

    # NOT a shell call: no shell=True anywhere.
    assert "shell" not in captured["kwargs"] or captured["kwargs"]["shell"] is False
    # argv is a list; base cmd is split; prompt is the SINGLE trailing element.
    argv = captured["argv"]
    assert argv[0] == "codex" and argv[1] == "exec"
    assert argv[-1] == client._render_prompt(
        [{"role": "user", "content": "rm -rf / ; echo $(whoami)"}]
    )
    # The dangerous metacharacters live inside one argv element, never parsed.
    assert "rm -rf / ; echo $(whoami)" in argv[-1]
    assert res.text == "final answer"
    assert res.total_tokens == 0


def test_nonzero_exit_raises_runtimeerror(monkeypatch):
    def fake_run(argv, **kwargs):
        return _FakeProc(returncode=2, stdout="", stderr="boom failure tail")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CliLLMClient(cmd="codex exec")
    with pytest.raises(RuntimeError, match="exited 2"):
        client.chat([{"role": "user", "content": "hi"}])


def test_timeout_raises_runtimeerror(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1.0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CliLLMClient(cmd="codex exec", timeout=1.0)
    with pytest.raises(RuntimeError, match="timed out"):
        client.chat([{"role": "user", "content": "hi"}])


def test_tool_calls_parsed_from_stdout(monkeypatch):
    stdout = '<tool_call>{"name": "search", "arguments": {"q": "cats"}}</tool_call>'

    def fake_run(argv, **kwargs):
        return _FakeProc(returncode=0, stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CliLLMClient(cmd="codex exec")
    res = client.chat([{"role": "user", "content": "find cats"}])
    assert res.tool_calls == [("search", {"q": "cats"})]
    assert res.text == stdout
