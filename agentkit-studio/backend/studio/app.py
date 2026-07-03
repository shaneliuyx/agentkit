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
import hmac
import json
import os
import threading
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from agentkit.types import LLMClient

from agentkit.artifacts.patcher import cleanup_orphaned_tmp
from studio.backends import (
    build_chat_client,
    build_embedder,
    list_embedders,
    list_profiles,
    resolve_backend,
)
from studio.events import StudioEvent
from studio.export import run_to_loop, run_to_research_package
from studio.loops import CatalogClient
from studio.models import LoopConfig
from studio.runner import Runner
from studio.session import SessionRegistry, flatten_chat_to_requirement
from studio.skills_paths import build_path_skills
from studio.templates import TemplateStore
from studio.workspace import workspace_root

#: Sentinel pushed onto the event queue to signal stream completion.
_STREAM_DONE = object()

app = FastAPI(title="AgentKit Studio", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

registry = SessionRegistry()

#: Loaded once on first /loops or seed call (fetch + 24h disk cache).
_catalog: CatalogClient | None = None


@app.on_event("startup")
def _startup_cleanup() -> None:
    """Remove *.tmp orphans left by a crash mid-atomic-rename."""
    cleanup_orphaned_tmp(workspace_root())


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
    loop_config = LoopConfig.from_dict(body.get("loop_config") or {})

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
        loop_config=loop_config,
    )
    from studio.rubric import DEFAULT_TEMPLATE, DEFAULT_WEIGHTS, default_scoring_matrix

    session.rubric_config = {
        "weights": DEFAULT_WEIGHTS,
        "template": DEFAULT_TEMPLATE,
        "scoring_template": DEFAULT_TEMPLATE,
        "scoring_matrix": default_scoring_matrix("general", DEFAULT_TEMPLATE),
        "report_type": "general",
    }
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
async def get_run(
    session_id: str, requirement: str, history: str | None = None
) -> EventSourceResponse:
    """Stream the ordered SSE event sequence for ``requirement``.

    The runner runs on a worker thread; events are pushed onto an ``asyncio``
    queue from that thread (thread-safe via ``call_soon_threadsafe``) and yielded
    here as SSE frames.

    ``history`` (optional, JSON-encoded ``[{role, content}]``): when the ChatPanel
    (DESIGN §6) sends a multi-turn thread, the backend flattens it via
    ``flatten_chat_to_requirement`` and PREPENDS it so the planner sees every
    refinement, not just the final message. Absent/blank → ``requirement`` is used
    verbatim (unchanged single-textarea contract).
    """
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")

    if history:
        try:
            _turns = json.loads(history)
            _flat = flatten_chat_to_requirement(_turns) if isinstance(_turns, list) else ""
            if _flat:
                requirement = f"{_flat}\n\n[CURRENT REQUEST]: {requirement}"
        except (ValueError, TypeError):
            pass  # malformed history → fall back to bare requirement
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
# /chat
# ---------------------------------------------------------------------------

