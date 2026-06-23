"""agentkit.sandbox — containment for untrusted code/commands (REPLAN §5).

Deterministic-first security seam: ``SubprocessSandbox`` (argv-not-shell,
cwd-jailed, wall-clock timeout, output-capped) is the local default;
``DockerSandbox`` is a named seam for hard isolation. ``net_guard`` is the
default-deny egress allowlist sandboxed code/tools consult before the network.
"""

from agentkit.sandbox.core import (
    DEFAULT_TIMEOUT,
    MAX_OUTPUT_BYTES,
    DockerSandbox,
    ExecResult,
    Sandbox,
    SubprocessSandbox,
    is_within,
)
from agentkit.sandbox.net_guard import (
    EgressBlocked,
    allowed_hosts,
    assert_allowed,
    host_of,
    is_allowed,
)

__all__ = [
    "Sandbox",
    "ExecResult",
    "SubprocessSandbox",
    "DockerSandbox",
    "MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT",
    "is_within",
    "EgressBlocked",
    "allowed_hosts",
    "assert_allowed",
    "host_of",
    "is_allowed",
]
