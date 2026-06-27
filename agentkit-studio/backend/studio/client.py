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

from typing import Any, Callable

from agentkit.backends.openai_compat import _resilient, make_client
from agentkit.types import ChatResult, Message

from studio.shared_bridge import UsageReport

#: A per-call usage sink: the runner passes one that pushes a ``token`` frame
#: and feeds ``TokenAccounting``.
OnUsage = Callable[[UsageReport], None]


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
        self.retries = retries
        self._on_usage = on_usage
        self._client = make_client(base_url, api_key)
        self.n_calls = 0
        self.total_tokens = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """One completion → ``ChatResult``; emits one ``UsageReport`` per call.

        ``estimated=True`` when the endpoint omits a ``usage`` object (the CLI /
        non-reporting backend case), which flips the run's sticky ``~`` flag.
        """

        def _call() -> ChatResult:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
            }
            if tools:
                kwargs["tools"] = tools
            r = self._client.chat.completions.create(**kwargs)
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
