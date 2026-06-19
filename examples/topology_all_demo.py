"""Exercise ALL 7 topologies on a COMPLEX, multi-hop web-research task.

Two things make this a real test (vs a factual lookup that any topology handles):
  1. the task genuinely composes — sub-tasks build on each other (identify →
     compare → recommend), so a chaining pipeline differs from a flat star;
  2. the handler THREADS upstream results downstream — a node sees its
     dependencies' findings (computed from the generated DAG edges), so pipeline
     stage N+1 reads stage N, and star/tree reduce synthesizes its workers.

Each node still does a real SearXNG search + grounded gemma answer.

Run:  .venv/bin/python examples/topology_all_demo.py   (needs oMLX :8000 + SearXNG)
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
from agentkit.topology import TaskSpec, build_config, run_task  # noqa: E402
from run_measured import OMLXClient  # noqa: E402

CHAT_MODEL = "gemma-4-26B-A4B-it-heretic-4bit"
TASK = ("Recommend an open-source vector database for a local-first RAG app on "
        "Apple Silicon, and justify the choice.")
# Ordered, composing sub-tasks: each genuinely builds on the previous.
SUBTASKS = (
    "Identify three candidate open-source vector databases for local use",
    "Compare those candidates on Apple-Silicon support, memory use, and speed",
    "Recommend the single best candidate for this use case, with justification",
)

TOPOLOGY_SPECS = {
    "single":        dict(subtasks=(), single_agent_sufficient=True),
    "pipeline":      dict(subtasks=SUBTASKS, subtasks_independent=False),
    "star":          dict(subtasks=SUBTASKS, subtasks_independent=True),
    "tree":          dict(subtasks=SUBTASKS, subtasks_independent=True,
                          needs_subdecomposition=True),
    "mesh":          dict(subtasks=SUBTASKS, subtasks_independent=True,
                          workers_challenge=True),
    "gateway":       dict(subtasks=SUBTASKS, subtasks_independent=True,
                          multiple_entry_points=True),
    "durable_board": dict(subtasks=SUBTASKS, subtasks_independent=True,
                          cross_session=True),
}


def _deps_from_dag(dag: dict) -> dict[str, list[str]]:
    """Invert edges → {node: [upstream deps]}."""
    deps: dict[str, list[str]] = {n: [] for n in dag["nodes"]}
    for src, dst in dag.get("edges", []):
        deps[dst].append(src)
    return deps


def main() -> None:
    client = OMLXClient(model=CHAT_MODEL, max_tokens=110)

    print(f"TASK: {TASK}\n")
    print(f"{'topology':<13}{'nodes':>6}{'peak':>5}{'status':>8}  final/reduce answer")
    print("-" * 104)

    for _name, flags in TOPOLOGY_SPECS.items():
        spec = TaskSpec(task=TASK, **flags)
        dag = build_config(spec, model=CHAT_MODEL).dag       # same dag run_task builds
        deps = _deps_from_dag(dag)
        shared: dict[str, str] = {}                          # node name → finding
        lock = threading.Lock()

        def handler(node: Node) -> dict:
            q = node.payload.get("prompt", "")
            try:
                hits = web_search(q, results=2)
            except Exception:
                hits = []
            ctx = "\n".join(f"- {h.title}: {h.snippet} ({h.url})" for h in hits)
            with lock:
                upstream = "\n".join(f"[{d}] {shared.get(d, '')}"
                                     for d in deps.get(node.name, []) if shared.get(d))
            msgs = [
                {"role": "system", "content":
                 "You are one step in a research pipeline. Use the upstream "
                 "findings and the search results to answer in 1-2 sentences; "
                 "cite one URL."},
                {"role": "user", "content":
                 f"Step: {q}\n\nUpstream findings:\n{upstream or '(none)'}\n\n"
                 f"Search results:\n{ctx or '(none)'}"},
            ]
            ans = (client.chat(msgs).text or "").strip().replace("\n", " ")
            with lock:
                shared[node.name] = ans
            return {"text": ans[:200]}

        r = run_task(spec, client, handler=handler, model=CHAT_MODEL)
        # the synthesizing node carries the composed answer
        final_node = next((n for n in ("reduce", "gather", "stage3")
                           if n in r.results), next(iter(r.results)))
        final = r.results.get(final_node, "")
        print(f"{r.topology:<13}{len(r.results):>6}{r.peak_concurrency:>5}"
              f"{r.run_status:>8}  [{final_node}] {final[:74]}")


if __name__ == "__main__":
    main()
