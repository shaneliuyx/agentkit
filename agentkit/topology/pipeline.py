"""agentkit.topology.pipeline — define a task, get results via the chosen topology.

The end-to-end glue the user asked for: a task → (optionally infer the §2.7
answers with an LLM) → `select_topology` → `generate_dag` → run it on the durable
`agentkit.runtime.GraphStore` → collect results. The topology is *chosen by the
rules*, the DAG is *generated*, and execution is *durable* (the same store that
survives kill -9).

Execution is a simple synchronous driver (claim → handle → mark_done) over the
GraphStore — durable and dependency-correct. True parallel overlap for star/tree
is a runtime concern (an asyncio worker pool); the driver here realizes the
*structure* and records the run; `concurrency` from the choice is reported as the
shape's available parallelism.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentkit.runtime.graph_store import GraphStore, Node
from agentkit.topology.config import TopologyConfig, build_config
from agentkit.topology.core import TaskSpec
from agentkit.topology.infer import infer_spec
from agentkit.types import LLMClient, Message

NodeHandler = Callable[[Node], dict[str, Any]]


@dataclass(frozen=True)
class PipelineResult:
    topology: str
    trigger: str
    concurrency: int
    rationale: str
    run_status: str
    results: dict[str, str]          # node name → text output
    config: TopologyConfig


def _llm_handler(client: LLMClient) -> NodeHandler:
    """Default handler: llm nodes call the client on their prompt; tool nodes
    are no-ops. Returns a json-serializable result dict for mark_done."""
    def handle(node: Node) -> dict[str, Any]:
        if node.node_type == "llm":
            prompt = node.payload.get("prompt", "")
            msgs: list[Message] = [{"role": "user", "content": prompt}]
            res = client.chat(msgs)
            return {"text": (res.text or "").strip(),
                    "tokens": getattr(res, "total_tokens", 0) or 0}
        return {"text": f"[tool:{node.name}]"}
    return handle


def run_task(
    task: str | TaskSpec,
    client: LLMClient,
    *,
    infer: bool = False,
    handler: NodeHandler | None = None,
    db_path: str | Path | None = None,
    llm: bool = True,
    model: str = "gemma-4-26B-A4B-it-heretic-4bit",
) -> PipelineResult:
    """Run a task end-to-end through the rule-selected topology.

    ``task`` may be a ready `TaskSpec` or free text. With ``infer=True`` and free
    text, the §2.7 answers are inferred via one LLM call (`infer_spec`); otherwise
    a bare-text task with no sub-tasks resolves to `Single`.
    """
    if isinstance(task, TaskSpec):
        spec = task
    elif infer:
        spec = infer_spec(task, client)
    else:
        spec = TaskSpec(task=task)
    config = build_config(spec, llm=llm, model=model)
    handle = handler or _llm_handler(client)

    tmp = None
    if db_path is None:
        tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(tmp.name) / "topo_run.db")
    try:
        store = GraphStore(str(db_path))
        gid = store.create_graph(spec.task[:48] or "task", config.dag)
        run_id = store.start_run(gid, config.choice.trigger)

        results: dict[str, str] = {}
        # Synchronous durable driver: claim a READY node, run it, mark_done
        # (which promotes downstream). Stops when nothing is claimable.
        while True:
            node = store.claim_ready_node(run_id, "driver")
            if node is None:
                break
            try:
                out = handle(node)
                store.mark_done(run_id, node.name, out)
                results[node.name] = out.get("text", "")
            except Exception as exc:  # durable retry/fail path
                store.mark_failed(run_id, node.name, repr(exc))
                results[node.name] = f"[error: {exc}]"

        return PipelineResult(
            topology=config.choice.topology,
            trigger=config.choice.trigger,
            concurrency=config.concurrency,
            rationale=config.choice.rationale,
            run_status=store.run_status(run_id),
            results=results,
            config=config,
        )
    finally:
        if tmp is not None:
            tmp.cleanup()
