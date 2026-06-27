"""agentkit.tools.fetch_cache — in-flight URL dedup for parallel workers.

Prevents the same URL being fetched multiple times when parallel agents in the
same phase start simultaneously and all miss the disk cache (the classic
dog-pile problem). The first caller fetches; concurrent callers block on a
threading.Event and share the result when it arrives.

This is phase-scoped: create one InFlightRegistry per phase (or per runner)
and discard it after the phase completes so stale results don't bleed across
phases.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class InFlightRegistry:
    """Dog-pile prevention for concurrent URL fetches.

    Thread-safe: multiple worker threads may call ``get_or_fetch`` concurrently
    for the same or different keys.

    Usage::

        registry = InFlightRegistry()

        def fetch():
            return requests.get(url).text

        result = registry.get_or_fetch(url, fetch)

    The first caller for a key executes ``fetch_fn``; all subsequent callers for
    the same key block until the result is available, then receive the same
    cached value. The result is retained in memory for the lifetime of this
    registry instance.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._results: dict[str, Any] = {}

    def get_or_fetch(self, key: str, fetch_fn: Callable[[], T]) -> T:
        """Return the cached result for ``key``, fetching it if necessary.

        If another thread is already fetching ``key``, this call blocks until
        that fetch completes, then returns the shared result.
        """
        with self._lock:
            if key in self._results:
                return self._results[key]  # already done
            if key in self._events:
                event: threading.Event | None = self._events[key]  # in-flight: wait
            else:
                event = threading.Event()
                self._events[key] = event
                event = None  # sentinel: this thread is the fetcher

        if event is not None:
            event.wait()
            return self._results[key]

        # This thread is the fetcher.
        try:
            result = fetch_fn()
            with self._lock:
                self._results[key] = result
            return result
        finally:
            with self._lock:
                ev = self._events.pop(key, None)
            if ev is not None:
                ev.set()  # unblock all waiters

    def clear(self) -> None:
        """Reset cached results (useful between phases)."""
        with self._lock:
            self._results.clear()
