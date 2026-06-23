"""Tests for ToolAugmentedClient (tools.py) — fully offline (web_search mocked)."""

from __future__ import annotations

from typing import Any

from agentkit.types import ChatResult, LLMClient
from studio.shared_bridge import SHARED_PATH  # noqa: F401 - ensures shim is loaded
from studio.tools import ToolAugmentedClient

import sys

sys.path.insert(0, SHARED_PATH)
from web_toolkit import SearchResult  # noqa: E402


class _ScriptedClient:
    """Inner LLMClient that emits a tool_call on the first turn, text on the next.

    Lets us drive the tool loop deterministically with no network.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.seen_tools: list[Any] = []

    def chat(self, messages, tools=None) -> ChatResult:
        self.calls += 1
        self.seen_tools.append(tools)
        if self.calls == 1:
            return ChatResult(
                text="",
                total_tokens=10,
                tool_calls=[("web_search", {"query": "qwen 3 release date"})],
            )
        return ChatResult(text="Qwen 3 shipped in 2025.", total_tokens=7)


def _fake_search(query: str, *, results: int = 5) -> list[SearchResult]:
    return [
        SearchResult(title="Qwen 3", url="https://x.test/q3", snippet="released 2025"),
        SearchResult(title="Blog", url="https://x.test/blog", snippet="notes"),
    ][:results]


def test_satisfies_llmclient() -> None:
    c = ToolAugmentedClient(_ScriptedClient(), search_fn=_fake_search)
    assert isinstance(c, LLMClient)


def test_runs_tool_loop_and_accumulates_tokens() -> None:
    """Inner is called twice (tool turn + answer); tokens sum across iterations."""
    inner = _ScriptedClient()
    c = ToolAugmentedClient(inner, search_fn=_fake_search)
    res = c.chat([{"role": "user", "content": "when did qwen 3 ship?"}])
    assert inner.calls == 2
    assert res.text == "Qwen 3 shipped in 2025."
    assert res.total_tokens == 17  # 10 (tool turn) + 7 (answer)
    # web_search schema was merged into tools on the first call.
    assert any(
        t.get("function", {}).get("name") == "web_search"
        for t in (inner.seen_tools[0] or [])
    )


class _InlineToolClient:
    """Inner client that emits the tool call as INLINE TAGGED TEXT with NO structured
    tool_calls — mirrors oMLX/Qwen, which don't support OpenAI function-calling."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            return ChatResult(
                text='<execute>{"name": "web_search", "arguments": {"query": "qwen 3"}}</execute>',
                total_tokens=5,
                tool_calls=[],  # backend returns NO structured tool_calls
            )
        return ChatResult(text="Qwen 3 shipped in 2025.", total_tokens=4)


def test_parse_inline_tool_calls_variants() -> None:
    from studio.tools import _parse_inline_tool_calls

    names = {"web_search", "write_file"}
    txt = (
        '<tools>{"name":"write_file","arguments":{"path":"a.txt","content":"x"}}</tools>'
        '<execute>{"name":"web_search","arguments":{"query":"q"}}</execute>'
        '<tool_call>{"name":"unknown_tool","arguments":{}}</tool_call>'
        "<execute>not json</execute>"
    )
    got = _parse_inline_tool_calls(txt, names)
    assert ("write_file", {"path": "a.txt", "content": "x"}) in got
    assert ("web_search", {"query": "q"}) in got
    assert all(n != "unknown_tool" for n, _ in got)  # unregistered name filtered out
    assert len(got) == 2  # non-JSON blob + unknown tool dropped


