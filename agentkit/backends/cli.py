"""agentkit.backends.cli — a CLI-process LLMClient adapter.

Some agent backends are command-line tools (``codex exec``, ``claude -p``, an
``ollama run`` wrapper) rather than HTTP APIs. ``CliLLMClient`` adapts any such
"prompt in, text out" CLI to the ``agentkit.types.LLMClient`` protocol so the
ReAct loop can drive it unchanged.

It renders the OpenAI-style messages into a single prompt string, runs the CLI
as an argv list, and reuses the loop's text-based tool-call parser to recover
tool calls from the CLI's stdout (CLIs don't emit structured tool_calls). Token
usage is reported as 0 because a CLI doesn't expose it.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

# REUSE the loop's text tool-call parser — do not duplicate it.
from agentkit.agent.loop import _parse_text_tool_calls
from agentkit.types import ChatResult, Message

_STDERR_TAIL = 2000


@dataclass
class CliLLMClient:
    """An LLMClient backed by a command-line tool.

    Attributes:
        cmd:     The base command (split with shlex); the rendered prompt is
                 appended as a single trailing argv element.
        timeout: Per-call subprocess timeout in seconds.
    """

    cmd: str = "codex exec"
    timeout: float = 900.0

    def _render_prompt(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> str:
        """Flatten messages (+ optional tool note) into one prompt string."""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content")
            text = content if isinstance(content, str) else ""
            if role == "system":
                parts.append(f"[system]\n{text}")
            elif role == "user":
                parts.append(f"[user]\n{text}")
            elif role == "assistant":
                if text:
                    parts.append(f"[assistant]\n{text}")
            elif role == "tool":
                name = msg.get("name", "tool")
                parts.append(f"[tool:{name}]\n{text}")
            elif text:
                parts.append(f"[{role}]\n{text}")
        if tools:
            names = [
                t.get("function", {}).get("name", t.get("name", "?"))
                for t in tools
            ]
            parts.append("Available tools: " + ", ".join(str(n) for n in names))
        return "\n\n".join(parts)

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """Render → run the CLI → parse tool calls from stdout text."""
        prompt = self._render_prompt(messages, tools)
        argv = [*shlex.split(self.cmd), prompt]

        # SECURITY: shell=False and the prompt is a single argv ELEMENT, never
        # interpolated into a shell line — so prompt content (which may include
        # untrusted tool/file output) cannot inject shell commands.
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stderr = (exc.stderr or "")[-_STDERR_TAIL:] if exc.stderr else ""
            raise RuntimeError(
                f"CLI backend timed out after {self.timeout}s: {stderr}"
            ) from exc

        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "")[-_STDERR_TAIL:]
            raise RuntimeError(
                f"CLI backend exited {proc.returncode}: {stderr_tail}"
            )

        stdout = proc.stdout or ""
        tool_calls = _parse_text_tool_calls(stdout)
        # A CLI does not report token usage.
        return ChatResult(text=stdout, tool_calls=tool_calls, total_tokens=0)


if __name__ == "__main__":
    from agentkit.types import LLMClient

    client = CliLLMClient(cmd="echo")
    assert isinstance(client, LLMClient), "CliLLMClient must satisfy LLMClient"

    sample: list[Message] = [
        {"role": "system", "content": "You are a helpful agent."},
        {"role": "user", "content": "summarize the meeting notes; rm -rf / $(whoami)"},
    ]
    schemas = [{"type": "function", "function": {"name": "search"}}]
    prompt = client._render_prompt(sample, schemas)

    # The prompt carries the user content and the tool note verbatim.
    assert "summarize the meeting notes" in prompt
    assert "Available tools: search" in prompt
    assert "[system]" in prompt and "[user]" in prompt

    # The prompt is a SINGLE argv element — no shell parsing of its metacharacters.
    argv = [*shlex.split(client.cmd), prompt]
    assert argv[-1] == prompt, argv
    assert len(argv) == 2, argv  # ["echo", prompt] — metacharacters stay inert

    # Tool-call parsing reuses the loop's parser.
    parsed = _parse_text_tool_calls('<tool_call>{"name": "search", "arguments": {"q": "x"}}</tool_call>')
    assert parsed == [("search", {"q": "x"})], parsed

    # Only invoke a real subprocess if a trivial CLI exists; otherwise skip.
    if shutil.which("echo"):
        echo_client = CliLLMClient(cmd="echo")
        res = echo_client.chat([{"role": "user", "content": "hello world"}])
        assert "hello world" in res.text, res
        assert res.total_tokens == 0

    print("backends.cli self-check OK")
