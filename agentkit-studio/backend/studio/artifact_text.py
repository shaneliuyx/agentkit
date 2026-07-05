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

from studio.textutil import dbg as _dbg
from studio.textutil import FENCE_LINE_RE as _FENCE_LINE_RE
from studio.textutil import fence_rest_contaminated as _fence_rest_contaminated
from studio.textutil import MERMAID_BLOCK_RE as _MERMAID_BLOCK_RE
from studio.textutil import norm_url as _norm_url
from studio.textutil import norm_urls as _norm_urls

if TYPE_CHECKING:
    from agentkit.types import LLMClient


_TITLE_PLACEHOLDER_RE = _re.compile(
    r"(?i)(?:\b(?:report|deliverable)\s+title\b|\btitle\s*[-—]\s*generated\b|"
    r"generated from (?:the )?findings|generated below)"
)
_GENERIC_REPORT_TITLE_RE = _re.compile(
    r"(?i)^(?:research\s+report|technical\s+report|final\s+report|report|deliverable)$"
)
_TITLE_PREFIX_RE = _re.compile(
    r"(?is)^\s*(?:write|create|generate|prepare|draft|produce)\s+"
    r"(?:a|an|the)?\s*(?:concise|detailed|comprehensive|technical|research|generic|report|"
    r"analysis|study|white\s+paper|brief|overview|\s)+\s+"
    r"(?:about|on|regarding|covering|for)\s+"
)
_TITLE_STOP_RE = _re.compile(
    r"(?is),\s*(?:\w+\s+){0,3}\b(?:include|use|cover|provide|with)\b.*$"
)
_TITLE_CONTEXT_RE = _re.compile(r"(?i)\s+(?:in|within)\s+.{18,}$")


def _is_placeholder_title(title: str) -> bool:
    cleaned = _re.sub(r"[_()\-—]+", " ", title or "").strip()
    return (
        not cleaned
        or bool(_TITLE_PLACEHOLDER_RE.search(cleaned))
        or bool(_GENERIC_REPORT_TITLE_RE.fullmatch(cleaned))
    )


def _derive_title_from_requirement(requirement: str) -> str:
    """Return a task-derived report title without encoding any domain."""
    source = (requirement or "").strip()
    first_sentence = _re.split(r"(?<=[.!?])\s+", source, maxsplit=1)[0]
    topic = _TITLE_PREFIX_RE.sub("", first_sentence).strip()
    topic = _TITLE_STOP_RE.sub("", topic).strip(" .:;-")
    topic = _TITLE_CONTEXT_RE.sub("", topic).strip(" .:;-")
    if not topic:
        topic = first_sentence.strip(" .:;-") or "Research Report"
    topic = _re.sub(r"\s+", " ", topic)
    words = topic.split()
    # 18, not 14: the stop-anchor above already trims the requirement's clause
    # tail, so what reaches here is the real topic phrase — a 14-word cap chopped
    # "… create a research report" to "… create a research" (live H1, 2026-07-05).
    if len(words) > 18:
        words = words[:18]
        # a cut must not strand a dangling connective/article as the last word
        while words and words[-1].lower() in {
            "a", "an", "the", "and", "or", "to", "of", "for", "in", "on", "with",
        }:
            words.pop()
        topic = " ".join(words)
    return topic[:1].upper() + topic[1:]


def resolve_report_title(text: str, requirement: str, preferred_title: str = "") -> str:
    """Resolve placeholder or missing H1 titles from the task requirement.

    The skeleton intentionally starts with a placeholder title so the report can
    stay generic while the actual run fills in task-specific content. This helper
    keeps model-authored titles, replaces unresolved placeholders, and prepends a
    derived title when a report has sections but no H1.
    """
    body = text or ""
    if not body.strip():
        return body
    preferred = (preferred_title or "").strip()
    title = preferred if preferred and not _is_placeholder_title(preferred) else _derive_title_from_requirement(requirement)
    h1 = _re.search(r"(?m)^#\s+(.+?)\s*$", body)
    if h1:
        if _is_placeholder_title(h1.group(1)):
            return _re.sub(r"(?m)^#\s+.+?\s*$\n*", f"# {title}\n\n", body, count=1)
        return body
    if _re.search(r"(?m)^##\s+", body):
        return f"# {title}\n\n{body.lstrip()}"
    return body


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
    # §4d moving-window rule: a model TRUNCATES a long echo (verified 68 KB → 36 KB),
    # so the length guard below then REJECTS the truncated rewrite and synthesis SILENTLY
    # NO-OPS at scale. Below the window threshold the whole-doc echo is safe and is kept
    # verbatim (preserves the small-doc unit tests). Above it, synthesize section by
    # section: each call sees ONE section + the doc's heading list (cross-section context,
    # §5 caveat) so comparison still works, and reassembly is deterministic.
    if len(src) <= _SYNTH_WINDOW:
        return _synthesize_block(src, client, requirement, context="")
    return _synthesize_windowed(src, client, requirement)


#: Whole-doc synthesis is only safe up to this size; beyond it the model truncates a
#: long echo, so synthesis switches to section-windowed (§4d).
_SYNTH_WINDOW = 8_000


