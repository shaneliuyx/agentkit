"""agentkit.sandbox.core — containment for untrusted code/commands.

The ``Sandbox`` Protocol is the seam ``evolve/codegen`` and the gates execute
proposals through. The default ``SubprocessSandbox`` is the deterministic,
local containment tier; ``DockerSandbox`` is a named seam for hard isolation
(not implemented here — it raises a clear ``NotImplementedError``).

Security is THE design point (REPLAN §5). ``SubprocessSandbox`` enforces:
  - **argv-not-shell** — commands run via ``subprocess`` with a list argv and
    ``shell=False``; shell metacharacters (``;``, ``&&``, ``|``) are inert, so
    command injection is impossible.
  - **cwd jail** — the working directory must exist; a python *code string* is
    statically scanned for path literals that escape ``cwd`` and rejected before
    it ever runs.
  - **wall-clock timeout** — a runaway is killed (process-group kill) at the
    deadline; the result reports a non-zero exit and a ``timeout`` marker.
  - **output cap** — stdout/stderr are truncated at ``MAX_OUTPUT_BYTES`` so a
    flood cannot blow up the caller's context.

Pure stdlib. No vendor imports. ``net_guard`` (a sibling) is the deterministic
egress-allowlist tools/sandboxed code consult before reaching the network.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# Maximum bytes captured from each of stdout/stderr. A truncation marker is
# appended when output exceeds this, so the caller's context can never be
# flooded by a misbehaving proposal.
MAX_OUTPUT_BYTES: int = 64 * 1024

# Default wall-clock ceiling, in seconds, when a caller passes no timeout.
DEFAULT_TIMEOUT: float = 30.0

_TRUNC_MARKER = "\n...[truncated by sandbox output cap]..."

# A code string may name a path; these patterns capture string literals passed
# to filesystem entry points so an escape attempt can be rejected statically.
_PATH_OPEN_RE = re.compile(
    r"""(?:open|Path)\s*\(\s*(['"])(?P<path>.*?)\1""", re.DOTALL
)


@dataclass(frozen=True)
class ExecResult:
    """The immutable outcome of a sandboxed run.

    Attributes:
        stdout:    captured standard output (capped at ``MAX_OUTPUT_BYTES``).
        stderr:    captured standard error (capped at ``MAX_OUTPUT_BYTES``).
        exit_code: process exit code; non-zero on error, crash, or timeout.
        duration:  wall-clock seconds the run took.
    """

    stdout: str
    stderr: str
    exit_code: int
    duration: float


@runtime_checkable
class Sandbox(Protocol):
    """Anything that can run a command or code string under containment.

    ``code_or_cmd`` is either a python source string (run via
    ``[sys.executable, "-c", code]``) or an argv list (run as-is). ``timeout``
    is a wall-clock ceiling in seconds; ``cwd`` is the jailed working directory.
    """

    def run(
        self,
        code_or_cmd: str | list[str],
        *,
        timeout: float,
        cwd: str | Path,
    ) -> ExecResult:
        ...


def _cap(raw: bytes) -> str:
    """Decode and truncate captured output at ``MAX_OUTPUT_BYTES``."""
    if len(raw) > MAX_OUTPUT_BYTES:
        return raw[:MAX_OUTPUT_BYTES].decode("utf-8", "replace") + _TRUNC_MARKER
    return raw.decode("utf-8", "replace")


def is_within(path: str | Path, root: str | Path) -> bool:
    """True iff ``path`` resolves to ``root`` itself or somewhere inside it.

    The single source of truth for the cwd/root containment policy. A relative
    ``path`` is resolved against ``root``; an absolute ``path`` is taken as-is.
    Both sides are ``realpath``-resolved first, so symlink and ``..`` escapes
    are caught (a symlink pointing outside ``root`` resolves outside and fails).
    Used by the sandbox code-string scanner AND by ``agentkit.tools.fs`` — keep
    the jail with ONE implementation.
    """
    root_resolved = Path(root).resolve()
    candidate = Path(path)
    target = candidate if candidate.is_absolute() else root_resolved / candidate
    try:
        resolved = target.resolve()
    except OSError:  # pragma: no cover - defensive
        return False
    return resolved == root_resolved or root_resolved in resolved.parents


def _assert_paths_within_cwd(code: str, cwd: Path) -> None:
    """Reject a code string that opens a path escaping ``cwd``.

    Static, deterministic, conservative: only string literals handed to
    ``open(...)`` / ``Path(...)`` are inspected. Each is checked against the
    shared ``is_within`` jail; anything outside ``cwd`` raises ``ValueError``
    before the code is executed.
    """
    cwd_resolved = cwd.resolve()
    for m in _PATH_OPEN_RE.finditer(code):
        raw = m.group("path")
        if not raw:
            continue
        if not is_within(raw, cwd_resolved):
            raise ValueError(
                f"sandbox: path {raw!r} escapes the cwd jail {cwd_resolved}"
            )


class SubprocessSandbox:
    """Local subprocess containment: argv-not-shell, cwd-jailed, timed, capped."""

    def __init__(self, max_output_bytes: int = MAX_OUTPUT_BYTES) -> None:
        self.max_output_bytes = max_output_bytes

    def run(
        self,
        code_or_cmd: str | list[str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        cwd: str | Path,
    ) -> ExecResult:
        cwd_path = Path(cwd)
        if not cwd_path.is_dir():
            raise ValueError(f"sandbox: cwd {cwd_path} does not exist")

        if isinstance(code_or_cmd, str):
            _assert_paths_within_cwd(code_or_cmd, cwd_path)
            argv = [sys.executable, "-c", code_or_cmd]
        else:
            argv = list(code_or_cmd)

        start = time.monotonic()
        # start_new_session puts the child in its own process group so a timeout
        # can kill the whole group, not just the immediate child.
        proc = subprocess.Popen(  # noqa: S603 - argv list, shell=False, jailed
            argv,
            cwd=str(cwd_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=timeout)
            duration = time.monotonic() - start
            return ExecResult(
                stdout=_cap(out),
                stderr=_cap(err),
                exit_code=proc.returncode,
                duration=duration,
            )
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            out, err = proc.communicate()
            duration = time.monotonic() - start
            err_text = _cap(err)
            return ExecResult(
                stdout=_cap(out),
                stderr=(err_text + f"\nsandbox: timeout after {timeout}s — killed").strip(),
                exit_code=-signal.SIGKILL,
                duration=duration,
            )


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill the child's whole process group; fall back to killing the child."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):  # pragma: no cover
        proc.kill()


class DockerSandbox:
    """A named seam for hard (container) isolation — not implemented here.

    The Protocol is satisfied so callers can swap it in by config; the body
    raises with a clear message until a real docker backend is wired in.
    """

    def __init__(self, image: str = "python:3.12-slim") -> None:
        self.image = image

    def run(
        self,
        code_or_cmd: str | list[str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        cwd: str | Path,
    ) -> ExecResult:
        raise NotImplementedError(
            "DockerSandbox is a seam, not an implementation. Install docker and "
            "wire a real backend, or use SubprocessSandbox for local containment."
        )


if __name__ == "__main__":
    import tempfile

    sb = SubprocessSandbox()
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d)

        # shared jail primitive: inside passes, escapes fail.
        assert is_within("a/b.txt", cwd)
        assert is_within(str(cwd / "x"), cwd)
        assert not is_within("../escape.txt", cwd)
        assert not is_within("/etc/passwd", cwd)

        # argv command runs and captures stdout.
        r = sb.run([sys.executable, "-c", "print('hi')"], timeout=10, cwd=cwd)
        assert r.exit_code == 0 and "hi" in r.stdout, r

        # code string runs.
        r = sb.run("print(6 * 7)", timeout=10, cwd=cwd)
        assert "42" in r.stdout, r

        # shell metacharacters are inert (no shell): canary survives.
        canary = cwd / "canary.txt"
        canary.write_text("alive")
        r = sb.run([sys.executable, "-c", "import sys; print(sys.argv[1])",
                    "x; rm -rf " + str(canary)], timeout=10, cwd=cwd)
        assert canary.exists(), "no shell ran; canary must survive"

        # writing outside cwd is blocked before execution.
        try:
            sb.run(f"open({str(cwd.parent / 'esc.txt')!r}, 'w')", timeout=10, cwd=cwd)
            raise AssertionError("escape should have been blocked")
        except ValueError:
            pass

        # timeout kills a runaway.
        r = sb.run([sys.executable, "-c", "import time; time.sleep(30)"],
                   timeout=1, cwd=cwd)
        assert r.exit_code != 0 and r.duration < 10, r

        # output is capped.
        r = sb.run(f"print('A' * ({MAX_OUTPUT_BYTES} * 4))", timeout=15, cwd=cwd)
        assert len(r.stdout.encode()) <= MAX_OUTPUT_BYTES + 256, len(r.stdout)

    # DockerSandbox is a seam.
    try:
        DockerSandbox().run("print(1)", timeout=5, cwd=".")
        raise AssertionError("DockerSandbox should raise NotImplementedError")
    except NotImplementedError:
        pass

    print("sandbox.core self-check OK")
