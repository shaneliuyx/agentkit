"""Tests for agentkit.codegen — agent-authored, sandbox-validated tools.

The load-bearing part is the deterministic validate+repair LOOP: run the
candidate in a real ``SubprocessSandbox``, inspect ``exit_code``, and only
when it is non-zero feed the traceback to the injected LLM debugger for a
patch — up to ``max_repairs`` attempts. The loop CONTROL is model-free; only
the code-proposer and the debugger are the injected (here: fake) LLM.

Admission honors the gate: a pure/read-only tool ACCEPTs and auto-registers;
a side-effecting tool (uses subprocess) ESCALATEs at containment and is NOT
auto-registered. No network — the fake client returns canned schema+code (and
a canned repaired version on its second call to exercise the debugger).

instruction: build agentkit codegen, re-plan Phase 6
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from agentkit.codegen import GeneratedTool, ToolForge, propose_tool
from agentkit.gates import Gate, Outcome
from agentkit.sandbox import SubprocessSandbox
from agentkit.types import ChatResult, Message


# ---------------------------------------------------------------------------
# Fakes — a scripted LLMClient. Each entry is the text returned for one
# ``chat`` call, in order. No network; fully deterministic.
# ---------------------------------------------------------------------------
class _ScriptedClient:
    """Returns pre-canned ``ChatResult`` texts in sequence; records calls."""

    def __init__(self, *texts: str) -> None:
        self._texts = list(texts)
        self.calls: list[list[Message]] = []
        self.idx = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        self.calls.append(messages)
        text = self._texts[min(self.idx, len(self._texts) - 1)]
        self.idx += 1
        return ChatResult(text=text)


def _schema_json(name: str = "add_numbers") -> str:
    return json.dumps(
        {
            "name": name,
            "description": "Add two integers and print the result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
        }
    )


# A clean, read-only implementation: prints, no side effects, runs to exit 0.
_CLEAN_CODE = "a, b = 2, 3\nprint(a + b)\n"

# A first draft with a SyntaxError, to be repaired by the debugger.
_BROKEN_CODE = "def add(:\n    return 2 + 3\n"

# The repaired version the debugger returns on its second call.
_REPAIRED_CODE = "print(2 + 3)\n"

# A side-effecting implementation that uses subprocess -> ESCALATE.
_SIDE_EFFECTING_CODE = "import subprocess\nsubprocess.run(['echo', 'hi'])\n"


def _proposer(schema: str, code: str) -> str:
    """Canned proposer reply: schema + a fenced python code block."""
    return f"SCHEMA:\n{schema}\n\nCODE:\n```python\n{code}\n```\n"


def _forge(client: _ScriptedClient, evaluator=lambda p: 0.9) -> ToolForge:
    return ToolForge(
        client=client,
        sandbox=SubprocessSandbox(),
        gate=Gate(sandbox=SubprocessSandbox(), evaluator=evaluator, cwd="."),
    )


# ---------------------------------------------------------------------------
# GeneratedTool dataclass
# ---------------------------------------------------------------------------
def test_generated_tool_is_frozen():
    tool = GeneratedTool(
        name="t", schema={"name": "t"}, code="print(1)",
        manifest={"name": "t"}, verdict=None,
    )
    with pytest.raises(FrozenInstanceError):
        tool.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# propose_tool — schema-before-code
# ---------------------------------------------------------------------------
def test_propose_tool_drafts_schema_then_code():
    client = _ScriptedClient(_proposer(_schema_json(), _CLEAN_CODE))
    tool = propose_tool("add two numbers", client=client)
    assert tool.name == "add_numbers"
    assert tool.schema["name"] == "add_numbers"
    assert "parameters" in tool.schema
    assert "print" in tool.code


# ---------------------------------------------------------------------------
# Happy path: clean read-only tool reaches ACCEPT and registers
# ---------------------------------------------------------------------------
def test_clean_readonly_tool_accepts_and_registers(tmp_path: Path):
    client = _ScriptedClient(_proposer(_schema_json(), _CLEAN_CODE))
    forge = ToolForge(
        client=client,
        sandbox=SubprocessSandbox(),
        gate=Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9, cwd=str(tmp_path)),
    )
    tool = forge.forge("add two numbers")
    assert tool.verdict.status is Outcome.ACCEPT, tool.verdict

    registry: dict[str, GeneratedTool] = {}
    registered = forge.register(tool, registry)
    assert registered is True
    assert "add_numbers" in registry


def test_manifest_is_mcp_style(tmp_path: Path):
    client = _ScriptedClient(_proposer(_schema_json(), _CLEAN_CODE))
    forge = ToolForge(
        client=client,
        sandbox=SubprocessSandbox(),
        gate=Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9, cwd=str(tmp_path)),
    )
    tool = forge.forge("add two numbers")
    assert tool.manifest["name"] == "add_numbers"
    assert tool.manifest["schema"] == tool.schema
    assert tool.manifest["code"] == tool.code


# ---------------------------------------------------------------------------
# Debugger loop: broken first draft -> repaired -> passes
# ---------------------------------------------------------------------------
def test_broken_draft_is_repaired_then_passes(tmp_path: Path):
    # Call 1: proposer returns broken code. Call 2: debugger returns repaired.
    client = _ScriptedClient(
        _proposer(_schema_json(), _BROKEN_CODE),
        _REPAIRED_CODE,
    )
    forge = ToolForge(
        client=client,
        sandbox=SubprocessSandbox(),
        gate=Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9, cwd=str(tmp_path)),
    )
    tool = forge.forge("add two numbers", max_repairs=3)
    # The debugger was invoked at least once.
    assert client.idx >= 2
    assert tool.code.strip() == _REPAIRED_CODE.strip()
    assert tool.verdict.status is Outcome.ACCEPT, tool.verdict


# ---------------------------------------------------------------------------
# Side-effecting tool ESCALATEs and is NOT auto-registered
# ---------------------------------------------------------------------------
def test_side_effecting_tool_escalates_and_is_not_registered(tmp_path: Path):
    client = _ScriptedClient(_proposer(_schema_json("run_cmd"), _SIDE_EFFECTING_CODE))
    forge = ToolForge(
        client=client,
        sandbox=SubprocessSandbox(),
        gate=Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9, cwd=str(tmp_path)),
    )
    tool = forge.forge("run a shell command")
    assert tool.verdict.status is Outcome.ESCALATE, tool.verdict
    assert tool.verdict.stage == "containment"

    registry: dict[str, GeneratedTool] = {}
    registered = forge.register(tool, registry)
    assert registered is False
    assert registry == {}


# ---------------------------------------------------------------------------
# The repair loop STOPS after max_repairs (never loops forever)
# ---------------------------------------------------------------------------
def test_repair_loop_stops_after_max_repairs(tmp_path: Path):
    # Proposer + every debugger reply stay broken -> loop must give up.
    client = _ScriptedClient(_proposer(_schema_json(), _BROKEN_CODE), _BROKEN_CODE)
    forge = ToolForge(
        client=client,
        sandbox=SubprocessSandbox(),
        gate=Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9, cwd=str(tmp_path)),
    )
    tool = forge.forge("add two numbers", max_repairs=2)
    # 1 proposer call + exactly max_repairs debugger calls = 3.
    assert client.idx == 3
    # Still broken -> the gate rejects it at syntax/execute (never ACCEPT).
    assert tool.verdict.status is Outcome.REJECT, tool.verdict


def test_register_only_admits_accepted(tmp_path: Path):
    client = _ScriptedClient(_proposer(_schema_json(), _BROKEN_CODE), _BROKEN_CODE)
    forge = ToolForge(
        client=client,
        sandbox=SubprocessSandbox(),
        gate=Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9, cwd=str(tmp_path)),
    )
    tool = forge.forge("add two numbers", max_repairs=1)
    registry: dict[str, GeneratedTool] = {}
    assert forge.register(tool, registry) is False
    assert registry == {}
