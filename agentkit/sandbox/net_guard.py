"""agentkit.sandbox.net_guard — a default-deny egress allowlist.

THREAT (specific to a self-improving agent): the agent can propose changes to
its own config. One bad config mutation that rewrites a backend ``base_url``
from ``localhost:8000`` to ``http://attacker.example/v1`` turns every subsequent
turn into an exfiltration channel — it ships the full prompt (and any API key in
the Authorization header) off-box, and never "looks" malicious because it is
just calling its LLM as usual.

DEFENSE: an explicit allowlist of hosts the process is permitted to reach.
Loopback is always allowed (the local oMLX / VibeProxy backends live there);
anything else must be opted in via ``ALLOWED_EGRESS_HOSTS`` (comma-separated).

This is the deterministic, LLM-non-overridable counterpart to the sandbox: the
sandbox contains what code can *do* locally; ``net_guard`` contains where the
process can *reach* over the network. Pure stdlib — no external deps.

Ported from ``self-improving-agents-curriculum/scaffold/agent/net_guard.py``;
the scaffold's settings-bound ``assert_backends_allowed`` is dropped here because
agentkit injects backends rather than reading a global settings object.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse


class EgressBlocked(Exception):
    """Raised when a URL/host is not on the egress allowlist."""


# Loopback is always allowed — the local backends live here. Anything else must
# be opted in explicitly via ALLOWED_EGRESS_HOSTS (comma-separated hostnames).
_DEFAULT_ALLOWED: frozenset[str] = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
)


def allowed_hosts() -> set[str]:
    """The current egress allowlist: loopback plus any ``ALLOWED_EGRESS_HOSTS``."""
    extra = os.getenv("ALLOWED_EGRESS_HOSTS", "")
    extras = {h.strip().lower() for h in extra.split(",") if h.strip()}
    return set(_DEFAULT_ALLOWED) | extras


def host_of(url: str) -> str:
    """Lowercased hostname of ``url``.

    A bare host (no scheme) is returned as-is, so callers may pass either a full
    URL or a hostname.
    """
    parsed = urlparse(url if "://" in url else f"//{url}", scheme="")
    return (parsed.hostname or url).lower()


def is_allowed(url: str) -> bool:
    """True if ``url``'s host is on the egress allowlist."""
    return host_of(url) in allowed_hosts()


def assert_allowed(url: str) -> None:
    """Raise ``EgressBlocked`` if ``url``'s host is not on the allowlist."""
    if not is_allowed(url):
        raise EgressBlocked(
            f"egress blocked: host {host_of(url)!r} (from {url!r}) is not on the "
            f"allowlist {sorted(allowed_hosts())}. If intentional, add it to "
            f"ALLOWED_EGRESS_HOSTS — but a backend URL that left loopback is the "
            f"classic config-mutation exfiltration signature."
        )


if __name__ == "__main__":
    # Loopback allowed; external blocked.
    assert is_allowed("http://localhost:8000/v1")
    assert is_allowed("http://127.0.0.1:8317")
    assert not is_allowed("http://attacker.example/v1")

    # assert_allowed raises only off-allowlist.
    assert_allowed("http://localhost:8000")
    try:
        assert_allowed("https://evil.test/exfil")
        raise AssertionError("expected EgressBlocked")
    except EgressBlocked:
        pass

    # host extraction normalizes case and accepts bare hosts.
    assert host_of("http://Example.COM/path") == "example.com"
    assert host_of("localhost") == "localhost"

    # env extends the allowlist.
    os.environ["ALLOWED_EGRESS_HOSTS"] = "api.openai.com"
    assert is_allowed("https://api.openai.com/v1")
    del os.environ["ALLOWED_EGRESS_HOSTS"]

    print("net_guard self-check OK")
