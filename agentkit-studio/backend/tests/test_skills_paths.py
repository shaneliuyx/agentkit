"""M9: the 5 loop-library paths build + register into a SkillLibrary, offline.

Retrieval is tested at two levels:
  - the WIRING (offline, toy embedder): retrieve() returns k distinct path
    Skills, deterministically — the code we wrote, no model-quality claim;
  - the SEMANTICS (gated on a real embedder, @pytest.mark.integration): an audit
    query surfaces loop-doctor in the top-k — deselected by default via the
    repo's `-m 'not integration'` addopts, so it never runs against the toy.
A 64-dim hash bag-of-words embedder CANNOT rank these paths semantically (SHA256
bucketing erases word meaning + common query tokens collide), so asserting a
semantic outcome with the toy embedder would test the embedder, not this code.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentkit.skills.core import SkillLibrary
from studio.skills_paths import build_path_skills, register_paths

_EXPECTED = {"discover", "find", "loop-doctor", "adapt", "design"}


class _HashEmbedder:
    """Deterministic offline embedder (token-hash bag-of-words) for save/load.

    Mirrors skills/core.py's __main__ _HashEmbedder; used where retrieval ranking
    is not asserted (register/load), so a model download / network is never hit.
    """

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            out.append(vec)
        return out


def test_build_path_skills_has_five_named_paths() -> None:
    skills = build_path_skills()
    assert len(skills) == 5
    assert {s.name for s in skills} == _EXPECTED
    # Each carries a real (non-stub) body and a routing trigger.
    for s in skills:
        assert s.description.strip()
        assert s.trigger.strip()
        assert len(s.body.strip()) > 40, f"{s.name} body looks like a stub"


def test_register_paths_persists_into_library(tmp_path: Path) -> None:
    lib = SkillLibrary(_HashEmbedder(), tmp_path / "PATHS")
    saved = register_paths(lib)
    assert {s.name for s in saved} == _EXPECTED
    # The library lists all five (save wrote <name>.json each).
    assert set(lib.list()) == _EXPECTED
    # And each loads back as a Skill with its body intact.
    loaded = lib.load("loop-doctor")
    assert loaded is not None and "clear_stopping" in loaded.body


def test_retrieve_wiring_returns_k_distinct_path_skills(tmp_path: Path) -> None:
    """WIRING (offline): retrieve(query, k) returns exactly k distinct registered
    path Skills, deterministically — no semantic-quality claim, just the contract
    of the code we wrote (embed trigger+description, rank, slice k)."""
    lib = SkillLibrary(_HashEmbedder(), tmp_path / "PATHS")
    register_paths(lib)

    hits = lib.retrieve("audit and repair an existing loop for weak checks", k=3)
    # Exactly k results, all distinct, all members of the five registered paths.
    assert len(hits) == 3, [h.name for h in hits]
    names = [h.name for h in hits]
    assert len(set(names)) == 3, names
    assert set(names) <= _EXPECTED, names

    # Deterministic: a second identical call yields the same ranking.
    again = [h.name for h in lib.retrieve(
        "audit and repair an existing loop for weak checks", k=3)]
    assert again == names, (names, again)


@pytest.mark.integration
def test_retrieval_semantics_surfaces_loop_doctor() -> None:
    """SEMANTICS (gated): with a REAL embedder (oMLX BGE-M3), an audit-shaped
    query surfaces the loop-doctor path in the top-k. Deselected by default via
    the repo's `-m 'not integration'` addopts, so it never runs against the toy
    hash embedder. Requires the local oMLX embedding service on :8000."""
    import tempfile

    from studio.backends import build_embedder

    embedder, _info = build_embedder({})
    if embedder is None:
        pytest.skip("no embedder available (oMLX :8000 down)")
    # Probe the live service once; skip cleanly if it cannot embed.
    try:
        embedder.embed(["probe"])
    except Exception as exc:  # noqa: BLE001 - a down service is a skip, not a fail
        pytest.skip(f"embedder unavailable: {exc}")

    with tempfile.TemporaryDirectory() as d:
        lib = SkillLibrary(embedder, Path(d) / "PATHS")
        register_paths(lib)
        hits = lib.retrieve("audit and repair an existing loop for weak checks", k=3)
        assert any(h.name == "loop-doctor" for h in hits), [h.name for h in hits]
