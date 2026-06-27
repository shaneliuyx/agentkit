"""agentkit.tools.artifact — OpenAI tool schemas for artifact OCC tools.

``READ_ARTIFACT_TOOL`` and ``PATCH_ARTIFACT_TOOL`` expose concurrent-safe
artifact reads and targeted find/replace patches to the model. The backing
implementation is ``agentkit.artifacts.occ``.

Usage in a ToolAugmentedClient:

    from agentkit.tools.artifact import ARTIFACT_TOOL_SCHEMAS
    merged = caller_tools + ARTIFACT_TOOL_SCHEMAS
"""

from __future__ import annotations

from typing import Any

READ_ARTIFACT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_artifact",
        "description": (
            "Read the current artifact.md and return its full text content and a "
            "short hash. Always call this before patch_artifact so you hold the "
            "expected_hash required for OCC conflict detection. Blocks while "
            "another patch is being written."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

PATCH_ARTIFACT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "patch_artifact",
        "description": (
            "Atomically apply a find/replace patch to artifact.md using "
            "Optimistic Concurrency Control. If another worker modified the file "
            "since your read_artifact call, returns hash_mismatch with the "
            "current content and hash so you can re-analyze and retry. Use for "
            "inline URL insertions and popularity data additions to existing text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "find": {
                    "type": "string",
                    "description": "Exact verbatim text to find in the artifact.",
                },
                "replace": {
                    "type": "string",
                    "description": "Replacement text (must differ from find).",
                },
                "expected_hash": {
                    "type": "string",
                    "description": "Hash value returned by the most recent read_artifact call.",
                },
            },
            "required": ["find", "replace", "expected_hash"],
        },
    },
}

#: Convenience list — add to a tool client's schema list in one line.
ARTIFACT_TOOL_SCHEMAS: list[dict[str, Any]] = [READ_ARTIFACT_TOOL, PATCH_ARTIFACT_TOOL]
