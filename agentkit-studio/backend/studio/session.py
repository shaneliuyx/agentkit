"""studio.session — session lifecycle, run registry, cooperative-cancel flags.

One ``Session`` per ``POST /session``: it holds the resolved backend spec, the
embedder info, the run mode + budget ceiling, and a cooperative-cancel flag the
runner polls at the top of each phase loop (SPEC §5.3). One run per session —
the registry rejects a second concurrent ``/run`` for the same session.

Cancel uses the shared ``interrupt_state`` vocabulary: a session carries an
``InterruptStateSnapshot`` and ``request_cancel`` flips it to graceful-stop. The
runner reads ``cancel_requested`` rather than poking the snapshot directly.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from studio.shared_bridge import InterruptStateSnapshot, get_interrupt_disposition


@dataclass
class Session:
    """A configured studio session (mutable run-level state)."""

    session_id: str
    llm_spec: dict[str, Any]
    embed_spec: dict[str, Any]
    llm_info: dict[str, Any]
    embed_info: dict[str, Any]
    mode: str = "auto"
    budget_ceiling: float | None = None
    #: Cooperative-cancel snapshot (graceful-stop semantics from shared infra).
    interrupt: InterruptStateSnapshot = field(
        default_factory=lambda: InterruptStateSnapshot(
            status="running", graceful_stop_requested=False
        )
    )
    #: True while a /run stream is active for this session (one run at a time).
    running: bool = False

    @property
    def cancel_requested(self) -> bool:
        """True once a graceful stop has been requested (runner polls this)."""
        return self.interrupt.graceful_stop_requested

    def request_cancel(self) -> str:
        """Flip the interrupt snapshot to graceful-stop; return the disposition."""
        disposition = get_interrupt_disposition(self.interrupt)
        self.interrupt = replace(self.interrupt, graceful_stop_requested=True)
        return disposition


class SessionRegistry:
    """In-memory session store. Thread-safe for the FastAPI worker + run thread.

    The registry is process-local (Studio is a single-user dev tool, SPEC §5.4);
    nothing here is persisted.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        llm_spec: dict[str, Any],
        embed_spec: dict[str, Any],
        llm_info: dict[str, Any],
        embed_info: dict[str, Any],
        mode: str,
        budget_ceiling: float | None,
    ) -> Session:
        """Register a new session and return it."""
        session = Session(
            session_id=f"s_{uuid.uuid4().hex[:12]}",
            llm_spec=llm_spec,
            embed_spec=embed_spec,
            llm_info=llm_info,
            embed_info=embed_info,
            mode=mode,
            budget_ceiling=budget_ceiling,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def begin_run(self, session_id: str) -> Session:
        """Mark a session's run as active; raise if it is already running.

        Enforces the one-run-per-session rule (SPEC §9 concurrency).
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            if session.running:
                raise RuntimeError(f"session {session_id} already has an active run")
            session.running = True
            return session

    def end_run(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.running = False
