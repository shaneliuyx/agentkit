"""Tests for the PROFILES menu → backend resolution (backends.py)."""

from __future__ import annotations

import pytest

from studio.backends import (
    list_embedders,
    list_profiles,
    resolve_backend,
)


def test_list_profiles_mirrors_shared_profiles() -> None:
    profiles = list_profiles()
    names = {p["name"] for p in profiles}
    # The shared menu: haiku/opus (cloud), 14b/qwen (local).
    assert {"haiku", "opus", "14b", "qwen"} <= names
    by_name = {p["name"]: p for p in profiles}
    assert by_name["haiku"]["kind"] == "cloud"
    assert by_name["qwen"]["kind"] == "local"
    assert by_name["qwen"]["model"]


def test_resolve_profile() -> None:
    b = resolve_backend({"profile": "qwen"})
    assert b.label == "qwen"
    assert b.base_url.endswith("/v1")
    assert b.model


def test_resolve_unknown_profile_raises() -> None:
    with pytest.raises(ValueError):
        resolve_backend({"profile": "nope"})


def test_resolve_raw_override() -> None:
    b = resolve_backend({"raw": {"model": "my-model", "base_url": "http://x/v1", "api_key": "k"}})
    assert b.model == "my-model"
    assert b.base_url == "http://x/v1"
    assert b.kind == "raw"


def test_resolve_raw_requires_model() -> None:
    with pytest.raises(ValueError):
        resolve_backend({"raw": {"base_url": "http://x/v1"}})


def test_list_embedders_shape() -> None:
    embs = list_embedders()
    assert embs, "embedder menu must be non-empty"
    assert embs[0]["model"]
