"""studio.client — usage-capturing LLMClient wrappers (SPEC §5.1).

agentkit's ``OpenAIChatClient.chat`` reads only ``r.usage.total_tokens`` and
DISCARDS the prompt/completion split. The Studio token HUD needs the in/out
split, so ``StudioChatClient`` wraps the same raw ``openai`` client and captures
``prompt_tokens`` / ``completion_tokens``, pushing each call's usage to an
injected ``on_usage`` callback as a ``UsageReport``.

Honesty (SPEC §7): when the endpoint returns no ``usage`` object, the report is
``estimated=True`` — the runner's ``TokenAccounting`` makes that flag sticky for
the whole run, so the meter never renders an estimate as exact.

Both clients satisfy ``agentkit.types.LLMClient`` (``isinstance`` check passes),
so the runner can hand them straight to ``run_plan`` unchanged.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from agentkit.backends.openai_compat import _resilient, make_client
from agentkit.types import ChatResult, Message

from studio.shared_bridge import UsageReport

#: A per-call usage sink: the runner passes one that pushes a ``token`` frame
#: and feeds ``TokenAccounting``.
OnUsage = Callable[[UsageReport], None]


# ── deterministic context compaction (agentkit.context.compactor) ────────────
#: Compact only past this serialized size — small calls (planner, reducer,
#: judge single-shots) pass through untouched.
_COMPACT_MAX_CHARS = int(os.getenv("STUDIO_COMPACT_MAX_CHARS", "100000"))
#: Verbatim-tail budget. Deliberately generous: recent tool results carry the
#: fetched-page content the model must QUOTE for the citation contract — the
#: compactor's one-line transcript would destroy it.
_COMPACT_TAIL_CHARS = int(os.getenv("STUDIO_COMPACT_TAIL_CHARS", "50000"))


def _msg_chars(m: Message) -> int:
    """Approximate context cost of one message (content + tool_calls payload)."""
    n = len(str(m.get("content") or ""))
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function") if isinstance(tc, dict) else None
        if isinstance(fn, dict):
            n += len(str(fn.get("name") or "")) + len(str(fn.get("arguments") or ""))
    return n + 16  # role + framing overhead


def compact_messages(messages: list[Message], *,
                     max_chars: int = 0, tail_chars: int = 0) -> list[Message]:
    """Deterministically compact an oversized conversation before an LLM call.

    ``agentkit.context.compactor.compact`` cuts at the last ``keep`` USER turns,
    which no-ops on a tool loop (one user message, then assistant/tool
    alternation) — exactly the conversations that balloon. So the cut here is
    ours: keep leading system messages + the first user message (the
    assignment) + a verbatim recent tail sized by ``tail_chars``; summarize the
    middle with ``compact(head, keep=0)``. The tail never starts on a ``tool``
    message (an orphaned tool result is an API 400 — it must follow the
    assistant message carrying its tool_calls).
    """
    max_chars = max_chars or _COMPACT_MAX_CHARS
    tail_chars = tail_chars or _COMPACT_TAIL_CHARS
    if sum(_msg_chars(m) for m in messages) <= max_chars:
        return messages

    prefix_end = 0
    while prefix_end < len(messages) and messages[prefix_end].get("role") == "system":
        prefix_end += 1
    if prefix_end < len(messages) and messages[prefix_end].get("role") == "user":
        prefix_end += 1

    # walk the tail backward within budget, then pair-safety: never start on 'tool'
    start = len(messages)
    budget = tail_chars
    while start > prefix_end and budget - _msg_chars(messages[start - 1]) >= 0:
        start -= 1
        budget -= _msg_chars(messages[start])
    while (
        start > prefix_end
        and start < len(messages)
        and messages[start].get("role") == "tool"
    ):
        start -= 1
    if start <= prefix_end:  # tail swallowed everything → nothing left to summarize
        return messages

    try:
        from agentkit.context.compactor import compact

        summary = compact(list(messages[prefix_end:start]), keep=0).text
    except Exception:  # noqa: BLE001 — compaction is an optimization, never a crash
        return messages
    bridge: Message = {
        "role": "user",
        "content": "[Earlier turns compacted deterministically — summary]\n" + summary,
    }
    return list(messages[:prefix_end]) + [bridge] + list(messages[start:])


class StudioChatClient:
    """OpenAI-compatible ``LLMClient`` that reports the in/out token split.

    Construct from a resolved ``(base_url, model, api_key)`` and an ``on_usage``
    callback. Mirrors ``OpenAIChatClient`` (resilient retry, ``n_calls`` /
    ``total_tokens`` attrs) but additionally captures prompt/completion tokens.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str | None,
        api_key: str | None,
        on_usage: OnUsage,
        temperature: float = 0.0,
        timeout_s: float | None = None,
        # 7 retries (~4 min with backoff 2.0) instead of 4 (~30s): a research run
        # fans out many concurrent phase calls, and a transient upstream 503 lasting
        # longer than 30s would otherwise FAIL that phase (losing its research) rather
        # than recover. Verified the 503s are transient capacity, not a request bug:
        # small/large/concurrent calls all succeed. A failed phase, not the scorer, was
        # capping the score — so surviving the hiccup is the real fix.
        retries: int = 7,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.retries = int(os.getenv("STUDIO_LLM_RETRIES", str(retries)))
        # 240s, not 90: a legitimate full-budget completion (8192 tokens at the
        # measured ~112 tok/s through VibeProxy) takes ~73s of decode alone; add
        # prefill on a 50K+ spoke prompt and honest requests cross 90s. A 90s
        # read timeout then aborts work that WOULD finish, and the retry ladder
        # regenerates from scratch — the run grinds forever (2026-07-04 haiku
        # leg: 8 spokes × ~21 aborted attempts, hours of zero progress).
        self.timeout_s = timeout_s if timeout_s is not None else float(
            os.getenv("STUDIO_LLM_TIMEOUT_S", "240")
        )
        self._on_usage = on_usage
        self._client = make_client(base_url, api_key)
        self.n_calls = 0
        self.total_tokens = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """One completion → ``ChatResult``; emits one ``UsageReport`` per call.

        ``estimated=True`` when the endpoint omits a ``usage`` object (the CLI /
        non-reporting backend case), which flips the run's sticky ``~`` flag.

        ``max_tokens`` caps the completion; pass it sized to the output (the
        reducer that reproduces a large artifact must raise it above the API
        default or the document truncates mid-sentence — §11.10).
        """
        messages = compact_messages(messages)

        def _call() -> ChatResult:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
            }
            # Default a generous completion cap: the API default is low enough to
            # truncate a large reduced artifact mid-sentence (§11.10). 8192 fits a
            # ~32K-char document; a larger one needs the patch-based reducer (a
            # section edit is small) rather than full regeneration.
            kwargs["max_tokens"] = max_tokens if max_tokens is not None else 8192
            if tools:
                kwargs["tools"] = tools
            # max_retries=0: _resilient below owns retry policy. openai-python's
            # default (2 internal retries) MULTIPLIES with our ladder — 7 × 3
            # socket attempts = 21 × timeout worst case per chat() call.
            r = self._client.with_options(
                timeout=self.timeout_s, max_retries=0
            ).chat.completions.create(**kwargs)
            message = r.choices[0].message
            text = (getattr(message, "content", None) or "").strip()
            usage = getattr(r, "usage", None)
            inp = (getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
            out = (getattr(usage, "completion_tokens", 0) or 0) if usage else 0
            total = (getattr(usage, "total_tokens", 0) or 0) if usage else (inp + out)
            self.n_calls += 1
            self.total_tokens += total
            self._on_usage(
                UsageReport(input_tokens=inp, output_tokens=out, estimated=usage is None)
            )
            return ChatResult(
                text=text,
                total_tokens=total,
                tool_calls=_extract_tool_calls(message),
            )

        return _resilient(_call, retries=self.retries)


class MaxTokensClient:
    """LLMClient wrapper that caps completion size for structured control calls."""

    def __init__(self, inner: Any, max_tokens: int | None) -> None:
        self._inner = inner
        self.max_tokens = max_tokens

    @property
    def n_calls(self) -> int:
        return int(getattr(self._inner, "n_calls", 0) or 0)

    @property
    def total_tokens(self) -> int:
        return int(getattr(self._inner, "total_tokens", 0) or 0)

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        if self.max_tokens is None:
            return self._inner.chat(messages, tools=tools)
        try:
            return self._inner.chat(messages, tools=tools, max_tokens=self.max_tokens)
        except TypeError:
            return self._inner.chat(messages, tools=tools)


def _extract_tool_calls(message: Any) -> list[tuple[str, dict[str, Any]]]:
    """Map native OpenAI ``tool_calls`` → agentkit ``[(name, args), ...]``.

    Mirrors ``openai_compat._extract_tool_calls`` (kept local to avoid importing
    a private symbol). Returns ``[]`` for the common plain-text case.
    """
    import json

    raw = getattr(message, "tool_calls", None) or []
    calls: list[tuple[str, dict[str, Any]]] = []
    for tc in raw:
        fn = getattr(tc, "function", None)
        if fn is None:
            continue
        name = getattr(fn, "name", "") or ""
        args_raw = getattr(fn, "arguments", "") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
        except (ValueError, TypeError):
            args = {}
        if name:
            calls.append((name, args))
    return calls
