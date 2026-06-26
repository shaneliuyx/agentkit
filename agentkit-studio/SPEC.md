# AgentKit Studio — SPEC

A GUI agent platform over **agentkit** (Protocol-seam library) + **agent-prep/shared**
(battle-tested lab infra). User types a requirement; the system plans it into phases,
assigns a per-phase agent topology, deploys/runs it, and streams a live 2D topology
graph + honest token meter, ending in a verified result.

Status: design authority for the build. Code lives beside this file in
`/Users/yuxinliu/code/agentkit/agentkit-studio/`.

---

## 1. Scope (locked decisions)

- **Topology viz:** 2D animated graph — **React Flow** (structure) + **anime.js** (motion).
- **API coverage:** **comprehensive** — every relevant agentkit export + shared module.
- **Frontend stack:** **React + Vite + TypeScript**, Zustand store.
- **Transport:** **SSE** down (`GET /run`) + `POST /cancel` up (cooperative cancel via
  `agent_loop_tools/interrupt_state`). No WebSocket.
- **Ships:** core surfaces (topology graph, token HUD, stream pane) **+ all 7 comprehensive panels**
  (Memory, Self-improve/Re-plan, Evolve, Security spine, DAG, Verification, Router).

---

## 2. Two-layer infrastructure map

Everything below already exists — Studio is the glue + the FastAPI/React layer.

### agentkit (`/Users/yuxinliu/code/agentkit/agentkit`) — Protocol spine
| Need | Symbol | Notes |
|---|---|---|
| Pluggable LLM/embed | `types.LLMClient`, `types.Embedder` (`@runtime_checkable`) | inject, never import vendor |
| Chat result shape | `types.ChatResult` (`text`, `tool_calls`, `total_tokens`) | total only — no split |
| Streaming seam | `types.stream_chat`, `ChatChunk`, `supports_streaming` | back-compat one-shot wrap |
| OpenAI adapter | `backends.openai_compat.OpenAIChatClient` / `OpenAIEmbedder` / `make_client` | wraps any OpenAI-compatible endpoint |
| Anthropic adapter | `backends.anthropic_client.AnthropicChatClient` | native Claude |
| CLI adapter | `backends.CliLLMClient` | subprocess backend (no usage → estimated) |
| Requirement→phases | `planner.plan(task, *, decomposer=None)` → `Plan` (`steps: PlanStep[]`) | default offline; inject LLM decomposer |
| Phase→topology | `topology.dynamic.assign_topologies(plan, *, mode, client, llm, fixed)` | `mode="auto"` 0-LLM default; `llm=True` for richer |
| Topology classify | `topology.dynamic.classify_step_topology(desc)` → SINGLE/STAR/MAP/MESH/PIPELINE | keyword heuristic; MAP triggers on "each"/"every"/"map" — fans out one worker per upstream item |
| Deploy/run | `topology.dynamic.run_plan(plan, client, *, budget, max_workers)` → `DynamicPlanResult` | **synchronous**, dispatches per topology |
| Step result | `topology.dynamic.StepRun` (`step_id`, `description`, `topology`, `output`, `n_agents`, `tokens`, `wall_s`) | per-phase render data |
| Autonomous loop | `orchestrator.run`, `OrchestratorConfig`, `assess`, `StallAssessment`, `ProgressState`, `Finding`, `init_task`, `log_event` | self-improve / re-plan events |
| Budget ceiling | `orchestrator.FanoutBudget` (`add`, `spent_total`, `ceiling`), `BudgetExceeded` | cloud cost gauge |
| Roles | `agent.roles.run_role`, `dispatch`, `RESEARCHER`/`REVIEWER`/`WRITER`/`VERIFIER`, `AgentRole` | role specialization |
| Difficulty router | `agent.route`, `agent.run_agent`, `run_agent_stream`, `AgentResult`, `run_batch` | router-trace panel |
| Quality | `quality.verify`, `extract_claims`, `find_uncited`, `VerifyFinding`, `Claim` | verification panel |
| Memory | `memory.MemoryStore`, `MemoryEntry` | memory panel |
| Context compaction | `context.compact`, `merge`, `CompactResult` | (used internally; optional surface) |
| Durable DAG | `runtime.GraphStore`, `Scheduler`; `planner.plan_to_graph_config(plan)` → `{nodes, edges}` | DAG panel |
| Self-improving facade | `selfimproving.SelfImprovingAgent` | re-plan panel |
| Evolve | `evolve.distill_group`, `evolve.core` | evolve panel |
| Security spine | `gates.run_gate`, `Outcome`, `sandbox.SubprocessSandbox`, `sandbox.net_guard` | security panel |
| Roles config | `config.load_default_roles`, `load_roles`, `dump_role` | role editor |

