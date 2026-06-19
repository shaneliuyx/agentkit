"""Operator-side MEASURED run of the reference agent against a real oMLX backend.

The vendor adapters (OpenAI-compatible client + embedder) live HERE, operator
side — NOT in agentkit. The library only defines the `LLMClient` / `Embedder`
Protocols and depends on neither `openai` nor any endpoint. This script is the
"build the adapter and pass it in" half of the seam.

Run:  .venv/bin/python examples/run_measured.py
Needs: oMLX serving chat + embeddings on :8000.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from openai import OpenAI  # noqa: E402

from agentkit.types import ChatResult  # noqa: E402
from research_agent import (  # noqa: E402
    KEY_FINDINGS,
    KEY_LLM_CALLS,
    KEY_LLM_TOKENS,
    KEY_MEMORY_HITS,
    ResearchAgentConfig,
    run_all_llm_baseline,
    run_research,
)

BASE_URL = "http://localhost:8000/v1"
CHAT_MODEL = "gemma-4-26B-A4B-it-heretic-4bit"
EMBED_MODEL = "bge-m3-mlx-fp16"
ROUNDS = 8
QUESTION = (
    "How do long-horizon agents use external memory to avoid context loss "
    "across many steps?"
)


class OMLXClient:
    """Real `LLMClient` over an OpenAI-compatible oMLX endpoint. Tracks call
    count and cumulative reported tokens so the reference agent's metric dict
    picks them up via getattr."""

    def __init__(self, model: str = CHAT_MODEL, base_url: str = BASE_URL,
                 max_tokens: int = 512) -> None:
        self._c = OpenAI(base_url=base_url, api_key="local")
        self.model = model
        self.max_tokens = max_tokens
        self.n_calls = 0
        self.total_tokens = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.n_calls += 1
        kw: dict = dict(model=self.model, messages=messages,
                        temperature=0.3, max_tokens=self.max_tokens)
        if tools:
            kw["tools"] = tools
            kw["tool_choice"] = "auto"
        r = self._c.chat.completions.create(**kw)
        usage = getattr(r, "usage", None)
        tt = getattr(usage, "total_tokens", 0) or 0
        self.total_tokens += tt
        msg = r.choices[0].message
        tool_calls: list[tuple[str, dict]] = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            tool_calls.append((tc.function.name, args))
        return ChatResult(text=msg.content or "", tool_calls=tool_calls, total_tokens=tt)


class OMLXEmbedder:
    """Real `Embedder` over the oMLX embeddings endpoint (bge-m3, 1024-dim)."""

    def __init__(self, model: str = EMBED_MODEL, base_url: str = BASE_URL) -> None:
        self._c = OpenAI(base_url=base_url, api_key="local")
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        r = self._c.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in r.data]


def _timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t0


def main() -> None:
    embedder = OMLXEmbedder()
    cfg = ResearchAgentConfig(max_rounds=ROUNDS, use_memory=True)

    print(f"measured run — chat={CHAT_MODEL} embed={EMBED_MODEL} rounds={ROUNDS}")
    print("(real local inference; this takes a few minutes)\n", flush=True)

    # Fresh client per run so call/token counters are isolated.
    tiered, t_tiered = _timed(lambda: run_research(QUESTION, OMLXClient(), embedder, cfg))
    base, t_base = _timed(lambda: run_all_llm_baseline(QUESTION, OMLXClient(), cfg))
    nomem_cfg = ResearchAgentConfig(max_rounds=ROUNDS, use_memory=False)
    nomem, t_nomem = _timed(lambda: run_research(QUESTION, OMLXClient(), embedder, nomem_cfg))

    def row(name: str, d: dict, secs: float) -> None:
        print(f"{name:24} calls={d[KEY_LLM_CALLS]:>3}  tokens={d[KEY_LLM_TOKENS]:>7}  "
              f"time={secs:>6.1f}s  findings={len(d[KEY_FINDINGS]):>2}  "
              f"recall={d[KEY_MEMORY_HITS]}")

    print("-" * 80)
    row("tiered (use_memory)", tiered, t_tiered)
    row("all-LLM baseline", base, t_base)
    row("tiered (no_memory)", nomem, t_nomem)
    print("-" * 80)
    tt, bt = tiered[KEY_LLM_TOKENS], base[KEY_LLM_TOKENS]
    if bt:
        print(f"token reduction  tiered vs baseline : {100 * (bt - tt) / bt:+.1f}%")
    if t_base:
        print(f"wall-time        tiered vs baseline : {t_tiered:.1f}s vs {t_base:.1f}s "
              f"({100 * (t_base - t_tiered) / t_base:+.1f}%)")
    print(f"memory recall    with / without     : {tiered[KEY_MEMORY_HITS]} / "
          f"{nomem[KEY_MEMORY_HITS]}")
    print("\nBACKEND: real oMLX (measured — wall-time + real token usage are genuine).")


if __name__ == "__main__":
    main()
