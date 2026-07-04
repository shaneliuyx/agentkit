"""Patch-based publish revision (PLAN §9 step 3 / §8.1).

Replaces the whole-document publish rewrite — the stage that shrank a report
21.4KB→9.5KB on gemma — with bounded fragment+anchor patches (Step-DeepResearch
patch action). The model names a defect and emits ONLY the changed fragment plus
an anchor; the tool applies it via fuzzy matching. Fuzzy application is
load-bearing: Aider measured 9x more edit failures without it. Whole-doc-shaped
patches are rejected so a weak model cannot smuggle a full rewrite through the
patch channel.

The actual mutation reuses the house patcher (``agentkit.artifacts.patcher.
reduce_patches``); this module only adds the fuzzy anchor-resolution + prompt
that the reducer's exact-match contract lacks. The caller keeps the §9-step-1
shrink/de-cite guard as the safety net after application.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# Reject a "patch" whose replacement is a large fraction of the doc — that is a
# whole-doc rewrite wearing a patch costume (Aider: weak models do this).
_WHOLE_DOC_FRACTION = 0.30
_ANCHOR_CONF_FLOOR = 0.80  # never apply a fuzzy match below this confidence
_ANCHOR_MARGIN = 0.05      # fuzzy best must beat 2nd-best by this, else ambiguous
# Aggregate budget: many <30% patches can still sum to a full rewrite (P2-a).
_MAX_PATCHES = 12
_AGGREGATE_FRACTION = 0.50  # total replacement span over this → reject the revision

_PATCH_RE = re.compile(r"<<<ANCHOR\n(.*?)\n===REPLACE\n(.*?)\n>>>END", re.DOTALL)

PATCH_FORMAT = (
    "Emit ONLY patch blocks in this EXACT plain-text format, nothing else "
    "(plain text beats JSON for mid-sized models — Aider edit-format benchmark):\n"
    "<<<ANCHOR\n"
    "(2-6 consecutive lines copied VERBATIM from the report that locate the edit)\n"
    "===REPLACE\n"
    "(the replacement text for those exact lines)\n"
    ">>>END\n"
    "Rules: each patch is a SMALL bounded fragment, NEVER the whole document. "
    "Do not restate unchanged sections. Copy anchor lines character-for-character. "
    "Preserve every citation URL. Fix ONLY the listed defects."
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def resolve_anchor(doc: str, anchor: str) -> tuple[str | None, float]:
    """Return (exact doc substring matching ``anchor``, confidence).

    The match must be UNIQUE — otherwise a patch can land in the wrong section
    while preserving length/citations/score and slip past the final guard
    (codex P1-b). Exact: exactly one occurrence. Fuzzy: the best window must
    clear the floor, beat the 2nd-best by ``_ANCHOR_MARGIN``, AND be unique in
    the doc. Ambiguous or low-confidence → ``(None, conf)``; the caller skips."""
    if not anchor.strip():
        return None, 0.0
    n = doc.count(anchor)
    if n == 1:
        return anchor, 1.0
    if n > 1:
        return None, 1.0  # ambiguous exact match → refuse (would hit first occurrence)
    na = _norm(anchor)
    lines = doc.splitlines(keepends=True)
    span = max(1, anchor.count("\n") + 1)
    best_text, best_conf, second_conf = None, 0.0, 0.0
    for i in range(len(lines)):
        window = "".join(lines[i : i + span])
        conf = 1.0 if _norm(window) == na else SequenceMatcher(None, _norm(window), na).ratio()
        if conf > best_conf:
            second_conf, best_conf, best_text = best_conf, conf, window
        elif conf > second_conf:
            second_conf = conf
    if (
        best_text is not None
        and best_conf >= _ANCHOR_CONF_FLOOR
        and best_conf - second_conf >= _ANCHOR_MARGIN
        and doc.count(best_text) == 1
    ):
        return best_text, round(best_conf, 3)
    return None, round(best_conf, 3)


def apply_publish_patches(doc: str, raw: str) -> tuple[str, dict, list[str]]:
    """Apply the model's fragment+anchor patches to ``doc``.

    Returns ``(new_doc, stats, unresolved_anchors)``. ``unresolved_anchors`` lets
    the caller do ONE bounded retry with corrected anchors; there is no loop —
    every path terminates in apply-or-skip (PLAN §9 risk 1: no patch-gate
    deadlock)."""
    from agentkit.artifacts.patcher import DocPatch, reduce_patches  # noqa: PLC0415

    patches = _PATCH_RE.findall(raw)
    stats = {"emitted": len(patches), "applied": 0, "whole_doc_rejected": 0,
             "anchor_fail": 0, "conflict_skipped": 0, "aggregate_rejected": False}
    # P2-a: many <30% patches can still sum to a near-full rewrite. Reject the
    # whole revision (no-op) if the patch count or total replacement span blows
    # the budget — keep the prior text.
    total_replacement = sum(len(r) for _a, r in patches)
    if patches and (len(patches) > _MAX_PATCHES or total_replacement > _AGGREGATE_FRACTION * len(doc)):
        stats["aggregate_rejected"] = True
        return doc, stats, []
    unresolved: list[str] = []
    for anchor, replacement in patches:
        if len(replacement) > _WHOLE_DOC_FRACTION * len(doc):
            stats["whole_doc_rejected"] += 1
            continue
        exact, _conf = resolve_anchor(doc, anchor)
        if exact is None:
            stats["anchor_fail"] += 1
            unresolved.append(anchor)
            continue
        # Apply one-by-one so a conflict (anchor destroyed by an earlier patch)
        # is SKIPPED, not appended as an orphan `<!-- conflict -->` payload that
        # grows the doc past the shrink guard (codex P1-a).
        res = reduce_patches(doc, [[DocPatch(op="replace", anchor=exact, content=replacement, source="publish")]])
        if res.conflicts:
            stats["conflict_skipped"] += 1
            continue
        doc = res.text
        stats["applied"] += 1
    return doc, stats, unresolved


def build_patch_prompt(requirement: str, draft: str, issues, evidence: str) -> str:
    issue_block = "\n".join(f"- {i}" for i in issues) or "- (general publish polish)"
    return (
        "You are fixing specific publish-readiness defects in a research report "
        "WITHOUT rewriting it.\n\n"
        f"USER REQUEST:\n{requirement}\n\n"
        f"DEFECTS TO FIX:\n{issue_block}\n\n"
        f"{PATCH_FORMAT}\n\n"
        f"EVIDENCE (cite only URLs that appear here):\n{evidence}\n\n"
        f"=== REPORT ===\n{draft}\n=== END REPORT ==="
    )
