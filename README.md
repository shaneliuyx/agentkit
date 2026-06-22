# agentkit

A lean, reusable agent-systems library. Nine small modules, one philosophy.

> **The design axiom: a cheap deterministic stage gates the expensive LLM stage.**
> The model is the last resort, not the default — which is why most work never
> reaches it. See [`docs/DESIGN.md`](docs/DESIGN.md) for the full rationale,
> per-module decisions, and pattern provenance.

| Module | What it gives you |
| --- | --- |
| `agentkit.context` | **Deterministic, zero-LLM conversation compaction** (sticky/volatile sections, rolling transcript). |
| `agentkit.memory` | Tiered memory: pure deterministic extraction + a SQLite/numpy vector store over an injected embedder. |
| `agentkit.runtime` | Durable DAG execution: graph store, cross-process file lock, scheduler (demand-driven, survives `kill -9`). |
| `agentkit.agent` | DI ReAct loop + difficulty router + **role presets** (Researcher/Reviewer/Writer/Verifier) + a resilient batch runner. |
| `agentkit.orchestrator` | Long-horizon autonomy: pure stall/diversity/select control + file-state loop wiring `compact()` as the inter-iteration handoff. |
| `agentkit.topology` | Rule-driven multi-agent topology: pick shape by task (STAR/MESH/PIPELINE/…), generate the DAG, round-trip config ↔ JSON ↔ emitted code — the **config-as-policy reference pattern**. |
| `agentkit.quality` | Source-grounding `verify`: deterministic citation/link checks + optional LLM claim-support, severity-graded. |
| `agentkit.backends` | `CliLLMClient` — use a CLI (`codex exec`, `claude -p`) as the model, no API key, no shell-injection surface. |
| `agentkit.types` | The Protocol seams: `Embedder`, `LLMClient`, `ChatResult`, `Message`. |

## The deterministic-first thesis

The cheapest tier runs first. Stall detection, diversity checks, rubric
aggregation, citation extraction, conversation compaction — all done with
arithmetic/regex: **instantly, deterministically, zero LLM calls.** Embedding
(memory) and LLM passes happen only after the free tier has done its work. The
control logic that *decides* whether to spend a model call is itself model-free,
so it is unit-testable without a network.

