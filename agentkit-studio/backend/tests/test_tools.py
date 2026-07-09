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


def test_parse_inline_tool_calls_fenced_and_bare_json() -> None:
    """oMLX local models (e.g. qwen2.5-coder) emit tool calls as fenced/bare JSON,
    NOT <tag>-wrapped — the Pi/Craft run fetched 0 because the parser only matched
    tags. Fenced and bare JSON tool calls naming a registered tool must fire too."""
    from studio.tools import _parse_inline_tool_calls

    names = {"web_search", "web_fetch"}

    # qwen's actual format: ```json { "name", "arguments": {nested} } ```
    fenced = (
        '```json\n{\n  "name": "web_search",\n'
        '  "arguments": {\n    "query": "Pi agent", "results": 5\n  }\n}\n```'
    )
    assert ("web_search", {"query": "Pi agent", "results": 5}) in _parse_inline_tool_calls(
        fenced, names
    )

    # bare top-level JSON object (no fence, no tag)
    bare = '{"name": "web_fetch", "arguments": {"url": "https://x.com"}}'
    assert ("web_fetch", {"url": "https://x.com"}) in _parse_inline_tool_calls(bare, names)

    # "parameters" alias inside a plain fence
    params = '```\n{"name": "web_search", "parameters": {"query": "q2"}}\n```'
    assert ("web_search", {"query": "q2"}) in _parse_inline_tool_calls(params, names)

    # guards: unregistered tool name dropped; plain prose never false-positives
    assert _parse_inline_tool_calls('```json\n{"name":"evil","arguments":{}}\n```', names) == []
    assert _parse_inline_tool_calls("I think we should search the web for Pi.", names) == []


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
    # 3 tool-loop iterations + 1 forced synthesis call when last result has no text.
    assert inner.calls == 4


def test_search_budget_rejects_extra_searches() -> None:
    class _TwoSearches:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools=None) -> ChatResult:
            self.calls += 1
            if self.calls == 1:
                return ChatResult(text="", total_tokens=1, tool_calls=[("web_search", {"query": "one"})])
            if self.calls == 2:
                return ChatResult(text="", total_tokens=1, tool_calls=[("web_search", {"query": "two"})])
            return ChatResult(text="done", total_tokens=1)

    queries: list[str] = []
    results: list[tuple[str, str, bool]] = []

    def search(query: str, *, results: int = 5) -> list[SearchResult]:
        queries.append(query)
        return _fake_search(query, results=results)

    c = ToolAugmentedClient(
        _TwoSearches(),
        search_fn=search,
        max_searches=1,
        on_tool_result=lambda sid, tool, summary, n, notice, rejected: results.append(
            (tool, notice, rejected)
        ),
    )
    res = c.chat([{"role": "user", "content": "q"}])

    assert res.text == "done"
    assert queries == ["one"]
    assert ("web_search", "web_search search calls budget exhausted (1). Use the gathered evidence and produce the requested final answer.", True) in results


def test_successful_fetch_budget_rejects_after_success() -> None:
    class _TwoFetches:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools=None) -> ChatResult:
            self.calls += 1
            if self.calls == 1:
                return ChatResult(text="", total_tokens=1, tool_calls=[("web_fetch", {"url": "https://x.test/one"})])
            if self.calls == 2:
                return ChatResult(text="", total_tokens=1, tool_calls=[("web_fetch", {"url": "https://x.test/two"})])
            return ChatResult(text="done", total_tokens=1)

    fetched: list[str] = []
    results: list[tuple[str, str, bool]] = []

    def fetch(url: str, *, selector: str | None = None) -> FetchResult:
        fetched.append(url)
        return FetchResult(url=url, ok=True, content="page content", bytes=12)

    c = ToolAugmentedClient(
        _TwoFetches(),
        fetch_fn=fetch,
        max_successful_fetches=1,
        on_tool_result=lambda sid, tool, summary, n, notice, rejected: results.append(
            (tool, notice, rejected)
        ),
    )
    res = c.chat([{"role": "user", "content": "q"}])

    assert res.text == "done"
    assert fetched == ["https://x.test/one"]
    assert ("web_fetch", "web_fetch successful fetches budget exhausted (1). Use the gathered evidence and produce the requested final answer.", True) in results


