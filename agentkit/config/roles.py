"""agentkit.config.roles — declarative role config (re-plan Phase 1).

P1 (a file defines it, not code): an ``AgentRole`` is loaded from a YAML/JSON
file rather than hard-coded. Generalizes the ``topology/config.py`` round-trip
pattern (config <-> dict <-> file) to roles.

Validation happens at the boundary because file content is untrusted input:
required fields, difficulty label, and tool-list shape are all checked before an
``AgentRole`` is constructed.

JSON works on the standard library alone (keeps the core ``numpy``-only). YAML
is supported when ``pyyaml`` is installed (``pip install agentkit[config]``);
the shipped default roles are JSON so they load with no extra dependency.

ponytail: the default roles are duplicated here (as JSON) and in
``agent/roles.py`` (as code presets); ``tests/test_config.py`` drift-guards them.
Single-source follow-up: have ``agent/roles.py`` load these files directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentkit.agent.roles import AgentRole

# Must match agentkit.agent.router difficulty labels exactly.
_VALID_DIFFICULTY = frozenset({"trivial", "easy", "medium", "hard", "critical"})
_CONFIG_SUFFIXES = (".yaml", ".yml", ".json")
_DEFAULT_ROLES_DIR = Path(__file__).parent / "roles"


def role_to_dict(role: AgentRole) -> dict[str, Any]:
    """``AgentRole`` -> plain dict (tools as a list, for YAML/JSON friendliness)."""
    return {
        "name": role.name,
        "system_prompt": role.system_prompt,
        "tools": list(role.tools),
        "difficulty": role.difficulty,
        "output_schema": role.output_schema,
    }


def role_from_dict(data: dict[str, Any]) -> AgentRole:
    """Validate a config mapping and build an ``AgentRole`` from it."""
    if not isinstance(data, dict):
        raise ValueError(f"role config must be a mapping, got {type(data).__name__}")
    name = data.get("name")
    if not name or not isinstance(name, str):
        raise ValueError("role config requires a non-empty string 'name'")
    system_prompt = data.get("system_prompt")
    if not system_prompt or not isinstance(system_prompt, str):
        raise ValueError(f"role {name!r} requires a non-empty string 'system_prompt'")
    difficulty = data.get("difficulty", "medium")
    if difficulty not in _VALID_DIFFICULTY:
        raise ValueError(
            f"role {name!r} has invalid difficulty {difficulty!r}; "
            f"expected one of {sorted(_VALID_DIFFICULTY)}"
        )
    tools_raw = data.get("tools") or ()
    if not all(isinstance(t, str) for t in tools_raw):
        raise ValueError(f"role {name!r} tools must be a list of strings")
    output_schema = data.get("output_schema")
    if output_schema is not None and not isinstance(output_schema, dict):
        raise ValueError(f"role {name!r} output_schema must be a mapping or null")
    return AgentRole(
        name=name,
        system_prompt=system_prompt,
        tools=tuple(tools_raw),
        difficulty=difficulty,
        output_schema=output_schema,
    )


def _require_yaml(path: Path):
    try:
        import yaml
    except ImportError as e:  # pragma: no cover - dependency-presence branch
        raise ValueError(
            f"{path.name} needs pyyaml — install it with 'pip install agentkit[config]'"
        ) from e
    return yaml


def load_role(path: str | Path) -> AgentRole:
    """Load a single ``AgentRole`` from a ``.yaml`` / ``.yml`` / ``.json`` file."""
    path = Path(path)
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        data = json.loads(text)
    elif suffix in (".yaml", ".yml"):
        data = _require_yaml(path).safe_load(text)
    else:
        raise ValueError(
            f"unsupported config extension {path.suffix!r} ({path.name}); "
            "use .yaml, .yml, or .json"
        )
    return role_from_dict(data)


def dump_role(role: AgentRole, path: str | Path) -> None:
    """Write an ``AgentRole`` to a file; round-trips ``load_role`` for that suffix."""
    path = Path(path)
    data = role_to_dict(role)
    suffix = path.suffix.lower()
    if suffix == ".json":
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    elif suffix in (".yaml", ".yml"):
        yaml = _require_yaml(path)
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    else:
        raise ValueError(
            f"unsupported config extension {path.suffix!r} ({path.name}); "
            "use .yaml, .yml, or .json"
        )


def load_roles(directory: str | Path) -> dict[str, AgentRole]:
    """Load every role file in a directory, keyed by role name.

    Reads ``*.yaml`` / ``*.yml`` / ``*.json``; raises on a duplicate role name.
    """
    directory = Path(directory)
    roles: dict[str, AgentRole] = {}
    paths = sorted(
        p for p in directory.iterdir() if p.suffix.lower() in _CONFIG_SUFFIXES
    )
    for p in paths:
        role = load_role(p)
        if role.name in roles:
            raise ValueError(f"duplicate role name {role.name!r} ({p.name})")
        roles[role.name] = role
    return roles


def load_default_roles() -> dict[str, AgentRole]:
    """Load the shipped default roles (the feynman ensemble) from package files."""
    return load_roles(_DEFAULT_ROLES_DIR)


if __name__ == "__main__":  # pragma: no cover - runnable self-check
    import os
    import tempfile

    roles = load_default_roles()
    assert set(roles) == {"Researcher", "Reviewer", "Writer", "Verifier"}, roles
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.json")
        dump_role(roles["Researcher"], p)
        assert load_role(p) == roles["Researcher"]
    print(f"OK config.roles — {len(roles)} default roles loaded, round-trip clean")