### agent-prep/shared (`/Users/yuxinliu/code/agent-prep/shared`) — lab infra (import via `sys.path`)
| Need | Symbol | Notes |
|---|---|---|
| Backend preset menu | `llm.PROFILES` (`haiku`/`opus` via VibeProxy :8317, `14b`/`qwen` via oMLX :8000), `llm.resolve(role, default)` | GUI dropdown source |
| Endpoint defaults | `llm.make_client`, `_default_base` env chain | raw `openai.OpenAI` |
| Resilient calls | `llm.resilient`, `LLMUnavailable` | skip-don't-crash |
| **Token split + honesty** | `agent_loop_tools.TokenAccounting`, `UsageReport(input_tokens, output_tokens, estimated)` | sticky `~` estimated flag; `__add__` merge; `summary_line()` |
| **Cooperative cancel** | `agent_loop_tools.interrupt_state` | flip to stop a run cleanly |
| Hybrid RAG | `rag_hybrid/` (`encoder` BGE-M3, `rerank`, `fusion`, `retrieve`, `ingest`, `chunking`) | richer retrieval than `memory` |
| Tree-index RAG | `tree_index/` (PageIndex: `agentic`, `ensemble`, `summary`, `page_vector`) | alt retrieval |
| Web tools | `web_toolkit/` (`fetch`, `browse`, `_cache`) | agent tool-calls |
| Deep traces | `phoenix_tracing/` (Phoenix on :6006) | "Open in Phoenix" link-out |

> **Two `make_client`s, distinct roles.** `shared/llm.py.make_client` → raw `openai.OpenAI` + curated
> `PROFILES`. agentkit's `make_client` → wraps an endpoint into the `LLMClient` Protocol. Studio uses
> `PROFILES` as the **menu**, then builds the agentkit-Protocol client (or `StudioChatClient`, §5) from
> the resolved `(base_url, model, key)`.

---

## 3. Repo layout

```
agentkit-studio/
  SPEC.md                  # this file
  README.md                # quickstart
  backend/
    pyproject.toml         # fastapi, uvicorn, sse-starlette, agentkit[openai,anthropic]
    studio/
      __init__.py
      app.py               # FastAPI app + routes
      shared_bridge.py     # sys.path shim → import shared/llm, token_accounting, interrupt_state
      backends.py          # PROFILES menu → StudioChatClient / Embedder factory
      client.py            # StudioChatClient(LLMClient): usage-capturing wrapper (§5)
      session.py           # Session lifecycle, run registry, interrupt flags
      runner.py            # the step-loop driver that emits events (§4, §6)
      events.py            # SSE event schema (dataclasses) — THE contract
      task_runs.py         # cross-session hill-climb store: SQLite task_runs.db,
                           # score_result(result, req, client) → (float, unmet_str),
                           # mine_weaknesses(outputs, result, req, client, scorer_feedback=""),
                           # TaskRunStore.{best,latest,all_runs} — all_runs used to
                           # accumulate weaknesses across every prior attempt (not just best)
      panels/              # one module per comprehensive panel data-source
        memory.py  selfimprove.py  evolve.py  security.py  dag.py  verify.py  router.py
    tests/
      test_events.py  test_client.py  test_runner.py  test_backends.py
  frontend/
    package.json           # react, vite, typescript, reactflow, animejs, zustand
    vite.config.ts  tsconfig.json  index.html
    src/
      main.tsx  App.tsx
      api/sse.ts            # EventSource → typed StudioEvent union
      api/types.ts          # mirrors backend events.py (the contract, TS side)
      store/runStore.ts     # Zustand reducer over events
      components/
        config/BackendPanel.tsx      # PROFILES dropdown + raw override → POST /session
        config/RunBar.tsx            # requirement input, mode toggle, run/cancel
        graph/TopologyGraph.tsx      # React Flow canvas
        graph/topologyLayout.ts      # PlanStep+topology → nodes/edges (SINGLE/STAR/MESH/PIPELINE)
        graph/nodeAnim.ts            # anime.js: pulse running, edge dash-flow
        hud/TokenMeter.tsx           # in/out/total + budget gauge + ~estimated
        hud/StreamPane.tsx           # streamed text
        panels/MemoryPanel.tsx
        panels/SelfImprovePanel.tsx
        panels/EvolvePanel.tsx
        panels/SecurityPanel.tsx
        panels/DagPanel.tsx
        panels/VerifyPanel.tsx
        panels/RouterPanel.tsx
      styles/tokens.css  global.css
```

