from studio.run_metrics import build_pass_economics, build_run_metrics, build_stop_report


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


def test_pass_economics_math() -> None:
    econ = build_pass_economics(
        pass_ledger={"a": {"ran": 3, "accepted": 0}, "b": {"ran": 2, "accepted": 1}},
        token_cost=100,
    )
    assert econ["passes"]["a"]["acceptance_rate"] == 0.0  # chronic-0% => S5 removal candidate
    assert econ["passes"]["b"]["acceptance_rate"] == 0.5
    assert econ["total_accepted"] == 1
    assert econ["tokens_per_accepted"] == 100.0


def test_pass_economics_zero_accepted_no_div0() -> None:
    econ = build_pass_economics(pass_ledger={"a": {"ran": 5, "accepted": 0}}, token_cost=100)
    assert econ["tokens_per_accepted"] is None  # no accepted change => undefined, not a crash
