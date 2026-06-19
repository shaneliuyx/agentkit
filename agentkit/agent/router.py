"""
agentkit.agent.router - Route a task step to the appropriate backend/model.

RATIONALE (from the Reddit r/AI_Agents community reality check):
  "Don't default to the flagship model for every call; cheaper/older models
   often match at far lower cost."

For LOCAL backends this maps to:
  - CHEAP steps (simple arithmetic, formatting, short lookups):
    -> oMLX small model (fast, zero latency, no rate-limit pressure)
  - HARD steps (multi-step reasoning, reflection, code generation, eval):
    -> oMLX large model OR VibeProxy/Claude (richer reasoning)

Because these backends are RATE-LIMITED rather than per-token-metered, the
cost of routing everything to the large model is throughput exhaustion, not
money. Routing cheap steps locally preserves rate-limit headroom for hard
steps.

Difficulty levels:
  "trivial"  - single lookup, format conversion, yes/no question
  "easy"     - short calculation, simple tool call
  "medium"   - multi-step reasoning, code snippet
  "hard"     - reflection, self-critique, system-prompt mutation
  "critical" - eval gate, accepting/rejecting a self-modification

(Extracted verbatim from self-improving-agent-lab/backends/router.py; it is
already dependency-free except os/env.)
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    """Immutable routing decision returned by route()."""
    backend: str   # "omlx" | "vibeproxy"
    model: str     # exact model name string
    rationale: str # one-line explanation for logging


# ---------------------------------------------------------------------------
# Default model names (overridable via env vars)
# ---------------------------------------------------------------------------
_OMLX_SMALL  = os.getenv("OMLX_SMALL_MODEL",  os.getenv("OMLX_MODEL", "qwen2.5-coder-7b"))
_OMLX_LARGE  = os.getenv("OMLX_LARGE_MODEL",  os.getenv("OMLX_MODEL", "qwen2.5-coder-7b"))
_VIBE_MODEL  = os.getenv("VIBE_MODEL",  "claude-sonnet-4-5-20250929")
_ACTIVE_BACKEND = os.getenv("AGENT_BACKEND", "omlx")


def route(step_difficulty: str) -> RouteDecision:
    """
    Map a step difficulty label to a (backend, model) routing decision.

    Args:
        step_difficulty: One of "trivial", "easy", "medium", "hard", "critical".

    Returns:
        RouteDecision with backend, model, and rationale.

    Examples:
        >>> r = route("trivial")
        >>> r.backend
        'omlx'
        >>> r = route("critical")
        >>> r.backend in ("omlx", "vibeproxy")
        True
    """
    difficulty = step_difficulty.strip().lower()

    # trivial / easy: always route to oMLX small model.
    # These steps don't need heavy reasoning; keeping them local preserves
    # rate-limit headroom on VibeProxy for when it really matters.
    if difficulty in ("trivial", "easy"):
        return RouteDecision(
            backend="omlx",
            model=_OMLX_SMALL,
            rationale=f"step is {difficulty!r} - use local small model to save rate-limit headroom",
        )

    # medium: prefer oMLX large if available; fall back to VibeProxy if
    # the user has configured vibeproxy as their primary backend.
    if difficulty == "medium":
        if _ACTIVE_BACKEND == "vibeproxy":
            return RouteDecision(
                backend="vibeproxy",
                model=_VIBE_MODEL,
                rationale="medium step - user has vibeproxy as primary, use it",
            )
        return RouteDecision(
            backend="omlx",
            model=_OMLX_LARGE,
            rationale="medium step - route to oMLX large model",
        )

    # hard / critical: use the best available model.
    # For reflection, self-critique, and eval gating we want the highest
    # quality output possible. If VibeProxy is configured, use Claude.
    # Otherwise use the oMLX large model.
    if difficulty in ("hard", "critical"):
        if _ACTIVE_BACKEND == "vibeproxy":
            return RouteDecision(
                backend="vibeproxy",
                model=_VIBE_MODEL,
                rationale=f"step is {difficulty!r} - use Claude via VibeProxy for best quality",
            )
        return RouteDecision(
            backend="omlx",
            model=_OMLX_LARGE,
            rationale=f"step is {difficulty!r} - use oMLX large model",
        )

    # Unknown difficulty: default to medium routing with a warning.
    import warnings
    warnings.warn(
        f"Unknown step_difficulty={step_difficulty!r}. Defaulting to 'medium' routing.",
        stacklevel=2,
    )
    return route("medium")


if __name__ == "__main__":
    assert route("trivial").backend == "omlx"
    assert route("easy").model == _OMLX_SMALL
    assert route("medium").backend in ("omlx", "vibeproxy")
    assert route("critical").backend in ("omlx", "vibeproxy")
    # Unknown difficulty falls back to medium routing (with a warning).
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("ignore")
        assert route("???").backend in ("omlx", "vibeproxy")
    print("router self-check OK")
