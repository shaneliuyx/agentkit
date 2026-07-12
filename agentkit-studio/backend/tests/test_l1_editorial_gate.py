"""L1 verify-hardening: editorial gate, verdict router, seed-eligibility status.

Covers the correctness invariant — a could-not-verify check SKIPS (never records a
pass), a crashed/unverified run is seed-excluded (never poisons the lineage), and
the deterministic editorial rows pass-on-good / fail-on-bad.
"""
from __future__ import annotations

from studio.finalize import (
    _editorial_fail_weaknesses,
    _editorial_run_status,
    compute_editorial_rows,
)
from studio.task_runs import TaskRun, _seed_ineligible_reason


def _verdict(rows, name):
    return next(r["verdict"] for r in rows if r["row"] == name)


# --------------------------------------------------------------------------- #
# Verdict router — each outcome maps correctly.
# --------------------------------------------------------------------------- #
def test_router_publish_when_all_pass():
    rows = [{"row": "E1", "verdict": "pass", "required": False}]
    assert _editorial_run_status(rows, compliance_unavailable=False) == "completed"


def test_router_reject_on_e4_fail():
    rows = [{"row": "E4", "verdict": "fail", "required": False}]
    assert _editorial_run_status(rows, compliance_unavailable=False) == "rejected"


def test_router_reject_on_e3_coverage_fail():
    # USER RULING 2026-07-11 (codex HIGH-1): a subject fetched-but-uncited (E3 fail) is
    # a content defect → rejected + seed-excluded, NOT a completed run with a high score.
    rows = [{"row": "E3", "verdict": "fail", "required": True},
            {"row": "E1", "verdict": "pass", "required": False}]
    assert _editorial_run_status(rows, compliance_unavailable=False) == "rejected"


def test_router_unverified_on_compliance_outage():
    # A crashed/could-not-verify compliance check → unverified, NOT completed.
    rows = [{"row": "E1", "verdict": "pass", "required": False}]
    assert _editorial_run_status(rows, compliance_unavailable=True) == "unverified"


def test_router_unverified_on_required_could_not_verify():
    rows = [{"row": "E3", "verdict": "could_not_verify", "required": True}]
    assert _editorial_run_status(rows, compliance_unavailable=False) == "unverified"


def test_router_completed_when_could_not_verify_not_required():
    # A non-required could_not_verify row (e.g. E5 on a doc with no References)
    # must NOT escalate status — the fix is a next-epoch seed, not a false pass.
    rows = [{"row": "E5", "verdict": "could_not_verify", "required": False}]
    assert _editorial_run_status(rows, compliance_unavailable=False) == "completed"


def test_router_completed_on_minor_major_fail():
    # MINOR/MAJOR fails still record `completed` (score kept, fix = next-epoch seed).
    rows = [{"row": "E2", "verdict": "fail", "required": False},
            {"row": "E5", "verdict": "fail", "required": False}]
    assert _editorial_run_status(rows, compliance_unavailable=False) == "completed"


def test_fail_weaknesses_seed_next_epoch():
    rows = [{"row": "E2", "verdict": "fail", "evidence": "stub"},
            {"row": "E1", "verdict": "pass", "evidence": "ok"}]
    ws = _editorial_fail_weaknesses(rows)
    assert ws == ["[editorial:E2] stub"]


# --------------------------------------------------------------------------- #
# Fail-open invariant — a could-not-verify row is NEVER a pass.
# --------------------------------------------------------------------------- #
def test_coverage_missing_is_could_not_verify_never_pass():
    rows = compute_editorial_rows(
        text="## A\n\nbody\n", required_sections=None, coverage=None,
        rebuild_generated=True,
    )
    assert _verdict(rows, "E3") == "could_not_verify"
    assert _verdict(rows, "E3") != "pass"


# --------------------------------------------------------------------------- #
# E2 — stub floor.
# --------------------------------------------------------------------------- #
def test_e2_flags_thin_section_but_not_code_dense():
    doc = ("## Analysis\n\nToo short here.\n\n"
           "## Details\n\n```python\n" + "x = 1\n" * 40 + "```\n")
    rows = compute_editorial_rows(text=doc, required_sections=None, coverage={},
                                  rebuild_generated=False)
    assert _verdict(rows, "E2") == "fail"


