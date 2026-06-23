# agentkit

A lean, reusable agent-systems library. A deterministic-first **core** plus a
config-driven **self-improving layer** — seventeen small modules, one philosophy.

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
| `agentkit.topology` | Rule-driven multi-agent topology: pick shape by task (STAR/MESH/PIPELINE/…), generate the DAG, round-trip config ↔ JSON ↔ emitted code, **+ dynamic per-step topology assigned from a plan** (`assign_topologies`/`run_plan`). |
| `agentkit.quality` | Source-grounding `verify`: deterministic citation/link checks + optional LLM claim-support, severity-graded. |
| `agentkit.backends` | Standard `LLMClient` adapters behind one seam: `OpenAIChatClient` / `OpenAIEmbedder` (any OpenAI-compatible endpoint — model + `base_url` are params), native `AnthropicChatClient` (Claude), and `CliLLMClient` (a CLI as the model, no API key). |
| `agentkit.types` | The Protocol seams: `Embedder`, `LLMClient`, `ChatResult`, `Message`. |

**The self-improving layer** — config is the agent's policy surface; a gate + sandbox are the guard it can't edit (see [`docs/REPLAN-agentkit.md`](docs/REPLAN-agentkit.md)):

| Module | What it gives you |
| --- | --- |
| `agentkit.config` | Roles as declarative YAML/JSON files, round-tripped to objects (generalizes `topology/config.py`). |
| `agentkit.sandbox` | `Sandbox` Protocol + `SubprocessSandbox`: argv-not-shell, cwd-jailed, timed, output-capped containment. |
| `agentkit.gates` | The LEARN admission gate: syntax→containment→execute→regression→safety→delta → `ACCEPT/REJECT/ESCALATE`. The LLM is a veto, never a vote. |
| `agentkit.evolve` | One text-space optimizer (keep/discard, gate-admitted): DGM prompt evolution + an RHO label-free mode. |
| `agentkit.skills` | Skill library (propose→gate→save, semantic retrieve) + `optimize_skill` — the SkillOpt loop; deploys a best artifact + a baseline→optimized delta. |
| `agentkit.planner` | Task → subtask DAG → emitted runtime graph **config** (self-plan as a file, not code). |
| `agentkit.codegen` | Agent-authored tools: query→schema→code→sandbox-validate→debugger-repair→gate→register. |
| `agentkit.selfimproving` | `SelfImprovingAgent` — the facade wiring it all: `.run` / `.improve` (gated self-edit; writes the better role back to its config file) / `.skills` / `.forge_tool`. |

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
# readme-skip-exec
from agentkit import MemoryStore, run_agent, compact, OpenAIChatClient, OpenAIEmbedder

client = OpenAIChatClient(model="Qwen2.5-Coder-7B-Instruct-MLX-4bit")   # local oMLX :8000
embedder = OpenAIEmbedder(model="bge-m3-mlx-fp16")

memory = MemoryStore("memory.db", embedder=embedder)
memory.add("semantic", "Always validate inputs before parsing.")

result = run_agent("What is 2+2?", client=client,
                   tools={"add": lambda a: {"sum": a["a"] + a["b"]}},
                   memory=memory)
print(result.answer)

summary = compact(long_message_history, keep=1)   # zero-LLM compaction
print(summary.text, summary.est_tokens_after)
```

This snippet reaches a real backend (the agent call embeds memory and queries
the model), so it is marked `# readme-skip-exec`: the README test syntax-checks
it but does not run it offline. The shipped `OpenAIChatClient` / `OpenAIEmbedder`
adapters target any OpenAI-compatible endpoint — no hand-rolled client needed.

### Use a real backend (one line)

The `LLMClient` seam is the point: multiple vendor adapters, one interface.
Construct any of these and pass it anywhere an `LLMClient`/`Embedder` is expected
(`run_agent`, `MemoryStore`, `SelfImprovingAgent`, ...) — unchanged.

```python
from agentkit import OpenAIChatClient, AnthropicChatClient, OpenAIEmbedder
client = OpenAIChatClient(model="Qwen2.5-Coder-7B-Instruct-MLX-4bit")            # local oMLX :8000, no key
client = OpenAIChatClient(model="claude-sonnet-4-5", base_url="http://localhost:8317/v1")  # Claude via VibeProxy (OpenAI-compat)
client = AnthropicChatClient(model="claude-sonnet-4-5", api_key="sk-ant-...")    # Claude, native Anthropic API
embedder = OpenAIEmbedder(model="bge-m3-mlx-fp16")
```