---

## 4. SSE event contract (THE interface — backend `events.py` ⇆ frontend `api/types.ts`)

Every frame: `{ "type": <str>, "session_id": <str>, "ts": <float>, "payload": {...} }`.
One event type per GUI concern → frontend reducer stays a flat switch.

| `type` | payload | drives |
|---|---|---|
| `session` | `{llm:{label,model}, embed:{label,model}, mode}` | header |
| `plan` | `{task, steps:[{id,description,depends_on,role,difficulty}]}` | graph phases |
| `topology` | `{steps:[{id,topology}]}` (post `assign_topologies`) | graph shapes |
| `graph` | `{nodes:[{id,kind,phase,label,state}], edges:[{from,to,kind}]}` | derived render graph |
| `phase_start` | `{step_id}` | node → running (pulse) |
| `agent_event` | `{step_id, name, data}` (forwarded `log_event`) | self-improve timeline |
| `token` | `{step_id, input, output, total, estimated, cumulative:{input,output,total,estimated}}` | token HUD |
| `text` | `{step_id, delta}` (streamed `ChatChunk.text`) | stream pane |
| `phase_done` | `{step_id, topology, n_agents, tokens, wall_s, output}` (from `StepRun`) | node → done |
| `budget` | `{spent, ceiling, exceeded}` | budget gauge |
| `router` | `{step_id, difficulty, tier}` | router panel |
| `memory` | `{entries:[{id,text,tier,score}], notice}` (`notice`: degradation message, `""` when healthy — SPEC §9) | memory panel |
| `selfimprove` | `{round, stalled, assessment, action}` (from `assess`/`StallAssessment`) | self-improve panel |
| `evolve` | `{round, score, delta, variant}` | evolve panel |
| `gate` | `{name, outcome, detail, sandboxed}` (from `run_gate`/`Outcome`) | security panel |
| `dag` | `{graph_id, nodes:[{id,status}], edges:[[from,to]]}` (from `GraphStore`) | DAG panel |
| `verify` | `{findings:[{claim,supported,sources}], uncited:[...]}` (from `verify`) | verify panel |
| `done` | `{total_tokens, input, output, estimated, wall_s, result, cancelled}` (`cancelled`: true when stopped via `/cancel` — SPEC §5.3) | end state |
| `error` | `{message, where}` | error toast |

Ordering guarantee per run: `session` → `plan` → `topology` → `graph` → (per phase:
`phase_start`, [`router`], [`agent_event`…], [`token`…], [`text`…], `phase_done`) →
[`budget`] → [panel events interleaved] → `verify` → `done`.

---

## 5. Backend design

### 5.1 `StudioChatClient` — the usage-capturing LLMClient (the ~30-line bridge)
agentkit's `OpenAIChatClient.chat` reads `r.usage.total_tokens` and **discards the split**.
Studio needs `prompt_tokens`/`completion_tokens` for the in/out meter, so wrap the same
`openai` client and capture usage:

```python
class StudioChatClient:  # satisfies agentkit types.LLMClient
    def __init__(self, model, *, base_url, api_key, on_usage, temperature=0.0, retries=4):
        self._client = agentkit.backends.openai_compat.make_client(base_url, api_key)
        self.model, self.temperature, self.retries, self._on_usage = ...
    def chat(self, messages, tools=None) -> ChatResult:
        r = self._client.chat.completions.create(model=self.model, messages=messages,
                                                 temperature=self.temperature, tools=tools or None)
        u = getattr(r, "usage", None)
        inp = getattr(u, "prompt_tokens", 0) or 0
        out = getattr(u, "completion_tokens", 0) or 0
        total = getattr(u, "total_tokens", 0) or (inp + out)
        self._on_usage(UsageReport(input_tokens=inp, output_tokens=out, estimated=(u is None)))
        return ChatResult(text=..., total_tokens=total, tool_calls=...)
```
- `on_usage` is the per-step callback that pushes a `token` SSE frame and feeds `TokenAccounting`.
- When `usage is None` (CLI/non-reporting backend) → `estimated=True` → sticky `~`.
- Reuse agentkit's resilient retry (`_resilient`) or shared's `resilient`.
- The **Anthropic** path mirrors this against `AnthropicChatClient` (`usage.input_tokens`/`output_tokens`).

