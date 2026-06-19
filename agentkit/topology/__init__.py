"""agentkit.topology — rule-driven topology selection, DAG generation, pipeline.

Three tools over the Week 4.6 rules:
  1. select_topology + generate_dag  — task → topology → DAG  (pure, 0 LLM)
  2. config + emit_topologies_py     — config file ↔ topologies.py-style code
  3. run_task                        — task → durable run → results (over runtime)

`infer_spec` is the optional LLM front-end (free text → the §2.7 answers).
"""

from agentkit.topology.config import (
    TopologyConfig,
    build_config,
    emit_topologies_py,
    from_json,
    load_config,
    to_json,
    to_mermaid,
    write_config,
    write_topologies_py,
)
from agentkit.topology.core import (
    DURABLE_BOARD,
    GATEWAY,
    MESH,
    PIPELINE,
    SINGLE,
    STAR,
    TREE,
    TaskSpec,
    TopologyChoice,
    generate_dag,
    select_topology,
)
from agentkit.topology.infer import infer_spec
from agentkit.topology.pipeline import PipelineResult, run_task

__all__ = [
    "TaskSpec",
    "TopologyChoice",
    "select_topology",
    "generate_dag",
    "SINGLE", "PIPELINE", "STAR", "TREE", "MESH", "GATEWAY", "DURABLE_BOARD",
    "TopologyConfig",
    "build_config",
    "to_json", "from_json", "write_config", "load_config", "to_mermaid",
    "emit_topologies_py", "write_topologies_py",
    "infer_spec",
    "run_task", "PipelineResult",
]
