"""Tests for agentkit.config — declarative role config (re-plan Phase 1).

P1: a role is defined by a file, not code. These tests pin the round-trip
(AgentRole <-> dict <-> file) and that the shipped default YAMLs match the
existing code presets in agent/roles.py (drift guard).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentkit.agent.roles import DEFAULT_ROLES, AgentRole
from agentkit.config import (
    dump_role,
    load_default_roles,
    load_role,
    load_roles,
    role_from_dict,
    role_to_dict,
)

try:
    import yaml  # noqa: F401

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def _sample() -> AgentRole:
    return AgentRole(
        name="Tester",
        system_prompt="You test things.",
        tools=("run_tests", "read_file"),
        difficulty="hard",
        output_schema={"type": "object"},
    )


def test_dict_roundtrip():
    r = _sample()
    assert role_from_dict(role_to_dict(r)) == r


def test_json_roundtrip(tmp_path: Path):
    r = _sample()
    p = tmp_path / "tester.json"
    dump_role(r, p)
    assert load_role(p) == r


@pytest.mark.skipif(not HAS_YAML, reason="pyyaml not installed")
def test_yaml_roundtrip(tmp_path: Path):
    r = _sample()
    p = tmp_path / "tester.yaml"
    dump_role(r, p)
    assert load_role(p) == r


def test_load_roles_dir(tmp_path: Path):
    dump_role(_sample(), tmp_path / "tester.json")
    dump_role(AgentRole(name="Other", system_prompt="x"), tmp_path / "other.json")
    roles = load_roles(tmp_path)
    assert set(roles) == {"Tester", "Other"}
    assert roles["Tester"].difficulty == "hard"


def test_default_roles_match_code_presets():
    loaded = load_default_roles()
    by_name = {r.name: r for r in DEFAULT_ROLES}
    assert set(loaded) == set(by_name)
    for name, role in loaded.items():
        assert role.tools == by_name[name].tools
        assert role.difficulty == by_name[name].difficulty
        assert role.system_prompt.strip() == by_name[name].system_prompt.strip()


def test_missing_required_field_raises():
    with pytest.raises(ValueError):
        role_from_dict({"name": "NoPrompt"})


def test_invalid_difficulty_raises():
    with pytest.raises(ValueError):
        role_from_dict({"name": "X", "system_prompt": "y", "difficulty": "nope"})


def test_unknown_extension_raises(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_text("name: X")
    with pytest.raises(ValueError):
        load_role(p)
