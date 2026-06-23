"""agentkit.tools.fs — root-jailed file read/write tools for the agent loop.

The canonical, reusable file-tools component. A caller injects a ``root``; every
path is resolved relative to it and ``realpath``-checked against the shared
sandbox jail (``agentkit.sandbox.is_within``) before any I/O. ``..`` segments,
absolute paths, and symlink escapes are refused — a file outside ``root`` is
never read and never created.

Two surfaces, by design:
  - ``read_file`` / ``write_file`` — the Python API. They raise ``FileToolError``
    on escape/missing/oversize, so a programmatic caller handles a typed failure.
  - ``make_fs_tools(root)`` — returns a ``{name: callable(args) -> dict}``
    registry that CATCHES ``FileToolError`` and returns a structured
    ``{"error": ...}`` dict, wire-compatible with ``run_agent(tools=...)`` and
    its ``DictToolRegistry`` (which feeds the result dict back to the model).

``root`` is always injected — there is no global state. Pure stdlib; no vendor
imports (agentkit rule).
"""

from __future__ import annotations

from pathlib import Path

from agentkit.sandbox import is_within

# Default ceiling for a single read, so a tool result cannot flood the caller's
# context. Mirrors the spirit of the sandbox output cap (64 KiB).
DEFAULT_MAX_BYTES: int = 64 * 1024


class FileToolError(Exception):
    """A file tool refused or failed an operation.

    Raised for jail escapes, missing files, and oversize reads. The tool-handler
    surface (``make_fs_tools``) converts this into a structured ``{"error": ...}``
    result rather than letting it propagate as a crash.
    """


def _resolve_within(path: str | Path, root: str | Path) -> Path:
    """Resolve ``path`` under ``root`` and assert containment, or raise.

    The single choke point both tools go through. Uses the shared sandbox jail
    so the containment rule has ONE implementation across the codebase.
    """
    root_resolved = Path(root).resolve()
    if not is_within(path, root_resolved):
        raise FileToolError(
            f"path {str(path)!r} escapes the root jail {root_resolved}"
        )
    candidate = Path(path)
    target = candidate if candidate.is_absolute() else root_resolved / candidate
    return target.resolve()


def read_file(
    path: str | Path,
    *,
    root: str | Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> str:
    """Read a UTF-8 text file resolved relative to ``root``, capped at ``max_bytes``.

    Args:
        path:      File path, relative to ``root`` (absolute paths are jailed too).
        root:      The containment root; reads outside it are refused.
        max_bytes: Maximum bytes returned; a larger file raises ``FileToolError``.

    Returns:
        The file's text content.

    Raises:
        FileToolError: on jail escape, a missing/non-file path, or oversize read.
    """
    resolved = _resolve_within(path, root)
    if not resolved.is_file():
        raise FileToolError(f"not a readable file: {str(path)!r}")
    raw = resolved.read_bytes()
    if len(raw) > max_bytes:
        raise FileToolError(
            f"file {str(path)!r} is {len(raw)} bytes, exceeds max_bytes={max_bytes}"
        )
    return raw.decode("utf-8", "replace")


def write_file(path: str | Path, content: str, *, root: str | Path) -> int:
    """Write ``content`` to ``path`` under ``root``; create parent dirs inside root.

    Side-effecting. The path is jail-checked first; the parent directory is then
    created (only ever inside ``root``, since the resolved target is contained).

    Args:
        path:    File path, relative to ``root`` (absolute paths are jailed too).
        content: UTF-8 text to write.
        root:    The containment root; writes outside it are refused.

    Returns:
        Number of bytes written.

    Raises:
        FileToolError: on jail escape.
    """
    resolved = _resolve_within(path, root)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    resolved.write_bytes(data)
    return len(data)


# ---------------------------------------------------------------------------
# Tool wiring — OpenAI-format schemas + a root-injecting handler factory.
# ---------------------------------------------------------------------------

FS_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file, confined to the agent's root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the root.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a UTF-8 text file, confined to the agent's root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the root.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]


def make_fs_tools(
    root: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, object]:
    """Build a ``{name: callable(args) -> dict}`` fs-tool registry jailed to ``root``.

    Drop the result straight into ``run_agent(tools=...)``: the handlers match
    the ``ToolFn`` contract (``dict`` in, JSON-serialisable ``dict`` out) and
    convert a ``FileToolError`` into a structured ``{"error": ...}`` result so
    the agent loop feeds it back to the model as data, never a crash.

    ``root`` is captured in the closure — no global state, and each call yields
    an independent registry bound to its own root.
    """

    def _read(args: dict) -> dict:
        try:
            text = read_file(args.get("path", ""), root=root, max_bytes=max_bytes)
            return {"content": text, "bytes": len(text.encode("utf-8"))}
        except FileToolError as exc:
            return {"error": str(exc)}

    def _write(args: dict) -> dict:
        try:
            written = write_file(
                args.get("path", ""), args.get("content", ""), root=root
            )
            return {"bytes_written": written}
        except FileToolError as exc:
            return {"error": str(exc)}

    return {"read_file": _read, "write_file": _write}


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)

        # happy path: write then read within root.
        n = write_file("notes/hello.txt", "hi there", root=root)
        assert n == 8, n
        assert read_file("notes/hello.txt", root=root) == "hi there"
        assert (root / "notes" / "hello.txt").is_file()

        # escape rejection — relative .. and absolute, for BOTH read and write.
        for bad in ("../escape.txt", "/etc/passwd"):
            try:
                write_file(bad, "x", root=root)
                raise AssertionError(f"write escape not rejected: {bad}")
            except FileToolError:
                pass
            try:
                read_file(bad, root=root)
                raise AssertionError(f"read escape not rejected: {bad}")
            except FileToolError:
                pass
        # a file outside root was never created by the rejected writes.
        assert not (root.parent / "escape.txt").exists()

        # oversize read is capped.
        write_file("big.txt", "A" * 100, root=root)
        try:
            read_file("big.txt", root=root, max_bytes=10)
            raise AssertionError("oversize read not capped")
        except FileToolError:
            pass

        # tool-handler surface: errors come back as data, not exceptions.
        tools = make_fs_tools(root)
        assert tools["write_file"]({"path": "a.txt", "content": "z"}) == {
            "bytes_written": 1
        }
        assert tools["read_file"]({"path": "a.txt"})["content"] == "z"
        assert "error" in tools["read_file"]({"path": "../nope.txt"})
        assert "error" in tools["write_file"]({"path": "/etc/x", "content": "z"})

    print("tools.fs self-check OK")
