"""agentkit.orchestrator.state — durable orchestration state + structured logs.

A generalized port of the Deli_AutoResearch on-disk state schema. The
orchestrator keeps its progress on the filesystem so a long autonomous run is
crash-resumable and auditable:

  <state_dir>/
    state/
      task_spec.md          — the immutable task brief
      progress.json         — ProgressState (iteration / findings / status)
      findings.jsonl        — append-only Finding records
      directions_tried.json — directions already attempted (diversity input)
      iterations.jsonl      — append-only per-iteration log records
    logs/
      *.jsonl               — structured event logs (info|warn|error|decision)

This module DOES I/O — that is its job. The clock used for log timestamps is
INJECTED (``clock=time.time`` by default) so tests stay deterministic. The PURE
decision logic lives in stall.py / diversity.py / select.py, never here.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

# Log levels.
INFO = "info"
WARN = "warn"
ERROR = "error"
DECISION = "decision"
_LEVELS = {INFO, WARN, ERROR, DECISION}


@dataclass(frozen=True)
class Finding:
    """One immutable research finding produced by a spawned worker."""

    direction: str
    summary: str
    evidence: str = ""
    ts: float = 0.0


@dataclass
class ProgressState:
    """Mutable run-level progress (rewritten each round)."""

    iteration: int = 0
    total_findings: int = 0
    status: str = "running"
    stale_count: int = 0


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _state_dir(state_dir: str | Path) -> Path:
    return Path(state_dir) / "state"


def _logs_dir(state_dir: str | Path) -> Path:
    return Path(state_dir) / "logs"


def _progress_path(state_dir: str | Path) -> Path:
    return _state_dir(state_dir) / "progress.json"


def _findings_path(state_dir: str | Path) -> Path:
    return _state_dir(state_dir) / "findings.jsonl"


def _directions_path(state_dir: str | Path) -> Path:
    return _state_dir(state_dir) / "directions_tried.json"


def _iterations_path(state_dir: str | Path) -> Path:
    return _state_dir(state_dir) / "iterations.jsonl"


def _task_spec_path(state_dir: str | Path) -> Path:
    return _state_dir(state_dir) / "task_spec.md"


# ---------------------------------------------------------------------------
# Init + progress
# ---------------------------------------------------------------------------

def init_task(state_dir: str | Path, task_spec: str) -> None:
    """Create the state/ + logs/ dirs, write the task spec, seed empty progress."""
    _state_dir(state_dir).mkdir(parents=True, exist_ok=True)
    _logs_dir(state_dir).mkdir(parents=True, exist_ok=True)
    _task_spec_path(state_dir).write_text(task_spec, encoding="utf-8")
    save_progress(state_dir, ProgressState())


def save_progress(state_dir: str | Path, progress: ProgressState) -> None:
    """Write ProgressState to progress.json (full rewrite)."""
    _state_dir(state_dir).mkdir(parents=True, exist_ok=True)
    _progress_path(state_dir).write_text(
        json.dumps(asdict(progress), indent=2), encoding="utf-8"
    )


def load_progress(state_dir: str | Path) -> ProgressState:
    """Read ProgressState from progress.json (defaults if absent)."""
    path = _progress_path(state_dir)
    if not path.exists():
        return ProgressState()
    data = json.loads(path.read_text(encoding="utf-8"))
    return ProgressState(
        iteration=int(data.get("iteration", 0)),
        total_findings=int(data.get("total_findings", 0)),
        status=str(data.get("status", "running")),
        stale_count=int(data.get("stale_count", 0)),
    )


# ---------------------------------------------------------------------------
# Findings (append-only JSONL)
# ---------------------------------------------------------------------------

def append_finding(state_dir: str | Path, finding: Finding) -> None:
    """Append one Finding as a JSONL line."""
    _state_dir(state_dir).mkdir(parents=True, exist_ok=True)
    with _findings_path(state_dir).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(finding)) + "\n")


def read_findings(state_dir: str | Path) -> list[Finding]:
    """Read all findings back into Finding records (empty if none)."""
    path = _findings_path(state_dir)
    if not path.exists():
        return []
    out: list[Finding] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        out.append(
            Finding(
                direction=str(data.get("direction", "")),
                summary=str(data.get("summary", "")),
                evidence=str(data.get("evidence", "")),
                ts=float(data.get("ts", 0.0)),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Directions tried (JSON list — diversity input)
# ---------------------------------------------------------------------------

def read_directions(state_dir: str | Path) -> list[str]:
    """Read the list of directions already attempted (empty if none)."""
    path = _directions_path(state_dir)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [str(d) for d in data] if isinstance(data, list) else []


def append_direction(state_dir: str | Path, d: str) -> None:
    """Append one direction to directions_tried.json (read-modify-write)."""
    _state_dir(state_dir).mkdir(parents=True, exist_ok=True)
    directions = read_directions(state_dir)
    directions.append(d)
    _directions_path(state_dir).write_text(
        json.dumps(directions, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Iteration log (append-only JSONL)
# ---------------------------------------------------------------------------

def append_iteration_log(state_dir: str | Path, record: dict[str, Any]) -> None:
    """Append one arbitrary iteration record as a JSONL line."""
    _state_dir(state_dir).mkdir(parents=True, exist_ok=True)
    with _iterations_path(state_dir).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Structured event logging
# ---------------------------------------------------------------------------

def log_event(
    log_path: str | Path,
    source: str,
    level: str,
    event: str,
    detail: str = "",
    clock: Callable[[], float] = time.time,
) -> None:
    """Append one structured JSONL log line.

    Schema: ``{"ts", "source", "level", "event", "detail"}``. ``level`` must be
    one of info|warn|error|decision. The timestamp comes from the INJECTED
    ``clock`` so callers (and tests) control time.
    """
    if level not in _LEVELS:
        raise ValueError(f"invalid log level: {level!r} (expected one of {_LEVELS})")
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": clock(),
        "source": source,
        "level": level,
        "event": event,
        "detail": detail,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    import tempfile

    tmp = tempfile.mkdtemp(prefix="agentkit_state_")

    init_task(tmp, task_spec="Find the fastest cache eviction policy.")
    assert _task_spec_path(tmp).exists()
    assert load_progress(tmp) == ProgressState()

    # Progress round-trip.
    save_progress(tmp, ProgressState(iteration=3, total_findings=5,
                                     status="running", stale_count=1))
    p = load_progress(tmp)
    assert p.iteration == 3 and p.total_findings == 5 and p.stale_count == 1, p

    # Findings round-trip.
    append_finding(tmp, Finding("cache", "LRU beats FIFO", evidence="bench#1", ts=1.0))
    append_finding(tmp, Finding("cache", "ARC beats LRU", evidence="bench#2", ts=2.0))
    findings = read_findings(tmp)
    assert len(findings) == 2 and findings[1].summary == "ARC beats LRU", findings

    # Directions round-trip.
    append_direction(tmp, "tune LRU")
    append_direction(tmp, "try ARC")
    assert read_directions(tmp) == ["tune LRU", "try ARC"]

    # Iteration log append.
    append_iteration_log(tmp, {"round": 1, "action": "continue"})
    assert _iterations_path(tmp).exists()

    # Structured decision log line with an injected clock.
    log_file = _logs_dir(tmp) / "orchestrator.jsonl"
    log_event(log_file, source="orchestrator", level=DECISION,
              event="pivot", detail="structure not tactics", clock=lambda: 42.0)
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["level"] == "decision" and rec["ts"] == 42.0, rec
    assert rec["event"] == "pivot", rec

    print("state self-check OK")
