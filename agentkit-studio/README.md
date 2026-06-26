# AgentKit Studio

A GUI agent platform over **agentkit** (the Protocol-seam library) and
**agent-prep/shared** (battle-tested lab infra). You type a requirement; Studio
plans it into phases, assigns each phase an agent topology, runs it, and streams
a live 2D topology graph + an honest token meter, ending in a verified result.

See [`SPEC.md`](./SPEC.md) for the full design authority.

## Backend quickstart

The backend is a FastAPI server that streams an SSE event sequence (`GET /run`)
and accepts a cooperative cancel (`POST /cancel`).

```bash
cd backend
uv venv .venv
uv pip install -e .          # installs agentkit (editable) + fastapi + sse-starlette + openai
uv pip install curl_cffi     # web fetch support (tool loop page fetching)
uv pip install pytest httpx  # dev/test deps

# Offline unit tests (no API key, no local services needed):
.venv/bin/python -m pytest tests -q

# Run the dev server (serves the Vite frontend on :5173 via CORS):
# NOTE: port 8770 — :8000 is occupied by oMLX (the local model server).
.venv/bin/uvicorn studio.app:app --reload --port 8770
```

### Endpoints

| Method | Route | Purpose |
|---|---|---|
| `GET`  | `/backends` | The `PROFILES` menu + embedders (GUI dropdown source) |
| `POST` | `/session` | Build a session from a backend spec; runtime-checks `LLMClient` |
| `GET`  | `/run/{session_id}?requirement=...` | `text/event-stream` of the ordered event sequence |
| `POST` | `/cancel/{session_id}` | Cooperative graceful-stop |
| `GET`  | `/artifacts/{session_id}` | Panel backfill (re-hydrate after reconnect) |
| `GET`  | `/phoenix` | Phoenix UI link-out if the tracer is up on `:6006` |

### Event contract

Every SSE frame is `{type, session_id, ts, payload}`. The full type catalogue
and per-phase ordering guarantee live in [`SPEC.md`](./SPEC.md) §4 and are
mirrored 1:1 by `backend/studio/events.py` (Python) and the frontend's
`api/types.ts` (TypeScript). Ordering per run:

```
session → plan → topology → graph
  → (per phase: phase_start, router, memory, token…, phase_done, dag,
                selfimprove, evolve, gate)
  → budget? → verify → done
```

### Backends

The backend menu comes from `agent-prep/shared/llm.py` `PROFILES`:
`haiku`/`opus` (VibeProxy → Claude) and `14b`/`qwen` (local oMLX). Studio builds
a usage-capturing `StudioChatClient` from the resolved `(base_url, model, key)`,
so the token HUD gets the prompt/completion split that agentkit's own
`OpenAIChatClient` discards. A backend without usage telemetry flips the run to
a sticky `~estimated` meter (token honesty, SPEC §7).

### Local services (optional)

The Memory, DAG, and Phoenix panels assume oMLX `:8000`, Qdrant `:6333`, and
Phoenix `:6006` (the agent-prep smoke-test stack). Each panel degrades
gracefully — an empty panel + a notice — when its service is down, so a run
never crashes on a missing local service.

## The 14 comprehensive panels

The original 7 — Memory, Self-improve/Re-plan, Evolve, Security spine, DAG,
Verification, Router — each its own `backend/studio/panels/*.py` module, fed
during/after the run. They wrap real agentkit machinery (`MemoryStore`,
`orchestrator.stall.assess`, `evolve.distill_group`, `gates.run_gate` +
`SubprocessSandbox`, `GraphStore`, `quality.verify`, `agent.router.route`) —
Studio is the glue, not a reimplementation.

Three more landed with the Loop Library integration (M7–M8): **Loops**
(catalog browse/find + seed a run), **Tools** (web_search + workspace-jailed
read/write file activity), and **Loop Doctor** (the run audited against
bounded / material-checks / safe-actions / clear-stopping).

Four more added with the Loop Engineering shared library (`agentkit.loop`):
**Goal** (live stop-condition verdict after each phase), **Hill Climb** (epoch
scores from `hill_climb_from_traces`), **Scheduler** (registered cron/webhook
triggers), and **Chain** (JSON editor + live per-spec result stream). Configure
all of them from the **⚙ Loop** button in the header. See §3.8 for details.

## Frontend

The frontend (React + Vite + TypeScript, React Flow + anime.js + Zustand) lives
in `frontend/` and is built separately. The backend serves it via permissive
localhost CORS for `:5173`.

---

# User Manual

A complete guide to installing, configuring, and operating AgentKit Studio.

## 1. Installation

### 1.1 Prerequisites

