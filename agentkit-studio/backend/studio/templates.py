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

from studio.report_profiles import resolve_report_profile
from studio.task_runs import _blob_to_vec, _cosine, _db_path, _vec_to_blob

_TEMPLATE_SELECT = (
    "id, name, requirement, report_type, source, status, failure_reason, created_at, "
    "quality_score, created_from_session, approved_by, last_used_at, skeleton"
)


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
        self._ensure_metadata_columns()
        self._conn.commit()

    def _ensure_metadata_columns(self) -> None:
        cols = {
            row[1] for row in self._conn.execute("PRAGMA table_info(report_templates)")
        }
        for name, ddl in {
            "report_type": "TEXT NOT NULL DEFAULT 'general'",
            "source": "TEXT NOT NULL DEFAULT 'learned'",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "failure_reason": "TEXT NOT NULL DEFAULT ''",
            "quality_score": "REAL",
            "created_from_session": "TEXT NOT NULL DEFAULT ''",
            "approved_by": "TEXT NOT NULL DEFAULT ''",
            "last_used_at": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in cols:
                self._conn.execute(f"ALTER TABLE report_templates ADD COLUMN {name} {ddl}")

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
            "INSERT INTO report_templates "
            "(name, requirement, skeleton, requirement_embedding, report_type, source, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, requirement, skeleton + "\n", emb, "general", "learned", "active"),
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
        best: tuple[int, str] | None = None
        best_sim = threshold
        for row_id, skeleton, emb in self._conn.execute(
            "SELECT id, skeleton, requirement_embedding FROM report_templates "
            "WHERE requirement_embedding IS NOT NULL AND status = 'active'"
        ):
            sim = _cosine(qvec, _blob_to_vec(emb))
            if sim >= best_sim:
                best, best_sim = (row_id, skeleton), sim
        if best is None:
            return None
        self._conn.execute(
            "UPDATE report_templates SET last_used_at = datetime('now') WHERE id = ?",
            (best[0],),
        )
        self._conn.commit()
        return best[1]

    def _row_summary(self, row: tuple[Any, ...], *, include_skeleton: bool = False) -> dict[str, Any]:
        skeleton = row[12] or ""
        out = {
            "id": row[0],
            "name": row[1],
            "requirement": row[2],
            "report_type": row[3],
            "source": row[4],
            "status": row[5],
            "failure_reason": row[6],
            "created_at": row[7],
            "quality_score": row[8],
            "created_from_session": row[9],
            "approved_by": row[10],
            "last_used_at": row[11],
            "heading_count": len(re.findall(r"(?m)^#{1,3}\s+\S", skeleton)),
            "preview": skeleton[:240],
        }
        if include_skeleton:
            out["skeleton"] = skeleton
        return out

    def _audit_skeleton(self, skeleton: str, report_type: str) -> tuple[str, str, str, list[str]]:
        from studio.artifact_lint import lint_artifact

        profile = resolve_report_profile(report_type)
        issues = list(lint_artifact(skeleton))
        if not profile.code_default and re.search(
            r"(?im)^#{1,3}\s+.*\b(code|implementation)\b", skeleton
        ):
            issues.append(
                "[document] Off-profile code or implementation section in non-code report template."
            )
        status = "disabled" if issues else "active"
        return profile.report_type, status, "; ".join(issues), issues

    def list_templates(self, status: str | None = None) -> list[dict[str, Any]]:
        """Return template inventory without full skeleton bodies."""
        where = ""
        args: tuple[str, ...] = ()
        if status:
            where = "WHERE status = ?"
            args = (status,)
        rows = self._conn.execute(
            f"SELECT {_TEMPLATE_SELECT} FROM report_templates "
            f"{where} ORDER BY id",
            args,
        ).fetchall()
        return [self._row_summary(row) for row in rows]

    def export_templates(self, status: str | None = None) -> list[dict[str, Any]]:
        """Return template catalog rows including full skeleton bodies."""
        where = ""
        args: tuple[str, ...] = ()
        if status:
            where = "WHERE status = ?"
            args = (status,)
        rows = self._conn.execute(
            f"SELECT {_TEMPLATE_SELECT} FROM report_templates "
            f"{where} ORDER BY id",
            args,
        ).fetchall()
        return [self._row_summary(row, include_skeleton=True) for row in rows]

    def import_templates(self, templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Import approved/catalog templates and audit each row before activation."""
        imported: list[dict[str, Any]] = []
        for item in templates:
            skeleton = str(item.get("skeleton") or "").strip()
            if not skeleton:
                raise ValueError("each template requires a skeleton")
            existing = self._conn.execute(
                f"SELECT {_TEMPLATE_SELECT} FROM report_templates "
                "WHERE skeleton = ? LIMIT 1",
                (skeleton + "\n",),
            ).fetchone()
            if existing:
                row = self._row_summary(existing, include_skeleton=True)
                row["imported"] = False
                imported.append(row)
                continue

            requirement = str(item.get("requirement") or "")
            report_type, status, reason, _issues = self._audit_skeleton(
                skeleton,
                str(item.get("report_type") or "general"),
            )
            emb = None
            if self._embedder is not None and requirement.strip():
                try:
                    emb = _vec_to_blob(self._embedder.embed([requirement])[0])
                except Exception:  # noqa: BLE001 - import embedding is best effort
                    emb = None
            cur = self._conn.execute(
                "INSERT INTO report_templates "
                "(name, requirement, skeleton, requirement_embedding, report_type, "
                "source, status, failure_reason, quality_score, created_from_session) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(item.get("name") or ""),
                    requirement,
                    skeleton + "\n",
                    emb,
                    report_type,
                    str(item.get("source") or "imported"),
                    status,
                    reason,
                    item.get("quality_score"),
                    str(item.get("created_from_session") or ""),
                ),
            )
            self._conn.commit()
            inserted = self._conn.execute(
                f"SELECT {_TEMPLATE_SELECT} FROM report_templates WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
            row = self._row_summary(inserted, include_skeleton=True)
            row["imported"] = True
            imported.append(row)
        return imported

    def replace_template(
        self,
        template_id: int,
        skeleton: str,
        *,
        report_type: str = "general",
        source: str = "approved",
    ) -> dict[str, Any] | None:
        """Replace a stored skeleton in-place, preserving the row identity."""
        skeleton = (skeleton or "").strip()
        if not skeleton:
            raise ValueError("skeleton is required")
        if not self._conn.execute(
            "SELECT 1 FROM report_templates WHERE id = ? LIMIT 1", (template_id,)
        ).fetchone():
            return None

        resolved_type, status, reason, _issues = self._audit_skeleton(skeleton, report_type)
        # Finding 1: the row's requirement→skeleton binding just changed. The stored
        # requirement_embedding still points find_template() at this row, which would
        # now serve the REPLACED skeleton on a stale semantic match (persistent
        # cross-task contamination). Recompute it when an embedder is wired; otherwise
        # NULL it so find_template() (which filters requirement_embedding IS NOT NULL)
        # can't auto-reuse a replaced skeleton until it is re-learned/re-approved.
        new_emb = None
        if self._embedder is not None:
            req_row = self._conn.execute(
                "SELECT requirement FROM report_templates WHERE id = ?", (template_id,)
            ).fetchone()
            requirement = (req_row[0] if req_row else "") or ""
            if requirement.strip():
                try:
                    new_emb = _vec_to_blob(self._embedder.embed([requirement])[0])
                except Exception:  # noqa: BLE001 — embedding is best-effort; clear on failure
                    new_emb = None
        self._conn.execute(
            "UPDATE report_templates "
            "SET skeleton = ?, report_type = ?, source = ?, status = ?, failure_reason = ?, "
            "requirement_embedding = ? "
            "WHERE id = ?",
            (
                skeleton + "\n",
                resolved_type,
                source,
                status,
                reason,
                new_emb,
                template_id,
            ),
        )
        self._conn.commit()
        return next(
            (row for row in self.list_templates() if row["id"] == template_id),
            None,
        )

    def approve_template(
        self,
        template_id: int,
        *,
        approved_by: str = "",
        quality_score: float | None = None,
    ) -> dict[str, Any] | None:
        """Approve a clean template row; unsafe rows stay disabled."""
        row = self._conn.execute(
            "SELECT skeleton, report_type FROM report_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        if row is None:
            return None
        report_type, status, reason, _issues = self._audit_skeleton(row[0] or "", row[1])
        self._conn.execute(
            "UPDATE report_templates "
            "SET report_type = ?, source = ?, status = ?, failure_reason = ?, "
            "approved_by = ?, quality_score = ? WHERE id = ?",
            (
                report_type,
                "approved" if status == "active" else "learned",
                status,
                reason,
                approved_by,
                quality_score,
                template_id,
            ),
        )
        self._conn.commit()
        return next(
            (row for row in self.list_templates() if row["id"] == template_id),
            None,
        )

    def audit_templates(self, report_type: str = "general") -> list[dict[str, Any]]:
        """Disable unsafe stored skeletons; keep rows for provenance."""
        profile = resolve_report_profile(report_type)
        audited: list[dict[str, Any]] = []
        rows = self._conn.execute(
            "SELECT id, skeleton FROM report_templates WHERE status != 'disabled'"
        ).fetchall()
        for row_id, skeleton in rows:
            _report_type, status, reason, issues = self._audit_skeleton(
                skeleton or "",
                profile.report_type,
            )
            self._conn.execute(
                "UPDATE report_templates "
                "SET status = ?, report_type = ?, failure_reason = ? WHERE id = ?",
                (status, profile.report_type, reason, row_id),
            )
            audited.append({"id": row_id, "status": status, "issues": issues})
        self._conn.commit()
        return audited