def test_iteration_exhaustion_forces_synthesis_despite_preamble() -> None:
    """Root-cause fix: a model that emits PREAMBLE text ("I'll fetch now…") alongside
    a tool call every turn exhausts the iteration cap while still in tool_use. The old
    guard forced synthesis only on EMPTY final text, so the non-empty preamble was
    returned verbatim and ZERO findings were produced (the haiku no-op). The loop must
    now force a tools-disabled synthesis turn on iteration exhaustion regardless of the
    preamble — that is where Anthropic's post-tool-result answer is actually written."""

    class _Narrator:
        def __init__(self) -> None:
            self.calls = 0
            self.last_tools: list | None = None

        def chat(self, messages, tools=None) -> ChatResult:
            self.calls += 1
            self.last_tools = tools
            if tools:  # tool-loop turns: narrate + keep calling the tool forever
                return ChatResult(
                    text="I'll fetch the articles now to substantiate the claims.",
                    total_tokens=1,
                    tool_calls=[("web_search", {"query": "x"})],
                )
            # synthesis turn (tools disabled) → the real findings finally get written
            return ChatResult(
                text="RESEARCH_FINDING: real synthesized output",
                total_tokens=2, tool_calls=[],
            )

    inner = _Narrator()
    c = ToolAugmentedClient(inner, search_fn=_fake_search, max_iters=3)
    res = c.chat([{"role": "user", "content": "q"}])
    assert inner.calls == 4                 # 3 loop iters + 1 forced synthesis
    assert inner.last_tools == []           # synthesis turn ran with NO tools
    assert "RESEARCH_FINDING" in res.text   # findings, not the preamble
    assert "I'll fetch" not in res.text     # preamble narration replaced


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
        context_compact=False,
    )
    c.chat([{"role": "user", "content": "read big"}])
    payload = json.loads(captured[0]["content"])
    assert len(payload["content"]) == _MAX_FETCH_CHARS  # capped
    assert payload["truncated"] is True
    assert "truncated" in results[0][0] and results[0][1] == 1


def test_split_sections_deterministic() -> None:
    from studio.tools import _split_sections
    doc = "# Title\nintro\n\n## A\nbody a\n### sub\nmore\n\n## B\nbody b\n"
    secs = dict(_split_sections(doc))
    assert list(secs) == ["(intro)", "## A", "## B"]
    assert "### sub" in secs["## A"] and "more" in secs["## A"]  # nested ### stays in A
    assert "body b" not in secs["## A"]                          # B not bled into A


def test_read_artifact_returns_index_then_section(tmp_path) -> None:
    """No arg -> cheap section index (no full dump); section arg -> one section."""
    art = tmp_path / "artifact.md"
    art.write_text("# Title\nintro\n\n## Sources\nurls here\n\n## Findings\nstuff\n")

    class _Inner:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(text="", total_tokens=0)

    c = ToolAugmentedClient(_Inner(), artifact_path=art)

    idx = json.loads(c._run_read_artifact("s1", {})["content"])
    assert "index" in idx and "content" not in idx                # never dumps full doc
    assert [s["section"] for s in idx["index"]] == ["(intro)", "## Sources", "## Findings"]
    assert all("hash" in s and "chars" in s for s in idx["index"])

    sec = json.loads(c._run_read_artifact("s1", {"section": "## Sources"})["content"])
    assert sec["section"] == "## Sources" and "urls here" in sec["content"]
    assert "stuff" not in sec["content"]                          # only the one section

    bad = json.loads(c._run_read_artifact("s1", {"section": "## Nope"})["content"])
    assert "error" in bad and "## Sources" in bad["available"]


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


def test_offer_tools_restricts_advertised_schemas(tmp_path) -> None:
    """An explicit offer_tools allowlist advertises EXACTLY those tools (the editor
    pass: read_file + search_evidence + read_artifact + patch_artifact, no web/write)."""
    art = tmp_path / "artifact.md"
    art.write_text("# T\n\n## S\nbody\n")
    c = ToolAugmentedClient(
        _ScriptedClient(),
        workspace=_ws(tmp_path),
        artifact_path=art,
        offer_tools={"read_file", "search_evidence", "read_artifact", "patch_artifact"},
    )
    names = {s["function"]["name"] for s in c._schemas}
    assert names == {"read_file", "search_evidence", "read_artifact", "patch_artifact"}
    assert "web_search" not in names and "write_file" not in names