This axiom was observed converging across four independent projects
([pi-vcc](https://github.com/sting8k/pi-vcc),
Deli_AutoResearch, IdeaScout, feynman) and adopted as law — see the provenance
table in [`docs/DESIGN.md`](docs/DESIGN.md).

## The Protocol-seam design

Pluggable dependencies are **Protocols** in `agentkit.types`, never concrete
vendors. The original lab hardcoded `openai.OpenAI` + a local oMLX endpoint;
agentkit inverts that via dependency injection so the **same code** runs on
oMLX, Claude, a CLI subprocess, or a fake. agentkit never imports a vendor SDK —
you build the adapter and pass it in.

## Quickstart — a memory-aware agent

```python
from agentkit import MemoryStore, run_agent, compact, ChatResult

class MyEmbedder:                      # wrap any embeddings endpoint
    def embed(self, texts): return [[float(len(t))] for t in texts]

class MyClient:                        # wrap any chat endpoint
    def chat(self, messages, tools=None):
        return ChatResult(text="The answer is 4.")

memory = MemoryStore("memory.db", embedder=MyEmbedder())
memory.add("semantic", "Always validate inputs before parsing.")

result = run_agent("What is 2+2?", client=MyClient(),
                   tools={"add": lambda a: {"sum": a["a"] + a["b"]}},
                   memory=memory)
print(result.answer)                   # -> "The answer is 4."

summary = compact(long_message_history, keep=1)   # zero-LLM compaction
print(summary.text, summary.est_tokens_after)
```

## The reference agent — the whole stack composed

`examples/research_agent.py` is a long-horizon RAG/memory research agent that
wires every module: `MemoryStore` recall → `dispatch`/`run_role`
(Researcher→Verifier) → `context.compact()` handoff → `orchestrator.run`
(stall/diversity loop) → `quality.verify` (source-grounding).

It runs **offline** with a fake client/embedder (composition proof) and
**measured** against a real backend with one flag:

```bash
python examples/research_agent.py            # self-check, offline
python bench/bench_reference_agent.py        # structural numbers, offline
python bench/bench_reference_agent.py --backend omlx   # measured (needs oMLX :8000)
```

Measured on real `gemma-4-26B-A4B-it-heretic-4bit` + `bge-m3`, `max_rounds=8`:

```
                       calls   tokens    wall    recall
tiered (use_memory)      8      11191    86.3s     8
all-LLM baseline         8      19724    89.0s     0     →  -43.3% tokens
tiered (no_memory)       8       7392    82.8s     0     →  -62.5% tokens
```

**Real token reduction: 43.3%** vs routing everything to the model with a
growing transcript. Honest reading: *compaction* is the dominant lever (no-memory
tiered is cheapest, −62.5%); memory *adds* ~3800 tokens to inject recall, buying
8 recall hits whose answer-quality impact a blind distinct-judge eval found to be
**no reliable gain** (win-rate 1/4 — see `docs/DESIGN.md` §6). Wall-time
barely moves (−3%) at this scale — both make 8 calls and local decode dominates.
The win needs `rounds ≳ 6` and RAG recall capped at `k=1`. Full reconciliation
(and the earlier offline estimate) in [`docs/DESIGN.md`](docs/DESIGN.md) §6.

**But memory *does* pay off once history exceeds the context budget.** A scaled
LongMemEval-style test (`examples/eval_long_memory.py`: 8 needle facts in a
116-turn / 36-session history, reader held constant) measured memory **8/8 vs a
recent-window truncation baseline 0/8 — and memory used fewer tokens (1883 vs
4316)**. The break-even is the context budget: history that fits → memory is dead
weight; history that overflows → memory is the whole game. See `docs/DESIGN.md` §6.

## Module map

```
agentkit/
├── types.py              # Embedder, LLMClient, ChatResponse, ChatResult, Message
├── context/compactor.py  # compact(), merge(), Block, CompactResult            (NEW, pi-vcc)
├── memory/
│   ├── extract.py        # extract_files/commits/preferences/outstanding       (deterministic tier)
│   └── store.py          # MemoryStore, MemoryEntry                            (vector tier)
├── runtime/
│   ├── file_lock.py      # FileLock (cross-process claim lock)
│   ├── graph_store.py    # GraphStore, Node (durable DAG)
│   └── scheduler.py      # Scheduler, CronRegistration (external triggers)
├── agent/
│   ├── loop.py           # run_agent(), AgentResult, quarantine()
│   ├── router.py         # route(), RouteDecision
│   ├── roles.py          # AgentRole, dispatch, RESEARCHER/REVIEWER/WRITER/VERIFIER  (feynman)
│   └── batch.py          # run_batch(), BatchConfig (resilient, resumable)      (IdeaScout)
├── orchestrator/
│   ├── stall.py          # assess(), StallAssessment, exceeds_budget    (PURE)  (Deli)
│   ├── diversity.py      # is_novel(), similarity                       (PURE)
│   ├── select.py         # Rubric, Dimension, cascade, prefilter        (PURE aggregation)  (IdeaScout)
│   ├── state.py          # Finding, ProgressState, log_event (state-file schema)
│   └── loop.py           # run(), OrchestratorConfig, Spawn
├── quality/verify.py     # verify(), Claim, VerifyFinding, UrlChecker           (feynman)
├── backends/cli.py       # CliLLMClient (subprocess; no shell-injection)
└── topology/             # rule-driven topology select + DAG gen          (config-as-policy)
    ├── core.py           # topology shapes (STAR/MESH/PIPELINE/…)
    ├── config.py         # TopologyConfig ↔ JSON ↔ emit_topologies_py
    └── infer.py          # select_topology (choose shape by task)
```

Every module ships one runnable self-check:

```bash
python -m agentkit.context.compactor
python -m agentkit.memory.extract
python -m agentkit.memory.store
python -m agentkit.runtime.graph_store
python -m agentkit.agent.loop
python -m agentkit.orchestrator.stall
python -m agentkit.orchestrator.loop
python -m agentkit.quality.verify
python -m agentkit.agent.roles
python examples/research_agent.py
python examples/topology_all_demo.py
```

## Provenance

All `runtime` / `memory` / `agent`-loop / `router` code is **extracted and
hardened** from measured `agent-prep` / `self-improving-agent-lab`
implementations (the hardcoded oMLX/`openai` clients replaced by Protocol
seams). The `context` / `orchestrator` / `quality` / `roles` / `batch` /
`backends` modules **port studied patterns** natively. Full mapping in
[`docs/DESIGN.md`](docs/DESIGN.md) §5.

## Roadmap — the self-improving direction *(planned, not shipped)*

The nine modules above are *static*: a human writes the roles, tools, and
topology. The planned next step keeps the deterministic-first axiom but makes the
**policy surface a folder of config files the agent can improve on its own** —
behind a sandbox it can't escape and a gate it can't override. Seven planned
modules — `config/`, `sandbox/`, `gates/`, `evolve/` + `skills/`, `planner/`,
`evolve/codegen`, and a `SelfImprovingAgent` facade — ordered safety-before-
capability. **None of these are shipped yet.** Full plan, build order, and
security model: [`docs/REPLAN-agentkit.md`](docs/REPLAN-agentkit.md).

## Install

```bash
pip install -e .                # core (numpy only)
pip install -e ".[openai]"      # + the openai SDK, if you build an OpenAI adapter
pip install -e ".[dev]"         # + pytest
```

Python 3.11+. **75 tests** pass (`pytest`).