def test_e2_passes_substantive_prose():
    body = " ".join(["word"] * 40)
    doc = f"## Analysis\n\n{body}\n"
    rows = compute_editorial_rows(text=doc, required_sections=None, coverage={},
                                  rebuild_generated=False)
    assert _verdict(rows, "E2") == "pass"


# --------------------------------------------------------------------------- #
# E5 — references both directions.
# --------------------------------------------------------------------------- #
def _ref_doc(body_markers: str, ref_entries: str) -> str:
    return f"## Body\n\n{body_markers}\n\n## References\n\n{ref_entries}\n"


def test_e5_pass_when_symmetric():
    doc = _ref_doc("Cited [1] and [2].",
                   "[1] A. https://a.example\n[2] B. https://b.example")
    rows = compute_editorial_rows(text=doc, required_sections=None, coverage={},
                                  rebuild_generated=False)
    assert _verdict(rows, "E5") == "pass"


def test_e5_fail_on_orphan_reference():
    # [3] listed but never cited in body.
    doc = _ref_doc("Cited [1] and [2].",
                   "[1] A. https://a\n[2] B. https://b\n[3] C. https://c")
    rows = compute_editorial_rows(text=doc, required_sections=None, coverage={},
                                  rebuild_generated=False)
    assert _verdict(rows, "E5") == "fail"


def test_e5_fail_on_dangling_marker():
    # [3] cited in body but no reference entry for it.
    doc = _ref_doc("Cited [1] and [2] and [3].",
                   "[1] A. https://a\n[2] B. https://b")
    rows = compute_editorial_rows(text=doc, required_sections=None, coverage={},
                                  rebuild_generated=False)
    assert _verdict(rows, "E5") == "fail"


# --------------------------------------------------------------------------- #
# E7 — dynamic section carries its promised block (deterministic, advisory).
# --------------------------------------------------------------------------- #
def test_e7_fails_code_heading_without_fence():
    doc = "## Code Examples\n\nJust prose describing code, no fence.\n"
    rows = compute_editorial_rows(text=doc, required_sections=None, coverage={},
                                  rebuild_generated=False)
    assert _verdict(rows, "E7") == "fail"


def test_e7_passes_code_heading_with_fence_and_is_advisory():
    doc = "## Code Examples\n\n```python\nx = 1\n```\n"
    rows = compute_editorial_rows(text=doc, required_sections=None, coverage={},
                                  rebuild_generated=False)
    assert _verdict(rows, "E7") == "pass"
    # advisory: E7 is not a required/hard-reject row
    assert next(r for r in rows if r["row"] == "E7")["required"] is False


def test_e7_ignores_prose_heading_without_structural_promise():
    doc = "## Implications\n\nGeneral prose with no structural promise.\n"
    rows = compute_editorial_rows(text=doc, required_sections=None, coverage={},
                                  rebuild_generated=False)
    assert _verdict(rows, "E7") == "pass"   # no code/diagram/comparison keyword → nothing required


# --------------------------------------------------------------------------- #
# E9 — cross-section number consistency (deterministic, advisory).
# --------------------------------------------------------------------------- #
def test_e9_flags_conflicting_counts_across_sections():
    doc = ("## Overview\n\nPi exposes 4 execution modes.\n\n"
           "## Details\n\nCraft offers 3 execution modes here.\n")
    rows = compute_editorial_rows(text=doc, required_sections=None, coverage={},
                                  rebuild_generated=False)
    assert _verdict(rows, "E9") == "fail"


def test_e9_passes_consistent_counts():
    doc = ("## Overview\n\nPi exposes 4 execution modes.\n\n"
           "## Details\n\nIt supports 4 execution modes.\n")
    rows = compute_editorial_rows(text=doc, required_sections=None, coverage={},
                                  rebuild_generated=False)
    assert _verdict(rows, "E9") == "pass"
    assert next(r for r in rows if r["row"] == "E9")["required"] is False   # advisory


# --------------------------------------------------------------------------- #
# E3 — uncited subject fails unless declared not-found (Limitations).
# --------------------------------------------------------------------------- #
def test_e3_fail_uncited_subject_with_sources():
    cov = {"Alpha": {"sources_fetched": 3, "cited_in_artifact": 0}}
    rows = compute_editorial_rows(text="body", required_sections=None, coverage=cov,
                                  rebuild_generated=True)
    assert _verdict(rows, "E3") == "fail"


