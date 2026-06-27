"""agentkit.artifacts — deliverable lifecycle: patch, reduce, write, resolve."""

from agentkit.artifacts.occ import (
    PatchResult,
    ReadResult,
    artifact_hash,
    get_lock,
    patch_artifact,
    read_artifact,
)
from agentkit.artifacts.patcher import (
    ConflictNote,
    DocPatch,
    ReduceResult,
    cleanup_orphaned_tmp,
    reduce_patches,
    write_artifact,
)

__all__ = [
    "DocPatch",
    "ConflictNote",
    "ReduceResult",
    "reduce_patches",
    "write_artifact",
    "cleanup_orphaned_tmp",
    # OCC concurrent-write primitives
    "ReadResult",
    "PatchResult",
    "artifact_hash",
    "get_lock",
    "read_artifact",
    "patch_artifact",
]
