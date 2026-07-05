"""PLAN items 1, 5, 6, 8: synthesis guardrail, lineage identity, weakness filter, status."""
from studio.task_runs import _is_non_weakness, base_identity, task_hash
from studio.runner import (
    _epoch_status,
    _repair_doubled_citations,
    _repair_fence_contamination,
    _repair_lints,
    _synthesize_analysis,
)
from studio.rubric import DEFAULT_WEIGHTS, score_breakdown


# --- item 5: continuation reuses the lineage base task_hash -------------------

def test_base_identity_recovers_original_from_continue_blob():
    original = "Find the most popular agent-development articles"
    blob = (
        f"Original task: {original}\n\n"
        "Context from previous run:\n# Report\n…snippet…\n\n"
        "Follow-up: add more sources"
    )
    assert base_identity(blob) == original
    assert task_hash(base_identity(blob)) == task_hash(original)


def test_base_identity_recovers_original_from_chat_history_blob():
    original = "Compare vector databases for RAG"
    blob = f"[USER]: hi\n\n[ASSISTANT]: ok\n\n[CURRENT REQUEST]: {original}"
    assert base_identity(blob) == original


def test_base_identity_passthrough_for_plain_requirement():
    req = "Write a postmortem for the outage"
    assert base_identity(req) == req


# --- item 6: positive statements are not weaknesses ---------------------------

def test_non_weakness_filter_drops_positives():
    assert _is_non_weakness("The report is complete and satisfies all task constraints")
    assert _is_non_weakness("[document] No major weaknesses found")
    assert _is_non_weakness("Output is comprehensive and well-structured")
    assert _is_non_weakness("")


def test_non_weakness_filter_keeps_real_gaps():
    assert not _is_non_weakness("[## Sources] Missing URLs on three articles")
    assert not _is_non_weakness("[## Findings] Truncated mid-sentence")


# --- item 8: status from per-run epoch index, not cumulative version ----------

def test_epoch_status_converges_only_at_per_run_budget():
    # Epoch 1 of a 3-epoch run → NOT converged regardless of cumulative history.
    assert _epoch_status(1, 0.1, 0.02, 3) == "improving"
    assert _epoch_status(3, 0.1, 0.02, 3) == "converged"


def test_epoch_status_plateau_is_per_run():
    # First epoch never plateaus (no prior epoch in this run) even with a negative delta.
    assert _epoch_status(1, -0.5, 0.02, 3) == "improving"
    # Second epoch onward: low delta → plateau.
    assert _epoch_status(2, 0.0, 0.02, 5) == "plateau"
    # single manual pass (epoch_idx 0) never converges or plateaus
    assert _epoch_status(0, 0.1, 0.02, 3) == "improving"


# --- item 1A: synthesis keeps citations or is rejected ------------------------

class _FakeClient:
    def __init__(self, reply: str):
        self._reply = reply

    def chat(self, messages):
        return type("R", (), {"text": self._reply})()


def test_synthesis_accepts_when_citations_preserved():
    draft = "Finding one. https://a.com/1\n\nFinding two. https://b.com/2\n" * 30
    better = draft + "\n\nIn contrast, source one is more mature than source two; this implies a trade-off."
    out, changed = _synthesize_analysis(draft, _FakeClient(better), "task")
    assert changed and out == better


def test_synthesis_rejects_when_a_url_is_dropped():
    draft = "Finding one. https://a.com/1\n\nFinding two. https://b.com/2\n" * 30
    lost = draft.replace("https://b.com/2", "")  # drops a citation
    out, changed = _synthesize_analysis(draft, _FakeClient(lost), "task")
    assert not changed and out == draft


def test_synthesis_rejects_when_a_url_is_added():
    draft = "Finding one. https://a.com/1\n\nFinding two. https://b.com/2\n" * 30
    invented = draft + "\nExtra unsupported source https://invented.example/new"
    out, changed = _synthesize_analysis(draft, _FakeClient(invented), "task")
    assert not changed and out == draft


def test_synthesis_rejects_when_materially_shorter():
    draft = "Finding. https://a.com/1\n" * 50
    short = "Finding. https://a.com/1"
    out, changed = _synthesize_analysis(draft, _FakeClient(short), "task")
    assert not changed and out == draft