The OpenAI-compatible adapter defaults to a local oMLX on `:8000` (no API key);
point it at any OpenAI-compatible endpoint via `base_url=` / `api_key=` or the
`LLM_BASE_URL` / `LLM_API_KEY` env chain. The native Claude adapter reads
`ANTHROPIC_API_KEY` when `api_key=` is omitted. Optional extras:
`pip install agentkit[openai]` and/or `agentkit[anthropic]`.

## Configuring the agent — config is the policy surface

The self-improving layer's premise: an agent's behaviour is a **folder of config
files**, not hardcoded Python. Roles — each a system prompt, its tools, and a
reasoning tier — live on disk. The agent loads them, dispatches tasks to them, and
(gated) **rewrites them to improve itself**. A config directory is roles today
(tools, routing, and topology join them in later phases):

```
agent_config/
└── roles/
    ├── researcher.yaml      # or .json — both load; YAML needs the [config] extra
    ├── reviewer.yaml
    └── verifier.yaml
```

A role file is a flat, declarative document:

```yaml
name: Researcher
system_prompt: |
  You are a Researcher. Gather evidence across papers, the web, and code; prefer
  primary sources; attach the source for every non-trivial claim.
tools: [web_search, read_url, search_code, read_docs]
difficulty: medium          # trivial | easy | medium | hard | critical
output_schema: null         # optional JSON-schema-like dict, or null for free-form
```

Load roles from a folder — deterministic, no model call:

```python
from agentkit.config import load_roles, load_default_roles, dump_role
roles = load_roles("./agent_config/roles")      # -> {name: AgentRole}, your folder
defaults = load_default_roles()                 # shipped Researcher/Reviewer/Writer/Verifier
dump_role(defaults["Researcher"], "agent_config/roles/researcher.json")  # round-trips load
```

The agent runs on those files — and edits them to get better:

```python
# readme-skip-exec: runs the agent (needs a backend)
from agentkit import SelfImprovingAgent, OpenAIChatClient
agent = SelfImprovingAgent.from_config(
    "./agent_config",
    backend=OpenAIChatClient(model="Qwen2.5-Coder-7B-Instruct-MLX-4bit"))
agent.run("survey vector databases")            # dispatch to a role, run on its config
agent.improve(eval_set, role="Researcher", epochs=10)   # gated evolution REWRITES the role file
```

`.improve` evolves the role's prompt, admits each candidate through the LEARN gate,
and on `ACCEPT` writes the better prompt **back to the file on disk** — you review
it as a `git diff`. The policy is on disk, the history is in the log: nothing hidden.

## Usage by module

One short example per module. Blocks that reach a real backend construct the
shipped `OpenAIChatClient` / `OpenAIEmbedder` adapters and are marked
`# readme-skip-exec` (syntax-checked, not run offline); the deterministic,
zero-LLM blocks run offline in the README test as written.

### `context` — deterministic, zero-LLM compaction
```python
from agentkit import compact, merge
r = compact(messages, keep=1)              # keep the last turn verbatim; summarize the rest
print(r.text, r.est_tokens_after)
r = merge(r, compact(later_messages))      # fold a newer compaction into an older one
```

### `memory` — tiered episodic/semantic store
```python
# readme-skip-exec
from agentkit import MemoryStore, OpenAIEmbedder
mem = MemoryStore("mem.db", embedder=OpenAIEmbedder(model="bge-m3-mlx-fp16"))
mem.add("semantic", "Always validate inputs before parsing.")
hits = mem.search("input handling", top_k=4)        # vector recall -> list[MemoryEntry]
prompt_block = mem.inject_context("input handling", k=4)   # ready-to-prompt context string
```

### `runtime` — durable DAG (survives `kill -9`)
```python
from agentkit import GraphStore
gs = GraphStore("runs.db")
dag = {"nodes": {"fetch": {"type": "tool"}, "parse": {"type": "tool"}},
       "edges": [["fetch", "parse"]]}               # fetch -> parse
gid = gs.create_graph("pipeline", dag)
rid = gs.start_run(gid, trigger="manual")
node = gs.claim_ready_node(rid, worker_id="w1")     # demand-driven; recoverable
gs.mark_done(rid, "fetch", {"ok": True})            # unlocks 'parse'
```

