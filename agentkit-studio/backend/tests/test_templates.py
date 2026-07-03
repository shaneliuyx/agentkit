from pathlib import Path

from fastapi.testclient import TestClient

import studio.app as studio_app
from studio.task_runs import _vec_to_blob
from studio.templates import TemplateStore


class _Embedder:
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def test_template_audit_disables_bad_skeletons(tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    assert store.save_template("generic report", "# Summary\n\n_(pending — needs sourced content)_")

    result = store.audit_templates("general")

    assert result[0]["status"] == "disabled"
    row = store._conn.execute(  # noqa: SLF001 - test verifies sqlite migration state
        "SELECT status, failure_reason FROM report_templates"
    ).fetchone()
    assert row[0] == "disabled"
    assert "Placeholder text remains" in row[1]


def test_template_audit_disables_off_profile_code_sections(tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    assert store.save_template("market report", "# Executive Summary\n\n## Implementation Code\n")

    result = store.audit_templates("market")

    assert result[0]["status"] == "disabled"
    assert any("Off-profile code" in issue for issue in result[0]["issues"])


def test_find_template_ignores_disabled_rows(tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db", embedder=_Embedder())
    emb = _vec_to_blob([1.0, 0.0])
    store._conn.execute(  # noqa: SLF001
        "INSERT INTO report_templates "
        "(name, requirement, skeleton, requirement_embedding, status) VALUES (?, ?, ?, ?, ?)",
        ("bad", "same", "# Bad\n", emb, "disabled"),
    )
    store._conn.execute(  # noqa: SLF001
        "INSERT INTO report_templates "
        "(name, requirement, skeleton, requirement_embedding, status) VALUES (?, ?, ?, ?, ?)",
        ("good", "same", "# Good\n", emb, "active"),
    )
    store._conn.commit()  # noqa: SLF001

    assert store.find_template("same", threshold=0.1) == "# Good\n"
    last_used = store._conn.execute(  # noqa: SLF001 - verifies usage metadata
        "SELECT last_used_at FROM report_templates WHERE name = 'good'"
    ).fetchone()[0]
    assert last_used


def test_list_templates_returns_inventory_without_full_body(tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    long_body = "# Good\n\n" + ("x" * 500)
    assert store.save_template("generic report", long_body, name="good")

    row = store.list_templates()[0]

    assert row["name"] == "good"
    assert row["status"] == "active"
    assert row["heading_count"] == 1
    assert len(row["preview"]) == 240
    assert "skeleton" not in row
    assert "quality_score" in row
    assert "approved_by" in row
    assert "last_used_at" in row


def test_replace_template_preserves_row_and_audits_skeleton(tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    assert store.save_template("generic report", "# Old\n", name="seed")
    template_id = store.list_templates()[0]["id"]

    row = store.replace_template(
        template_id,
        "# New\n\n## Evidence\n\nSourced notes.",
        report_type="general",
    )

    assert row is not None
    assert row["id"] == template_id
    assert row["name"] == "seed"
    assert row["requirement"] == "generic report"
    assert row["status"] == "active"
    assert row["source"] == "approved"
    stored = store._conn.execute(  # noqa: SLF001 - verifies in-place row update
        "SELECT skeleton FROM report_templates WHERE id = ?", (template_id,)
    ).fetchone()[0]
    assert stored == "# New\n\n## Evidence\n\nSourced notes.\n"


def test_replace_template_disables_invalid_replacement(tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    assert store.save_template("market report", "# Old\n")
    template_id = store.list_templates()[0]["id"]

    row = store.replace_template(
        template_id,
        "# Executive Summary\n\n## Implementation Code\n",
        report_type="market",
    )

    assert row is not None
    assert row["status"] == "disabled"
    assert "Off-profile code" in row["failure_reason"]


def test_approve_template_records_metadata_for_clean_rows(tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    assert store.save_template("generic report", "# Executive Summary\n\n## Evidence\n")
    template_id = store.list_templates()[0]["id"]

    row = store.approve_template(template_id, approved_by="reviewer", quality_score=0.91)

    assert row is not None
    assert row["source"] == "approved"
    assert row["status"] == "active"
    assert row["approved_by"] == "reviewer"
    assert row["quality_score"] == 0.91


def test_approve_template_keeps_unsafe_rows_disabled(tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    assert store.save_template("market report", "# Executive Summary\n\n## Implementation Code\n")
    template_id = store.list_templates()[0]["id"]

    row = store.approve_template(template_id, approved_by="reviewer")

    assert row is not None
    assert row["status"] == "disabled"
    assert row["source"] == "learned"
    assert "Off-profile code" in row["failure_reason"]


def test_export_templates_includes_full_skeleton(tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    assert store.save_template("generic report", "# Full\n\n## Evidence\n", name="full")

    row = store.export_templates()[0]

    assert row["name"] == "full"
    assert row["skeleton"] == "# Full\n\n## Evidence\n"


def test_import_templates_audits_and_skips_duplicate_skeletons(tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")

    rows = store.import_templates([
        {
            "name": "market",
            "requirement": "market report",
            "report_type": "market",
            "skeleton": "# Executive Summary\n\n## Implementation Code\n",
        },
        {
            "name": "generic",
            "requirement": "generic report",
            "report_type": "general",
            "skeleton": "# Executive Summary\n\n## Evidence\n",
        },
    ])
    duplicate = store.import_templates([{
        "name": "generic copy",
        "skeleton": "# Executive Summary\n\n## Evidence\n",
    }])[0]

    assert rows[0]["status"] == "disabled"
    assert "Off-profile code" in rows[0]["failure_reason"]
    assert rows[1]["status"] == "active"
    assert rows[1]["source"] == "imported"
    assert duplicate["imported"] is False
    assert len(store.export_templates()) == 2


def test_template_audit_endpoint_uses_store(monkeypatch, tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    store.save_template("generic report", "# Summary\n\n_(pending — needs sourced content)_")
    monkeypatch.setattr(studio_app, "TemplateStore", lambda: store)

    resp = TestClient(studio_app.app).post(
        "/catalog/templates/audit", json={"report_type": "general"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["disabled"] == 1
    assert body["audited"][0]["status"] == "disabled"


def test_template_list_endpoint_uses_store(monkeypatch, tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    store.save_template("generic report", "# Good\n", name="good")
    monkeypatch.setattr(studio_app, "TemplateStore", lambda: store)

    resp = TestClient(studio_app.app).get("/catalog/templates?status=active")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    assert body["templates"][0]["name"] == "good"
    assert body["templates"][0]["status"] == "active"


def test_template_replace_endpoint_uses_store(monkeypatch, tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    store.save_template("generic report", "# Old\n", name="seed")
    template_id = store.list_templates()[0]["id"]
    monkeypatch.setattr(studio_app, "TemplateStore", lambda: store)

    resp = TestClient(studio_app.app).post(
        f"/catalog/templates/{template_id}/replace",
        json={"skeleton": "# New\n\n## Evidence\n\nSourced notes.", "report_type": "general"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["template"]["id"] == template_id
    assert body["template"]["status"] == "active"


def test_template_approve_endpoint_uses_store(monkeypatch, tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    store.save_template("generic report", "# Executive Summary\n\n## Evidence\n")
    template_id = store.list_templates()[0]["id"]
    monkeypatch.setattr(studio_app, "TemplateStore", lambda: store)

    resp = TestClient(studio_app.app).post(
        f"/catalog/templates/{template_id}/approve",
        json={"approved_by": "reviewer", "quality_score": 0.9},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["template"]["source"] == "approved"
    assert body["template"]["approved_by"] == "reviewer"
    assert body["template"]["quality_score"] == 0.9


def test_template_export_endpoint_returns_full_skeleton(monkeypatch, tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    store.save_template("generic report", "# Full\n", name="full")
    monkeypatch.setattr(studio_app, "TemplateStore", lambda: store)

    resp = TestClient(studio_app.app).get("/catalog/templates/export")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    assert body["templates"][0]["skeleton"] == "# Full\n"


def test_template_import_endpoint_uses_store(monkeypatch, tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    monkeypatch.setattr(studio_app, "TemplateStore", lambda: store)

    resp = TestClient(studio_app.app).post(
        "/catalog/templates/import",
        json={
            "templates": [{
                "name": "generic",
                "requirement": "generic report",
                "skeleton": "# Executive Summary\n\n## Evidence\n",
            }]
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["imported"] == 1
    assert store.export_templates()[0]["name"] == "generic"


def test_template_replace_endpoint_rejects_missing_rows(monkeypatch, tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    monkeypatch.setattr(studio_app, "TemplateStore", lambda: store)

    resp = TestClient(studio_app.app).post(
        "/catalog/templates/999/replace", json={"skeleton": "# New\n"}
    )

    assert resp.status_code == 404


def test_template_replace_endpoint_rejects_empty_skeleton(monkeypatch, tmp_path: Path) -> None:
    store = TemplateStore(db_path=tmp_path / "templates.db")
    store.save_template("generic report", "# Old\n")
    template_id = store.list_templates()[0]["id"]
    monkeypatch.setattr(studio_app, "TemplateStore", lambda: store)

    resp = TestClient(studio_app.app).post(
        f"/catalog/templates/{template_id}/replace", json={"skeleton": "  "}
    )

    assert resp.status_code == 422
