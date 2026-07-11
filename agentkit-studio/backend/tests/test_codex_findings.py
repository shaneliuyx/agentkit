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
# Codex HIGH (2026-07-11) — _page_for_url substring match misattributes a page body
# ---------------------------------------------------------------------------

def test_page_for_url_exact_match_never_misattributes_a_superset_url():
    # A cached SUPERSET key (…/topic-extra) inserted BEFORE the exact key must not be
    # returned for a request for …/topic — the old `u in ck` containment returned the
    # first (superset) page, corrupting evidence + topical verdicts.
    tools._fetch_cache.clear()
    tools._fetch_cache["https://example.com/topic-extra|"] = ("EXTRA_BODY", 1)
    tools._fetch_cache["https://example.com/topic|"] = ("TOPIC_BODY", 1)
    assert tools._page_for_url("https://example.com/topic") == "TOPIC_BODY"

    # Substring-only (no exact key present) must now return "" — never a wrong body.
    tools._fetch_cache.clear()
    tools._fetch_cache["https://example.com/topic-extra|"] = ("EXTRA_BODY", 1)
    assert tools._page_for_url("https://example.com/topic") == ""


def test_page_for_url_still_tolerates_trailing_slash_fragment_case_drift():
    # The real near-miss the tolerant match existed for is still honored via
    # normalization (rstrip('/') + fragment strip + lower) — exact after normalize.
    tools._fetch_cache.clear()
    tools._fetch_cache["https://Example.com/Doc/|sel"] = ("DOC", 1)
    assert tools._page_for_url("https://example.com/doc#frag") == "DOC"
    assert tools._page_for_url("https://nope.com/x") == ""


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


# ---------------------------------------------------------------------------
# Topical-relevance floor — grounding is not relevance (run 1531 π-Wikipedia)
# ---------------------------------------------------------------------------

def test_offtopic_finding_dropped_by_source_page_vocabulary():
    """A genuinely-fetched but off-topic source (the π math page) is dropped:
    its PAGE shares ~none of the requirement's content vocabulary. The finding's
    own self-rationalizing prose cannot save it — the page is the judge."""
    from types import SimpleNamespace

    from studio.findings import _drop_offtopic_findings

    tools._fetch_cache.clear()
    req = ("Research how to build a custom agent framework with the pi-ai "
           "package, covering the agent loop, tool calling, and example code")
    tools._fetch_cache["http://good.example|"] = (
        "Building a custom agent framework: the agent loop calls each tool, "
        "the pi-ai package provides completions, example code included.", 9)
    tools._fetch_cache["http://pi.wikipedia|"] = (
        "The number pi is a mathematical constant, the ratio of a circle's "
        "circumference to its diameter, used in geometry and trigonometry.", 9)
    good = SimpleNamespace(url="http://good.example")
    junk = SimpleNamespace(url="http://pi.wikipedia")
    kept, dropped = _drop_offtopic_findings([good, junk], req)
    assert kept == [good]
    assert dropped == 1


def test_offtopic_filter_fails_open_on_missing_signal():
    """No cached page → keep (the grounding oracle owns fabrication). A
    requirement too thin to carry vocabulary signal → no-op entirely."""
    from types import SimpleNamespace

    from studio.findings import _drop_offtopic_findings

    tools._fetch_cache.clear()
    f = SimpleNamespace(url="http://never.fetched")
    req = ("Research how to build a custom agent framework with the pi-ai "
           "package, covering the agent loop, tool calling, and example code")
    kept, dropped = _drop_offtopic_findings([f], req)
    assert kept == [f] and dropped == 0

    tools._fetch_cache["http://pi.wikipedia|"] = ("circle ratio mathematics", 9)
    j = SimpleNamespace(url="http://pi.wikipedia")
    kept, dropped = _drop_offtopic_findings([j], "do it")
    assert kept == [j] and dropped == 0


def test_offtopic_llm_patch_dropped_by_source_page_vocabulary():
    """The aborted 2026-07-05 run showed the π citation riding an LLM PATCH —
    _sanitize_llm_patches applies the same page-vocabulary oracle: a patch whose
    every cited URL is judged off-topic is dropped; an uncached URL keeps it."""
    from types import SimpleNamespace

    from studio.findings import _sanitize_llm_patches

    tools._fetch_cache.clear()
    req = ("Research how to build a custom agent framework with the pi-ai "
           "package, covering the agent loop, tool calling, and example code")
    tools._fetch_cache["https://pi.wikipedia|"] = (
        "The number pi is a mathematical constant, the ratio of a circle's "
        "circumference to its diameter, used in geometry and trigonometry.", 9)
    art = "## Evidence and Analysis\n\nbody\n"

    def _p(url):
        return SimpleNamespace(op="insert_after", anchor="## Evidence and Analysis",
                               content=f"A grounded claim ({url}).")

    kept = _sanitize_llm_patches(art, [_p("https://pi.wikipedia")], req)
    assert kept == []
    kept = _sanitize_llm_patches(art, [_p("https://never.fetched/x")], req)
    assert len(kept) == 1