def test_inline_tool_call_fires_on_non_function_calling_backend() -> None:
    """A backend emitting the call as inline <execute> text still fires the tool;
    the call blob must NOT leak into the final answer (the live oMLX bug)."""
    calls: list[tuple] = []
    inner = _InlineToolClient()
    c = ToolAugmentedClient(
        inner,
        search_fn=_fake_search,
        on_tool_call=lambda sid, tool, args: calls.append((tool, args)),
    )
    res = c.chat([{"role": "user", "content": "when did qwen 3 ship?"}])
    assert inner.calls == 2  # tool turn + answer turn — the loop continued
    assert calls and calls[0][0] == "web_search"  # the inline call actually fired
    assert "<execute>" not in res.text  # blob did not leak
    assert res.text == "Qwen 3 shipped in 2025."


def test_emits_tool_call_and_result_events() -> None:
    calls: list[tuple] = []
    results: list[tuple] = []
    c = ToolAugmentedClient(
        _ScriptedClient(),
        search_fn=_fake_search,
        on_tool_call=lambda sid, tool, args: calls.append((sid, tool, args)),
        on_tool_result=lambda sid, tool, summary, n, notice, rejected: results.append(
            (sid, tool, summary, n, notice)
        ),
        step_id_getter=lambda: "s1",
    )
    c.chat([{"role": "user", "content": "q"}])
    assert calls and calls[0][1] == "web_search"
    assert calls[0][0] == "s1"
    assert results and results[0][3] == 2  # n_results
    assert results[0][4] == ""  # no notice on success


def test_max_iters_caps_runaway_loop() -> None:
    """A client that ALWAYS requests the tool stops at the iteration cap."""

    class _AlwaysTool:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools=None) -> ChatResult:
            self.calls += 1
            return ChatResult(
                text="", total_tokens=1, tool_calls=[("web_search", {"query": "x"})]
            )

    inner = _AlwaysTool()
    c = ToolAugmentedClient(inner, search_fn=_fake_search, max_iters=3)
    c.chat([{"role": "user", "content": "q"}])
    assert inner.calls == 3  # capped


def test_search_failure_is_nonfatal_with_notice() -> None:
    """A SearchError-equivalent yields an empty result + a notice, loop continues."""

    def boom(query: str, *, results: int = 5) -> list[SearchResult]:
        raise RuntimeError("no backend")

    results: list[tuple] = []
    c = ToolAugmentedClient(
        _ScriptedClient(),
        search_fn=boom,
        on_tool_result=lambda sid, tool, summary, n, notice, rejected: results.append(
            (n, notice)
        ),
    )
    res = c.chat([{"role": "user", "content": "q"}])
    # Still produced the final answer despite the search failing.
    assert res.text == "Qwen 3 shipped in 2025."
    assert results[0][0] == 0  # n_results
    assert "degraded" in results[0][1]


def test_no_tool_call_passes_through() -> None:
    """When the model never calls the tool, inner runs once and text passes through."""

    class _PlainClient:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(text="direct answer", total_tokens=4)

    c = ToolAugmentedClient(_PlainClient(), search_fn=_fake_search)
    res = c.chat([{"role": "user", "content": "q"}])
    assert res.text == "direct answer"
    assert res.total_tokens == 4


# --- file tools through the client (jailed; offline) --------------------------

import json  # noqa: E402

import pytest  # noqa: E402

from studio.workspace import Workspace  # noqa: E402


class _FileToolClient:
    """Inner client that issues one file tool_call, then answers."""

    def __init__(self, name: str, args: dict) -> None:
        self._name = name
        self._args = args
        self.calls = 0

    def chat(self, messages, tools=None) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            return ChatResult(text="", total_tokens=2, tool_calls=[(self._name, self._args)])
        # The tool-result message is the last appended message; echo its content
        # so the test can assert the loop fed the result back to the model.
        return ChatResult(text="done", total_tokens=1)


def _ws(tmp_path) -> Workspace:
    return Workspace("sess-tools", root=tmp_path / "ws")


def _last_tool_msg(seen: list) -> dict:
    """Pull the tool-result message the loop appended (role == 'tool')."""
    return seen[-1]


