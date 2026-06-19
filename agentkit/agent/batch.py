"""agentkit.agent.batch — resilient, resumable batch runner.

A generalized port of the IdeaScout ``run_autoretry`` pattern: run a function
over many items, surviving transient failures (errors, API quota) and machine
restarts. The output is an append-only JSONL file so a re-run RESUMES — items
already recorded are skipped by key.

The clock and sleep are INJECTED (default real) so tests pass a no-op sleep and
never actually wait. The retry policy distinguishes:
  - quota errors  → wait ``sleep_on_quota`` and retry WITHOUT consuming a retry
                    (the work is fine; we are just rate-limited).
  - other errors  → retry up to ``max_retries`` with ``sleep_on_error`` between;
                    on exhaustion the item is recorded to ``failures_path`` and
                    the batch continues (one bad item never sinks the run).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class BatchConfig:
    """Retry + pacing policy for a batch run."""

    batch_size: int = 1
    sleep_between: float = 0.0
    sleep_on_quota: float = 3600.0
    sleep_on_error: float = 600.0
    max_retries: int = 3


def _read_done_keys(output_path: str | Path) -> set[str]:
    """Collect the keys already recorded in an append-only output file."""
    path = Path(output_path)
    if not path.exists():
        return set()
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = record.get("key")
        if key is not None:
            done.add(str(key))
    return done


def _append_jsonl(path: str | Path, record: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def run_batch(
    items: list[T],
    fn: Callable[[T], Any],
    output_path: str | Path,
    failures_path: str | Path,
    config: BatchConfig = BatchConfig(),
    key: Callable[[T], str] = lambda x: str(x),
    is_quota_error: Callable[[Exception], bool] = lambda e: False,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> dict[str, int]:
    """Run ``fn`` over ``items`` resiliently and resumably.

    Args:
        items:          The work items.
        fn:             The per-item function; its return is recorded as result.
        output_path:    Append-only JSONL of successes ``{"key", "result"}``.
        failures_path:  Append-only JSONL of permanent failures ``{"key", "error"}``.
        config:         Retry + pacing policy.
        key:            Maps an item to a stable string key (for resume dedup).
        is_quota_error: Predicate marking an exception as a rate-limit (retry free).
        sleep:          INJECTED sleep (tests pass a no-op).
        clock:          INJECTED clock (reserved for pacing; not awaited here).

    Returns:
        ``{"done": n, "failed": n, "skipped": n}``.
    """
    done_keys = _read_done_keys(output_path)
    done = 0
    failed = 0
    skipped = 0

    for index, item in enumerate(items):
        item_key = key(item)

        if item_key in done_keys:
            skipped += 1
            continue

        retries_left = config.max_retries
        recorded = False

        while not recorded:
            try:
                result = fn(item)
            except Exception as exc:  # noqa: BLE001 — failures are data, not crashes
                if is_quota_error(exc):
                    # Rate-limited: wait and retry WITHOUT consuming a retry.
                    sleep(config.sleep_on_quota)
                    continue
                if retries_left > 0:
                    retries_left -= 1
                    sleep(config.sleep_on_error)
                    continue
                # Retries exhausted → record a permanent failure and move on.
                _append_jsonl(failures_path, {"key": item_key, "error": str(exc)})
                done_keys.add(item_key)
                failed += 1
                recorded = True
            else:
                _append_jsonl(output_path, {"key": item_key, "result": result})
                done_keys.add(item_key)
                done += 1
                recorded = True

        if config.sleep_between and index < len(items) - 1:
            sleep(config.sleep_between)

    return {"done": done, "failed": failed, "skipped": skipped}


if __name__ == "__main__":
    import tempfile

    tmp = tempfile.mkdtemp(prefix="agentkit_batch_")
    out = f"{tmp}/out.jsonl"
    fails = f"{tmp}/fails.jsonl"

    # A flaky fn: item "flaky" fails once then succeeds; "bad" always fails.
    _attempts: dict[str, int] = {}

    def _fn(item: str) -> dict[str, str]:
        _attempts[item] = _attempts.get(item, 0) + 1
        if item == "flaky" and _attempts[item] == 1:
            raise RuntimeError("transient")
        if item == "bad":
            raise RuntimeError("permanent")
        return {"echo": item}

    noop_sleep: Callable[[float], None] = lambda _s: None

    stats = run_batch(
        ["ok", "flaky", "bad"], fn=_fn, output_path=out, failures_path=fails,
        config=BatchConfig(max_retries=2), key=lambda x: x, sleep=noop_sleep,
    )
    # ok + flaky succeed (flaky after one retry); bad lands in failures.
    assert stats == {"done": 2, "failed": 1, "skipped": 0}, stats
    assert _attempts["flaky"] == 2, _attempts

    fail_lines = open(fails, encoding="utf-8").read().strip().splitlines()
    assert len(fail_lines) == 1 and "bad" in fail_lines[0], fail_lines

    # Resume: a second run skips the two already-done items; bad re-runs (it is
    # in failures, not output) and fails again.
    stats2 = run_batch(
        ["ok", "flaky", "bad"], fn=_fn, output_path=out, failures_path=fails,
        config=BatchConfig(max_retries=2), key=lambda x: x, sleep=noop_sleep,
    )
    assert stats2["skipped"] == 2, stats2
    assert stats2["done"] == 0, stats2

    # Quota path: a quota error retries via sleep_on_quota WITHOUT consuming a
    # retry; we assert it used the quota sleep and eventually succeeded.
    quota_tmp = tempfile.mkdtemp(prefix="agentkit_batch_q_")
    q_out = f"{quota_tmp}/out.jsonl"
    q_fails = f"{quota_tmp}/fails.jsonl"
    _q_attempts = [0]
    _slept: list[float] = []

    def _quota_fn(item: str) -> dict[str, str]:
        _q_attempts[0] += 1
        if _q_attempts[0] <= 3:  # rate-limited the first 3 times
            raise RuntimeError("429 quota exceeded")
        return {"echo": item}

    def _spy_sleep(seconds: float) -> None:
        _slept.append(seconds)

    qstats = run_batch(
        ["q"], fn=_quota_fn, output_path=q_out, failures_path=q_fails,
        config=BatchConfig(max_retries=0, sleep_on_quota=99.0),
        key=lambda x: x,
        is_quota_error=lambda e: "quota" in str(e),
        sleep=_spy_sleep,
    )
    # max_retries=0 yet it still succeeded: quota retries did NOT consume retries.
    assert qstats == {"done": 1, "failed": 0, "skipped": 0}, qstats
    assert _slept and all(s == 99.0 for s in _slept), _slept

    print("batch self-check OK")
