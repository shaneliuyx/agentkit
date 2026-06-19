"""file_lock.py — a file-based distributed lock (advisory fcntl.flock).

Why a *file* lock and not a mutex: a mutex is in-process and dies with the
process. A distributed lock must be visible across processes AND release
itself when a holder dies. `fcntl.flock` gives us both for free: the lock is
associated with an open file descriptor, and when the process dies the kernel
closes the fd, which releases the lock. No deadlock-on-crash, zero daemons,
zero dependencies — the correct primitive for a local-first runtime.

(Extracted verbatim from agent-prep lab-04-6-durable-runtime.)
"""
from __future__ import annotations

import errno
import fcntl
import os
import time
from types import TracebackType


class LockTimeout(TimeoutError):
    """Raised when `acquire(timeout=...)` cannot get the lock in time."""


class FileLock:
    """Advisory cross-process lock over a sentinel file.

    Usage (context manager — the only correct way; guarantees release):
        lock = FileLock("/tmp/run.claim.lock")
        with lock:
            ...critical section: claim a node...
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self, timeout: float = 10.0, poll: float = 0.01) -> None:
        """Block until the lock is held or `timeout` seconds elapse.

        We open the fd once, then poll `flock(LOCK_EX | LOCK_NB)`. Non-blocking
        + poll (rather than a blocking `flock`) lets us honor a timeout and stay
        responsive to interrupts — a blocking flock cannot be timed out portably.
        """
        # O_CREAT so the sentinel file need not pre-exist; the fd, not the file
        # contents, carries the lock.
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError as exc:
                # EAGAIN/EACCES == "held by someone else"; anything else is real.
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    os.close(self._fd)
                    self._fd = None
                    raise
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise LockTimeout(f"could not acquire {self.path} in {timeout}s")
                time.sleep(poll)

    def release(self) -> None:
        """Release the lock and close the fd. Idempotent."""
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.release()


if __name__ == "__main__":
    import tempfile

    path = tempfile.mktemp(suffix=".lock")
    lock = FileLock(path)
    with lock:
        # A second non-blocking acquire from a fresh lock must time out fast.
        other = FileLock(path)
        try:
            other.acquire(timeout=0.1)
            raise AssertionError("second acquire should have timed out")
        except LockTimeout:
            pass
    # After release, re-acquire must succeed.
    lock.acquire(timeout=1.0)
    lock.release()
    print("file_lock self-check OK")
