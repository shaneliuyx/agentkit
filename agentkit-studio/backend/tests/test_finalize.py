"""Tests for the finalize-pipeline wrapper itself (PLAN §2 S2), NOT the 17
production passes — those are exercised end-to-end via tests/test_runner.py, which
runs the real pipeline through Runner._postrun_score_and_record. This file pins the
wrapper's own contract: fail-open-and-continue, and the L2 inert-pass ledger.
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

import studio.finalize as finalize


def _state(runner) -> finalize.FinalizeState:
    """Minimal FinalizeState — only fields the fake passes below actually touch."""
    return finalize.FinalizeState(
        runner=runner,
        session=SimpleNamespace(session_id="s1"),
        outputs={},
        base_client=None,
        judge_client=None,
        use_llm=False,
        base_requirement="req",
        original_requirement="req",
        artifact_copied=False,
        reducer_gaps=[],
        hc_cfg={},
        store=None,
        thash="h",
        effective_ws_root=None,
        art_file=SimpleNamespace(exists=lambda: False),
        art_path="",
        is_last_epoch=False,
        scored_text="text",
        result_output="text",
        verified_urls=[],
        seed_text="",
        outcome="initial-outcome",
    )


def test_a_raising_pass_is_caught_and_the_next_pass_still_runs(monkeypatch):
    calls: list[str] = []

    def _boom(state):
        calls.append("boom")
        raise RuntimeError("pass failure")

    def _after(state):
        calls.append("after")
        return state

    monkeypatch.setattr(finalize, "PASSES", [("boom", _boom), ("after", _after)])

    runner = SimpleNamespace()
    out = finalize.run_passes(_state(runner))

    assert calls == ["boom", "after"]  # the wrapper did not abort on the exception
    assert out.outcome == "initial-outcome"  # untouched — boom never set it


def test_noop_streak_flags_at_three_consecutive_unchanged_epochs(monkeypatch):
    def _noop(state):
        return state  # never changes scored_text/result_output

    monkeypatch.setattr(finalize, "PASSES", [("stagnant", _noop)])
    runner = SimpleNamespace()

    for _ in range(3):
        finalize.run_passes(_state(runner))

    assert runner._finalize_noop_streak["stagnant"] == 3


def test_a_changing_pass_resets_the_streak(monkeypatch):
    def _flip(state):
        state.scored_text = state.scored_text + "!"
        return state

    monkeypatch.setattr(finalize, "PASSES", [("mover", _flip)])
    runner = SimpleNamespace()

    finalize.run_passes(_state(runner))
    finalize.run_passes(_state(runner))

    assert runner._finalize_noop_streak["mover"] == 0


def test_score_failure_gates_the_record_pass_via_mined_flag(monkeypatch):
    """FAULT-PROPAGATION NOTE, pinned answer. Pre-extraction, ``score_result``
    raising aborted the ENTIRE rest of the postrun via the one shared outer try —
    no task_runs.db row, no HillClimbEvent. The per-pass wrapper isolated the
    failure and originally let the record pass run to completion on a default-empty
    weakness list (divergence confirmed empirically 2026-07-05). Resolution:
    ``state.mined`` is set only when score_and_mine_weaknesses COMPLETES, and
    ``score_scorecard_and_record`` returns early (logged, no row, no event) when it
    is False — restoring the pre-extraction abort contract."""
    monkeypatch.setattr(
        "studio.task_runs.score_result",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("scorer down")),
    )

    events: list[object] = []
    runner = SimpleNamespace(
        _emit=events.append,
        _epoch_relevance_penalty=0.0,
        _epoch_relevance_issues=[],
        _epoch_compliance_penalty=0.0,
        _epoch_compliance_issues=[],
        _epoch_relevance_checked=False,
        _epoch=0,
        _embedder=None,
        _last_scorecard_100=None,
    )
    recorded: list[object] = []
    store = SimpleNamespace(record_versioned=lambda run: recorded.append(run) or 1)

    state = _state(runner)
    state.session = SimpleNamespace(session_id="s1", rubric_config={})
    state.store = store

    # The wrapper's real sequence for these two: try score_and_mine_weaknesses,
    # catch+log its exception, continue to the next pass regardless (exactly what
    # run_passes does — replicated here directly per the requested fallback shape).
    with pytest.raises(Exception):
        finalize._pass_score_and_mine_weaknesses(state)
    assert state.mined is False

    # Must NOT raise: the mined gate returns early before touching rubric/store.
    out = finalize._pass_score_scorecard_and_record(state)

    assert out is state
    assert recorded == []  # PINNED: store.record_versioned must not have been called
    assert events == []  # PINNED: no HillClimbEvent emitted


def test_rebuild_generated_skips_content_mutating_passes_only(monkeypatch):
    """PLAN §16: research_first's ASSEMBLE stage already owns dedupe/references/
    repairs, so re-running the old content-mutating passes on its output is the
    double-processing disease one stage later. The mode marker must skip ONLY the
    passes named in _CONTENT_MUTATING_PASSES — everything else (the recording/
    scoring tail) still runs."""
    calls: list[str] = []

    def _record(name):
        def _fn(state):
            calls.append(name)
            return state
        return _fn

    monkeypatch.setattr(finalize, "PASSES", [
        ("editor", _record("editor")),  # content-mutating — must be skipped
        ("normalize_dedupe", _record("normalize_dedupe")),  # content-mutating — skipped
        ("score_and_mine_weaknesses", _record("score_and_mine_weaknesses")),  # tail — must run
    ])

    runner = SimpleNamespace()
    state = dataclasses.replace(_state(runner), rebuild_generated=True)
    finalize.run_passes(state)

    assert calls == ["score_and_mine_weaknesses"]


def test_rebuild_generated_false_runs_every_pass_unchanged(monkeypatch):
    calls: list[str] = []

    def _record(name):
        def _fn(state):
            calls.append(name)
            return state
        return _fn

    monkeypatch.setattr(finalize, "PASSES", [
        ("editor", _record("editor")),
        ("score_and_mine_weaknesses", _record("score_and_mine_weaknesses")),
    ])

    runner = SimpleNamespace()
    finalize.run_passes(_state(runner))  # rebuild_generated defaults to False

    assert calls == ["editor", "score_and_mine_weaknesses"]


def test_materialize_artifact_overwrites_stale_file_when_rebuild_generated(tmp_path):
    """HIGH follow-up (team-lead, seed-chain): if research_first's own
    write-after-return (runner.py) fails, an auto-improve seed-carry-forward's
    stale artifact.md is left on disk with the original ``not exists()`` guard
    never correcting it. On a rebuild_generated run, this pass must overwrite a
    stale file directly with scored_text — never through
    _write_artifact_through_sections, which mutates a finished document (see
    docstring)."""
    art_file = tmp_path / "artifact.md"
    art_file.write_text("# Stale Title\n\nOld seeded content.\n", encoding="utf-8")

    runner = SimpleNamespace()
    state = dataclasses.replace(
        _state(runner),
        rebuild_generated=True,
        art_file=art_file,
        scored_text="# Fresh Title\n\nFresh research_first content.\n",
    )

    finalize._pass_materialize_artifact(state)

    assert art_file.read_text(encoding="utf-8") == "# Fresh Title\n\nFresh research_first content.\n"


def test_materialize_artifact_is_a_noop_when_file_already_matches(tmp_path, monkeypatch):
    """The common case (write-after-return succeeded): no redundant write."""
    from pathlib import Path

    art_file = tmp_path / "artifact.md"
    art_file.write_text("# Fresh Title\n\nFresh research_first content.\n", encoding="utf-8")

    write_calls: list[str] = []
    real_write_text = Path.write_text

    def _spy_write_text(self, *a, **k):
        write_calls.append(str(self))
        return real_write_text(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", _spy_write_text)

    runner = SimpleNamespace()
    state = dataclasses.replace(
        _state(runner),
        rebuild_generated=True,
        art_file=art_file,
        scored_text="# Fresh Title\n\nFresh research_first content.\n",
    )

    finalize._pass_materialize_artifact(state)

    assert write_calls == []  # already matches — no rewrite attempted


def test_artifact_structure_rejects_detached_fence_language_and_heading_inside_fence():
    broken = """# Report

```typescript
console.log("ok")
```
python
def __init__(self):
    pass
## References
```
"""

    assert not finalize._artifact_structure_ok(broken)


def test_artifact_structure_allows_plain_untagged_fence():
    text = """# Report

Flow sketch:

```
discover -> plan -> execute -> verify
```

## References
"""

    assert finalize._artifact_structure_ok(text)


def test_post_gate_finalize_rejects_structurally_invalid_rewrite(monkeypatch):
    original = "# Report\n\n```python\nprint('ok')\n```\n"
    broken = "# Report\n\n```python\nprint('ok')\n```\npython\n## References\n```\n"

    monkeypatch.setattr(finalize, "normalize_artifact", lambda _text: broken)
    monkeypatch.setattr(finalize, "_repair_lints", lambda text, *_args: (text, []))

    runner = SimpleNamespace()
    state = dataclasses.replace(_state(runner), scored_text=original, result_output=original)
    out = finalize._pass_post_gate_finalize(state)

    assert out.scored_text == original
    assert out.result_output == original