# --- read-only wandering forcing turn ---------------------------------------
# Real live evidence (agentkit-studio, gemma-4-26B-A4B-it-heretic-4bit, editor
# structural retry): given read_artifact + patch_artifact, the model called
# read_artifact 24/24 times across 3 real attempts — never once patch_artifact
# — repeatedly re-reading the same sections instead of ever committing an edit.
# The existing `_PLANNING_RE` forcing turn only fires on narration text with
# ZERO tool calls; a model that keeps calling a READ tool never triggers it.
# This closes that gap: track a streak of read-only tool calls (classified by
# tool CATEGORY via the existing WRITE_FILE_TOOL/EDIT_FILE_TOOL/PATCH_ARTIFACT_TOOL
# names, not any task keyword) and force a decision once the streak crosses
# half the configured iteration budget.

class _AlwaysReadClient:
    """Inner client that always calls a READ-only tool, never a write tool."""

    def __init__(self, read_name: str, read_args: dict) -> None:
        self._name = read_name
        self._args = read_args
        self.calls = 0
        self.prompts: list[str] = []

    def chat(self, messages, tools=None) -> ChatResult:
        self.calls += 1
        self.prompts.append(str(messages[-1].get("content", "")))
        return ChatResult(text="", total_tokens=1, tool_calls=[(self._name, self._args)])


def test_read_only_streak_injects_forcing_turn(tmp_path) -> None:
    ws = _ws(tmp_path)
    (ws.root / "notes.md").write_text("body")
    inner = _AlwaysReadClient("read_file", {"path": "notes.md"})
    c = ToolAugmentedClient(
        inner, workspace=ws, offer_tools={"read_file", "write_file"}, max_iters=6,
    )
    c.chat([{"role": "user", "content": "fix the doc"}])
    forced = [p for p in inner.prompts if "Make your decision now" in p]
    assert forced, "expected a forcing turn once the read-only streak crossed the threshold"


def test_read_only_streak_never_fires_without_a_write_tool_offered(tmp_path) -> None:
    """No write tool advertised (a pure research/read pass) -> nothing to force."""
    ws = _ws(tmp_path)
    (ws.root / "notes.md").write_text("body")
    inner = _AlwaysReadClient("read_file", {"path": "notes.md"})
    c = ToolAugmentedClient(
        inner, workspace=ws, offer_tools={"read_file"}, max_iters=6,
    )
    c.chat([{"role": "user", "content": "just read"}])
    assert not any("Make your decision now" in p for p in inner.prompts)


def test_read_only_streak_resets_once_a_write_tool_fires(tmp_path) -> None:
    """A model that writes before the threshold is never nagged."""
    ws = _ws(tmp_path)

    class _ReadThenWriteClient:
        def __init__(self) -> None:
            self.calls = 0
            self.prompts: list[str] = []

        def chat(self, messages, tools=None) -> ChatResult:
            self.calls += 1
            self.prompts.append(str(messages[-1].get("content", "")))
            if self.calls == 1:
                return ChatResult(text="", total_tokens=1, tool_calls=[("read_file", {"path": "n.md"})])
            if self.calls == 2:
                return ChatResult(
                    text="", total_tokens=1,
                    tool_calls=[("write_file", {"path": "n.md", "content": "x"})],
                )
            return ChatResult(text="done", total_tokens=1)

    inner = _ReadThenWriteClient()
    c = ToolAugmentedClient(inner, workspace=ws, offer_tools={"read_file", "write_file"}, max_iters=6)
    res = c.chat([{"role": "user", "content": "fix it"}])
    assert res.text == "done"
    assert not any("Make your decision now" in p for p in inner.prompts)


def _evidence_ws(tmp_path) -> Workspace:
    ws = Workspace("sess-ev", root=tmp_path / "ws")
    ev = ws.root / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "source-001.md").write_text(
        "URL: https://x.test/a\n\nLangGraph enables stateful multi-agent orchestration.\n"
        "It persists checkpoints between steps.\n",
        encoding="utf-8",
    )
    (ev / "source-002.md").write_text("URL: https://x.test/b\n\nNothing relevant.\n", encoding="utf-8")
    # Manifest carries NO page content — it must never be grepped.
    (ev / "fetched-sources.json").write_text('[{"url": "https://x.test/a", "stateful": true}]')
    return ws


