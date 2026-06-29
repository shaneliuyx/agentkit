"""studio.artifact_text — artifact-text sanitizers, gap detection, and the
in-run synthesis / lint-repair passes.

Extracted from ``studio.runner`` (SRP): these operate purely on artifact text
(plus an optional ``LLMClient`` for the two LLM passes) and hold no runner state.
``studio.runner`` re-exports every name here so existing
``from studio.runner import _detect_gaps`` (etc.) imports keep working.
"""

from __future__ import annotations

import re as _re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentkit.types import LLMClient


def _synthesize_analysis(
    text: str, client: "LLMClient | None", requirement: str
) -> tuple[str, bool]:
    """Add a synthesis/analysis layer to a grounded, additively-merged report (PLAN item 1A).

    The additive reducer is forbidden from rewriting (anti-regression §14.6), so its output
    is grounded but reads as stitched-together quotations with no interpretation. This
    SEPARATE pass weaves in analysis and cross-source comparison WITHOUT regression: it must
    keep every citation and every sourced fact. The rewrite is REJECTED (original kept) if it
    drops any URL that was present or comes back materially shorter — synthesis, never loss.
    Returns ``(text, changed)``.
    """
    src = text or ""
    if not src.strip() or client is None:
        return src, False
    urls_before = set(_re.findall(r"https?://\S+", src))
    prompt = (
        "You are a research analyst. The DRAFT report below is well-sourced and cited but "
        "reads as stitched-together quotations with little original analysis.\n\n"
        "Rewrite it to ADD a synthesis/analysis layer: explain what the findings MEAN "
        "together, COMPARE and CONTRAST the sources, and surface implications, patterns, and "
        "trade-offs in your own words.\n\n"
        "HARD RULES (a violation makes the rewrite worthless):\n"
        "- Keep EVERY URL and citation exactly as it appears — never drop or alter one.\n"
        "- Do not remove any sourced fact, quote, or section heading.\n"
        "- The result must be at least as long as the draft (you are ADDING analysis).\n"
        "- Output ONLY the improved report, with no preamble or commentary.\n\n"
        f"TASK: {requirement[:400]}\n\n"
        f"DRAFT:\n{src}"
    )
    try:
        resp = client.chat([{"role": "user", "content": prompt}])
        out = (getattr(resp, "text", "") or "").strip()
    except Exception:  # noqa: BLE001 — synthesis is best-effort; never break the run
        return src, False
    if not out:
        return src, False
    urls_after = set(_re.findall(r"https?://\S+", out))
    # Reject regressions: a dropped citation, or a materially shorter document.
    if not urls_before <= urls_after or len(out) < int(0.9 * len(src)):
        return src, False
    return out, True


#: A fenced ```mermaid block, captured whole for block-level repair + deterministic splice.
_MERMAID_BLOCK_RE = _re.compile(r"```mermaid\b.*?```", _re.DOTALL)