### 5.2 Runner — studio drives the loop, agentkit runs the step
`run_plan` is synchronous and emits nothing mid-run, so the runner does:

```
plan_obj = plan(requirement, decomposer=<llm decomposer if mode=='llm' else None>)
emit plan
plan_obj = assign_topologies(plan_obj, mode='auto', client=client, llm=(mode=='llm'))
emit topology; emit graph(derive_render_graph(plan_obj))   # §6
acc = TokenAccounting(); outputs = {}
for step in plan_obj.steps:                      # already topo-sorted
    if interrupted(session): break
    emit phase_start(step.id)
    emit router(step.id, step.difficulty, route(...))           # router panel
    upstream = join(f"[{d}] {outputs[d]}" for d in step.depends_on if outputs.get(d))
    sub = Plan(task=plan_obj.task, steps=(replace(step, description=with_upstream(...), depends_on=()),))
    res = run_plan(sub, client, budget=budget, max_workers=N)   # real STAR/MESH/PIPELINE fan-out here
    sr = res.runs[0]; outputs[step.id] = sr.output
    emit phase_done(sr)
emit budget; run_panels(...); emit verify(verify(final_output)); emit done(acc, ...)
```
- `on_usage` callback (passed into `StudioChatClient`) fires `token` frames *during* `run_plan`,
  carrying `step_id` (closure over the current step) and `acc.add(usage)` for cumulative totals.
- Streaming text: if the chosen client `supports_streaming`, wrap each fan-out call through
  `stream_chat` and forward `ChatChunk.text` as `text` frames; else skip (per-phase granularity).
- The runner runs in a worker thread; events cross to the SSE generator via an `asyncio.Queue`.

### 5.3 Cancel
`POST /cancel/{session_id}` flips `interrupt_state` for that session; the runner checks it at the
top of each phase loop and (where supported) between orchestrator rounds, then stops cleanly and
emits a final `done` with partial results.

### 5.4 Endpoints
- `GET  /backends` → `{profiles:[{name,label,kind,model,endpoint}], embedders:[...]}` (from `PROFILES`).
- `POST /session` → body `{llm:{profile|raw{base_url,model,api_key}}, embed:{...}, mode:'auto'|'llm', budget:{ceiling|null}}`; builds `StudioChatClient` + embedder, runtime-checks `isinstance(c, LLMClient)`, returns `{session_id}`.
- `GET  /run/{session_id}?requirement=...` → `text/event-stream` (sse-starlette).
- `POST /cancel/{session_id}` → `{cancelled:true}`.
- `GET  /artifacts/{session_id}` → memory dump, GraphStore DAG, gate log (panel backfill).
- `POST /session/{session_id}/chat` → body `{message:str, history:[{role,content}]}` — one-shot follow-up grounded in `session.last_run.result`; returns `{reply:str}`. Requires a finished run (409 otherwise). Uses the session's own LLM backend at `temperature=0.3`. History is client-side — full prior turns sent each request.
- Phoenix link: `GET /phoenix` → `{url:"http://localhost:6006"}` if up.

### 5.5 Panels (comprehensive — each its own `panels/*.py`, fed during/after the run)
1. **Memory** — `MemoryStore` writes per phase; emit `memory` with entries + which were recalled.
2. **Self-improve / Re-plan** — drive a phase through `SelfImprovingAgent` / `orchestrator.run`; forward `assess`/`StallAssessment` as `selfimprove`; forward `log_event` as `agent_event`.
3. **Evolve** — run `evolve.distill_group` over a fan-out's candidates; emit `evolve` per round with score/delta.
4. **Security spine** — execute any tool/codegen step through `SubprocessSandbox` + `run_gate`; emit `gate` with `Outcome` + `net_guard` decisions.
5. **DAG** — `plan_to_graph_config(plan)` → `GraphStore.create_graph`; `Scheduler` status → `dag` frames.
6. **Verification** — `verify(final_output)` → `VerifyFinding[]` + `find_uncited`.
7. **Router** — `route(...)` per step → difficulty tier → `router` frames.

