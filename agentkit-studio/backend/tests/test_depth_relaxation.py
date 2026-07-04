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


# --- CHANGE 3: per-URL depth cap (replaces hard drop) --------------------------

def test_cited_url_capped_not_dropped() -> None:
    from studio.findings import _MAX_FINDINGS_PER_CITED_URL, _cap_findings_by_cited_url
    from studio.task_runs import _normalize_url

    cited_url = "https://cited.com/x"
    fresh_url = "https://fresh.com/y"
    cited = {_normalize_url(cited_url)}

    findings = [Finding(url=cited_url, why=f"distinct angle {i}") for i in range(6)]
    findings.append(Finding(url=fresh_url, why="brand new source"))

    kept = _cap_findings_by_cited_url(findings, cited)

    n_cited = sum(1 for f in kept if _normalize_url(f.url) == _normalize_url(cited_url))
    assert n_cited == _MAX_FINDINGS_PER_CITED_URL == 2  # 2 survive, 4 capped
    assert any(_normalize_url(f.url) == _normalize_url(fresh_url) for f in kept)  # uncited untouched
