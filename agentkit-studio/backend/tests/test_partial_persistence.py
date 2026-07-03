"""Entry 166: persist partial run state when a run dies mid-flight.

When the LLM backend dies mid-run the exception unwinds to runner.run()'s single
top-level catch and NOTHING was recorded — all partial research was lost and the
next run cold-started. These tests cover the two halves of the fix:

  * TaskRunStore: a ``status`` column ('completed' | 'failed_partial'), migration on
    legacy DBs, and the rule that failed rows feed NO improvement signal (weaknesses,
    repeat-failures, similar-run seeds, config budget) yet ARE eligible seed content.
  * Runner._persist_partial_run: saves a partial when the artifact has real content,
    skips a skeleton-only artifact, never raises, and records under the SAME base-
    identity task_hash normal recording uses (no lineage fork).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from studio.runner import Runner, _artifact_has_real_content
from studio.session import SessionRegistry
from studio.task_runs import (
    TaskRun,
    TaskRunStore,
    base_identity,
    task_hash,
)


# --------------------------------------------------------------------------- #
# Store: status column + migration                                            #
# --------------------------------------------------------------------------- #


def _failed(req: str, sid: str, *, text: str = "partial body", weaknesses=None) -> TaskRun:
    return TaskRun(
        task_hash=task_hash(req), session_id=sid, version=0, score=0.0,
        weaknesses=weaknesses or [], artifact_path="", requirement=req,
        result_text=text, config={"failure": "Connection error."},
        status="failed_partial",
    )


def test_status_column_roundtrips(tmp_path: Path) -> None:
    """A failed_partial row's status survives record → latest/all_runs read-back."""
    store = TaskRunStore(db_path=tmp_path / "t.db")
    req = "compare redis and postgres"
    store.record_versioned(_failed(req, "s1"))
    th = task_hash(req)
    assert store.latest(th).status == "failed_partial"
    assert [r.status for r in store.all_runs(th)] == ["failed_partial"]


def test_status_migration_adds_column_to_legacy_db(tmp_path: Path) -> None:
    """A pre-existing DB without the status column is migrated in place, its legacy
    row reads back as 'completed', and new records still work."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    # The original pre-migration schema (no result_text/config/status/... columns).
    conn.execute(
        """CREATE TABLE task_runs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               task_hash TEXT NOT NULL,
               session_id TEXT NOT NULL,
               version INTEGER NOT NULL,
               score REAL NOT NULL,
               weaknesses_json TEXT NOT NULL DEFAULT '[]',
               artifact_path TEXT NOT NULL DEFAULT '',
               requirement TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.execute(
        "INSERT INTO task_runs (task_hash, session_id, version, score, requirement) "
        "VALUES (?, ?, ?, ?, ?)",
        (task_hash("legacy task"), "s_old", 1, 0.5, "legacy task"),
    )
    conn.commit()
    conn.close()

    store = TaskRunStore(db_path=db)  # opening runs the PRAGMA-cols migration
    cols = {r[1] for r in store._conn.execute("PRAGMA table_info(task_runs)").fetchall()}
    assert "status" in cols
    legacy = store.latest(task_hash("legacy task"))
    assert legacy is not None and legacy.status == "completed"  # backfilled default
    # New records still round-trip after the migration.
    store.record_versioned(_failed("another task", "s_new"))
    assert store.latest(task_hash("another task")).status == "failed_partial"


# --------------------------------------------------------------------------- #
# Store: failed rows carry no improvement signal                              #
# --------------------------------------------------------------------------- #


def test_completed_runs_excludes_failed(tmp_path: Path) -> None:
    store = TaskRunStore(db_path=tmp_path / "t.db")
    req = "study agent loops"
    th = task_hash(req)
    store.record_versioned(TaskRun(
        task_hash=th, session_id="s1", version=0, score=0.7, weaknesses=[],
        artifact_path="", requirement=req, result_text="done",
    ))
    store.record_versioned(_failed(req, "s2"))
    assert len(store.all_runs(th)) == 2
    completed = store.completed_runs(th)
    assert [r.session_id for r in completed] == ["s1"]


