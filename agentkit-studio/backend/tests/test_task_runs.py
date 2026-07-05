"""Tests for the L3 lineage immune system (PLAN §6 L3 / §12 slice 4).

``TaskRunStore.latest_with_content`` must never seed a new run from a poisoned
row (crashed, malformed, uncited, or an outlier-low score) — it walks back
through history and returns the newest row that is actually eligible, or None
(cold start) when every row is poisoned. See ``studio.task_runs._seed_ineligible_reason``.
"""
from __future__ import annotations

from pathlib import Path

from studio.task_runs import TaskRun, TaskRunStore, task_hash

_URL = "https://example.com/report"
_CLEAN = f"# Report\n\n## Findings\nSourced content. {_URL}\n"
_UNCLOSED_FENCE = f"# Report\n\n## Findings\n{_URL}\n```python\nprint('oops')\n"


def _run(th: str, sid: str, version: int, score: float, text: str, *, status: str = "completed") -> TaskRun:
    return TaskRun(
        task_hash=th, session_id=sid, version=version, score=score, weaknesses=[],
        artifact_path="", requirement="req", result_text=text, status=status,
    )


def test_healthy_latest_row_returned_unchanged(tmp_path: Path) -> None:
    """No behavior change for a clean lineage — the critical regression case."""
    store = TaskRunStore(db_path=tmp_path / "t.db")
    th = task_hash("req")
    store.record(_run(th, "s1", 1, 0.6, _CLEAN))
    seed = store.latest_with_content(th, ws_root=tmp_path / "ws")
    assert seed is not None and seed.session_id == "s1"


def test_walks_back_past_crashed_zero_score_row(tmp_path: Path) -> None:
    store = TaskRunStore(db_path=tmp_path / "t.db")
    th = task_hash("req")
    store.record(_run(th, "s_good", 1, 0.6, _CLEAN))
    store.record(_run(th, "s_crash", 2, 0.0, _CLEAN))  # newer but score<=0
    seed = store.latest_with_content(th, ws_root=tmp_path / "ws")
    assert seed is not None and seed.session_id == "s_good"


def test_lint_broken_row_stays_eligible_for_self_heal(tmp_path: Path) -> None:
    """A lint-broken but otherwise-healthy completed row is NOT poison — it is the
    designed input to DESIGN §14.6 in-run self-healing (the reducer receives the
    lint finding as a repair instruction). L3 must not gate on lint, or it would
    seed cold instead of feeding the broken row forward to be repaired in place.
    See test_bad_mermaid_seed_reaches_reducer_with_repair_instruction (test_runner.py)
    and the REVISED note on studio.task_runs._seed_ineligible_reason."""
    store = TaskRunStore(db_path=tmp_path / "t.db")
    th = task_hash("req")
    store.record(_run(th, "s_good", 1, 0.6, _CLEAN))
    store.record(_run(th, "s_broken", 2, 0.7, _UNCLOSED_FENCE))  # newer, lint-broken, still eligible
    seed = store.latest_with_content(th, ws_root=tmp_path / "ws")
    assert seed is not None and seed.session_id == "s_broken"


def test_walks_back_past_no_citation_row(tmp_path: Path) -> None:
    store = TaskRunStore(db_path=tmp_path / "t.db")
    th = task_hash("req")
    store.record(_run(th, "s_good", 1, 0.6, _CLEAN))
    store.record(_run(th, "s_uncited", 2, 0.7, "# Report\n\n## Findings\nNo sources at all.\n"))
    seed = store.latest_with_content(th, ws_root=tmp_path / "ws")
    assert seed is not None and seed.session_id == "s_good"


def test_median_criterion_skipped_below_min_rows(tmp_path: Path) -> None:
    """With fewer than 3 scored rows, an outlier-low (but otherwise clean) score
    must NOT be rejected — the median criterion needs a real sample."""
    store = TaskRunStore(db_path=tmp_path / "t.db")
    th = task_hash("req")
    store.record(_run(th, "s1", 1, 0.9, _CLEAN))
    store.record(_run(th, "s2", 2, 0.1, _CLEAN))  # would fail median if it applied
    seed = store.latest_with_content(th, ws_root=tmp_path / "ws")
    assert seed is not None and seed.session_id == "s2"  # latest wins — no median gate yet


def test_median_criterion_rejects_outlier_with_enough_history(tmp_path: Path) -> None:
    store = TaskRunStore(db_path=tmp_path / "t.db")
    th = task_hash("req")
    store.record(_run(th, "s1", 1, 0.8, _CLEAN))
    store.record(_run(th, "s2", 2, 0.9, _CLEAN))
    store.record(_run(th, "s3", 3, 0.7, _CLEAN))  # median of ALL 4 scored rows [0.2, 0.7, 0.8, 0.9] == 0.75 (self-inclusive, not leave-one-out)
    store.record(_run(th, "s4_outlier", 4, 0.2, _CLEAN))  # 0.2 < 0.5 * 0.8
    seed = store.latest_with_content(th, ws_root=tmp_path / "ws")
    assert seed is not None and seed.session_id == "s3"


def test_all_rows_poisoned_returns_none(tmp_path: Path) -> None:
    """Poisoned == crashed or content-free — lint-brokenness alone is no longer
    poison (a lint-broken row is self-heal input, see test_lint_broken_row_stays_
    eligible_for_self_heal), so this uses only the criteria that still gate."""
    store = TaskRunStore(db_path=tmp_path / "t.db")
    th = task_hash("req")
    store.record(_run(th, "s_crash", 1, 0.0, _CLEAN))
    store.record(_run(th, "s_uncited", 2, 0.7, "# Report\n\n## Findings\nNo sources at all.\n"))
    seed = store.latest_with_content(th, ws_root=tmp_path / "ws")
    assert seed is None


def test_failed_partial_row_exempt_from_the_gate(tmp_path: Path) -> None:
    """entry 166: a failed_partial row's status ALREADY flags it as a crash — the
    gate must not additionally reject it for score<=0 or missing citations."""
    store = TaskRunStore(db_path=tmp_path / "t.db")
    th = task_hash("req")
    store.record(_run(
        th, "s_partial", 1, 0.0, "# Partial\nno citation here", status="failed_partial",
    ))
    seed = store.latest_with_content(th, ws_root=tmp_path / "ws")
    assert seed is not None and seed.session_id == "s_partial"


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        test_healthy_latest_row_returned_unchanged(p / "a")
        test_walks_back_past_crashed_zero_score_row(p / "b")
        test_lint_broken_row_stays_eligible_for_self_heal(p / "c")
        test_walks_back_past_no_citation_row(p / "d")
        test_median_criterion_skipped_below_min_rows(p / "e")
        test_median_criterion_rejects_outlier_with_enough_history(p / "f")
        test_all_rows_poisoned_returns_none(p / "g")
        test_failed_partial_row_exempt_from_the_gate(p / "h")
    print("ok")
