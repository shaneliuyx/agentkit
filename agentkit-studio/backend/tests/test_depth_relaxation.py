"""Depth-restoration regression tests (2026-07-04).

An earlier anti-quote-wall fix over-corrected into three depth-starving caps.
These tests pin the relaxed behavior while keeping the quote-wall protection:

  1. reducer contract allows a short 2-4 sentence paragraph (not one sentence);
  2. gathered worker outputs round-trip through TaskRunStore.evidence_json so a
     resumed run can re-feed them to the depth-expansion stage;
  3. an already-cited URL may deepen a section with a couple of NEW angles, but
     is capped (no rebuilding a 26-citation wall from one source).
"""
from __future__ import annotations

from agentkit.artifacts.types import Finding


class _FakeRes:
    text = ""
    total_tokens = 0


class _FakeClient:
    def chat(self, messages):  # noqa: D401 — minimal reducer client stub
        return _FakeRes()


# --- CHANGE 1: per-finding depth (short paragraph, not one sentence) -----------

def test_reducer_contract_allows_short_paragraph() -> None:
    from studio.findings import _make_section_reducer

    reducer = _make_section_reducer(_FakeClient(), "## Findings\nseed body\n", ["weak"])
    reducer(["no findings in this draft"])  # populates _io_capture["prompt"]
    prompt = reducer._io_capture["prompt"]

    assert "2-4 sentence" in prompt
    assert "one short paragraph or sentence" not in prompt
    assert "woven from the finding" not in prompt or "SENTENCE woven" not in prompt


def test_multi_sentence_finding_survives_untruncated() -> None:
    from studio.findings import _findings_to_patches

    f = Finding(
        url="https://example.com/a",
        title="A",
        quote="verbatim source excerpt",
        why="First point about X. Second develops the claim. Third ties it to the source.",
        quote_verified=True,
        patch_target="## Findings",
    )
    patches = _findings_to_patches([f])
    assert len(patches) == 1
    content = patches[0].content
    assert "Second develops the claim" in content
    assert "Third ties it to the source" in content


# --- CHANGE 2: worker-output evidence retention (round-trip) --------------------

def test_worker_outputs_round_trip_through_store(tmp_path) -> None:
    from studio.task_runs import (
        TaskRun,
        TaskRunStore,
        evidence_rows_from_outputs,
        task_hash,
    )

    outputs = {"worker-1": "finding A body", "worker-2": "finding B body"}
    rows = evidence_rows_from_outputs(outputs)
    assert rows  # non-empty

    store = TaskRunStore(db_path=tmp_path / "t.db")
    req = "compare agent frameworks"
    th = task_hash(req)
    store.record(TaskRun(
        task_hash=th, session_id="s1", version=1, score=0.5, weaknesses=[],
        artifact_path="", requirement=req, evidence=rows,
    ))

    latest = store.latest(th)
    assert latest is not None and latest.evidence == rows

    # bounded: an enormous output is trimmed, not stored whole
    big = evidence_rows_from_outputs({"w": "x" * 20000})
    assert len(big[0]["output"]) == 8000


# --- CHANGE 3: per-URL depth floor survives the FULL reduce pipeline -----------

def test_cited_url_yields_one_finding_after_full_reduce_pipeline() -> None:
    """Depth-restoration guard, asserting the ACTUAL reduce sequence — not the helper in
    isolation. An already-cited URL with several distinct-claim findings must survive
    dedupe -> _cap_findings_by_cited_url -> consolidate_findings as EXACTLY ONE finding:

      * not ZERO  — proves the un-zeroing depth win (the old hard-drop deleted every
        already-cited source on a resumed report, leaving it shallow);
      * not a rebuilt wall — consolidate_findings collapses same-URL to one downstream,
        which is exactly why capping the helper at >1 buys nothing. The old isolation
        test asserted ``== 2`` against the helper alone and hid this collapse.
    """
    from agentkit.artifacts.dedup import consolidate_findings, dedupe_findings
    from studio.findings import _cap_findings_by_cited_url
    from studio.task_runs import _normalize_url

    cited_url = "https://cited.com/x"
    fresh_url = "https://fresh.com/y"
    cited = {_normalize_url(cited_url)}

    angles = [
        "Throughput scales with worker concurrency.",
        "Latency is dominated by network round-trips.",
        "Cost grows linearly with token volume.",
    ]
    findings = [
        Finding(url=cited_url, quote=f"verbatim excerpt {i}", why=angles[i])
        for i in range(len(angles))
    ]
    findings.append(Finding(url=fresh_url, quote="fresh excerpt", why="A brand-new uncited source."))

    # replicate studio/findings.py reduce order (~336-347); no embedder -> lexical fallback
    findings, _ = dedupe_findings(findings, None)
    findings = _cap_findings_by_cited_url(findings, cited)
    findings, _ = consolidate_findings(findings, norm_url=_normalize_url)

    n_cited = sum(1 for f in findings if _normalize_url(f.url) == _normalize_url(cited_url))
    n_fresh = sum(1 for f in findings if _normalize_url(f.url) == _normalize_url(fresh_url))
    assert n_cited == 1  # un-zeroed: survives, held to one by consolidate (no wall)
    assert n_fresh == 1  # fresh/uncited source unaffected


