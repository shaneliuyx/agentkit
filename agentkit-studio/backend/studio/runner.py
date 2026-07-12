"""studio.runner — the step-loop driver that emits the SSE sequence (SPEC §5.2).

``agentkit.topology.dynamic.run_plan`` is synchronous and emits nothing mid-run,
so Studio drives the phase loop itself and delegates each step to ``run_plan``
on a SINGLE-STEP sub-plan, folding upstream outputs into the description exactly
as agentkit's own ``_with_upstream`` does. The real STAR/MESH/PIPELINE fan-out
happens inside that per-step ``run_plan`` call; Studio adds observability around
it.

Event ordering (SPEC §4):
  session → plan → topology → graph → (per phase: phase_start, [router],
  [memory], [token…], [agent_event], phase_done, [dag], [selfimprove],
  [evolve], [gate]) → budget → verify → done

Token frames fire *during* a phase via the ``on_usage`` callback closed over the
current step (StudioChatClient calls it per LLM call). The runner runs in a
worker thread; events cross to the SSE generator through a queue (app.py owns the
asyncio bridge). Here the runner just calls an injected ``emit(event)`` sink.
"""

from __future__ import annotations

import json as _json
import os
import re as _re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from agentkit.orchestrator.fanout import FanoutBudget
# Module-level so EVERY method sees it: the phase-loop extraction left this as a
# local import in the parent method, and _run_phase_loop's gap-ledger clause hit
# NameError on TaskRecord (silently, behind fail-open guards) — same disease as
# the dropped judge_client parameter.
from agentkit.orchestrator.ledger import TaskLedger, TaskRecord
from agentkit.planner.core import plan
from agentkit.topology.core import (
    DURABLE_BOARD,
    GATEWAY,
    SINGLE,
    STAR,
    TREE,
)
from agentkit.types import LLMClient

from studio.backends import build_chat_client, resolve_backend
from studio.events import (
    BudgetEvent,
    DoneEvent,
    ErrorEvent,
    GateEvent,
    LoopSeedEvent,
    MetricsEvent,
    PhaseStartEvent,
    PlanEvent,
    SessionEvent,
    StudioEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    TopologyEvent,
)
from studio.loops import make_seeded_decomposer
from studio.panels.dag import DagTracker
from studio.panels.loopdoctor import build_loopdoctor_event
from studio.panels.memory import MemoryTracker
from studio.panels.security import run_gate_event
from studio.panels.selfimprove import SelfImproveTracker
from studio.panels.verify import build_verify_event
from studio.client import MaxTokensClient
from studio.model_profiles import resolve_model_profile
from studio.session import RunSnapshot, Session
from studio.section_workspace import (
    active_outline_titles,  # noqa: F401  (transitive re-export: studio.legacy_loop late-imports this from studio.runner)
    write_section_workspace,
)
from studio.shared_bridge import TokenAccounting, UsageReport
from studio.tools import ToolAugmentedClient, web_toolkit_available
from studio.workspace import Workspace, workspace_root


def _verified_urls_from_cache(text: str) -> list[str]:
    """Return URLs in ``text`` that are present in the local web cache.

    Primary path (P0-A): flush the load-once cache to close its debounce freshness
    window, then verify against ``cache_snapshot()`` instead of re-parsing the whole
    13.6MB file. Fail-open is preserved: if the cache API is unavailable we fall back
    to the ORIGINAL direct ``.web_cache.json`` parse, so "cache module missing" still
    means "verification fails open", never "no verified URLs".
    """
    try:
        from studio.task_runs import verified_urls_in_cache

        try:
            from web_toolkit import cache_flush, cache_snapshot
        except Exception:  # noqa: BLE001 - cache API unavailable → direct-parse fallback
            cache_path = Path(".web_cache.json")
            if not cache_path.exists():
                return []
            return verified_urls_in_cache(_json.loads(cache_path.read_text()), text or "")

        cache_flush()
        return verified_urls_in_cache(cache_snapshot(), text or "")
    except Exception:  # noqa: BLE001 - verification is best-effort; scoring fails open.
        return []


def _web_cache_available() -> bool:
    """True when URL verification COULD run (the web cache is present and readable).

    Finding 6: an empty verified set is ambiguous — it means either "verification ran,
    no sources were used" (legitimate) or "verification couldn't run" (cache missing
    after an outage / misconfig). In the second case fabricated URLs pass through the
    fail-open neutralizer silently. This lets the publish path emit a distinguishable
    warning for the "couldn't check" case instead of treating it as clean.

    P0-A: coarse "is there ANY cache" check → no flush needed (not freshness-sensitive).
    Falls back to the original direct read if the cache API is unavailable (fail-open)."""
    try:
        try:
            from web_toolkit import cache_snapshot
        except Exception:  # noqa: BLE001 - cache API unavailable → direct-read fallback
            cache_path = Path(".web_cache.json")
            return cache_path.exists() and bool(cache_path.read_text().strip())
        return bool(cache_snapshot())
    except Exception:  # noqa: BLE001
        return False


_PUB_REV_URL_RE = _re.compile(r"https?://[^\s)>\]\"'`]+")


def _publish_revision_regressed(
    pre_text: str,
    rev_text: str,
    *,
    required_sections: list[str] | tuple[str, ...] | None,
    verified_pre: list[str] | None,
    verified_rev: list[str] | None,
) -> list[str]:
    """Guard the publish-revision accept point (PLAN §9 step 1). A publish
    revision may only FIX cited defects — never shrink or de-cite. Returns the
    list of regression reasons (empty == accept-ok). The measured failure
    (session s_0a3669434b76): a gemma whole-doc rewrite condensed 21.4KB→9.5KB
    and dropped URL density, yet passed the publish-readiness checks and was
    accepted wholesale. These guards reject that class DIRECTLY, independent of
    whether the rewrite happens to be publish-clean.

    Fail-CLOSED: any error returns a single ``gate-error`` reason so the caller
    NO-OPs (keeps the pre-revision text). Fail-OPEN would accept an unvetted
    rewrite — wrong outside the epoch gate (PLAN §9 risk 4)."""
    try:
        from studio.rubric import rubric_score  # noqa: PLC0415

        pre, rev = pre_text or "", rev_text or ""
        reasons: list[str] = []
        if len(rev.split()) < 0.9 * len(pre.split()):
            reasons.append("shrink-words")
        if len(rev.encode("utf-8")) < 0.9 * len(pre.encode("utf-8")):
            reasons.append("shrink-bytes")
        # S3: deliberately NOT migrated to guards.urls_preserved. This is a COUNT
        # comparison over a NARROW regex; unifying onto the broad norm_urls set would
        # both widen capture and switch count→set — flipping the verdict on a URL-SWAP
        # revision (drop A, add B: equal count today, lost-A under sets). Kept as-is to
        # preserve behavior; it is one signal in a multi-reason regression gate.
        pre_urls = {u.rstrip(".,;:)") for u in _PUB_REV_URL_RE.findall(pre)}
        rev_urls = {u.rstrip(".,;:)") for u in _PUB_REV_URL_RE.findall(rev)}
        if len(rev_urls) < len(pre_urls):
            reasons.append("de-cite-urls")
        if len(verified_rev or []) < len(verified_pre or []):
            reasons.append("de-cite-verified")
        rev_score = rubric_score(rev, verified_urls=verified_rev or None, required_sections=required_sections)
        pre_score = rubric_score(pre, verified_urls=verified_pre or None, required_sections=required_sections)
        if rev_score < pre_score - 1e-3:
            reasons.append("score-regress")
        return reasons
    except Exception:  # noqa: BLE001 — gate error → NO-OP (keep pre-revision), never fail-open-accept (§9 risk 4)
        return ["gate-error"]


def _prune_resolved_weaknesses(weaknesses: list[str], text: str) -> list[str]:
    """Drop stale deterministic weaknesses that the final artifact no longer has."""
    if not weaknesses:
        return weaknesses
    try:
        from studio.artifact_lint import lint_artifact
        from studio.task_runs import refute_false_weaknesses

        current_lints = set(lint_artifact(text or ""))
        lint_markers = (
            "Duplicate section heading",
            "Placeholder text remains",
            "Malformed or explicitly unverified markdown link",
            "Markdown link has empty or unverified target",
            "Citation marked (unverified) remains visible",
            "Citation wall",
            "Code fragment appears outside",
            "Code-looking line appears outside",
            "Long evidence-bearing section has no citation URL",
            "Malformed markdown table separator",
            "Empty comparison table",
            "Mermaid diagram has no nearby explanatory prose",
            "Python code block has syntax error",
            "References section is followed by additional report content",
            "Malformed mermaid edge",
            "Unbalanced code fence",
        )
        pruned = [
            w for w in weaknesses
            if w in current_lints or not any(marker in w for marker in lint_markers)
        ]
        return refute_false_weaknesses(pruned, text or "")
    except Exception:  # noqa: BLE001 — cleanup must not break recording
        return weaknesses


# ---------------------------------------------------------------------------
# Re-exports — stateless helpers extracted into focused modules (SRP).
# These names remain importable from ``studio.runner`` for callers/tests that do
# ``from studio.runner import X``. The runner body uses them unchanged.
# ---------------------------------------------------------------------------
from studio.prompts import (  # noqa: E402,F401
    _build_executor_prompt,
    _build_planner_cot_prompt,
    _build_reducer_refine_prompt,
    _today_note,
)
from studio.findings import (  # noqa: E402,F401
    _apply_ranking,
    _findings_to_patches,
    _make_section_reducer,
    _parse_findings,
    _parse_patches_from_output,
    _prefetch_cited,
    _research_findings_to_patches,
    _weakness_score,
)
from studio.planning import (  # noqa: E402,F401
    EpochResult,
    _dedupe_assignment,
    _dedupe_plan_steps,
    _epoch_status,
    _expand_topology,
    _parse_assigned,
    _parse_epic_plan,
    _phase_search_failed,
    _plan_from_epics,
    _render_graph,
    _with_upstream,
    build_section_assignment_rows,
    select_topologies_by_llm,
    verify_assignment_coverage,
)
from studio.artifact_text import (  # noqa: E402,F401
    _detect_gaps,
    _ends_cleanly,
    _gap_sections,
    _merge_missing_sections,
    _repair_doubled_citations,
    _repair_fence_contamination,
    _repair_lints,
    _refine_readability,
    _strip_preamble,
    _synthesize_analysis,
    _unresolved_block,
    add_missing_section_citations,
    dedupe_sections,
    merge_duplicate_sections,
    normalize_artifact,
    reconcile_outline,
    resolve_report_title,
    strip_satisfied_placeholders,
)


#: Emit sink: the runner calls this for every event; app.py wires it to a queue.
Emit = Callable[[StudioEvent], None]


def _section_title(section: str) -> str:
    return _re.sub(r"^#{1,6}\s*", "", str(section or "")).strip()


def _active_template(session: Session) -> list[str]:
    rc = getattr(session, "rubric_config", None) or {}
    return list(rc.get("active_template") or rc.get("template") or [])


def _scoring_template(session: Session) -> list[str]:
    rc = getattr(session, "rubric_config", None) or {}
    return list(rc.get("scoring_template") or rc.get("template") or [])


def _prompt_scoring_matrix(session: Session) -> list[dict[str, object]]:
    rc = getattr(session, "rubric_config", None) or {}
    matrix = rc.get("remaining_scoring_matrix")
    if matrix is None:
        matrix = rc.get("scoring_matrix")
    return list(matrix or [])


