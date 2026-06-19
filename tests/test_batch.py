"""Tests for agentkit.agent.batch — the resilient, resumable batch runner."""

from __future__ import annotations

import json
import tempfile
from typing import Callable

from agentkit.agent import BatchConfig, run_batch

_NOOP: Callable[[float], None] = lambda _s: None


def _paths():
    tmp = tempfile.mkdtemp(prefix="agentkit_test_batch_")
    return f"{tmp}/out.jsonl", f"{tmp}/fails.jsonl"


def test_flaky_fn_retry_success():
    out, fails = _paths()
    attempts: dict[str, int] = {}

    def fn(item: str) -> dict[str, str]:
        attempts[item] = attempts.get(item, 0) + 1
        if attempts[item] == 1:
            raise RuntimeError("transient")
        return {"echo": item}

    stats = run_batch(
        ["x"], fn=fn, output_path=out, failures_path=fails,
        config=BatchConfig(max_retries=2), key=lambda x: x, sleep=_NOOP,
    )
    assert stats == {"done": 1, "failed": 0, "skipped": 0}
    assert attempts["x"] == 2  # one failure then success


def test_permanent_failure_lands_in_failures_file():
    out, fails = _paths()

    def fn(item: str):
        raise RuntimeError("permanent")

    stats = run_batch(
        ["bad"], fn=fn, output_path=out, failures_path=fails,
        config=BatchConfig(max_retries=2), key=lambda x: x, sleep=_NOOP,
    )
    assert stats == {"done": 0, "failed": 1, "skipped": 0}
    lines = open(fails, encoding="utf-8").read().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["key"] == "bad" and "permanent" in rec["error"]


def test_resume_skips_done_items():
    out, fails = _paths()

    def fn(item: str) -> dict[str, str]:
        return {"echo": item}

    first = run_batch(
        ["a", "b"], fn=fn, output_path=out, failures_path=fails,
        key=lambda x: x, sleep=_NOOP,
    )
    assert first["done"] == 2

    second = run_batch(
        ["a", "b", "c"], fn=fn, output_path=out, failures_path=fails,
        key=lambda x: x, sleep=_NOOP,
    )
    assert second["skipped"] == 2  # a, b already done
    assert second["done"] == 1     # only c is new


def test_quota_path_uses_quota_sleep_without_consuming_retries():
    out, fails = _paths()
    attempts = [0]
    slept: list[float] = []

    def fn(item: str) -> dict[str, str]:
        attempts[0] += 1
        if attempts[0] <= 3:
            raise RuntimeError("429 quota exceeded")
        return {"echo": item}

    stats = run_batch(
        ["q"], fn=fn, output_path=out, failures_path=fails,
        config=BatchConfig(max_retries=0, sleep_on_quota=42.0),
        key=lambda x: x,
        is_quota_error=lambda e: "quota" in str(e),
        sleep=slept.append,
    )
    # max_retries=0 yet it succeeded → quota retries did not consume retries.
    assert stats == {"done": 1, "failed": 0, "skipped": 0}
    assert slept == [42.0, 42.0, 42.0]  # three quota waits, all sleep_on_quota


def test_error_path_uses_error_sleep():
    out, fails = _paths()
    slept: list[float] = []

    def fn(item: str):
        raise RuntimeError("boom")

    run_batch(
        ["e"], fn=fn, output_path=out, failures_path=fails,
        config=BatchConfig(max_retries=2, sleep_on_error=7.0),
        key=lambda x: x, sleep=slept.append,
    )
    # Two retries → two error sleeps before recording the permanent failure.
    assert slept == [7.0, 7.0]
