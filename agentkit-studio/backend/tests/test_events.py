"""Tests for the SSE event contract (events.py)."""

from __future__ import annotations

import json

from studio.events import (
    DoneEvent,
    GraphEvent,
    PlanEvent,
    SessionEvent,
    TokenEvent,
)


def test_envelope_shape() -> None:
    """Every frame is {type, session_id, ts, payload} with the type discriminator."""
    ev = SessionEvent(llm={"label": "haiku", "model": "claude"}, mode="auto")
    env = ev.to_sse("s_abc", 1234.5)
    assert env["type"] == "session"
    assert env["session_id"] == "s_abc"
    assert env["ts"] == 1234.5
    assert env["payload"]["mode"] == "auto"
    assert env["payload"]["llm"]["label"] == "haiku"
    # EVENT_TYPE must not leak into the payload.
    assert "EVENT_TYPE" not in env["payload"]


def test_sse_data_is_valid_json() -> None:
    ev = PlanEvent(task="t", steps=[{"id": "s1", "description": "do it"}])
    data = ev.sse_data("s1", 0.0)
    parsed = json.loads(data)
    assert parsed["type"] == "plan"
    assert parsed["payload"]["steps"][0]["id"] == "s1"


def test_token_payload_field_names() -> None:
    """SPEC §4: token carries input/output/total/estimated/cumulative."""
    ev = TokenEvent(
        step_id="s1",
        input=10,
        output=5,
        total=15,
        estimated=False,
        cumulative={"input": 10, "output": 5, "total": 15, "estimated": False},
    )
    p = ev.payload()
    assert set(p) == {"step_id", "input", "output", "total", "estimated", "cumulative"}
    assert p["cumulative"]["total"] == 15


def test_graph_edges_use_from_to_keys() -> None:
    """Edges use 'from'/'to' string keys (not Python identifiers)."""
    ev = GraphEvent(
        nodes=[{"id": "s1", "kind": "phase", "phase": "s1", "label": "x", "state": "pending"}],
        edges=[{"from": "s1", "to": "s2", "kind": "depends"}],
    )
    p = ev.payload()
    assert p["edges"][0]["from"] == "s1"
    assert p["edges"][0]["to"] == "s2"


def test_done_event_fields() -> None:
    ev = DoneEvent(total_tokens=20, input=12, output=8, estimated=True, result="r")
    p = ev.payload()
    assert p["estimated"] is True
    assert p["total_tokens"] == 20
    assert p["result"] == "r"