#: Default rewrite directive — adds an analysis/synthesis layer (PLAN item 1A).
_DIRECTIVE_ANALYSIS = (
    "You are a research analyst. The DRAFT below is well-sourced and cited but reads as "
    "stitched-together quotations with little original analysis.\n\n"
    "Rewrite it to ADD a synthesis/analysis layer: explain what the findings MEAN together, "
    "COMPARE and CONTRAST the sources, and surface implications, patterns, and trade-offs in "
    "your own words.\n\n"
)
#: Readability/instructor-tone directive — the FINAL refine pass the user asked for. Chosen by
#: an A/B/C/D eval on a real seed section with the gemma model: this "instructor" style won the
#: readability judge, and the explicit "CITATIONS ARE SACRED" reinforcement lifted citation
#: retention from 3/4 to 4/4 (the eval's decisive metric — the user requires citations intact).
_DIRECTIVE_READABILITY = (
    "You are an expert INSTRUCTOR writing for an educated but non-specialist reader. The DRAFT "
    "below is accurate and well-cited but reads like a REPETITIVE LIST of stitched quotations — "
    "one 'This defines… / This provides… : \"quote\" (citation)' sentence after another, often "
    "restating the same point several times.\n\n"
    "REWRITE it into flowing, natural teaching prose that SYNTHESIZES the sources, not lists them:\n"
    "- SUMMARIZE and MERGE: collapse repeated or overlapping points into ONE clear explanation. "
    "The result will be SHORTER than the draft — that is correct and wanted.\n"
    "- DELETE the scaffolding ('This defines', 'This provides', 'This establishes', 'This "
    "validates', 'This clarifies', etc.). Never start a sentence that way.\n"
    "- Explain each idea in plain language with a warm, explanatory tone (a brief analogy or 'in "
    "other words…' where it helps) and connect ideas with smooth transitions.\n"
    "- ANALYZE and REFLECT in your own words: what the sources mean TOGETHER, how they agree or "
    "differ, why it matters, the trade-off or takeaway — not just what each one says.\n"
    "- Quote sparingly; prefer paraphrase. You MAY support one synthesized sentence with several "
    "citations.\n\n"
    "CITATIONS ARE SACRED: every URL that appears in the draft (inside a [text](url) link or bare) "
    "MUST still appear somewhere in your rewrite, attached to a relevant claim — you may regroup "
    "them, but never DROP one. Before finishing, silently check that no URL was lost.\n\n"
)


def _salvage_section_echo(src: str, out: str, tag: str) -> str:
    """Slice OUR section back out of a whole-document echo.

    Live attempt 4 (2026-07-05): gemma answered EVERY per-section window with
    the whole document, so the invented-headings guard rejected all of them and
    the depth pass no-op'd. When src is a single headed section and the reply
    contains that same heading plus other document headings, the reply is an
    echo — judge the matching slice instead of rejecting wholesale. A wrong
    slice only ever faces the same guards; worst case is the old reject."""
    src_head = (src.lstrip().splitlines() or [""])[0].strip()
    if not src_head.startswith("#"):
        return out
    other_heads = [h for h in _re.findall(r"(?m)^#{1,3}\s.*$", out)
                   if h.strip() != src_head]
    if src_head not in {h.strip() for h in _re.findall(r"(?m)^#{1,3}\s.*$", out)} or not other_heads:
        return out
    parts = _re.split(r"(?m)^(#{1,3}\s.*)$", out)
    for head, body in zip(parts[1::2], parts[2::2]):
        if head.strip() == src_head:
            _dbg(f"synth[{tag}]: whole-doc echo → salvaged own section "
                 f"({len(body)} chars of {len(out)})")
            return f"{head.strip()}\n{body}".strip()
    return out