def test_synthesis_rejects_when_a_fence_is_dropped():
    """§14 slate bundle A: the URL/overlap/ratio guards never checked fenced
    blocks — a rewrite dropped a code fence while every citation survived
    (live: "finalize[synthesize_readability]: changed=True fences 2->0")."""
    draft = (
        "Finding one. https://a.com/1\n\n```python\nprint(1)\n```\n\n"
        "Finding two. https://b.com/2\n"
    ) * 10
    dropped_fence = draft.replace("```python\nprint(1)\n```\n", "")
    out, changed = _synthesize_analysis(draft, _FakeClient(dropped_fence), "task")
    assert not changed and out == draft


def test_synthesis_accepts_when_fence_count_preserved():
    """No over-triggering: a rewrite that keeps every fenced block (even if the
    content inside changes) is unaffected by the new guard."""
    draft = (
        "Finding one. https://a.com/1\n\n```python\nprint(1)\n```\n\n"
        "Finding two. https://b.com/2\n"
    ) * 10
    better = draft + "\n\nIn contrast, this implies a further trade-off worth noting overall."
    out, changed = _synthesize_analysis(draft, _FakeClient(better), "task")
    assert changed and out == better


# --- §14.6 root cause: block-level mermaid repair + deterministic splice ------
# Whole-doc repair truncates a large artifact, so _repair_lints sends ONLY the broken
# ```mermaid block to the model and splices the corrected block back; the surrounding
# document (prose, citations) is byte-for-byte untouched by the splice.

_BLOCK = "```mermaid\ngraph TD\n    A -->|Search| B\n    A|Read| C\n```"
_FIXED_BLOCK = _BLOCK.replace("A|Read|", "A -->|Read|")
_BROKEN = f"## Arch\n{_BLOCK}\nBody prose with a citation https://real.com/x.\n"
_FIXED = f"## Arch\n{_FIXED_BLOCK}\nBody prose with a citation https://real.com/x.\n"


def test_repair_lints_noop_on_clean_doc():
    # No lint findings → client never consulted, no change.
    out, changed = _repair_lints(_FIXED, _FakeClient("SHOULD NOT BE USED"), "task")
    assert not changed and out == _FIXED


def test_repair_lints_splices_corrected_block_and_preserves_rest():
    # Model returns ONLY the corrected block; splice leaves all surrounding text intact.
    out, changed = _repair_lints(_BROKEN, _FakeClient(_FIXED_BLOCK), "task")
    assert changed and out == _FIXED
    assert "https://real.com/x" in out  # prose/citation untouched by the splice


def test_repair_lints_rejects_when_block_still_broken():
    # Model echoes the still-glued block → not accepted, original kept.
    out, changed = _repair_lints(_BROKEN, _FakeClient(_BLOCK), "task")
    assert not changed and out == _BROKEN


def test_repair_lints_rejects_when_reply_has_no_block():
    # Model returns prose, not a mermaid block → keep original.
    out, changed = _repair_lints(_BROKEN, _FakeClient("Sorry, I cannot."), "task")
    assert not changed and out == _BROKEN


def test_repair_lints_logs_residual_lint_names_when_nothing_could_be_fixed(tmp_path, monkeypatch):
    """PLAN §14 attempt-9 #3: run v5 logged lints_before=6 changed=False with no
    way to tell WHICH six. When nothing could be fixed, the lint list itself must
    reach the debug log, not just a count."""
    log = tmp_path / "debug.log"
    monkeypatch.setenv("OMC_THROUGHPUT_DEBUG", str(log))
    # A table lint that neither deterministic sub-repair nor the mermaid repair
    # can touch → changed=False with a real, nameable lint remaining.
    text = "## X\n\n| a | b |\n| -- | --- |\n| 1 | 2 |\n"
    out, changed = _repair_lints(text, _FakeClient("Sorry, I cannot."), "task")
    assert not changed and out == text
    logged = log.read_text()
    assert "repair_lints: residual" in logged
    assert "Malformed markdown table separator" in logged


def test_repair_lints_logs_residual_lints_even_when_something_was_fixed(tmp_path, monkeypatch):
    """Review follow-up: logging must be UNCONDITIONAL on changed — a repair that
    fixes ONE defect (the glued fence) but leaves ANOTHER (the malformed table)
    still ships changed=True, and that residual lint needs the same visibility."""
    log = tmp_path / "debug.log"
    monkeypatch.setenv("OMC_THROUGHPUT_DEBUG", str(log))
    text = (
        "```python\nprint(1)\n``` https://example.com/report\n\n"
        "| a | b |\n| -- | --- |\n| 1 | 2 |\n"
    )
    out, changed = _repair_lints(text, _FakeClient("Sorry, I cannot."), "task")
    assert changed  # the fence glue WAS fixed
    assert "``` https://example.com/report" not in out
    logged = log.read_text()
    assert "repair_lints: residual" in logged
    assert "Malformed markdown table separator" in logged


