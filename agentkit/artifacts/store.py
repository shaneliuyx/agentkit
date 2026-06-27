"""agentkit.artifacts.store — deliverable path resolution.

resolve_deliverable: three-source priority chain from DESIGN §2.1:
  1. Explicit path from loop_config (user override)
  2. Latest prior run with non-empty artifact.md (hill-climb history)
  3. Auto-create: workspace_root/{session_id}/artifact.md

latest_with_content: wraps TaskRunStore to find most recent run where the
artifact actually has content. Prefers *latest* over *best-score* because LLM
self-eval scores are noisy and the latest run has accumulated the most work.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def latest_with_content(store: Any, task_hash_str: str, ws_root: Path) -> Any | None:
    """Return most recent TaskRun whose artifact.md exists and is non-empty.

    ``store``         — TaskRunStore instance
    ``task_hash_str`` — sha256[:12] of the requirement
    ``ws_root``       — workspace root (workspaces/{session_id}/artifact.md)
    """
    try:
        runs = store.all_runs(task_hash_str)
    except Exception:  # noqa: BLE001
        return None

    for run in reversed(runs):  # newest first
        art = ws_root / run.session_id / "artifact.md"
        if art.exists() and art.stat().st_size > 0:
            return run
    return None


def resolve_deliverable(
    session: Any,
    workspace_root: Path,
    store: Any,
    task_hash_str: str,
) -> Path:
    """Resolve the artifact path using the three-source priority chain.

    Priority:
      1. loop_config.deliverable_path (explicit user override)
      2. Latest prior run with content (hill-climb seeding)
      3. Auto-create: workspace_root/{session_id}/artifact.md

    When priority 2 applies, the prior artifact is copied into the current
    session workspace so subsequent writes stay session-local.
    """
    session_id = session.session_id
    cfg = getattr(session, "loop_config", None)

    # Priority 1: explicit path from Loop Config panel
    if cfg is not None and getattr(cfg, "deliverable_path", None):
        return Path(cfg.deliverable_path)

    auto_improve = True if cfg is None else getattr(cfg, "auto_improve", True)

    # Priority 2: latest prior run with content
    if auto_improve and store is not None:
        prior = latest_with_content(store, task_hash_str, workspace_root)
        if prior:
            prior_art = workspace_root / prior.session_id / "artifact.md"
            dest = workspace_root / session_id / "artifact.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(prior_art, dest)
            return dest

    # Priority 3: auto-create in current session workspace
    dest = workspace_root / session_id / "artifact.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest
