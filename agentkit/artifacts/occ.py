"""agentkit.artifacts.occ — Optimistic Concurrency Control for shared artifacts.

Concurrent workers (threads) can modify the same artifact.md without conflicts:

  1. Worker calls ``read_artifact(path)``  → gets ``ReadResult(content, hash)``
     (blocks while another write is in progress — same lock as writes)
  2. Worker does its analysis + web search (no lock held)
  3. Worker calls ``patch_artifact(path, find, replace, expected_hash)``
     → acquires lock → re-reads → checks hash matches → applies find/replace
     → writes → returns ``PatchResult(success=True, new_hash=...)``
     If another worker wrote first, returns ``PatchResult(success=False,
     reason='hash_mismatch', content=<fresh>, new_hash=...)`` so the caller
     can re-analyze and retry without a second round-trip.

Both read and write acquire the same per-path ``threading.Lock``, ensuring
reads never observe a half-written file.

This is in-process only (threads). For cross-process safety pair with
``agentkit.runtime.file_lock.FileLock`` on a sentinel path.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

_locks: dict[str, threading.Lock] = {}
_locks_mu = threading.Lock()


def get_lock(path: str) -> threading.Lock:
    """Return the per-path lock, creating it on first use. Thread-safe."""
    with _locks_mu:
        if path not in _locks:
            _locks[path] = threading.Lock()
        return _locks[path]


def artifact_hash(content: str) -> str:
    """Short MD5 of content (12 hex chars) — fast, sufficient for OCC tokens."""
    return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()[:12]


@dataclass
class ReadResult:
    """Return value of ``read_artifact``."""

    content: str
    hash: str


@dataclass
class PatchResult:
    """Return value of ``patch_artifact``.

    On success: ``success=True``, ``new_hash`` set, ``reason`` and ``content`` None.
    On failure: ``success=False``, ``reason`` is ``'hash_mismatch'`` or
    ``'find_not_matched'``. On ``hash_mismatch``, ``content`` and ``new_hash``
    carry the current file state so the caller can retry without re-reading.
    """

    success: bool
    new_hash: str
    reason: str | None = None
    content: str | None = None


def read_artifact(path: Path) -> ReadResult:
    """Read artifact content under the per-path lock.

    Blocks while a ``patch_artifact`` write is in progress so callers never
    observe a half-written file.
    """
    lock = get_lock(str(path))
    with lock:
        content = path.read_text(encoding="utf-8")
        h = artifact_hash(content)
    return ReadResult(content=content, hash=h)


def patch_artifact(
    path: Path,
    find: str,
    replace: str,
    expected_hash: str,
) -> PatchResult:
    """Atomically apply a find/replace patch with OCC hash check.

    Acquires the per-path lock, re-reads the file, verifies the hash matches
    ``expected_hash`` (from the caller's prior ``read_artifact`` call), then
    applies ``content.replace(find, replace, 1)`` and writes.

    Returns ``PatchResult(success=True, new_hash=...)`` on success.
    Returns ``PatchResult(success=False, reason='hash_mismatch', ...)`` when
    another writer modified the file first — caller uses the returned
    ``content``/``new_hash`` to re-analyze and retry.
    Returns ``PatchResult(success=False, reason='find_not_matched')`` when
    ``find`` is absent from the current content.
    """
    lock = get_lock(str(path))
    with lock:
        content = path.read_text(encoding="utf-8")
        current_hash = artifact_hash(content)
        if current_hash != expected_hash:
            return PatchResult(
                success=False,
                new_hash=current_hash,
                reason="hash_mismatch",
                content=content,
            )
        if find not in content:
            return PatchResult(
                success=False,
                new_hash=current_hash,
                reason="find_not_matched",
            )
        new_content = content.replace(find, replace, 1)
        path.write_text(new_content, encoding="utf-8")
        new_hash = artifact_hash(new_content)
    return PatchResult(success=True, new_hash=new_hash)
