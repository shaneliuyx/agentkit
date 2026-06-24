# agentkit.loop — Loop Engineering Shared Library

Three focused modules that close the gap between agentkit's existing loop
primitives and a complete loop-engineering toolkit.

## Modules

### `loop.goal` — Verifiable Stop Conditions

`LoopGoal` makes termination criteria explicit and machine-verifiable,
replacing stall-based stopping (N stale rounds) with objective evidence.

```python
from agentkit.loop.goal import LoopGoal, check_goal

goal = LoopGoal(
    end_state="All billing tests pass",
    evidence_cmd="pytest tests/billing -q",
    success_pattern=r"\d+ passed",
    max_turns=25,
    max_tokens=100_000,
)
verdict = check_goal(goal, cwd=".")
if verdict.met:
    print(f"Goal met: {verdict.reason}")
```

The Ralph Technique (`while ! grep -q "DONE" STATUS.md`) is the simplest
`LoopGoal`: `evidence_cmd="grep 'ALL DONE' STATUS.md"`, `success_pattern="ALL DONE"`.

### `loop.hill_climb` — End-to-End Self-Improvement Pipeline

Wires production trace intake into the existing `evolve/core.py` DGM pipeline:

1. `mine_weaknesses()` — LLM scans trajectories, extracts recurring failure patterns
2. `make_llm_proposer(weaknesses=...)` — targets the proposer at observed failures
3. `evolve_prompt()` — DGM keep/discard with gate admission

```python
from agentkit.loop.hill_climb import hill_climb_from_traces

result = hill_climb_from_traces(
    baseline_prompt=system_prompt,
    trajectories=recent_agent_runs,
    gate=gate,
    client=llm_client,
    baseline_score=0.65,
    evaluate=my_evaluator,
    epochs=10,
)
print(f"Improved by {result.delta:.2f} → {result.best_score:.2f}")
```

### `loop.chain` — DAG Composition of Loops

Chains loops as a DAG, passing outputs between them. Enables
"research → verify → deploy" pipelines without writing orchestration code.

```python
from agentkit.loop.chain import LoopChain, LoopSpec
from agentkit.loop.goal import LoopGoal

chain = (
    LoopChain()
    .add(LoopSpec("research", run_research))
    .add(LoopSpec("verify", run_verify, depends_on=("research",)))
    .add(LoopSpec(
        "deploy", run_deploy,
        goal=LoopGoal("Deploy healthy", "curl -s /health", "ok"),
        depends_on=("verify",),
    ))
)
result = chain.run({"task": "ship billing v2"})
```

## Design Principles

1. **Verifiable over heuristic**: stop on evidence, not stale-round count.
2. **Gate never bypassed**: every evolved variant passes the LEARN gate.
3. **Pure subprocess**: `check_goal()` has no LLM, no network, no mutation.
4. **mine_weaknesses is the only new LLM call**: everything else is model-free.
5. **Compatible with existing primitives**: `evolve/core.py`, `gates/core.py`,
   `orchestrator/stall.py` are unchanged — this module composes them.

## Relation to External Frameworks

| This module | External analogue |
|---|---|
| `LoopGoal` + `check_goal()` | LangGraph `add_conditional_edges` routing fn → `END` |
| `LoopGoal.max_turns` | LangGraph `recursion_limit`; Temporal workflow timeout |
| `mine_weaknesses` | DSPy `BootstrapFewShot` trace distillation |
| `hill_climb_from_traces` | DSPy `MIPROv2` + LangSmith prompt engineering loop |
| `LoopChain` | Prefect Automations (event-driven) / LangGraph subgraph composition |

## Studio Integration

The loop module is surfaced in AgentKit Studio through four new panels:

- **Goal** — displays `goal_met` events when a `LoopGoal` is configured
- **Hill Climb** — epoch-by-epoch score timeline from `hill_climb_from_traces()`
- **Scheduler** — registered cron/webhook triggers from `runtime/scheduler.py`
- **Chain** — live result view from `POST /chain/run`

Set a goal via the REST API:
```bash
curl -X POST http://localhost:8000/session/{id}/goal \
  -H "Content-Type: application/json" \
  -d '{"end_state": "All tests pass", "evidence_cmd": "pytest -q", "success_pattern": "passed"}'
```
