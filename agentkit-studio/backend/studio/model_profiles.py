"""Model capability profiles for report-generation controls.

Profiles keep weak local models in smaller, more deterministic work units
without changing the default behavior for stronger backends.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    name: str
    weak_instruction_following: bool = False
    context_chars: int = 80_000
    max_tool_iters: int = 8
    max_actions_per_phase: int = 8
    max_searches: int = 3
    max_successful_fetches: int = 6
    section_window_chars: int = 12_000
    finding_batch_size: int = 8
    allowed_sections_from_profile: bool = True


DEFAULT_MODEL_PROFILE = ModelProfile(name="default")

GEMMA_4_26B_PROFILE = ModelProfile(
    name="gemma-4-26B-A4B-it-heretic-4bit",
    weak_instruction_following=True,
    context_chars=12_000,
    max_tool_iters=4,
    max_actions_per_phase=3,
    max_searches=1,
    max_successful_fetches=2,
    section_window_chars=6_000,
    finding_batch_size=3,
)

_MODEL_PROFILE_MATCHES: tuple[tuple[str, ModelProfile], ...] = (
    ("gemma-4-26b-a4b-it-heretic-4bit", GEMMA_4_26B_PROFILE),
    ("gemma-4-26b", GEMMA_4_26B_PROFILE),
)


def resolve_model_profile(model_id: str | None) -> ModelProfile:
    """Return the best known profile for *model_id*.

    Unknown or empty model ids keep the default profile so existing behavior does
    not change unless a known weak model is selected.
    """
    normalized = (model_id or "").strip().lower()
    if not normalized:
        return DEFAULT_MODEL_PROFILE
    for needle, profile in _MODEL_PROFILE_MATCHES:
        if needle in normalized:
            return profile
    return DEFAULT_MODEL_PROFILE