# --- CHANGE 2b: persisted worker evidence is reconstructed + re-fed on resume ---

def test_evidence_rows_reconstruct_round_trip() -> None:
    """``outputs_from_evidence_rows`` is the inverse of ``evidence_rows_from_outputs``
    (modulo the 8000-char trim) and ignores non-worker_output rows."""
    from studio.task_runs import evidence_rows_from_outputs, outputs_from_evidence_rows

    d = {"worker-1": "body A", "worker-2": "body B"}
    assert outputs_from_evidence_rows(evidence_rows_from_outputs(d)) == d

    # finding/other rows are filtered out
    mixed = evidence_rows_from_outputs(d) + [{"kind": "finding", "label": "x", "output": "q"}]
    assert outputs_from_evidence_rows(mixed) == d

    # the char trim is the only lossy step
    assert outputs_from_evidence_rows(evidence_rows_from_outputs({"w": "x" * 20000})) == {"w": "x" * 8000}


def test_resume_walks_back_past_evidenceless_partial(tmp_path) -> None:
    """codex P2: the NEWEST run for a task can be a ``failed_partial`` recorded WITHOUT
    worker-output evidence. The resume lookup must walk newest->oldest and pick the newest
    run that actually persisted worker_output rows — not stop at the empty partial and
    hand depth-expansion nothing. Mirrors the runner's reversed(all_runs) walk (runner.py
    ~4174)."""
    from studio.task_runs import (
        TaskRun,
        TaskRunStore,
        evidence_rows_from_outputs,
        outputs_from_evidence_rows,
        task_hash,
    )

    store = TaskRunStore(db_path=tmp_path / "t.db")
    req = "compare agent frameworks"
    th = task_hash(req)
    rows = evidence_rows_from_outputs({"worker-1": "prior grounded body"})
    # older: a completed run that persisted evidence
    store.record(TaskRun(
        task_hash=th, session_id="s1", version=1, score=0.6, weaknesses=[],
        artifact_path="", requirement=req, evidence=rows, status="completed",
    ))
    # newer: a failed_partial recorded WITHOUT evidence — the masking row
    store.record(TaskRun(
        task_hash=th, session_id="s2", version=2, score=0.0, weaknesses=[],
        artifact_path="", requirement=req, evidence=[], status="failed_partial",
    ))

    # naive latest() returns the evidenceless partial -> nothing to re-feed (the bug)
    assert outputs_from_evidence_rows(store.latest(th).evidence) == {}
    # the runner's walk-back recovers the older completed run's outputs
    recovered: dict = {}
    for pr in reversed(store.all_runs(th)):
        recovered = outputs_from_evidence_rows(pr.evidence)
        if recovered:
            break
    assert recovered == {"worker-1": "prior grounded body"}


def test_resume_refeeds_prior_worker_outputs_to_expand() -> None:
    """Simulated resume: the current run produced NO worker outputs, but the prior run
    persisted worker_output evidence rows. The runner's reconstruct+merge (runner.py
    ~4165) must hand ``expand_underdeveloped_sections`` non-empty evidence so depth can
    grow — the defect was passing the current run's empty ``outputs`` and ignoring the
    saved rows. Asserts the reconstructed prior source actually reaches expand's synthesis
    prompt (proves it flowed all the way in, not merely that a dict was non-empty)."""
    from studio.expand_sections import expand_underdeveloped_sections
    from studio.task_runs import evidence_rows_from_outputs, outputs_from_evidence_rows

    current_outputs: dict[str, str] = {}  # silent-worker / restart resume
    prior_rows = evidence_rows_from_outputs({"w1": "PRIOR EVIDENCE BODY https://src.example/a"})

    # replicate runner merge (current-run outputs win)
    prior_outputs = outputs_from_evidence_rows(prior_rows)
    exp_outputs = {**prior_outputs, **current_outputs}
    assert exp_outputs  # non-empty — expand now has evidence to grow from

    captured: dict[str, bool] = {}

    def fake_chat(prompt: str) -> str:
        captured["saw_prior_evidence"] = "PRIOR EVIDENCE BODY" in prompt
        return ""  # no synthesis; we only assert the evidence reached the prompt

    # thin section + a source URL not yet in the body => that source is "under-used"
    expand_underdeveloped_sections(
        text="## Analysis\nShort.\n",
        requirement="analyze the system",
        evidence_outputs=exp_outputs,
        verified_urls=None,
        required_sections=None,
        chat=fake_chat,
        rubric_score=lambda *a, **k: 0.0,
    )
    assert captured.get("saw_prior_evidence") is True
