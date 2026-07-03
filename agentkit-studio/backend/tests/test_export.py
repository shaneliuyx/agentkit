"""M9: GET /export serializes a finished run to a loop; 409 on a fresh session.

Uses FastAPI's TestClient. The /export path reads a session's RunSnapshot, so we
record one directly (the runner records it at run end; here we inject it to keep
the test offline and fast). /skills is also exercised — it is pure.
"""

from __future__ import annotations

import subprocess
import sys

from fastapi.testclient import TestClient

from studio.app import app, registry
from studio.export import run_to_loop, run_to_research_package
from studio.session import RunSnapshot

client = TestClient(app)

_REQUIRED_LOOP_FIELDS = {
    "slug", "title", "category", "description", "useWhen",
    "prompt", "verification", "steps", "why", "keywords",
}


def _new_session():
    return registry.create(
        llm_spec={"profile": "qwen"}, embed_spec={},
        llm_info={"label": "qwen", "model": "m"}, embed_info={"label": "none", "model": "none"},
        mode="auto", budget_ceiling=1000.0,
    )


def _finished_snapshot() -> RunSnapshot:
    return RunSnapshot(
        requirement="compare redis and postgres and write a recommendation",
        plan_steps=[
            {"id": "s1", "description": "compare redis and postgres", "depends_on": []},
            {"id": "s2", "description": "write a recommendation", "depends_on": ["s1"]},
        ],
        topology={"s1": "mesh", "s2": "single"},
        loopdoctor_checks=[
            {"name": "bounded", "status": "pass", "fix": ""},
            {"name": "material_checks", "status": "pass", "fix": ""},
            {"name": "safe_actions", "status": "pass", "fix": ""},
            {"name": "clear_stopping", "status": "pass", "fix": ""},
        ],
        budget_ceiling=1000.0,
        result="Redis for cache, Postgres for durable state.",
        cancelled=False,
        evidence_matrix="| Claim | Source |\n| --- | --- |\n| Redis is fast | https://redis.io |",
        scorecard_100={
            "total": 91.0,
            "categories": [{"category": "Citation integrity", "score": 11, "points": 12}],
        },
        metrics={
            "stop_reason": "validation_passed",
            "stop_report": {"reason": "validation_passed", "tool_calls": 0},
        },
        agent_trace_jsonl='{"id":"obs_1","tool":"web_search","status":"ok"}\n',
        checkpoints_jsonl='{"id":"cp_final","phase_id":"final"}\n',
    )


def test_export_on_finished_run_returns_a_loop() -> None:
    session = _new_session()
    session.record_run(_finished_snapshot())

    resp = client.get(f"/export/{session.session_id}")
    assert resp.status_code == 200, resp.text
    loop = resp.json()["loop"]
    # All required catalog loop fields present.
    assert _REQUIRED_LOOP_FIELDS <= set(loop), _REQUIRED_LOOP_FIELDS - set(loop)
    # The shapes round-trip conceptually.
    assert loop["category"] == {"slug": "engineering", "label": "Engineering"}
    assert set(loop["verification"]) == {"title", "detail"}
    assert isinstance(loop["steps"], list) and len(loop["steps"]) == 2
    # Topology + dependency annotations survive in the flat steps list.
    assert any("mesh" in s for s in loop["steps"])
    assert any("after s1" in s for s in loop["steps"])
    assert isinstance(loop["keywords"], list) and loop["keywords"]
    assert "Redis is fast" in loop["evidenceMatrix"]


def test_export_on_fresh_session_409() -> None:
    session = _new_session()  # no run recorded
    resp = client.get(f"/export/{session.session_id}")
    assert resp.status_code == 409, resp.text
    assert "no finished run" in resp.json()["detail"]


