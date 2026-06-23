"""agentkit.tools — reusable, jailed tools for the agent loop.

The canonical home for tool implementations a caller can drop into
``run_agent(tools=...)`` (or a ``ToolAugmentedClient``). Each tool keeps its
root/containment injected — no global state — and reuses the sandbox jail
primitive (``agentkit.sandbox.is_within``) so the filesystem boundary has ONE
source of truth.

``fs`` is the first member: ``read_file`` / ``write_file`` confined to a root.
"""

from agentkit.tools.fs import (
    FS_TOOL_SCHEMAS,
    FileToolError,
    make_fs_tools,
    read_file,
    write_file,
)

__all__ = [
    "read_file",
    "write_file",
    "FileToolError",
    "FS_TOOL_SCHEMAS",
    "make_fs_tools",
]
