"""studio.app — the FastAPI app + routes (SPEC §5.4).

Endpoints:
  GET  /backends                 — the PROFILES menu + embedders
  POST /session                  — build a session (resolve backend, runtime-check)
  GET  /run/{session_id}         — text/event-stream of the ordered SSE sequence
  POST /cancel/{session_id}      — cooperative graceful-stop
  GET  /artifacts/{session_id}   — panel backfill (placeholder; live events are primary)
  GET  /phoenix                  — Phoenix link-out if the tracer is up

The agentkit run is synchronous; the runner executes on a worker thread and
events cross to the SSE generator through an ``asyncio.Queue`` (SPEC §9
concurrency). One run per session — the registry rejects a concurrent /run.

CORS is open for localhost dev (Vite on :5173).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from agentkit.types import LLMClient

from studio.backends import (
    build_chat_client,
    build_embedder,
    list_embedders,
    list_profiles,
    resolve_backend,
)
from studio.events import StudioEvent
from studio.export import run_to_loop
from studio.loops import CatalogClient
from studio.runner import Runner
from studio.session import SessionRegistry
from studio.skills_paths import build_path_skills

#: Sentinel pushed onto the event queue to signal stream completion.
_STREAM_DONE = object()

app = FastAPI(title="AgentKit Studio", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

registry = SessionRegistry()

#: Loaded once on first /loops or seed call (fetch + 24h disk cache).
_catalog: CatalogClient | None = None


def _get_catalog() -> CatalogClient:
    global _catalog
    if _catalog is None:
        _catalog = CatalogClient.load()
    return _catalog


# ---------------------------------------------------------------------------
# /backends
# ---------------------------------------------------------------------------

@app.get("/backends")
def get_backends() -> dict[str, Any]:
    """The GUI dropdown source: every PROFILES entry + the embedder menu."""
    return {"profiles": list_profiles(), "embedders": list_embedders()}


# ---------------------------------------------------------------------------
# /session
# ---------------------------------------------------------------------------

@app.post("/session")
def post_session(body: dict[str, Any]) -> dict[str, str]:
    """Build a session: resolve the backend, runtime-check ``LLMClient``.

    Body: ``{llm:{profile|raw}, embed:{...}, mode:'auto'|'llm', budget:{ceiling|null}}``.
    """
    llm_spec = body.get("llm") or {}
    embed_spec = body.get("embed") or {}
    mode = body.get("mode", "auto")
    budget = (body.get("budget") or {}).get("ceiling")
    tools_enabled = bool(body.get("tools_enabled", True))

    try:
        backend = resolve_backend(llm_spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Runtime-check the seam: a StudioChatClient must satisfy LLMClient.
    probe = build_chat_client(backend, lambda _u: None)
    if not isinstance(probe, LLMClient):
        raise HTTPException(status_code=500, detail="client does not satisfy LLMClient")

    _embedder, embed_info = build_embedder(embed_spec)
    llm_info = {"label": backend.label, "model": backend.model}

    session = registry.create(
        llm_spec=llm_spec,
        embed_spec=embed_spec,
        llm_info=llm_info,
        embed_info=embed_info,
        mode=mode,
        budget_ceiling=budget,
        tools_enabled=tools_enabled,
    )
    # Optionally seed from a chosen loop-library loop in the same request.
    loop_id = body.get("loop_id")
    if loop_id:
        _seed_session(session.session_id, loop_id)
    return {"session_id": session.session_id}


# ---------------------------------------------------------------------------
# /loops (M7 Wave 1 — loop-library catalog integration)
# ---------------------------------------------------------------------------

@app.get("/loops")
def get_loops(requirement: str) -> dict[str, Any]:
    """Match ``requirement`` against the loop-library catalog → top matches."""
    matches = _get_catalog().find(requirement)
    return {"matches": [m.to_dict() for m in matches]}


@app.post("/session/{session_id}/seed")
def post_seed(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Seed a session from a chosen loop. Body: ``{loop_id}``."""
    loop_id = body.get("loop_id")
    if not loop_id:
        raise HTTPException(status_code=400, detail="loop_id required")
    return _seed_session(session_id, loop_id)


