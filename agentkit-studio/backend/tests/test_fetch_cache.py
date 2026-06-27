"""Tests for agentkit.tools.fetch_cache.InFlightRegistry."""

import threading

from agentkit.tools.fetch_cache import InFlightRegistry


def test_basic_fetch():
    reg = InFlightRegistry()
    result = reg.get_or_fetch("key1", lambda: "value1")
    assert result == "value1"


def test_cached_result():
    reg = InFlightRegistry()
    calls = [0]

    def _fetch():
        calls[0] += 1
        return "fetched"

    reg.get_or_fetch("k", _fetch)
    reg.get_or_fetch("k", _fetch)
    assert calls[0] == 1


def test_different_keys_fetched_independently():
    reg = InFlightRegistry()
    r1 = reg.get_or_fetch("a", lambda: 1)
    r2 = reg.get_or_fetch("b", lambda: 2)
    assert r1 == 1
    assert r2 == 2


def test_concurrent_same_key_fetches_once():
    reg = InFlightRegistry()
    call_count = [0]
    barrier = threading.Barrier(5)

    def _fetch():
        call_count[0] += 1
        return "shared"

    results = []

    def _worker():
        barrier.wait()
        results.append(reg.get_or_fetch("same", _fetch))

    threads = [threading.Thread(target=_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert call_count[0] == 1
    assert all(r == "shared" for r in results)


def test_concurrent_different_keys():
    reg = InFlightRegistry()
    results = {}

    def _worker(key: str) -> None:
        results[key] = reg.get_or_fetch(key, lambda k=key: k.upper())

    threads = [threading.Thread(target=_worker, args=(f"key{i}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(10):
        assert results[f"key{i}"] == f"KEY{i}"
