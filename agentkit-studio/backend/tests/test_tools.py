"""Tests for ToolAugmentedClient (tools.py) — fully offline (web_search mocked)."""

from __future__ import annotations

from typing import Any

from agentkit.types import ChatResult, LLMClient
from studio.shared_bridge import SHARED_PATH  # noqa: F401 - ensures shim is loaded
from studio.tools import ToolAugmentedClient

import sys

sys.path.insert(0, SHARED_PATH)
from web_toolkit import FetchResult, SearchResult  # noqa: E402


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


# --- web_fetch through the client (offline; fetch_fn injected) ----------------


class _FetchClient:
    """Inner client that issues one web_fetch call, then answers."""

    def __init__(self, args: dict) -> None:
        self._args = args
        self.calls = 0

    def chat(self, messages, tools=None) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            return ChatResult(text="", total_tokens=3, tool_calls=[("web_fetch", self._args)])
        return ChatResult(text="read the page", total_tokens=2)


def test_web_fetch_success_emits_host_summary() -> None:
    """A successful fetch emits 'fetched <size> from <host>' (host only, not url)."""

    def ok_fetch(url, *, selector=None):
        return FetchResult(url=url, ok=True, content="hello world", bytes=11)

    results: list[tuple] = []
    inner = _FetchClient({"url": "https://example.com/page?x=1"})
    c = ToolAugmentedClient(
        inner,
        fetch_fn=ok_fetch,
        on_tool_result=lambda sid, tool, summary, n, notice, rejected: results.append(
            (tool, summary, n, notice, rejected)
        ),
    )
    res = c.chat([{"role": "user", "content": "read it"}])
    assert inner.calls == 2  # fetch turn + answer turn
    assert res.text == "read the page"
    tool, summary, n, notice, rejected = results[0]
    assert tool == "web_fetch"
    assert summary == "fetched 11B from example.com"  # host only, no path/query
    assert n == 1 and notice == "" and rejected is False


def test_web_fetch_content_is_capped() -> None:
    """A huge page is truncated to _MAX_FETCH_CHARS and the summary notes it."""
    from studio.tools import _MAX_FETCH_CHARS

    big = "x" * (_MAX_FETCH_CHARS + 5000)

    def big_fetch(url, *, selector=None):
        return FetchResult(url=url, ok=True, content=big, bytes=len(big))

    captured: list[dict] = []
    results: list[tuple] = []

    class _Rec(_FetchClient):
        def chat(self, messages, tools=None) -> ChatResult:
            if self.calls >= 1:
                captured.append(messages[-1])
            return super().chat(messages, tools)

    inner = _Rec({"url": "https://example.com/big"})
    c = ToolAugmentedClient(
        inner,
        fetch_fn=big_fetch,
        on_tool_result=lambda sid, tool, summary, n, notice, rejected: results.append(
            (summary, n)
        ),
    )
    c.chat([{"role": "user", "content": "read big"}])
    payload = json.loads(captured[0]["content"])
    assert len(payload["content"]) == _MAX_FETCH_CHARS  # capped
    assert payload["truncated"] is True
    assert "truncated" in results[0][0] and results[0][1] == 1


def test_web_fetch_page_failure_is_nonfatal() -> None:
    """ok=False (404/blocked) → error tool-message + notice, loop continues, n=0."""

    def blocked_fetch(url, *, selector=None):
        return FetchResult(url=url, ok=False, error="403 blocked")

    captured: list[dict] = []
    results: list[tuple] = []

    class _Rec(_FetchClient):
        def chat(self, messages, tools=None) -> ChatResult:
            if self.calls >= 1:
                captured.append(messages[-1])
            return super().chat(messages, tools)

    inner = _Rec({"url": "https://example.com/blocked"})
    c = ToolAugmentedClient(
        inner,
        fetch_fn=blocked_fetch,
        on_tool_result=lambda sid, tool, summary, n, notice, rejected: results.append(
            (summary, n, notice, rejected)
        ),
    )
    res = c.chat([{"role": "user", "content": "read blocked"}])
    assert res.text == "read the page"  # not raised; loop continued
    payload = json.loads(captured[0]["content"])
    assert payload["error"] == "403 blocked"
    summary, n, notice, rejected = results[0]
    assert "fetch failed: 403 blocked" == summary
    assert n == 0 and notice == "403 blocked" and rejected is False  # degradation, not jail


def test_web_fetch_scrapling_missing_is_nonfatal() -> None:
    """A FetchError (scrapling CLI missing) → same graceful error path, loop continues."""
    from web_toolkit import FetchError

    def no_scrapling(url, *, selector=None):
        raise FetchError("'scrapling' not found")

    results: list[tuple] = []
    inner = _FetchClient({"url": "https://example.com/x"})
    c = ToolAugmentedClient(
        inner,
        fetch_fn=no_scrapling,
        on_tool_result=lambda sid, tool, summary, n, notice, rejected: results.append(
            (summary, n, rejected)
        ),
    )
    res = c.chat([{"role": "user", "content": "read x"}])
    assert res.text == "read the page"  # not raised
    summary, n, rejected = results[0]
    assert "fetch failed" in summary and "scrapling" in summary
    assert n == 0 and rejected is False


def test_web_fetch_in_tool_names_and_dispatched() -> None:
    """web_fetch is advertised and routed through _dispatch."""
    c = ToolAugmentedClient(_ScriptedClient(), fetch_fn=lambda url, *, selector=None: None)
    assert "web_fetch" in c._tool_names
    # Dispatch routes a web_fetch call to _run_fetch (returns a tool message).
    msg = c._dispatch("web_fetch", {"url": "https://example.com"})
    assert msg["role"] == "tool"


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
    """No workspace → file-tool schemas absent; web_search + web_fetch always present."""
    c = ToolAugmentedClient(_ScriptedClient(), search_fn=_fake_search)
    names = {s["function"]["name"] for s in c._schemas}
    assert names == {"web_search", "web_fetch"}


def test_file_tools_present_with_workspace(tmp_path) -> None:
    c = ToolAugmentedClient(_ScriptedClient(), workspace=_ws(tmp_path))
    names = {s["function"]["name"] for s in c._schemas}
    assert names == {"web_search", "web_fetch", "read_file", "write_file"}
