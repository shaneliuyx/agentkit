"""MEDIUM safety-contract test (rf-reviewer): POST /session must actually set
``use_research_first=True`` on the created session — this is the ONLY thing
that routes real GUI sessions onto the research_first pipeline (PLAN §16).
A regression here silently reverts every real session to the old hub/spoke
loop with no visible error.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import studio.app as studio_app


def test_post_session_sets_use_research_first_true() -> None:
    resp = TestClient(studio_app.app).post(
        "/session", json={"llm": {"profile": "qwen"}, "embed": {}}
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    session = studio_app.registry.get(session_id)
    assert session is not None
    assert session.use_research_first is True