def _synthesize_block(
    block: str, client: "LLMClient", requirement: str, *, context: str,
    directive: str | None = None, min_ratio: float = 0.9,
) -> tuple[str, bool]:
    """Rewrite ONE block (whole small doc, or one section of a large one) per ``directive``
    (default: add an analysis layer; pass ``_DIRECTIVE_READABILITY`` for the instructor-tone
    refine).

    Same anti-regression contract as the caller: keep every URL, never come back materially
    shorter. ``context`` is an optional cross-section summary (the doc's heading list) so a
    per-section call can still compare across the report. Returns ``(text, changed)``."""
    src = block or ""
    if not src.strip():
        return src, False
    urls_before = _norm_urls(src)
    _ctx = f"DOCUMENT SECTIONS (for cross-section comparison):\n{context}\n\n" if context else ""
    prompt = (
        (directive or _DIRECTIVE_ANALYSIS)
        + "HARD RULES (a violation makes the rewrite worthless):\n"
        "- Keep EVERY URL and citation exactly as it appears — never drop, move, or alter one.\n"
        "- Do not introduce any new URL, citation, source, quote, data, or named source.\n"
        "- Do not remove any sourced fact, quote, or section heading.\n"
        "- The result must be at least as long as the draft.\n"
        "- Output ONLY the improved text, with no preamble or commentary.\n\n"
        f"{_ctx}"
        f"TASK: {requirement[:400]}\n\n"
        f"DRAFT:\n{src}"
    )
    _tag = (src.lstrip().splitlines() or [""])[0][:60]
    try:
        resp = client.chat([{"role": "user", "content": prompt}])
        out = (getattr(resp, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 — synthesis is best-effort; never break the run
        _dbg(f"synth[{_tag}]: REJECT exception={type(exc).__name__}")
        return src, False
    if not out:
        _dbg(f"synth[{_tag}]: REJECT empty-reply")
        return src, False
    out = _salvage_section_echo(src, out, _tag)
    urls_after = _norm_urls(out)
    # Reject regressions: a dropped citation (always), or shrinking below ``min_ratio`` of the
    # block. Analysis ADDS (ratio 0.9); the readability/summarize pass CONDENSES repeated quotes
    # (ratio ~0.4) — so it may come back shorter, but every URL must survive.
    if not urls_before <= urls_after or not urls_after <= urls_before:
        _dbg(f"synth[{_tag}]: REJECT urls lost={len(urls_before - urls_after)} "
             f"gained={len(urls_after - urls_before)}")
        return src, False
    # §14 slate bundle A: the URL/overlap/ratio guards above never checked fenced
    # blocks — a rewrite can preserve every citation while silently dropping a code
    # or mermaid block (observed live: "finalize[synthesize_readability]: changed=True
    # fences 2→0"). Pair count, not raw marker count, so an accidentally-unbalanced
    # rewrite still reads as a loss rather than being masked by integer division.
    _fences_before, _fences_after = src.count("```") // 2, out.count("```") // 2
    if _fences_after < _fences_before:
        _dbg(f"synth[{_tag}]: REJECT fence lost {_fences_before}→{_fences_after}")
        return src, False
    if len(out) < int(min_ratio * len(src)):
        _dbg(f"synth[{_tag}]: REJECT length ratio={len(out) / max(1, len(src)):.2f} "
             f"< min_ratio={min_ratio}")
        return src, False
    # The URL/length guards are BLIND on a citation-free window (∅ == ∅), which let a
    # weak model's meta-refusal — "Once you provide the text … I will immediately
    # transform it…", QUOTING heading names like `## References").` — be accepted and
    # written into the document as fake sections (live run 1530, score 0.97 → 0.087).
    # Two STRUCTURAL guards (no hardcoded phrasing — task-neutral guardrail):
    # 1. Synthesis must never INVENT a heading — new heading lines are how the
    #    refusal's quoted section names became fake sections on reassembly.
    # Probe 2026-07-05: scan CODE-MASKED text — a `# comment` inside a fenced
    # example gemma legitimately added matched the heading regex and killed the
    # rewrite (one reason code fences never survived synthesis).
    from studio.rubric import mask_fenced_code as _mask
    headings_before = set(_re.findall(r"(?m)^#{1,3}\s.*$", _mask(src)))
    _invented = set(_re.findall(r"(?m)^#{1,3}\s.*$", _mask(out))) - headings_before
    if _invented:
        _dbg(f"synth[{_tag}]: REJECT invented-headings={sorted(_invented)[:3]}")
        return src, False
    # 2. A REWRITE must preserve the draft's own content vocabulary. Genuine
    #    synthesis paraphrases but keeps the topic's terms; prose ABOUT the task
    #    (refusals, rule restatements, requests for input) shares almost none of
    #    the draft's words — in any phrasing, so no phrase list can rot.
    _ov = _content_word_overlap(src, out)
    if _ov < _SYNTH_MIN_OVERLAP:
        _dbg(f"synth[{_tag}]: REJECT overlap={_ov:.2f} < {_SYNTH_MIN_OVERLAP}")
        return src, False
    _dbg(f"synth[{_tag}]: ACCEPT overlap={_ov:.2f} "
         f"ratio={len(out) / max(1, len(src)):.2f} urls={len(urls_after)}")
    return out, True


#: Minimum fraction of the draft's content words a rewrite must retain. Low on
#: purpose: the readability pass legitimately condenses and paraphrases; a
#: refusal/meta reply scores near zero, so the margin is wide.
_SYNTH_MIN_OVERLAP = 0.3


def _content_word_overlap(src: str, out: str) -> float:
    """Fraction of *src*'s content vocabulary present in *out*.

    Heading lines are excluded from the source vocabulary — a meta-reply that
    QUOTES section names must not earn overlap credit for them."""
    src_body = "\n".join(
        ln for ln in (src or "").splitlines() if not ln.lstrip().startswith("#")
    )
    src_words = {w for w in _re.findall(r"[a-z]{4,}", src_body.lower())}
    if not src_words:
        return 1.0
    out_words = set(_re.findall(r"[a-z]{4,}", (out or "").lower()))
    return len(src_words & out_words) / len(src_words)


def _synthesize_windowed(
    src: str, client: "LLMClient", requirement: str, *, directive: str | None = None,
    min_ratio: float = 0.9,
) -> tuple[str, bool]:
    """Section-windowed rewrite for a large doc (§4d): rewrite each section with the small-input
    call per ``directive``, then reassemble deterministically. A section that fails its own
    anti-regression guard is kept verbatim, so the whole pass is monotone — it can only improve,
    never lose content (or citations)."""
    from agentkit.artifacts.sections import split_sections
    sections = split_sections(src)
    if len(sections) < 2:
        # One section but over the window: still safer to try the single block than to echo
        # nothing. The block guard rejects a truncated result, so worst case = unchanged.
        return _synthesize_block(src, client, requirement, context="", directive=directive,
                                 min_ratio=min_ratio)
    headings = "\n".join(f"- {h}" for h, _b in sections)
    out_parts: list[str] = []
    any_changed = False
    for heading, body in sections:
        whole = f"{heading}\n{body}" if heading else body
        new, changed = _synthesize_block(
            whole, client, requirement, context=headings, directive=directive,
            min_ratio=min_ratio,
        )
        any_changed = any_changed or changed
        out_parts.append(new if changed else whole)
    if not any_changed:
        return src, False
    rebuilt = "\n".join(out_parts)
    # §4d dedup-on-reassembly: windows that share a cross-section summary can restate the
    # same comparison sentence in two sections. Drop a paragraph that is a verbatim (lexical)
    # duplicate of one already emitted — but NEVER drop a paragraph carrying a URL (citations
    # must survive). Cheap lexical stage; the semantic (cosine) stage is the reducer's job
    # where an embedder is wired.
    rebuilt = _dedup_paragraphs(rebuilt)
    # Final whole-doc guard: never return something that dropped a URL or shrank overall.
    urls_before = _norm_urls(src)
    urls_after = _norm_urls(rebuilt)
    if not urls_before <= urls_after or not urls_after <= urls_before:
        return src, False
    if len(rebuilt) < int(min(min_ratio, 0.80) * len(src)):
        return src, False
    return rebuilt, True


def _refine_readability(
    text: str, client: "LLMClient | None", requirement: str
) -> tuple[str, bool]:
    """FINAL instructor-tone readability pass (user request). Rewrites the report into clear,
    natural teaching prose — explaining complex theory in plain language with more analysis and
    reflection — PARAGRAPH BY PARAGRAPH, while keeping every citation. Same §4d windowing and
    anti-regression contract as ``_synthesize_analysis`` (small doc → one block; large doc →
    per-section, each rewrite rejected if it drops a URL or shrinks). Returns ``(text, changed)``.
    The prompt style + the 'CITATIONS ARE SACRED' reinforcement were chosen by a gemma A/B eval
    (4/4 citation retention; best readability)."""
    src = text or ""
    if not src.strip() or client is None:
        return src, False
    # PER TOP-LEVEL SECTION (not per sub-section/paragraph): feed each whole "## " section — its
    # sub-sections included — to the model as ONE block, so it can SYNTHESIZE and MERGE the
    # repetitive same-source sentences within the section (the quote-wall). Splitting into smaller
    # sub-blocks made each URL-dense block fail the citation guard and stay verbatim. Summarizing
    # outputs SHORTER text, so a large section is NOT an echo and does not truncate (§4d's
    # truncation risk is echo-back, not condense). min_ratio 0.4: accept down to 40% as long as
    # every URL survives (the citation guard is unconditional). Reassemble deterministically.
    sections = _split_top_level(src)
    if len(sections) < 2:
        return _synthesize_block(src, client, requirement, context="",
                                 directive=_DIRECTIVE_READABILITY, min_ratio=0.4)
    headings = "\n".join(
        f"- {s.splitlines()[0]}" for s in sections if s.strip().startswith("#")
    )
    out_parts: list[str] = []
    any_changed = False
    for sec in sections:
        new, changed = _synthesize_block(
            sec, client, requirement, context=headings,
            directive=_DIRECTIVE_READABILITY, min_ratio=0.4,
        )
        any_changed = any_changed or changed
        out_parts.append(new if changed else sec)
    if not any_changed:
        return src, False
    rebuilt = _dedup_paragraphs("\n\n".join(out_parts))
    if not _norm_urls(src) <= _norm_urls(rebuilt):
        return src, False
    return rebuilt, True


def _split_top_level(text: str) -> list[str]:
    """Split a document into TOP-LEVEL (``## ``) sections, each chunk carrying its own
    sub-sections (``### …``) whole. Any preamble before the first ``## `` is its own chunk.
    Used by the readability pass so a section is rewritten as ONE coherent unit, not fragmented."""
    parts = _re.split(r"(?m)^(## .+)$", text or "")
    out: list[str] = []
    if parts[0].strip():
        out.append(parts[0].rstrip())
    for h, b in zip(parts[1::2], parts[2::2]):
        out.append(f"{h}\n{b}".rstrip())
    return out


def _norm_para(p: str) -> str:
    """Normalized key for lexical paragraph dedup: lowercased, whitespace-collapsed."""
    return _re.sub(r"\s+", " ", p).strip().lower()


def _dedup_paragraphs(text: str) -> str:
    """Drop verbatim-duplicate paragraphs (§4d lexical dedup), preserving order and any
    paragraph that carries a URL (citations never deduped away). Headings are never dropped."""
    seen: set[str] = set()
    out: list[str] = []
    for para in (text or "").split("\n\n"):
        key = _norm_para(para)
        is_heading = para.lstrip().startswith("#")
        has_url = "http://" in para or "https://" in para
        if key and key in seen and not is_heading and not has_url:
            continue
        if key and not has_url:
            seen.add(key)
        out.append(para)
    return "\n\n".join(out)


def _dedup_long_sentences(text: str) -> str:
    """Drop repeated long boilerplate sentences without losing unique citations."""
    seen: set[str] = set()
    seen_urls: set[str] = set()

    def repl(match: _re.Match[str]) -> str:
        sentence = match.group(0)
        key_text = _re.sub(r"(?m)^#{1,6}\s+.*$", "", sentence)
        key = _re.sub(r"\s+", " ", key_text).strip().lower()
        urls = set(_re.findall(r"https?://[^\s)>\]\"']+", sentence))
        if len(key) >= 120 and key in seen and urls <= seen_urls:
            headings = "\n".join(
                line for line in sentence.splitlines()
                if _re.match(r"^#{1,6}\s+", line.strip())
            )
            return headings + ("\n" if headings else "")
        if key:
            seen.add(key)
            seen_urls.update(urls)
        return sentence

    return _re.sub(r"(?s)(.*?[.!?])(?=(?:\s+(?:[A-Z#]|\Z)|\Z))", repl, text or "")


#: A 2-6 ``#`` heading marker glued mid-line after a non-newline, non-``#`` char — e.g.
#: ``## Design Architecture### Implementation`` (two headings on one line). Single ``#`` is
#: excluded so code comments (``x = 1  # c``) are never split.
_GLUED_HEADING_RE = _re.compile(r"(?<=[^\n#])(#{2,6}\s)")


def _split_glued_headings(text: str) -> str:
    """Put a glued mid-line heading marker onto its own block (fixes the 'wrong format'):
    ``## A### B`` → ``## A\\n\\n### B``. Leaves a heading already on its own line, a code
    ``# comment`` (single ``#``), and a heading glued to plain prose (no marker) untouched."""
    return _GLUED_HEADING_RE.sub(r"\n\n\1", text or "")


def normalize_artifact(text: str) -> str:
    """Deterministic markdown hygiene for the assembled artifact (PLAN N1 + format repair):
    un-glue mid-line headings, THEN collapse duplicate sections to one (richest body).
    Order matters — un-gluing first lets dedupe see the real, separated headings. Idempotent
    and a no-op on an already-clean document, so it is safe to run after every phase."""
    return _dedup_long_sentences(dedupe_sections(_split_glued_headings(text)))


_PENDING_PLACEHOLDER_RE = _re.compile(
    r"(?im)^\s*_\((?:pending|to be completed)\s*[-—][^)]*\)_\s*$"
)
_REFERENCE_PLACEHOLDER_RE = _re.compile(
    r"(?is)(?:no\s+specific\s+urls?\s+were\s+provided|placeholder\s+for\s+"
    r"(?:the\s+)?required\s+citations|placeholder\s+for\s+citations)"
)


def _urls_in_order(text: str) -> list[str]:
    """Unique URLs, first-appearance order. Built on ``textutil.extract_urls``
    (S1) — that helper is intentionally NOT deduplicated (other callers count
    occurrences), so dedup happens here via ``dict.fromkeys``."""
    from studio.textutil import extract_urls

    return list(dict.fromkeys(extract_urls(text or "")))


def strip_satisfied_placeholders(text: str) -> str:
    """Remove template placeholder lines from sections that now contain real content.

    Additive reducers insert findings after a section heading, leaving the original
    ``_(pending - needs sourced content)_`` line below the new prose. That marker is
    useful in an empty skeleton but becomes a false quality failure once content exists.
    """
    from agentkit.artifacts.sections import split_sections

    sections = split_sections(text or "")
    if not sections:
        return text
    out: list[str] = []
    changed = False
    all_urls = _urls_in_order(text or "")
    for _heading, body in sections:
        body_text = body or ""
        stripped_body = _PENDING_PLACEHOLDER_RE.sub("", body_text).strip()
        lines = stripped_body.splitlines()
        heading_line = lines[0].strip() if lines else ""
        content_only = "\n".join(stripped_body.splitlines()[1:]).strip()
        if (
            heading_line.lower().startswith("## references")
            and all_urls
            and _REFERENCE_PLACEHOLDER_RE.search(content_only)
        ):
            changed = True
            out.append(heading_line + "\n\n" + "\n".join(f"- {u}" for u in all_urls))
            continue
        if content_only and stripped_body != body_text.strip():
            changed = True
            out.append(stripped_body)
        else:
            out.append(body_text.rstrip())
    if not changed:
        return text
    return "\n\n".join(out).rstrip() + "\n"


#: The References/Sources heading a report ends with (case-insensitive).
_REFERENCES_HEADING_RE = _re.compile(r"(?im)^##\s+(?:references|sources)\b.*$")


def rebuild_references_section(text: str) -> str:
    """Replace the References section with a deterministic bibliography (PLAN-CONSOLIDATED P2-8).

    References is a template section like any other, so spokes fill it with
    PROSE — which is where junk findings landed live (a self-admittedly
    off-topic π-Wikipedia note, refusal filler, "definition of Pi… baseline for
    geometric calculations"). The real bibliography is derivable: every URL
    cited in the BODY, first-appearance order, titled from its markdown link
    text when the body has one. A URL cited ONLY inside References earned no
    body citation and is dropped with the prose. Fail-open: no References
    heading or no body-cited URLs → text unchanged."""
    src = text or ""
    m = _REFERENCES_HEADING_RE.search(src)
    if not m:
        return src
    nxt = _re.compile(r"(?m)^##\s").search(src, m.end())
    refs_end = nxt.start() if nxt else len(src)
    body = src[: m.start()] + src[refs_end:]

    urls: list[str] = []
    for raw in _re.findall(r"https?://[^\s)>\]\"']+", body):
        u = raw.rstrip(".,;:")
        if u and u not in urls:
            urls.append(u)
    if not urls:
        return src

    titles: dict[str, str] = {}
    for t, raw in _re.findall(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", body):
        u = raw.rstrip(".,;:")
        titles.setdefault(u, t.strip())
    entries = "\n".join(
        f"- [{titles[u]}]({u})" if u in titles else f"- {u}" for u in urls
    )
    rebuilt = f"{m.group(0)}\n\n{entries}\n"
    tail = src[refs_end:]
    return src[: m.start()] + rebuilt + ("\n" + tail.lstrip("\n") if tail.strip() else "")


def add_missing_section_citations(
    text: str,
    verified_urls: list[str] | tuple[str, ...] | None,
) -> str:
    """Append verified source URLs to long sections that still have no inline URL."""
    urls = [u for u in (verified_urls or []) if isinstance(u, str) and u.startswith("http")]
    if not urls or not (text or "").strip():
        return text
    matches = list(_re.finditer(r"(?m)^#{1,6}\s+.+$", text or ""))
    if not matches:
        return text
    out: list[str] = []
    pos = 0
    url_i = 0
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append(text[pos:start])
        section = text[start:end]
        heading = match.group(0).lower()
        body = section[match.end() - start:]
        if (
            not any(skip in heading for skip in ("reference", "appendix", "glossary", "title"))
            and len(_re.findall(r"\w+", body)) > 150
            and "http://" not in body
            and "https://" not in body
        ):
            section = section.rstrip() + f"\n\nSource: {urls[url_i % len(urls)]}\n"
            url_i += 1
        out.append(section)
        pos = end
    out.append(text[pos:])
    return "".join(out)


def _heading_key(h: str) -> str:
    """Normalized identity of a heading for duplicate detection: drop the leading ``#``,
    a leading enumerator (``2.`` / ``3)``), lowercase, collapse whitespace. So
    ``## 2. Design Architecture`` and ``## Design Architecture`` are the SAME section."""
    s = _re.sub(r"^#+\s*", "", h)
    s = _re.sub(r"^\d+[.)]\s*", "", s)
    return _re.sub(r"\s+", " ", s).strip().lower()


def dedupe_sections(text: str) -> str:
    """Collapse DUPLICATE headings to ONE (PLAN N1 — reconcile to one outline).

    A heading that appears more than once — the echo-accumulation failure where gemma spokes
    re-emit the whole document and the grow-only writeback stacks copies, so the eight template
    sections each end up present 8-10× — is collapsed to a single instance carrying the RICHEST
    (longest) body seen for that heading; the rest are dropped. Order is first-appearance.
    Preamble before the first heading is preserved. Returns *text* unchanged when no heading
    repeats, so it is a safe no-op on a clean document."""
    if not text or "```" in text:
        # fenced code can contain '#'-comment lines; mask them so they are not mistaken for
        # headings (N4), then operate on the masked split but rebuild from ORIGINAL slices.
        from studio.rubric import mask_fenced_code
        masked = mask_fenced_code(text)
    else:
        masked = text
    # split on headings in the masked view, but index into the ORIGINAL text so bodies keep
    # their fenced code verbatim.
    parts = _re.split(r"(?m)^(#{1,6}\s+.+)$", masked)
    if len(parts) < 4:
        return text  # 0 or 1 heading → nothing to dedupe
    # reconstruct (heading, body) pairs over the ORIGINAL text using the same split positions
    o_parts = _re.split(r"(?m)^(#{1,6}\s+.+)$", text)
    pre = o_parts[0]
    pairs = list(zip(o_parts[1::2], o_parts[2::2]))
    counts: dict[str, int] = {}
    for h, _b in pairs:
        counts[_heading_key(h)] = counts.get(_heading_key(h), 0) + 1
    if all(c == 1 for c in counts.values()):
        return text  # no duplicates → no-op
    # richest body per key
    richest: dict[str, tuple[str, str]] = {}
    for h, b in pairs:
        k = _heading_key(h)
        if k not in richest or len((b or "").strip()) > len((richest[k][1] or "").strip()):
            richest[k] = (h, b)
    seen: set[str] = set()
    out = [pre.rstrip()] if pre.strip() else []
    for h, _b in pairs:
        k = _heading_key(h)
        if k in seen:
            continue
        seen.add(k)
        rh, rb = richest[k]
        out.append(f"{rh}\n{rb}".rstrip())
    return ("\n\n".join(out)).rstrip() + "\n"


def reconcile_outline(text: str, template: list[str]) -> str:
    """N1 reconcile: collapse a DOUBLED outline to one (PLAN N1).

    ``_merge_missing_sections`` PREVENTS new doubling; this repairs a doc that ALREADY carries
    two parallel skeletons — e.g. the agent's own headings (`## 2. Design Architecture`,
    `## 4. Example Code`) PLUS an appended template block (`## Background and Scope`,
    `## Key Findings`, …) whose bodies are empty placeholders. Strategy: when a template
    section appears as an EMPTY/placeholder heading AND the same concept is already covered by
    a non-empty heading elsewhere, DROP the empty duplicate. Order-preserving; never removes a
    section that has real content. Returns *text* unchanged when no reconciliation applies."""
    if not template:
        return text
    # First normalize: un-glue mid-line headings, then collapse exact-duplicate headings (the
    # echo-accumulation bug), then drop empty template duplicates below. The heavy lifter for a
    # seed that stacked the whole template 8-10× with glued headings.
    text = normalize_artifact(text)
    from agentkit.artifacts.sections import split_sections
    from studio.rubric import _content_tokens
    sections = split_sections(text or "")
    if len(sections) < 2:
        return text
    # concept tokens of every section that HAS content
    populated_tokens: set[str] = set()
    for h, b in sections:
        if (b or "").strip() and "_(to be completed)_" not in b and "_(pending" not in b.lower():
            populated_tokens |= _content_tokens(h)
    tmpl_lower = {s.lower() for s in template}
    drop_idx: set[int] = set()
    for i, (h, b) in enumerate(sections):
        body_empty = not (b or "").strip() or "_(to be completed)_" in b or "_(pending" in b.lower()
        is_tmpl = h.strip().lower() in tmpl_lower or any(
            t in h.lower() for t in tmpl_lower
        )
        # an EMPTY template-named heading whose concept is already populated elsewhere
        if body_empty and is_tmpl and (_content_tokens(h) & populated_tokens):
            drop_idx.add(i)
    if not drop_idx:
        return text
    kept = "\n".join(
        f"{h}\n{b}".rstrip() for i, (h, b) in enumerate(sections) if i not in drop_idx
    )
    return kept.rstrip() + "\n"


# S1: _MERMAID_BLOCK_RE moved to studio.textutil (imported at module top) — a
# fenced ```mermaid block, captured whole for block-level repair + deterministic splice.


def _repair_fence_contamination(text: str) -> tuple[str, bool]:
    """Split a fence line that carries trailing content glued after the ``` marker
    onto its own line (run-1537 line 93: a closing fence immediately followed by a
    citation URL — ```` ``` https://nader.substack.com/... ````, breaking markdown
    rendering). Detection is shared with ``studio.artifact_lint`` via
    ``textutil.fence_rest_contaminated`` so the two can never disagree on what counts.

    A CLOSING fence's trailing content moves to its own line AFTER the fence (it was
    written right after the code, so that's where it reads naturally). An OPENING
    fence's trailing content moves BEFORE the fence instead — dropping it after
    would land it as the first line of actual code, corrupting the block body. If
    the line before an opening fence is non-blank (e.g. "...as shown below:"), a
    blank line is inserted first so the moved citation doesn't fuse into that
    sentence's paragraph. The moved line is prefixed with the fence's own `indent`
    so a citation moved out of an indented (list-item) fence doesn't de-indent to
    the margin. Idempotent: after the split, no fence line carries trailing
    content, so a second pass is a no-op.
    """
    src = text or ""
    out: list[str] = []
    pos = 0
    changed = False
    is_opening = True
    for m in _FENCE_LINE_RE.finditer(src):
        rest = m.group("rest")
        if _fence_rest_contaminated(rest, is_opening=is_opening):
            indent = m.group("indent")
            rest_stripped = rest.strip()
            prefix = src[pos:m.start()]
            if is_opening:
                # Keep a legal language token (if any) on the fence; only the part
                # beyond it — the glued URL — moves, and it moves BEFORE the fence.
                lang, _, extra = rest_stripped.partition(" ")
                moved, lang = (rest_stripped, "") if "://" in lang else (extra.strip(), lang)
                sep = "" if not prefix or prefix.endswith("\n\n") else "\n"
                out.append(prefix)
                out.append(f"{sep}{indent}{moved}\n{indent}```{lang}")
            else:
                out.append(prefix)
                out.append(f"{indent}```\n{indent}{rest_stripped}")
            pos = m.end()
            changed = True
        is_opening = not is_opening
    out.append(src[pos:])
    return ("".join(out), True) if changed else (src, False)


#: A markdown link immediately followed by a bare URL that duplicates its own
#: target — `[title](URL) URL` (run-1537 line 105: the reducer echoed the same
#: source both as a link and as a trailing bare citation).
#: KNOWN LIMITATION: `[^)\s]+` is the same narrow char-class shape S1 already
#: replaced twice in textutil (see extract_urls's docstring) — it mis-captures a
#: URL with an internal paren, e.g. `.../wiki/Pi_(number)`, truncating at the `(`.
#: Fail-SAFE here (a missed repair, not a corruption): the doubled bare URL just
#: won't match and is left alone. Real fix is balanced-paren handling; out of
#: scope for this repair pass.
_MD_LINK_RE = _re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_BARE_URL_AFTER_RE = _re.compile(r"(\s+)(https?://\S+)")


def _repair_doubled_citations(text: str) -> tuple[str, bool]:
    """Drop a bare URL that immediately follows a markdown link and duplicates the
    link's own target (normalized via ``textutil.norm_url``). Only an EXACT
    normalized match is dropped — an adjacent DIFFERENT URL is left untouched.
    Any trailing sentence punctuation the bare URL carried (e.g. a wrapping `).`)
    is preserved, since that belongs to the surrounding prose, not the citation.
    """
    src = text or ""
    out: list[str] = []
    pos = 0
    changed = False
    for m in _MD_LINK_RE.finditer(src):
        out.append(src[pos:m.end()])
        pos = m.end()
        tail_m = _BARE_URL_AFTER_RE.match(src, pos)
        if not tail_m:
            continue
        raw_bare = tail_m.group(2)
        bare_core = _norm_url(raw_bare)
        if _norm_url(m.group(2)) != bare_core:
            continue  # a different adjacent URL — never touch it
        out.append(raw_bare[len(bare_core):])  # keep trailing punctuation only
        pos = tail_m.end()
        changed = True
    out.append(src[pos:])
    return ("".join(out), True) if changed else (src, False)


#: Cap on how many residual lints get named in one log line — a pathological
#: artifact could carry dozens; the point is diagnosability, not a full dump.
_RESIDUAL_LINT_LOG_CAP = 10


def _log_residual_lints(lints: list[str]) -> None:
    """PLAN §14 attempt-9 #2/#3: run v5 logged ``lints_before=6 changed=False`` with
    no way to tell WHICH six — name them (first ~80 chars each, capped at
    ``_RESIDUAL_LINT_LOG_CAP``) so a stuck repair is diagnosable instead of a bare
    count. Called UNCONDITIONALLY on whatever text ``_repair_lints`` is about to
    return, regardless of ``changed`` — a repair can fix ONE lint and still ship
    with others remaining, and that case needs the same visibility."""
    if lints:
        names = " | ".join(w[:80] for w in lints[:_RESIDUAL_LINT_LOG_CAP])
        _dbg(f"repair_lints: residual ({len(lints)}): {names}")


def _repair_lints(
    text: str, client: "LLMClient | None", requirement: str
) -> tuple[str, bool]:
    """Repair deterministic format defects, then a malformed mermaid diagram via
    the model — root-cause fix for the reported served-broken-diagram bug and
    run-1537's fence/citation glue (DESIGN §14.6).

    The two deterministic sub-repairs (fence-line contamination, doubled
    citations) run FIRST and unconditionally — they are pure text fixes, need no
    LLM, and their own correctness doesn't depend on lint count improving (a
    fence split or dedup is always right when it fires). The mermaid repair below
    is unchanged: LLM-based, gated on ``client``, and self-verifying via lint count.

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
    if not src.strip():
        return src, False
    out, fence_fixed = _repair_fence_contamination(src)
    out, dup_fixed = _repair_doubled_citations(out)
    changed = fence_fixed or dup_fixed
    if client is not None:
        before = len(lint_artifact(out))
        if before > 0:
            before_mermaid = out
            for block in _MERMAID_BLOCK_RE.findall(out):
                if not _MERMAID_GLUED_EDGE.search(block):
                    continue  # this diagram is well-formed — leave it
                prompt = (
                    "Fix the syntax errors in this Mermaid diagram. A node glued to an edge "
                    "label (e.g. `A|label| B`) is missing its link operator and must become "
                    "`A -->|label| B`. Change ONLY what is needed to make it valid; keep "
                    "every node and edge. Output ONLY the corrected ```mermaid code block, "
                    "nothing else.\n\n" + block
                )
                try:
                    reply = (
                        getattr(client.chat([{"role": "user", "content": prompt}]), "text", "")
                        or ""
                    ).strip()
                except Exception:  # noqa: BLE001 — repair is best-effort; never break the run
                    continue
                m = _MERMAID_BLOCK_RE.search(reply)
                fixed = m.group(0) if m else ""
                if not fixed or _MERMAID_GLUED_EDGE.search(fixed):
                    continue  # model didn't return a clean block — keep the original
                out = out.replace(block, fixed, 1)
            # Accept the mermaid splice only on a strict improvement; the deterministic
            # sub-repairs above are already-verified fixes and always count as `changed`.
            if out != before_mermaid and len(lint_artifact(out)) < before:
                changed = True
            else:
                out = before_mermaid  # unaccepted mermaid attempt — revert to pre-loop text
    result = out if changed else src
    _log_residual_lints(lint_artifact(result))
    return result, changed


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
    from studio.rubric import (
        _HEADING_TEXT_RE,
        _content_tokens,
        mask_fenced_code,
        sections_present,
    )
    present = {s.lower() for s in sections_present(text, sections)}
    missing = [s for s in sections if s.lower() not in present]
    if not missing:
        return text
    # N1 — Frankenstein guard. `sections_present` only matches a template name against
    # the doc's HEADINGS. When the agent organized the SAME concepts under different
    # names ("Design Architecture", "Example Code" instead of Background / Evidence),
    # the template names don't match any heading, so all of them were appended — the
    # served doc then carried TWO parallel skeletons. So additionally treat a template
    # section as covered when its concept tokens already appear in the doc BODY, and
    # never bolt the template onto a doc that already has its OWN rich outline.
    masked = mask_fenced_code(text)
    n_headings = len(_HEADING_TEXT_RE.findall(masked))
    body_tokens = _content_tokens(masked)            # singular-normalized word set
    still_missing = [
        s for s in missing
        if not ((_toks := _content_tokens(s)) and _toks <= body_tokens)
    ]
    if not still_missing:
        return text
    # The doc already has a full outline of its own (>= as many real headings as the
    # template) and most template sections are concept-covered in the body → respect
    # the agent's structure; appending now is what creates the doubled skeleton.
    if n_headings >= len(sections) and (
        len(still_missing) < len(missing) or len(still_missing) <= max(1, len(sections) // 4)
    ):
        return text
    add = "\n\n".join(f"## {s}\n\n_(to be completed)_" for s in still_missing)
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
    from studio.rubric import mask_fenced_code
    # N4: a `# comment` inside a ```python fence is not a section heading. Split on
    # the masked text so in-code lines never start a (phantom, always-"empty") section.
    artifact_text = mask_fenced_code(artifact_text)
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