> Panel modules must **read the real signatures** of `selfimproving.py`, `gates/core.py`,
> `runtime/graph_store.py`, `evolve/core.py`, `orchestrator/loop.py`, `config/roles.py`
> before wiring — these were not pinned here; verify against source, do not guess.

---

## 6. Frontend design

- **State:** `runStore` (Zustand) — a reducer keyed on `event.type`. Holds `phases[]`,
  `graph{nodes,edges}`, `tokens{input,output,total,estimated}`, `budget`, per-panel arrays,
  `result`, `status`, `task` (from `plan` event), `pendingContinue` (one-shot signal: `ResultWindow` sets → `RunBar` consumes + clears to fire a new run).
- **Graph (`topologyLayout.ts`):** map each `PlanStep` to a **phase node**; expand its
  `topology` into intra-phase agent nodes/edges:
  - `SINGLE` → 1 agent node.
  - `STAR` → hub + N spokes + reduce node.
  - `MESH` → N nodes fully connected (debate) + reduce.
  - `PIPELINE` → chain of stage nodes.
  Inter-phase edges = `depends_on`. `n_agents` (from `phase_done`) reconciles the spoke count to
  the actual runtime fan-out.
- **Motion (`nodeAnim.ts`, anime.js):** pulse scale/opacity on `state==='running'`;
  stroke-dashoffset flow on active edges; count-up tween on the token meter. React Flow owns
  layout/structure, anime.js owns transitions — clean split.
- **Token HUD:** `"{input} in / {output} out · {total} total"`, prefixed `~` when `estimated`.
  Budget gauge = `spent/ceiling`; red on `exceeded`.
- **Panels:** tabbed drawer; each subscribes to its event type(s) from the store. Empty-state
  until its first event.

---

## 7. Token honesty (non-negotiable)

The meter reports `input`/`output` **exact** when the backend returns `usage`, and switches the
whole run to `~estimated` (sticky, never un-set) the moment any phase runs on a backend without
usage telemetry — `TokenAccounting`'s designed behavior. Never render estimated counts as exact.

---

## 8. Build milestones (each a verifiable vertical slice)

1. **Spine alive** — `events.py` + `StudioChatClient` + `runner` with a **fake** `LLMClient`;
   `GET /run` SSE emits the full ordered event sequence. *Verify: `curl` the stream; `test_runner.py` asserts order; no API key needed.*
2. **Graph renders** — frontend draws React Flow graph from `plan`+`topology`+`graph`.
   *Verify: all 4 topology kinds render from canned events.*
3. **Live state + token meter** — wire `phase_*`/`token`/`budget`; pulse + count-up.
   *Verify with real oMLX: HUD totals reconcile to `DynamicPlanResult.total_tokens`; `~` shows on CLI backend.*
4. **Backend swap** — `BackendPanel` from `PROFILES`; OpenAI/Anthropic/CLI + local embedder.
   *Verify: same requirement on two backends.*
5. **All 7 panels** — one slice per panel; each verified against its `/artifacts` data + live events.
6. **Polish** — anime.js motion, cancel, `BudgetExceeded` handling, Phoenix link, README quickstart.
7. **M7 — Loop Library: catalog-seeded planning + Loops panel** (§10).
8. **M8 — Loop Doctor: audit wired to gates/budget/verify** (§10).
9. **M9 — agentkit skills (5 paths) + export-run-as-loop** (§10).
10. **M10 — Chat panel + Continue run** — `POST /session/{id}/chat`; VS Code-style right-side panel; result as first assistant message; `↑ Send` for instant Q&A, `↻ Continue run` composes original-task + result context + follow-up into a new `/run`; MAP topology (fan-out one worker per upstream item).

---

## 9. Risks / open seams

- **`shared/` has no packaging** → import via `sys.path.insert(0, "/Users/yuxinliu/code/agent-prep/shared")`
  in `shared_bridge.py`. `# ponytail: add a pyproject to shared/ only if Studio ships independently.`
