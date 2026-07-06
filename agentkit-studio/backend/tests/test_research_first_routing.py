"""Tests for the research_first generation-core routing switch (PLAN §16).

``_use_research_first(session)`` decides whether ``Runner._run_inner`` calls
``generate_research_first`` (the rebuild) or falls back to the seed-and-patch
``_run_phase_loop``. The primary signal is ``session.use_research_first``
(off by default on the Session dataclass — every test builds a Session via
``SessionRegistry.create`` directly, bypassing the real ``POST /session``
endpoint that turns it on, so existing tests are unaffected). An env var is
an additional operational kill switch, not a task-specific check.
"""
from __future__ import annotations

from types import SimpleNamespace

from studio.runner import _use_research_first


def test_use_research_first_false_by_default(monkeypatch):
    monkeypatch.delenv("STUDIO_DISABLE_RESEARCH_FIRST", raising=False)
    session = SimpleNamespace()  # no use_research_first attribute at all
    assert _use_research_first(session) is False


def test_use_research_first_true_when_session_opts_in(monkeypatch):
    monkeypatch.delenv("STUDIO_DISABLE_RESEARCH_FIRST", raising=False)
    session = SimpleNamespace(use_research_first=True)
    assert _use_research_first(session) is True


def test_use_research_first_kill_switch_overrides_session_flag(monkeypatch):
    session = SimpleNamespace(use_research_first=True)
    for value in ("1", "true", "True", "yes", "YES"):
        monkeypatch.setenv("STUDIO_DISABLE_RESEARCH_FIRST", value)
        assert _use_research_first(session) is False, value


def test_use_research_first_ignores_unrelated_env_values(monkeypatch):
    monkeypatch.setenv("STUDIO_DISABLE_RESEARCH_FIRST", "0")
    session = SimpleNamespace(use_research_first=True)
    assert _use_research_first(session) is True


def test_research_first_exception_surfaces_without_legacy_fallback(
    fake_client_factory, tmp_path, monkeypatch
) -> None:
    """A research_first opt-in crash must be visible, not silently served by the
    legacy phase loop. Operators can still choose the legacy path explicitly via
    ``STUDIO_DISABLE_RESEARCH_FIRST`` before the run starts."""
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    import studio.research_first as research_first_mod
    from studio.runner import Runner
    from studio.session import SessionRegistry
    from studio.task_runs import TaskRunStore, task_hash

    def _boom(*_a, **_k):
        raise RuntimeError("simulated research_first crash")

    monkeypatch.setattr(research_first_mod, "generate_research_first", _boom)

    req = "1. compare redis and postgres 2. write a recommendation"
    reg = SessionRegistry()
    session = reg.create(
        llm_spec={"profile": "qwen"}, embed_spec={},
        llm_info={"label": "qwen", "model": "Qwen-test"},
        embed_info={"label": "none", "model": "none"},
        mode="auto", budget_ceiling=None,
    )
    session.use_research_first = True

    events = []
    runner = Runner(
        session, events.append, client_factory=fake_client_factory,
        embedder=None, workspace_root=tmp_path / "ws",
    )
    runner.run(req)

    error_events = [e for e in events if e.EVENT_TYPE == "error"]
    assert len(error_events) == 1
    assert "RuntimeError: simulated research_first crash" in error_events[0].message

    done_events = [e for e in events if e.EVENT_TYPE == "done"]
    assert len(done_events) == 1, "run must still terminate the stream exactly once"
    assert done_events[0].result == ""
    assert runner._used_research_first is False

    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    rows = store._conn.execute(  # noqa: SLF001 - test verifies exactly-once recording
        "SELECT COUNT(*) FROM task_runs WHERE task_hash = ?", (task_hash(req),)
    ).fetchone()
    assert rows[0] == 0, "must not record a legacy-pipeline success for this task"