### `agent` — ReAct loop + router + roles + batch
```python
# readme-skip-exec
from agentkit import (run_agent, run_agent_stream, route, run_role, dispatch,
                      RESEARCHER, run_batch, BatchConfig, OpenAIChatClient)
client = OpenAIChatClient(model="Qwen2.5-Coder-7B-Instruct-MLX-4bit")
res = run_agent("What is 2+2?", client=client,
                tools={"add": lambda a: {"sum": a["a"] + a["b"]}})
print(res.answer)
route("hard")                                       # -> RouteDecision (which reasoning tier)
role = dispatch("review this draft")                # keyword heuristic -> AgentRole (no LLM)
run_role(RESEARCHER, "survey vector DBs", client=client)   # a role is config over run_agent
run_batch(items, lambda x: run_agent(x, client=client).answer,   # return JSON-serializable
          output_path="out.jsonl", failures_path="fail.jsonl", config=BatchConfig())
for chunk in run_agent_stream("research X", client=client):
    ...                                             # streaming: partial ChatChunks then AgentResult (TTFT)
```

### `orchestrator` — long-horizon autonomy (pure, model-free control)
```python
from agentkit import assess, is_novel, similarity, cascade, Rubric, Dimension
a = assess(new_findings=0, stale_count=3)           # -> StallAssessment (pivot/escalate/stop)
is_novel("try GraphRAG", tried=["try vector RAG"], threshold=0.6)   # diversity gate
rubric = Rubric((Dimension("relevance", "Relevance", 1.0),))   # (key, name, weight)
cascade(items, predicate=lambda x: True, rubric=rubric,
        scorer=lambda x, r: {"relevance": 0.9})     # prefilter -> rank (cheap before LLM)
```

### `quality` — source-grounding verification
```python
from agentkit import verify
findings = verify(text, sources={"[1]": "the cited source text"})  # client optional: deterministic citation/link checks run with no model
for f in findings:                                  # uncited claims, dead links, unsupported claims
    print(f)                                        # each VerifyFinding is severity-graded
```

### `topology` — pick a multi-agent shape, generate its DAG
```python
# readme-skip-exec
from agentkit.topology import infer_spec, select_topology, generate_dag
from agentkit import OpenAIChatClient
client = OpenAIChatClient(model="Qwen2.5-Coder-7B-Instruct-MLX-4bit")
spec = infer_spec("compare A and B then summarize", client=client)  # -> TaskSpec
choice = select_topology(spec)                      # rule-driven -> TopologyChoice (STAR/MESH/...)
dag, n_calls = generate_dag(choice, spec, llm=False)   # config-as-policy: the DAG as data

# dynamic per-step topology (Phase 8): each plan step gets its own shape
from agentkit import plan, assign_topologies, run_plan
p = assign_topologies(plan("compare X and Y, then write a brief"), mode="auto")
#   -> 'compare' step = MESH, 'write' step = single (deterministic keyword cues, 0 LLM)
result = run_plan(p, client=client)                 # runs each step under its own topology
```

### `config` — roles as declarative files (re-plan Phase 1)
```python
from agentkit.config import load_default_roles, load_roles, dump_role, load_role
roles = load_default_roles()                        # the shipped feynman ensemble, from files
my = load_roles("./agent_config/roles")             # your YAML/JSON role folder -> {name: AgentRole}
dump_role(roles["Researcher"], "researcher.yaml")   # round-trips load_role (YAML needs [config] extra)
```

### `sandbox` — contained execution (re-plan Phase 2)
```python
from agentkit.sandbox import SubprocessSandbox
sb = SubprocessSandbox()                            # argv-not-shell, cwd-jailed, timeout, output-capped
r = sb.run("print('hi')", timeout=5, cwd=".")       # -> ExecResult(stdout, stderr, exit_code, duration)
print(r.exit_code, r.stdout)                         # "; rm -rf" in code is inert — no shell
```

### `gates` — the LEARN admission gate (re-plan Phase 3)
```python
from agentkit.gates import run_gate, Outcome
from agentkit.sandbox import SubprocessSandbox
v = run_gate({"type": "skill", "code": "print('ok')"}, baseline_score=0.5,
             sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9)
print(v.status, v.stage)                            # syntax->containment->execute->regression->safety->delta
assert v.status in (Outcome.ACCEPT, Outcome.REJECT, Outcome.ESCALATE)
```

### `backends` — a CLI as the model (no API key)
```python
from agentkit import CliLLMClient
client = CliLLMClient(...)        # wraps `claude -p` / `codex exec`; argv, no shell-injection surface
# pass `client` anywhere an LLMClient is expected (run_agent, run_role, verify, gates safety...)
```

