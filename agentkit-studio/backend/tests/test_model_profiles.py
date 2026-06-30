from studio.model_profiles import DEFAULT_MODEL_PROFILE, resolve_model_profile


def test_gemma_profile_uses_weak_model_limits() -> None:
    profile = resolve_model_profile("models/gemma-4-26B-A4B-it-heretic-4bit")

    assert profile.weak_instruction_following is True
    assert profile.max_tool_iters == 4
    assert profile.max_searches == 1
    assert profile.max_successful_fetches == 2
    assert profile.section_window_chars == 6000


def test_unknown_model_uses_default_profile() -> None:
    assert resolve_model_profile("some-strong-model") == DEFAULT_MODEL_PROFILE
    assert resolve_model_profile(None) == DEFAULT_MODEL_PROFILE