def test_search_evidence_greps_raw_files_with_context(tmp_path) -> None:
    c = ToolAugmentedClient(_ScriptedClient(), workspace=_evidence_ws(tmp_path))
    msg = c._dispatch("search_evidence", {"query": "stateful", "context_lines": 1})
    payload = json.loads(msg["content"])
    matches = payload["matches"]
    assert len(matches) == 1
    m = matches[0]
    assert m["file"] == "evidence/source-001.md"
    assert m["line"] == 3
    assert "stateful multi-agent" in m["context"]
    # context window is small — never the whole file, and the .json manifest is skipped.
    assert "URL: https://x.test/a" not in m["context"]  # ctx=1 excludes the header line
    assert all(not mm["file"].endswith(".json") for mm in matches)


def test_search_evidence_caps_matches(tmp_path) -> None:
    ws = Workspace("sess-cap", root=tmp_path / "ws")
    ev = ws.root / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "source-001.md").write_text("\n".join(f"agents line {i}" for i in range(50)), encoding="utf-8")
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    payload = json.loads(c._dispatch("search_evidence", {"query": "agents"})["content"])
    assert len(payload["matches"]) == 10  # _MAX_EVIDENCE_MATCHES


def test_search_evidence_empty_query_and_no_workspace(tmp_path) -> None:
    no_ws = ToolAugmentedClient(_ScriptedClient())
    assert "error" in json.loads(no_ws._dispatch("search_evidence", {"query": "x"})["content"])
    c = ToolAugmentedClient(_ScriptedClient(), workspace=Workspace("s", root=tmp_path / "ws"))
    assert "error" in json.loads(c._dispatch("search_evidence", {"query": "  "})["content"])


def test_search_evidence_matches_returned_url(tmp_path) -> None:
    """Every match carries the source url parsed from the file's 'URL:' header (Bug B)."""
    c = ToolAugmentedClient(_ScriptedClient(), workspace=_evidence_ws(tmp_path))
    payload = json.loads(c._dispatch("search_evidence", {"query": "stateful"})["content"])
    assert payload["matches"]
    assert all(m["url"] == "https://x.test/a" for m in payload["matches"])


def _split_phrase_ws(tmp_path) -> Workspace:
    """Evidence where multi-word phrases are SPLIT across lines/words — the exact
    shape that Bug A's contiguous-substring match returned 0 hits on."""
    ws = Workspace("sess-split", root=tmp_path / "ws")
    ev = ws.root / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "source-001.md").write_text(
        "URL: https://paper.test/x\n\n"
        "This paper describes the methodology used.\n"
        "The overall structure and layout follow a standard form.\n"
        "\n"
        "The results are summarised below and the discussion\n"
        "that follows interprets them.\n",
        encoding="utf-8",
    )
    return ws


def test_search_evidence_multiword_matches_across_lines(tmp_path) -> None:
    """Bug A regression: previously-failing multi-word queries now return hits when
    their words are split across nearby lines, with the source url attached."""
    c = ToolAugmentedClient(_ScriptedClient(), workspace=_split_phrase_ws(tmp_path))
    for q in ("methodology and structure", "results and discussion"):
        payload = json.loads(c._dispatch("search_evidence", {"query": q})["content"])
        matches = payload["matches"]
        assert matches, f"{q!r} returned no hits"
        assert matches[0]["url"] == "https://paper.test/x"
        # The whole phrase is never on one raw line — a substring match would miss it.
        assert not any(q in ln.lower() for ln in matches[0]["context"].splitlines())


# -- edit_file ------------------------------------------------------------


def test_edit_file_unique_replace(tmp_path) -> None:
    ws = _ws(tmp_path)
    (ws.root / "doc.md").write_text("alpha beta gamma", encoding="utf-8")
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    payload = json.loads(
        c._dispatch("edit_file", {"file_path": "doc.md", "old_string": "beta", "new_string": "DELTA"})["content"]
    )
    assert payload["replacements"] == 1
    assert (ws.root / "doc.md").read_text() == "alpha DELTA gamma"


def test_edit_file_not_found(tmp_path) -> None:
    ws = _ws(tmp_path)
    (ws.root / "doc.md").write_text("hello world", encoding="utf-8")
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    payload = json.loads(
        c._dispatch("edit_file", {"file_path": "doc.md", "old_string": "absent", "new_string": "x"})["content"]
    )
    assert "error" in payload and "not found" in payload["error"]
    assert (ws.root / "doc.md").read_text() == "hello world"  # untouched