def _repair_lints(
    text: str, client: "LLMClient | None", requirement: str
) -> tuple[str, bool]:
    """Repair a malformed mermaid diagram in the OUTPUT — root-cause fix for the reported
    served-broken-diagram bug (DESIGN §14.6).

    Why on the output, in-run: the reducer is told to "preserve verbatim", and the §14.6
    repair clause is built only from ``lint_artifact(_seed_text)`` (the SEED). A defect BORN
    in a worker/reducer this run (cold start, no seed — traced in agent_io.jsonl of
    s_eeff6e911cd6: worker e1:s1 generated the glued edge, the reducer preserved it) is never
    surfaced, so the verbatim rule keeps it. The lint feeds the NEXT epoch's seed (which would
    self-heal), but a single-epoch run never gets an epoch 2. This pulls the repair in-run.

    Why BLOCK-LEVEL, not whole-document: verified on the real 68 KB artifact, asking the model
    to echo the whole corrected document TRUNCATED it to ~half (36 KB) — a toy 557-char test
    passed 5/5 but hid this (the convenient-test-set trap). So instead extract ONLY the broken
    ```mermaid block (~0.5 KB), repair just that, and splice the corrected block back
    DETERMINISTICALLY (``str.replace``). The model stays in its reliable small-input regime;
    the surrounding document — all prose and citations — is byte-for-byte untouched.

    Accepts only if the splice strictly reduces the lint count (and the model's reply still
    parses as a glued-edge-free mermaid block). Returns ``(text, changed)``.
    """
    from studio.artifact_lint import _MERMAID_GLUED_EDGE, lint_artifact
    src = text or ""
    if not src.strip() or client is None:
        return src, False
    before = len(lint_artifact(src))
    if before == 0:
        return src, False
    out = src
    for block in _MERMAID_BLOCK_RE.findall(src):
        if not _MERMAID_GLUED_EDGE.search(block):
            continue  # this diagram is well-formed — leave it
        prompt = (
            "Fix the syntax errors in this Mermaid diagram. A node glued to an edge label "
            "(e.g. `A|label| B`) is missing its link operator and must become `A -->|label| "
            "B`. Change ONLY what is needed to make it valid; keep every node and edge. "
            "Output ONLY the corrected ```mermaid code block, nothing else.\n\n" + block
        )
        try:
            reply = (getattr(client.chat([{"role": "user", "content": prompt}]), "text", "") or "").strip()
        except Exception:  # noqa: BLE001 — repair is best-effort; never break the run
            continue
        m = _MERMAID_BLOCK_RE.search(reply)
        fixed = m.group(0) if m else ""
        if not fixed or _MERMAID_GLUED_EDGE.search(fixed):
            continue  # model didn't return a clean block — keep the original
        out = out.replace(block, fixed, 1)
    # Accept only on a strict improvement; the deterministic splice cannot touch prose/URLs.
    if out != src and len(lint_artifact(out)) < before:
        return out, True
    return src, False


def _strip_preamble(text: str) -> str:
    """Strip any non-document preamble before the artifact's first markdown
    heading (DESIGN §11.4). A reducer occasionally prepends review commentary
    ('The artifact is complete... Weaknesses addressed: ✅... Remaining concern:')
    instead of emitting the document. That commentary belongs in the chat (the
    surfaced _unresolved_block), NEVER in the artifact — and the grow-only ratchet
    would otherwise LOCK it into the seed forever (a clean-up that shortens the doc
    is rejected as a regression). Applied at every artifact boundary (seed, reducer
    read, write-back) so inherited corruption is sanitized and cannot propagate.

    Strips everything before the first line beginning with '#'. No heading found =>
    return unchanged (never destroy a genuinely heading-less document).

    Also removes inherited '<!-- conflict(...): anchor not found -->' markers that
    reduce_patches emitted on a missing anchor in an EARLIER version (before the
    anchor-demotion fix) and that the additive merge then froze into the seed forever.
    Anchor-demotion prevents NEW markers; this strips the old ones (the content beneath
    a marker is kept — only the noise comment line is removed).
    """
    import re
    text = re.sub(r"[ \t]*<!--\s*conflict.*?-->[ \t]*\n?", "", text)
    m = re.search(r"^#", text, flags=re.MULTILINE)
    return text[m.start():] if m else text


def _merge_missing_sections(text: str, sections: list[str]) -> str:
    """Append each template section ABSENT from *text* as an empty heading +
    placeholder, giving a seeded hill-climb run a PATCH_TARGET for it (DESIGN §14.6).

    A seeded run keeps the seed's structure: the reducer PATCHES existing headings
    and never injects a missing one, so a required section absent from the seed is
    mined as a weakness every epoch but never created. Laying down an empty heading
    closes that loop — the additive pipeline then fills it. Concept-aware
    (``sections_present``) so a renamed-but-present section ("Conclusion and Best
    Practices" ≈ "Conclusion and Recommendations") is NOT duplicated. Returns *text*
    unchanged when nothing is missing.
    """
    if not sections:
        return text
    from studio.rubric import sections_present
    present = {s.lower() for s in sections_present(text, sections)}
    missing = [s for s in sections if s.lower() not in present]
    if not missing:
        return text
    add = "\n\n".join(f"## {s}\n\n_(to be completed)_" for s in missing)
    return text.rstrip() + "\n\n" + add + "\n"


