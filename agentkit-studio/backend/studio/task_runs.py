"""studio.task_runs — cross-session task improvement history.

Records each run's score and weaknesses so subsequent sessions can edit-in-place
rather than regenerating from scratch. Backed by a stable SQLite DB at the
workspace root (never a tmpdir).

Three entry points for the runner:
  task_hash(req)                    → stable 12-char key
  TaskRunStore.latest(hash)         → prior run for a task (seed auto-improve)
  TaskRunStore.record(run)          → write score + weaknesses after a run
  score_result(result, req, client) → LLM 0-1 scorer
  mine_weaknesses_from_outputs(…)   → LLM weakness list from plain text outputs
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from studio.workspace import workspace_root


def _db_path() -> Path:
    root = workspace_root()
    path = root.parent / "task_runs.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def task_hash(requirement: str) -> str:
    """Stable 12-char key derived from the requirement text."""
    return hashlib.sha256(requirement.strip().lower().encode()).hexdigest()[:12]


@dataclass
class TaskRun:
    task_hash: str
    session_id: str
    version: int
    score: float
    weaknesses: list[str]
    artifact_path: str
    requirement: str
    result_text: str = ""


class TaskRunStore:
    """SQLite store for cross-session task run history."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _db_path()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_hash TEXT NOT NULL,
                session_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                score REAL NOT NULL,
                weaknesses_json TEXT NOT NULL DEFAULT '[]',
                artifact_path TEXT NOT NULL DEFAULT '',
                requirement TEXT NOT NULL DEFAULT '',
                result_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migrate existing DBs that lack result_text column
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(task_runs)").fetchall()}
        if "result_text" not in cols:
            self._conn.execute("ALTER TABLE task_runs ADD COLUMN result_text TEXT NOT NULL DEFAULT ''")
        self._conn.commit()

    def next_version(self, task_hash_str: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(version) FROM task_runs WHERE task_hash = ?", (task_hash_str,)
        ).fetchone()
        return (row[0] or 0) + 1

    def record(self, run: TaskRun) -> None:
        self._conn.execute(
            """INSERT INTO task_runs
               (task_hash, session_id, version, score, weaknesses_json,
                artifact_path, requirement, result_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run.task_hash,
                run.session_id,
                run.version,
                run.score,
                json.dumps(run.weaknesses),
                run.artifact_path,
                run.requirement,
                run.result_text,
            ),
        )
        self._conn.commit()

    def _row_to_run(self, row: tuple) -> TaskRun:
        return TaskRun(
            task_hash=row[0], session_id=row[1], version=row[2], score=row[3],
            weaknesses=json.loads(row[4]), artifact_path=row[5],
            requirement=row[6], result_text=row[7],
        )

    def latest(self, task_hash_str: str) -> TaskRun | None:
        row = self._conn.execute(
            """SELECT task_hash, session_id, version, score, weaknesses_json,
                      artifact_path, requirement, result_text
               FROM task_runs WHERE task_hash = ?
               ORDER BY version DESC LIMIT 1""",
            (task_hash_str,),
        ).fetchone()
        return self._row_to_run(row) if row else None

    def best(self, task_hash_str: str) -> TaskRun | None:
        row = self._conn.execute(
            """SELECT task_hash, session_id, version, score, weaknesses_json,
                      artifact_path, requirement, result_text
               FROM task_runs WHERE task_hash = ?
               ORDER BY score DESC LIMIT 1""",
            (task_hash_str,),
        ).fetchone()
        return self._row_to_run(row) if row else None

    def latest_with_content(self, task_hash_str: str, ws_root: Path | None = None) -> TaskRun | None:
        """Return most recent run whose artifact.md exists and is non-empty.

        Prefers *latest* over *best-score*: LLM self-eval scores are noisy and
        the latest run has accumulated the most incremental work.
        """
        from studio.workspace import workspace_root as _ws_root  # noqa: PLC0415
        root = ws_root or _ws_root()
        for run in reversed(self.all_runs(task_hash_str)):
            art = root / run.session_id / "artifact.md"
            if art.exists() and art.stat().st_size > 0:
                return run
        return None

    def all_runs(self, task_hash_str: str) -> list[TaskRun]:
        rows = self._conn.execute(
            """SELECT task_hash, session_id, version, score, weaknesses_json,
                      artifact_path, requirement, result_text
               FROM task_runs WHERE task_hash = ? ORDER BY version ASC""",
            (task_hash_str,),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]


def score_result(
    result: str,
    requirement: str,
    client: Any,
    verified_urls: list[str] | None = None,
) -> tuple[float, str]:
    """LLM 0-1 quality score for the session result.

    Returns ``(score, unmet_feedback)`` where ``unmet_feedback`` is a newline-joined
    list of criteria the scorer found NOT fully met.  Callers that only need the
    score can ignore the second element.  Returns ``(0.5, "")`` on failure.
    """
    if not result.strip():
        return 0.0, ""
    # Rubric-based scoring with criteria DERIVED FROM THE TASK (not hardcoded). A bare
    # "rate 0-1" prompt structurally caps ~0.75 (the model hedges with no criteria), so a
    # flawless output can never earn 1.0. But hardcoding rubric items (e.g. "must cite
    # articles") games the scorer toward one task shape. Instead we ask the model to first
    # derive the criteria THIS specific task implies, then score 0.2 per criterion met —
    # general across any task, and full marks only when the task is genuinely fully met.
    # Show the full output (capped only to avoid pathological inputs) so tail content and
    # the conclusion are visible.
    _cap = min(len(result), 20_000)
    _label = "full output" if len(result) <= _cap else f"first {_cap} chars"
    _today = datetime.date.today().isoformat()
    _url_note = ""
    if verified_urls:
        _url_note = (
            "VERIFIED SOURCES: the following URLs were confirmed real via actual web "
            "search (not fabricated) — treat them as genuine citations:\n"
            + "\n".join(f"  - {u}" for u in verified_urls[:20])
            + "\n"
        )
    prompt = (
        f"Today's date is {_today}. "
        "Score how well the OUTPUT fulfills the TASK, using a rubric you derive from the "
        "task itself.\n"
        "Step 1: from the TASK alone, identify the 5 most important criteria a complete, "
        "excellent response must satisfy (what this task genuinely demands — completeness, "
        "evidence/grounding, structure, directly answering the ask, etc.).\n"
        "Step 2: award 0.2 for EACH criterion the OUTPUT FULLY meets (partial = 0). Be "
        "strict: an incomplete, truncated, unsubstantiated, or off-task output loses points.\n"
        f"{_url_note}"
        f"TASK: {requirement[:400]}\n"
        f"OUTPUT ({_label}): {result[:_cap]}\n"
        "Reply with exactly this format:\n"
        "SCORE: <total 0.0-1.0>\n"
        "UNMET: <one short phrase per criterion NOT fully met, semicolon-separated, or NONE>"
    )
    try:
        resp = client.chat([{"role": "user", "content": prompt}])
        text = (getattr(resp, "text", "") or "").strip()
        score_m = re.search(r"SCORE:\s*(1\.0|0\.\d+)", text)
        score = float(score_m.group(1)) if score_m else 0.5
        unmet_m = re.search(r"UNMET:\s*(.+)", text)
        unmet_raw = unmet_m.group(1).strip() if unmet_m else ""
        unmet_feedback = "" if unmet_raw.upper() == "NONE" else unmet_raw
        return min(1.0, max(0.0, score)), unmet_feedback
    except Exception:  # noqa: BLE001
        pass
    return 0.5, ""


def mine_weaknesses_from_outputs(
    outputs: dict[str, str],
    result: str,
    requirement: str,
    client: Any,
    scorer_feedback: str = "",
) -> list[str]:
    """Extract weakness patterns from plain text phase outputs (no AgentResult needed).

    ``requirement`` is the task itself, so the miner judges against what the task
    genuinely demands rather than hardcoded assumptions about the output's shape.
    ``scorer_feedback`` is the semicolon-separated list of unmet criteria from
    ``score_result`` — passing it avoids re-deriving what the scorer already found.
    """
    combined = "\n\n---\n\n".join(f"[{k}]: {v[:400]}" for k, v in outputs.items())
    if result:
        # Show the FINAL output's head AND tail. Truncation/cutoff happens at the end,
        # so a head-only window (the old 6000-char bug) never saw it and the miner
        # reported "no weaknesses" on a clearly-truncated report. Head+tail lets the
        # miner catch a missing conclusion or a mid-sentence cutoff.
        if len(result) <= 12_000:
            combined += f"\n\n[FINAL]: {result}"
        else:
            combined += (
                f"\n\n[FINAL HEAD]: {result[:8_000]}"
                f"\n\n[FINAL TAIL]: {result[-4_000:]}"
            )
    _today = datetime.date.today().isoformat()
    _scorer_section = (
        f"A quality scorer already evaluated this output and found these UNMET criteria:\n"
        f"{scorer_feedback}\n"
        f"Use these as your starting point — include them and add any further gaps you find.\n\n"
    ) if scorer_feedback else ""
    prompt = (
        f"Today's date is {_today}. "
        f"Review these agent phase outputs against the TASK. List up to 5 specific "
        f"weaknesses or gaps that would stop the output from fully satisfying the task. "
        f"Always check, regardless of task type:\n"
        f"1. Is the output truncated / cut off mid-sentence / missing a proper ending?\n"
        f"2. Are claims substantiated and grounded rather than merely asserted? "
        f"Note: sources dated {_today[:7]} are current, not future.\n"
        f"3. If the TASK asks to FIND, DISCOVER, or LIST articles/sources/links: "
        f"does every cited article or source include an actual URL "
        f"(starting with http:// or https://)? Flag missing URLs as a weakness.\n"
        f"4. If the TASK asks for 'most popular' or 'top' items: is there evidence "
        f"(views, stars, engagement metrics) justifying the ranking?\n\n"
        f"{_scorer_section}"
        f"TASK: {requirement[:400]}\n\n{combined}\n\n"
        f'Return a JSON array of short strings, e.g. ["Truncated mid-section", "Claims unsupported"]. '
        f"Return [] ONLY if the work is genuinely complete and strong."
    )
    try:
        resp = client.chat([{"role": "user", "content": prompt}])
        text = (getattr(resp, "text", "") or "").strip()
        # Greedy match for the OUTERMOST bracket pair — a lazy match (the old `.*?`)
        # stops at the first "]", truncating a weakness string that itself contains one.
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            items = json.loads(m.group())
            return [str(x).strip() for x in items if x][:5]
    except Exception:  # noqa: BLE001
        pass
    return []