def test_e3_pass_when_declared_not_found():
    # sources_fetched == 0 → the subject is a declared not-found (Limitations), pass.
    cov = {"Alpha": {"sources_fetched": 0, "cited_in_artifact": 0},
           "Beta": {"sources_fetched": 2, "cited_in_artifact": 1}}
    rows = compute_editorial_rows(text="body", required_sections=None, coverage=cov,
                                  rebuild_generated=True)
    assert _verdict(rows, "E3") == "pass"


# --------------------------------------------------------------------------- #
# Seed eligibility — a crashed/unverified run records a real score but never seeds.
# --------------------------------------------------------------------------- #
def _run(status: str, score: float = 0.5) -> TaskRun:
    return TaskRun(
        task_hash="h", session_id="s", version=1, score=score, weaknesses=[],
        artifact_path="", requirement="r",
        result_text="body with https://example.com citation", status=status,
    )


def test_completed_run_is_seed_eligible():
    assert _seed_ineligible_reason(_run("completed"), median=None) is None


def test_failed_partial_stays_seed_eligible():
    # Salvage carry-forward exemption preserved.
    assert _seed_ineligible_reason(_run("failed_partial"), median=None) is None


def test_unverified_run_is_salvage_eligible_not_zeroed():
    # Judge-down (unverified) recorded a REAL deterministic score (0.5, not 0.0).
    # Like failed_partial it is salvage-eligible as a SEED (latest usable work beats
    # a stale seed) — but the status='completed' filters keep it out of the
    # improvement STATS, so it never poisons the lineage signal.
    run = _run("unverified", score=0.5)
    assert _seed_ineligible_reason(run, median=None) is None  # seed-eligible salvage
    assert run.score == 0.5  # never forced to 0.0


def test_unverified_run_excluded_from_completed_stats():
    from studio.task_runs import TaskRunStore
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        store = TaskRunStore(db_path=Path(d) / "t.db")
        store.record_versioned(_run("completed"))
        store.record_versioned(_run("unverified"))
        th = "h"
        completed = store.completed_runs(th)
        assert [r.status for r in completed] == ["completed"]  # unverified excluded


def test_rejected_run_is_seed_excluded():
    reason = _seed_ineligible_reason(_run("rejected"), median=None)
    assert reason is not None and "rejected" in reason


# --------------------------------------------------------------------------- #
# Reviewer-caught invariant fixes (L1 CHANGES_REQUESTED, 2026-07-10).
# --------------------------------------------------------------------------- #
def test_lint_raise_makes_e6_e11_could_not_verify_not_pass(monkeypatch):
    # HIGH: a raised lint check left lints=[] (indistinguishable from clean) and
    # E6/E11 recorded "pass" — a false pass on a could-not-verify run.
    import studio.artifact_lint as al

    def _boom(_txt):
        raise RuntimeError("lint parser blew up")

    monkeypatch.setattr(al, "lint_artifact", _boom)
    rows = compute_editorial_rows(
        text="## Intro\n\nSome body.\n", required_sections=None,
        coverage=None, rebuild_generated=False,
    )
    assert _verdict(rows, "E6") == "could_not_verify"
    assert _verdict(rows, "E11") == "could_not_verify"


def test_empty_rows_map_to_unverified_not_completed():
    # MEDIUM: a gate that crashed before emitting any row must not read as a fully
    # scored "completed" run (which would be seed-eligible and poison the median).
    assert _editorial_run_status([], compliance_unavailable=False) == "unverified"


def test_e5_ignores_code_indexing_in_fences():
    # MEDIUM: `arr[0]`/`list[2]` inside a code fence are indexing, not citation
    # markers — must not seed phantom orphan/dangling E5 fails.
    text = (
        "## Intro\n\nSee the snippet [1].\n\n"
        "```python\nx = arr[0]\ny = data[2]\n```\n\n"
        "## References\n\n[1] http://x.com\n"
    )
    rows = compute_editorial_rows(
        text=text, required_sections=None, coverage=None, rebuild_generated=True,
    )
    assert _verdict(rows, "E5") == "pass"
