"""Report-template store: reuse a proven report SKELETON across research runs.

On the FIRST document of a run, the requirement is matched (semantic cosine over the BGE-M3
embedder — the same machinery as ``TaskRunStore.similar_runs``) against saved templates; a good
match seeds the skeleton instead of LLM-generating one, so the STRUCTURE of a good report is
reused (headings only, never its content). Templates are extracted from finished reports."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from studio.task_runs import _blob_to_vec, _cosine, _db_path, _vec_to_blob


def extract_skeleton(report_md: str) -> str:
    """Heading skeleton of a report: every '#'/'##'/'###' heading kept, each body replaced by a
    placeholder line. This is exactly the format ``_build_skeleton`` emits, so it drops straight
    into the first-document pipeline."""
    out: list[str] = []
    in_fence = False
    for ln in report_md.splitlines():
        if ln.lstrip().startswith("```"):   # skip fenced code — '#' lines inside are comments,
            in_fence = not in_fence          # not document headings (e.g. '# PLAN.md')
            continue
        if not in_fence and re.match(r"^#{1,3}\s+\S", ln):
            out.append(ln.rstrip())
            out.append("_(pending — needs sourced content)_")
            out.append("")
    return ("\n".join(out).strip() + "\n") if out else ""


class TemplateStore:
    """SQLite store of reusable report skeletons (table ``report_templates`` in task_runs.db)."""

    def __init__(self, db_path: Path | None = None, embedder: Any = None) -> None:
        self._path = db_path or _db_path()
        self._embedder = embedder
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS report_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                requirement TEXT NOT NULL DEFAULT '',
                skeleton TEXT NOT NULL,
                requirement_embedding BLOB,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.commit()

    def save_template(self, requirement: str, skeleton: str, name: str = "") -> bool:
        """Save a skeleton under its requirement (embedded for later search). Skips an empty
        skeleton or one already stored verbatim. Returns True if a row was inserted."""
        skeleton = (skeleton or "").strip()
        if not skeleton:
            return False
        if self._conn.execute(
            "SELECT 1 FROM report_templates WHERE skeleton = ? LIMIT 1", (skeleton + "\n",)
        ).fetchone():
            return False
        emb = None
        if self._embedder is not None and requirement.strip():
            try:
                emb = _vec_to_blob(self._embedder.embed([requirement])[0])
            except Exception:  # noqa: BLE001 — embedding is best-effort enrichment
                emb = None
        self._conn.execute(
            "INSERT INTO report_templates (name, requirement, skeleton, requirement_embedding) "
            "VALUES (?, ?, ?, ?)",
            (name, requirement, skeleton + "\n", emb),
        )
        self._conn.commit()
        return True

    def find_template(self, requirement: str, threshold: float = 0.6) -> str | None:
        """Best-matching skeleton for ``requirement`` by cosine over the stored requirement
        embeddings, or None if nothing clears ``threshold`` (or no embedder is wired)."""
        if self._embedder is None or not requirement.strip():
            return None
        try:
            qvec = self._embedder.embed([requirement])[0]
        except Exception:  # noqa: BLE001
            return None
        best: str | None = None
        best_sim = threshold
        for skeleton, emb in self._conn.execute(
            "SELECT skeleton, requirement_embedding FROM report_templates "
            "WHERE requirement_embedding IS NOT NULL"
        ):
            sim = _cosine(qvec, _blob_to_vec(emb))
            if sim >= best_sim:
                best, best_sim = skeleton, sim
        return best
