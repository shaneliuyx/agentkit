"""Tests for agentkit.artifacts.store — deliverable path resolution (DESIGN §2.1).

Three-source priority:
  1. loop_config.deliverable_path (explicit override)
  2. Latest prior run with non-empty artifact.md (hill-climb seed)
  3. Auto-create: workspace_root/{session_id}/artifact.md

Uses lightweight fakes (no SQLite, no LLM, no network).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from agentkit.artifacts.store import latest_with_content, resolve_deliverable


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _FakeRun:
    session_id: str
    created_at: float = 0.0


@dataclass
class _FakeLoopConfig:
    deliverable_path: str | None = None
    auto_improve: bool = True


@dataclass
class _FakeSession:
    session_id: str
    loop_config: _FakeLoopConfig | None = None


class _FakeStore:
    """In-memory TaskRunStore that returns a fixed list of runs."""

    def __init__(self, runs: list[_FakeRun]) -> None:
        self._runs = runs

    def all_runs(self, task_hash_str: str) -> list[_FakeRun]:
        return list(self._runs)


# ---------------------------------------------------------------------------
# latest_with_content
# ---------------------------------------------------------------------------

def test_latest_with_content_returns_most_recent_nonempty(tmp_path: Path) -> None:
    run_old = _FakeRun("sess-old", created_at=1.0)
    run_new = _FakeRun("sess-new", created_at=2.0)

    (tmp_path / "sess-new").mkdir()
    (tmp_path / "sess-new" / "artifact.md").write_text("# Report")

    store = _FakeStore([run_old, run_new])
    result = latest_with_content(store, "hash123", tmp_path)
    assert result is run_new


def test_latest_with_content_skips_empty_artifact(tmp_path: Path) -> None:
    run = _FakeRun("sess-empty", created_at=1.0)
    (tmp_path / "sess-empty").mkdir()
    (tmp_path / "sess-empty" / "artifact.md").write_text("")

    store = _FakeStore([run])
    assert latest_with_content(store, "hash123", tmp_path) is None


def test_latest_with_content_skips_missing_artifact(tmp_path: Path) -> None:
    run = _FakeRun("sess-nofile", created_at=1.0)
    (tmp_path / "sess-nofile").mkdir()

    store = _FakeStore([run])
    assert latest_with_content(store, "hash123", tmp_path) is None


def test_latest_with_content_returns_none_on_empty_store(tmp_path: Path) -> None:
    assert latest_with_content(_FakeStore([]), "hash123", tmp_path) is None


def test_latest_with_content_selects_latest_by_list_order(tmp_path: Path) -> None:
    """all_runs returns ascending; latest_with_content walks reversed → last wins."""
    run_a = _FakeRun("sess-a", created_at=1.0)
    run_b = _FakeRun("sess-b", created_at=3.0)
    run_c = _FakeRun("sess-c", created_at=2.0)

    for sid in ("sess-a", "sess-b", "sess-c"):
        (tmp_path / sid).mkdir()
        (tmp_path / sid / "artifact.md").write_text(f"# {sid}")

    store = _FakeStore([run_a, run_c, run_b])
    result = latest_with_content(store, "hash123", tmp_path)
    assert result is run_b


# ---------------------------------------------------------------------------
# resolve_deliverable — priority 1: explicit path
# ---------------------------------------------------------------------------

def test_resolve_deliverable_explicit_path_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "custom" / "artifact.md"
    session = _FakeSession(
        "sess-1",
        loop_config=_FakeLoopConfig(deliverable_path=str(explicit)),
    )
    result = resolve_deliverable(session, tmp_path, store=None, task_hash_str="h")
    assert result == explicit


# ---------------------------------------------------------------------------
# resolve_deliverable — priority 2: hill-climb seed
# ---------------------------------------------------------------------------

def test_resolve_deliverable_seeds_from_prior_run(tmp_path: Path) -> None:
    prior_run = _FakeRun("sess-prior")
    (tmp_path / "sess-prior").mkdir()
    (tmp_path / "sess-prior" / "artifact.md").write_text("# Prior report")

    session = _FakeSession("sess-current")
    store = _FakeStore([prior_run])

    result = resolve_deliverable(session, tmp_path, store=store, task_hash_str="h")

    assert result == tmp_path / "sess-current" / "artifact.md"
    assert result.read_text() == "# Prior report"


def test_resolve_deliverable_no_prior_falls_through_to_auto(tmp_path: Path) -> None:
    session = _FakeSession("sess-fresh")
    store = _FakeStore([])

    result = resolve_deliverable(session, tmp_path, store=store, task_hash_str="h")

    assert result == tmp_path / "sess-fresh" / "artifact.md"
    assert result.parent.is_dir()


# ---------------------------------------------------------------------------
# resolve_deliverable — priority 3: auto-create / overrides
# ---------------------------------------------------------------------------

def test_resolve_deliverable_auto_improve_off_skips_seed(tmp_path: Path) -> None:
    prior_run = _FakeRun("sess-prior")
    (tmp_path / "sess-prior").mkdir()
    (tmp_path / "sess-prior" / "artifact.md").write_text("# Old")

    session = _FakeSession(
        "sess-new",
        loop_config=_FakeLoopConfig(auto_improve=False),
    )
    store = _FakeStore([prior_run])

    result = resolve_deliverable(session, tmp_path, store=store, task_hash_str="h")

    assert result == tmp_path / "sess-new" / "artifact.md"
    assert not result.exists() or result.read_text() != "# Old"


def test_resolve_deliverable_no_loop_config_defaults_auto_improve(tmp_path: Path) -> None:
    prior_run = _FakeRun("sess-old")
    (tmp_path / "sess-old").mkdir()
    (tmp_path / "sess-old" / "artifact.md").write_text("# Seeded")

    session = _FakeSession("sess-x", loop_config=None)
    store = _FakeStore([prior_run])

    result = resolve_deliverable(session, tmp_path, store=store, task_hash_str="h")

    assert result.read_text() == "# Seeded"