@app.post("/session/{session_id}/chat")
def post_chat(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Follow-up chat grounded in the finished run result.

    ``body`` shape: ``{message: str, history: [{role, content}]}``.
    Returns ``{reply: str}``.
    """
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    if session.last_run is None:
        raise HTTPException(status_code=409, detail="no finished run in this session")

    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message is required")
    history: list[dict[str, Any]] = body.get("history") or []

    backend = resolve_backend(session.llm_spec)
    client = build_chat_client(backend, on_usage=lambda _: None, temperature=0.3)

    system = (
        "You are a helpful research assistant. The following document was produced by a "
        "multi-agent research system. Use it as the sole source of truth when answering "
        "follow-up questions. Be concise and accurate.\n\n"
        f"--- RESULT ---\n{session.last_run.result}\n--- END RESULT ---"
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        *({"role": turn["role"], "content": turn["content"]} for turn in history),
        {"role": "user", "content": message},
    ]

    result = client.chat(messages)
    return {"reply": result.text}


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
    """Loop-library path skills plus domain skills (e.g. research-report-agent)."""
    from studio.skills_paths import build_domain_skills

    skills = [
        {"name": s.name, "description": s.description, "source": "builtin", "kind": "path"}
        for s in build_path_skills()
    ]
    skills += [
        {"name": s.name, "description": s.description, "trigger": s.trigger,
         "source": "builtin", "kind": "domain"}
        for s in build_domain_skills()
    ]
    return {"skills": skills}


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


@app.get("/export/{session_id}/research-package")
def get_research_package_export(session_id: str) -> dict[str, Any]:
    """Serialize a finished run into a research-report package draft."""
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    if session.last_run is None or not session.last_run.plan_steps:
        raise HTTPException(
            status_code=409,
            detail="session has no finished run to export; start a run first",
        )
    return {"package": run_to_research_package(session.last_run)}


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


# ---------------------------------------------------------------------------
# /session/{id}/goal — set or clear the LoopGoal for a session
# ---------------------------------------------------------------------------

@app.post("/session/{session_id}/goal")
def set_goal(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Set or clear the LoopGoal for a session.

    Body: {end_state, evidence_cmd?, success_pattern?, constraints?,
           max_turns?, max_tokens?, timeout_s?}
    Send {} or {end_state: ""} to clear.
    """
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    end_state = body.get("end_state", "")
    if not end_state:
        session.goal = None
        return {"cleared": True}

    try:
        from agentkit.loop.goal import LoopGoal
        session.goal = LoopGoal(
            end_state=end_state,
            evidence_cmd=body.get("evidence_cmd") or None,
            success_pattern=body.get("success_pattern") or None,
            constraints=tuple(body.get("constraints") or []),
            max_turns=int(body.get("max_turns", 25)),
            max_tokens=int(body.get("max_tokens", 100_000)),
            timeout_s=float(body.get("timeout_s", 1800.0)),
        )
    except (ImportError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"set": True, "end_state": session.goal.end_state}




# ---------------------------------------------------------------------------
# /session/{id}/goal/suggest — LLM-inferred LoopGoal parameters
# ---------------------------------------------------------------------------

@app.post("/session/{session_id}/goal/suggest")
def suggest_goal_params(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Use the session's LLM to suggest LoopGoal parameters from an end_state description.

    Body: {end_state: str, task?: str}
    Returns: {evidence_cmd, success_pattern, max_turns, max_tokens, timeout_s, constraints[]}
    """
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    end_state = (body.get("end_state") or "").strip()
    task = (body.get("task") or "").strip()
    if not end_state:
        raise HTTPException(status_code=422, detail="end_state is required")


    try:
        from agentkit.loop.suggest import suggest_goal_params
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="agentkit.loop not installed") from exc

    backend = resolve_backend(session.llm_spec)
    client = build_chat_client(backend, on_usage=lambda _: None, temperature=0.2)
    s = suggest_goal_params(end_state, client, task=task)
    return {
        "evidence_cmd":    s.evidence_cmd,
        "success_pattern": s.success_pattern,
        "max_turns":       s.max_turns,
        "max_tokens":      s.max_tokens,
        "timeout_s":       s.timeout_s,
        "constraints":     list(s.constraints),
    }


@app.delete("/session/{session_id}/goal")
def clear_goal(session_id: str) -> dict[str, Any]:
    """Remove the active LoopGoal from a session."""
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    session.goal = None
    return {"cleared": True}

# ---------------------------------------------------------------------------
# /scheduler — read registered Scheduler triggers
# ---------------------------------------------------------------------------

@app.get("/scheduler")
def get_scheduler() -> dict[str, Any]:
    """Return current scheduler trigger list (stub — wire via agentkit.runtime.scheduler)."""
    return {
        "triggers": [],
        "note": "Wire cron/webhook triggers via agentkit.runtime.scheduler.Scheduler",
    }


# ---------------------------------------------------------------------------
# /chain/run — synchronous LoopChain execution
# ---------------------------------------------------------------------------

@app.post("/chain/run")
def run_chain(body: dict[str, Any]) -> dict[str, Any]:
    """Run a LoopChain described as a JSON spec.

    Body: {
      "specs": [
        {"name": "step1", "description": "...", "depends_on": []},
        {"name": "step2", "description": "...", "depends_on": ["step1"]}
      ],
      "initial_ctx": {"task": "..."}
    }
    """
    try:
        from agentkit.loop.chain import LoopChain, LoopSpec
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"agentkit.loop not installed: {exc}") from exc

    specs_raw: list[dict[str, Any]] = body.get("specs", [])
    initial_ctx: dict[str, Any] = body.get("initial_ctx", {})

    if not specs_raw:
        raise HTTPException(status_code=400, detail="specs must be non-empty")

    chain = LoopChain()
    for s in specs_raw:
        name = s.get("name", "")
        if not name:
            raise HTTPException(status_code=400, detail="each spec must have a name")
        description = s.get("description", name)
        depends_on = tuple(s.get("depends_on") or [])

        def _make_runner(desc: str):  # type: ignore[no-untyped-def]
            def _run(ctx: dict) -> dict:
                return {"description": desc, "status": "stub — wire a real runner"}
            return _run

        try:
            chain.add(LoopSpec(name=name, runner=_make_runner(description), depends_on=depends_on))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = chain.run(initial_ctx)
    return {
        "status": result.status,
        "outputs": {
            k: {kk: str(vv)[:500] for kk, vv in v.items()}
            for k, v in result.outputs.items()
        },
        "results": [
            {
                "name": r.name,
                "skipped": r.skipped,
                "verdict": r.verdict.reason if r.verdict else "no goal",
            }
            for r in result.results
        ],
    }


# /chain/suggest — LLM-inferred LoopChain DAG spec from a task description
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/chain/suggest")
def suggest_chain(body: dict[str, Any]) -> dict[str, Any]:
    """Use an LLM to decompose a task into a LoopChain DAG spec."""
    task = str(body.get("task") or "").strip()
    if not task:
        raise HTTPException(status_code=422, detail="task required")

    from agentkit.loop.suggest import suggest_chain_spec
    from studio.backends import resolve_backend, build_chat_client

    # Use the first available LLM backend to generate the suggestion.
    try:
        from studio.catalog import _catalog  # type: ignore[attr-defined]
        backends = list(_catalog.values())
    except Exception:
        backends = []

    if not backends:
        # Fallback: single-step spec
        return {
            "specs": [{"name": "run", "description": task, "depends_on": []}],
            "initial_ctx": {"task": task},
        }

    backend = resolve_backend(backends[0].get("spec") or backends[0])
    client = build_chat_client(backend, on_usage=lambda _: None, temperature=0.3)
    suggestion = suggest_chain_spec(task, client)
    return {
        "specs": list(suggestion.specs),
        "initial_ctx": suggestion.initial_ctx,
    }


@app.post("/session/{session_id}/hill-climb")
def set_hill_climb(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Configure hill-climb auto-improve for a session.

    Body: {score_metric?, min_improvement?, max_epochs?, auto_improve?}
    Subsequent runs for the same task will score, mine weaknesses, and (when
    auto_improve=true) seed the next run from the prior artifact.
    """
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    session.hill_climb_config = {
        "score_metric": str(body.get("score_metric") or "quality"),
        "min_improvement": float(body.get("min_improvement") or 0.02),
        "max_epochs": int(body.get("max_epochs") or 5),
        "auto_improve": bool(body.get("auto_improve", False)),
        # Optional explicit seed file: point the run at any artifact on disk,
        # overriding exact-hash + semantic DB seeding (the escape hatch when a
        # weak same-task lineage would otherwise block a stronger seed).
        "seed_path": (str(body.get("seed_path")).strip() or None) if body.get("seed_path") else None,
    }
    # Agent Sizing sliders (min/max tasks per agent, max-agents cap) ride in the
    # same panel payload, but the runner reads them from loop_config
    # (_sizing_cfg = loop_config.sizing()), NOT hill_climb_config — so sync them
    # here or they are silently dropped (they only matched the 3/5 defaults by
    # coincidence before). 2026-06-27 menu-configurable cap fix.
    _lc = getattr(session, "loop_config", None) or LoopConfig()
    for _k in ("min_tasks_per_agent", "max_tasks_per_agent", "max_agents"):
        if body.get(_k) is not None:
            setattr(_lc, _k, int(body[_k]))
    session.loop_config = _lc
    return {"status": "ok", "hill_climb_config": session.hill_climb_config}


@app.get("/rubric/defaults")
def rubric_defaults() -> dict[str, Any]:
    """Default rubric weights + deliverable template, so the GUI can seed the rubric
    panel with the same values the scorer uses (DESIGN §14.2)."""
    from studio.rubric import DEFAULT_TEMPLATE, DEFAULT_WEIGHTS, default_scoring_matrix
    from studio.report_profiles import profile_template_presets

    return {
        "weights": DEFAULT_WEIGHTS,
        "template": DEFAULT_TEMPLATE,
        "scoring_template": DEFAULT_TEMPLATE,
        "scoring_matrix": default_scoring_matrix("general", DEFAULT_TEMPLATE),
        "report_type": "general",
        "template_presets": profile_template_presets(),
    }


#: Env var holding the shared admin key for catalog mutation/export routes.
_ADMIN_KEY_ENV = "STUDIO_ADMIN_KEY"


def require_catalog_admin(
    x_studio_admin_key: str | None = Header(default=None),
) -> None:
    """Auth gate for catalog template mutation/export routes (finding 1).

    These routes operate on the shared TemplateStore (task_runs.db) and can export
    skeletons or poison future runs, yet the app otherwise has no auth. Protection is
    OPT-IN: set ``STUDIO_ADMIN_KEY`` in the backend env to require callers to send a
    matching ``X-Studio-Admin-Key`` header (403 on missing/mismatch). Unset = the
    historical keyless localhost-dev default (the GUI sends no key), so existing
    deployments and tests are unaffected until a key is configured."""
    expected = os.environ.get(_ADMIN_KEY_ENV)
    if not expected:
        return  # no key configured → keyless dev default (see docstring)
    if not x_studio_admin_key or not hmac.compare_digest(x_studio_admin_key, expected):
        raise HTTPException(status_code=403, detail="invalid or missing X-Studio-Admin-Key")


@app.post("/catalog/templates/audit")
def audit_templates(
    body: dict[str, Any] | None = None, _: None = Depends(require_catalog_admin)
) -> dict[str, Any]:
    """Audit stored report templates and disable unsafe skeletons."""
    report_type = str((body or {}).get("report_type") or "general")
    audited = TemplateStore().audit_templates(report_type)
    return {
        "status": "ok",
        "report_type": report_type,
        "audited": audited,
        "disabled": sum(1 for row in audited if row.get("status") == "disabled"),
    }


@app.get("/catalog/templates")
def list_templates(status: str | None = None) -> dict[str, Any]:
    """Read-only template inventory for catalog management."""
    templates = TemplateStore().list_templates(status=status)
    return {"templates": templates, "count": len(templates)}


@app.post("/catalog/templates/{template_id}/replace")
def replace_template(
    template_id: int, body: dict[str, Any], _: None = Depends(require_catalog_admin)
) -> dict[str, Any]:
    """Replace one stored template skeleton while preserving its catalog row."""
    try:
        template = TemplateStore().replace_template(
            template_id,
            str(body.get("skeleton") or ""),
            report_type=str(body.get("report_type") or "general"),
            source=str(body.get("source") or "approved"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if template is None:
        raise HTTPException(status_code=404, detail="template not found")
    return {"status": "ok", "template": template}


@app.post("/catalog/templates/{template_id}/approve")
def approve_template(
    template_id: int,
    body: dict[str, Any] | None = None,
    _: None = Depends(require_catalog_admin),
) -> dict[str, Any]:
    """Approve a clean catalog template for future automatic reuse."""
    body = body or {}
    score = body.get("quality_score")
    try:
        template = TemplateStore().approve_template(
            template_id,
            approved_by=str(body.get("approved_by") or ""),
            quality_score=float(score) if score is not None else None,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="quality_score must be numeric") from exc
    if template is None:
        raise HTTPException(status_code=404, detail="template not found")
    return {"status": "ok", "template": template}


@app.get("/catalog/templates/export")
def export_templates(
    status: str | None = None, _: None = Depends(require_catalog_admin)
) -> dict[str, Any]:
    """Export catalog templates, including full skeleton bodies."""
    templates = TemplateStore().export_templates(status=status)
    return {"templates": templates, "count": len(templates)}


@app.post("/catalog/templates/import")
def import_templates(
    body: dict[str, Any], _: None = Depends(require_catalog_admin)
) -> dict[str, Any]:
    """Import catalog templates and audit them before activation."""
    templates = body.get("templates")
    if not isinstance(templates, list):
        raise HTTPException(status_code=422, detail="templates must be a list")
    try:
        imported = TemplateStore().import_templates(
            [item for item in templates if isinstance(item, dict)]
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "ok",
        "templates": imported,
        "imported": sum(1 for row in imported if row.get("imported")),
        "skipped": sum(1 for row in imported if not row.get("imported")),
    }


@app.post("/session/{session_id}/rubric")
def set_rubric(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Set the GUI rubric + deliverable template for a session (DESIGN §14.2).

    Body: {"weights": {criterion: float}, "template": [section, ...],
    "report_type": "technical"}. Template wins when provided; otherwise report_type
    selects a built-in profile template.
    """
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    from studio.rubric import default_scoring_matrix, resolve_scoring_matrix, resolve_weights
    from studio.report_profiles import resolve_report_profile

    weights = body.get("weights")
    template = body.get("template")
    report_type = str(body.get("report_type") or "general")
    profile = resolve_report_profile(report_type)
    resolved_template = (
        [str(s) for s in template]
        if isinstance(template, list)
        else list(profile.sections)
    )
    scoring_template = (
        [str(s) for s in body.get("scoring_template")]
        if isinstance(body.get("scoring_template"), list)
        else list(resolved_template)
    )
    scoring_matrix = (
        resolve_scoring_matrix(body.get("scoring_matrix"), scoring_template)
        if isinstance(body.get("scoring_matrix"), list)
        else default_scoring_matrix(profile.report_type, scoring_template)
    )
    session.rubric_config = {
        # Normalize now so a bad GUI payload can't break the gate mid-run.
        "weights": resolve_weights(weights if isinstance(weights, dict) else None),
        "template": resolved_template,
        "scoring_template": scoring_template,
        "scoring_matrix": scoring_matrix,
        "report_type": profile.report_type,
    }
    return {"status": "ok", "rubric_config": session.rubric_config}


@app.get("/task-runs/{task_hash_str}")
def get_task_runs(task_hash_str: str) -> dict[str, Any]:
    """Return all recorded runs for a task hash (cross-session version history)."""
    from studio.task_runs import TaskRunStore
    store = TaskRunStore()
    runs = store.all_runs(task_hash_str)
    return {
        "task_hash": task_hash_str,
        "runs": [
            {
                "version": r.version,
                "session_id": r.session_id,
                "score": r.score,
                "weaknesses": r.weaknesses,
                "artifact_path": r.artifact_path,
                # entry 166: distinguish failed_partial rows from normal history.
                "status": r.status,
            }
            for r in runs
        ],
    }