def test_quote_escape_flood_normalized_at_parse():
    """Live defect (s_5190ea1601a8): a QUOTE with hundreds of literal \\n escape
    sequences was woven verbatim (~4KB of backslash garbage). Escape runs are
    collapsed to whitespace at parse time."""
    from studio.findings import _parse_findings

    tools._fetch_cache.clear()  # empty cache → grounding fail-open, finding kept
    flood = "send" + "\\n" * 300 + "end"
    draft = (f"RESEARCH_FINDING:\nARTICLE_TITLE: T\nURL: https://x.example/a\n"
             f"PATCH_TARGET: ## Evidence and Analysis\nQUOTE: {flood}\nWHY: w\n")
    fs = _parse_findings(draft)
    assert len(fs) == 1
    assert "\\n" not in fs[0].quote
    assert fs[0].quote == "send end"


def test_offtopic_gray_zone_goes_to_llm_judge():
    """Density in [0.010, 0.030] is lexically inseparable (calibrated live:
    dictionary/limitation 0.0136 vs a genuine nav page 0.0124) — the binary
    LLM verdict decides; no judge or judge error keeps the URL (fail-open)."""
    from types import SimpleNamespace

    from studio.findings import _drop_offtopic_findings, _OFFTOPIC_VERDICT_CACHE

    class _Judge:
        def __init__(self, verdict): self.verdict = verdict
        def chat(self, messages, tools=None):
            return SimpleNamespace(text=f"reasoning...\n{self.verdict}")

    tools._fetch_cache.clear()
    _OFFTOPIC_VERDICT_CACHE.clear()
    req = ("Research how to build a custom agent framework with the pi-ai "
           "package, covering the agent loop, tool calling, and example code")
    # 2 requirement-word hits in ~100 tokens → density ~0.02 (gray zone).
    gray_page = ("agent example " + "lorem ipsum dolor amet consectetur "
                 "adipiscing elit sed eiusmod tempor incididunt " * 7)
    tools._fetch_cache["https://gray.example|"] = (gray_page, 9)
    f = SimpleNamespace(url="https://gray.example")

    kept, dropped = _drop_offtopic_findings([f], req, judge=_Judge("IRRELEVANT"))
    assert kept == [] and dropped == 1
    _OFFTOPIC_VERDICT_CACHE.clear()  # Clear cache before testing different verdict.
    kept, dropped = _drop_offtopic_findings([f], req, judge=_Judge("RELEVANT"))
    assert kept == [f] and dropped == 0
    kept, dropped = _drop_offtopic_findings([f], req, judge=None)
    assert kept == [f] and dropped == 0


def test_llm_patch_with_one_junk_url_is_dropped():
    """Live attempt 4 (2026-07-05): a junk citation rode an accepted patch NEXT
    TO a genuine one — the every-URL-junk rule was too lax. ANY positively
    off-topic URL now sinks the patch; an uncached URL still casts no vote."""
    from types import SimpleNamespace

    from studio.findings import _sanitize_llm_patches

    tools._fetch_cache.clear()
    req = ("Research how to build a custom agent framework with the pi-ai "
           "package, covering the agent loop, tool calling, and example code")
    tools._fetch_cache["https://good.example|"] = (
        "Building a custom agent framework: the agent loop calls each tool, "
        "the pi-ai package provides completions, example code included.", 9)
    tools._fetch_cache["https://pi.wikipedia|"] = (
        "The number pi is a mathematical constant, the ratio of a circle's "
        "circumference to its diameter, used in geometry and trigonometry.", 9)
    art = "## Evidence and Analysis\n\nbody\n"
    p = SimpleNamespace(
        op="insert_after", anchor="## Evidence and Analysis",
        content=("Grounded claim (https://good.example). Baseline definition "
                 "(https://pi.wikipedia)."))
    assert _sanitize_llm_patches(art, [p], req) == []
    # Junk URL uncached → unknown → patch survives (fail-open unchanged).
    del tools._fetch_cache["https://pi.wikipedia|"]
    p2 = SimpleNamespace(
        op="insert_after", anchor="## Evidence and Analysis",
        content=("Grounded claim (https://good.example). Unknown source "
                 "(https://never.fetched/x)."))
    assert len(_sanitize_llm_patches(art, [p2], req)) == 1


