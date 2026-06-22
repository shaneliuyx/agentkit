"""agentkit.codegen — agent-authored, sandbox-validated tools (REPLAN §4 Ph6).

The youtu EDP-46 pipeline behind the security spine:
``query -> schema -> code -> sandbox-validate -> debugger-repair -> gate ->
register``. The injected ``LLMClient`` is the code-proposer and the debugger;
the validate/repair LOOP and gate admission are deterministic. Read-only tools
ACCEPT and auto-register; side-effecting tools ESCALATE and stop for a human.
"""

from agentkit.codegen.core import (
    DEFAULT_MAX_REPAIRS,
    GeneratedTool,
    ToolForge,
    parse_proposal,
    propose_tool,
)

__all__ = [
    "GeneratedTool",
    "ToolForge",
    "propose_tool",
    "parse_proposal",
    "DEFAULT_MAX_REPAIRS",
]
