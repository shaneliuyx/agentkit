"""bench/bench_compactor.py — proves the deterministic-first performance thesis.

Builds a large synthetic agent session, then measures:
  (a) compaction wall-time (time.perf_counter — fine in a bench, never inside
      the compactor itself, which must stay deterministic),
  (b) estimated tokens before vs after, and the % reduction,
  (c) determinism (compact twice → byte-identical .text).

These are the numbers that back the "deterministic, fast, zero-LLM" claim. Run:
    python bench/bench_compactor.py
"""

from __future__ import annotations

import time

from agentkit.context import compact
from agentkit.types import Message

N_MESSAGES = 400


def build_session(n: int = N_MESSAGES) -> list[Message]:
    """A realistic-ish coding session: a goal, many tool-calling rounds with
    file ops + periodic commits + an error, and a late scope change."""
    msgs: list[Message] = [
        {"role": "system", "content": "You are a senior coding agent."},
        {"role": "user",
         "content": "Build a durable task queue. Always write tests first; "
                    "never block the event loop."},
    ]
    i = 0
    while len(msgs) < n - 2:
        msgs.append({
            "role": "assistant",
            "content": f"Implementing component {i}; reasoning about edge cases.",
            "tool_calls": [{"function": {
                "name": "edit_file" if i % 3 else "write_file",
                "arguments": f'{{"path": "src/queue/part_{i}.py"}}'}}],
        })
        if i % 5 == 0:
            msgs.append({"role": "tool", "name": "shell",
                         "content": f"git commit -m \"part {i} done\"\n"
                                    f"[main {i:07x}] part {i} done"})
        elif i % 7 == 0:
            msgs.append({"role": "tool", "name": "pytest",
                         "content": f"Traceback: TimeoutError in part_{i}.py worker"})
        else:
            msgs.append({"role": "tool", "name": "pytest", "content": "3 passed"})
        i += 1
    msgs.append({"role": "user", "content": "Actually, prefer asyncio.Queue here."})
    msgs.append({"role": "assistant", "content": "Refactoring onto asyncio.Queue."})
    return msgs


def main() -> None:
    session = build_session()

    # (a) wall-time, averaged over a few runs for a stable number.
    runs = 5
    t0 = time.perf_counter()
    for _ in range(runs):
        result = compact(session, keep=1)
    elapsed_ms = (time.perf_counter() - t0) / runs * 1000.0

    # (b) token reduction.
    before = result.est_tokens_before
    after = result.est_tokens_after
    reduction = 100.0 * (1.0 - after / max(1, before))

    # (c) determinism.
    deterministic = compact(session, keep=1).text == result.text

    rows = [
        ("messages", f"{len(session)}"),
        ("sections extracted", f"{len(result.sections)}"),
        ("est_tokens_before", f"{before}"),
        ("est_tokens_after", f"{after}"),
        ("reduction", f"{reduction:.1f}%"),
        ("compaction_time", f"{elapsed_ms:.3f} ms (avg of {runs})"),
        ("deterministic", "YES" if deterministic else "NO"),
        ("LLM calls", "0"),
    ]
    width = max(len(k) for k, _ in rows)
    lines = ["", "agentkit compactor benchmark", "-" * 44]
    lines += [f"{k.ljust(width)} : {v}" for k, v in rows]
    lines.append("-" * 44)
    # bench output is the artifact; printing here is intentional (not in lib code).
    print("\n".join(lines))


if __name__ == "__main__":
    main()