- **`run_plan` granularity** — token-by-token streaming only when the client implements `stream_chat`;
  otherwise per-phase updates (still correct, less granular). Don't promise a typewriter effect unless
  the adapter streams.
- **Panel signatures unpinned** — self-improve/gates/graphstore/evolve wired against source reads, not
  this spec. Verify before claiming done.
- **Concurrency** — agentkit run is sync; runner uses a thread + `asyncio.Queue`. One run per session
  (registry rejects a second concurrent `/run` for the same session).
- **Local services** — Memory/RAG/Phoenix panels assume oMLX :8000, Qdrant :6333, Phoenix :6006 per
  `agent-prep` smoke-test. Degrade gracefully (empty panel + notice) when a service is down.
- **Ports (live-verified)** — backend binds **`:8770`**, NOT `:8000` (oMLX, the model server, owns
  `:8000`). Frontend dev server `:5173` proxies `/api` → `:8770` via `VITE_BACKEND_URL`
  (`frontend/vite.config.ts` + `.env.local`). The original `:8000` proxy target was a collision
  caught by the M-live smoke-test, not by any offline test.

---

## 10. Loop Library integration (M7–M9, follow-on after MVP)

Source: `github.com/Forward-Future/loop-library` — a catalog of **loops** (bounded, repeatable
agent workflows with material checks, safe actions, and a clear stop condition) + an installable
guide skill. A Studio run (`plan → topology → run → verify`, bounded by `FanoutBudget`, gated by
`gates`/`sandbox`) **is** such a loop — so this is a native fit, not a bolt-on.

Live, machine-readable endpoints (consume read-only; cache locally; degrade offline):
- catalog JSON: `https://signals.forwardfuture.ai/loop-library/catalog.json`
- catalog text / agent instructions: `.../catalog.txt`, `.../llms.txt`, `.../agents/`

> **Verify at build time:** the exact `catalog.json` schema (loop fields) was NOT pinned here.
> Fetch it and read real fields before coding M7 — do not guess the loop shape.

### M7 — Catalog-seeded planning + Loops panel
- `backend/studio/loops.py` — catalog client: fetch + local cache (TTL), `find(requirement) -> [loop]`
  (the "Find" path), `adapt(loop, tools/limits) -> seed` (the "Adapt" path).
- Seeded decomposer: a chosen loop's steps become the `decomposer` injected into
  `planner.plan(task, decomposer=...)` → plan pre-filled from a published loop instead of cold
  decomposition. User edits before running.
- `frontend/.../panels/LoopsPanel.tsx` (8th panel) — browse/find catalog, pick a loop to seed a run.
- New events: `loops` `{matches:[{id,title,summary,url}]}`, `loop_seed` `{loop_id, steps}`.

### M8 — Loop Doctor: audit wired to Studio's safety spine
- Map loop-library's audit (bounded? material checks? unsafe actions? clear stopping?) onto Studio's
  existing primitives: bounded ⇆ `FanoutBudget.ceiling`; checks ⇆ `quality.verify` + `gates.run_gate`;
  safe actions ⇆ `sandbox.SubprocessSandbox` + `net_guard`; stop ⇆ orchestrator stall `assess`.
- `backend/studio/panels/loopdoctor.py` (or fold into `security.py`) — run the generated plan through
  the checklist; emit `loopdoctor` `{checks:[{name,status,fix}]}` to the Security/Verify panels.
- Repairs are surfaced as suggestions, not auto-applied (matches loop-library's no-silent-change rule).

### M9 — agentkit skills (5 paths) + export-run-as-loop
- Wrap the 5 paths (Discover/Find/Loop-Doctor/Adapt/Design) as entries in `agentkit/skills/core.py`
  so Studio agents can invoke them as skills. **Read `agentkit/skills/core.py` for its real registry
  API before wiring.**
- Export: serialize a finished run (`plan` + per-step `topology` + checks + budget) to loop-library's
  loop format (schema from the catalog) → `GET /export/{session_id}` → downloadable loop the user can
  publish/contribute. Closes the loop: Studio both *consumes* and *produces* loops.

### Dev-time aid (not a runtime dep)
The installable skill (`npx skills add Forward-Future/loop-library --skill loop-library --agent
claude-code -g -y`) is for the *developer agent* to design/audit Studio's own loops conversationally
(`/loop-library ...`). It is NOT installed into the Studio runtime — Studio integrates via
`catalog.json` + the code above.