def _seed_session(session_id: str, loop_id: str) -> dict[str, Any]:
    """Adapt a loop's steps and seed the session; raise 404 on unknown ids."""
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    catalog = _get_catalog()
    loop = catalog.get(loop_id)
    if loop is None:
        raise HTTPException(status_code=404, detail=f"unknown loop: {loop_id}")
    steps = catalog.adapt(loop)
    session.seed(loop_id, steps)
    return {"session_id": session_id, "loop_id": loop_id, "steps": steps}


# ---------------------------------------------------------------------------
# /run
# ---------------------------------------------------------------------------

@app.get("/run/{session_id}")
async def get_run(session_id: str, requirement: str) -> EventSourceResponse:
    """Stream the ordered SSE event sequence for ``requirement``.

    The runner runs on a worker thread; events are pushed onto an ``asyncio``
    queue from that thread (thread-safe via ``call_soon_threadsafe``) and yielded
    here as SSE frames.
    """
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    try:
        registry.begin_run(session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()

    def emit(event: StudioEvent) -> None:
        # Called from the worker thread → hop back to the event loop safely.
        loop.call_soon_threadsafe(queue.put_nowait, event)

    # Build the embedder once for the run (None on failure → memory panel notice).
    embedder, _info = build_embedder(session.embed_spec)

    def client_factory(on_usage):  # type: ignore[no-untyped-def]
        backend = resolve_backend(session.llm_spec)
        return build_chat_client(backend, on_usage)

    def worker() -> None:
        try:
            runner = Runner(
                session,
                emit,
                client_factory=client_factory,
                embedder=embedder,
            )
            runner.run(requirement)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _STREAM_DONE)
            registry.end_run(session_id)

    threading.Thread(target=worker, name=f"studio-run-{session_id}", daemon=True).start()

    async def event_generator():
        while True:
            item = await queue.get()
            if item is _STREAM_DONE:
                break
            ts = _now()
            # Emit an UNNAMED SSE frame: the browser's EventSource.onmessage only
            # fires for unnamed events — a named `event: <type>` line routes to a
            # typed listener the frontend never registers, so it would receive
            # nothing (0 tokens + SSE error). The type is already in the JSON
            # payload, so the event name is redundant.
            yield {"data": item.sse_data(session_id, ts)}

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# /cancel
# ---------------------------------------------------------------------------

@app.post("/cancel/{session_id}")
def post_cancel(session_id: str) -> dict[str, Any]:
    """Flip the session's cooperative-cancel flag (graceful stop)."""
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    disposition = session.request_cancel()
    return {"cancelled": True, "disposition": disposition}


# ---------------------------------------------------------------------------
# /skills + /export (M9 — loop-library paths + export-run-as-loop)
# ---------------------------------------------------------------------------

@app.get("/skills")
def get_skills() -> dict[str, Any]:
    """The 5 loop-library paths as agentkit skills: name + description each."""
    return {
        "skills": [
            {"name": s.name, "description": s.description} for s in build_path_skills()
        ]
    }


@app.get("/export/{session_id}")
def get_export(session_id: str) -> dict[str, Any]:
    """Serialize a session's finished run into a loop-library loop draft.

    409 when the session has not run (no plan/snapshot) — there is nothing to
    export until a run completes and records its ``RunSnapshot``.
    """
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    if session.last_run is None or not session.last_run.plan_steps:
        raise HTTPException(
            status_code=409,
            detail="session has no finished run to export; start a run first",
        )
    return {"loop": run_to_loop(session.last_run)}


# ---------------------------------------------------------------------------
# /artifacts + /phoenix
# ---------------------------------------------------------------------------

@app.get("/artifacts/{session_id}")
def get_artifacts(session_id: str) -> dict[str, Any]:
    """Panel backfill placeholder. Live SSE events are the primary panel feed;
    this endpoint exists for the frontend to re-hydrate after a reconnect."""
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return {"session_id": session_id, "memory": [], "dag": None, "gates": []}


@app.get("/phoenix")
def get_phoenix() -> dict[str, Any]:
    """Return the Phoenix UI link if the tracer is reachable on :6006."""
    url = "http://localhost:6006"
    return {"url": url, "up": _port_open("localhost", 6006)}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now() -> float:
    import time

    return time.time()


def _port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    """Cheap liveness probe for a local service (degrade-gracefully helper)."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