def test_accumulated_weaknesses_ignores_failed_rows(tmp_path: Path) -> None:
    """Belt-and-braces: even a failed row carrying a non-empty weakness must NOT
    surface in the exact-task weakness feed."""
    store = TaskRunStore(db_path=tmp_path / "t.db")
    req = "study agent loops"
    th = task_hash(req)
    store.record_versioned(TaskRun(
        task_hash=th, session_id="s1", version=0, score=0.6,
        weaknesses=["[document] real gap"], artifact_path="",
        requirement=req, result_text="done",
    ))
    store.record_versioned(_failed(req, "s2", weaknesses=["[document] phantom from a dead run"]))
    merged = store.accumulated_weaknesses(req, th)
    assert "[document] real gap" in merged
    assert all("phantom" not in w for w in merged)


def test_repeat_failures_ignores_failed_rows(tmp_path: Path) -> None:
    """A weakness present only in failed rows never reaches the repeat-failure limit."""
    store = TaskRunStore(db_path=tmp_path / "t.db")
    req = "study agent loops"
    th = task_hash(req)
    for i in range(5):  # 5 failed rows all carrying the same weakness
        store.record_versioned(_failed(req, f"s{i}", weaknesses=["[document] infra 503"]))
    assert store.repeat_failures(th) == set()


def test_similar_runs_excludes_failed_rows(tmp_path: Path) -> None:
    class _Emb:
        _vocab = ["agent", "loop", "cook", "recipe"]

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0 if w in t.lower() else 0.0 for w in self._vocab] for t in texts]

    store = TaskRunStore(db_path=tmp_path / "t.db", embedder=_Emb())
    # A failed row for one task; a completed row for a different-but-similar task.
    store.record_versioned(_failed("build an agent loop", "s_fail"))
    store.record_versioned(TaskRun(
        task_hash=task_hash("design an agent loop system"), session_id="s_ok",
        version=0, score=0.6, weaknesses=[], artifact_path="",
        requirement="design an agent loop system", result_text="ok",
    ))
    hits = store.similar_runs("agent loop orchestration", _Emb(), k=5)
    sessions = [run.session_id for run, _sim in hits]
    assert "s_fail" not in sessions
    assert "s_ok" in sessions


def test_latest_config_ignores_failed_row(tmp_path: Path, monkeypatch) -> None:
    """A failed_partial's config={"failure": ...} must NOT shadow a prior real
    hill-climb budget (it is a failure marker, not an epoch budget)."""
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    store = TaskRunStore(db_path=tmp_path / "t.db")
    req = "compare redis and postgres"
    th = task_hash(req)
    cfg = {"auto_improve": True, "max_epochs": 4}
    store.record_versioned(TaskRun(
        task_hash=th, session_id="s1", version=0, score=0.5, weaknesses=[],
        artifact_path="", requirement=req, result_text="x", config=cfg,
    ))
    store.record_versioned(_failed(req, "s2"))  # newer, config={"failure": ...}
    assert store.latest_config(th) == cfg  # failed marker did not win


def test_latest_with_content_returns_failed_partial(tmp_path: Path) -> None:
    """The carry-forward win: when the latest run with content is a failed_partial,
    latest_with_content returns it (NO status filter) so its partial artifact seeds
    the next run instead of a cold start."""
    ws_root = tmp_path / "ws"
    store = TaskRunStore(db_path=tmp_path / "t.db")
    req = "study agent loops"
    th = task_hash(req)
    store.record_versioned(_failed(req, "s_partial", text="# Partial Report\nreal partial body"))
    seed = store.latest_with_content(th, ws_root=ws_root)
    assert seed is not None
    assert seed.status == "failed_partial"
    assert seed.result_text == "# Partial Report\nreal partial body"


# --------------------------------------------------------------------------- #
# Runner._persist_partial_run                                                 #
# --------------------------------------------------------------------------- #

_SKELETON_HYPHEN = "# Report\n\n## Findings\n_(pending - needs sourced content)_\n\n## Sources\n_(pending - needs sourced content)_\n"
_SKELETON_EMDASH = "# Report\n\n## Findings\n_(pending — needs sourced content)_\n\n## Sources\n_(pending — needs sourced content)_\n"
_REAL_ARTIFACT = (
    "# Report\n\n## Findings\n"
    "The research surfaced three concrete patterns worth carrying forward into the "
    "next iteration, each grounded in a cited source and summarized in plain prose.\n\n"
    "## Sources\n_(pending - needs sourced content)_\n"
)


