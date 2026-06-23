"""studio.tools — ToolAugmentedClient: a tool loop over any LLMClient.

The architecture constraint (SPEC §5.2 / topology.dynamic): ``run_plan``'s
runners call ``client.chat()`` ONCE with no tool loop. To give a phase real tool
capability without touching ``run_plan``, ``ToolAugmentedClient`` SATISFIES
``agentkit.types.LLMClient`` and WRAPS an inner client (a ``StudioChatClient``):

  - It registers tool schemas (OpenAI tool format): ``web_search`` and
    ``web_fetch`` (built from ``web_toolkit``) plus ``read_file`` / ``write_file``
    confined to a per-session :class:`~studio.workspace.Workspace` (realpath jail).
  - ``.chat(messages, tools=None)`` merges those schemas into ``tools``, calls
    the inner client, and if the result carries a registered tool_call, executes
    it, appends a tool-result message, and re-calls — looping until no tool_call
    or a max-iter cap. Tokens accumulate across iterations.
  - It fires ``tool_call`` / ``tool_result`` events through injected callbacks
    (same pattern as ``on_usage``), carrying the current ``step_id``.

web_search degrades per web_toolkit precedence (SearXNG → Tavily → DDG); a
``SearchError`` is non-fatal (empty result + notice). web_fetch reads a page to
clean markdown; a missing scrapling CLI (``FetchError``) or a per-page failure
(``ok=False`` for 404/blocked) is non-fatal (error result + notice), and the
content is capped at ``_MAX_FETCH_CHARS`` so one huge page cannot flood context.
File tools are jailed: a path escaping the workspace returns an error result + a
notice, never raising, never a raw ``open()`` outside the workspace.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from agentkit.types import ChatResult, Message

from studio.workspace import Workspace, WorkspaceError

#: Default number of web results to request per tool call.
_DEFAULT_RESULTS = 5
#: Hard cap on tool-loop iterations so a misbehaving model cannot loop forever.
_MAX_TOOL_ITERS = 3
#: Ceiling on fetched page content (chars ≈ bytes for ASCII-dominant markdown) so
#: a huge page cannot flood the model's context; truncation is noted in the summary.
_MAX_FETCH_CHARS = 16 * 1024

#: The OpenAI tool schema advertised to the model.
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for up-to-date information. Returns ranked results "
            "with title, url, and snippet. Use for facts you are unsure about or "
            "that may have changed recently."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "results": {
                    "type": "integer",
                    "description": f"Max results (default {_DEFAULT_RESULTS}).",
                },
            },
            "required": ["query"],
        },
    },
}

#: Fetch a web page's main text as clean markdown (peer to web_search).
WEB_FETCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Fetch a web page's main text content as clean markdown (use after "
            "web_search to read a result)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL (include https://)."},
                "selector": {
                    "type": "string",
                    "description": "Optional CSS selector to extract only a region.",
                },
            },
            "required": ["url"],
        },
    },
}

#: Read a file from the per-session workspace (jailed).
READ_FILE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file from your workspace. The path is relative to "
            "the workspace; paths escaping it are rejected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path."},
            },
            "required": ["path"],
        },
    },
}

#: Write a file into the per-session workspace (jailed, side-effecting).
WRITE_FILE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Write a UTF-8 text file into your workspace, creating parent "
            "directories as needed. The path is relative to the workspace; paths "
            "escaping it are rejected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path."},
                "content": {"type": "string", "description": "File contents to write."},
            },
            "required": ["path", "content"],
        },
    },
}

#: Callbacks: (step_id, tool, args) and (step_id, tool, summary, n_results, notice).
OnToolCall = Callable[[str, str, dict[str, Any]], None]
OnToolResult = Callable[[str, str, str, int, str, bool], None]


def web_toolkit_available() -> bool:
    """True iff ``web_toolkit.web_search`` can be imported (tools-enabled gate)."""
    try:
        from web_toolkit import web_search  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


#: Backends without OpenAI structured function-calling (e.g. oMLX/Qwen) emit the
#: call as inline text in a <execute>/<tools>/<tool_call> tag instead of a
#: `tool_calls` field. Parse that so tools fire on those backends too — otherwise
#: the call blob silently leaks into the answer and the tool never runs.
_INLINE_TOOL_RE = re.compile(r"<(execute|tools?|tool_call)>(.*?)</\1>", re.DOTALL)


def _parse_inline_tool_calls(
    text: str, names: set[str]
) -> list[tuple[str, dict[str, Any]]]:
    """Extract ``(name, args)`` calls a model emitted as inline tagged JSON.

    Matches paired ``<execute|tools|tool_call>{...}</...>`` blocks, json-parses the
    inner object, and keeps only calls naming a registered tool. Returns ``[]`` on
    any parse miss — an unparseable blob must not break the loop.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    for _tag, inner in _INLINE_TOOL_RE.findall(text):
        try:
            obj = json.loads(inner.strip())
        except Exception:  # noqa: BLE001 - a non-JSON blob is simply not a tool call
            continue
        name = obj.get("name")
        args = obj.get("arguments", obj.get("parameters", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:  # noqa: BLE001
                args = {}
        if name in names and isinstance(args, dict):
            out.append((name, args))
    return out


class ToolAugmentedClient:
    """An ``LLMClient`` that runs a ``web_search`` tool loop over an inner client.

    ``search_fn`` / ``fetch_fn`` are injectable (tests pass stubs so NO network is
    hit); the defaults lazily import ``web_toolkit.web_search`` / ``web_fetch``.
    ``step_id`` is read at call time via ``step_id_getter`` so tool events carry
    the live phase id.
    """

    def __init__(
        self,
        inner: Any,
        *,
        on_tool_call: OnToolCall | None = None,
        on_tool_result: OnToolResult | None = None,
        step_id_getter: Callable[[], str] | None = None,
        search_fn: Callable[..., list[Any]] | None = None,
        fetch_fn: Callable[..., Any] | None = None,
        workspace: Workspace | None = None,
        max_iters: int = _MAX_TOOL_ITERS,
    ) -> None:
        self._inner = inner
        self._on_tool_call = on_tool_call
        self._on_tool_result = on_tool_result
        self._step_id_getter = step_id_getter or (lambda: "")
        self._search_fn = search_fn
        self._fetch_fn = fetch_fn
        self._workspace = workspace
        self._max_iters = max_iters

    @property
    def _schemas(self) -> list[dict[str, Any]]:
        """The tool schemas advertised this run. File tools appear only when a
        workspace is wired (no workspace → no file tools offered)."""
        schemas = [WEB_SEARCH_TOOL, WEB_FETCH_TOOL]
        if self._workspace is not None:
            schemas += [READ_FILE_TOOL, WRITE_FILE_TOOL]
        return schemas

    @property
    def _tool_names(self) -> set[str]:
        return {s["function"]["name"] for s in self._schemas}

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """Run the inner client with web_search available, looping on tool_calls.

        Accumulates ``total_tokens`` across every inner call; returns the final
        assistant ChatResult once the model stops calling the tool (or the cap is
        hit). The web_search schema is merged into any caller-supplied ``tools``.
        """
        merged_tools = list(tools or []) + self._schemas
        names = self._tool_names
        convo = list(messages)
        total_tokens = 0
        last: ChatResult | None = None
        call_seq = 0  # globally-unique, valid-char tool_call ids across the chat

        for _ in range(self._max_iters):
            result = self._inner.chat(convo, tools=merged_tools)
            total_tokens += getattr(result, "total_tokens", 0) or 0
            last = result

            tool_calls = [
                (name, args)
                for (name, args) in (getattr(result, "tool_calls", None) or [])
                if name in names
            ]
            if not tool_calls:
                # Backends without structured function-calling (oMLX/Qwen) emit the
                # call as inline tagged text — parse it so tools still fire.
                tool_calls = _parse_inline_tool_calls(result.text or "", names)
            if not tool_calls:
                break

            # Echo the assistant turn WITH its tool_calls, then each result keyed to
            # a matching tool_call_id. agentkit's ChatResult drops the original id,
            # so we synthesize a clean one (`call_N`): a tool_result's id MUST match
            # ^[a-zA-Z0-9_-]+$ or Anthropic-backed endpoints (VibeProxy→Claude) 400.
            assistant_calls: list[dict[str, Any]] = []
            tool_messages: list[Message] = []
            for name, args in tool_calls:
                cid = f"call_{call_seq}"
                call_seq += 1
                assistant_calls.append(
                    {
                        "id": cid,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                )
                msg = self._dispatch(name, args)
                msg["tool_call_id"] = cid
                msg.pop("name", None)  # OpenAI tool msg keys on tool_call_id, not name
                tool_messages.append(msg)
            convo.append(
                {"role": "assistant", "content": result.text or "", "tool_calls": assistant_calls}
            )
            convo.extend(tool_messages)

        text = (last.text if last else "") or ""
        remaining = list(last.tool_calls) if last else []
        # Strip executed tool calls from the surfaced result.
        remaining = [(n, a) for (n, a) in remaining if n not in names]
        return ChatResult(text=text, total_tokens=total_tokens, tool_calls=remaining)

    # -- dispatch ----------------------------------------------------------

    def _dispatch(self, name: str, args: dict[str, Any]) -> Message:
        """Route a tool call to its executor; emit events; return the tool msg."""
        step_id = self._step_id_getter()
        if self._on_tool_call:
            self._on_tool_call(step_id, name, dict(args))
        if name == "web_search":
            return self._run_search(step_id, args)
        if name == "web_fetch":
            return self._run_fetch(step_id, args)
        if name == "read_file":
            return self._run_read(step_id, args)
        if name == "write_file":
            return self._run_write(step_id, args)
        # Unknown tool: report it back so the model can recover.
        return self._tool_message(name, {"error": f"unknown tool {name!r}"})

    def _emit_result(
        self,
        step_id: str,
        name: str,
        summary: str,
        n: int,
        notice: str,
        rejected: bool = False,
    ) -> None:
        if self._on_tool_result:
            self._on_tool_result(step_id, name, summary, n, notice, rejected)

    @staticmethod
    def _tool_message(name: str, payload: dict[str, Any]) -> Message:
        return {"role": "tool", "name": name, "content": json.dumps(payload, default=str)}

    # -- web_search --------------------------------------------------------

    def _run_search(self, step_id: str, args: dict[str, Any]) -> Message:
        """Execute one web_search, emit a result event, return the tool message."""
        query = str(args.get("query", "")).strip()
        n = int(args.get("results", _DEFAULT_RESULTS) or _DEFAULT_RESULTS)
        results, notice = self._search(query, n)
        payload = [r.to_dict() if hasattr(r, "to_dict") else r for r in results]
        summary = "; ".join(
            f"{p.get('title', '')} ({p.get('url', '')})" for p in payload[:3]
        )
        self._emit_result(step_id, "web_search", summary, len(payload), notice)
        return self._tool_message("web_search", {"results": payload, "notice": notice})

    # -- web_fetch ---------------------------------------------------------

    def _run_fetch(self, step_id: str, args: dict[str, Any]) -> Message:
        """Fetch one page to markdown, emit a result event, return the tool message.

        A missing scrapling CLI or a per-page failure (ok=False) is non-fatal: the
        tool message carries an ``error`` and the event a ``notice``, so the loop
        continues. ``rejected`` stays False — a fetch failure is degradation, not a
        jail rejection. Content is capped at ``_MAX_FETCH_CHARS`` (truncation noted).
        """
        url = str(args.get("url", "")).strip()
        selector = args.get("selector")
        selector = str(selector).strip() if selector else None
        content, n_bytes, error = self._fetch(url, selector)
        if error:
            self._emit_result(step_id, "web_fetch", f"fetch failed: {error}", 0, error)
            return self._tool_message("web_fetch", {"url": url, "error": error})
        truncated = len(content) > _MAX_FETCH_CHARS
        if truncated:
            content = content[:_MAX_FETCH_CHARS]
        host = _host_of(url)
        summary = f"fetched {_fmt_bytes(n_bytes)} from {host}"
        if truncated:
            summary += f" (truncated to {_fmt_bytes(_MAX_FETCH_CHARS)})"
        self._emit_result(step_id, "web_fetch", summary, 1, "")
        return self._tool_message(
            "web_fetch", {"url": url, "content": content, "bytes": n_bytes, "truncated": truncated}
        )

    def _fetch(self, url: str, selector: str | None) -> tuple[str, int, str]:
        """Run the injected fetch_fn (or web_toolkit.web_fetch); never raises.

        Returns ``(content, bytes, error)``. A ``FetchError`` (scrapling missing),
        an import failure, any other exception, or a ``FetchResult`` with
        ``ok=False`` all collapse to ``("", 0, "<reason>")`` so the loop continues.
        """
        fn = self._fetch_fn
        if fn is None:
            try:
                from web_toolkit import web_fetch as fn  # type: ignore
            except Exception as exc:  # noqa: BLE001
                return "", 0, f"web_fetch unavailable: {exc}"
        try:
            res = fn(url, selector=selector)
        except Exception as exc:  # noqa: BLE001 - FetchError (scrapling missing) / backend down
            return "", 0, f"web_fetch degraded: {exc}"
        if not getattr(res, "ok", False):
            return "", 0, getattr(res, "error", "") or "fetch failed"
        content = getattr(res, "content", "") or ""
        n_bytes = getattr(res, "bytes", 0) or len(content.encode("utf-8"))
        return content, n_bytes, ""

    # -- file tools (jailed) -----------------------------------------------

    def _run_read(self, step_id: str, args: dict[str, Any]) -> Message:
        """read_file inside the workspace jail; an escape → error result + notice."""
        path = str(args.get("path", ""))
        if self._workspace is None:
            self._emit_result(step_id, "read_file", "no workspace", 0, "tools disabled")
            return self._tool_message("read_file", {"error": "no workspace configured"})
        try:
            text, n_bytes = self._workspace.read(path)
        except WorkspaceError as exc:
            self._emit_result(
                step_id, "read_file", f"rejected: {exc}", 0, str(exc), rejected=True
            )
            return self._tool_message("read_file", {"error": str(exc)})
        summary = f"read {_fmt_bytes(n_bytes)} from {path}"
        self._emit_result(step_id, "read_file", summary, 1, "")
        return self._tool_message("read_file", {"path": path, "content": text, "bytes": n_bytes})

    def _run_write(self, step_id: str, args: dict[str, Any]) -> Message:
        """write_file inside the workspace jail; an escape → error result + notice.

        The containment check runs before any byte is written, so a rejected
        write leaves the filesystem untouched.
        """
        path = str(args.get("path", ""))
        content = str(args.get("content", ""))
        if self._workspace is None:
            self._emit_result(step_id, "write_file", "no workspace", 0, "tools disabled")
            return self._tool_message("write_file", {"error": "no workspace configured"})
        try:
            n_bytes, shown = self._workspace.write(path, content)
        except WorkspaceError as exc:
            self._emit_result(
                step_id, "write_file", f"rejected: {exc}", 0, str(exc), rejected=True
            )
            return self._tool_message("write_file", {"error": str(exc)})
        summary = f"wrote {_fmt_bytes(n_bytes)} to {shown}"
        self._emit_result(step_id, "write_file", summary, 1, "")
        return self._tool_message("write_file", {"path": shown, "bytes": n_bytes})

    def _search(self, query: str, n: int) -> tuple[list[Any], str]:
        """Run the injected search_fn (or web_toolkit.web_search); never raises.

        Returns ``(results, notice)``. A ``SearchError`` / import failure yields
        ``([], "<reason>")`` so the tool loop continues with an empty result.
        """
        fn = self._search_fn
        if fn is None:
            try:
                from web_toolkit import web_search as fn  # type: ignore
            except Exception as exc:  # noqa: BLE001
                return [], f"web_search unavailable: {exc}"
        try:
            return list(fn(query, results=n)), ""
        except Exception as exc:  # noqa: BLE001 - SearchError / backend down
            return [], f"web_search degraded: {exc}"


def _fmt_bytes(n: int) -> str:
    """Human-readable byte count for tool-result summaries (e.g. '1.2KB')."""
    if n < 1024:
        return f"{n}B"
    return f"{n / 1024:.1f}KB"


def _host_of(url: str) -> str:
    """Host portion of a URL for tool-result summaries (full url stays out of the
    surfaced summary). Falls back to the raw string if it does not parse."""
    from urllib.parse import urlparse

    try:
        return urlparse(url).netloc or url
    except Exception:  # noqa: BLE001
        return url