# --- deterministic format repairs: fence-line glue + doubled citation ---------
# Run-1537 artifact.md line 93 (closing fence immediately followed by a citation
# URL) and line 105 (`[title](url) url` duplicate) — both purely mechanical text
# fixes, so _repair_lints must apply them even with client=None.

_FENCE_GLUED = (
    "```python\nprint(1)\n``` "
    "https://nader.substack.com/p/how-to-build-a-custom-agent-framework\n\n"
    "Next paragraph.\n"
)
_DOUBLED_CITATION = (
    "Traces help ([GitHub - x](https://github.com/earendil-works/pi) "
    "https://github.com/earendil-works/pi)."
)


def test_repair_lints_fixes_fence_line_glue_without_a_client():
    out, changed = _repair_lints(_FENCE_GLUED, None, "task")
    assert changed
    assert "``` https://nader" not in out
    assert "https://nader.substack.com/p/how-to-build-a-custom-agent-framework" in out


def test_repair_lints_fixes_doubled_citation_without_a_client():
    out, changed = _repair_lints(_DOUBLED_CITATION, None, "task")
    assert changed
    assert out == "Traces help ([GitHub - x](https://github.com/earendil-works/pi))."


def test_repair_lints_format_fixes_are_idempotent():
    once, _ = _repair_lints(_FENCE_GLUED + _DOUBLED_CITATION, None, "task")
    twice, changed_again = _repair_lints(once, None, "task")
    assert twice == once
    assert not changed_again


def test_repair_lints_leaves_legal_python_opener_untouched():
    clean = "```python\nprint(1)\n```\n"
    out, changed = _repair_lints(clean, None, "task")
    assert not changed and out == clean


def test_repair_lints_leaves_adjacent_different_url_untouched():
    text = "See [a](https://a.com/1) https://b.com/2 for context."
    out, changed = _repair_lints(text, None, "task")
    assert not changed and out == text


def test_fence_split_preserves_every_citation_url():
    from studio.textutil import norm_urls
    before = norm_urls(_FENCE_GLUED)
    out, _ = _repair_lints(_FENCE_GLUED, None, "task")
    assert before <= norm_urls(out)


# --- §14 slate item 5 (user-escalated): per-step writeback normalize path -----
# The deterministic fence/citation repairs used to run only at finalize, so a
# glued fence broke markdown for the REST OF THE RUN (every later step, and the
# GUI mid-run, inherited it). studio/runner.py's per-step normalize block (the
# "normalize step=" _dbg line) now applies BOTH repairs, last, after
# normalize_artifact + strip_satisfied_placeholders — this mirrors that exact
# composition to pin it without driving a full multi-step Runner.run().

def _normalize_then_repair(text: str) -> str:
    from studio.artifact_text import normalize_artifact, strip_satisfied_placeholders
    out = strip_satisfied_placeholders(normalize_artifact(text))
    out, _ = _repair_fence_contamination(out)
    out, _ = _repair_doubled_citations(out)
    return out


def test_normalize_path_repairs_a_glued_fence():
    text = "# Report\n\n## Body\n\n```python\nprint(1)\n``` https://example.com/report\n"
    out = _normalize_then_repair(text)
    assert "``` https://example.com/report" not in out
    assert "https://example.com/report" in out  # citation kept, just moved off the fence


def test_normalize_path_repair_is_idempotent():
    text = "# Report\n\n## Body\n\n```python\nprint(1)\n``` https://example.com/report\n"
    once = _normalize_then_repair(text)
    twice = _normalize_then_repair(once)
    assert twice == once


# --- item 1B: analysis criterion exists and quote density is down-weighted ----

def test_analysis_criterion_present_and_weights_normalized():
    assert "analysis" in DEFAULT_WEIGHTS
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
    # evidence_depth (quote density) down-weighted below analysis (PLAN 1B intent)
    assert DEFAULT_WEIGHTS["evidence_depth"] < DEFAULT_WEIGHTS["analysis"]


def test_analysis_subscore_rewards_synthesis_language():
    quotes_only = '"a" "b" "c" "d" "e" "f"'
    analysis = (
        "However the data suggests X; in contrast Y. This implies a trade-off, and "
        "compared to Z it is more reliable. Therefore the key implication differs."
    )
    assert score_breakdown(analysis)["analysis"] > score_breakdown(quotes_only)["analysis"]
