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
    planner_max_tokens: int | None = None
    topology_max_tokens: int | None = None
    max_tool_iters: int = 8
    max_actions_per_phase: int = 8
    max_searches: int = 3
    max_successful_fetches: int = 6
    section_window_chars: int = 12_000
    finding_batch_size: int = 8
    allowed_sections_from_profile: bool = True
    #: >0 → after each web_search the tool loop deterministically fetches the top
    #: N result pages and splices their content into the search result. Weak
    #: models search but then cite fabricated URLs instead of fetching — this
    #: guarantees real page text under real URLs reaches the model (and the
    #: fetch cache, so grounding + the evidence dossier can verify citations).
    auto_fetch_top_results: int = 0


DEFAULT_MODEL_PROFILE = ModelProfile(name="default")

GEMMA_4_26B_PROFILE = ModelProfile(
    name="gemma-4-26B-A4B-it-heretic-4bit",
    weak_instruction_following=True,
    context_chars=100_000,
    planner_max_tokens=1024,
    topology_max_tokens=512,
    # Raised from 1/2/4 (2026-07-04): those caps were set while gemma wasted its
    # whole budget fabricating citations (0 tool calls ever) — never data-bound.
    # With the fabricated-citation forcing turn making gemma actually research,
    # match the generic default budget and measure before raising further.
    max_tool_iters=6,
    max_actions_per_phase=3,
    max_searches=3,
    max_successful_fetches=6,
    section_window_chars=6_000,
    finding_batch_size=3,
    # gemma searches but cites fabricated URLs instead of fetching (live runs
    # 1523-1525: 21 searches, 1 fetch, 0 surviving citations) — fetch the top
    # results for it so findings can quote real pages.
    auto_fetch_top_results=2,
)

#: Strong hosted models (Claude haiku/sonnet/opus): 200K-token context with solid
#: long-context attention, fast API turns. The generic default was sized for an
#: unknown backend; a known-strong model can hold far more fetched material per
#: spoke, so give it a deeper research budget instead of the lowest common
#: denominator. Depth scales with sources ONLY when the model actually grounds
#: on them — the tool loop's fabricated-citation forcing turn is the guard.
STRONG_CLAUDE_PROFILE = ModelProfile(
    name="claude-strong",
    context_chars=200_000,
    max_tool_iters=12,
    max_actions_per_phase=10,
    max_searches=10,
    max_successful_fetches=20,
    section_window_chars=20_000,
    finding_batch_size=12,
)

_MODEL_PROFILE_MATCHES: tuple[tuple[str, ModelProfile], ...] = (
    ("gemma-4-26b-a4b-it-heretic-4bit", GEMMA_4_26B_PROFILE),
    ("gemma-4-26b", GEMMA_4_26B_PROFILE),
    # Claude ids ("claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-4-8")
    # all carry the vendor prefix; bare-name needles cover proxy aliases.
    ("claude", STRONG_CLAUDE_PROFILE),
    ("haiku", STRONG_CLAUDE_PROFILE),
    ("sonnet", STRONG_CLAUDE_PROFILE),
    ("opus", STRONG_CLAUDE_PROFILE),
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
