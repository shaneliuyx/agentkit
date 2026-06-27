"""agentkit.artifacts.patcher — patch-based document modification.

Workers never write to disk. They emit PATCHES suggestion blocks; the Reducer
collects all workers' suggestions, resolves conflicts, and performs ONE atomic
write via ``write_artifact``.

Atomic write: write .tmp → POSIX rename. Crash before rename = original intact;
.tmp orphan cleaned on startup by ``cleanup_orphaned_tmp``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class DocPatch:
    """A single suggested change to a document."""

    op: Literal["replace", "insert_after", "insert_before", "append", "prepend", "delete"]
    anchor: str | None
    content: str
    source: str = ""


@dataclass
class ConflictNote:
    """Record of a patch that could not be cleanly applied."""

    patch: DocPatch
    reason: str  # "anchor_destroyed" | "duplicate" | "ambiguous_anchor"


@dataclass
class ReduceResult:
    """Output of ``reduce_patches``."""

    text: str
    conflicts: list[ConflictNote] = field(default_factory=list)


def _apply_one(text: str, patch: DocPatch) -> str:
    """Apply a single non-conflicting patch to ``text``."""
    op, anchor, content = patch.op, patch.anchor, patch.content

    if op == "append":
        sep = "\n" if text and not text.endswith("\n") else ""
        return text + sep + content

    if op == "prepend":
        sep = "\n" if content and not content.endswith("\n") else ""
        return content + sep + text

    if anchor is None:
        return text

    if op == "replace":
        return text.replace(anchor, content, 1)

    if op == "delete":
        return text.replace(anchor, "", 1)

    if op == "insert_after":
        idx = text.find(anchor)
        if idx == -1:
            return text
        insert_at = idx + len(anchor)
        return text[:insert_at] + content + text[insert_at:]

    if op == "insert_before":
        idx = text.find(anchor)
        if idx == -1:
            return text
        return text[:idx] + content + text[idx:]

    return text


def _conflict_marker(patch: DocPatch) -> str:
    src = f"({patch.source})" if patch.source else ""
    return f"\n<!-- conflict{src}: anchor not found -->\n{patch.content}"


def reduce_patches(
    current_text: str,
    patch_groups: list[list[DocPatch]],
    llm_merge_fn=None,
    llm_refine_fn=None,
) -> ReduceResult:
    """Two-phase reduce (DESIGN §2.2): structural merge, then document refinement.

    ``patch_groups`` — list-of-lists, one per worker in task-assignment order
    (earlier assignments win on destructive conflicts).

    Phase 1 — structural merge. Conflict rules:
      - Anchor missing (destroyed by prior patch): append with conflict marker,
        or call ``llm_merge_fn(working_text, patch)`` if provided.
      - Content already present (duplicate insert): skip.
      - All other patches: applied in order.

    Phase 2 — document refinement. When ``llm_refine_fn`` is provided, the merged
    text is passed through it for a full-document editorial polish (coherence,
    flow, gap-filling, conflict-marker cleanup). Refinement is best-effort: any
    failure or empty result leaves the structurally-merged text untouched, so a
    flaky LLM never corrupts a clean merge.
    """
    working = current_text
    conflicts: list[ConflictNote] = []

    # Phase 1 — structural merge
    for patches in patch_groups:
        for p in patches:
            if p.op in ("append", "prepend") or p.anchor is None:
                working = _apply_one(working, p)
                continue

            if p.anchor not in working:
                if llm_merge_fn is not None:
                    try:
                        working = llm_merge_fn(working, p)
                    except Exception:  # noqa: BLE001
                        conflicts.append(ConflictNote(p, "anchor_destroyed"))
                        working += _conflict_marker(p)
                else:
                    conflicts.append(ConflictNote(p, "anchor_destroyed"))
                    working += _conflict_marker(p)
                continue

            if p.op == "insert_after" and p.content and p.content in working:
                conflicts.append(ConflictNote(p, "duplicate"))
                continue

            working = _apply_one(working, p)

    # Phase 2 — document refinement (optional LLM editorial pass)
    if llm_refine_fn is not None:
        try:
            refined = llm_refine_fn(working)
            if refined and refined.strip():
                working = refined
        except Exception:  # noqa: BLE001 — refine is best-effort polish
            pass

    return ReduceResult(text=working, conflicts=conflicts)


def write_artifact(path: Path, text: str) -> None:
    """Atomic write via tmp + POSIX rename. Only the Reducer calls this."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(path)


def cleanup_orphaned_tmp(workspace_root: Path) -> None:
    """Remove any .tmp files left by a crash mid-rename. Call at server startup."""
    for tmp in workspace_root.rglob("*.tmp"):
        tmp.unlink(missing_ok=True)
