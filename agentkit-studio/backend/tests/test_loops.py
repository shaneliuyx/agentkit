"""Tests for loop-library catalog integration (loops.py) — fully offline.

Uses the bundled fixture (tests/fixtures/catalog_sample.json, extracted from the
REAL catalog.json) so no network is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentkit.planner.core import plan
from studio.loops import CatalogClient, make_seeded_decomposer

_FIXTURE = Path(__file__).parent / "fixtures" / "catalog_sample.json"


@pytest.fixture
def catalog() -> CatalogClient:
    data = json.loads(_FIXTURE.read_text())
    return CatalogClient(data)


def test_fixture_has_real_schema(catalog: CatalogClient) -> None:
    """The fixture mirrors the real per-loop schema (slug/description/useWhen/steps)."""
    loop = catalog.loops[0]
    for key in ("number", "slug", "title", "description", "useWhen", "steps", "keywords"):
        assert key in loop, key
    assert isinstance(loop["steps"], list)


def test_find_returns_relevant_matches(catalog: CatalogClient) -> None:
    """A documentation requirement matches the docs-sweep loop above others."""
    matches = catalog.find("review the codebase and fix stale documentation drift")
    assert matches, "expected at least one match"
    assert matches[0].id == "overnight-docs-sweep"
    # The match maps the real schema to the event field names.
    m = matches[0].to_dict()
    assert set(m) == {"id", "title", "summary", "url", "trigger", "keywords", "score"}
    assert m["summary"]  # ← description
    assert m["trigger"]  # ← useWhen
    assert m["score"] > 0


def test_find_empty_requirement_returns_nothing(catalog: CatalogClient) -> None:
    assert catalog.find("") == []


def test_find_respects_limit(catalog: CatalogClient) -> None:
    matches = catalog.find("loop workflow agent review", limit=2)
    assert len(matches) <= 2


def test_get_by_slug_and_number(catalog: CatalogClient) -> None:
    assert catalog.get("overnight-docs-sweep") is not None
    assert catalog.get("001") is not None
    assert catalog.get("nope") is None


def test_adapt_builds_linear_dag(catalog: CatalogClient) -> None:
    """A loop's flat steps become a linear DAG (sN depends on s(N-1))."""
    loop = catalog.get("overnight-docs-sweep")
    steps = catalog.adapt(loop)
    assert len(steps) == len(loop["steps"])
    assert steps[0]["id"] == "s1" and steps[0]["depends_on"] == []
    assert steps[1]["depends_on"] == ["s1"]
    # category slug becomes the role hint.
    assert steps[0]["role"] == "engineering"


def test_seeded_decomposer_produces_valid_plan(catalog: CatalogClient) -> None:
    """The seeded decomposer feeds planner.plan and yields a valid Plan DAG."""
    loop = catalog.get("overnight-docs-sweep")
    seed = catalog.adapt(loop)
    plan_obj = plan("anything", decomposer=make_seeded_decomposer(seed))
    assert len(plan_obj.steps) == len(seed)
    assert plan_obj.steps[1].depends_on == ("s1",)
    assert plan_obj.steps[0].role == "engineering"


def test_seeded_decomposer_empty_falls_back() -> None:
    """An empty seed yields a single-step plan (never an invalid empty DAG)."""
    plan_obj = plan("do the thing", decomposer=make_seeded_decomposer([]))
    assert len(plan_obj.steps) == 1
    assert plan_obj.steps[0].description == "do the thing"


def test_load_degrades_on_fetch_failure(tmp_path: Path) -> None:
    """A failing fetch with no cache yields an empty catalog, never raises."""

    def boom(_url: str) -> str:
        raise RuntimeError("network down")

    client = CatalogClient.load(
        cache_path=tmp_path / "missing.json", fetcher=boom
    )
    assert client.loops == []
    assert client.find("anything") == []


def test_load_uses_injected_fetcher(tmp_path: Path) -> None:
    """A successful injected fetch is parsed + cached (no real network)."""
    payload = json.dumps({"loops": [{"slug": "x", "title": "X", "description": "d",
                                      "useWhen": "u", "steps": ["a"], "keywords": []}]})
    client = CatalogClient.load(cache_path=tmp_path / "c.json", fetcher=lambda _u: payload)
    assert client.get("x") is not None
    assert (tmp_path / "c.json").exists()  # cached
