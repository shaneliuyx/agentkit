"""F4a — metric acquisition (offline: HTTP injected, no live calls)."""
from agentkit.artifacts.metrics import Metric, fetch_metrics, source_kind


def test_source_kind():
    assert source_kind("https://arxiv.org/abs/2210.03629") == ("arxiv", "2210.03629")
    assert source_kind("https://arxiv.org/html/2303.11366v1") == ("arxiv", "2303.11366")
    assert source_kind("https://github.com/THUDM/LongWriter") == ("github", "THUDM/LongWriter")
    assert source_kind("https://addyosmani.com/blog/loop") == (None, "")


def test_fetch_metrics_arxiv_batch_and_github():
    calls = []

    def http(method, url, json=None, headers=None):
        calls.append((method, url))
        if "batch" in url:
            return [{"citationCount": 7094}]   # aligned with the single ARXIV id
        return {"stargazers_count": 1234}      # github

    urls = ["https://arxiv.org/abs/2210.03629", "https://github.com/a/b", "https://blog.example/x"]
    out = fetch_metrics(urls, http=http)
    assert out["https://arxiv.org/abs/2210.03629"] == Metric(7094, "citations", "semantic-scholar")
    assert out["https://github.com/a/b"] == Metric(1234, "stars", "github")
    assert out["https://blog.example/x"] is None       # no public metric → not fabricated
    assert sum(1 for c in calls if "batch" in c[1]) == 1   # ONE batch request (rate-limit-safe)


def test_fetch_metrics_cache_first_no_network():
    cache = {"metric:https://arxiv.org/abs/2210.03629": (7094, "citations", "semantic-scholar")}

    def http(*a, **k):
        raise AssertionError("network hit despite cache")

    out = fetch_metrics(["https://arxiv.org/abs/2210.03629"], cache=cache, http=http)
    assert out["https://arxiv.org/abs/2210.03629"] == Metric(7094, "citations", "semantic-scholar")


def test_fetch_metrics_degrades_on_failure():
    def http(*a, **k):
        raise RuntimeError("429 / down")

    out = fetch_metrics(["https://arxiv.org/abs/2210.03629", "https://github.com/a/b"], http=http)
    assert out["https://arxiv.org/abs/2210.03629"] is None
    assert out["https://github.com/a/b"] is None        # never raises — degrades to None
