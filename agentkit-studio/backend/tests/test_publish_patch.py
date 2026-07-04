"""§9 step 3 — patch-based publish revision (studio.publish_patch)."""
from __future__ import annotations

import pytest

from studio.publish_patch import apply_publish_patches, build_patch_prompt, resolve_anchor

# Production-shaped: large enough that a one-line fragment patch is well under
# the 30% whole-doc threshold (real reports are ~20KB; a tiny fixture would trip
# the whole-doc guard on a legitimate single-line edit).
_FILLER = "\n".join(f"Background sentence number {i} about agent development loops." for i in range(60))
DOC = (
    "# Report\n\n"
    "## Background and Context\n"
    f"{_FILLER}\n\n"
    "## Key Findings\n"
    "Pi and Craft both support agent loops (https://example.com/a).\n"
    "The reducer applies bounded patches to the artifact.\n\n"
    "## References\n"
    "- https://example.com/a\n"
)


def _patch(anchor: str, replace: str) -> str:
    return f"<<<ANCHOR\n{anchor}\n===REPLACE\n{replace}\n>>>END"


def test_resolve_exact():
    text, conf = resolve_anchor(DOC, "The reducer applies bounded patches to the artifact.")
    assert conf == 1.0 and text in DOC


def test_resolve_whitespace_fuzzy():
    # extra spaces in the anchor still resolve to the exact doc substring
    text, conf = resolve_anchor(DOC, "The   reducer  applies bounded patches to the artifact.")
    assert text is not None and conf >= 0.8 and text in DOC


def test_resolve_below_floor_returns_none():
    text, conf = resolve_anchor(DOC, "completely unrelated sentence about penguins zzz")
    assert text is None and conf < 0.8


def test_apply_replaces_fragment():
    raw = _patch(
        "The reducer applies bounded patches to the artifact.",
        "The reducer applies bounded, fuzzy-matched patches to the artifact.",
    )
    new, stats, unresolved = apply_publish_patches(DOC, raw)
    assert "fuzzy-matched" in new
    assert stats["applied"] == 1 and unresolved == []


def test_whole_doc_shaped_patch_rejected():
    huge = "X" * int(len(DOC) * 0.35)  # >30% per-patch but <50% aggregate
    raw = _patch("## Key Findings", huge)
    new, stats, unresolved = apply_publish_patches(DOC, raw)
    assert stats["whole_doc_rejected"] == 1
    assert stats["applied"] == 0
    assert new == DOC  # unchanged


def test_failed_anchor_terminates_as_noop():
    raw = _patch("this anchor does not exist in the document at all zzz", "replacement")
    new, stats, unresolved = apply_publish_patches(DOC, raw)
    assert stats["anchor_fail"] == 1
    assert unresolved  # surfaced for the caller's one bounded retry
    assert new == DOC  # no-op, no crash, no loop


def test_no_patches_is_noop():
    new, stats, unresolved = apply_publish_patches(DOC, "no patch blocks here")
    assert new == DOC and stats["emitted"] == 0


def test_conflicting_patches_never_append_orphan():
    # P1-a: two patches on the same anchor must NOT leave a `<!-- conflict -->`
    # orphan payload that grows the doc past the shrink guard.
    raw = _patch("The reducer applies bounded patches to the artifact.", "First rewrite.") + "\n" + \
        _patch("The reducer applies bounded patches to the artifact.", "Second rewrite payload.")
    new, stats, _ = apply_publish_patches(DOC, raw)
    assert "<!-- conflict" not in new
    assert "Second rewrite payload." not in new  # the losing patch is dropped, not appended
    assert stats["applied"] == 1


def test_ambiguous_anchor_is_skipped():
    # P1-b: a repeated boilerplate anchor must not be applied to the first hit.
    doc = "## A\nSee the note below.\n## B\nSee the note below.\n## C\ntail\n"
    raw = _patch("See the note below.", "REPLACED")
    new, stats, unresolved = apply_publish_patches(doc, raw)
    assert new == doc and stats["applied"] == 0
    assert resolve_anchor(doc, "See the note below.")[0] is None  # ambiguous → None


def test_aggregate_budget_rejects_split_rewrite():
    # P2-a: many small patches summing to a near-full rewrite → whole revision rejected.
    raw = "\n".join(_patch(f"Background sentence number {i} about agent development loops.",
                           f"Rewritten sentence {i} that is noticeably longer than the original line was.")
                     for i in range(14))
    new, stats, _ = apply_publish_patches(DOC, raw)
    assert stats["aggregate_rejected"] is True
    assert new == DOC and stats["applied"] == 0


def test_prompt_carries_defects_and_format():
    p = build_patch_prompt("req", DOC, ["fix citation X"], "evidence here")
    assert "fix citation X" in p and "<<<ANCHOR" in p and "NEVER the whole document" in p


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
