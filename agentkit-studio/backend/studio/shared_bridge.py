"""studio.shared_bridge — import the agent-prep/shared lab infra via sys.path.

``shared/`` has no packaging (SPEC §9 risk), so we put it on ``sys.path`` once
and re-export the exact symbols Studio uses. Importing from this module instead
of fiddling with ``sys.path`` at every call site keeps the path hack in one
place.

# ponytail: add a pyproject to shared/ only if Studio ships independently.
"""

from __future__ import annotations

import sys

#: Absolute path to the lab's shared infra (SPEC §2 / §9).
SHARED_PATH = "/Users/yuxinliu/code/agent-prep/shared"

if SHARED_PATH not in sys.path:
    sys.path.insert(0, SHARED_PATH)

# Re-export the shared symbols Studio relies on. These imports happen at module
# load, so a broken shared/ surfaces immediately and clearly.
from agent_loop_tools import TokenAccounting, UsageReport  # noqa: E402
from agent_loop_tools.interrupt_state import (  # noqa: E402
    InterruptStateSnapshot,
    get_interrupt_disposition,
    get_interrupt_hint,
)
from llm import (  # noqa: E402
    PROFILES,
    LLMUnavailable,
    make_client,
    resolve,
)

__all__ = [
    "SHARED_PATH",
    "TokenAccounting",
    "UsageReport",
    "InterruptStateSnapshot",
    "get_interrupt_disposition",
    "get_interrupt_hint",
    "PROFILES",
    "LLMUnavailable",
    "make_client",
    "resolve",
]
