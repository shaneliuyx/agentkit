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


def test_to_context_block_empty():
    ledger = TaskLedger()
    block = ledger.to_context_block()
    assert "(none)" in block
