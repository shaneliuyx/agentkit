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


# Loop-closure check (DESIGN §11.4): a weakness re-recorded in this many DISTINCT
# prior runs of the same task was injected and never fixed — stop re-injecting it
# so a persistently-unfixable lesson (data doesn't exist, infra 503) cannot crowd
# out actionable ones forever.
REPEAT_LIMIT = 3


def _norm_weakness(w: str) -> str:
    """Normalize a weakness for recurrence counting across runs.

    Strips a leading "[section]" label, lowercases, and collapses whitespace so
    the same lesson phrased near-identically across runs counts as one. (Coarse by
    design — semantic drift is handled separately by _consolidate_weaknesses.)
    """
    s = re.sub(r"^\s*\[[^\]]*\]\s*", "", w or "")  # drop "[## Section]" / "[document]"
    return re.sub(r"\s+", " ", s).strip().lower()


# --- similarity retrieval helpers (R10: cross-task context history) -----------
# task_hash is an EXACT key — only the identical requirement's history is found.
# To also retrieve context from SIMILAR prior tasks we embed each run's
# requirement and rank by cosine, mirroring agentkit.memory.store's pattern.

def _vec_to_blob(vec: list[float]) -> bytes:
    import numpy as np
    return np.asarray(vec, dtype=np.float32).tobytes()


def _blob_to_vec(blob: bytes):
    import numpy as np
    return np.frombuffer(blob, dtype=np.float32)


