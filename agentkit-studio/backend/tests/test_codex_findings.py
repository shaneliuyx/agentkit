"""Regression tests for 6 production-risk findings from an independent Codex
adversarial review (2026-07-02). Each test proves the specific failure mode is closed.

Finding 2 (dynamic.py module-global race) lives in the agentkit lib suite:
../../tests/test_dynamic_topology_concurrency.py — it imports agentkit only.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from fastapi.testclient import TestClient

import studio.app as studio_app
import studio.runner as runner_mod
import studio.tools as tools
from studio.runner import Runner
from studio.session import SessionRegistry
from studio.task_runs import (
    TaskRun,
    TaskRunStore,
    neutralize_unverified_urls,
    task_hash,
)
from studio.templates import TemplateStore


class _FakeEmbedder:
    """Constant vector → cosine 1.0 for any text, so find_template matches any row
    that still HAS an embedding (and matches nothing once the embedding is cleared)."""

    def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


# ---------------------------------------------------------------------------
# Finding 1 — catalog mutation routes had no auth + /replace left stale embedding
# ---------------------------------------------------------------------------

def test_catalog_mutation_requires_admin_key_when_configured(monkeypatch):
    monkeypatch.setenv("STUDIO_ADMIN_KEY", "s3cret")
    client = TestClient(studio_app.app)

    # No header → 403 (never reaches the store, so no mutation).
    assert client.post("/catalog/templates/1/replace", json={"skeleton": "# H\n"}).status_code == 403
    # Wrong key → 403.
    assert client.post(
        "/catalog/templates/1/replace",
        json={"skeleton": "# H\n"},
        headers={"X-Studio-Admin-Key": "wrong"},
    ).status_code == 403
    # Correct key → passes auth (404 because the id is absent, NOT 403).
    r = client.post(
        "/catalog/templates/999999999/replace",
        json={"skeleton": "# H\n_(pending)_\n"},
        headers={"X-Studio-Admin-Key": "s3cret"},
    )
    assert r.status_code != 403


def test_catalog_open_when_no_admin_key(monkeypatch):
    """Unset STUDIO_ADMIN_KEY preserves the keyless localhost-dev default (the GUI
    sends no key), so existing deployments/tests are unaffected."""
    monkeypatch.delenv("STUDIO_ADMIN_KEY", raising=False)
    client = TestClient(studio_app.app)
    assert client.get("/catalog/templates/export").status_code == 200


def test_replace_template_clears_stale_embedding(tmp_path):
    db = tmp_path / "templates.db"
    store = TemplateStore(db_path=db, embedder=_FakeEmbedder())
    store.save_template("build an agent loop framework", "# Old\n_(pending)_\n", name="x")
    tid = store.list_templates()[0]["id"]
    # Before replace: the requirement embedding lets find_template auto-serve the row.
    assert store.find_template("build an agent loop framework") is not None

    # Replace through the route path (a store with NO embedder → can't recompute).
    no_embedder = TemplateStore(db_path=db)
    no_embedder.replace_template(tid, "# Attacker Skeleton\n_(pending)_\n")

    # The stale embedding is cleared, so find_template can't serve the replaced
    # skeleton on an outdated semantic match (persistent cross-task contamination).
    fresh = TemplateStore(db_path=db, embedder=_FakeEmbedder())
    assert fresh.find_template("build an agent loop framework") is None


# ---------------------------------------------------------------------------
# Finding 3 — race-prone version allocation (two runs claim the same version)
# ---------------------------------------------------------------------------

def test_record_versioned_no_duplicate_under_concurrency(tmp_path):
    db = tmp_path / "runs.db"
    TaskRunStore(db_path=db)  # create schema + unique index up front
    th = task_hash("shared task")
    n = 8
    versions: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker() -> None:
        # Each concurrent run has its OWN store/connection to the same file — the
        # real scenario (two auto-improve runs of the same task at once).
        store = TaskRunStore(db_path=db)
        barrier.wait()
        v = store.record_versioned(
            TaskRun(
                task_hash=th, session_id="s", version=0, score=0.5,
                weaknesses=[], artifact_path="", requirement="shared task",
            )
        )
        with lock:
            versions.append(v)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(versions) == n
    assert len(set(versions)) == n            # no duplicate version landed
    assert sorted(versions) == list(range(1, n + 1))
    # latest() is deterministic: the single highest version.
    assert TaskRunStore(db_path=db).latest(th).version == n


# ---------------------------------------------------------------------------
# Finding 4 — process-global fetch cache let one session verify via another's fetches
# ---------------------------------------------------------------------------

def test_fetch_cache_isolated_across_sessions():
    tools._fetch_cache.clear()  # clear the current (test) context's view
    results: dict[str, bool] = {}
    barrier = threading.Barrier(2)

    def session_a() -> None:
        tools._fetch_cache["http://a.example|"] = ("content A", 9)
        barrier.wait()  # both threads now mid-"run" simultaneously
        results["a_sees_own"] = tools._url_in_cache("http://a.example")

    def session_b() -> None:
        barrier.wait()
        # B never fetched anything; it must NOT verify against A's fetch.
        results["b_sees_a"] = tools._url_in_cache("http://a.example")

    ta = threading.Thread(target=session_a)
    tb = threading.Thread(target=session_b)
    ta.start(); tb.start(); ta.join(); tb.join()

    assert results["a_sees_own"] is True    # A grounds its own citation
    assert results["b_sees_a"] is False     # B's citation can't leak-verify via A


# ---------------------------------------------------------------------------
# Finding 5 — last_run published (raw) before postrun neutralization/editing finished
# ---------------------------------------------------------------------------

def test_last_run_published_only_after_postrun(
    fake_client_factory: Callable[..., object], tmp_path, monkeypatch
):
    reg = SessionRegistry()
    session = reg.create(
        llm_spec={"profile": "qwen"}, embed_spec={},
        llm_info={"label": "qwen", "model": "q"}, embed_info={"label": "none", "model": "none"},
        mode="auto", budget_ceiling=None,
    )
    events: list[object] = []
    runner = Runner(
        session, events.append, client_factory=fake_client_factory,
        embedder=None, workspace_root=tmp_path,
    )

    order: list[str] = []
    orig_postrun = Runner._postrun_score_and_record

    def wrapped_postrun(self, **kw):
        order.append("postrun")
        return orig_postrun(self, **kw)

    monkeypatch.setattr(Runner, "_postrun_score_and_record", wrapped_postrun)

    orig_record = session.record_run

    def wrapped_record(snapshot):
        order.append("record")
        return orig_record(snapshot)

    monkeypatch.setattr(session, "record_run", wrapped_record)

    runner.run("1. compare redis and postgres 2. write a recommendation")

    assert "record" in order
    assert order.count("record") == 1                       # premature publish removed
    assert order.index("postrun") < order.index("record")   # publish only after postrun
    # The published snapshot is the final (post-processed) served text.
    assert session.last_run is not None


# ---------------------------------------------------------------------------
# Finding 6 — fail-open URL verification masked fabrication during outages
# ---------------------------------------------------------------------------

def test_web_cache_available_distinguishes_missing(tmp_path, monkeypatch):
    # P0-A: the primary path checks the load-once in-memory cache (how production fills it
    # via web_search/web_fetch), not a mid-process disk write. The direct-read fallback and
    # its whitespace/missing-file nuances are covered by
    # test_runner_web_cache_available_fallback_matches_direct_read.
    from web_toolkit import cache_store

    monkeypatch.setenv("WEB_CACHE", "1")
    monkeypatch.setenv("WEB_CACHE_PATH", str(tmp_path / ".web_cache.json"))
    assert runner_mod._web_cache_available() is False          # nothing cached → couldn't check
    cache_store("k", [{"url": "https://x"}])                   # a cached entry now exists
    assert runner_mod._web_cache_available() is True           # verification could run


def test_neutralize_fail_open_preserved_but_verified_set_still_strips():
    text = "See http://real.example/x for details."
    # None / empty = "couldn't verify" → unchanged, so a transient outage never
    # blanks real citations (deliberate fail-open the finding must not regress).
    assert neutralize_unverified_urls(text, None) == text
    assert neutralize_unverified_urls(text, []) == text
    # A real verified set still strips an unlisted (fabricated) URL.
    out = neutralize_unverified_urls(text, ["http://other.example/"])
    assert "http://real.example" not in out