def test_edit_file_not_unique_without_replace_all(tmp_path) -> None:
    ws = _ws(tmp_path)
    (ws.root / "doc.md").write_text("a a a", encoding="utf-8")
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    payload = json.loads(
        c._dispatch("edit_file", {"file_path": "doc.md", "old_string": "a", "new_string": "b"})["content"]
    )
    assert "error" in payload and "not unique" in payload["error"]
    assert "3 occurrences" in payload["error"]
    assert (ws.root / "doc.md").read_text() == "a a a"  # untouched


def test_edit_file_replace_all(tmp_path) -> None:
    ws = _ws(tmp_path)
    (ws.root / "doc.md").write_text("a a a", encoding="utf-8")
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    payload = json.loads(
        c._dispatch(
            "edit_file",
            {"file_path": "doc.md", "old_string": "a", "new_string": "b", "replace_all": True},
        )["content"]
    )
    assert payload["replacements"] == 3
    assert (ws.root / "doc.md").read_text() == "b b b"


def test_edit_file_escape_rejected(tmp_path) -> None:
    ws = _ws(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("orig", encoding="utf-8")
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    payload = json.loads(
        c._dispatch(
            "edit_file",
            {"file_path": "../../secret.txt", "old_string": "orig", "new_string": "pwned"},
        )["content"]
    )
    assert "error" in payload and "escapes workspace" in payload["error"]
    assert outside.read_text() == "orig"  # untouched


def test_edit_file_no_workspace() -> None:
    c = ToolAugmentedClient(_ScriptedClient())
    payload = json.loads(
        c._dispatch("edit_file", {"file_path": "x", "old_string": "a", "new_string": "b"})["content"]
    )
    assert "error" in payload and "no workspace" in payload["error"]


def test_edit_file_mid_range_file_now_editable(tmp_path) -> None:
    """A 70 KiB file (over the old 64 KiB cap, under the new 100 KiB cap) is now
    editable — proving the read-side bump is meaningful, not just a dead number."""
    ws = _ws(tmp_path)
    body = "x" * (70 * 1024) + "TARGET"  # ~70 KiB, unique marker at the tail
    (ws.root / "big.md").write_text(body, encoding="utf-8")
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    payload = json.loads(
        c._dispatch("edit_file", {"file_path": "big.md", "old_string": "TARGET", "new_string": "DONE"})["content"]
    )
    assert payload["replacements"] == 1
    assert (ws.root / "big.md").read_text().endswith("DONE")


def test_edit_file_refuses_read_over_cap(tmp_path) -> None:
    """A file at/over _EDIT_FILE_MAX_BYTES is refused (truncated-read → data-loss
    guard), left untouched — the read-side guard on the studio-local threshold."""
    from studio.tools import _EDIT_FILE_MAX_BYTES
    ws = _ws(tmp_path)
    body = "a" + "b" * _EDIT_FILE_MAX_BYTES  # strictly over the cap
    (ws.root / "huge.md").write_text(body, encoding="utf-8")
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    payload = json.loads(
        c._dispatch("edit_file", {"file_path": "huge.md", "old_string": "a", "new_string": "z"})["content"]
    )
    assert "error" in payload and "too large to edit safely" in payload["error"]
    assert (ws.root / "huge.md").read_text() == body  # untouched


def test_edit_file_refuses_write_that_exceeds_cap(tmp_path) -> None:
    """An edit whose replacement inflates the file past the cap is refused before
    the write touches disk — the write-side guard on the same threshold."""
    from studio.tools import _EDIT_FILE_MAX_BYTES
    ws = _ws(tmp_path)
    body = "SEED then filler " + "c" * (60 * 1024)  # ~60 KiB, editable
    (ws.root / "grow.md").write_text(body, encoding="utf-8")
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    inflate = "Z" * (_EDIT_FILE_MAX_BYTES + 1)  # replacement blows past the cap
    payload = json.loads(
        c._dispatch("edit_file", {"file_path": "grow.md", "old_string": "SEED", "new_string": inflate})["content"]
    )
    assert "error" in payload and "exceed max file size" in payload["error"]
    assert (ws.root / "grow.md").read_text() == body  # untouched


# -- glob -----------------------------------------------------------------


def test_glob_matches_within_workspace(tmp_path) -> None:
    ws = _ws(tmp_path)
    (ws.root / "sections").mkdir()
    (ws.root / "sections" / "intro.md").write_text("x", encoding="utf-8")
    (ws.root / "sections" / "body.md").write_text("y", encoding="utf-8")
    (ws.root / "notes.txt").write_text("z", encoding="utf-8")
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    payload = json.loads(c._dispatch("glob", {"pattern": "sections/*.md"})["content"])
    assert sorted(payload["matches"]) == ["sections/body.md", "sections/intro.md"]
    assert payload["truncated"] is False
    # Recursive pattern finds the .md files anywhere but not the .txt.
    payload2 = json.loads(c._dispatch("glob", {"pattern": "**/*.md"})["content"])
    assert "notes.txt" not in payload2["matches"]
    assert "sections/intro.md" in payload2["matches"]


def test_glob_escape_rejected(tmp_path) -> None:
    ws = _ws(tmp_path)
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    payload = json.loads(c._dispatch("glob", {"pattern": "*", "path": "../.."})["content"])
    assert "error" in payload and "escapes workspace" in payload["error"]


def test_glob_caps_and_notes_truncation(tmp_path) -> None:
    ws = Workspace("sess-glob-cap", root=tmp_path / "ws")
    for i in range(120):
        (ws.root / f"f{i:03d}.md").write_text("x", encoding="utf-8")
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    payload = json.loads(c._dispatch("glob", {"pattern": "*.md"})["content"])
    assert len(payload["matches"]) == 100  # _MAX_GLOB_RESULTS
    assert payload["truncated"] is True


def test_glob_empty_pattern_and_no_workspace(tmp_path) -> None:
    c = ToolAugmentedClient(_ScriptedClient(), workspace=_ws(tmp_path))
    assert "error" in json.loads(c._dispatch("glob", {"pattern": "  "})["content"])
    no_ws = ToolAugmentedClient(_ScriptedClient())
    assert "error" in json.loads(no_ws._dispatch("glob", {"pattern": "*"})["content"])


# -- search_evidence widening ---------------------------------------------


def test_search_evidence_glob_widens_scope(tmp_path) -> None:
    """A wider glob finds a match in sections/*.md that the default evidence-only
    scope misses; the default scope stays backward compatible (no glob arg)."""
    ws = _evidence_ws(tmp_path)
    (ws.root / "sections").mkdir()
    (ws.root / "sections" / "draft.md").write_text(
        "URL: https://draft.test/s\n\nQuantum entanglement is spooky.\n", encoding="utf-8"
    )
    c = ToolAugmentedClient(_ScriptedClient(), workspace=ws)
    # Default scope (evidence/*.md) does not see the sections file.
    default = json.loads(c._dispatch("search_evidence", {"query": "entanglement"})["content"])
    assert default["matches"] == []
    # Widened scope finds it, with the file path and source url attached.
    wide = json.loads(
        c._dispatch("search_evidence", {"query": "entanglement", "glob": "**/*.md"})["content"]
    )
    assert wide["matches"]
    assert wide["matches"][0]["file"] == "sections/draft.md"
    assert wide["matches"][0]["url"] == "https://draft.test/s"


# --- fabricated-citation grounding forcing turn ------------------------------
# Real live evidence (agentkit-studio runs 1523/1524, gemma-4-26B): research
# spokes with web_search/web_fetch offered emitted their FINAL answer on turn 1
# — a patch citing a plausible URL they never fetched — with ZERO tool calls.
# The narration forcing turn never fires (the text is a well-formed answer, not
# a plan) and grounding later drops every invented URL, leaving an uncited
# shallow report and no evidence/ dossier. The loop must force ONE research
# round when a would-be final answer cites URLs while web_fetch_success == 0.


class _FabricatingClient:
    """Emits a URL-citing final on turn 1; searches only if pushed back."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    def chat(self, messages, tools=None) -> ChatResult:
        self.calls += 1
        self.prompts.append(str(messages[-1].get("content", "")))
        if self.calls == 1:
            return ChatResult(
                text=("RESEARCH_FINDING:\nURL: https://www.confluent.io/blog/kafka-vs-pulsar/\n"
                      "QUOTE: Kafka is faster."),
                total_tokens=5,
            )
        if self.calls == 2:
            return ChatResult(
                text="", total_tokens=5,
                tool_calls=[("web_search", {"query": "kafka vs pulsar"})],
            )
        return ChatResult(
            text="RESEARCH_FINDING:\nURL: https://x.test/q3\nQUOTE: released 2025.",
            total_tokens=5,
        )


def test_fabricated_citation_forces_one_research_round() -> None:
    inner = _FabricatingClient()
    c = ToolAugmentedClient(inner, search_fn=_fake_search, max_iters=6)
    res = c.chat([{"role": "user", "content": "research kafka vs pulsar"}])
    forced = [p for p in inner.prompts if "never fetched" in p]
    assert forced, "expected the grounding forcing turn after a URL-citing zero-fetch final"
    assert inner.calls >= 3          # final -> forcing turn -> search -> re-final
    assert "x.test" in res.text      # the re-emitted final is the accepted answer


def test_fabricated_citation_forcing_fires_at_most_once() -> None:
    """A model that STILL fabricates after the push-back is accepted (fail-open),
    not looped forever."""

    class _Stubborn(_FabricatingClient):
        def chat(self, messages, tools=None) -> ChatResult:
            self.calls += 1
            self.prompts.append(str(messages[-1].get("content", "")))
            return ChatResult(
                text="URL: https://made.up/page\nQUOTE: whatever.", total_tokens=5,
            )

    inner = _Stubborn()
    c = ToolAugmentedClient(inner, search_fn=_fake_search, max_iters=6)
    res = c.chat([{"role": "user", "content": "research"}])
    assert inner.calls == 2          # final -> forcing turn -> same final, accepted
    assert "made.up" in res.text


def test_no_forcing_when_answer_cites_nothing() -> None:
    """A URL-free final answer (legit non-research reply) is accepted on turn 1."""

    class _Plain(_FabricatingClient):
        def chat(self, messages, tools=None) -> ChatResult:
            self.calls += 1
            self.prompts.append(str(messages[-1].get("content", "")))
            return ChatResult(text="No relevant findings.", total_tokens=5)

    inner = _Plain()
    c = ToolAugmentedClient(inner, search_fn=_fake_search, max_iters=6)
    res = c.chat([{"role": "user", "content": "research"}])
    assert inner.calls == 1
    assert res.text == "No relevant findings."


# --- auto-fetch of top search results (weak-model grounding backstop) --------
# Live runs 1523-1525 (gemma): 21 searches, 1 fetch, 0 surviving citations —
# gemma searches but then fabricates URLs instead of fetching results, so
# grounding drops every citation and the evidence/ dossier stays empty. With
# auto_fetch_top_results > 0 the loop fetches the top result pages itself and
# splices their content into the search tool message.


class _SearchThenFinal:
    """Turn 1: web_search. Turn 2: final answer citing the fetched URL."""

    def __init__(self) -> None:
        self.calls = 0
        self.tool_msgs: list[str] = []

    def chat(self, messages, tools=None) -> ChatResult:
        self.calls += 1
        for m in messages:
            if m.get("role") == "tool":
                self.tool_msgs.append(str(m.get("content", "")))
        if self.calls == 1:
            return ChatResult(
                text="", total_tokens=1,
                tool_calls=[("web_search", {"query": "pi agent harness"})],
            )
        return ChatResult(
            text="RESEARCH_FINDING:\nURL: https://x.test/q3\nQUOTE: released 2025.",
            total_tokens=1,
        )


def test_auto_fetch_top_results_fetches_and_splices_content() -> None:
    fetched: list[str] = []

    def fetch(url: str, *, selector: str | None = None):
        from web_toolkit import FetchResult
        fetched.append(url)
        return FetchResult(url=url, ok=True, content=f"real page text from {url}", bytes=30)

    inner = _SearchThenFinal()
    c = ToolAugmentedClient(
        inner, search_fn=_fake_search, fetch_fn=fetch,
        max_iters=6, auto_fetch_top_results=2, max_successful_fetches=6,
    )
    c.chat([{"role": "user", "content": "research pi"}])
    # both top results fetched deterministically, no model cooperation needed
    assert fetched == ["https://x.test/q3", "https://x.test/blog"]
    # the search tool message the model sees carries the fetched page content
    assert any("fetched_pages" in m and "real page text" in m for m in inner.tool_msgs)


def test_auto_fetch_respects_fetch_budget() -> None:
    fetched: list[str] = []

    def fetch(url: str, *, selector: str | None = None):
        from web_toolkit import FetchResult
        fetched.append(url)
        return FetchResult(url=url, ok=True, content="body", bytes=4)

    inner = _SearchThenFinal()
    c = ToolAugmentedClient(
        inner, search_fn=_fake_search, fetch_fn=fetch,
        max_iters=6, auto_fetch_top_results=2, max_successful_fetches=1,
    )
    c.chat([{"role": "user", "content": "research"}])
    assert fetched == ["https://x.test/q3"]  # budget 1 → second result not fetched


def test_auto_fetch_off_by_default_and_fail_open() -> None:
    def broken_fetch(url: str, *, selector: str | None = None):
        raise RuntimeError("network down")

    inner = _SearchThenFinal()
    c = ToolAugmentedClient(inner, search_fn=_fake_search, fetch_fn=broken_fetch, max_iters=6)
    res = c.chat([{"role": "user", "content": "research"}])
    assert "x.test" in res.text  # default 0 → no auto-fetch, loop unaffected

    inner2 = _SearchThenFinal()
    c2 = ToolAugmentedClient(
        inner2, search_fn=_fake_search, fetch_fn=broken_fetch,
        max_iters=6, auto_fetch_top_results=2,
    )
    res2 = c2.chat([{"role": "user", "content": "research"}])
    assert res2.text  # failing fetches never break the loop


def test_auto_fetch_page_slice_is_configurable_for_citation_grade_content() -> None:
    """A triage-sized slice (old hardcoded 2500) forces paraphrased quotes when
    the quotable sentence sits deeper in the article; the caller sizes the slice
    to the model's section window so verbatim quoting is possible."""
    long_page = "x" * 9_000 + " THE QUOTABLE SENTENCE " + "y" * 1_000

    def fetch(url: str, *, selector: str | None = None):
        from web_toolkit import FetchResult
        return FetchResult(url=url, ok=True, content=long_page, bytes=len(long_page))

    inner = _SearchThenFinal()
    c = ToolAugmentedClient(
        inner, search_fn=_fake_search, fetch_fn=fetch,
        max_iters=6, auto_fetch_top_results=1, auto_fetch_page_chars=12_000,
    )
    c.chat([{"role": "user", "content": "research"}])
    assert any("THE QUOTABLE SENTENCE" in m for m in inner.tool_msgs)

    inner2 = _SearchThenFinal()
    c2 = ToolAugmentedClient(
        inner2, search_fn=_fake_search, fetch_fn=fetch,
        max_iters=6, auto_fetch_top_results=1, auto_fetch_page_chars=2_500,
    )
    c2.chat([{"role": "user", "content": "research"}])
    assert not any("THE QUOTABLE SENTENCE" in m for m in inner2.tool_msgs)


# --- PDF source support (fetch a PDF and read it like a README) --------------

def test_fetch_page_routes_pdf_suffix_to_extractor(monkeypatch) -> None:
    import studio.tools as tools
    monkeypatch.setattr(tools, "_fetch_pdf_text", lambda u: "extracted pdf body about agents")
    _key, page = tools._fetch_page("https://x.test/paper.pdf")
    assert page is not None and page[0] == "extracted pdf body about agents"


def test_fetch_page_routes_arxiv_pdf_path(monkeypatch) -> None:
    # arXiv PDFs are served at /pdf/<id> with NO .pdf suffix — still routed.
    import studio.tools as tools
    monkeypatch.setattr(tools, "_fetch_pdf_text", lambda u: "arxiv paper text")
    _key, page = tools._fetch_page("https://arxiv.org/pdf/1706.03762")
    assert page is not None and page[0] == "arxiv paper text"


def test_fetch_page_pdf_url_not_actually_pdf_falls_through_to_html(monkeypatch) -> None:
    import studio.tools as tools
    import types
    import web_toolkit
    monkeypatch.setattr(tools, "_fetch_pdf_text", lambda u: None)  # not a real PDF
    monkeypatch.setattr(
        web_toolkit, "web_fetch",
        lambda u, selector=None: types.SimpleNamespace(ok=True, content="real html body", bytes=14),
    )
    _key, page = tools._fetch_page("https://x.test/report.pdf")
    assert page is not None and page[0] == "real html body"


def test_fetch_pdf_text_rejects_non_pdf_bytes(monkeypatch) -> None:
    import studio.tools as tools

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return b"<html>not a pdf</html>"

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    assert tools._fetch_pdf_text("https://x.test/fake.pdf") is None
