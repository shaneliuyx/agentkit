"""Studio scheduler triggers + in-memory chain registry (real, not a stub).

Replaces the old ``GET /scheduler`` stub and the missing ``POST /scheduler/cron``
route. A cron trigger registers a (cron_expr, chain_id) pair; when armed it fires
the SAVED chain of that id on a self-rescheduling ``threading.Timer`` (mirrors
``agentkit.runtime.scheduler.Scheduler``'s cron design — register is complete on
its own, arming is a separate explicit step). Chains are saved by id when run
through ``/chain/run`` with a ``chain_id``.

Process-local + in-memory: a durable store is a future enhancement, but
register / list / delete / fire are fully real here (no placeholder returns).
The tick function ``fire`` is directly callable so firing is unit-testable
without waiting on real timers.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

# chain_id -> {"specs": [...], "initial_ctx": {...}}
_chains: dict[str, dict[str, Any]] = {}
# chain_id -> {"cron_expr": str, "chain_id": str, "interval_s": float, "runs": int}
_triggers: dict[str, dict[str, Any]] = {}
_timers: dict[str, threading.Timer] = {}
_lock = threading.Lock()

#: cron ticks default to this period — this runtime ships no full cron parser
#: (APScheduler is deliberately avoided, matching agentkit.runtime.scheduler).
_DEFAULT_INTERVAL_S = 60.0
#: floor on the tick period — a real LLM call fires every tick, so an unclamped
#: tiny interval_s would be a self-rescheduling runaway (cost/resource foot-gun).
_MIN_INTERVAL_S = 5.0

#: minimal cron parser: only the common ``*/N * * * *`` (every N minutes) and
#: ``* * * * *`` (every minute) forms are honored (this runtime ships no full cron
#: parser — APScheduler is deliberately avoided). Anything else is flagged
#: ``cron_parsed=False`` so the API/GUI can surface that firing falls back to the
#: default period rather than silently misreading e.g. "0 9 * * *" as every 60s.
import re as _re  # noqa: E402

_EVERY_N_MIN_RE = _re.compile(r"^\*/(\d+)\s+\*\s+\*\s+\*\s+\*$")
_EVERY_MIN_RE = _re.compile(r"^(?:\*\s+){4}\*$")


def _cron_to_interval(cron_expr: str) -> float | None:
    """Return the tick period (seconds) for a supported cron form, else None."""
    expr = " ".join(cron_expr.split())
    if _EVERY_MIN_RE.match(expr):
        return 60.0
    m = _EVERY_N_MIN_RE.match(expr)
    if m:
        return max(1, int(m.group(1))) * 60.0
    return None


def save_chain(chain_id: str, specs: list[dict[str, Any]], initial_ctx: dict[str, Any],
               llm_spec: dict[str, Any] | None = None) -> None:
    """Persist a chain spec under ``chain_id`` so a cron trigger can fire it. The
    ``llm_spec`` is stored too so a scheduled firing uses the SAME model the
    interactive run used (not a silent fall-back to the default profile)."""
    with _lock:
        _chains[chain_id] = {
            "specs": list(specs),
            "initial_ctx": dict(initial_ctx or {}),
            "llm_spec": dict(llm_spec) if llm_spec else None,
        }


def get_chain(chain_id: str) -> dict[str, Any] | None:
    with _lock:
        c = _chains.get(chain_id)
        return dict(c) if c else None


def register_cron(chain_id: str, cron_expr: str, interval_s: float | None = None) -> dict[str, Any]:
    """Register (or replace) a cron trigger for ``chain_id``. Real storage; returns
    the stored trigger record. Does NOT arm the timer (call ``arm``). The tick
    period is an explicit ``interval_s``, else derived from a supported cron form,
    else the default — always clamped to ``_MIN_INTERVAL_S`` and never a runaway."""
    if not chain_id or not str(chain_id).strip():
        raise ValueError("chain_id is required")
    if not cron_expr or not str(cron_expr).strip():
        raise ValueError("cron_expr is required")
    derived = _cron_to_interval(cron_expr)
    cron_parsed = derived is not None
    period = float(interval_s) if interval_s else (derived or _DEFAULT_INTERVAL_S)
    rec = {
        "chain_id": chain_id,
        "cron_expr": cron_expr,
        "interval_s": max(_MIN_INTERVAL_S, period),
        "cron_parsed": cron_parsed,  # False → cron_expr unsupported, fires on interval_s/default
        "runs": 0,
        "failures": 0,
        "last_error": None,
    }
    with _lock:
        _triggers[chain_id] = rec
    return dict(rec)


def list_triggers() -> list[dict[str, Any]]:
    """Return all registered triggers (real list — the old GET /scheduler was a stub)."""
    with _lock:
        return [dict(r) for r in _triggers.values()]


def delete_trigger(chain_id: str) -> bool:
    """Remove a trigger and cancel its timer. Idempotent; returns whether it existed.
    Pops ``_triggers`` FIRST so an in-flight tick's ``finally`` re-arm guard fails
    (no extra fire / orphaned timer after delete), THEN cancels the timer."""
    with _lock:
        existed = _triggers.pop(chain_id, None) is not None
    disarm(chain_id)
    return existed


def fire(chain_id: str, run_chain: Callable[..., Any]) -> dict[str, Any]:
    """Fire one run of the saved chain NOW (the tick body; directly callable in tests).
    Honest no-op with a reason when the trigger's chain was never saved — never a
    silent stub. ``run_chain(specs, initial_ctx, llm_spec)`` does the real execution
    with the SAME model the chain was saved with."""
    saved = get_chain(chain_id)
    if saved is None:
        return {"chain_id": chain_id, "fired": False,
                "reason": "no saved chain for this id (run it once via /chain/run with a chain_id)"}
    result = run_chain(saved["specs"], saved["initial_ctx"], saved.get("llm_spec"))
    with _lock:
        if chain_id in _triggers:
            _triggers[chain_id]["runs"] += 1
    return {"chain_id": chain_id, "fired": True, "result": result}


def arm(chain_id: str, run_chain: Callable[..., Any]) -> None:
    """Arm a self-rescheduling timer for a registered trigger. Each tick fires the
    chain and re-arms (daemon timer, no separate process — mirrors Scheduler.start_cron).
    A failing tick is caught + recorded on the trigger (``failures``/``last_error``,
    surfaced by GET /scheduler) rather than spamming an uncaught thread traceback;
    the schedule self-heals (re-arm is in ``finally``)."""
    with _lock:
        rec = _triggers.get(chain_id)
    if rec is None:
        raise KeyError(f"no trigger registered for chain_id: {chain_id}")
    interval = rec["interval_s"]

    def _tick() -> None:
        try:
            fire(chain_id, run_chain)
        except Exception as exc:  # noqa: BLE001 — a background tick must never die loud
            with _lock:
                if chain_id in _triggers:
                    _triggers[chain_id]["failures"] += 1
                    _triggers[chain_id]["last_error"] = repr(exc)[:300]
        finally:
            with _lock:
                if chain_id in _triggers:  # not deleted meanwhile
                    t = threading.Timer(interval, _tick)
                    t.daemon = True
                    _timers[chain_id] = t
                    t.start()

    disarm(chain_id)
    t = threading.Timer(interval, _tick)
    t.daemon = True
    with _lock:
        _timers[chain_id] = t
    t.start()


def disarm(chain_id: str) -> None:
    """Cancel a trigger's running timer. Idempotent."""
    with _lock:
        t = _timers.pop(chain_id, None)
    if t is not None:
        t.cancel()


def _reset_for_tests() -> None:
    """Clear all state (timers cancelled). Test-only."""
    with _lock:
        ids = list(_timers.keys())
    for cid in ids:
        disarm(cid)
    with _lock:
        _chains.clear()
        _triggers.clear()
        _timers.clear()
