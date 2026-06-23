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
uv pip install pytest httpx  # dev/test deps

# Offline unit tests (no API key, no local services needed):
.venv/bin/python -m pytest tests -q

# Run the dev server (serves the Vite frontend on :5173 via CORS):
.venv/bin/uvicorn studio.app:app --reload --port 8000
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

## The 7 comprehensive panels

Memory, Self-improve/Re-plan, Evolve, Security spine, DAG, Verification, Router —
each its own `backend/studio/panels/*.py` module, fed during/after the run. They
wrap real agentkit machinery (`MemoryStore`, `orchestrator.stall.assess`,
`evolve.distill_group`, `gates.run_gate` + `SubprocessSandbox`, `GraphStore`,
`quality.verify`, `agent.router.route`) — Studio is the glue, not a reimplementation.

## Frontend

The frontend (React + Vite + TypeScript, React Flow + anime.js + Zustand) lives
in `frontend/` and is built separately. The backend serves it via permissive
localhost CORS for `:5173`.
