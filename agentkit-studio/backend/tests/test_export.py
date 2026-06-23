"""M9: GET /export serializes a finished run to a loop; 409 on a fresh session.

Uses FastAPI's TestClient. The /export path reads a session's RunSnapshot, so we
record one directly (the runner records it at run end; here we inject it to keep
the test offline and fast). /skills is also exercised — it is pure.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from studio.app import app, registry
from studio.export import run_to_loop
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


def test_export_on_fresh_session_409() -> None:
    session = _new_session()  # no run recorded
    resp = client.get(f"/export/{session.session_id}")
    assert resp.status_code == 409, resp.text
    assert "no finished run" in resp.json()["detail"]


def test_export_unknown_session_404() -> None:
    resp = client.get("/export/s_does_not_exist")
    assert resp.status_code == 404


def test_skills_endpoint_lists_five_paths() -> None:
    resp = client.get("/skills")
    assert resp.status_code == 200
    skills = resp.json()["skills"]
    assert {s["name"] for s in skills} == {
        "discover", "find", "loop-doctor", "adapt", "design"
    }
    assert all(s["description"].strip() for s in skills)


def test_run_to_loop_is_pure_and_round_trips() -> None:
    """The serializer is a pure value->value mapping (no app needed)."""
    loop = run_to_loop(_finished_snapshot())
    assert _REQUIRED_LOOP_FIELDS <= set(loop)
    assert loop["prompt"].startswith("compare redis")
    assert loop["slug"]  # non-empty slug