def test_write_file_tool_inside_workspace(tmp_path) -> None:
    ws = _ws(tmp_path)
    results: list[tuple] = []
    inner = _FileToolClient("write_file", {"path": "out.txt", "content": "hi there"})
    c = ToolAugmentedClient(
        inner,
        workspace=ws,
        on_tool_result=lambda sid, tool, summary, n, notice, rejected: results.append(
            (tool, summary, notice)
        ),
    )
    res = c.chat([{"role": "user", "content": "write it"}])
    assert res.text == "done"
    assert (ws.root / "out.txt").read_text() == "hi there"
    assert results[0][0] == "write_file"
    assert "wrote 8B to out.txt" == results[0][1]
    assert results[0][2] == ""  # no notice on success


def test_read_file_tool_inside_workspace(tmp_path) -> None:
    ws = _ws(tmp_path)
    (ws.root / "notes.md").write_text("remember this")
    inner = _FileToolClient("read_file", {"path": "notes.md"})
    captured: list[dict] = []

    class _Recorder(_FileToolClient):
        def chat(self, messages, tools=None) -> ChatResult:
            if self.calls >= 1:
                captured.append(messages[-1])  # the tool-result message
            return super().chat(messages, tools)

    rec = _Recorder("read_file", {"path": "notes.md"})
    c = ToolAugmentedClient(rec, workspace=ws)
    c.chat([{"role": "user", "content": "read it"}])
    payload = json.loads(captured[0]["content"])
    assert payload["content"] == "remember this"
    assert payload["bytes"] == 13


def test_write_file_escape_rejected_via_tool_result(tmp_path) -> None:
    """A ../ escape returns an ERROR result (not raised) and writes nothing."""
    ws = _ws(tmp_path)
    outside = tmp_path / "pwned.txt"
    results: list[tuple] = []
    captured: list[dict] = []

    class _Rec(_FileToolClient):
        def chat(self, messages, tools=None) -> ChatResult:
            if self.calls >= 1:
                captured.append(messages[-1])
            return super().chat(messages, tools)

    inner = _Rec("write_file", {"path": "../../pwned.txt", "content": "owned"})
    c = ToolAugmentedClient(
        inner,
        workspace=ws,
        on_tool_result=lambda sid, tool, summary, n, notice, rejected: results.append(
            (summary, notice, rejected)
        ),
    )
    res = c.chat([{"role": "user", "content": "escape"}])
    # The run did NOT raise; it returned a normal answer.
    assert res.text == "done"
    # The tool result carried an error, and the file outside is untouched.
    assert not outside.exists()
    payload = json.loads(captured[0]["content"])
    assert "error" in payload and "escapes workspace" in payload["error"]
    assert results[0][1] and "escapes" in results[0][1]  # notice on the event
    assert results[0][2] is True  # explicit rejected flag, not inferred from text


def test_read_file_escape_rejected_via_tool_result(tmp_path) -> None:
    ws = _ws(tmp_path)
    captured: list[dict] = []

    class _Rec(_FileToolClient):
        def chat(self, messages, tools=None) -> ChatResult:
            if self.calls >= 1:
                captured.append(messages[-1])
            return super().chat(messages, tools)

    inner = _Rec("read_file", {"path": "/etc/passwd"})
    c = ToolAugmentedClient(inner, workspace=ws)
    res = c.chat([{"role": "user", "content": "read secrets"}])
    assert res.text == "done"  # not raised
    payload = json.loads(captured[0]["content"])
    assert "error" in payload


def test_file_tools_absent_without_workspace() -> None:
    """No workspace → file-tool schemas are not advertised (only web_search)."""
    c = ToolAugmentedClient(_ScriptedClient(), search_fn=_fake_search)
    names = {s["function"]["name"] for s in c._schemas}
    assert names == {"web_search"}


def test_file_tools_present_with_workspace(tmp_path) -> None:
    c = ToolAugmentedClient(_ScriptedClient(), workspace=_ws(tmp_path))
    names = {s["function"]["name"] for s in c._schemas}
    assert names == {"web_search", "read_file", "write_file"}
