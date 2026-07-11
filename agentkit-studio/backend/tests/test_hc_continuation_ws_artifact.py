"""HIGH (rf-reviewer): on an auto-improve continuation, does the stale seeded
workspace artifact.md override research_first's fresh output?

Controlled, network-free repro: research_first's own generation is
monkeypatched to a fixed FRESH_TEXT (short, clean) so the only variable is
whether the pre-existing (seeded) workspace artifact.md — longer, clean,
same-or-better lints — wins the runner.py:2593 preference check.
"""
from __future__ import annotations

from studio.artifact_lint import lint_artifact
from studio.runner import Runner
from studio.session import SessionRegistry
from studio.task_runs import TaskRun, TaskRunStore, task_hash


def _make_session():
    reg = SessionRegistry()
    return reg.create(
        llm_spec={"profile": "qwen"}, embed_spec={},
        llm_info={"label": "qwen", "model": "Qwen-test"},
        embed_info={"label": "none", "model": "none"},
        mode="auto", budget_ceiling=None,
    )


REQ = "Study Pi and Craft to build agents and write a research report."

# NOT a generic title ("# Report" alone matches artifact_text's placeholder
# regex and gets silently replaced by resolve_report_title — a fixture
# artifact, not something real research_first output would ever produce
# since its own ASSEMBLE stage already derives a real title).
FRESH_TEXT = "# Pi and Craft Integration Report\n\n## Executive Summary\n\nFresh output discusses the topic briefly (https://fresh.test/a).\n"
STALE_TEXT = (
    "# Stale Report\n\n## Executive Summary\n\n"
    + ("Stale seeded paragraph content padded for length. " * 30)
    + "(https://stale.test/a).\n\n## References\n\n- https://stale.test/a\n"
)


def _assert_repro_fixture_is_sound() -> None:
    assert len(STALE_TEXT) > len(FRESH_TEXT)
    assert lint_artifact(STALE_TEXT) == []
    assert lint_artifact(FRESH_TEXT) == []


def test_stale_seed_does_not_override_fresh_research_first_output(
    fake_client_factory, tmp_path, monkeypatch
) -> None:
    _assert_repro_fixture_is_sound()
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))

    # Prior run for the SAME task_hash — the hill-climb seed source.
    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    store.record(TaskRun(
        task_hash=task_hash(REQ), session_id="s_prior", version=1, score=0.6,
        weaknesses=[], artifact_path="", requirement=REQ, result_text=STALE_TEXT,
    ))

    # Patch the underlying generator, NOT the Runner method that calls it —
    # the method itself carries the fix (writes the fresh text to
    # artifact.md), so replacing the whole method would test around the fix
    # instead of through it.
    import studio.research_first as research_first_mod

    monkeypatch.setattr(research_first_mod, "generate_research_first", lambda *_a, **_k: FRESH_TEXT)

    session = _make_session()
    session.use_research_first = True
    session.hill_climb_config = {"auto_improve": True, "max_epochs": 1}
    runner = Runner(
        session, lambda _e: None, client_factory=fake_client_factory,
        embedder=None, workspace_root=tmp_path / "ws",
    )
    runner.run(REQ)

    recorded = store.latest_with_content(task_hash(REQ), ws_root=tmp_path / "ws")
    assert recorded is not None
    assert FRESH_TEXT.strip() in recorded.result_text, (
        "the stale seeded artifact.md overrode research_first's fresh output "
        f"(recorded {len(recorded.result_text)} chars, expected the "
        f"{len(FRESH_TEXT)}-char fresh text)"
    )

    # Seed-chain check (team-lead): a clean recorded row is not enough — if
    # THIS session's own workspace artifact.md is left holding the stale
    # seed, the NEXT continuation seeds from the file (preferred over
    # result_text) and the bug re-enters through the workspace even though
    # the DB is clean.
    ws_art = (tmp_path / "ws" / session.session_id / "artifact.md")
    assert ws_art.exists(), "no artifact.md written for this session's workspace"
    assert ws_art.read_text().strip() == FRESH_TEXT.strip(), (
        "this session's own workspace artifact.md still holds stale content — "
        "the NEXT continuation would seed from it and re-introduce the bug"
    )


def test_research_first_seed_carry_forward_is_skipped(tmp_path, monkeypatch) -> None:
    # MVP-9: research_first is cold-start (D4). Even with auto_improve + a prior
    # lineage row, the seed carry-forward must NOT run — seeding writes the prior
    # artifact into artifact.md AND splits it into per-section files, and a
    # downstream merge pulled the prior's relationship section back onto the
    # fresh output (live v45–v47: a stale "Comparison" section accumulated on top
    # of the fresh "Integration" one and broke References-last). The generator's
    # raw output is clean; the pollution was entirely this carry-forward.
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    store.record(TaskRun(
        task_hash=task_hash(REQ), session_id="s_prior", version=1, score=0.6,
        weaknesses=[], artifact_path="", requirement=REQ, result_text=STALE_TEXT,
    ))
    session = _make_session()
    session.use_research_first = True
    runner = Runner(
        session, lambda _e: None, client_factory=None,
        embedder=None, workspace_root=tmp_path / "ws",
    )
    (_req, _weak, artifact_copied, _ws, seed_len, seed_text, _cross, _topic) = (
        runner._seed_carry_forward(
            session=session, requirement=REQ, _base_requirement=REQ,
            _hc_cfg={"auto_improve": True},
        )
    )
    assert artifact_copied is False, "research_first session must not copy a seed"
    assert seed_len == 0 and seed_text == "", "research_first session must cold-start"


def test_repeat_failure_machinery_not_invoked_under_research_first(tmp_path, monkeypatch) -> None:
    # L4 MEASURED-NULL invariant (2026-07-11): 'repeat-weakness escalation' needs a hot
    # iterative loop where the SAME weakness survives K epochs so a lever can escalate.
    # research_first is cold-start, so its repeat-failure substrate (repeat_failures /
    # _repeat_failed) is gated OFF — the SAME `if auto_improve and NOT use_research_first`
    # guard that skips seed carry-forward. Building L4 on research_first would be dead
    # code; its intent (notice the wall, try once, disclose) is met per-run by P2.5
    # recovery→Limitation. This asserts the detector is never even called.
    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    store = TaskRunStore(db_path=tmp_path / "task_runs.db")
    for v in (1, 2, 3):  # a weakness recorded across enough runs to trip repeat_failures
        store.record(TaskRun(
            task_hash=task_hash(REQ), session_id=f"s{v}", version=v, score=0.5,
            weaknesses=["[editorial:E3] uncited subject"], artifact_path="",
            requirement=REQ, result_text=STALE_TEXT,
        ))
    calls: list[int] = []
    orig = TaskRunStore.repeat_failures
    monkeypatch.setattr(
        TaskRunStore, "repeat_failures",
        lambda self, *a, **k: (calls.append(1), orig(self, *a, **k))[1],
    )
    session = _make_session()
    session.use_research_first = True
    runner = Runner(session, lambda _e: None, client_factory=None,
                    embedder=None, workspace_root=tmp_path / "ws")
    runner._seed_carry_forward(
        session=session, requirement=REQ, _base_requirement=REQ,
        _hc_cfg={"auto_improve": True},
    )
    assert calls == [], "repeat_failures (L4 substrate) must NOT run on a research_first cold-start"