def _pick_seed_with_content(sims, ws_root, min_chars: int = 500, recency_fn=None):
    """From similarity-ranked prior runs, pick a prior that actually has artifact content.

    Skips empty/placeholder runs (high embedding similarity but 0-score, empty text) —
    seeding garbage is worse than cold-starting. When MULTIPLE priors clear the content
    bar, choose the LATEST (``recency_fn(session_id)`` highest, e.g. max DB row id) so the
    most recently accumulated work wins — consistent with ``latest_with_content``. Falls
    back to closest-similarity when no ``recency_fn``. Returns ``(TaskRun, similarity)`` or
    ``None`` (genuine cold start). Shared by local and remote loop-seed carry-forward."""
    kept: list[tuple] = []
    for cand, sim in sims or []:
        has_content = False
        try:
            art = ws_root / cand.session_id / "artifact.md"
            has_content = art.exists() and len(art.read_text()) >= min_chars
        except OSError:
            has_content = False
        if not has_content:
            has_content = len(getattr(cand, "result_text", "") or "") >= min_chars
        if has_content:
            kept.append((cand, sim))
    if not kept:
        return None
    if len(kept) == 1 or recency_fn is None:
        return kept[0]  # single match, or no recency signal → closest-similarity
    return max(kept, key=lambda cs: recency_fn(cs[0].session_id))


def _seed_prior_from_path(seed_path: str, thash: str, requirement: str):
    """Build a synthetic prior TaskRun from an explicit seed file, or None.

    The escape hatch behind ``hill_climb_config.seed_path``: point a run at any
    artifact on disk, overriding exact-hash + semantic DB seeding — the fix for a
    weak same-task lineage silently blocking a stronger seed. The synthetic run's
    ``session_id`` deliberately has no on-disk ``artifact.md`` so the seed
    application falls through to ``result_text`` (the file body). Returns None when
    the path is blank, missing, unreadable, or empty (caller then falls back)."""
    from studio.task_runs import TaskRun  # noqa: PLC0415

    sp = (seed_path or "").strip()
    if not sp:
        return None
    p = Path(sp)
    try:
        text = p.read_text() if p.is_file() else ""
    except OSError:
        text = ""
    if not text.strip():
        return None
    return TaskRun(
        task_hash=thash, session_id=f"__seedfile__{p.name}", version=0, score=0.0,
        weaknesses=[], artifact_path="", requirement=requirement, result_text=text,
    )


def _full_scoring_matrix(session: Session) -> list[dict[str, object]]:
    rc = getattr(session, "rubric_config", None) or {}
    matrix = list(rc.get("scoring_matrix") or [])
    if matrix:
        return matrix
    try:
        from studio.rubric import DEFAULT_TEMPLATE, default_scoring_matrix

        template = rc.get("scoring_template") or rc.get("template") or DEFAULT_TEMPLATE
        return default_scoring_matrix(str(rc.get("report_type") or "general"), template)
    except Exception:  # noqa: BLE001
        return []


def _strip_task_scoring_block(text: str) -> str:
    marker = "\n\nUnified scoring requirements for this task:\n"
    head, sep, _tail = (text or "").partition(marker)
    return head if sep else (text or "")


def _final_step_instruction(
    requirement: str,
    desc: str,
    *,
    scoring_rules: str = "",
    weaknesses: list[str] | tuple[str, ...] = (),
    evidence_dossier: str = "",
) -> str:
    """Return final-step framing; report tasks must synthesize, not echo."""
    try:
        from studio.report_profiles import is_report_request
        report_like = is_report_request(requirement)
    except Exception:  # noqa: BLE001
        report_like = False
    if not report_like:
        return (
            "You are the final step of a multi-step agent workflow. "
            "The prior steps have already produced the following output. "
            "Your job: return the complete, final artifact exactly as produced "
            "by the prior steps (optionally refining it). "
            "Do NOT ask for more context or input — all necessary work is already done.\n\n"
            f"Workflow instruction: {desc}"
        )
    scoring_block = (scoring_rules or "").strip() or "- (no scoring matrix provided)"
    weakness_block = "\n".join(f"- {w}" for w in weaknesses if str(w).strip()) or "- (none)"
    evidence_block = (evidence_dossier or "").strip() or "- (no fetched evidence excerpts available)"
    return (
        "You are the final synthesis step for a research report. The prior steps "
        "already fetched evidence and may include RESEARCH_FINDING blocks. Write the "
        "complete publishable report from that evidence; do not echo intermediate "
        "drafts, internal scaffolding, or worker notes.\n\n"
        "Required report contract:\n"
        "- Use every relevant fetched finding. If several findings share one URL, "
        "cover their distinct claims instead of citing the URL once.\n"
        "- Include an Executive Summary that answers the user request.\n"
        "- Include evidence-backed analysis that explains patterns, implications, "
        "trade-offs, and why the evidence matters.\n"
        "- Include practical recommendations or next steps when useful.\n"
        "- Include limitations, caveats, or reflection on what remains uncertain or "
        "not fully verified.\n"
        "- Include References with only URLs present in the prior evidence.\n"
        "- If fetched evidence file paths are listed, use read_file on those paths "
        "when prior outputs are too thin to support analysis.\n"
        "- Do not invent source URLs, quotes, named sources, data, or citations.\n\n"
        "Before writing, evaluate the prior outputs against the full scoring standard "
        "and unresolved weaknesses below. The final report must address any relevant "
        "weakness; if evidence is insufficient, disclose that in limitations instead "
        "of inventing content.\n\n"
        f"FULL SCORING STANDARD:\n{scoring_block}\n\n"
        f"UNRESOLVED WEAKNESSES:\n{weakness_block}\n\n"
        f"FETCHED EVIDENCE FILES:\n{evidence_block}\n\n"
        f"Workflow instruction: {desc}"
    )


def _phase1_requirement_notice(requirements: Any) -> str:
    """PROACTIVE phase-1 requirement heads-up injected into the reducer prompt.

    Phase 1 of an epoch has generated nothing yet, so this is NOT a repair clause —
    it lists EVERY explicit checkable requirement the user stated (from the cached
    ``extract_requirements`` result) so the reducer addresses them from the start,
    the earliest possible shot at fulfilment, instead of waiting for the epoch-end
    verifier to flag a miss. Additive to the entry 167-170 epoch-end backstop.
    Returns ``""`` when the task stated no explicit checkable requirement (so a
    task with none injects nothing). Multi-branch OR groups render as
    ``X (or alternatively: Y)``; the OR is satisfied by ANY one branch.
    """
    from studio.requirement_compliance import _normalize_groups
    groups = _normalize_groups(requirements)
    if not groups:
        return ""
    lines: list[str] = []
    for branches in groups:
        if len(branches) == 1:
            lines.append(f"      - {branches[0]}")
        else:
            head, *rest = branches
            lines.append(f"      - {head} (or alternatively: {'; or '.join(rest)})")
    body = "\n".join(lines)
    return (
        "  - STATED TASK REQUIREMENTS (the full explicit list the user asked for — "
        "address each where your assigned section is relevant, starting now):\n"
        f"{body}\n"
    )


def _per_phase_compliance_repair_clause(client: Any, requirements: Any, partial_artifact: str) -> str:
    """VERIFY-AND-CORRECT clause for phases 2..N, injected into the reducer prompt.

    Verifies the cached requirements against the PARTIAL artifact assembled from the
    sections generated SO FAR this epoch, then lists what is STILL unaddressed — both
    genuine misses (``hard_issues``) AND not-yet-included OR-branch opportunities
    (``quality_opportunities``, which the user wants pursued early too) — so the
    reducer can fulfil them while phases remain, rather than leaving everything to
    the epoch-end backstop. Returns ``""`` when everything stated is already
    addressed, when there are no requirements, or on ANY verifier failure —
    ``requirement_compliance_issues(strict=False)`` already fail-opens to empty, and
    the ``try`` guards the import/unexpected-error path so a broken check never
    blocks a phase.
    """
    try:
        from studio.requirement_compliance import requirement_compliance_issues
        _pen, hard_issues, quality_opportunities = requirement_compliance_issues(
            client, requirements or [], partial_artifact or "", strict=False
        )
    except Exception:  # noqa: BLE001 — a per-phase verification failure never blocks the phase
        return ""
    outstanding = list(hard_issues) + list(quality_opportunities)
    if not outstanding:
        return ""
    items = "\n".join(f"      - {w}" for w in outstanding)
    return (
        "  - STATED REQUIREMENTS NOT YET ADDRESSED (nothing in the document so far "
        "fulfils these — fulfil any whose section is relevant to your patches now, "
        "while phases remain):\n"
        f"{items}\n"
    )


def _final_evidence_dossier(
    upstream: str,
    *,
    workspace_dir: Path | None = None,
    max_chars: int = 10_000,
) -> str:
    """Return final evidence handoff for URLs cited in upstream outputs."""
    urls: list[str] = []
    for raw in _re.findall(r"https?://[^\s)>\]\"']+", upstream or ""):
        url = raw.rstrip(".,;:")
        if url and url not in urls:
            urls.append(url)
    if not urls:
        return ""

    # P0-A: informational dossier and a SECONDARY source behind the in-process
    # _fetch_cache below → slight staleness is fine, so no cache_flush().
    cache: dict[str, Any] = {}
    try:
        try:
            from web_toolkit import cache_snapshot
        except Exception:  # noqa: BLE001 - cache API unavailable → direct-parse fallback
            cache_path = Path(".web_cache.json")
            if cache_path.exists():
                cache = _json.loads(cache_path.read_text())
        else:
            cache = cache_snapshot()
    except Exception:  # noqa: BLE001
        cache = {}

    def _cached_content(url: str) -> str:
        try:
            from studio.tools import _fetch_cache

            for key, value in _fetch_cache.items():
                if str(key).startswith(f"{url}|"):
                    return str(value[0] or "")
        except Exception:  # noqa: BLE001
            pass
        for key, value in cache.items():
            if not str(key).startswith(f"fetch:{url}:"):
                continue
            if isinstance(value, dict) and value.get("ok"):
                return str(value.get("content") or "")
        return ""

    rows: list[dict[str, object]] = []
    path_lines: list[str] = []
    if workspace_dir is not None:
        try:
            evidence_dir = workspace_dir / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            evidence_dir = None
    else:
        evidence_dir = None

    chunks: list[str] = []
    remaining = max_chars
    for url in urls:
        content = _cached_content(url)
        if not content:
            continue
        if evidence_dir is not None:
            idx = len(rows) + 1
            rel_path = f"evidence/source-{idx:03d}.md"
            try:
                (evidence_dir / f"source-{idx:03d}.md").write_text(
                    f"URL: {url}\n\n{content}",
                    encoding="utf-8",
                )
                rows.append({"url": url, "path": rel_path, "bytes": len(content.encode("utf-8"))})
                path_lines.append(f"- {rel_path} — {url}")
                continue
            except OSError:
                pass
        excerpt = " ".join(content.split())
        if not _length_ratio_ok(excerpt, max_chars=2500):  # cap only; action = truncate
            excerpt = excerpt[:2500].rstrip() + " [truncated]"
        block = f"SOURCE: {url}\nEXCERPT: {excerpt}"
        if len(block) > remaining:
            block = block[:remaining].rstrip() + "\n[truncated]"
        chunks.append(block)
        remaining -= len(block) + 2
        if remaining <= 0:
            break
    if rows and evidence_dir is not None:
        try:
            (evidence_dir / "fetched-sources.json").write_text(
                _json.dumps(rows, indent=2),
                encoding="utf-8",
            )
            path_lines.insert(0, "- evidence/fetched-sources.json — manifest of fetched source files")
        except OSError:
            pass
        return "\n".join(path_lines)
    return "\n\n".join(chunks)


def _merge_weaknesses(session: Session, weaknesses: list[str] | tuple[str, ...]) -> None:
    """Prepend new weakness strings to session.weaknesses, preserving old order."""
    new = [str(w).strip() for w in weaknesses if str(w).strip()]
    if not new:
        return
    current = list(getattr(session, "weaknesses", []) or [])
    seen = set(current)
    session.weaknesses = [w for w in new if w not in seen] + current


