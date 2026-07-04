from studio.model_profiles import (
    DEFAULT_MODEL_PROFILE,
    STRONG_CLAUDE_PROFILE,
    resolve_model_profile,
)


def test_gemma_profile_uses_weak_model_limits() -> None:
    profile = resolve_model_profile("models/gemma-4-26B-A4B-it-heretic-4bit")

    assert profile.weak_instruction_following is True
    assert profile.context_chars == 100_000
    assert profile.planner_max_tokens == 1024
    assert profile.topology_max_tokens == 512
    assert profile.max_tool_iters == 6
    assert profile.max_searches == 3
    assert profile.max_successful_fetches == 6
    assert profile.section_window_chars == 6000


def test_unknown_model_uses_default_profile() -> None:
    assert resolve_model_profile("some-strong-model") == DEFAULT_MODEL_PROFILE
    assert resolve_model_profile(None) == DEFAULT_MODEL_PROFILE


def test_claude_models_use_strong_profile() -> None:
    """Known-strong hosted models get a deeper research budget than the generic
    default (which is sized for an unknown backend)."""
    for model_id in (
        "claude-haiku-4-5-20251001",
        "claude-sonnet-5",
        "claude-opus-4-8",
        "anthropic/claude-haiku-4-5",   # proxy-prefixed alias
        "haiku",                         # bare profile alias
    ):
        profile = resolve_model_profile(model_id)
        assert profile == STRONG_CLAUDE_PROFILE, model_id

    assert STRONG_CLAUDE_PROFILE.max_searches > DEFAULT_MODEL_PROFILE.max_searches
    assert (
        STRONG_CLAUDE_PROFILE.max_successful_fetches
        > DEFAULT_MODEL_PROFILE.max_successful_fetches
    )
    assert STRONG_CLAUDE_PROFILE.weak_instruction_following is False


def test_gemma_still_wins_over_claude_needles() -> None:
    """Matcher order: a weak-model id never falls through to the strong tier."""
    assert resolve_model_profile("gemma-4-26b").weak_instruction_following is True
