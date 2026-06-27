"""Tests for agentkit.orchestrator.ledger."""

from agentkit.orchestrator.ledger import TaskLedger, TaskRecord


def _rec(i: int) -> TaskRecord:
    return TaskRecord(id=f"t{i}", description=f"task {i}")


def test_taskrecord_hash_by_id():
    r1 = TaskRecord(id="x", description="a")
    r2 = TaskRecord(id="x", description="b")
    assert r1 == r2
    assert hash(r1) == hash(r2)


def test_ledger_remaining_all_new():
    ledger = TaskLedger()
    ledger.add_task(_rec(1))
    ledger.add_task(_rec(2))
    assert len(ledger.remaining()) == 2


def test_ledger_mark_done_removes_from_remaining():
    ledger = TaskLedger()
    ledger.add_task(_rec(1))
    ledger.add_task(_rec(2))
    ledger.mark_done("t1")
    remaining = ledger.remaining()
    assert len(remaining) == 1
    assert remaining[0].id == "t2"


def test_ledger_in_flight_excluded_from_remaining():
    ledger = TaskLedger()
    ledger.add_task(_rec(1))
    ledger.mark_in_flight("t1")
    assert ledger.remaining() == []


def test_ledger_mark_done_clears_in_flight():
    ledger = TaskLedger()
    ledger.add_task(_rec(1))
    ledger.mark_in_flight("t1")
    ledger.mark_done("t1")
    assert ledger.remaining() == []
    assert len(ledger.completed) == 1


def test_ledger_add_task_dedup():
    ledger = TaskLedger()
    ledger.add_task(_rec(1))
    ledger.add_task(_rec(1))
    assert len(ledger.all_tasks) == 1


def test_to_context_block_format():
    ledger = TaskLedger()
    ledger.add_task(_rec(1))
    ledger.add_task(_rec(2))
    ledger.mark_done("t1")
    block = ledger.to_context_block()
    assert "COMPLETED TASKS" in block
    assert "[t1] task 1" in block
    assert "REMAINING TASKS" in block
    assert "[t2] task 2" in block


def test_runner_seed_then_track_flow_R1():
    """R1: the runner seeds all_tasks up front, then marks each phase in-flight
    while running and done after — so remaining() reflects REAL pending work and
    completed work syncs forward (no re-doing). This mirrors runner.run()."""
    phases = [_rec(1), _rec(2), _rec(3)]
    ledger = TaskLedger()
    for p in phases:  # seed up front (the fix)
        ledger.add_task(p)

    # Before any phase runs: all three are pending, none completed.
    assert {t.id for t in ledger.remaining()} == {"t1", "t2", "t3"}
    assert ledger.completed == []

    # Phase 1 runs: in-flight excludes it from remaining; done moves it forward.
    ledger.mark_in_flight("t1")
    assert "t1" not in {t.id for t in ledger.remaining()}
    ledger.mark_done("t1")

    # At phase 2, the context block shows t1 COMPLETED and t2/t3 REMAINING —
    # the cross-phase 'do not duplicate' signal is now real (was always empty).
    block = ledger.to_context_block()
    assert "[t1] task 1" in block.split("REMAINING")[0]  # t1 in COMPLETED half
    assert "[t2] task 2" in block.split("REMAINING")[1]  # t2 in REMAINING half
    assert {t.id for t in ledger.remaining()} == {"t2", "t3"}

    ledger.mark_in_flight("t2"); ledger.mark_done("t2")
    ledger.mark_in_flight("t3"); ledger.mark_done("t3")
    assert ledger.remaining() == []
    assert {t.id for t in ledger.completed} == {"t1", "t2", "t3"}


def test_to_context_block_empty():
    ledger = TaskLedger()
    block = ledger.to_context_block()
    assert "(none)" in block
