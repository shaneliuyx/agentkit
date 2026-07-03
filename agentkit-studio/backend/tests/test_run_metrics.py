from studio.run_metrics import build_run_metrics, build_stop_report


def test_run_metrics_handle_zero_denominators() -> None:
    stop = build_stop_report(reason="validation_passed", token_cost=12)

    metrics = build_run_metrics(stop_report=stop)

    assert metrics["task_success"] is True
    assert metrics["tool_call_accuracy"] is None
    assert metrics["citation_accuracy"] is None
    assert metrics["stop_reason"] == "validation_passed"
    assert metrics["stop_report"]["token_cost"] == 12


def test_run_metrics_flag_review_required() -> None:
    stop = build_stop_report(reason="validation_passed", tool_calls=2)

    metrics = build_run_metrics(
        stop_report=stop,
        evidence_count=2,
        weak_evidence_count=1,
        tool_calls=2,
        tool_failures=1,
        review={"required": True},
        scorecard={"score": 81},
    )

    assert metrics["task_success"] is False
    assert metrics["tool_call_accuracy"] == 0.5
    assert metrics["citation_accuracy"] == 0.5
    assert metrics["human_intervention_required"] is True
    assert metrics["score"] == 81.0
