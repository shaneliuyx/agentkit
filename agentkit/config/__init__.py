"""agentkit.config — declarative policy files (re-plan Phase 1: roles).

The agent's policy surface lives in files, not code. Phase 1 ships roles; later
phases add tools, routing, and topology under the same loader/round-trip shape.
See ``docs/REPLAN-agentkit.md``.
"""

from agentkit.config.roles import (
    dump_role,
    load_default_roles,
    load_role,
    load_roles,
    role_from_dict,
    role_to_dict,
)

__all__ = [
    "dump_role",
    "load_default_roles",
    "load_role",
    "load_roles",
    "role_from_dict",
    "role_to_dict",
]