def _cosine(a, b) -> float:
    import numpy as np
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0 or a.shape != b.shape:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


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

    def __init__(self, db_path: Path | None = None, embedder: Any = None) -> None:
        self._path = db_path or _db_path()
        # ``embedder`` (optional, agentkit.types.Embedder): when supplied, each
        # recorded run's requirement is embedded so similar_runs() can do
        # cosine-ranked cross-task retrieval (R10). Without it the store works
        # exactly as before (exact task_hash retrieval only).
        self._embedder = embedder
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
        # Migrate existing DBs that lack newer columns.
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(task_runs)").fetchall()}
        if "result_text" not in cols:
            self._conn.execute("ALTER TABLE task_runs ADD COLUMN result_text TEXT NOT NULL DEFAULT ''")
        if "requirement_embedding" not in cols:
            # NULL for existing rows; backfilled lazily by similar_runs().
            self._conn.execute("ALTER TABLE task_runs ADD COLUMN requirement_embedding BLOB")
        self._conn.commit()

    def next_version(self, task_hash_str: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(version) FROM task_runs WHERE task_hash = ?", (task_hash_str,)
        ).fetchone()
        return (row[0] or 0) + 1

    def record(self, run: TaskRun) -> None:
        emb_blob = None
        if self._embedder is not None and run.requirement.strip():
            try:
                vec = self._embedder.embed([run.requirement])[0]
                emb_blob = _vec_to_blob(vec)
            except Exception:  # noqa: BLE001 — embedding is best-effort enrichment
                emb_blob = None
        self._conn.execute(
            """INSERT INTO task_runs
               (task_hash, session_id, version, score, weaknesses_json,
                artifact_path, requirement, result_text, requirement_embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run.task_hash,
                run.session_id,
                run.version,
                run.score,
                json.dumps(run.weaknesses),
                run.artifact_path,
                run.requirement,
                run.result_text,
                emb_blob,
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

    def _backfill_embeddings(self, embedder: Any) -> None:
        """Embed any rows whose requirement_embedding is NULL (lazy migration).

        Existing rows predate the embedding column; embed them once on first
        similarity query so cross-task retrieval works over historical data too.
        """
        rows = self._conn.execute(
            "SELECT id, requirement FROM task_runs "
            "WHERE requirement_embedding IS NULL AND requirement != ''"
        ).fetchall()
        if not rows:
            return
        try:
            vecs = embedder.embed([r[1] for r in rows])
        except Exception:  # noqa: BLE001 — backfill is best-effort
            return
        for (row_id, _req), vec in zip(rows, vecs):
            self._conn.execute(
                "UPDATE task_runs SET requirement_embedding = ? WHERE id = ?",
                (_vec_to_blob(vec), row_id),
            )
        self._conn.commit()

    def similar_runs(
        self,
        requirement: str,
        embedder: Any,
        k: int = 5,
        min_similarity: float = 0.35,
        exclude_hash: str | None = None,
    ) -> list[tuple[TaskRun, float]]:
        """Retrieve context history from SIMILAR prior tasks (R10).

        Unlike exact-key methods (latest/best/all_runs filter on task_hash), this
        embeds ``requirement`` and cosine-ranks every prior task's requirement,
        returning up to ``k`` representative runs (the best-scoring run per
        distinct task_hash) above ``min_similarity``. ``exclude_hash`` drops the
        current task's own exact history (already covered by all_runs).

        Returns ``[(TaskRun, similarity), ...]`` sorted by similarity desc.
        Empty list when no embedder/embeddings or nothing clears the threshold.
        """
        if embedder is None or not requirement.strip():
            return []
        try:
            qvec = embedder.embed([requirement])[0]
        except Exception:  # noqa: BLE001
            return []
        self._backfill_embeddings(embedder)

        # One representative row per distinct task_hash: the best-scoring one
        # (its weaknesses are the most informative). Exclude the current task.
        rows = self._conn.execute(
            """SELECT task_hash, session_id, version, score, weaknesses_json,
                      artifact_path, requirement, result_text, requirement_embedding
               FROM task_runs
               WHERE requirement_embedding IS NOT NULL AND task_hash != ?
               ORDER BY score DESC""",
            (exclude_hash or "",),
        ).fetchall()

        best_by_hash: dict[str, tuple[TaskRun, float]] = {}
        for row in rows:
            thash = row[0]
            if thash in best_by_hash:  # already kept the top-scoring row (ORDER BY score DESC)
                continue
            sim = _cosine(qvec, _blob_to_vec(row[8]))
            if sim >= min_similarity:
                best_by_hash[thash] = (self._row_to_run(row[:8]), sim)

        ranked = sorted(best_by_hash.values(), key=lambda t: t[1], reverse=True)
        return ranked[:k]

    def _consolidate_weaknesses(
        self, weaknesses: list[str], embedder: Any, sim_threshold: float = 0.85,
    ) -> list[str]:
        """Semantic near-duplicate consolidation (beyond exact-string dedup).

        Two weaknesses mined from different runs/tasks often say the same thing
        in different words ("no citations" vs "sources lack URLs"). Exact-string
        dedup keeps both; this drops a weakness when it is >= ``sim_threshold``
        cosine-similar to one already kept, preserving the earlier (higher-
        priority — exact-task-first) phrasing. No-op without an embedder.
        """
        if embedder is None or len(weaknesses) <= 1:
            return weaknesses
        try:
            vecs = embedder.embed(weaknesses)
        except Exception:  # noqa: BLE001 — consolidation is best-effort
            return weaknesses
        kept: list[str] = []
        kept_vecs: list[Any] = []
        for w, v in zip(weaknesses, vecs):
            if any(_cosine(v, kv) >= sim_threshold for kv in kept_vecs):
                continue  # near-duplicate of an already-kept lesson
            kept.append(w)
            kept_vecs.append(v)
        return kept

    def repeat_failures(self, exact_hash: str, limit: int = REPEAT_LIMIT) -> set[str]:
        """Normalized weaknesses re-recorded in >= ``limit`` distinct prior runs.

        The loop-closure signal (DESIGN §11.4): a weakness recorded this many times
        was injected and never fixed, so the **reducer** drops it from its handoff
        instead of grinding on it forever. Counting and the policy live here (the
        history layer); the reducer owns *applying* it before writing a handoff.
        """
        freq: dict[str, int] = {}
        for run in self.all_runs(exact_hash):
            for nk in {_norm_weakness(w) for w in run.weaknesses}:  # per-run distinct
                if nk:
                    freq[nk] = freq.get(nk, 0) + 1
        return {nk for nk, c in freq.items() if c >= limit}

    def accumulated_weaknesses(
        self,
        requirement: str,
        exact_hash: str,
        embedder: Any = None,
        k_similar: int = 5,
        min_similarity: float = 0.35,
        consolidate_threshold: float = 0.85,
    ) -> list[str]:
        """Merge, dedup, and CONSOLIDATE weaknesses for the agent (R10).

        Pipeline: exact-task lessons first (most relevant), then lessons from
        semantically similar prior tasks → exact-string dedup → semantic
        consolidation (near-duplicate phrasings collapsed). With no embedder this
        degrades to exact-string-deduped exact-task weaknesses (prior behaviour).

        The loop-closure check (dropping repeat-failures) is NOT applied here — it
        is owned by the reducer at handoff time (§11.4), so a known-unfixable
        weakness is never emitted into the loop in the first place. See
        ``repeat_failures``.
        """
        seen: set[str] = set()
        merged: list[str] = []
        for run in self.all_runs(exact_hash):
            for w in run.weaknesses:
                if w not in seen:
                    seen.add(w)
                    merged.append(w)
        for run, _sim in self.similar_runs(
            requirement, embedder, k=k_similar,
            min_similarity=min_similarity, exclude_hash=exact_hash,
        ):
            for w in run.weaknesses:
                if w not in seen:
                    seen.add(w)
                    merged.append(w)
        return self._consolidate_weaknesses(merged, embedder, consolidate_threshold)


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
        f"Return a JSON array of short strings. PREFIX each weakness with the artifact "
        f"SECTION it concerns, in square brackets — because the next run assigns each "
        f"section to one agent, and an agent can only fix weaknesses for its own "
        f"section. Use the section's verbatim heading (e.g. '## Sources'); use "
        f"'[document]' only for whole-document or structural issues that no single "
        f'section owns. Example: ["[## Sources] Missing URLs on three articles", '
        f'"[## Findings] Truncated mid-sentence", "[document] No conclusion section"]. '
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
