"""M8: Loop Doctor audit — each dimension's pass/warn/fail logic, offline.

build_loopdoctor_event is PURE: it reads a run's plan steps + collected gate
events + verify event + budget ceiling and returns a LoopDoctorEvent. No network,
no services.
"""

from __future__ import annotations

from studio.events import GateEvent, VerifyEvent
from studio.panels.loopdoctor import build_loopdoctor_event


def _checks(event):
    """{name: check-dict} for ergonomic per-dimension assertions."""
    return {c["name"]: c for c in event.checks}


_LINEAR_PLAN = [
    {"id": "s1", "description": "research", "depends_on": [], "role": "r"},
    {"id": "s2", "description": "write", "depends_on": ["s1"], "role": "w"},
]


def _accept_gates(*step_ids: str) -> list[GateEvent]:
    return [GateEvent(name=f"phase:{sid}", outcome="accept") for sid in step_ids]


def _verify_with_claim() -> VerifyEvent:
    return VerifyEvent(findings=[], uncited=["The answer is 42."])


# --- bounded ------------------------------------------------------------------

def test_bounded_pass_when_ceiling_set() -> None:
    event = build_loopdoctor_event(
        _LINEAR_PLAN, budget_ceiling=1000.0,
        gate_events=_accept_gates("s1", "s2"), verify_event=_verify_with_claim(),
    )
    assert _checks(event)["bounded"]["status"] == "pass"
    assert _checks(event)["bounded"]["fix"] == ""


def test_bounded_warn_when_unbounded() -> None:
    event = build_loopdoctor_event(
        _LINEAR_PLAN, budget_ceiling=None,
        gate_events=_accept_gates("s1", "s2"), verify_event=_verify_with_claim(),
    )
    bounded = _checks(event)["bounded"]
    assert bounded["status"] == "warn"
    assert "ceiling" in bounded["fix"].lower()


# --- material_checks ----------------------------------------------------------

def test_material_checks_pass_with_verifiable_claims() -> None:
    event = build_loopdoctor_event(
        _LINEAR_PLAN, budget_ceiling=1.0,
        gate_events=_accept_gates("s1", "s2"), verify_event=_verify_with_claim(),
    )
    assert _checks(event)["material_checks"]["status"] == "pass"


def test_material_checks_warn_when_no_verifiable_claims() -> None:
    event = build_loopdoctor_event(
        _LINEAR_PLAN, budget_ceiling=1.0,
        gate_events=_accept_gates("s1", "s2"),
        verify_event=VerifyEvent(findings=[], uncited=[]),
    )
    assert _checks(event)["material_checks"]["status"] == "warn"


def test_material_checks_warn_when_verify_missing() -> None:
    event = build_loopdoctor_event(
        _LINEAR_PLAN, budget_ceiling=1.0,
        gate_events=_accept_gates("s1", "s2"), verify_event=None,
    )
    assert _checks(event)["material_checks"]["status"] == "warn"


# --- safe_actions -------------------------------------------------------------

def test_safe_actions_pass_when_all_gates_accept() -> None:
    event = build_loopdoctor_event(
        _LINEAR_PLAN, budget_ceiling=1.0,
        gate_events=_accept_gates("s1", "s2"), verify_event=_verify_with_claim(),
    )
    assert _checks(event)["safe_actions"]["status"] == "pass"


def test_safe_actions_fail_on_escalate_names_the_phase() -> None:
    gates = [
        GateEvent(name="phase:s1", outcome="accept"),
        GateEvent(name="phase:s2", outcome="escalate", detail="safety: blocked"),
    ]
    event = build_loopdoctor_event(
        _LINEAR_PLAN, budget_ceiling=1.0,
        gate_events=gates, verify_event=_verify_with_claim(),
    )
    safe = _checks(event)["safe_actions"]
    assert safe["status"] == "fail"
    assert "phase:s2" in safe["fix"]  # the escalated phase is named


def test_safe_actions_fail_on_reject() -> None:
    gates = [GateEvent(name="phase:s1", outcome="reject")]
    event = build_loopdoctor_event(
        [{"id": "s1", "description": "x", "depends_on": []}], budget_ceiling=1.0,
        gate_events=gates, verify_event=_verify_with_claim(),
    )
    assert _checks(event)["safe_actions"]["status"] == "fail"


# --- clear_stopping -----------------------------------------------------------

def test_clear_stopping_pass_on_finite_dag() -> None:
    event = build_loopdoctor_event(
        _LINEAR_PLAN, budget_ceiling=1.0,
        gate_events=_accept_gates("s1", "s2"), verify_event=_verify_with_claim(),
    )
    assert _checks(event)["clear_stopping"]["status"] == "pass"


def test_clear_stopping_warn_on_dangling_depends_on() -> None:
    plan = [{"id": "s1", "description": "x", "depends_on": ["nope"]}]
    event = build_loopdoctor_event(
        plan, budget_ceiling=1.0,
        gate_events=_accept_gates("s1"), verify_event=_verify_with_claim(),
    )
    stop = _checks(event)["clear_stopping"]
    assert stop["status"] == "warn"
    assert "nope" in stop["fix"]


def test_clear_stopping_warn_on_cycle() -> None:
    plan = [
        {"id": "s1", "description": "x", "depends_on": ["s2"]},
        {"id": "s2", "description": "y", "depends_on": ["s1"]},
    ]
    event = build_loopdoctor_event(
        plan, budget_ceiling=1.0,
        gate_events=_accept_gates("s1", "s2"), verify_event=_verify_with_claim(),
    )
    assert _checks(event)["clear_stopping"]["status"] == "warn"


# --- serialization ------------------------------------------------------------

def test_event_serializes_via_to_sse() -> None:
    event = build_loopdoctor_event(
        _LINEAR_PLAN, budget_ceiling=1.0,
        gate_events=_accept_gates("s1", "s2"), verify_event=_verify_with_claim(),
    )
    envelope = event.to_sse("s_test", 123.0)
    assert envelope["type"] == "loopdoctor"
    assert envelope["session_id"] == "s_test"
    assert envelope["ts"] == 123.0
    checks = envelope["payload"]["checks"]
    assert {c["name"] for c in checks} == {
        "bounded", "material_checks", "safe_actions", "clear_stopping"
    }
    for c in checks:
        assert c["status"] in {"pass", "warn", "fail"}
        assert set(c.keys()) == {"name", "status", "fix"}
    # And sse_data round-trips to JSON.
    import json

    assert json.loads(event.sse_data("s_test", 1.0))["type"] == "loopdoctor"