### `evolve` + `skills` — text-space optimization against the gate
```python
# readme-skip-exec
from agentkit.evolve import evolve_prompt
from agentkit.skills import SkillLibrary
from agentkit import OpenAIEmbedder
res = evolve_prompt("You are an agent.", propose=my_proposer, evaluate=my_scorer,
                    gate=my_gate, baseline_score=0.5, epochs=5)
print(res.best, res.delta)                          # best variant kept only if it passed the gate
lib = SkillLibrary(embedder=OpenAIEmbedder(model="bge-m3-mlx-fp16"), directory="skills/")
lib.retrieve("summarize a PDF", k=3)                # semantic recall of curated, gate-passed skills
```

### `planner` — self-plan a task into a runtime config
```python
from agentkit.planner import plan, plan_to_graph_config
p = plan("fetch the data and then parse it")        # -> Plan (validated subtask DAG)
cfg = plan_to_graph_config(p)                       # -> {nodes, edges} for GraphStore.create_graph
```

### `codegen` — agent-authored, sandbox-validated tools
```python
# readme-skip-exec
from agentkit.codegen import ToolForge
from agentkit.sandbox import SubprocessSandbox
from agentkit.gates import Gate
from agentkit import OpenAIChatClient
forge = ToolForge(client=OpenAIChatClient(model="Qwen2.5-Coder-7B-Instruct-MLX-4bit"),
                  sandbox=SubprocessSandbox(),
                  gate=Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 1.0))
tool = forge.forge("a tool that adds two numbers")  # query->schema->code->validate->repair->gate
forge.register(tool, registry)                       # registers ONLY if the gate returned ACCEPT
```

### `selfimproving` — the facade (the whole loop, one object)
```python
# readme-skip-exec
from agentkit import SelfImprovingAgent, OpenAIChatClient, OpenAIEmbedder
agent = SelfImprovingAgent.from_config("./agent_config",
                                       backend=OpenAIChatClient(model="Qwen2.5-Coder-7B-Instruct-MLX-4bit"),
                                       embedder=OpenAIEmbedder(model="bge-m3-mlx-fp16"))
agent.run("research X and write a brief")           # config-driven role dispatch + memory
agent.improve(eval_set, role="Researcher", epochs=10)   # gated prompt evolution -> rewrites the role FILE
agent.skills.retrieve("how to cite sources")        # the curated skill library
agent.forge_tool("compute IRR")                     # forge a new tool, sandboxed + gated
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

## The self-improving layer

The core modules are *static*: a human writes the roles, tools, and topology. The
self-improving layer makes the **policy surface a folder of config files the agent
can improve on its own** — behind a sandbox it can't escape and a gate it can't
override. Built in seven phases (`config` → `sandbox` + `gates` → `evolve` +
`skills` → `planner` → `codegen` → the `SelfImprovingAgent` facade), each
self-modification admitted by the deterministic LEARN gate and reversible. An
end-to-end test drives the whole loop — `from_config` → `run` → gated `improve`
(rewrites the role file on disk) → `skills` → `forge_tool` → planned DAG on the
durable runtime. Full design, build order, and security model:
[`docs/REPLAN-agentkit.md`](docs/REPLAN-agentkit.md).

## Engineering-pattern compliance

Audited against the curriculum's **Engineering Decision Patterns** + **Bad-Case
Journal**. The auditable safety / optimizer / read-side gaps are closed:

- **P39 — fan-out cost ceiling** (`orchestrator/fanout.py`: sums child tokens, aborts a runaway fan-out).
- **P43 — streaming / TTFT** (`run_agent_stream` + the `stream_chat` seam).
- **P42 — atomic writes** (temp + `os.replace` for state artifacts; no torn `progress.json`).
- **P45 — group-relative distillation** (`evolve.distill_group`: keep above-group-mean lessons, weight-free).
- **memory/quality read-side** — provenance tags, earned read-retention, topic-presence abstention, a cheap-first keyword→vector ladder, a union-vs-single guard, and eval-saturation detection.

Patterns specific to RAG-corpus tuning, multi-tenant isolation, or proxy
deployment are out of scope for a single-tenant, local-first library.

## Install

```bash
pip install -e .                # core (numpy only)
pip install -e ".[openai]"      # + the openai SDK, if you build an OpenAI adapter
pip install -e ".[config]"      # + pyyaml, for YAML role config files
pip install -e ".[dev]"         # + pytest, pyyaml
```

Python 3.11+. **342 tests** pass (`pytest`).
