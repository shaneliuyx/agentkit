"""Mesh with REAL peer communication via the A2A message bus.

Unlike a star (workers never see each other), here mesh peers actually talk:
  - round 1: each peer web-searches its angle and BROADCASTS a hypothesis to the
    bus;
  - round 2: each peer READS what its peers posted (shared context, excluding its
    own), then challenges / refines its view and posts again;
  - reduce: reads the whole transcript and synthesizes the debate.

Communication is through the `MessageBus` (A2A), not just DAG edges — the bus is
what makes this a mesh rather than a fan-out. The full conversation is printed.

Run:  .venv/bin/python examples/mesh_a2a_demo.py   (needs oMLX :8000 + SearXNG)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SEARXNG_URL", "http://localhost:8080")
sys.path.insert(0, "/Users/yuxinliu/code/agent-prep/shared")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from web_toolkit import web_search  # noqa: E402

from agentkit.runtime.graph_store import Node  # noqa: E402
from agentkit.topology import TaskSpec, run_task  # noqa: E402
from agentkit.topology.a2a import MessageBus  # noqa: E402
from run_measured import OMLXClient  # noqa: E402

CHAT_MODEL = "gemma-4-26B-A4B-it-heretic-4bit"


def main() -> None:
    client = OMLXClient(model=CHAT_MODEL, max_tokens=90)
    bus = MessageBus()
    spec = TaskSpec(
        task="Diagnose why a local LLM server intermittently returns HTTP 422.",
        subtasks=("payload schema mismatch", "token / context-length limits",
                  "concurrent-request handling"),
        subtasks_independent=True, workers_challenge=True)   # → mesh

    def handler(node: Node) -> dict:
        name = node.name
        if name == "dispatch":
            return {"text": "(dispatched to peers)"}
        if name == "reduce":
            convo = "\n".join(f"[{m.sender} r{m.round}] {m.content}"
                              for m in bus.transcript())
            ans = client.chat([
                {"role": "system", "content": "Synthesize the peer debate into a "
                 "single ranked diagnosis. 2-3 sentences."},
                {"role": "user", "content": convo}]).text
            return {"text": (ans or "").strip().replace("\n", " ")}

        peer, rnd = name.rsplit("_r", 1)            # 'peer2_r1' → ('peer2', '1')
        q = node.payload.get("prompt", "")
        if rnd == "1":                              # draft + broadcast a hypothesis
            try:
                hits = web_search(q, results=2)
            except Exception:
                hits = []
            ctx = "\n".join(f"- {h.title}: {h.snippet} ({h.url})" for h in hits)
            ans = client.chat([
                {"role": "system", "content": "Draft a one-sentence hypothesis from "
                 "the search results; cite a URL."},
                {"role": "user", "content": f"Angle: {q}\n\nSearch:\n{ctx or '(none)'}"}
            ]).text
            ans = (ans or "").strip().replace("\n", " ")
            bus.post(peer, ans, round=1)            # COMMUNICATE: broadcast to peers
            return {"text": ans[:160]}
        # round 2: read peers' messages (A2A + shared context), then challenge.
        peers_said = bus.context(reader=peer)       # what OTHERS posted, not self
        ans = client.chat([
            {"role": "system", "content": "Your peers proposed other causes. "
             "Challenge or refine your own view in light of theirs. 1-2 sentences."},
            {"role": "user", "content":
             f"Your angle: {q}\n\nYour peers said:\n{peers_said or '(none)'}"}
        ]).text
        ans = (ans or "").strip().replace("\n", " ")
        bus.post(peer, ans, round=2)
        return {"text": ans[:160]}

    r = run_task(spec, client, handler=handler, model=CHAT_MODEL)

    print(f"TASK: {spec.task}")
    print(f"topology={r.topology} nodes={len(r.results)} peak={r.peak_concurrency} "
          f"status={r.run_status}\n")
    print("=== A2A TRANSCRIPT (peers communicating) ===")
    for m in bus.transcript():
        print(f"[{m.sender} · round {m.round}] {m.content[:90]}")
    print("\n=== SYNTHESIZED DIAGNOSIS (reduce) ===")
    print(r.results.get("reduce", ""))


if __name__ == "__main__":
    main()