| Requirement | Why | Notes |
|---|---|---|
| **Python ≥ 3.11** | Backend (FastAPI) | `uv` recommended for the venv |
| **[uv](https://github.com/astral-sh/uv)** | Venv + editable installs | `brew install uv` |
| **Node ≥ 18 + npm** | Frontend (Vite) | `node -v` to check |
| **agentkit checkout** | Protocol spine (installed editable) | this repo's parent `agentkit/` |
| **agent-prep/shared** | Backend menu + token honesty | imported via `sys.path` shim, no install |
| An **LLM endpoint** | To actually run | local oMLX, or VibeProxy→Claude, or any OpenAI-compatible URL |
| **scrapling** (optional) | Stronger `web_fetch` on JS / anti-bot pages | `web_fetch` works without it (stdlib `urllib` fallback). Install only if pages render empty: `uv pip install "scrapling[all]" && scrapling install` (or set `SCRAPLING_BIN`) |

### 1.2 Backend

```bash
cd backend
uv venv .venv
uv pip install -e .          # agentkit (editable) + fastapi + sse-starlette + openai
uv pip install curl_cffi     # web fetch support (tool loop page fetching)
uv pip install pytest httpx  # dev/test deps

# Sanity: offline unit tests (no API key, no services)
.venv/bin/python -m pytest tests -q
```

> **Editable agentkit matters.** `pyproject.toml` pins agentkit as an *editable*
> path dep (`[tool.uv.sources] … editable = true`). A frozen copy would miss
> later agentkit source changes (e.g. `agentkit.tools.fs`) and break imports at
> runtime. If you see `ImportError: cannot import name … from agentkit`, re-run
> `uv pip install -e .`.

### 1.3 Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # if present; otherwise create .env.local (see §2.2)
```

### 1.4 Run both servers

```bash
# Terminal 1 — backend on :8770  (NOT :8000, which oMLX owns)
cd backend && .venv/bin/uvicorn studio.app:app --reload --port 8770

# Terminal 2 — frontend dev server on :5173 (proxies /api → :8770)
cd frontend && npm run dev
```

Open **http://localhost:5173**.

## 2. Configuration

### 2.1 Ports

| Service | Port | Override |
|---|---|---|
| Backend (FastAPI) | `8770` | `uvicorn … --port N` + set `VITE_BACKEND_URL` |
| Frontend (Vite dev) | `5173` | `vite --port N` |
| oMLX model server | `8000` | external; do **not** put the backend here |
| Qdrant / Phoenix | `6333` / `6006` | optional; for Memory/DAG/Phoenix panels |

### 2.2 Frontend → backend wiring

`frontend/.env.local` (gitignored, no secrets — localhost URL only):

```
VITE_BACKEND_URL=http://localhost:8770
```

The Vite dev proxy maps `/api/*` → this URL, so the app uses same-origin
relative paths and there is no CORS dance in development.

### 2.3 Backends (LLM + embedder)

The model menu is sourced from `agent-prep/shared/llm.py` `PROFILES` and exposed
at `GET /backends`. In the UI, the **Backend panel** lets you pick:

- **`haiku` / `opus`** — VibeProxy → Claude (usage telemetry → exact token meter)
- **`14b` / `qwen`** — local oMLX (`:8000`)
- **Raw override** — paste a `base_url` + `model` + `api_key` for any
  OpenAI-compatible endpoint.

A backend that does **not** return usage telemetry flips the token HUD to a
sticky `~estimated` meter (token honesty, SPEC §7) — the `~` is your signal the
numbers are estimated, not billed.

An **embedder** (local oMLX by default) powers the Memory panel; if none is
configured, the Memory panel degrades to a notice instead of erroring.

### 2.4 Optional local services

| Panel | Needs | If down |
|---|---|---|
| Memory / RAG | oMLX `:8000` + Qdrant `:6333` | empty panel + notice |
| DAG | (derived from the plan) | always available |
| Phoenix link | Phoenix `:6006` | link hidden |

Nothing crashes a run when a service is missing — panels degrade gracefully.

## 3. Using AgentKit Studio

### 3.1 The basic loop

1. **Pick a backend** in the Backend panel (LLM + embedder). Optionally set a
   **token ceiling** in the Budget control — this bounds the run (and is the
   first thing Loop Doctor checks, §3.5).
2. **(Optional) Seed from a loop** — open the **Loops** panel, type what you want
   ("research and compare X vs Y"), hit *Find loops*, and *Seed this run* from a
   catalog match. Your plan is pre-filled from a published loop instead of cold
   decomposition; you can edit it before running.
3. **Type your requirement** in the run box and **Run**.
4. **Watch the graph** — the 2D topology graph draws phase nodes and their
   intra-phase agent fan-out (SINGLE / STAR / MESH / PIPELINE). Active phases
   pulse; the token meter counts up live.
5. **Read the result** — the run ends with a verified final output.

### 3.2 Reading the topology graph

- A **phase node** is one plan step; the badge shows its `N calls` (raw call
  count) and, if tools fired, a `🔍 web_search (2)` / `📄 read_file (1)` badge
  (or `🛠 N tools` when several ran).
- Edges between phases are `depends_on`; edges inside a phase are the topology
  fan-out (spokes/mesh/pipeline stages).
- A **"seeded from <loop>"** banner appears above the canvas when the run was
  seeded from a loop.

### 3.3 The panels

Open the panel drawer to inspect any dimension of the run:

| Panel | Shows |
|---|---|
| **Memory** | Retrieved memory entries (tier + score); notice if the embedder is down |
| **Self-improve** | Per-round re-plan assessments + actions when the run stalls |
| **Evolve** | Variant scores + deltas across improvement rounds |
| **Security** | Gate verdicts (`ACCEPT`/`REJECT`/`ESCALATE`) over phase outputs, sandboxed |
| **DAG** | The plan as a dependency graph with per-node status |
| **Verification** | Claims found in the output, whether each is supported, and uncited claims |
| **Router** | Per-phase difficulty → model tier routing |
| **Loops** | Browse/find the loop-library catalog; seed a run from a loop |
| **Tools** | Each web_search / read_file / write_file call: args, result summary, and any rejection or degradation notice |
| **Goal** | Live stop-condition verdict: `met` / `not yet met`, the evidence command output, and which phase triggered the check |
| **Hill Climb** | Epoch-by-epoch score + delta; scorer surfaces unmet criteria → miner uses them as grounding; weaknesses from ALL prior runs accumulated as hard constraints for the next attempt |
| **Scheduler** | All registered cron/webhook triggers with last-fired and next-fire timestamps |
| **Chain** | JSON editor for `LoopChain` specs + live per-spec status, skipped/done state, and output summary |

### 3.4 Tools (web + files)

When tools are enabled, agents can call `web_search` and the **workspace-jailed**
`read_file` / `write_file`. File tools are confined to a per-session workspace —
a path that escapes the jail (`..`, absolute, or symlink-out) is **refused**, and
the Tools panel renders that refusal in an amber warning (driven by an explicit
`rejected` flag, not text-guessing). A web search that falls back to a secondary
backend shows a degradation notice rather than failing the run.

`web_fetch` reads a page to clean markdown with **no required install**: it uses
the `scrapling` CLI when present (best for JS-rendered / anti-bot pages) and
otherwise degrades to a pure-stdlib `urllib` GET + HTML→markdown converter. The
fallback can't run JavaScript or evade anti-bot, so if a page comes back empty,
install scrapling (see §1.1) to upgrade that path.

### 3.5 Loop Doctor (health check)

Loop Doctor audits the run against the loop-library checklist, mapping each
dimension to a Studio primitive:

| Check | Pass when | Fix suggestion if not |
|---|---|---|
| **bounded** | a token ceiling is set | "Set a token ceiling in the Budget panel" |
| **material_checks** | verification produced findings | add verifiable claims |
| **safe_actions** | no gate escalated/rejected | names the escalated phase |
| **clear_stopping** | the plan is a finite DAG | resolve dangling/cyclic deps |

Each check shows a pass / warn / fail pill. **Repairs are suggestions only** —
Studio never auto-applies a change (matching loop-library's no-silent-change rule).

### 3.6 Export your run as a loop

After a run finishes, **Export as loop** downloads the run serialized into
loop-library's loop JSON (plan steps, topology, the requirement as
`description`/`useWhen`, Loop Doctor checks as `verification`). This closes the
loop: Studio both *consumes* published loops (§3.1) and *produces* new ones you
can publish or contribute back.

### 3.7 Cancel & budget

- **Cancel** a running session at any time — it stops cooperatively and the run
  ends marked `cancelled` with the partial result, not an error.
- If a **token ceiling** is exceeded mid-run, the run halts on `BudgetExceeded`
  and the Budget readout shows `exceeded`.


### 3.8 Loop Engineering — Goal, Scheduler, Chain

Click **⚙ Loop** in the header to open the Loop Config modal. Three tabs:

#### Goal tab — verifiable stop conditions

A `LoopGoal` replaces stall-based stopping with machine-checkable evidence.
`check_goal()` runs a shell command and matches its output against a regex —
**no LLM, no network**. The runner calls it after every phase.

| Field | Required | Description |
|---|---|---|
| **End state** | ✓ | Human-readable description of what "done" means |
| **Evidence command** | — | Shell command whose stdout is checked (`pytest -q`, `curl -sf …/health`) |
| **Success pattern** | — | Regex applied to stdout. If blank, exit code 0 = met |
| **Constraints** | — | One per line — shown to the LLM as invariants; not mechanically enforced |
| **Max turns** | — | Hard turn cap (default 25) |
| **Max tokens** | — | Hard token cap (default 100 000) |
| **Timeout (s)** | — | Wall-clock limit (default 1800) |

Common recipes:

| Use case | Evidence command | Success pattern |
|---|---|---|
| pytest passes | `pytest -q 2>&1 \| tail -1` | `\d+ passed` |
| HTTP health check | `curl -sf http://host/health` | `"status".*"ok"` |
| File exists + non-empty | `wc -c < output.md` | `[1-9]\d*` |
| Git commit present | `git log --oneline -1` | `feat:` |
| Status flag in file | `grep -q DONE STATUS.md && echo ok` | `ok` |
| Max-turns only (advisory) | *(leave blank)* | *(leave blank)* |

Click **Apply goal** to `POST /session/{id}/goal`. Click **Clear** to remove it
(`DELETE /session/{id}/goal`). The **Goal** panel tab shows the live verdict.

```bash
# Apply via curl (no UI)
curl -X POST http://localhost:8000/session/{SESSION_ID}/goal \
  -H "Content-Type: application/json" \
  -d '{
    "end_state": "All billing tests pass",
    "evidence_cmd": "pytest tests/billing -q 2>&1 | tail -1",
    "success_pattern": "\\d+ passed",
    "max_turns": 30
  }'

# Remove goal
curl -X DELETE http://localhost:8000/session/{SESSION_ID}/goal
```

#### Scheduler tab — automated triggers

Register a cron expression to fire a `LoopChain` automatically:

| Field | Example | Description |
|---|---|---|
| **Cron expression** | `0 2 * * *` | Standard 5-field cron — hourly, daily, etc. |
| **Chain ID** | `nightly-improve` | Which chain to run when the trigger fires |

The registered triggers appear in the table below the form and stream as
`scheduler` SSE events to the **Scheduler** panel tab.

Common cron patterns:

| Schedule | Expression |
|---|---|
| Every hour | `0 * * * *` |
| Every night at 02:00 UTC | `0 2 * * *` |
| Every Monday 09:00 | `0 9 * * 1` |
| Every 15 minutes | `*/15 * * * *` |

```bash
# Register via curl
curl -X POST http://localhost:8000/scheduler/cron \
  -H "Content-Type: application/json" \
  -d '{"spec": "0 2 * * *", "chain_id": "nightly-improve"}'

# List all triggers
curl http://localhost:8000/scheduler
```

#### Chain tab — compose DAG pipelines

Click **Chain** in the panel drawer to open the chain JSON editor. A chain
spec declares named nodes and their `depends_on` predecessors; outputs from
upstream nodes are injected into downstream runners.

Minimal example:

```json
{
  "specs": [
    { "name": "research",   "depends_on": [] },
    { "name": "synthesize", "depends_on": ["research"] },
    { "name": "deploy",     "depends_on": ["synthesize"] }
  ],
  "initial_ctx": { "task": "ship billing v2" }
}
```

Click **Run Chain** to `POST /chain/run`. Each spec fires a `ChainEvent` SSE
frame; the panel shows per-spec status (done / skipped) and output summary.
A spec is skipped automatically if any upstream node fails.

```bash
curl -X POST http://localhost:8000/chain/run \
  -H "Content-Type: application/json" \
  -d '{
    "specs": [
      { "name": "research",   "depends_on": [] },
      { "name": "synthesize", "depends_on": ["research"] }
    ],
    "initial_ctx": { "task": "analyze agent patterns" }
  }'
```

## 4. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Backend won't bind `:8770` | another process on the port | `lsof -i :8770`; kill or pick another port + update `VITE_BACKEND_URL` |
| Frontend calls 404 / connection refused | backend not running or wrong proxy | confirm backend on `:8770`; check `.env.local` |
| `ImportError … agentkit` | non-editable agentkit copy | `cd backend && uv pip install -e .` |
| Token meter shows `~` everywhere | backend returns no usage telemetry | expected on usage-less backends; switch to one with telemetry for exact counts |
| Memory panel empty + notice | oMLX/Qdrant down or no embedder | start the services, or ignore (degrades by design) |
| Port `:8000` "address in use" | you pointed the backend at oMLX's port | use `:8770` — see §2.1 |