def test_synthesis_whole_doc_echo_salvages_own_section():
    """Live attempt 4: gemma answered EVERY per-section synthesis window with
    the whole document → all rewrites rejected → depth pass no-op. The echo is
    now sliced back to the window's own section and judged on its merits."""
    from types import SimpleNamespace

    from studio.artifact_text import _synthesize_block

    src = ("## Key Findings\nThe loop calls tools repeatedly. "
           "However, compared to a single prompt, this iterates until done. "
           "See https://x.example/a for details.")
    echo = (
        "## Executive Summary\nIntro prose here.\n\n"
        "## Key Findings\nThe loop calls tools repeatedly. However, compared "
        "to a single prompt, this iterates until the goal is met — which "
        "suggests a trade-off between cost and autonomy. In contrast to "
        "manual prompting, the loop verifies its own work. "
        "See https://x.example/a for details and more analysis of the loop.\n\n"
        "## References\nhttps://x.example/a\n"
    )
    client = SimpleNamespace(chat=lambda messages, tools=None: SimpleNamespace(text=echo))
    new, changed = _synthesize_block(src, client, "study the agent loop", context="")
    assert changed is True
    assert new.startswith("## Key Findings")
    assert "Executive Summary" not in new
    assert "https://x.example/a" in new


def test_offtopic_verdict_cache_avoids_duplicate_judge_calls():
    """Memoization of offtopic verdicts prevents repeated judge.chat() calls on
    the same URL + requirement pair within a run. Same URL + requirement should
    hit the cache on the second call; judge.chat() called exactly once total."""
    from types import SimpleNamespace

    from studio.findings import _drop_offtopic_findings, _OFFTOPIC_VERDICT_CACHE

    class _CountingJudge:
        def __init__(self):
            self.call_count = 0

        def chat(self, messages, tools=None):
            self.call_count += 1
            return SimpleNamespace(text="reasoning...\nIRRELEVANT")

    # Clear the cache at test start to avoid cross-test pollution.
    _OFFTOPIC_VERDICT_CACHE.clear()

    tools._fetch_cache.clear()
    req = ("Research how to build a custom agent framework with the pi-ai "
           "package, covering the agent loop, tool calling, and example code")
    # 2 requirement-word hits in ~100 tokens → density ~0.02 (gray zone).
    gray_page = ("agent example " + "lorem ipsum dolor amet consectetur "
                 "adipiscing elit sed eiusmod tempor incididunt " * 7)
    tools._fetch_cache["https://gray.example|"] = (gray_page, 9)

    judge = _CountingJudge()
    f1 = SimpleNamespace(url="https://gray.example")
    f2 = SimpleNamespace(url="https://gray.example")

    # First call with same URL in first finding → judge.chat() called once.
    kept, dropped = _drop_offtopic_findings([f1], req, judge=judge)
    assert judge.call_count == 1
    assert kept == [] and dropped == 1

    # Second call with same URL in second finding → should hit cache, no new judge.chat().
    kept, dropped = _drop_offtopic_findings([f2], req, judge=judge)
    assert judge.call_count == 1  # Still 1 — cached hit, no new call.
    assert kept == [] and dropped == 1

    # Verify cache now holds the key.
    assert len(_OFFTOPIC_VERDICT_CACHE) == 1


def test_offtopic_judge_failure_is_not_cached_and_is_retried():
    """A judge exception must NOT be memoized (fix: the cache previously stored
    ``None`` for a failed call, which the lookup path returned as-is — inconsistent
    with this same failure's own ``return False`` — and suppressed retrying a
    transient error on a later call with the identical URL+requirement)."""
    from types import SimpleNamespace

    from studio.findings import _drop_offtopic_findings, _OFFTOPIC_VERDICT_CACHE

    class _FlakyJudge:
        def __init__(self):
            self.call_count = 0

        def chat(self, messages, tools=None):
            self.call_count += 1
            raise RuntimeError("transient judge outage")

    _OFFTOPIC_VERDICT_CACHE.clear()
    tools._fetch_cache.clear()
    req = ("Research how to build a custom agent framework with the pi-ai "
           "package, covering the agent loop, tool calling, and example code")
    gray_page = ("agent example " + "lorem ipsum dolor amet consectetur "
                 "adipiscing elit sed eiusmod tempor incididunt " * 7)
    tools._fetch_cache["https://gray.example|"] = (gray_page, 9)
    f = SimpleNamespace(url="https://gray.example")

    judge = _FlakyJudge()
    kept, dropped = _drop_offtopic_findings([f], req, judge=judge)
    assert kept == [f] and dropped == 0  # fail-open: keep on judge error
    assert judge.call_count == 1
    assert len(_OFFTOPIC_VERDICT_CACHE) == 0  # the failure was NOT cached

    # A second call with the identical URL+requirement retries the judge instead
    # of replaying a stale cached failure.
    kept, dropped = _drop_offtopic_findings([f], req, judge=judge)
    assert judge.call_count == 2
    assert kept == [f] and dropped == 0
