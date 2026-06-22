"""Real-LLM end-to-end for Phase 8 — dynamic per-step topology.

A multi-part task is planned, each step is assigned its OWN topology
(deterministic, 0 LLM), and the plan is then EXECUTED against a real local
oMLX backend. The headline proof: a "compare ..." step routes to a MESH of
debating peers and a "write ..." step routes to a single agent — and we run
both for real and print the model's output per step.

Adapter lives operator-side (here), NOT in agentkit: the library only defines
the ``LLMClient`` Protocol. This is the "build the adapter and pass it in" half
of the seam, same as ``examples/run_measured.py``.

Run:   .venv/bin/python examples/dynamic_topology_e2e.py
Needs: oMLX serving chat on http://localhost:8000/v1 (no API key).
"""

from __future__ import annotations

import sys
import time
import urllib.request
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

from agentkit.planner import plan  # noqa: E402
from agentkit.topology import (  # noqa: E402
    MESH,
    SINGLE,
    assign_topologies,
    run_plan,
)
from agentkit.types import ChatResult  # noqa: E402

BASE_URL = "http://localhost:8000/v1"
# Preference order — first one that is BOTH served AND responds wins. Some
# models appear in /v1/models but 500 on inference (broken weights on this
# instance), so discovery probes each candidate with a tiny request, not just
# the listing.
PREFERRED_MODELS = (
    "MLX-Qwen3.5-35B-A3B-Claude-4.6-Opus-Reasoning-Distilled-4bit",
    "Qwen3.5-27B-Claude-4.6-Opus-Distilled-MLX-4bit",
    "Gemma-4-31B-JANG_4M-CRACK",
)
TASK = (
    "1. compare vector RAG and GraphRAG "
    "2. write a short recommendation"
)


class OMLXClient:
    """Real ``LLMClient`` over an OpenAI-compatible oMLX endpoint (no API key)."""

    def __init__(self, model: str, base_url: str = BASE_URL,
                 max_tokens: int = 400) -> None:
        self._c = OpenAI(base_url=base_url, api_key="not-needed")
        self.model = model
        self.max_tokens = max_tokens
        self.n_calls = 0
        self.total_tokens = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.n_calls += 1
        r = self._c.chat.completions.create(
            model=self.model, messages=messages,
            temperature=0.3, max_tokens=self.max_tokens,
        )
        usage = getattr(r, "usage", None)
        tt = getattr(usage, "total_tokens", 0) or 0
        self.total_tokens += tt
        return ChatResult(text=r.choices[0].message.content or "", total_tokens=tt)


def _responds(model: str) -> bool:
    """True if the model answers a tiny chat request (some served ids 500)."""
    try:
        c = OpenAI(base_url=BASE_URL, api_key="not-needed")
        r = c.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "Say OK."}],
            max_tokens=8, temperature=0,
        )
        return bool((r.choices[0].message.content or "").strip())
    except Exception:
        return False


def discover_model() -> str:
    """Pick the first model that is BOTH served AND responds.

    GET /v1/models lists every loaded id, but some 500 on inference; so we
    probe candidates in preference order and return the first that actually
    answers. Falls back to probing the remaining served non-embedding models.
    """
    url = f"{BASE_URL}/models"
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 (local)
        data = json.loads(resp.read().decode())
    ids = [m["id"] for m in data.get("data", [])]

    ordered = [m for m in PREFERRED_MODELS if m in ids]
    ordered += [m for m in ids
                if m not in ordered and "bge" not in m and "embed" not in m]
    for mid in ordered:
        if _responds(mid):
            return mid
    raise RuntimeError(f"no responding chat model among served ids: {ids}")


def main() -> int:
    # 1. Connectivity + model discovery (explicit failure if unreachable).
    try:
        model = discover_model()
    except Exception as exc:
        print(f"oMLX UNREACHABLE at {BASE_URL} — {exc}")
        print("Start oMLX on :8000 and re-run. NOT substituting fakes.")
        return 1

    print(f"oMLX reachable. model={model}  base={BASE_URL}\n")

    # 2. Plan → assign per-step topology (deterministic, 0 LLM).
    p = plan(TASK)
    assigned = assign_topologies(p, mode="auto")

    print("=== plan + per-step topology assignment (deterministic) ===")
    for st in assigned.steps:
        print(f"  [{st.id}] topology={st.topology:8}  {st.description}")
    print()

    # 3. Headline assertions: compare→MESH, write→SINGLE.
    by_desc = {st.description.lower(): st.topology for st in assigned.steps}
    compare_step = next(t for d, t in by_desc.items() if "compare" in d)
    write_step = next(t for d, t in by_desc.items() if "write" in d or "recommend" in d)
    assert compare_step == MESH, f"compare step should be MESH, got {compare_step}"
    assert write_step == SINGLE, f"write step should be SINGLE, got {write_step}"
    print("ASSERT OK: 'compare' step → MESH, 'write' step → SINGLE\n")

    # 4. EXECUTE the plan with the real model. Budget OFF (local tokens free).
    print("=== executing plan against the REAL model (this takes a minute) ===\n",
          flush=True)
    client = OMLXClient(model)
    t0 = time.perf_counter()
    result = run_plan(assigned, client)
    wall = time.perf_counter() - t0

    for run in result.runs:
        print(f"--- step [{run.step_id}]  topology={run.topology}  "
              f"agents={run.n_agents}  tokens={run.tokens}  "
              f"wall={run.wall_s:.1f}s ---")
        print(f"  task: {run.description}")
        print(f"  model output:\n{_indent(run.output)}\n", flush=True)

    print("=" * 70)
    print(f"model            : {model}")
    print(f"total LLM calls  : {client.n_calls}")
    print(f"total tokens     : {result.total_tokens}")
    print(f"wall-time        : {wall:.1f}s")
    print("BACKEND: real oMLX (measured — output + wall-time are genuine).")
    return 0


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + ln for ln in (text or "").splitlines()) or f"{prefix}<empty>"


if __name__ == "__main__":
    raise SystemExit(main())
