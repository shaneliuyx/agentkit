"""Illustrate the whole capability: different tasks → topology → diagram → result.

For each free-text task: infer the §2.7 answers (LLM front-end) → the PURE rule
tree picks a topology → render the DAG's node relationships (Mermaid) → run it
durably → produce the result. Distinct tasks route to distinct topologies — that
is the point of rule-driven selection.

Handler is model-only here (no web) to keep the routing illustration fast; the
web-search version is in examples/topology_all_demo.py / DESIGN §5.9.

Run:  .venv/bin/python examples/topology_routing_demo.py   (needs oMLX :8000)
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

os.environ.setdefault("SEARXNG_URL", "http://localhost:8080")
sys.path.insert(0, "/Users/yuxinliu/code/agent-prep/shared")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from web_toolkit import web_search  # noqa: E402

from agentkit.runtime.graph_store import Node  # noqa: E402
from agentkit.topology import (  # noqa: E402
    build_config,
    infer_spec,
    run_task,
    select_topology,
    to_mermaid,
)
from run_measured import OMLXClient  # noqa: E402

CHAT_MODEL = "gemma-4-26B-A4B-it-heretic-4bit"

# Distinct tasks, each phrased to exercise a different §2.7 answer pattern.
TASKS = [
    "What does the acronym RAG stand for in machine learning?",
    "Locate the bug in a failing login flow, then write a fix, then add a regression test.",
    "Review this pull request for security, performance, and test coverage in parallel.",
    "Investigate why a local LLM server intermittently returns HTTP 422 — weigh competing root causes.",
    "Plan and run a three-week database migration with weekly checkpoints and human sign-off.",
    "Route incoming Slack, Telegram, and email messages each to the right specialized assistant.",
]


def main() -> None:
    # infer needs room to emit the full decomposition JSON (subtasks + booleans);
    # the per-node handler can stay short.
    infer_client = OMLXClient(model=CHAT_MODEL, max_tokens=300)
    client = OMLXClient(model=CHAT_MODEL, max_tokens=110)

    for i, task in enumerate(TASKS, 1):
        spec = infer_spec(task, infer_client)       # text → §2.7 answers (1 LLM call)
        choice = select_topology(spec)              # pure rule tree
        dag = build_config(spec, model=CHAT_MODEL).dag

        # Real research handler: each node web-searches (SearXNG) + answers
        # grounded, and sees its upstream dependencies' findings (threading).
        deps = {n: [] for n in dag["nodes"]}
        for s, d in dag["edges"]:
            deps[d].append(s)
        shared: dict[str, str] = {}
        lock = threading.Lock()

        def handler(node: Node, _deps=deps, _shared=shared, _lock=lock) -> dict:
            q = node.payload.get("prompt", "")
            try:
                hits = web_search(q, results=2)
            except Exception:
                hits = []
            ctx = "\n".join(f"- {h.title}: {h.snippet} ({h.url})" for h in hits)
            with _lock:
                up = "\n".join(f"[{d}] {_shared.get(d, '')}"
                               for d in _deps.get(node.name, []) if _shared.get(d))
            ans = (client.chat([
                {"role": "system", "content":
                 "One step in a research pipeline. Use the upstream findings + "
                 "search results; answer in 1-2 sentences and cite one URL."},
                {"role": "user", "content":
                 f"Step: {q}\n\nUpstream:\n{up or '(none)'}\n\nSearch:\n{ctx or '(none)'}"},
            ]).text or "").strip().replace("\n", " ")
            with _lock:
                _shared[node.name] = ans
            return {"text": ans[:200]}

        r = run_task(spec, client, handler=handler, model=CHAT_MODEL)
        final_node = next((n for n in ("reduce", "gather", "stage3", "entry", "agent")
                           if n in r.results), list(r.results)[-1])
        print(f"\n{'='*78}\n[{i}] TASK: {task}")
        print(f"  infer  : subtasks={len(spec.subtasks)} independent={spec.subtasks_independent} "
              f"challenge={spec.workers_challenge} cross_session={spec.cross_session} "
              f"entry_points={spec.multiple_entry_points}")
        print(f"  ROUTE  : {choice.topology}  (trigger={choice.trigger}, conc={choice.concurrency})")
        print(f"  rule   : {choice.rationale}")
        print("  DIAGRAM:")
        print("    " + to_mermaid(dag).replace("\n", "\n    "))
        print(f"  run    : status={r.run_status} nodes={len(r.results)} peak={r.peak_concurrency}")
        print(f"  RESULT : [{final_node}] {r.results.get(final_node, '')[:140]}")


if __name__ == "__main__":
    main()
