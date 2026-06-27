"""agentkit.orchestrator.ledger — cross-phase task tracking.

A TaskLedger carries the full task universe across phase boundaries so hubs
can assign only remaining work (never duplicate a completed task).

TaskRecord has two fields because they serve different purposes:
  id          — slug for dedup (set/dict membership, O(1) lookup)
  description — full human-readable text that the next hub LLM uses to reason
                about what has already been covered
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TaskRecord:
    """An immutable task descriptor.

    ``id`` uniquely identifies the task for dedup; ``description`` is the full
    text injected into hub prompts so agents understand what work was done.
    """

    id: str
    description: str

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TaskRecord):
            return self.id == other.id
        return NotImplemented


@dataclass
class TaskLedger:
    """Mutable state carried across all phases of one run.

    all_tasks  — the complete universe of tasks for this run (grows as hub
                 output is parsed and new TASK_LIST blocks are discovered).
    completed  — tasks confirmed done by a DONE block from any prior phase.
    in_flight  — task ids currently assigned but not yet confirmed done
                 (collision guard: prevents the same task appearing in two
                 simultaneous assignment blocks).
    """

    all_tasks: list[TaskRecord] = field(default_factory=list)
    completed: list[TaskRecord] = field(default_factory=list)
    in_flight: set[str] = field(default_factory=set)

    def remaining(self) -> list[TaskRecord]:
        """Tasks not yet completed or in-flight."""
        done_ids = {t.id for t in self.completed} | self.in_flight
        return [t for t in self.all_tasks if t.id not in done_ids]

    def mark_done(self, task_id: str) -> None:
        """Move ``task_id`` from in-flight / all-tasks to completed."""
        rec = next((t for t in self.all_tasks if t.id == task_id), None)
        if rec and rec not in self.completed:
            self.completed.append(rec)
        self.in_flight.discard(task_id)

    def mark_in_flight(self, task_id: str) -> None:
        """Record ``task_id`` as currently assigned."""
        self.in_flight.add(task_id)

    def add_task(self, record: TaskRecord) -> None:
        """Add ``record`` to all_tasks if not already present (by id)."""
        if not any(t.id == record.id for t in self.all_tasks):
            self.all_tasks.append(record)

    def to_context_block(self) -> str:
        """Human + machine readable serialisation for injection into hub prompts."""
        done_lines = "\n".join(
            f"- [{t.id}] {t.description}" for t in self.completed
        )
        remaining_lines = "\n".join(
            f"- [{t.id}] {t.description}" for t in self.remaining()
        )
        return (
            f"COMPLETED TASKS FROM PRIOR PHASES:\n{done_lines or '(none)'}\n\n"
            f"REMAINING TASKS (do not duplicate the above):\n{remaining_lines or '(none)'}"
        )