def _detect_gaps(artifact_text: str) -> list[tuple[str, str]]:
    """Detect gaps in the merged deliverable (DESIGN §11.4).

    A gap is a section that is **empty or a placeholder**. Each gap is tagged with
    its nearest **top-level** section (h1/h2) so the caller can consolidate by
    section before sizing agents — a report has a bounded number of top-level
    sections, so the worklist (and thus agent count) is structurally bounded.

    Returns (top_level_section, message) tuples. Routed by the caller: non-last
    phase → consolidated to distinct sections, handed to the next phase via the
    ledger; last phase → messages carried to the next run as weaknesses.

    NOTE (2026-06-27 fix): the old "substantive prose but no inline http → gap"
    rule mis-flagged every well-formed section of a properly-cited report (whose
    citations live in a References section, not inline) — ~74 false gaps that
    exploded agent sizing. Removed: prose with content is NOT a gap; only
    empty/placeholder sections are.
    """
    gaps: list[tuple[str, str]] = []
    parts = _re.split(r'(?m)^(#{1,6}\s+.+)$', artifact_text)
    # parts = [pre, heading1, body1, heading2, body2, ...]
    it = iter(parts[1:])
    top = "(document root)"
    for heading in it:
        body = next(it, '')
        h, b = heading.strip(), body.strip()
        level = len(h) - len(h.lstrip('#'))
        if level <= 2:
            top = h  # nearest h1/h2 owns the sub-sections beneath it
        low = b.lower()
        if not b or '_(pending' in low or 'placeholder' in low:
            gaps.append((top, f"{h}: empty/placeholder — needs sourced content"))
    return gaps


def _gap_sections(gaps: list[tuple[str, str]]) -> list[str]:
    """Consolidate gaps to distinct top-level sections (DESIGN §11.4).

    Consolidation shrinks the LEDGER input (sections, not raw gap count) that
    drives ``_max_workers`` (concurrency) and the hub prompt's ledger block.
    It does NOT by itself bound the spoke COUNT: the fan-out derives breadth
    from ``_facets`` (STAR/MESH) and the upstream item count (MAP), which read
    prose/lists, not section count. The hard breadth cap is ``run_plan``'s
    ``max_agents`` arg (the 2026-06-27 gap-flood fix). Order-preserving.
    """
    seen: set[str] = set()
    out: list[str] = []
    for top, _ in gaps:
        if top not in seen:
            seen.add(top)
            out.append(top)
    return out


def _unresolved_block(weaknesses: list[str], repeat_failed: set[str], limit: int) -> str:
    """User-facing 'known unresolved issues' block (DESIGN §11.4 — surface, don't hide).

    A repeat-failure (recorded in >= ``limit`` prior runs) still present in this
    run's weaknesses was attempted again — including the last phase — and remains
    open. It is appended below the result shown in the chat window so the user
    knows what could not be resolved, instead of being silently dropped. Returns
    '' when nothing is still open.
    """
    from studio.task_runs import _norm_weakness
    still_open = [w for w in weaknesses if _norm_weakness(w) in repeat_failed]
    if not still_open:
        return ""
    return (
        "\n\n---\n\n## ⚠️ Known unresolved issues\n\n"
        f"_Attempted across {limit}+ runs (including this run's final phase) and "
        "still open — surfaced, not hidden:_\n\n"
        + "\n".join(f"- {w}" for w in still_open)
    )


#: A document is "complete" if its last non-space char closes a sentence/structure.
#: Used to reject a truncated artifact in favor of a complete synthesis (see the
#: result_output selection below). Markdown reports legitimately end on a period,
#: list/table row, fence, blockquote, or heading underline — so the set is permissive;
#: a bare cutoff mid-word/URL (the truncation symptom) fails it.
_CLEAN_END_CHARS = frozenset(".!?)]\"'`|>*-_")


def _ends_cleanly(text: str) -> bool:
    """True if ``text`` ends at a sentence/structure boundary (not truncated mid-line).

    Research reports end with reference lines like "- Author. 'Title.' https://url"
    where the last WORD is a URL, not the line itself. Check last word for URL prefix.
    """
    stripped = text.rstrip()
    if not stripped:
        return False
    last_line = stripped.split("\n")[-1].strip()
    last_word = last_line.split()[-1] if last_line.split() else ""
    if last_word.startswith("http://") or last_word.startswith("https://"):
        return True
    return stripped[-1] in _CLEAN_END_CHARS
