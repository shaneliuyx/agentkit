"""Live demo — task → infer → select topology → run agents → results.

End-to-end exercise of agentkit.topology against real oMLX:
  1. a free-text task is analysed by the LLM front-end (infer_spec → §2.7 answers)
  2. the rule tree selects a topology + trigger (pure, 0 LLM)
  3. the DAG is generated and run durably on the GraphStore (real agent calls)
  4. Tool 2 emits the config JSON + a topologies.py-style module

Run:  .venv/bin/python examples/topology_pipeline_demo.py   (needs oMLX on :8000)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentkit.topology import (  # noqa: E402
    build_config,
    emit_topologies_py,
    infer_spec,
    run_task,
    select_topology,
    to_json,
)
from run_measured import OMLXClient  # noqa: E402

CHAT_MODEL = "gemma-4-26B-A4B-it-heretic-4bit"
TASK = ("Review this pull request for security issues, test coverage, and "
        "performance regressions, and summarize the findings.")


def banner(t: str) -> None:
    print(f"\n{'='*70}\n{t}\n{'='*70}")


def main() -> None:
    client = OMLXClient(model=CHAT_MODEL, max_tokens=256)

    banner(f"TASK\n{TASK}")

    # 1. LLM front-end infers the §2.7 answers from free text.
    spec = infer_spec(TASK, client)
    print(f"[infer] subtasks={spec.subtasks}")
    print(f"[infer] independent={spec.subtasks_independent} "
          f"challenge={spec.workers_challenge} cross_session={spec.cross_session} "
          f"entry_points={spec.multiple_entry_points}")

    # 2. Pure rule tree selects the topology.
    choice = select_topology(spec)
    banner("TOPOLOGY SELECTED (pure rules, 0 LLM)")
    print(f" topology   : {choice.topology}")
    print(f" trigger    : {choice.trigger}")
    print(f" concurrency: {choice.concurrency}")
    print(f" rule fired : {choice.rationale}")

    # 3. Run the generated DAG durably on the GraphStore with real agents.
    result = run_task(spec, client, model=CHAT_MODEL)
    banner("RUN RESULTS (durable GraphStore, real agent calls)")
    print(f" run_status: {result.run_status}  | nodes: {list(result.results)}")
    for node, text in result.results.items():
        print(f"  • {node}: {text[:80]}")

    # 4. Tool 2 — emit config + topologies.py-style code.
    cfg = build_config(spec, model=CHAT_MODEL)
    banner("CONFIG (Tool 1 output — JSON)")
    print(to_json(cfg)[:600] + " ...")
    banner("GENERATED topologies.py (Tool 2 — codegen)")
    print(emit_topologies_py(cfg))


if __name__ == "__main__":
    main()
