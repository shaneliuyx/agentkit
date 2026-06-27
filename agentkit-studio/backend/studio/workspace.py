"""studio.workspace — a per-session file jail (realpath containment).

File tools (read_file / write_file) are confined to a per-session workspace
directory, mirroring ``SubprocessSandbox``'s cwd-jail intent: a tool can never
read or write outside its workspace. The containment check is realpath-based —
the resolved target must live under the resolved workspace root, so ``..`` and
absolute-path escapes are rejected (returned as an error, never raised, never a
raw ``open()`` outside the jail).

The root is configurable (``STUDIO_WORKSPACE_ROOT`` env, default
``tmp/studio-workspaces`` under the cwd) so tests point it at a tmp dir.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Output cap for reads — reuse the sandbox's MAX_OUTPUT_BYTES (64 KiB).
from agentkit.sandbox.core import MAX_OUTPUT_BYTES

#: The ONE containment check, shared with agentkit.tools.fs and the code scanner.
from agentkit.sandbox import is_within

#: Default workspace root (overridable via env or the constructor).
_DEFAULT_ROOT = "tmp/studio-workspaces"


def workspace_root() -> Path:
    """The configured workspace root (env override → default under cwd)."""
    return Path(os.getenv("STUDIO_WORKSPACE_ROOT", _DEFAULT_ROOT))


def resolve_deliverable(session: object, store: object, task_hash_str: str) -> Path:
    """Resolve the artifact.md path for *session* using the three-source priority
    chain from DESIGN §2.1 (explicit path → hill-climb seed → auto-create).

    M7 integration: thin wrapper over ``agentkit.artifacts.store.resolve_deliverable``
    that supplies the studio workspace root automatically so callers don't need to
    import the store function and the root helper separately.
    """
    from agentkit.artifacts.store import resolve_deliverable as _resolve

    return _resolve(session, workspace_root(), store, task_hash_str)


class WorkspaceError(Exception):
    """A containment violation or file error — surfaced as a tool error result."""


class Workspace:
    """A per-session jailed directory for file tools.

    All paths handed to ``read``/``write`` are resolved and required to live
    under the workspace root; an escape raises ``WorkspaceError`` (the tool layer
    turns that into an error result rather than propagating it).
    """

    def __init__(self, session_id: str, *, root: Path | None = None) -> None:
        base = (root or workspace_root()).expanduser()
        self.root = (base / session_id).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- containment -------------------------------------------------------

    def _resolve_inside(self, rel_path: str) -> Path:
        """Resolve ``rel_path`` against the workspace; require it stays inside.

        Rejects absolute paths and ``..`` escapes via a realpath-prefix check.
        Uses ``os.path.realpath`` so symlinks are followed before the check —
        a symlink pointing out of the jail is also rejected.
        """
        if not rel_path or not str(rel_path).strip():
            raise WorkspaceError("empty path")
        # Delegate containment to the shared sandbox jail (ONE implementation).
        # is_within resolves a relative path against root and realpath-checks both
        # sides, so ``..`` and symlink escapes are caught the same way everywhere.
        if not is_within(rel_path, self.root):
            raise WorkspaceError(
                f"path escapes workspace: {rel_path!r} resolves outside the sandbox"
            )
        candidate = Path(rel_path)
        target = candidate if candidate.is_absolute() else self.root / candidate
        return target.resolve()

    # -- operations --------------------------------------------------------

    def read(self, rel_path: str) -> tuple[str, int]:
        """Read a file inside the workspace → ``(text, bytes_read)``.

        Output is capped at ``MAX_OUTPUT_BYTES``. Raises ``WorkspaceError`` on an
        escape or a missing/unreadable file.
        """
        target = self._resolve_inside(rel_path)
        if not target.is_file():
            raise WorkspaceError(f"not a file: {rel_path}")
        raw = target.read_bytes()[:MAX_OUTPUT_BYTES]
        return raw.decode("utf-8", "replace"), len(raw)

    def write(self, rel_path: str, content: str) -> tuple[int, str]:
        """Write a file inside the workspace → ``(bytes_written, rel_path)``.

        Creates parent directories (inside the jail). Raises ``WorkspaceError``
        on an escape. The containment check runs BEFORE any directory is created
        or any byte is written, so an escaping write touches nothing on disk.
        """
        target = self._resolve_inside(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        target.write_bytes(data)
        # Report the path relative to the workspace root for a clean summary.
        try:
            shown = str(target.relative_to(self.root))
        except ValueError:
            shown = str(target)
        return len(data), shown