def test_research_package_export_on_finished_run() -> None:
    session = _new_session()
    session.record_run(_finished_snapshot())

    resp = client.get(f"/export/{session.session_id}/research-package")
    assert resp.status_code == 200, resp.text
    package = resp.json()["package"]
    files = package["files"]

    assert package["manifest"]["format"] == "research_package_json"
    assert package["manifest"]["packageVersion"] == 1
    assert {
        "research_report.md",
        "research_report.html",
        "evidence_matrix.md",
        "evidence_matrix.json",
        "scorecard.json",
        "metrics.json",
        "human_review_checklist.md",
        "run_manifest.json",
        "agent_trace.jsonl",
        "checkpoints.jsonl",
        "source_notes.json",
        "requirements.txt",
        "research_agent_demo.py",
        "research_agent_demo.pseudo",
        "research_agent_demo_output.txt",
        "loop.json",
    } <= set(files)
    assert "Redis for cache" in files["research_report.md"]
    assert "<html" in files["research_report.html"]
    assert "Redis is fast" in files["evidence_matrix.md"]
    assert files["evidence_matrix.json"][0]["claim"] == "Redis is fast"
    assert files["scorecard.json"]["total"] == 91.0
    assert files["metrics.json"]["stop_reason"] == "validation_passed"
    assert '"tool":"web_search"' in files["agent_trace.jsonl"]
    assert '"phase_id":"final"' in files["checkpoints.jsonl"]
    assert files["run_manifest.json"]["packageVersion"] == 1
    assert files["run_manifest.json"]["hasEvidenceMatrix"] is True
    assert files["run_manifest.json"]["evidenceCount"] == 1
    assert files["run_manifest.json"]["review"]["status"] == "NOT_REQUIRED"
    assert files["run_manifest.json"]["stopReport"]["reason"] == "validation_passed"
    assert files["run_manifest.json"]["rendererStatus"]["html"] == "rendered"
    assert files["run_manifest.json"]["rendererStatus"]["pdf"] == "unavailable"
    assert set(files["run_manifest.json"]["exportedFiles"]) == set(files)
    assert files["source_notes.json"][0]["url"] == "https://redis.io"
    assert "def run(" in files["research_agent_demo.py"]
    assert "PLAN topic" in files["research_agent_demo.pseudo"]
    assert "evidence_rows: 1" in files["research_agent_demo_output.txt"]
    assert "Confirm cited evidence" in files["human_review_checklist.md"]


def test_research_package_marks_required_review() -> None:
    snap = RunSnapshot(
        requirement="Write a medical research report about patient safety risks.",
        plan_steps=[{"id": "s1", "description": "draft", "depends_on": []}],
        topology={"s1": "single"},
        loopdoctor_checks=[{"name": "safe_actions", "status": "warn", "fix": "review"}],
        budget_ceiling=None,
        result="Short draft.",
        cancelled=False,
        evidence_matrix="",
        scorecard_100={"score": 60, "max_score": 100, "categories": []},
    )

    package = run_to_research_package(snap)

    review = package["manifest"]["review"]
    assert review["status"] == "REVIEW_REQUIRED"
    assert review["publish_decision"] == "REVIEW_REQUIRED"
    assert "high-impact topic" in review["reasons"]
    assert "Complete required human review" in package["files"]["human_review_checklist.md"]


def test_research_package_export_on_fresh_session_409() -> None:
    session = _new_session()
    resp = client.get(f"/export/{session.session_id}/research-package")
    assert resp.status_code == 409, resp.text


def test_export_unknown_session_404() -> None:
    resp = client.get("/export/s_does_not_exist")
    assert resp.status_code == 404


def test_skills_endpoint_lists_paths_and_domain_skills() -> None:
    resp = client.get("/skills")
    assert resp.status_code == 200
    skills = resp.json()["skills"]
    names = {s["name"] for s in skills}
    # the five loop-library paths plus the research-report domain skill
    assert {"discover", "find", "loop-doctor", "adapt", "design"} <= names
    assert "research-report-agent" in names
    assert all(s["description"].strip() for s in skills)


def test_run_to_loop_is_pure_and_round_trips() -> None:
    """The serializer is a pure value->value mapping (no app needed)."""
    loop = run_to_loop(_finished_snapshot())
    assert _REQUIRED_LOOP_FIELDS <= set(loop)
    assert loop["prompt"].startswith("compare redis")
    assert loop["slug"]  # non-empty slug


def test_run_to_research_package_is_pure() -> None:
    package = run_to_research_package(_finished_snapshot())
    assert "research_report.md" in package["files"]
    assert package["files"]["loop.json"]["prompt"].startswith("compare redis")


def test_research_package_html_renders_headings_and_tables() -> None:
    snap = RunSnapshot(
        requirement="Write a research report.",
        plan_steps=[],
        topology={},
        loopdoctor_checks=[],
        budget_ceiling=None,
        result="# Report\n\n| Claim | Status |\n| --- | --- |\n| A | verified |",
        cancelled=False,
    )

    html = run_to_research_package(snap)["files"]["research_report.html"]

    assert "<h1>Report</h1>" in html
    assert "<table>" in html
    assert "<td>A</td>" in html


def test_research_package_demo_runs_offline(tmp_path) -> None:
    package = run_to_research_package(_finished_snapshot())
    script = tmp_path / "research_agent_demo.py"
    script.write_text(package["files"]["research_agent_demo.py"], encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "research_report.md" in result.stdout
    assert (tmp_path / "demo_output" / "source_notes.json").is_file()