def _score_text_weaknesses(session: Session, text: str) -> list[str]:
    """Score current stage text against the frozen matrix and return weaknesses."""
    rc = getattr(session, "rubric_config", None) or {}
    if not (text or "").strip() or not rc.get("scoring_matrix"):
        return []
    try:
        from studio.rubric import rubric_scorecard_100, scorecard_weaknesses

        scorecard = rubric_scorecard_100(
            text,
            required_sections=_scoring_template(session),
            scoring_matrix=_full_scoring_matrix(session),
            weights=rc.get("weights"),
        )
        return scorecard_weaknesses(scorecard, _scoring_template(session))
    except Exception:  # noqa: BLE001
        return []


def _active_report_title(session: Session) -> str:
    rc = getattr(session, "rubric_config", None) or {}
    return str(rc.get("active_title") or rc.get("title") or "").strip()


def _report_title_from_artifact(artifact_text: str) -> str:
    import re as _re2
    match = _re2.search(r"(?m)^#\s+(.+?)\s*$", artifact_text or "")
    if not match:
        return ""
    title = match.group(1).strip()
    return "" if title.lower() in {"research report", "technical report", "final report", "report", "deliverable"} else title


def _update_active_template_from_artifact(session: Session, artifact_text: str) -> list[str]:
    """Track accepted outline additions without treating missing headings as removals."""
    rc = getattr(session, "rubric_config", None)
    if rc is None:
        return []
    title = _report_title_from_artifact(artifact_text)
    if title and "title" not in title.lower():
        rc["active_title"] = title
    outline = [str(s) for s in (rc.get("active_template") or rc.get("template") or [])]
    seen = {_section_title(s).lower() for s in outline if str(s).strip()}
    try:
        from agentkit.artifacts.sections import split_sections
        for heading, _body in split_sections(artifact_text or ""):
            if not heading.lstrip().startswith("## "):
                continue
            title = _section_title(heading)
            key = title.lower()
            if title and key not in seen:
                outline.append(title)
                seen.add(key)
    except Exception:  # noqa: BLE001 — outline tracking must never break generation
        return outline
    rc["active_template"] = outline
    session.rubric_config = rc
    return outline


def _sync_section_workspace(session: Session, workspace_root_path: Path, artifact_text: str) -> None:
    """Best-effort sync from assembled artifact to section files."""
    try:
        write_section_workspace(
            Path(workspace_root_path) / session.session_id,
            artifact_text,
            _active_template(session),
        )
    except Exception:  # noqa: BLE001 — section files must not break the current artifact path
        pass


def _write_artifact_through_sections(
    session: Session,
    workspace_root_path: Path,
    artifact_text: str,
    requirement: str = "",
) -> str:
    """Write section files first, then assemble ``artifact.md``."""
    root = Path(workspace_root_path) / session.session_id
    artifact_text = resolve_report_title(
        artifact_text, requirement, preferred_title=_active_report_title(session)
    )
    write_section_workspace(root, artifact_text, _active_template(session))
    return (root / "artifact.md").read_text(encoding="utf-8")


def _pick_scored_source(
    art_file: Path, result_output: str, *, rebuild_generated: bool = False
) -> str:
    """Return the text to finalize/score: the canonical ``artifact.md`` when present,
    else the most substantial workspace ``.md`` — in default "auto" mode nothing
    bootstraps the canonical file and the agent writes the report under a filename
    IT chose, so keying on ``artifact.md`` alone scored a phase's short status
    string instead of the real report on disk. Never prefers a shorter file over
    a longer in-memory return.

    On a ``rebuild_generated`` run (research_first) the trust model inverts: the
    in-memory return IS the deliverable the assembler just produced, and
    ``artifact.md`` is materialized AFTER this call, so any file on disk here is
    stale (a prior epoch's, or a truncated write). Consulting disk is then a
    hazard — in the write-early OSError edge a longer clean stale file would win
    the length comparison and poison scored_text before the shipped insurance
    guards run. Bypass disk entirely and take the fresh return."""
    if rebuild_generated and (result_output or "").strip():
        return result_output
    try:
        if art_file.exists():
            file_text = art_file.read_text()
            if len(file_text.strip()) >= len((result_output or "").strip()):
                return file_text
            return result_output
        # result.md is the per-epoch grounded-full ARCHIVE (written after recording),
        # not the deliverable — a stale one from a prior epoch must never shadow the
        # agent's report. A deliverable also has heading structure; size alone would
        # let a heading-less scratch/notes dump win.
        candidates = [
            p for p in art_file.parent.glob("*.md") if p.name != "result.md"
        ]
        for p in sorted(candidates, key=lambda p: p.stat().st_size, reverse=True):
            file_text = p.read_text()
            if not _re.search(r"^#{1,3} ", file_text, _re.MULTILINE):
                continue
            if len(file_text.strip()) >= len((result_output or "").strip()):
                return file_text
            break  # largest report-like file is still shorter than the return
    except Exception:  # noqa: BLE001 - a read failure must not break recording
        pass
    return result_output


# S1: moved to studio.textutil.dbg (was copy-pasted into artifact_text.py and
# structural_producer.py to dodge a circular import back to this module — kept as
# a thin alias so every existing `from studio.runner import _dbg` keeps working.
from studio.textutil import dbg as _dbg  # noqa: E402  (late alias: dodges circular import back to this module)
from studio.guards import length_ratio_ok as _length_ratio_ok  # noqa: E402  (late alias: same reason)
from studio.legacy_loop import LegacyLoopMixin  # noqa: E402  (quarantined legacy rollback loop; legacy_loop imports nothing from runner at module level)
from studio.seed_carry import SeedCarryForwardMixin  # noqa: E402  (quarantined seed carry-forward; imports nothing from runner at module level)
from studio.editor_pass import (  # noqa: F401,E402  (re-export: editor subsystem quarantined to editor_pass)
    _EDITOR_CHUNK,
    _classify_structural_opportunity,
    _editor_drive_round,
    _editor_opportunity_prompt,
    _editor_scored_issues,
    _editor_snapshot,
    _editor_structural_retry,
    _editor_structural_retry_prompt,
    _is_structural_opportunity,
    _run_editor_pass,
)


def _build_template_skeleton(
    sections: list[str] | tuple[str, ...],
    subsections: dict[str, list[str]] | None = None,
) -> str:
    """Skeleton of ``##`` placeholder sections; *subsections* (Workstream P,
    planner ``subsection`` decisions) adds ``###`` placeholders under the named
    parent — matched case-insensitively; an unmatched parent is dropped (the
    planner named a section that does not exist — fail-open, never invent one)."""
    subs = {k.strip().lower(): v for k, v in (subsections or {}).items()}
    blocks: list[str] = []
    for section in sections:
        title = str(section).strip().lstrip("#").strip()
        if not title:
            continue
        block = f"## {title}\n_(pending - needs sourced content)_\n\n"
        for sub in subs.get(title.lower(), []):
            block += f"### {str(sub).strip()}\n_(pending - needs sourced content)_\n\n"
        blocks.append(block)
    body = "".join(blocks)
    return "# _(deliverable title - generated from the findings below)_\n\n" + body.rstrip() + "\n"


#: entry 166 skeleton gate: a mid-flight death is worth persisting only when its artifact
#: holds at least this many words of REAL (non-placeholder) body. A skeleton-only seed is
#: worse than a cold start, so below this floor _persist_partial_run records nothing.
_MIN_PARTIAL_CONTENT_WORDS = 20

#: A resumed / silent-worker run whose current worker outputs number fewer than this is
#: treated as "thin": depth-expansion re-feeds the prior run's persisted worker_output
#: evidence so it has something to grow from, instead of the current run's empty dict.
_RESUME_OUTPUT_FLOOR = 2

#: Generation-core routing switch (PLAN-codebase-simplification.md §16): research_first
#: replaces the seed-and-patch phase loop as the default generator for sessions the
#: real ``POST /session`` endpoint creates (``session.use_research_first``, off by
#: default on the Session dataclass — see studio/session.py). An additional env-based
#: kill switch (not a task-specific check) gives an instant rollback with no code
#: change if the rebuild needs to be pulled, mirroring every other operational toggle
#: in this app (STUDIO_WORKSPACE_ROOT, STUDIO_LLM_RETRIES, ...).
def _use_research_first(session) -> bool:
    if not getattr(session, "use_research_first", False):
        return False
    return os.getenv("STUDIO_DISABLE_RESEARCH_FIRST", "").strip().lower() not in ("1", "true", "yes")


def _artifact_has_real_content(text: str) -> bool:
    """True when ``text`` has section-body content that is real, not just a skeleton.

    Strips heading lines and any line carrying a known placeholder marker — reusing
    ``artifact_lint._PLACEHOLDER_PATTERNS`` so BOTH the hyphen ``(pending - needs sourced
    content)`` and em-dash ``(pending — needs sourced content)`` spellings are caught — then
    checks the residue clears a small word floor. A pure skeleton yields zero body words.
    """
    if not (text or "").strip():
        return False
    from studio.artifact_lint import _PLACEHOLDER_PATTERNS
    words = 0
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        low = s.lower()
        if any(pat in low for pat in _PLACEHOLDER_PATTERNS):
            continue
        words += len(s.split())
    return words >= _MIN_PARTIAL_CONTENT_WORDS