def _make_session():
    reg = SessionRegistry()
    return reg.create(
        llm_spec={"profile": "qwen"}, embed_spec={},
        llm_info={"label": "qwen", "model": "Qwen-test"},
        embed_info={"label": "none", "model": "none"},
        mode="auto", budget_ceiling=None,
    )


def _write_artifact(ws_root: Path, session_id: str, text: str) -> None:
    art_dir = ws_root / session_id
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "artifact.md").write_text(text, encoding="utf-8")


def test_artifact_has_real_content_gate() -> None:
    """The skeleton gate rejects empty + BOTH placeholder dash spellings, accepts real body."""
    assert _artifact_has_real_content("") is False
    assert _artifact_has_real_content(_SKELETON_HYPHEN) is False
    assert _artifact_has_real_content(_SKELETON_EMDASH) is False
    assert _artifact_has_real_content(_REAL_ARTIFACT) is True


def test_persist_partial_saves_when_artifact_has_content(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    ws_root = tmp_path / "ws"
    session = _make_session()
    runner = Runner(session, lambda _e: None, embedder=None, workspace_root=ws_root)
    _write_artifact(ws_root, session.session_id, _REAL_ARTIFACT)

    saved = runner._persist_partial_run("study agent loops", RuntimeError("Connection error."))
    assert saved is True

    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    rows = store.all_runs(task_hash(base_identity("study agent loops")))
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "failed_partial"
    assert row.score == 0.0
    assert row.weaknesses == []
    # carries the artifact text for carry-forward (_read_workspace_artifact strips edges).
    assert row.result_text == _REAL_ARTIFACT.strip()
    # The failure reason is preserved in config_json for inspection (config is stored, not
    # hydrated back onto TaskRun by _row_to_run — only latest_config reads that column, and
    # it now skips failed rows by design). Verify it via the raw column.
    (raw_config,) = store._conn.execute(
        "SELECT config_json FROM task_runs WHERE status = 'failed_partial'"
    ).fetchone()
    assert "Connection error." in raw_config


def test_persist_partial_skips_skeleton(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    ws_root = tmp_path / "ws"
    session = _make_session()
    runner = Runner(session, lambda _e: None, embedder=None, workspace_root=ws_root)

    for skeleton in (_SKELETON_HYPHEN, _SKELETON_EMDASH):
        _write_artifact(ws_root, session.session_id, skeleton)
        assert runner._persist_partial_run("t", RuntimeError("boom")) is False

    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    assert store.all_runs(task_hash(base_identity("t"))) == []


def test_persist_partial_never_raises_when_store_explodes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    ws_root = tmp_path / "ws"
    session = _make_session()
    runner = Runner(session, lambda _e: None, embedder=None, workspace_root=ws_root)
    _write_artifact(ws_root, session.session_id, _REAL_ARTIFACT)

    def _boom(self, *a, **k):
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(TaskRunStore, "record_versioned", _boom)
    # Must swallow its own failure and return False — never mask the original error.
    assert runner._persist_partial_run("t", RuntimeError("orig")) is False


def test_run_catch_saves_partial_and_annotates_error(tmp_path: Path, monkeypatch) -> None:
    """The top-level catch: a mid-flight death records a partial row under the SAME
    base-identity task_hash normal recording uses (lineage-fork regression), and the
    surfaced ErrorEvent gains the '(partial progress saved' suffix."""
    from studio.events import ErrorEvent

    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    ws_root = tmp_path / "ws"
    events = []
    session = _make_session()
    runner = Runner(session, events.append, embedder=None, workspace_root=ws_root)
    _write_artifact(ws_root, session.session_id, _REAL_ARTIFACT)

    def _die(_req):
        raise RuntimeError("Connection error.")

    monkeypatch.setattr(runner, "_run_inner", _die)
    req = "study agent loops"
    runner.run(req)

    errs = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errs) == 1
    assert "partial progress saved for carry-forward" in errs[0].message

    # Lineage: the partial row shares the hash normal recording derives for this req.
    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    expected_hash = task_hash(base_identity(req))
    rows = store.all_runs(expected_hash)
    assert len(rows) == 1 and rows[0].status == "failed_partial"