class Runner(LegacyLoopMixin, SeedCarryForwardMixin):
    """Drives one studio run end-to-end, emitting the ordered SSE sequence.

    Constructed per run from a ``Session`` + an ``Emit`` sink + an injected
    ``LLMClient`` factory (so tests pass a fake client with no network). The
    factory takes the ``on_usage`` callback and returns a client.
    """

    def __init__(
        self,
        session: Session,
        emit: Emit,
        *,
        client_factory: Callable[[Callable[[UsageReport], None]], LLMClient] | None = None,
        embedder: Any = None,
        sandbox_cwd: str = ".",
        search_fn: Callable[..., list[Any]] | None = None,
        fetch_fn: Callable[..., Any] | None = None,
        workspace_root: Any = None,
        prefer_fn: Callable[[str, str, str], int] | None = None,
    ) -> None:
        self._session = session
        self._emit = emit
        self._client_factory = client_factory
        self._embedder = embedder
        self._sandbox_cwd = sandbox_cwd
        #: Injected web_search fn for the tool loop (tests pass a stub → no net).
        self._search_fn = search_fn
        #: Injected web_fetch fn for the tool loop (tests pass a stub → no net).
        self._fetch_fn = fetch_fn
        #: Workspace root override for the file-tool jail (tests pass a tmp dir).
        self._workspace_root = workspace_root
        #: Phase-1 epoch keep/discard judge (new, prior, requirement) -> net pref.
        #: Tests inject a deterministic stub; production builds one from
        #: agentkit.evolve.self_preference (see studio.epoch_gate, DESIGN §14.1).
        self._prefer_fn = prefer_fn
        self._acc = TokenAccounting()
        self._current_step_id = ""
        #: §14.4 epoch heartbeat: 1-based index of the current in-process pass when
        #: the epoch loop is engaged (0 = single-pass mode → step ids un-prefixed).
        self._epoch = 0
        #: Effective hill-climb config for this run, resolved once in run() from the
        #: session config + the persisted per-task snapshot (None until run() sets it).
        self._effective_hc: dict | None = None
        #: Final output / cancel flag of the last pass — run() reads these to emit the
        #: single terminal `done` after the epoch loop (the done moved out of _run_inner).
        self._last_result = ""
        self._last_cancelled = False
        self._last_scorecard_100: dict[str, Any] | None = None
        #: Per-epoch relevance-check state (studio.relevance) — set in
        #: _run_phase_loop (once per epoch, gated on a cross-task seed), read in
        #: _postrun_score_and_record (a sibling method, so state travels via `self`
        #: exactly like `_last_scorecard_100` above rather than widening either
        #: method's return signature).
        self._epoch_relevance_penalty: float = 0.0
        self._epoch_relevance_issues: list[str] = []
        #: True once relevance_issues() has actually RUN for this epoch (cross-task
        #: seed only). Recorded onto the TaskRun so future similar_runs() can
        #: deprioritize pre-feature runs whose score never accounted for relevance.
        self._epoch_relevance_checked: bool = False
        #: Requirement-compliance state (studio.requirement_compliance). The
        #: EXTRACTED explicit requirements are computed ONCE per run (task text is
        #: static for a task_hash) and cached — ``None`` until first extracted.
        #: Verification runs each epoch on the assembled artifact in
        #: _postrun_score_and_record; its penalty/issues travel via `self` to the
        #: editor pass + score, and feed the NEXT epoch's worker repair clause.
        self._task_requirements: list[list[str]] | None = None
        self._epoch_compliance_penalty: float = 0.0
        self._epoch_compliance_issues: list[str] = []
        #: Optional (non-blocking) polish for the editor: unsatisfied SIBLING
        #: branches of an OR requirement ALREADY met by another branch. These
        #: never affect the score/penalty or the hard weakness list — they only
        #: nudge the editor to add a nice-to-have (e.g. a diagram when the task
        #: said "code OR diagram" and the code alone satisfied it).
        self._epoch_quality_opportunities: list[str] = []
        self._last_evidence_matrix = ""
        self._last_evidence_count = 0
        self._last_weak_evidence_count = 0
        self._last_review: dict[str, Any] | None = None
        self._last_metrics: dict[str, Any] | None = None
        self._last_stop_reason = "validation_passed"
        self._tool_calls = 0
        self._tool_failures = 0
        self._agent_trace: list[dict[str, Any]] = []
        self._checkpoints: list[dict[str, Any]] = []
        self._pending_tool_args: list[dict[str, Any]] = []
        self._last_publish_issues: tuple[str, ...] = ()
        #: Wall-clock start of the run, stamped in run(); the done frame reports
        #: real elapsed time (per-phase wall_s lives on phase_done).
        self._t0: float | None = None
        #: T1 baseline instrumentation (PLAN §1): per-run, per-stage wall-clock
        #: accumulator (seconds). Per-Runner-instance (a Runner is built per run),
        #: so concurrent runs never clobber each other's timings. Reset in run();
        #: summarized onto the terminal ``done`` event + one _dbg summary line.
        self._stage_timings: dict[str, float] = {}
        #: T1: the CURRENT phase's timing bucket (spoke/reducer/prefetch/scorecard).
        #: Reset at the top of each phase iteration; snapshotted onto phase_done.
        #: Instance-scoped (phases are sequential; the reducer runs synchronously
        #: inside run_plan), mirroring the existing per-phase ``_phase_captured``.
        self._phase_timing: dict[str, float] = {}
        #: Tokens captured via on_usage for the current phase (used to reconcile
        #: against run_plan's StepRun.tokens for non-StudioChatClient backends).
        self._phase_captured = 0

    # -- token plumbing ----------------------------------------------------

    def _on_usage(self, usage: UsageReport) -> None:
        """Per-call usage sink: feed accounting + push a ``token`` frame.

        Closes over ``_current_step_id`` so each frame is attributed to the phase
        running when the LLM call fired.
        """
        self._acc.add(usage)
        self._phase_captured += usage.input_tokens + usage.output_tokens
        self._emit(
            TokenEvent(
                step_id=self._current_step_id,
                input=usage.input_tokens,
                output=usage.output_tokens,
                total=usage.input_tokens + usage.output_tokens,
                estimated=usage.estimated,
                cumulative={
                    "input": self._acc.total_input_tokens,
                    "output": self._acc.total_output_tokens,
                    "total": self._acc.total_tokens,
                    "estimated": self._acc.tokens_estimated,
                },
            )
        )

    def _reconcile_phase_tokens(self, step_id: str, step_tokens: int) -> None:
        """Emit a ``token`` frame for tokens ``run_plan`` counted but ``on_usage``
        did not capture (the raw/CLI-client case, where no UsageReport fires).

        Such tokens carry no in/out split and no usage telemetry, so they are
        booked as ``estimated`` output tokens — flipping the run's sticky ``~``,
        which is the honest signal for "this backend did not report a split".
        Keeps the HUD reconciled to ``DynamicPlanResult.total_tokens`` (SPEC §8).
        """
        remainder = step_tokens - self._phase_captured
        if remainder <= 0:
            return
        self._on_usage(
            UsageReport(input_tokens=0, output_tokens=remainder, estimated=True)
        )

    # -- T1 baseline instrumentation --------------------------------------

    def _stage_add(self, key: str, t0: float) -> None:
        """Accumulate ``monotonic()-t0`` seconds into the per-run stage timer ``key``.

        PURELY ADDITIVE (PLAN §1 T1): reads ``time.monotonic()`` and books the delta;
        never affects control flow or output. Keys are summed so a stage that runs once
        per epoch accumulates across the epoch loop.
        """
        self._stage_timings[key] = self._stage_timings.get(key, 0.0) + (time.monotonic() - t0)

    def _phase_time_add(self, key: str, dt: float) -> None:
        """T1: accumulate a wall-clock delta (seconds) into the CURRENT phase bucket.

        The reduce/prefetch sink passed into ``_make_section_reducer`` — called
        synchronously from inside ``run_plan`` within the active phase iteration.
        """
        self._phase_timing[key] = self._phase_timing.get(key, 0.0) + dt

    # -- the run -----------------------------------------------------------

    def run(self, requirement: str) -> None:
        """Execute the full pipeline for ``requirement``, emitting every event.

        §14.4 epoch heartbeat: when ``auto_improve`` is on AND ``max_epochs > 1``
        the run auto-iterates ``_run_inner`` up to ``max_epochs`` times — each pass
        seeds from the prior via the existing ``latest_with_content`` carry-forward —
        breaking early on plateau/converged or cancellation. Otherwise a single pass
        runs, exactly as before. The terminal ``done`` is emitted once, after the loop.
        """
        self._t0 = time.perf_counter()
        self._stage_timings = {}
        self._last_scorecard_100 = None
        self._last_evidence_matrix = ""
        self._last_evidence_count = 0
        self._last_weak_evidence_count = 0
        self._last_review = None
        self._last_metrics = None
        self._last_stop_reason = "validation_passed"
        self._tool_calls = 0
        self._tool_failures = 0
        self._agent_trace = []
        self._checkpoints = []
        self._pending_tool_args = []
        self._last_publish_issues = ()
        # RC3: the off-topic verdict cache is process-global (studio.findings) and
        # keyed on (normalized URL, requirement hash) — a stale verdict from an
        # earlier run of the SAME task must not survive into this run after a
        # refetch changed the page (the source content could have changed even
        # though the URL/requirement pair didn't). Safe to keep warm ACROSS the
        # epochs of a single run() call (handled below); reset only per run().
        from studio.findings import _OFFTOPIC_VERDICT_CACHE
        _OFFTOPIC_VERDICT_CACHE.clear()
        try:
            cfg = self._resolve_hc_config(requirement)
            self._effective_hc = cfg
            auto = bool(cfg.get("auto_improve"))
            max_epochs = int(cfg.get("max_epochs", 5))
            if auto and max_epochs > 1:
                for i in range(max_epochs):
                    self._epoch = i + 1
                    res = self._run_inner(requirement)
                    # Stop only on a genuine PLATEAU (this epoch's score didn't beat the
                    # prior by min_improvement) or cancel — NOT on "converged". "converged"
                    # is `_version >= max_epochs`, and `_version` is the CUMULATIVE all-time
                    # version (next_version = MAX(version)+1 over every prior run of this
                    # task). On a task with history it fires on the FIRST epoch, so the loop
                    # ran once and max_epochs=2 did a single pass while two MANUAL runs did
                    # two — the "1+1 ≠ 2 epochs" surprise. The per-run epoch count is already
                    # bounded by range(max_epochs); converged must not also gate it. (§14.8)
                    if res.status == "plateau":
                        break
                    if self._session.cancel_requested:
                        break
            else:
                self._epoch = 0
                self._run_inner(requirement)
            # Single terminal done for the whole stream (all epochs share one stream).
            self._emit(
                self._done_event(self._last_result, cancelled=self._last_cancelled)
            )
        except Exception as exc:  # noqa: BLE001 - any failure becomes an error frame
            # Log the full traceback to stderr (→ uvicorn.log) BEFORE swallowing the error
            # into an ErrorEvent. Without this, a mid-run failure surfaces ONLY as ``str(exc)``
            # (e.g. a bare "Connection error." from a transient oMLX drop) with no stack or
            # exception type, making infra-vs-code failures undiagnosable from the log alone —
            # cost a full browser-spelunk to root-cause an oMLX connection drop on 2026-07-04.
            import traceback as _tb
            _tb.print_exc()
            # entry 166: snapshot partial artifact state before surfacing the error, so a
            # mid-flight death carries its research forward instead of cold-starting next run.
            saved = self._persist_partial_run(requirement, exc)
            # Prefix the exception TYPE so the surfaced message distinguishes an infra failure
            # (e.g. APIConnectionError) from a code failure even in the frontend, not just str().
            msg = f"{type(exc).__name__}: {exc}" if str(exc).strip() else type(exc).__name__
            if saved:
                msg += " (partial progress saved for carry-forward)"
            self._emit(ErrorEvent(message=msg, where="runner"))
            # Still emit a terminal done so the frontend leaves the running state.
            self._emit(self._done_event("", cancelled=False))

    def _resolve_hc_config(self, requirement: str) -> dict:
        """Resolve the effective hill-climb config (DESIGN §14.4 persistence).

        Session config wins. When the session did not pin the epoch budget (no
        config, or a config missing ``max_epochs``) the per-task snapshot recorded
        by a prior run is loaded via ``TaskRunStore.latest_config`` — keyed by the
        SAME base ``task_hash`` the artifact carry-forward uses — so a requirement
        remembers its epoch budget across backend restarts. ``requirement`` here is
        the base requirement (goal/template are injected inside ``_run_inner`` only),
        so the key never forks the task identity (§14.1 D1).
        """
        session_cfg = dict(getattr(self._session, "hill_climb_config", None) or {})
        if "max_epochs" not in session_cfg:
            try:
                from studio.task_runs import (
                    TaskRunStore,
                    base_identity as _base_identity,
                    task_hash as _task_hash,
                )
                # PLAN item 5: key the config lookup on the lineage base, not the raw
                # (possibly conversational) requirement, so a "Continue run" finds its
                # prior epoch budget instead of cold-starting.
                persisted = TaskRunStore().latest_config(
                    _task_hash(_base_identity(requirement))
                )
            except Exception:  # noqa: BLE001 — persistence is best-effort
                persisted = {}
            if persisted:
                return {**persisted, **session_cfg}  # session always wins
        return session_cfg

    def _run_inner(self, requirement: str) -> EpochResult:
        session = self._session
        # §14.4: reset per-pass step/phase accumulators so each in-process epoch is a
        # clean sub-DAG (the panel trackers below are already rebuilt per pass).
        self._current_step_id = ""
        self._phase_captured = 0
        # Default outcome — if a pass errors before scoring, the loop treats it as
        # "improving" (score 0) and continues until max_epochs (DESIGN §14.4 #1).
        _outcome = EpochResult(version=0, score=0.0, delta=0.0, status="improving")

        # Task IDENTITY for hill-climb continuity is the BASE requirement, captured
        # BEFORE the goal/constraints block (just below) and the per-iteration prefix
        # (~L1281) are prepended. Hashing the augmented string forked the lineage every
        # time a goal was attached: same task, new task_hash → cold-start v1, no artifact
        # carry-forward, weakness-score 0/N = 0.00. The goal still STEERS the agent (it
        # stays in `requirement` for planning); it just no longer changes the task's
        # identity. Used at the auto_improve seed lookup and the run-record block below.
        _base_requirement = requirement

        # Inject goal end_state and constraints into requirement so the agent sees them
        # (worker goal + post-phase verification). NOT into the PLANNER input: when the
        # goal overlaps the task, prepending it doubled the text and the planner split the
        # repeat into DUPLICATE phases (a multi-subject run where the goal matched the
        # requirement made a subject name / "create a report" appear twice). So the goal lives only in
        # `requirement`/`_original_requirement`; the planner reads `_plan_requirement`,
        # which starts from the clean base.
        _goal = getattr(session, "goal", None)
        if _goal is not None:
            _parts: list[str] = []
            if getattr(_goal, "end_state", None):
                _parts.append(f"Goal: {_goal.end_state}")
            _constraints = getattr(_goal, "constraints", None) or []
            if _constraints:
                _parts.append("Constraints:\n" + "\n".join(f"- {c}" for c in _constraints))
            if _parts:
                requirement = "\n".join(_parts) + "\n\n" + requirement

        # Wire the deliverable TEMPLATE into GENERATION (DESIGN §14.2): when the GUI has set
        # a rubric template, instruct the agent to produce those sections so the template
        # STEERS the report instead of only being graded after the fact. Appended AFTER
        # `_base_requirement` is captured (never changes task_hash), and to BOTH the full
        # `requirement` AND the goal-free `_plan_requirement` — distinct section names
        # (unlike the goal) never duplicate the task, so the template IS safe in the planner
        # input. Only when explicitly configured — defaulting to DEFAULT_TEMPLATE would
        # force report headings onto non-research tasks and compromise generation.
        _plan_requirement = _base_requirement
        _rc = getattr(session, "rubric_config", None) or {}
        _template = _rc.get("active_template") or _rc.get("template")
        if _template:
            _sections = "\n".join(f"- {s}" for s in _template)
            _title = str(_rc.get("active_title") or "").strip()
            _title_line = f"\nUse this current H1 report title unless you improve it: {_title}" if _title else ""
            _tpl_suffix = (
                "\n\nStructure the deliverable with these sections (use them as "
                "top-level headings, in order):"
                + _title_line
                + "\n" + _sections
            )
            requirement = requirement + _tpl_suffix
            _plan_requirement = _plan_requirement + _tpl_suffix
        _scoring_matrix = _rc.get("scoring_matrix")
        if _rc:
            from studio.rubric import format_scoring_rules
            _score_suffix = (
                "\n\nUnified scoring requirements for this task:\n"
                "- The original deterministic rubric signals are the base measurements.\n"
                "- The frozen scoring matrix below defines the profile/template-specific 100-point scorecard.\n"
                "- Agents must satisfy the scoring rows related to their assigned sections; universal rows apply everywhere.\n"
                "- Reducers measure the whole artifact against the full scoring matrix.\n"
                f"{format_scoring_rules(_scoring_matrix)}"
            )
            requirement = requirement + _score_suffix
            _plan_requirement = _plan_requirement + _score_suffix

        # Stash the original requirement so task_hash is stable across iterations
        # (the seeder may rewrite requirement with "ITERATION N —..." prefix).
        _original_requirement = requirement

        # Hill climb: if auto_improve is on and a prior run exists for this task,
        # copy its artifact into the current workspace and prefix the requirement
        # with the prior score + weaknesses so the agent edits rather than regenerates.
        # Effective config = the one run() resolved (session ⊕ persisted, §14.4); fall
        # back to the raw session config when _run_inner is exercised directly.
        _hc_cfg = (
            self._effective_hc
            if self._effective_hc is not None
            else (getattr(session, "hill_climb_config", None) or {})
        )
        # build the usage-capturing client (injected factory in tests) BEFORE
        # seed carry-forward so the coarse whole-doc seed-relevance gate can use
        # it (a cross-task R10 seed about a different subject is dropped there).
        base_client = self._build_client()
        # Strong-model judge for presentation detection (built once per run; degrades to
        # base_client in tests / when the judge backend is unavailable).
        judge_client = self._build_judge_client(base_client)
        (
            requirement, _weaknesses_block, _artifact_copied, _eff_ws2,
            _seed_len, _seed_text, _seed_cross_task, _seed_topic,
        ) = self._seed_carry_forward(
            session=session,
            requirement=requirement,
            _base_requirement=_base_requirement,
            _hc_cfg=_hc_cfg,
            base_client=base_client,
        )

        # session frame
        self._emit(
            SessionEvent(llm=session.llm_info, embed=session.embed_info, mode=session.mode)
        )

        _model_id = str(
            (session.llm_info or {}).get("model")
            or (session.llm_spec or {}).get("model")
            or ""
        )
        _model_profile = resolve_model_profile(_model_id)
        # Planning runs on the STRONG judge model (same rationale as presentation
        # detection, _build_judge_client): a weak generation model shreds compound
        # requirements and under-specifies phase deliverables. judge_client degrades
        # to base_client when the judge backend is unavailable or tests inject one
        # client, so this never adds a hard dependency.
        _planner_client = MaxTokensClient(judge_client, _model_profile.planner_max_tokens)
        # Workstream P: the template is only the STARTING outline. The planner
        # reviews it against the requirement's deliverable-shaped asks (example
        # code, design architecture, …) and may add a section or a sub-section —
        # additions land on rc["active_template"] (the live outline the skeleton,
        # assignments, and publish gate read); the frozen scoring_template /
        # scoring_matrix are never touched (no moving target). 0 LLM calls when
        # the requirement names no form deliverable; fail-open on review errors.
        self._dyn_subsections = {}
        try:
            _rc_p = getattr(session, "rubric_config", None)
            if _rc_p is not None and (_rc_p.get("active_template") or _rc_p.get("template")):
                from studio.planning import (
                    apply_section_decisions,
                    requirement_section_decisions,
                )

                _p_decisions = requirement_section_decisions(
                    _planner_client,
                    _active_template(session),
                    _base_requirement,
                    weaknesses=tuple(getattr(session, "weaknesses", []) or [])[:6],
                )
                if _p_decisions:
                    _added, self._dyn_subsections = apply_section_decisions(
                        _rc_p, _p_decisions
                    )
                    for _d in _p_decisions:
                        _dbg(
                            f"section review: {_d['deliverable']!r} → {_d['action']}"
                            f" {_d['title']!r}"
                            + (f" under {_d['parent']!r}" if _d.get("parent") else "")
                        )
                    if _added:
                        _dbg(f"dynamic sections added to active outline: {_added}")
        except Exception:  # noqa: BLE001 — outline review must never block a run
            pass
        # Wrap in a web_search tool loop when tools are enabled (run_plan stays
        # unchanged — it sees a plain LLMClient that happens to run a tool loop).
        # When a prior artifact was seeded, also offer read_artifact/patch_artifact
        # so concurrent workers can apply OCC patches directly to artifact.md.
        _art_for_tools = (
            _eff_ws2 / session.session_id / "artifact.md"
            if _artifact_copied and _eff_ws2 is not None
            else None
        )
        client = self._maybe_tool_augment(base_client, artifact_path=_art_for_tools)

        # plan → emit plan. Three paths:
        #   1. Seeded session  → pre-seed decomposition from a loop-library loop.
        #   2. LLM mode        → EPIC-BASED planning (DESIGN §2.3): the planner
        #                        LLM emits an EPIC_PLAN; each epic is one phase.
        #   3. Offline/auto    → deterministic plan() (no LLM available; tests).
        seed_steps = session.seed_steps
        use_llm = session.mode == "llm"
        # Plan from the BASE requirement, NOT the goal/template-injected `requirement`.
        # The goal end_state is prepended to `requirement` for steering, but when it
        # OVERLAPS the task the planner split the DOUBLED text into duplicate phases — a
        # goal == the requirement produced a subject name / "create a report" twice.
        # The goal still steers via the keep/discard gate + verification; it must not
        # become phase-splitting text. _base_requirement is also free of the
        # weakness/template bloat the epic planner already wanted to avoid.
        _t_plan = time.monotonic()  # T1: plan-construction stage timer
        if seed_steps:
            plan_obj = plan(_plan_requirement, decomposer=make_seeded_decomposer(seed_steps))
            self._emit(LoopSeedEvent(loop_id=session.seed_loop_id, steps=seed_steps))
        elif use_llm:
            # Planner runs on base_client (no tool loop — planning needs no web).
            plan_obj = _plan_from_epics(
                _plan_requirement, _planner_client, weaknesses_block=_weaknesses_block
            )
        else:
            plan_obj = plan(_plan_requirement)
        self._stage_add("plan", _t_plan)
        # Collapse duplicate phases regardless of which planner produced them (seeded,
        # epic-LLM, or deterministic): a goal listing several sub-tasks otherwise yields
        # the same phase twice in the DAG, doubling agents + tokens.
        plan_obj = _dedupe_plan_steps(plan_obj)
        # §14.4 re-entrancy: when the epoch loop replays _run_inner in-process, prefix
        # every step id (and its depends_on edges) with the epoch index so each pass is
        # a distinct sub-DAG and ids never collide across epochs (e.g. e2:s3). Single-pass
        # runs keep epoch 0 and are left un-prefixed (back-compat for existing tests).
        if self._epoch > 0:
            _pfx = f"e{self._epoch}:"
            plan_obj = replace(
                plan_obj,
                steps=tuple(
                    replace(
                        s,
                        id=_pfx + s.id,
                        depends_on=tuple(_pfx + d for d in s.depends_on),
                    )
                    for s in plan_obj.steps
                ),
            )
        # Capture the plan-as-dicts once: the PlanEvent payload AND the input the
        # Loop Doctor audits (its clear_stopping check walks this DAG at run end).
        plan_step_dicts = [
            {
                "id": s.id,
                "description": s.description,
                "depends_on": list(s.depends_on),
                "role": s.role,
                "difficulty": s.difficulty,
            }
            for s in plan_obj.steps
        ]
        self._emit(PlanEvent(task=plan_obj.task, steps=plan_step_dicts))

        # assign topologies (auto; llm path only when mode=='llm' AND client given).
        # use_llm already computed above for the epic-planning branch.
        # E2/E1 (PLAN §4b §4c): derive each phase's topology AND keep the full
        # TopologyChoice so we can log WHY it was chosen. In LLM mode the model
        # chooses topology+rationale directly from the design principles; the
        # deterministic classifier remains the non-LLM fallback.
        from agentkit.topology.dynamic import assign_topologies_with_choices
        _topology_client = MaxTokensClient(base_client, _model_profile.topology_max_tokens)
        _t_topo = time.monotonic()  # T1: topology-assignment stage timer
        if use_llm:
            plan_obj, _topo_choices = select_topologies_by_llm(plan_obj, _topology_client)
        else:
            plan_obj, _topo_choices = assign_topologies_with_choices(plan_obj)
        self._stage_add("topology", _t_topo)
        _execution_topology = {
            GATEWAY: SINGLE,
            DURABLE_BOARD: SINGLE,
            TREE: STAR,
        }
        _selector_runtime_map = {
            s.id: s.topology
            for s in plan_obj.steps
            if s.topology and s.topology in _execution_topology
        }
        if _selector_runtime_map:
            plan_obj = replace(
                plan_obj,
                steps=tuple(
                    replace(s, topology=_execution_topology[str(s.topology)])
                    if s.id in _selector_runtime_map
                    else s
                    for s in plan_obj.steps
                ),
            )
        # E1 (PLAN §4b): the force-STAR override is DELETED. Every topology now satisfies the
        # assemble+verify reducer contract — STAR/MAP/MESH fold worker drafts through the
        # section reducer, and SINGLE (identity-fold) / PIPELINE (terminal-stage capture) route
        # their single output through the SAME reducer (dynamic.py). So the section-aware
        # additive merge runs under EVERY topology, and the already-computed, principled
        # selection is honored under hill-climb instead of discarded. The injected reducer is
        # additive-only (+ the accept_rewrite writeback guard), so a capture-only phase that
        # folds nothing preserves the artifact — honoring selection never regresses it.
        topology_map = {s.id: (s.topology or SINGLE) for s in plan_obj.steps}
        # E2: surface the rationale per phase so every phase logs WHY its topology was chosen
        # — auditable, not a silent verdict.
        _topo_steps: list[dict[str, Any]] = []
        for sid, topo in topology_map.items():
            _ch = _topo_choices.get(sid)
            _entry: dict[str, Any] = {"id": sid, "topology": topo}
            if _ch is not None:
                _entry["questions_fired"] = list(_ch.questions_fired)
            if sid in _selector_runtime_map:
                _entry["rationale"] = (
                    f"selector proposed state/routing topology "
                    f"{_selector_runtime_map[sid]} → execute as {topo}. "
                    f"{_ch.rationale if _ch else ''}".strip()
                )
            elif _ch is not None:
                _entry["rationale"] = _ch.rationale
            _topo_steps.append(_entry)
        self._emit(TopologyEvent(steps=_topo_steps))

        # derived render graph
        self._emit(_render_graph(plan_obj))

        # panel trackers
        dag = DagTracker(plan_obj)
        self._emit(dag.snapshot())
        mem = MemoryTracker(self._embedder)
        selfimp = SelfImproveTracker()
        budget = (
            FanoutBudget(ceiling=session.budget_ceiling)
            if session.budget_ceiling is not None
            else None
        )

        # M8: cross-phase TaskLedger and dynamic sizing (DESIGN §3, §5)
        _ledger = TaskLedger()
        # Seed the ledger with every planned phase UP FRONT (DESIGN §2.3 / §3.2).
        # Without this, all_tasks stayed empty and remaining() was structurally
        # always empty — the REMAINING block printed "(none)" and worker sizing
        # saw max(1,0)=1 every phase. Seeding makes remaining() reflect real
        # pending work; mark_done() (end of loop) moves each finished phase to
        # completed, so later hubs see an accurate COMPLETED-vs-REMAINING split
        # and never re-assign prior-phase work.
        for _s in plan_obj.steps:
            _ledger.add_task(TaskRecord(id=_s.id, description=_s.description[:120]))
        _lc = getattr(session, "loop_config", None)
        _sizing_cfg = _lc.sizing() if _lc is not None else None

        outputs: dict[str, str] = {}
        _reducer_gaps: list[str] = []   # §11.4 last-phase gaps → next-run weaknesses

        _tmpl_sections = _active_template(session)
        # §14.1: create == improve. When no prior doc exists yet but the session
        # declares a deliverable section template, bootstrap a generic skeleton so
        # the phase loop fills it additively through the same section reducer used
        # for seeded improvement runs.
        _t_skel = time.monotonic()  # T1: cold-start skeleton-bootstrap stage timer
        # NOT gated on use_llm (root cause of uncited/shallow cold-start runs,
        # 2026-07-04): without this skeleton, _artifact_copied stays False for the
        # WHOLE phase loop in default "auto" mode, which silently disables the
        # section-aware reducer injection (citation contract + _prefetch_cited +
        # evidence/ dossier), the artifact OCC tools, and the additive writeback —
        # every phase reduced through the generic synthesis prompt and stripped
        # all 42 worker-cited URLs (live run s_891b68ae6c35). create == improve
        # applies to every generation mode.
        if not _artifact_copied and _tmpl_sections:
            _eff_ws2 = _eff_ws2 or self._workspace_root or workspace_root()
            if _eff_ws2 is not None:
                _skel = _build_template_skeleton(
                    _tmpl_sections, getattr(self, "_dyn_subsections", None)
                )
                _skel_file = _eff_ws2 / session.session_id / "artifact.md"
                _skel_file.parent.mkdir(parents=True, exist_ok=True)
                _skel_file.write_text(_skel)
                _update_active_template_from_artifact(session, _skel)
                _sync_section_workspace(session, _eff_ws2, _skel)
                _artifact_copied = True       # additive pipeline now has a base
                _seed_len = len(_skel)        # may grow from here, never shrink below
        self._stage_add("skeleton", _t_skel)

        # §14.6: a SEEDED run keeps the seed's section structure — the reducer
        # PATCHES existing headings, it never injects a missing one. So a rubric-
        # template section absent from the seed (e.g. "Limitations and Open
        # Questions") is mined as a weakness EVERY epoch yet never created: there is
        # no PATCH_TARGET heading to fill, so hill-climb "can't create new sections."
        # Fix: append each MISSING template section as an empty heading + placeholder
        # so the additive patch pipeline has a target to fill. Concept-aware match
        # (sections_present) avoids re-adding a renamed-but-present section. Covers
        # both paths — on cold start the skeleton already has every section, so the
        # missing set is empty and this is a no-op. Only when a template is configured.
        if _artifact_copied and _tmpl_sections and _eff_ws2 is not None:
            _art_f = _eff_ws2 / session.session_id / "artifact.md"
            try:
                _cur = _art_f.read_text()
                # N1: first RECONCILE any doubled outline a prior epoch left (drop empty
                # template duplicates whose concept is already populated), THEN add only the
                # genuinely-missing template sections. Order matters — reconcile before merge.
                _recon = reconcile_outline(_cur, _tmpl_sections)
                _merged = _merge_missing_sections(_recon, _tmpl_sections)
                if _merged != _cur:
                    _art_f.write_text(_merged)
                    _update_active_template_from_artifact(session, _merged)
                    _sync_section_workspace(session, _eff_ws2, _merged)
                    _seed_len = len(_merged)
                    _dbg(f"reconciled+seeded template sections ({len(_cur)}→{len(_merged)})")
            except Exception:  # noqa: BLE001 — structure-merge is best-effort
                pass
        #: Gate outcomes collected across phases — the Loop Doctor's safe_actions
        #: check reads these at run end (no re-running of any gate).
        gate_events: list[GateEvent] = []

        # Requirement extraction is run-scoped (cached once on `self`). Pull it
        # forward to BEFORE the phase loop so phase-1's proactive requirement notice
        # and phases 2..N's per-phase verification (see _run_phase_loop) have it on a
        # COLD-START epoch too — the epoch-end record path (_postrun_score_and_record)
        # also extracts lazily, but that is too late for in-epoch injection. `is None`
        # keeps it to one call per run (epoch 2+ reuses); using _original_requirement
        # matches the epoch-end check's input exactly so both share one cache. Fail-open:
        # a failed extract leaves it None and simply skips the proactive/per-phase
        # injection this run (the epoch-end backstop still runs).
        if self._task_requirements is None and base_client is not None:
            try:
                from studio.requirement_compliance import extract_requirements
                self._task_requirements = extract_requirements(
                    base_client, _original_requirement
                )
            except Exception:  # noqa: BLE001 — extraction failure must never strand a run
                pass

        self._used_research_first = False
        if _use_research_first(session):
            cancelled, final_output, _seed_text = self._run_research_first_generation(
                session=session,
                client=client,
                base_client=base_client,
                requirement=requirement,
                base_requirement=_base_requirement,
            )
        else:
            cancelled = None
        if cancelled is None:
            cancelled, final_output, _seed_text = self._run_phase_loop(
                session=session,
                plan_obj=plan_obj,
                client=client,
                base_client=base_client,
                budget=budget,
                _ledger=_ledger,
                _sizing_cfg=_sizing_cfg,
                _lc=_lc,
                _eff_ws2=_eff_ws2,
                _artifact_copied=_artifact_copied,
                _seed_len=_seed_len,
                _seed_text=_seed_text,
                _seed_cross_task=_seed_cross_task,
                _seed_topic=_seed_topic,
                use_llm=use_llm,
                requirement=requirement,
                mem=mem,
                dag=dag,
                selfimp=selfimp,
                outputs=outputs,
                gate_events=gate_events,
                _reducer_gaps=_reducer_gaps,
                base_requirement=_base_requirement,
            )

        # budget gauge
        if budget is not None:
            self._emit(
                BudgetEvent(
                    spent=budget.spent_total,
                    ceiling=session.budget_ceiling,
                    exceeded=False,
                )
            )

        # If the final step produced less than its direct predecessor, fall back
        # to the predecessor's output. In research loops, the last step is a
        # meta "stop/continue" decision — the real artifact lives in the step it
        # depends on (its direct predecessor in the DAG).
        last_step = plan_obj.steps[-1] if plan_obj.steps else None
        predecessor_id = (
            last_step.depends_on[-1] if (last_step and last_step.depends_on) else None
        )
        predecessor_output = outputs.get(predecessor_id, "") if predecessor_id else ""
        result_output = (
            predecessor_output
            if predecessor_output and len(predecessor_output) > len(final_output)
            else final_output
        )

        # Steps that write their artifact to artifact.md produce content in a file
        # rather than the LLM text response, so prefer the file — BUT only when it is
        # complete. Auto-improve copies the prior best artifact into the workspace as a
        # seed; if the agent doesn't overwrite it, the file is a STALE (and here,
        # truncated) seed. The old "prefer the longest text" rule then re-kept that
        # truncated seed over the agent's fresh, complete-but-shorter synthesis — every
        # iteration re-scored the same truncated text and the score could never climb
        # past the "not truncated" criterion. Fix: only prefer the file when it is longer
        # AND ends cleanly; a truncated file loses to the agent's actual final output.
        # research_first already writes its own fresh output to artifact.md
        # (write-after-return, see _run_research_first_generation) — this
        # length/lint race exists to pick between an agent's LLM-return text
        # and a file a DIFFERENT step wrote. For research_first, the file IS
        # the return text; letting a leftover auto-improve seed win here (if
        # the write-after-return itself failed) is a stale-seed regression,
        # not a legitimate "prefer the file" case. Bypass it entirely.
        ws_artifact = self._read_workspace_artifact()
        if (
            ws_artifact
            and not self._used_research_first
            and len(ws_artifact) > len(result_output)
            and _ends_cleanly(ws_artifact)
        ):
            try:
                from studio.artifact_lint import lint_artifact

                ws_lints = lint_artifact(ws_artifact)
                result_lints = lint_artifact(result_output)
                if len(ws_lints) <= len(result_lints):
                    result_output = ws_artifact
            except Exception:  # noqa: BLE001 — preserve prior fail-open behavior
                result_output = ws_artifact

        # §11.10: strip any reducer commentary preamble from the DISPLAYED/stored
        # result too — not just artifact.md. A reducer that narrated ("The artifact
        # is complete… Weaknesses addressed: ✅… Remaining concern: future-dated…")
        # leaves that in result_output even when artifact.md was sanitized, since
        # the preamble version is longer and wins the length check above. That
        # commentary belongs in the surfaced _unresolved_block (chat), never in the
        # deliverable shown to the user.
        result_output = _strip_preamble(result_output)

        # verification (pure tier, always runs)
        verify_event = build_verify_event(result_output)
        self._emit(verify_event)

        # Loop Doctor (M8): audit the finished run against loop-library's
        # checklist, composed from the run's collected gate/verify outcomes +
        # the budget ceiling + the plan DAG. Suggestions only — never applied.
        loopdoctor_event = build_loopdoctor_event(
            plan_step_dicts,
            budget_ceiling=session.budget_ceiling,
            gate_events=gate_events,
            verify_event=verify_event,
        )
        self._emit(loopdoctor_event)

        # Finding 5: do NOT publish session.last_run here with the RAW result — postrun
        # scoring still mutates the served artifact (URL neutralization, editor pass). A
        # client hitting /chat or /export in this window would get unvalidated output.
        # last_run is set only after _postrun_score_and_record() below, from the final text.
        _t_postrun = time.monotonic()  # T1: postrun stage timer (spans editor + publish)
        _outcome, result_output = self._postrun_score_and_record(
            session=session,
            result_output=result_output,
            outputs=outputs,
            base_client=base_client,
            judge_client=judge_client,
            use_llm=use_llm,
            _base_requirement=_base_requirement,
            _original_requirement=_original_requirement,
            _artifact_copied=_artifact_copied,
            _seed_text=_seed_text,
            _reducer_gaps=_reducer_gaps,
            _hc_cfg=_hc_cfg,
            _outcome=_outcome,
        )
        self._stage_add("postrun", _t_postrun)
        try:
            from studio.report_quality import build_review_status

            self._last_review = build_review_status(
                requirement,
                evidence_count=self._last_evidence_count,
                weak_evidence_count=self._last_weak_evidence_count,
                scorecard=self._last_scorecard_100,
                publish_issues=self._last_publish_issues,
                loopdoctor_checks=loopdoctor_event.checks,
            )
        except Exception:  # noqa: BLE001 - review state is advisory
            self._last_review = None
        try:
            from studio.run_metrics import (
                build_pass_economics,
                build_run_metrics,
                build_stop_report,
            )

            if cancelled and self._last_stop_reason == "validation_passed":
                self._last_stop_reason = "cancel_requested"
            _failed_validations = len(self._last_publish_issues) + sum(
                1 for c in loopdoctor_event.checks if c.get("status") != "pass"
            )
            self._checkpoints.append({
                "id": f"cp_pre_validation_{len(self._checkpoints) + 1}",
                "phase_id": "pre_validation",
                "artifact_path": self._read_workspace_artifact() and "artifact.md",
                "evidence_count": self._last_evidence_count,
                "weak_evidence_count": self._last_weak_evidence_count,
                "score": (
                    self._last_scorecard_100 or {}
                ).get("score", (self._last_scorecard_100 or {}).get("total")),
                "publish_issue_count": len(self._last_publish_issues),
                "loopdoctor_failure_count": sum(
                    1 for c in loopdoctor_event.checks if c.get("status") != "pass"
                ),
                "review_required": bool((self._last_review or {}).get("required")),
                "observation_ids": [row.get("id") for row in self._agent_trace],
            })
            _elapsed = time.perf_counter() - self._t0 if self._t0 is not None else 0.0
            _stop_report = build_stop_report(
                reason=self._last_stop_reason,
                tool_calls=self._tool_calls,
                failed_validations=_failed_validations,
                checkpoints=len(self._checkpoints) + 1,
                wall_s=_elapsed,
                token_cost=self._acc.total_tokens,
            )
            self._last_metrics = build_run_metrics(
                stop_report=_stop_report,
                evidence_count=self._last_evidence_count,
                weak_evidence_count=self._last_weak_evidence_count,
                tool_calls=self._tool_calls,
                tool_failures=self._tool_failures,
                review=self._last_review,
                scorecard=self._last_scorecard_100,
                pass_economics=build_pass_economics(
                    pass_ledger=getattr(self, "_finalize_pass_ledger", None),
                    token_cost=self._acc.total_tokens,
                ),
            )
            self._checkpoints.append({
                "id": f"cp_final_{len(self._checkpoints) + 1}",
                "phase_id": "final",
                "artifact_path": self._read_workspace_artifact() and "artifact.md",
                "evidence_count": self._last_evidence_count,
                "score": (
                    self._last_scorecard_100 or {}
                ).get("score", (self._last_scorecard_100 or {}).get("total")),
                "weakness_count": _failed_validations,
                "observation_ids": [row.get("id") for row in self._agent_trace],
                "stop_reason": self._last_stop_reason,
            })
            self._emit(MetricsEvent(metrics=self._last_metrics))
        except Exception:  # noqa: BLE001 - metrics are advisory
            self._last_metrics = None
        session.record_run(
            RunSnapshot(
                requirement=requirement,
                plan_steps=plan_step_dicts,
                topology=topology_map,
                loopdoctor_checks=loopdoctor_event.checks,
                budget_ceiling=session.budget_ceiling,
                result=result_output,
                cancelled=cancelled,
                evidence_matrix=self._last_evidence_matrix,
                scorecard_100=self._last_scorecard_100,
                review=self._last_review,
                metrics=self._last_metrics,
                agent_trace_jsonl=self._jsonl(self._agent_trace),
                checkpoints_jsonl=self._jsonl(self._checkpoints),
            )
        )

        # §14.4: the terminal `done` now lives in run() (emitted once after the epoch
        # loop). Stash this pass's final output + cancel flag so run() can build it,
        # and hand back the per-epoch outcome that drives continue/stop.
        self._last_result = result_output
        self._last_cancelled = cancelled
        return _outcome

    def _run_research_first_generation(
        self, *, session, client, base_client, requirement: str, base_requirement: str
    ) -> tuple[bool, str, str]:
        """Generate via ``studio.research_first`` instead of the phase loop (PLAN §16
        — the rebuild replaces hub/spoke as the generation core). Same
        ``(cancelled, final_output, seed_text)`` contract as ``_run_phase_loop`` so
        the caller doesn't need to know which one ran. Cold-start by design (D4):
        ignores any prior-artifact content — the hill-climb recording tail below
        this call still records into the SAME lineage (task_hash is computed from
        ``base_requirement`` unchanged by this method).

        Emits ``PhaseStartEvent`` for each of FRAME/RESEARCH/CLAIMS/WRITE/ASSEMBLE
        so the GUI stream isn't dead during generation (SPEC §4's per-phase slot);
        token accounting flows through the existing ``client``/``base_client``
        (no separate accounting path). Exceptions are fail-visible for opted-in
        research_first sessions; operators can choose the legacy phase loop before
        the run starts with ``STUDIO_DISABLE_RESEARCH_FIRST``.
        """
        from studio.research_first import generate_research_first
        from studio.workspace import Workspace
        from studio.workspace import workspace_root as _ws_root_fn

        def _emit_phase(stage: str, _data: dict) -> None:
            if stage in ("frame", "research", "claims", "write", "assemble"):
                self._emit(PhaseStartEvent(step_id=f"research_first_{stage}", n_agents=None))

        try:
            judge_client = self._build_judge_client(base_client)
            ws_root = self._workspace_root or _ws_root_fn()
            text = generate_research_first(
                requirement,
                client=client,
                judge_client=judge_client,
                workspace_root=ws_root,
                session_id=session.session_id,
                emit=_emit_phase,
            )
            self._used_research_first = True
            # HIGH (rf-reviewer): this call never wrote artifact.md itself, so on
            # an auto-improve continuation the seed carry-forward's stale file
            # was the ONLY thing on disk — the runner.py:2593 "prefer the file"
            # check then re-recorded pre-rebuild bloat over this fresh text.
            # Writing it now makes that check compare fresh-against-fresh (a
            # no-op) AND leaves a correct file for the NEXT continuation's own
            # seed. _pass_materialize_artifact's `not exists()` guard then
            # correctly skips — no double section-sync on this same text.
            try:
                Workspace(session.session_id, root=ws_root).root.joinpath(
                    "artifact.md"
                ).write_text(text, encoding="utf-8")
            except OSError as exc:
                _dbg(f"research_first: artifact.md write failed {exc!r} — continuing with in-memory text")
            return False, text, ""
        except Exception as exc:  # noqa: BLE001 — surface through Runner.run's error path
            _dbg(f"research_first: EXCEPTION {exc!r} — surfacing failure")
            self._used_research_first = False
            raise

    def _make_opportunity_recount(self, base_client: Any) -> Callable[[str], int | None]:
        """Return ``text -> #unsatisfied OR-sibling opportunity branches`` for the
        editor's soft-accept tie-breaker. Re-verifies the SAME cached task
        requirements against a candidate text. Returns ``None`` when the compliance
        re-check itself could not run (client down / LLM error / unparseable reply)
        — an UNKNOWN result, NOT a real zero. ``requirement_compliance_issues``
        normally fail-opens to an empty list, which ``len()`` would read as "0
        opportunities remaining" and the tie-breaker would misread as success; the
        ``strict=True`` mode raises instead so that false success is impossible."""
        def _recount(text: str) -> int | None:
            try:
                from studio.requirement_compliance import requirement_compliance_issues
                # Hard misses count too (run 1532): the structural retry's accept
                # rule is "unmet requirement branches went DOWN" — a retry that
                # fixes a fully-unmet OR group must register as progress even
                # though no OPPORTUNITY existed (all branches were hard misses).
                _, _hard, _opps = requirement_compliance_issues(
                    base_client, self._task_requirements or [], text or "", strict=True
                )
                return len(_hard) + len(_opps)
            except Exception:  # noqa: BLE001 — check unavailable → UNKNOWN, never a spurious 0
                return None
        return _recount

    def _postrun_score_and_record(
        self,
        *,
        session,
        result_output: str,
        outputs: dict[str, str],
        base_client,
        # Strong-model judge for the editor's presentation detection. The postrun
        # extraction silently dropped this from scope — the editor call's arg
        # evaluation raised NameError, the fail-open except ate it, and the whole
        # editor/presentation stage never ran on ANY live run (editor=0.00s).
        # Default None (not base_client) so a missed caller degrades visibly in
        # tests rather than silently judging with the weak generator.
        judge_client=None,
        use_llm: bool,
        _base_requirement: str,
        _original_requirement: str,
        _artifact_copied: bool,
        _seed_text: str,
        _reducer_gaps: list[str],
        _hc_cfg: dict,
        _outcome: EpochResult,
    ) -> tuple[EpochResult, str]:
        """Post-run pipeline (DESIGN §14): score -> synthesize -> repair_lints ->
        neutralize URLs -> mine weaknesses -> epoch gate -> rubric/adjusted score ->
        record -> emit HillClimbEvent. PLAN §2 S2: the pass SEQUENCE now lives in
        ``studio.finalize.PASSES`` (one ordered list, one fail-open+timing+diagnostic
        wrapper) — stage order and skip conditions are unchanged from the
        pre-extraction inline version; this method keeps only the setup that must run
        BEFORE any pass can (score source selection, task_hash/store, workspace
        paths) and the tail cosmetic pass that must run AFTER every pass regardless
        of outcome. Returns ``(outcome, result_output)`` — ``result_output`` may be
        mutated by any pass.
        """
        # Hill climb post-run: score output, mine weaknesses, record, emit HillClimbEvent.
        # Runs regardless of hill_climb_config so task_hash-based lookup always has data.
        try:
            from studio.task_runs import (
                TaskRunStore,
                base_identity as _base_identity,
                task_hash as _task_hash,
            )
            # Embedder wired so this run's requirement is embedded on record()
            # → future runs can find it via similar_runs() (R10).
            _store = TaskRunStore(embedder=self._embedder)
            # Hash the BASE requirement (goal-invariant identity) — must match the
            # auto_improve seed-lookup hash above so a run records under the same
            # task_hash it seeded from. PLAN item 5: base_identity strips the GUI
            # continuation wrapper so a continued run records into the prior lineage.
            _thash = _task_hash(_base_identity(_base_requirement))
            from studio.workspace import workspace_root as _ws_root_fn
            _effective_ws_root = self._workspace_root or _ws_root_fn()
            _art_file = _effective_ws_root / session.session_id / "artifact.md"
            _art_path = str(_art_file)
            # Score the PERSISTED artifact, not the loose result_output. The final phase's
            # returned text and the artifact.md it wrote to disk can diverge (a phase may
            # return a short status string while the full report lives in the file). Since
            # auto-improve seeds the NEXT run from artifact.md, scoring anything else means
            # scoring one text and carrying forward another — the cause of phantom scores
            # (e.g. a recorded 0.50 on a report that re-scores 0.80). Prefer the file when
            # it is at least as substantial as the return; fall back to result_output.
            _scored_text = _pick_scored_source(
                _art_file,
                result_output,
                rebuild_generated=getattr(self, "_used_research_first", False),
            )
            # Scoring and weakness mining must use the RAW client (base_client), not the
            # ToolAugmentedClient. When the scorer has web_search available, it calls it
            # to verify citations — fabricated or paywalled articles score 0.0 even when
            # the output quality is genuinely good. The scorer is an LLM judge, not a
            # research agent; it must not make live web calls. (score_and_mine_weaknesses
            # uses state.base_client directly for this — no separate alias needed.)
            # Check scored text URLs against web cache — real (cached) URLs get marked
            # as verified so the judge doesn't penalise genuine citations as fabricated.
            _verified_urls: list[str] = _verified_urls_from_cache(_scored_text or "")
            _is_last_epoch = (self._epoch == 0) or (
                self._epoch >= int(_hc_cfg.get("max_epochs", 5) or 5)
            )

            from studio import finalize
            _state = finalize.FinalizeState(
                runner=self,
                session=session,
                outputs=outputs,
                base_client=base_client,
                judge_client=judge_client,
                use_llm=use_llm,
                base_requirement=_base_requirement,
                original_requirement=_original_requirement,
                artifact_copied=_artifact_copied,
                reducer_gaps=_reducer_gaps,
                hc_cfg=_hc_cfg,
                store=_store,
                thash=_thash,
                effective_ws_root=_effective_ws_root,
                art_file=_art_file,
                art_path=_art_path,
                is_last_epoch=_is_last_epoch,
                scored_text=_scored_text,
                result_output=result_output,
                verified_urls=_verified_urls,
                seed_text=_seed_text,
                outcome=_outcome,
                rebuild_generated=getattr(self, "_used_research_first", False),
            )
            _state = finalize.run_passes(_state)
            _outcome = _state.outcome
            result_output = _state.result_output
        except Exception:  # noqa: BLE001 — scoring failure must never crash the run
            pass
        # Cosmetic pass on the SERVED copy only — after all scoring/gating and the
        # task_runs.db record above ran on the raw text. Fail-open (see module).
        from studio.markdown_format import beautify_markdown

        result_output = beautify_markdown(result_output)
        return _outcome, result_output

    # -- helpers -----------------------------------------------------------

    def _build_client(self) -> LLMClient:
        """Build the run's LLMClient — injected factory in tests, else from spec."""
        if self._client_factory is not None:
            return self._client_factory(self._on_usage)
        backend = resolve_backend(self._session.llm_spec)
        # session info may be filled lazily; ensure label/model present
        return build_chat_client(backend, self._on_usage)

    def _build_judge_client(self, base_client: LLMClient) -> LLMClient:
        """Strong-model JUDGE for presentation DETECTION (form/diagram warrant). The weak
        generation model over-affirms "structure" on any section that names components, so
        detection runs on a capable model (``session.judge_spec``, default ``haiku``) while
        generation stays on the session's model. Test mode (an injected client factory) and
        any resolve/build failure fall back to ``base_client`` so detection still runs,
        degraded — never a crash, never a hard dependency on the judge backend."""
        if self._client_factory is not None:
            return base_client  # tests inject ONE client; do not build a real judge backend
        spec = getattr(self._session, "judge_spec", None) or {"profile": "haiku"}
        try:
            return build_chat_client(resolve_backend(spec), self._on_usage, temperature=0.0)
        except Exception:  # noqa: BLE001 — judge backend unavailable → degrade to generation model
            return base_client

    def _maybe_tool_augment(
        self, client: LLMClient, *, artifact_path: Path | None = None
    ) -> LLMClient:
        """Wrap ``client`` in the tool loop (web_search + jailed file tools) when
        tools are enabled.

        Gated on ``session.tools_enabled`` AND web_toolkit being importable; in
        tests an injected ``search_fn`` (set via ``self._search_fn``) bypasses the
        import so no network is hit. The file tools are confined to a per-session
        :class:`~studio.workspace.Workspace` (realpath jail). When ``artifact_path``
        is provided, the artifact OCC tools (read_artifact / patch_artifact) are
        also offered. Returns the bare client when tools are off.
        """
        enabled = self._session.tools_enabled and (
            self._search_fn is not None or web_toolkit_available()
        )
        if not enabled:
            return client
        model_id = str(
            (self._session.llm_info or {}).get("model")
            or (self._session.llm_spec or {}).get("model")
            or ""
        )
        model_profile = resolve_model_profile(model_id)
        workspace = Workspace(self._session.session_id, root=self._workspace_root)
        return ToolAugmentedClient(
            client,
            on_tool_call=self._emit_tool_call,
            on_tool_result=self._emit_tool_result,
            step_id_getter=lambda: self._current_step_id,
            search_fn=self._search_fn,
            fetch_fn=self._fetch_fn,
            workspace=workspace,
            artifact_path=artifact_path,
            max_iters=model_profile.max_tool_iters,
            max_searches=model_profile.max_searches,
            max_successful_fetches=model_profile.max_successful_fetches,
            auto_fetch_top_results=model_profile.auto_fetch_top_results,
            # Citation-grade slice: a triage-sized snippet forces the model to
            # paraphrase quotes it should copy verbatim. One section window per
            # page is what the model can actually attend to per work unit.
            auto_fetch_page_chars=model_profile.section_window_chars,
        )

    def _emit_tool_call(self, sid: str, tool: str, args: dict[str, Any]) -> None:
        self._pending_tool_args.append({
            "step_id": sid,
            "tool": tool,
            "args_redacted": self._redact_tool_args(args),
        })
        self._emit(ToolCallEvent(step_id=sid, tool=tool, args=args))

    def _emit_tool_result(
        self, sid: str, tool: str, summary: str, n: int, notice: str, rejected: bool
    ) -> None:
        self._tool_calls += 1
        if rejected or notice or "error" in (summary or "").lower():
            self._tool_failures += 1
        args_redacted: dict[str, Any] = {}
        for i, pending in enumerate(self._pending_tool_args):
            if pending.get("step_id") == sid and pending.get("tool") == tool:
                args_redacted = dict(pending.get("args_redacted") or {})
                del self._pending_tool_args[i]
                break
        self._agent_trace.append({
            "id": f"obs_{len(self._agent_trace) + 1}",
            "step_id": sid,
            "tool": tool,
            "args_redacted": args_redacted,
            "allowed": not rejected,
            "requires_approval": False,
            "status": "error" if rejected or notice or "error" in (summary or "").lower() else "ok",
            "message": notice,
            "result_summary": summary,
            "validation_status": "fail" if rejected else "pass",
            "validation_issues": [notice] if notice else [],
            "retry_count": 0,
            "n_results": n,
            "ts": time.time(),
        })
        self._emit(ToolResultEvent(
            step_id=sid,
            tool=tool,
            summary=summary,
            n_results=n,
            notice=notice,
            rejected=rejected,
        ))

    @staticmethod
    def _jsonl(rows: list[dict[str, Any]]) -> str:
        return "".join(_json.dumps(row, ensure_ascii=False) + "\n" for row in rows)

    @staticmethod
    def _redact_tool_args(args: dict[str, Any]) -> dict[str, Any]:
        def clean(key: str, value: Any) -> Any:
            if any(s in key.lower() for s in ("api_key", "token", "password", "secret")):
                return "[redacted]"
            if isinstance(value, str):
                return value if len(value) <= 200 else value[:200] + "...[truncated]"
            if isinstance(value, dict):
                return {str(k): clean(str(k), v) for k, v in value.items()}
            if isinstance(value, list):
                return [clean(key, v) for v in value[:20]]
            return value

        return {str(k): clean(str(k), v) for k, v in (args or {}).items()}

    def _gate_event_for(self, step_id: str, output: str) -> GateEvent:
        """Run the phase output through the security gate as a text proposal."""
        proposal = {"type": "phase_output", "content": output, "description": output[:200]}
        return run_gate_event(f"phase:{step_id}", proposal, cwd=self._sandbox_cwd)

    def _done_event(self, final_output: str, *, cancelled: bool) -> DoneEvent:
        elapsed = time.perf_counter() - self._t0 if self._t0 is not None else 0.0
        # T1 baseline instrumentation (PLAN §1): whole-run per-stage summary + total.
        # NOTE: these spans NEST, don't sum to `total` — `postrun` wraps editor+publish
        # (runner.py:2148), and each phase's `spoke` wraps its reducer+prefetch (see the
        # per-phase `timing` on phase_done events for that inner breakdown).
        _timing = {**self._stage_timings, "total": elapsed}
        _dbg("T1 stage timings (nested, not additive — see NOTE above) — " + ", ".join(
            f"{k}={_timing[k]:.2f}s" for k in sorted(_timing)
        ))
        return DoneEvent(
            total_tokens=self._acc.total_tokens,
            input=self._acc.total_input_tokens,
            output=self._acc.total_output_tokens,
            estimated=self._acc.tokens_estimated,
            wall_s=elapsed,
            result=final_output,
            cancelled=cancelled,
            result_path=self._write_result(final_output),
            scorecard_100=self._last_scorecard_100,
            review=self._last_review,
            metrics=self._last_metrics,
            timing=_timing,
        )

    def _read_workspace_artifact(self) -> str:
        """Return content of artifact.md from the workspace if it exists."""
        try:
            ws = Workspace(self._session.session_id, root=self._workspace_root)
            artifact = ws.root / "artifact.md"
            if artifact.exists():
                return artifact.read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _persist_partial_run(self, requirement: str, exc: Exception) -> bool:
        """Snapshot partial artifact state when a run dies mid-flight (entry 166).

        When the LLM backend (or anything else) dies mid-run, the exception unwinds to
        the top-level catch and NOTHING was recorded — all partial research was lost and
        the next run cold-started. This records a ``failed_partial`` row so the next run
        of this task carries the partial work forward via ``latest_with_content``.

        Returns True iff a partial was saved. The whole body is guarded: ANY exception
        here returns False — persistence must never mask the original failure.
        """
        try:
            artifact = self._read_workspace_artifact()
            # Skeleton gate: a skeleton-only/empty artifact is worse than a cold start.
            if not _artifact_has_real_content(artifact):
                return False
            from studio.task_runs import (
                TaskRun,
                TaskRunStore,
                base_identity as _base_identity,
                task_hash as _task_hash,
            )
            from studio.workspace import workspace_root as _ws_root_fn
            # SAME base-identity hash normal recording uses (_postrun_score_and_record:
            # _thash = _task_hash(_base_identity(_base_requirement))). run()'s ``requirement``
            # IS that base requirement — goal/template injection happens inside _run_inner —
            # so the partial row lands in the lineage carry-forward queries, not a fork.
            thash = _task_hash(_base_identity(requirement))
            ws_root = self._workspace_root or _ws_root_fn()
            art_path = str(ws_root / self._session.session_id / "artifact.md")
            store = TaskRunStore(embedder=self._embedder)
            store.record_versioned(
                TaskRun(
                    task_hash=thash,
                    session_id=self._session.session_id,
                    version=0,  # record_versioned allocates the real next version
                    score=0.0,
                    weaknesses=[],  # death reason must NOT enter the weakness feed-forward
                    artifact_path=art_path,
                    requirement=requirement,
                    result_text=artifact,
                    config={"failure": str(exc)},  # preserved for human inspection only
                    status="failed_partial",
                )
            )
            return True
        except Exception:  # noqa: BLE001 - persistence must never mask the original error
            return False

    def _write_result(self, final_output: str) -> str:
        """Save the final result to the session workspace → its absolute path.

        Best-effort: a write failure returns "" (the result still rides in the
        ``done`` event), and an empty result is not written.
        """
        if not final_output.strip():
            return ""
        try:
            ws = Workspace(self._session.session_id, root=self._workspace_root)
            ws.write("result.md", final_output)
            return str(ws.root / "result.md")
        except Exception:  # noqa: BLE001 - saving is auxiliary; never break `done`
            return ""


