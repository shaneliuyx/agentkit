"""studio.research_first — research-first generation pipeline.

PLAN-codebase-simplification.md §9-§11: the existing seed-and-patch loop drafts
sections FIRST and treats research as patch material for whatever the seed already
says, so a task naming two subjects can silently research
only one of them (run 1534) and an executive summary can overclaim because it is
written before the body that should ground it. This module is an ALTERNATIVE
generator for a single run — it does coverage-by-construction (every extracted
subject gets its own query fanout, never joint-only: P1/§9.2), builds a
claims-with-provenance layer between fetched pages and drafted prose (D1, §11.3)
instead of compiling sections straight from search hits, and writes the Executive
Summary LAST from the already-written body (D4, §11.3) so it can only summarize
what exists. It hands its returned markdown to the existing finalize shell; it
does not touch runner.py or the hill-climb loop.

Five stages (§10.1), each fail-open — a bad source/claim/section is skipped, never
a crash: FRAME (extract subjects/sections from the task text) -> RESEARCH (query
fanout per subject + joint integration queries, fetched pages cached to
``evidence/``) -> CLAIMS (one grounded claim-extraction call per fetched source,
verbatim-quote checked, persisted to ``claims.jsonl``) -> WRITE (one call per
section from its relevant claims; summary last) -> ASSEMBLE (references rebuilt
deterministically, fence/citation repairs applied).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from studio.artifact_lint import lint_artifact
from studio.artifact_text import (
    _derive_title_from_requirement,
    _REFERENCES_HEADING_RE,
    _repair_doubled_citations,
    _repair_fence_contamination,
    references_last,
)
from studio import diagram_render
from studio.report_profiles import GENERIC_RESEARCH_PROFILE
from studio.requirement_compliance import (
    _CODE_SHAPED_RE,
    _COVERS_SHAPED_RE,
    _DIAGRAM_SHAPED_RE,
    extract_requirements,
)
from studio.textutil import dbg

EmitFn = Callable[[str, dict[str, Any]], None] | None

#: A section requirement is phrased "include a/an/the <name> section" — extraction
#: item 5's sibling shape for structure rather than subject coverage.
_SECTION_REQ_RE = re.compile(r"(?i)include\s+(?:an?|the)?\s*(.+?)\s+section\b")
#: Sections that naturally host grounded source material — the code example lands
#: in whichever of these appears first (mirrors diagram_render._TARGET_HEADING_RE's
#: "evidence" match). Diagram home is picked separately via the relationship
#: section (_pick_relationship_home), not a heading regex.
_CODE_HOME_RE = re.compile(r"(?i)evidence|analysis|implementation|example|walkthrough|code")
_SKIP_WRITE = frozenset({"executive summary", "references"})
#: Cap on claims spliced into one section prompt — Evidence-shaped sections get
#: the larger share (§10.1 WRITE: "Evidence gets most").
_EVIDENCE_SECTION_RE = re.compile(r"(?i)evidence|analysis")
_MAX_CLAIMS_EVIDENCE = 12
_MAX_CLAIMS_OTHER = 6
#: Per-subject research budget: up to 3 queries, fetch stops once this many
#: distinct-domain sources land.
_MAX_QUERIES_PER_SUBJECT = 3
_MAX_SOURCES_PER_SUBJECT = 3
#: Coverage-gate floors: fewer than this many claims for any subject, or zero
#: joint claims when the kind wants an integration story, triggers ONE recovery
#: fetch before WRITE (fail-visible safety net, not a fabrication path).
_MIN_CLAIMS_PER_SUBJECT = 2
_INTEGRATION_KINDS = ("cooperates", "extends", "unknown")
#: Joint breadth: a paired query plus one per-side mechanism probe (3 total), each
#: kept until 3 real integration sources land — a both-names blob alone finds no
#: page for two tools that never co-occur; the per-side probes surface each side's
#: integration-surface page (e.g. an MCP-server docs page) as a joint candidate.
_MAX_JOINT_QUERIES = 3
_MAX_SOURCES_PER_JOINT_QUERY = 3
#: Chars of a fetched page handed to the claim extractor — enough for 3-6 claims
#: without flooding a local model's context.
_CLAIM_SOURCE_CHARS = 8_000
#: A verbatim QUOTE is meant to be 10-40 words (the extraction prompt says so);
#: this is a hard backstop against a model that copies a whole raw block
#: instead (excerpt, never the page) — generous margin over ~40 words.
_MAX_QUOTE_CHARS = 400
#: Markdown link/image syntax around a URL — a scraped page's rendered links
#: (wiki-internal refs, LaTeX math-render SVGs), not prose a human would write.
_QUOTE_MARKUP_URL_RE = re.compile(r"!?\[[^\]]*\]\(https?://[^)]*\)")
_MAX_QUOTE_MARKUP_DENSITY = 0.3


def _is_markup_dense_quote(quote: str) -> bool:
    """True if *quote* is mostly rendered-markdown/URL noise rather than prose.
    A quote can pass the verbatim-substring check (it really is in the page)
    while being a LaTeX math-render dump or a chain of wiki-internal links —
    grounded in the source doesn't mean readable as prose; reject it as an
    embed candidate entirely rather than blockquoting noise."""
    if not quote:
        return False
    markup_chars = sum(len(m) for m in _QUOTE_MARKUP_URL_RE.findall(quote))
    markup_chars += sum(len(u) for u in re.findall(r"https?://\S+", quote))
    return markup_chars / len(quote) > _MAX_QUOTE_MARKUP_DENSITY


# ---------------------------------------------------------------------------
# FRAME
# ---------------------------------------------------------------------------


_SUBJECT_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _norm_subject_text(text: str) -> str:
    return _SUBJECT_NORMALIZE_RE.sub(" ", text.lower()).strip()


def _extract_subjects(groups: list[list[str]], *, requirement: str = "") -> list[str]:
    """Named subjects from "covers <subject>" branches (extraction item 5),
    order-preserving, deduped. Empty when the task names no specific subject."""
    subjects: list[str] = []
    whole_task = _norm_subject_text(_base_task_text(requirement)) if requirement else ""
    for group in groups:
        for branch in group:
            if not _COVERS_SHAPED_RE.search(branch):
                continue
            subject = _COVERS_SHAPED_RE.sub("", branch, count=1).strip(" :.-")
            if whole_task and _norm_subject_text(subject) == whole_task:
                dbg(f"research_first FRAME: dropped whole-task subject branch={branch!r}")
                continue
            if subject and subject.lower() not in (s.lower() for s in subjects):
                subjects.append(subject)
    return subjects


def _subjects_from_requirement(client: Any, requirement: str) -> list[str]:
    """LLM fallback for subject identity when no covers-shaped branch names a
    usable subject (v39 regression: extract_requirements emitted ONE covers
    branch quoting the whole task; the whole-task guard dropped it and the
    [title] fallback reinstated a whole-task-shaped single subject, collapsing
    every downstream stage to generic research). The LLM only PROPOSES —
    deterministic validation gates: a candidate must appear verbatim in the
    requirement, stay short, and not re-echo the whole task. Fails open to []
    (caller keeps its [title] last resort)."""
    if client is None:
        return []
    prompt = (
        f"TASK: {requirement[:400]}\n\n"
        "List the distinct named things (products, tools, frameworks, systems) "
        "this task asks to study or use — not the task itself. Answer in "
        "exactly this format:\nSUBJECTS: <comma-separated names copied "
        "verbatim from the task>"
    )
    try:
        reply = client.chat([{"role": "user", "content": prompt}])
        text = str(getattr(reply, "text", "") or "")
    except Exception as exc:  # noqa: BLE001 — a bad fallback call must never stall FRAME
        dbg(f"research_first FRAME: subject-fallback LLM call failed exc={exc!r}")
        return []
    m = re.search(r"SUBJECTS:\s*(.+)", text)
    if not m:
        return []
    whole_task = _norm_subject_text(_base_task_text(requirement))
    req_norm = f" {_norm_subject_text(requirement)} "
    subjects: list[str] = []
    for cand in m.group(1).split(","):
        cand = cand.strip(" .:;-")
        norm = _norm_subject_text(cand)
        # length + whole-task checks run BEFORE the compound split so a task
        # echo cannot shed words into an acceptable-looking fragment
        if not norm or norm == whole_task or len(norm.split()) > 4:
            dbg(f"research_first FRAME: rejected fallback subject {cand!r}")
            continue
        # a compound "A and B" answer names two subjects, not one
        parts = re.split(r"\s+and\s+", cand) if " and " in cand else [cand]
        for part in (p.strip(" .:;-") for p in parts):
            part_norm = _norm_subject_text(part)
            if not part_norm or f" {part_norm} " not in req_norm:
                dbg(f"research_first FRAME: rejected fallback subject {part!r}")
                continue
            if part.lower() not in (s.lower() for s in subjects):
                subjects.append(part)
    return subjects


def _build_sections(groups: list[list[str]]) -> list[str]:
    """The generic report template plus any explicit "include an X section"
    branch, inserted before References. Never drops a template section."""
    sections = list(GENERIC_RESEARCH_PROFILE.sections)
    for group in groups:
        for branch in group:
            m = _SECTION_REQ_RE.search(branch)
            if not m:
                continue
            name = m.group(1).strip().title()
            if name and name.lower() not in (s.lower() for s in sections):
                sections.insert(-1, name)
    return sections


def _relationship_section_title(subjects: list[str], relationship: "Relationship") -> str:
    """``"{A} and {B}: {descriptor}"`` — the descriptor is LLM-derived per task
    (R3), never the hardcoded word "Integration"."""
    return f"{' and '.join(subjects)}: {relationship.descriptor}"


def _is_relationship_section(name: str, relationship: "Relationship") -> bool:
    """A section is relationship-shaped if its title carries the per-task
    descriptor — tracked STRUCTURALLY by the descriptor, not the literal word
    "integrat" (R3: not every pair integrates)."""
    desc = relationship.descriptor.strip().lower()
    return bool(desc) and desc in name.lower()


def _ensure_relationship_section(
    sections: list[str], subjects: list[str], relationship: "Relationship"
) -> list[str]:
    """N>=2 subjects that actually relate → the skeleton MUST include a
    relationship section BY CONSTRUCTION (user: "all the flow needs to consider
    their relationships" — never emergent). Named from the per-task descriptor.
    No-op for <2 subjects, ``kind == 'independent'`` (no relationship to report),
    or an already-present relationship-shaped section."""
    if len(subjects) < 2 or relationship.kind == "independent":
        return sections
    if any(_is_relationship_section(s, relationship) for s in sections):
        return sections
    out = list(sections)
    out.insert(-1, _relationship_section_title(subjects, relationship))
    return out


def _pick_relationship_home(sections: list[str], relationship: "Relationship") -> str | None:
    """The dedicated relationship section if one exists (present for N>=2
    subjects that relate, post ``_ensure_relationship_section``)."""
    return next((s for s in sections if _is_relationship_section(s, relationship)), None)


#: Literal markers the runner appends when generation-time template/scoring
#: config is set (studio.runner._tpl_suffix / _score_suffix) — splitting on
#: these gives the CLEAN base task text for domain-word extraction (REBUILD-
#: LESSONS §2: "iteration-prefixed or template-suffixed text dilutes densities
#: ~10x"). A requirement without either marker is returned unchanged.
_TEMPLATE_SUFFIX_MARKERS = (
    "\n\nStructure the deliverable with these sections",
    "\n\nUnified scoring requirements for this task:",
)
_SCOPE_HOME_RE = re.compile(r"(?i)scope")


def _base_task_text(requirement: str) -> str:
    text = requirement or ""
    for marker in _TEMPLATE_SUFFIX_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
    return text.strip()


def _pick_home(sections: list[str], pattern: re.Pattern[str]) -> str | None:
    """First body section (excluding Executive Summary/References) matching
    *pattern*; falls back to the first body section so structural content
    always has somewhere to land."""
    body = [s for s in sections if s.lower() not in _SKIP_WRITE]
    for name in body:
        if pattern.search(name):
            return name
    return body[0] if body else None


# ---------------------------------------------------------------------------
# RESEARCH — query fanout per subject (never joint-only), fetch + cache.
# ---------------------------------------------------------------------------


def _domain(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def _search(query: str, results: int = 5) -> list[Any]:
    try:
        from web_toolkit import web_search
    except Exception:  # noqa: BLE001 — toolkit absent → no results, never a crash
        dbg("research_first _search: web_toolkit unavailable — no results")
        return []
    try:
        return list(web_search(query, results=results) or [])
    except Exception as exc:  # noqa: BLE001 — network/backend failure is non-fatal
        dbg(f"research_first _search: backend error query={query!r} exc={exc!r}")
        return []


def _fetch_and_store(url: str, evidence_dir: Path, idx: int) -> str | None:
    """Fetch *url* through the shared cache (studio.tools owns fetch+cache — this
    never reimplements it) and persist it as ``evidence/source-NNN.md`` in the
    same ``URL: <url>\\n\\n<content>`` shape ``structural_producer`` reads."""
    from studio.tools import _page_for_url, prefetch_url

    if not prefetch_url(url):
        return None
    content = _page_for_url(url)
    if not content:
        return None
    try:
        (evidence_dir / f"source-{idx:03d}.md").write_text(
            f"URL: {url}\n\n{content}", encoding="utf-8"
        )
    except OSError as exc:
        dbg(f"research_first _fetch_and_store: evidence write failed url={url!r} exc={exc!r}")
    return content


def _discard_evidence_file(evidence_dir: Path, idx: int) -> None:
    """Delete a rejected source's evidence file. ``_find_evidence_code`` (used by
    ``_splice_code``) scans the evidence/ DIRECTORY directly, independent of the
    claims ledger — live bug: a LaTeX-heavy Wikipedia math page, correctly
    dropped by the offtopic floor, still sat on disk and got spliced in as
    "example code" because the code producer never saw the rejection."""
    try:
        (evidence_dir / f"source-{idx:03d}.md").unlink(missing_ok=True)
    except OSError as exc:
        # LOW (rf-reviewer): a silently-failed delete re-opens the exact leak
        # this function exists to close — the rejected file survives on disk
        # for _find_evidence_code to pick up later. Log it so that recurrence
        # is traceable instead of silent.
        dbg(f"research_first _discard_evidence_file: unlink failed idx={idx} exc={exc!r}")


def _is_offtopic(url: str, req_words: set[str], requirement: str, judge_client: Any) -> bool:
    """Grounded (page was fetched) does not mean relevant — a page can be a real,
    verbatim-quotable source and still be about a DIFFERENT thing that merely
    shares a name with the task's subject (REBUILD-LESSONS §2: "a fetched,
    quote-verified junk page passes grounding"). Reuses the SAME calibrated
    density-floor + gray-zone judge ``findings.py`` already uses for this exact
    check — including its dictionary-definition/homonym framing, which is the
    live failure this pipeline hit (a subject name that is also an English word).
    Fail-open (keep) on any error or "unknown" — this must never starve research."""
    from studio.findings import _offtopic_url

    return _offtopic_url(url, req_words, requirement, judge_client) is True


def _disambiguate_subject(
    subject: str, domain_words: set[str], requirement: str, judge_client: Any
) -> tuple[str, list[str]]:
    """Resolve WHICH real-world thing <subject> means before researching it
    (REBUILD-LESSONS §9 step 0 / P3). A bare subject-name query samples whatever
    interpretation dominates the web, not the one THIS task means — the
    requirement's own domain words shape the discovery query instead. Returns
    ``(descriptor, anchor_terms)``; fails open to ``(subject, [subject])`` on any
    error or empty result so a disambiguation miss never stalls research (the
    offtopic floor is still the backstop, not the only defense)."""
    if judge_client is None:
        return subject, [subject]
    domain_phrase = " ".join(sorted(domain_words)[:6])
    query = f"{subject} {domain_phrase}".strip()
    results = _search(query)
    if not results:
        return subject, [subject]
    listing = "\n".join(
        f"- {getattr(r, 'title', '')}: {getattr(r, 'snippet', '')}" for r in results[:5]
    )
    prompt = (
        f"TASK: {requirement[:400]}\n\n"
        f'Search results for the query "{query}":\n{listing}\n\n'
        f'Which interpretation of "{subject}" does THIS task mean? Answer in '
        "exactly this format:\nDESCRIPTOR: <short canonical name>\n"
        "ANCHORS: <2-4 comma-separated distinguishing terms>"
    )
    try:
        reply = judge_client.chat([{"role": "user", "content": prompt}])
        text = str(getattr(reply, "text", "") or "")
    except Exception:  # noqa: BLE001 — a bad disambiguation call must never stall research
        return subject, [subject]
    dm = re.search(r"DESCRIPTOR:\s*(.+)", text)
    am = re.search(r"ANCHORS:\s*(.+)", text)
    descriptor = dm.group(1).strip() if dm else subject
    anchors = [a.strip() for a in (am.group(1).split(",") if am else []) if a.strip()]
    return descriptor, (anchors or [subject])


#: The relationship kinds the classifier may return. A model answer outside this
#: set is not trusted → "unknown". No kind presumes a SOFTWARE relationship;
#: "cooperates"/"extends" warrant an integration story, "competes"/"alternative"
#: a comparison, "independent" no relationship artifact at all.
_RELATIONSHIP_KINDS = frozenset(
    {"cooperates", "competes", "extends", "alternative", "independent", "unknown"}
)
#: Neutral descriptor when the relationship is unknown — a section can still be
#: named without asserting a kind. Generic, not a domain term.
_NEUTRAL_RELATIONSHIP_DESCRIPTOR = "Relationship"


@dataclass(frozen=True)
class Relationship:
    """How this task's subjects relate — determined per-task by an LLM, never
    presumed. ``kind`` drives the downstream flow (integration vs comparison vs
    none); ``descriptor`` names the relationship section without a hardcoded
    word; ``mechanism`` is the short hypothesis/framing sentence (search
    directive only, never content by itself); ``mechanism_terms`` are the
    per-task terms grounding replaces the old fixed interface enum with."""

    kind: str
    descriptor: str
    mechanism: str
    mechanism_terms: list[str]


def _classify_relationship(
    subjects: list[str], resolved: dict[str, str], requirement: str, judge_client: Any
) -> Relationship:
    """Classify how the subjects relate for THIS task (R3). Same architecture as
    disambiguation, one step later — identity can't be assumed, and neither can
    the relationship KIND (presuming "integration" was a domain assumption: not
    every pair integrates; some compete, some are independent). One LLM call
    over the resolved descriptors classifies the kind, then gives a descriptor,
    a mechanism/framing hypothesis, and per-task terms to corroborate — NO
    interface vocabulary in the prompt. The hypothesis DIRECTS search and edge
    candidates; grounding rules elsewhere decide what ships. Fails open to
    ``kind='unknown'`` (neutral) on no judge, <2 subjects, parse miss, or any
    error — never stalls research."""
    neutral = Relationship("unknown", _NEUTRAL_RELATIONSHIP_DESCRIPTOR, "", [])
    if judge_client is None or len(subjects) < 2:
        return neutral
    d0, d1 = resolved[subjects[0]], resolved[subjects[1]]
    prompt = (
        f"TASK: {requirement[:400]}\n\n"
        f"Subject A: {subjects[0]} — {d0}\nSubject B: {subjects[1]} — {d1}\n\n"
        "Classify how A and B relate FOR THIS TASK. Weigh how the TASK FRAMES "
        "them above their surface similarity: a task about USING or COMBINING or "
        "INTEGRATING A and B together to build something is cooperation/extension "
        "even if A and B are similar tools; a task that asks to COMPARE, or picks "
        "one as an alternative to the other, is competition. Do NOT default to "
        "either — read the task's intent. They might cooperate, one might extend "
        "the other, they might compete or be alternatives, or be independent. "
        "Answer in exactly this format:\n"
        "KIND: <one of: cooperates, competes, extends, alternative, independent>\n"
        "DESCRIPTOR: <short noun phrase to title a section about this relationship, "
        "e.g. Integration, Extension, Comparison, Alternatives>\n"
        "MECHANISM: <one short sentence: how they connect if cooperating/extending, "
        "or the axis they differ on if competing; empty if independent>\n"
        "TERMS: <comma-separated concrete terms to search for to confirm this>"
    )
    try:
        reply = judge_client.chat([{"role": "user", "content": prompt}])
        text = str(getattr(reply, "text", "") or "")
    except Exception:  # noqa: BLE001 — a bad relationship call must never stall research
        return neutral
    km = re.search(r"KIND:\s*(.+)", text)
    dm = re.search(r"DESCRIPTOR:\s*(.+)", text)
    mm = re.search(r"MECHANISM:\s*(.+)", text)
    tm = re.search(r"TERMS:\s*(.+)", text)
    kind = (km.group(1).strip().lower() if km else "unknown")
    if kind not in _RELATIONSHIP_KINDS:
        kind = "unknown"
    descriptor = (dm.group(1).strip() if dm else "") or _NEUTRAL_RELATIONSHIP_DESCRIPTOR
    mechanism = mm.group(1).strip() if mm else ""
    terms = [t.strip() for t in (tm.group(1).split(",") if tm else []) if t.strip()]
    return Relationship(kind, descriptor, mechanism, terms)


_ANCHOR_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


def _anchor_hits(content: str, anchors: list[str], exclude: frozenset[str] = frozenset()) -> int:
    """Count resolved ANCHORS with >=1 constituent word present in *content* —
    the signal the generic topical floor can't give: a page can be dense in
    domain vocabulary yet share none of the specific anchors a disambiguation
    judge resolved for THIS subject. TOKEN-level, not exact-phrase: a judge
    can invent a descriptive phrase that a real source paraphrases rather than
    repeats verbatim — live failure: exact-phrase matching dropped a real target
    repository over this.
    ``exclude`` drops tokens that would trivially match (e.g. bare subject
    names when *content* is guaranteed to name every subject already) — a
    corroboration check gated on those would rubber-stamp anything."""
    low = content.lower()
    hits = 0
    for anchor in anchors:
        tokens = [t for t in _ANCHOR_TOKEN_RE.findall(anchor.lower()) if t not in exclude]
        if any(re.search(r"\b" + re.escape(t) + r"\b", low) for t in tokens):
            hits += 1
    return hits


def _subject_name_absent(subject: str, content: str) -> bool:
    """True if none of *subject*'s own words appear anywhere in *content*.
    Anchors stay paraphrase-tolerant by design (any ONE shared word counts —
    required so a real README that paraphrases a judge-invented phrase still
    passes), but that tolerance lets a same-CATEGORY, different-PRODUCT page
    pass on generic overlap alone — live failure: a same-category package page
    passed a subject's anchor gate via generic product words while containing
    zero occurrences of the subject name itself. This is an ADDITIONAL
    floor, not a replacement for the anchor check."""
    words = _ANCHOR_TOKEN_RE.findall(subject.lower())
    if not words:
        return False
    low = content.lower()
    return not any(re.search(r"\b" + re.escape(w) + r"\b", low) for w in words)


def _subject_named(subject: str, content: str) -> bool:
    """Strict literal-naming test for the coverage-gate recovery co-occurrence
    check: EVERY significant word of *subject* must appear (word-boundary), not
    just any one (all ``_subject_name_absent`` requires). Two multi-word subjects
    that merely share a generic word ('agents', 'sdk') must not both count as
    'named' on a page that names neither product — that would let joint recovery
    mint an ungrounded joint claim. The gate is a backstop, so a false-negative
    (missing a real joint page) is safe; a false-positive fabricates. Generic for
    any subject string via re.escape."""
    words = _ANCHOR_TOKEN_RE.findall(subject.lower())
    if not words:
        return False
    low = content.lower()
    return all(re.search(r"\b" + re.escape(w) + r"\b", low) for w in words)


def _subject_present(subject: str, anchors: list[str], content: str) -> bool:
    """True if *content* identifies *subject* by a DISTINCTIVE anchor — every token
    of at least one resolved anchor present (word-boundary). Distinctive anchors
    disambiguate an ambiguous bare name: 'Pi' the framework is confirmed by
    'pi-ai'/'pi-agent-core', so a stray 'Inflection Pi' on an unrelated page (which
    carries none of those anchors) is never mistaken for it — exactly how a human
    researcher tells the two apart. Falls back to the bare name when the subject was
    never disambiguated (anchors == [subject]). Generic for any task: anchors come
    from per-subject disambiguation, not baked vocabulary."""
    low = content.lower()
    phrases = anchors if (anchors and anchors != [subject]) else [subject]
    for phrase in phrases:
        toks = _ANCHOR_TOKEN_RE.findall(phrase.lower())
        if toks and all(re.search(r"\b" + re.escape(t) + r"\b", low) for t in toks):
            return True
    return False


def _page_subjects(content: str, subjects: list[str], all_anchors: dict[str, list[str]]) -> list[str]:
    """The subjects a page genuinely discusses, by distinctive-anchor presence."""
    return [s for s in subjects if _subject_present(s, all_anchors.get(s) or [s], content)]


def _mentions_subject(subject: str, anchors: list[str], text: str) -> bool:
    """True if *text* genuinely references *subject* by bare name, EXCLUDING a
    compound-proper-noun collision where the name is a suffix of a different product
    ("Inflection Pi", "Raspberry Pi"). A bare-name hit immediately preceded by a
    capitalised word that is NOT part of the subject's own name/anchors is treated as
    a different product, not the subject. Generic: handles any short/ambiguous subject
    token within a page, no product literals — the within-page complement to the
    cross-page anchor identity check."""
    own = set(_ANCHOR_TOKEN_RE.findall(subject.lower()))
    for a in anchors or []:
        own |= set(_ANCHOR_TOKEN_RE.findall(a.lower()))
    for m in re.finditer(r"\b" + re.escape(subject) + r"\b", text, re.IGNORECASE):
        prev = text[: m.start()].rstrip()
        pm = re.search(r"([A-Za-z][A-Za-z0-9-]*)\s*$", prev)
        if pm and pm.group(1)[0].isupper() and pm.group(1).lower() not in own:
            continue  # compound proper noun → a different product, not the subject
        return True
    return False


def _subject_supported(subject: str, anchors: list[str], claim: str, quote: str) -> bool:
    """Whether a claim's subject tag is backed by its VERBATIM quote (ground truth),
    with the paraphrased claim as fallback ONLY when the quote is silent on the
    subject (the quote may pronoun it). If the quote names the subject token but only
    as a compound-proper-noun collision ("Inflection Pi"), the tag is REJECTED even
    when the claim says a bare "Pi" — the evidence is a different product, and the LLM
    merely paraphrased the qualifier away."""
    if _mentions_subject(subject, anchors, quote):
        return True
    if re.search(r"\b" + re.escape(subject) + r"\b", quote, re.IGNORECASE):
        return False  # token in quote but only as a collision → a different product
    return _mentions_subject(subject, anchors, claim)  # quote silent → trust the claim


def _tag_claim(
    claim: str,
    quote: str,
    loop_subject: str | None,
    page_subjects: list[str],
    all_anchors: dict[str, list[str]],
) -> list[str]:
    """Ground a claim's subject tags. On an anchor-confirmed page a subject counts
    only when GENUINELY mentioned — ``_mentions_subject`` rejects a compound-proper-
    noun collision ("Inflection Pi") so a coincidental same-token co-mention can't
    mint a joint claim. The page's own subject (*loop_subject*) is always included; a
    joint-loop claim (loop_subject None) naming no genuine confirmed subject grounds
    nothing and returns []."""
    # Back a subject by the VERBATIM QUOTE (ground truth), claim only as fallback when
    # the quote is silent — the model can paraphrase "Inflection Pi" -> bare "Pi",
    # defeating the lexical collision guard on the claim; the quote still carries it.
    mentioned = [s for s in page_subjects if _subject_supported(s, all_anchors.get(s) or [s], claim, quote)]
    if loop_subject is None:
        return mentioned
    return list(dict.fromkeys([loop_subject, *[s for s in mentioned if s != loop_subject]]))


def _research(
    subjects: list[str],
    evidence_dir: Path,
    requirement: str,
    judge_client: Any,
    *,
    emit: EmitFn,
) -> tuple[dict[str, list[dict[str, str]]], list[str], "Relationship", dict[str, list[str]]]:
    """Per subject: disambiguate FIRST (never search the bare subject name
    alone), then fetch up to ``_MAX_SOURCES_PER_SUBJECT`` distinct-domain,
    on-topic pages using the resolved descriptor+anchors. Once all subjects are
    resolved, CLASSIFY their RELATIONSHIP (kind + descriptor + mechanism + terms,
    R3) and derive the joint queries FROM it, under the ``"__joint__"`` ledger
    key. Returns ``(ledger, assumptions, relationship)`` — the resolved
    interpretation of each subject and the classified relationship, for an
    honest Scope-section disclosure (P4/D3) and the diagram's edge candidates.
    ``relationship.mechanism_terms`` let a downstream consumer corroborate the
    hypothesis against real evidence before ever printing it as content
    (team-lead: a hypothesis directs search/prompts but never becomes content
    by itself)."""
    from studio.textutil import content_word_stems

    emit = emit or (lambda *_a: None)
    domain_words = content_word_stems(_base_task_text(requirement))
    ledger: dict[str, list[dict[str, str]]] = {}
    resolved: dict[str, str] = {}
    all_anchors: dict[str, list[str]] = {}
    assumptions: list[str] = []
    idx = 0
    for subject in subjects:
        descriptor, anchors = _disambiguate_subject(subject, domain_words, requirement, judge_client)
        resolved[subject] = descriptor
        all_anchors[subject] = anchors
        assumptions.append(f'Interpreting "{subject}" as {descriptor} (anchors: {", ".join(anchors)}).')
        dbg(f"research_first RESEARCH: disambiguate subject={subject!r} descriptor={descriptor!r} anchors={anchors}")

        anchor_phrase = " ".join(anchors[:3])
        # Brand-word queries rank marketing sites; a quoted descriptor + a
        # descriptor+anchor query rank the actual product (team-lead fix 2).
        queries = [f'"{descriptor}"', f"{descriptor} {anchor_phrase}", f"{subject} {anchor_phrase} example"]
        disambiguated = anchors != [subject]  # real judge output, not the fail-open sentinel
        sources: list[dict[str, str]] = []
        seen_domains: set[str] = set()
        for query in queries[:_MAX_QUERIES_PER_SUBJECT]:
            if len(sources) >= _MAX_SOURCES_PER_SUBJECT:
                break
            results = _search(query)
            emit("research_query", {"subject": subject, "query": query, "n_results": len(results)})
            for r in results:
                if len(sources) >= _MAX_SOURCES_PER_SUBJECT:
                    break
                url = str(getattr(r, "url", "") or "").strip()
                domain = _domain(url)
                if not url or domain in seen_domains:
                    continue
                idx += 1
                content = _fetch_and_store(url, evidence_dir, idx)
                if not content:
                    continue
                # Offtopic floor judges against the CLEAN base requirement's
                # domain words (REBUILD-LESSONS §2), not the template-diluted one.
                if _is_offtopic(url, domain_words, requirement, judge_client):
                    _discard_evidence_file(evidence_dir, idx)
                    continue
                # The topical floor passes anything domain-adjacent, but a
                # page matching ZERO resolved anchors is dropped even though it
                # cleared the floor.
                if disambiguated and _anchor_hits(content, anchors) == 0:
                    dbg(f"research_first RESEARCH: anchor-mismatch drop url={url!r} subject={subject!r}")
                    _discard_evidence_file(evidence_dir, idx)
                    continue
                if disambiguated and _subject_name_absent(subject, content):
                    dbg(f"research_first RESEARCH: subject-name-absent drop url={url!r} subject={subject!r}")
                    _discard_evidence_file(evidence_dir, idx)
                    continue
                seen_domains.add(domain)
                sources.append({"url": url, "content": content})
        ledger[subject] = sources
        dbg(f"research_first RESEARCH: subject={subject!r} queries={len(queries[:_MAX_QUERIES_PER_SUBJECT])} fetched={len(sources)}")

    relationship = Relationship("unknown", _NEUTRAL_RELATIONSHIP_DESCRIPTOR, "", [])
    if len(subjects) >= 2:
        relationship = _classify_relationship(subjects, resolved, requirement, judge_client)
        mechanism = relationship.mechanism
        terms = relationship.mechanism_terms
        if mechanism:
            assumptions.append(f"Hypothesized relationship: {mechanism}.")
        elif relationship.kind == "independent":
            assumptions.append(
                f"{subjects[0]} and {subjects[1]} appear independent — no relationship assumed."
            )
        else:
            assumptions.append(f"Relationship kind: {relationship.kind}.")
        dbg(
            f"research_first RESEARCH: relationship subjects={subjects} "
            f"kind={relationship.kind!r} mechanism={mechanism!r}"
        )

        d0, d1 = resolved[subjects[0]], resolved[subjects[1]]
        joint: list[dict[str, str]] = []
        # Joint queries are derived from the classified RELATIONSHIP (mechanism
        # + per-task terms), not generic subject-A+subject-B concatenation —
        # replacing the anchor-derived version, which still left joint fetches
        # thin (team-lead: "that's why joint fetches have been thin").
        # The prose `mechanism` sentence (100+ chars) matches no real page — a
        # both-names blob + that sentence returns SEO junk (live: JQ1 hit
        # Japanese-finance spam). Search TERMS instead, and add per-side probes:
        # each side's integration-surface page (e.g. an MCP-server docs page)
        # only surfaces under a `{descriptor} {terms}` query, never a both-names
        # blob. All pieces are LLM-derived (descriptors, terms) — no literals.
        verify_words = " ".join(terms[:4])
        paired = f"{d0} {d1}".strip()
        joint_queries = [
            q
            for q in (
                f"{paired} {verify_words}".strip(),
                f"{d0} {verify_words}".strip(),
                f"{d1} {verify_words}".strip(),
            )
            if q
        ]
        dbg(f"research_first RESEARCH: joint_queries={joint_queries!r}")
        # The joint loop had no anchor gate at all before this — only the
        # generic offtopic floor. A page can be broadly on-topic yet name
        # neither subject's anchors at all; gate on the UNION of every
        # subject's anchors (a joint page only needs to connect to one side
        # to be a real integration source, not both).
        union_anchors = [a for subject in subjects for a in all_anchors.get(subject, [])]
        # A joint source must be an integration page, not a subject homepage
        # re-fetched under the joint loop (the clamp at :698 would re-tag it with
        # both subjects, faking a joint claim). Skip anything already in a
        # subject ledger.
        subject_urls = {s["url"] for subj in subjects for s in ledger.get(subj, [])}
        for query in joint_queries[:_MAX_JOINT_QUERIES]:
            results = _search(query)
            emit("research_query", {"subject": "__joint__", "query": query, "n_results": len(results)})
            kept = 0
            for r in results:
                if kept >= _MAX_SOURCES_PER_JOINT_QUERY:
                    break
                url = str(getattr(r, "url", "") or "").strip()
                if not url:
                    continue
                if url in subject_urls:
                    dbg(f"research_first RESEARCH: joint subject-url skip url={url!r}")
                    continue
                idx += 1
                content = _fetch_and_store(url, evidence_dir, idx)
                if not content:
                    continue
                if _is_offtopic(url, domain_words, requirement, judge_client):
                    _discard_evidence_file(evidence_dir, idx)
                    continue
                if union_anchors and _anchor_hits(content, union_anchors) == 0:
                    dbg(f"research_first RESEARCH: joint anchor-mismatch drop url={url!r}")
                    _discard_evidence_file(evidence_dir, idx)
                    continue
                joint.append({"url": url, "content": content})
                kept += 1
        ledger["__joint__"] = joint
        dbg(f"research_first RESEARCH: joint queries={len(joint_queries[:_MAX_JOINT_QUERIES])} fetched={len(joint)}")
    return ledger, assumptions, relationship, all_anchors


# ---------------------------------------------------------------------------
# CLAIMS — one grounded extraction call per fetched source.
# ---------------------------------------------------------------------------

_CLAIM_BLOCK_RE = re.compile(r"\n(?=CLAIM:)")


_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")  # group(1) = link label (for _plainer_markdown)


def _strip_boilerplate(text: str) -> str:
    """Drop navigation/chrome blocks from converted page text — generic main-content
    extraction, STRUCTURAL not phrase-based (no per-site literals): a block is chrome
    if it is link-dominated (a menu is a list of links) or a run of very short
    link/menu lines. Prose blocks (the actual body) are kept. Never strips to empty
    (fail-open to the original). Used before the extraction window so a rendered
    page's nav chrome can't crowd the real body out of the model's view — live:
    GitHub's first 8k was pure 'Sign in / Navigation Menu / Copilot' links."""
    kept: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        if not para.strip():
            continue
        # Chrome = links with almost NO prose around them (a menu is a list of bare
        # links). A CONTENT row (`[name](url): role`, a package table) is a link PLUS a
        # real description — keep it. Decide PER LINE: a "menu line" is a linked line
        # whose non-link text is ≤1 word. Both the old ratio test AND the old short-
        # line test wrongly stripped a concise row like `[pi-ai](url): Unified LLM API`
        # (label >50% of a short line), so the Pi package table died before extraction
        # (0/4 packages; codex 2026-07-09). Bias to KEEP: a surviving nav block is
        # harmless (query-directed, verbatim-grounded extraction won't quote a menu),
        # a stripped content row loses the fact forever.
        lines = [ln for ln in para.splitlines() if ln.strip()]
        linky = [ln for ln in lines if _MD_LINK_RE.search(ln)]
        if linky:
            menu_lines = sum(
                1 for ln in linky
                if len(re.findall(r"[A-Za-z0-9]{2,}", _MD_LINK_RE.sub(" ", ln))) <= 1
            )
            if menu_lines / len(lines) > 0.8:
                continue  # mostly bare-link lines = nav chrome
        kept.append(para)
    out = "\n\n".join(kept)
    return out if out.strip() else text


def _extraction_window(
    content: str, subjects: list[str], all_anchors: dict[str, list[str]] | None,
    limit: int = _CLAIM_SOURCE_CHARS,
) -> str:
    """The most subject-relevant ``limit`` chars for claim extraction. A rendered
    long page (a GitHub repo is 100k+ chars whose first 8k is pure nav chrome and
    whose README body sits at char ~93k) makes a blind ``content[:limit]`` window
    feed the extractor boilerplate — live: gemma extracted "6.5k stars" from the
    chrome and never saw "uses the Pi SDK side by side". Score paragraphs by how many
    distinct subject/anchor terms they contain (distinctive anchors live in the body,
    not the nav chrome) and take the highest-scoring first, up to ``limit``. Fall back
    to the head when nothing matches or no terms are known. Generic: no per-site rules."""
    if len(content) <= limit:
        return content
    content = _strip_boilerplate(content)  # drop nav/menu chrome first
    if len(content) <= limit:
        return content
    terms: set[str] = set()
    for s in subjects:
        terms |= {w for w in _ANCHOR_TOKEN_RE.findall(s.lower()) if len(w) >= 3}
    for anchors in (all_anchors or {}).values():
        for a in anchors:
            terms |= {w for w in _ANCHOR_TOKEN_RE.findall(a.lower()) if len(w) >= 3}
    if not terms:
        return content[:limit]
    pats = [re.compile(r"\b" + re.escape(w) + r"\b") for w in terms]
    scored: list[tuple[int, int, str]] = []
    for i, para in enumerate(re.split(r"\n\s*\n", content)):
        low = para.lower()
        hits = sum(1 for p in pats if p.search(low))
        if hits:
            scored.append((-hits, i, para))  # -hits: most-relevant first; i: stable tie-break
    scored.sort()
    picked: list[str] = []
    size = 0
    for _neg, _i, para in scored:
        picked.append(para)
        size += len(para) + 2
        if size >= limit:
            break
    joined = "\n\n".join(picked)
    return joined[:limit] if joined else content[:limit]


_EXTRACT_OVERLAP = 400
_MAX_EXTRACT_WINDOWS = 20


def _content_windows(content: str, size: int = _CLAIM_SOURCE_CHARS, overlap: int = _EXTRACT_OVERLAP) -> list[str]:
    """Cover the WHOLE (boilerplate-stripped) content in overlapping windows so
    extraction reads the FULL file — never a truncated head nor a relevance-selected
    subset that can miss a body paragraph (live: the Craft README's "uses the Pi SDK
    side by side" sits at char ~93k / paragraph 39 of a 120k rendered page). Overlap
    keeps a sentence split across a boundary recoverable. Bounded by
    ``_MAX_EXTRACT_WINDOWS`` (dbg-logged when hit — never a silent truncation)."""
    body = _strip_boilerplate(content) if len(content) > size else content
    if len(body) <= size:
        return [body]
    step = max(size - overlap, 1)
    windows = [body[i : i + size] for i in range(0, len(body), step)]
    if len(windows) > _MAX_EXTRACT_WINDOWS:
        dbg(f"research_first _content_windows: capped {len(windows)} -> {_MAX_EXTRACT_WINDOWS} windows (len={len(body)})")
        windows = windows[:_MAX_EXTRACT_WINDOWS]
    return windows


_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MD_BULLET_RE = re.compile(r"(?m)^[ \t]*[*\-]\s+")
_MD_PIPE_RE = re.compile(r"[ \t]*\|[ \t]*")


def _plainer_markdown(text: str) -> str:
    """Render link/bold/table markup down to the readable text a human sees, so a
    VERBATIM quote can capture a fact that lives INSIDE the markup. Live proof: the Pi
    README states its architecture as `[@earendil-works/pi-ai](url): Unified LLM API`
    rows — the package NAME sits inside link syntax, so no clean verbatim quote could
    ever name it, and extraction produced 0/4 packages. Stripping the decoration
    (link→text, bold→text, bullet markers dropped, table pipes→space) yields 4/4.
    Structural only — it never rewrites prose. Applied AFTER `_content_windows`'
    boilerplate detection (which keys on link density), never before, so nav-chrome
    stripping is unaffected."""
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_BULLET_RE.sub("", text)
    return _MD_PIPE_RE.sub(" ", text)


def _extract_claims_from_source(
    url: str, content: str, subjects: list[str], client: Any, *,
    loop_subject: str | None = None, all_anchors: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """(claim, quote, subject-tags) rows from one fetched page. A claim whose quote is
    not a verbatim substring of the cached page is dropped — deterministic grounding,
    no judge call (§11.3 D1). Reads the FULL file via a moving window (``_content_
    windows``): a long rendered page is processed in overlapping chunks so a body fact
    deep in the file (e.g. an architecture statement at char ~93k) is seen, not lost
    to a head-truncation that would be pure nav chrome. Claims are de-duplicated by
    quote across the overlapping windows. ``all_anchors`` is accepted for signature
    parity with the caller; windowing now covers everything so no relevance-selection
    is needed here."""
    if client is None or not (content or "").strip():
        return []
    # Verbatim check runs against markdown-stripped content so a quote the model reads
    # off a de-decorated window (e.g. a package name freed from its `[..](url)` link)
    # still matches. Windows come from RAW content (boilerplate detection intact), then
    # get stripped for the model's view — see _plainer_markdown.
    norm_content = " ".join(_plainer_markdown(content).split()).lower()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for window in _content_windows(content):
        window = _plainer_markdown(window)
        subj = ", ".join(subjects) or "the topic"
        # Query-directed + structure-aware extraction. A generic "extract 3-6 facts"
        # pass reads a source the way nobody researches it — it grabs the easiest
        # declarative sentences and misses the architecture. Live proof: the Pi
        # README's 4-package table produced ZERO package claims; the 4 extracted were
        # peripheral (permissions, session-sharing). A researcher reads a source
        # AGAINST the question ("what's the architecture, how do I use it, how do these
        # relate?") and reads a component TABLE as the architecture. So: direct the
        # model at those universal research facets (generic — keyed on `subjects`, no
        # task/domain literal) and explicitly license a terse list/table ROW as a valid
        # quote, since that is exactly where READMEs put the component structure the old
        # "10-40 word prose" framing skipped. Verbatim-quote grounding is unchanged
        # (still the anti-fabrication check), so this widens WHAT is captured, not
        # whether it must be real.
        prompt = (
            f"Extract the 3-8 MOST IMPORTANT factual CLAIMS about {subj} from the SOURCE "
            "TEXT below. Prioritise, in this order: (1) architecture and components — "
            "packages, layers, modules, and what each does; (2) how to build with or use "
            "it; (3) how the subjects relate to or depend on each other. Prefer these "
            "over peripheral facts (licensing, install trivia, contribution notes). A "
            "component/package LIST or TABLE ROW is itself a high-value claim — capture "
            "it. If the source lists components/packages in a TABLE or bulleted list, "
            "emit ONE claim per ROW and name each item — never collapse a roster into a "
            "count (a nearby sentence may say e.g. 'three packages' and name only some; "
            "the full table is the authority, so capture every row it lists, including "
            "any the summarising sentence omits). Each claim MUST be grounded in a QUOTE copied VERBATIM from the source "
            "(exact wording); a short list/table row such as a 'name — role' line is a "
            "perfectly good quote and need NOT be a full sentence. If the QUOTE states "
            "that one named thing uses, routes to, powers, depends on, connects to, or "
            "provides another (including one thing linked to SEVERAL targets, e.g. one "
            "SDK connecting to several providers), add a RELATIONS line listing each as "
            "'head | relation | tail', ONE per line, using only names that appear in the "
            "quote; omit RELATIONS when the claim states no such link. Output ONE block "
            "per claim in exactly this format, nothing else:\n\n"
            "CLAIM: <one sentence>\nQUOTE: <verbatim quote from the source>\n"
            f"SUBJECTS: <comma-separated subset of: {', '.join(subjects) or '(none named)'}>\n"
            "RELATIONS:\n<head | relation | tail>  (optional; omit line if none)\n\n"
            f"=== SOURCE ({url}) ===\n{window}\n=== END SOURCE ==="
        )
        try:
            reply = client.chat([{"role": "user", "content": prompt}])
            text = str(getattr(reply, "text", "") or "")
        except Exception as exc:  # noqa: BLE001 — a bad claim call must never break the run
            dbg(f"research_first _extract_claims_from_source: call failed url={url!r} exc={exc!r}")
            continue
        for block in _CLAIM_BLOCK_RE.split(text):
            cm = re.search(r"CLAIM:\s*(.+)", block)
            qm = re.search(r"QUOTE:\s*(.+)", block)
            if not cm or not qm:
                continue
            claim = cm.group(1).strip()
            # Escape-flood guard (REBUILD-LESSONS §2 / live findings.py precedent): a
            # QUOTE can arrive with literal "\n"/"\t"/"\r" sequences the model typed as
            # text rather than real whitespace — collapse before verbatim-checking.
            quote = re.sub(r"(?:\\+[ntr])+", " ", qm.group(1).strip().strip('"'))
            norm_quote = " ".join(quote.split()).lower()
            if (
                not norm_quote
                or norm_quote in seen
                or norm_quote not in norm_content
                or len(quote) > _MAX_QUOTE_CHARS
                or _is_markup_dense_quote(quote)
            ):
                continue  # dup across windows, unverifiable, oversized, or markup noise
            seen.add(norm_quote)
            # Provisional tags — the caller (_build_claims / _recover_claims) re-grounds
            # every tag via anchor-confirmed page identity (_tag_claim), so this is only
            # a best-effort default. Single-subject loop clamps to its own subject.
            sm = re.search(r"SUBJECTS:\s*(.+)", block)
            tags = [s.strip() for s in (sm.group(1).split(",") if sm else []) if s.strip()]
            if loop_subject:
                if tags != [loop_subject]:
                    tags = [loop_subject]
            else:
                tags = [s for s in subjects if _subject_named(s, content)]
            rels = _parse_relation_triples(block, norm_quote)
            out.append({"claim": claim, "quote": quote, "url": url, "subjects": tags, "relations": rels})
    return out


def _token_in(token: str, norm_text: str) -> bool:
    """Whole-token membership via word boundary — the file's anti-fabrication
    convention (cf. _subject_supported), not a raw substring test. ``norm_text`` is
    already lowercased by the caller."""
    return bool(re.search(r"\b" + re.escape(token.lower()) + r"\b", norm_text))


def _parse_relation_triples(block: str, norm_quote: str) -> list[dict[str, str]]:
    """Lift ``head | relation | tail`` triples from a claim block's optional RELATIONS
    line(s). Anti-fabrication (same bar as the QUOTE): a triple survives only if BOTH
    its head and tail appear in the verbatim quote — the model may NAME the relation
    verb freely, but the two endpoints must be quote-grounded, so a hallucinated party
    can't enter the graph. Directed and de-duplicated by (head, tail). This is the
    KGGen shape — the weak local model does the in-quote NER, code validates + keeps
    only grounded edges (mirrors the diagram triple pattern). Generic: no domain/
    provider/verb literal — grounding is purely 'is this token in the quote'."""
    m = re.search(r"RELATIONS:\s*(.+?)(?:\nCLAIM:|\Z)", block, re.S)
    if not m:
        return []
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in m.group(1).splitlines():
        parts = [p.strip().strip('"').strip() for p in line.split("|")]
        if len(parts) != 3:
            continue
        head, rel, tail = parts
        if not (head and rel and tail):
            continue
        # Endpoints must be grounded in the verbatim quote as WHOLE tokens (the relation
        # verb is free text). Word-boundary, not substring — matching the file's own
        # anti-fabrication convention (_subject_supported): a short product name like
        # "Pi"/"Go"/"AI" must not ground on being a substring of "shipping"/"Google".
        if not (_token_in(head, norm_quote) and _token_in(tail, norm_quote)):
            continue
        if head.lower() == tail.lower():
            continue  # a self-edge is noise, not a relationship
        key = (head.lower(), tail.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"head": head, "rel": rel, "tail": tail})
    return out


def _extract_relationship_claim(
    url: str, content: str, subjects_present: list[str],
    all_anchors: dict[str, list[str]], loop_subject: str | None, client: Any,
) -> list[dict[str, Any]]:
    """On a page anchor-confirmed for >=2 subjects, extract 1-2 claims stating HOW
    they relate — integration, dependency, one embedding the other, or comparison.
    Generic per-page extraction routinely skips this cross-subject fact (it pulls
    per-subject features); a human researcher reads a README specifically for it.
    Grounded in a verbatim quote, tagged with the co-occurring subjects so it lands
    as a joint claim. Returns [] on any failure (fail-open)."""
    if client is None or len(subjects_present) < 2 or not (content or "").strip():
        return []
    a, b = subjects_present[0], subjects_present[1]
    prompt = (
        f"The SOURCE below discusses BOTH {a} and {b}. Extract 1-2 CLAIMS stating HOW "
        f"{a} and {b} relate — integration, dependency, one using or embedding the "
        f"other, or a direct comparison. Each claim MUST be grounded in a QUOTE copied "
        f"VERBATIM from the source (10-40 words, exact wording). After the QUOTE, if it "
        f"states directed relationships between named things (one component using, "
        f"routing to, powering, depending on, or embedding another — including one "
        f"component connecting to several targets), add a RELATIONS line listing each as "
        f"'head | relation | tail', ONE per line, using only names that appear in the "
        f"quote; omit RELATIONS entirely if there are none. If the page does not actually "
        f"relate them, output nothing. Output ONE block per claim, nothing else:\n"
        f"CLAIM: <one sentence>\nQUOTE: <verbatim quote>\n"
        f"RELATIONS:\n<head | relation | tail>\n<head | relation | tail>\n\n"
        f"=== SOURCE ({url}) ===\n{_extraction_window(content, subjects_present, all_anchors)}\n=== END SOURCE ==="
    )
    try:
        reply = client.chat([{"role": "user", "content": prompt}])
        text = str(getattr(reply, "text", "") or "")
    except Exception as exc:  # noqa: BLE001 — a bad relationship call must not break the run
        dbg(f"research_first _extract_relationship_claim: call failed url={url!r} exc={exc!r}")
        return []
    norm_content = " ".join(content.split()).lower()
    out: list[dict[str, Any]] = []
    for block in _CLAIM_BLOCK_RE.split(text):
        cm = re.search(r"CLAIM:\s*(.+)", block)
        qm = re.search(r"QUOTE:\s*(.+)", block)
        if not cm or not qm:
            continue
        claim = cm.group(1).strip()
        quote = re.sub(r"(?:\\+[ntr])+", " ", qm.group(1).strip().strip('"'))
        norm_quote = " ".join(quote.split()).lower()
        if (
            not norm_quote
            or norm_quote not in norm_content
            or len(quote) > _MAX_QUOTE_CHARS
            or _is_markup_dense_quote(quote)
        ):
            continue
        rels = _parse_relation_triples(block, norm_quote)
        # Quote-backed (see _subject_supported): the verbatim quote carries a dropped
        # qualifier, so the collision guard can't be paraphrased away.
        others = [
            s for s in subjects_present
            if s != loop_subject and _subject_supported(s, all_anchors.get(s) or [s], claim, quote)
        ]
        if loop_subject is not None:
            # On a subject's OWN page the page owner is one party even when the
            # sentence pronouns it ("It uses the Pi SDK side by side" on Craft's
            # README). Require >=1 GENUINE other subject — the collision guard drops
            # "Inflection Pi", so a coincidental provider-list co-mention yields no
            # other and is skipped.
            if not others:
                continue
            tags = list(dict.fromkeys([loop_subject, *others]))
        else:
            # Joint-fetched source with no page owner: require >=2 genuine mentions.
            tags = [s for s in subjects_present if _subject_supported(s, all_anchors.get(s) or [s], claim, quote)]
            if len(tags) < 2:
                continue
        out.append({"claim": claim, "quote": quote, "url": url, "subjects": tags, "relations": rels})
    return out[:2]


def _build_claims(
    ledger: dict[str, list[dict[str, str]]],
    subjects: list[str],
    client: Any,
    ws_dir: Path,
    all_anchors: dict[str, list[str]],
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for key, sources in ledger.items():
        loop_subject = None if key == "__joint__" else key
        for src in sources:
            # Which subjects does THIS page genuinely discuss (distinctive anchors)?
            # This is the identity check — a page in one subject's loop that also
            # documents another subject (e.g. Craft's README naming its Pi backend)
            # is a real joint source; a page merely sharing an ambiguous token is not.
            page_subjects = _page_subjects(src["content"], subjects, all_anchors)
            for c in _extract_claims_from_source(
                src["url"], src["content"], subjects, client,
                loop_subject=loop_subject, all_anchors=all_anchors,
            ):
                tags = _tag_claim(c["claim"], c["quote"], loop_subject, page_subjects, all_anchors)
                if not tags:
                    continue  # grounds no confirmed subject — never woven in
                c["subjects"] = tags
                claims.append(c)
            # A page confirmed for >=2 subjects states their relationship explicitly;
            # a targeted pass pulls that integration/dependency claim (which generic
            # per-page extraction skips), and the collision guard inside it drops a
            # coincidental co-mention so only a genuine relationship lands as joint.
            if len(page_subjects) >= 2:
                claims.extend(
                    _extract_relationship_claim(
                        src["url"], src["content"], page_subjects, all_anchors, loop_subject, client
                    )
                )
    _persist_claims(claims, ws_dir)
    n_sources = sum(len(v) for v in ledger.values())
    dbg(f"research_first CLAIMS: {len(claims)} claims from {n_sources} sources")
    return claims


def _persist_claims(claims: list[dict[str, Any]], ws_dir: Path) -> None:
    """Write the claims ledger to claims.jsonl. Called by both _build_claims and
    _coverage_gate so audit/downstream always read the augmented set."""
    try:
        with (ws_dir / "claims.jsonl").open("w", encoding="utf-8") as fh:
            for c in claims:
                fh.write(json.dumps(c) + "\n")
    except OSError as exc:
        dbg(f"research_first _persist_claims: fail-open exc={exc!r}")


def _next_evidence_idx(evidence_dir: Path) -> int:
    """Highest existing source-NNN index (0 if none), so recovery fetches append
    without overwriting _research's evidence files. Fail-open to 0."""
    try:
        return max(
            (int(p.stem.split("-")[1]) for p in evidence_dir.glob("source-*.md")),
            default=0,
        )
    except (ValueError, OSError) as exc:
        dbg(f"research_first _next_evidence_idx: fail-open exc={exc!r}")
        return 0


def _recover_claims(
    query: str,
    loop_subject: str | None,
    subjects: list[str],
    all_anchors: dict[str, list[str]],
    evidence_dir: Path,
    idx: int,
    requirement: str,
    client: Any,
    judge_client: Any,
) -> tuple[int, list[dict[str, Any]]]:
    """Fetch for *query*, extract claims, and ground the subject tags in real
    co-occurrence — never fabricated. Per-subject recovery (*loop_subject* set)
    forwards it so every claim clamps to that one vetted subject. Joint recovery
    (*loop_subject* None) keeps a page only if it literally names >=2 subjects and
    tags its claims with exactly the *present* subjects, mirroring the union-anchor
    gate ``_research``'s joint loop applies — so a single-subject page can never
    mint a joint claim. Returns ``(idx, rows)``."""
    from studio.textutil import content_word_stems

    req_words = content_word_stems(_base_task_text(requirement))
    rows: list[dict[str, Any]] = []
    for r in _search(query)[:_MAX_SOURCES_PER_JOINT_QUERY]:
        url = str(getattr(r, "url", "") or "").strip()
        if not url:
            continue
        idx += 1
        content = _fetch_and_store(url, evidence_dir, idx)
        if not content:
            continue
        if _is_offtopic(url, req_words, requirement, judge_client):
            _discard_evidence_file(evidence_dir, idx)
            continue
        page_subjects = _page_subjects(content, subjects, all_anchors)
        if loop_subject is None:
            if len(page_subjects) < 2:
                # Joint recovery, but <2 subjects are anchor-confirmed on the page —
                # it cannot ground a joint claim; drop it to prevent a fabricated tag.
                dbg(f"research_first COVERAGE-GATE: joint recovery drop page_subjects={page_subjects!r} url={url!r}")
                _discard_evidence_file(evidence_dir, idx)
                continue
            tag_subjects = page_subjects
        elif not _subject_present(loop_subject, all_anchors.get(loop_subject) or [loop_subject], content):
            # Per-subject recovery, but the page doesn't ANCHOR-CONFIRM the thin
            # subject (bare-name presence isn't enough — an ambiguous same-token
            # collision like "Inflection Pi" would otherwise pass, then _tag_claim
            # could pair it with an anchor-confirmed other subject into a fabricated
            # joint claim). Same identity rule as the main path.
            dbg(f"research_first COVERAGE-GATE: per-subject recovery drop subject={loop_subject!r} url={url!r}")
            _discard_evidence_file(evidence_dir, idx)
            continue
        else:
            # Page was fetched for (and names) loop_subject; include it plus any
            # OTHER anchor-confirmed subject so a genuine joint recovery still counts.
            tag_subjects = list(dict.fromkeys([loop_subject, *page_subjects]))
        for c in _extract_claims_from_source(
            url, content, subjects, client, loop_subject=loop_subject, all_anchors=all_anchors
        ):
            tags = _tag_claim(c["claim"], c["quote"], loop_subject, tag_subjects, all_anchors)
            if not tags:
                continue
            c["subjects"] = tags
            rows.append(c)
    return idx, rows


def _coverage_gate(
    claims: list[dict[str, Any]],
    subjects: list[str],
    relationship: "Relationship",
    all_anchors: dict[str, list[str]],
    evidence_dir: Path,
    requirement: str,
    client: Any,
    judge_client: Any,
    ws_dir: Path,
    *,
    emit: EmitFn = None,
) -> list[dict[str, Any]]:
    """Fail-visible floor between CLAIMS and WRITE: if any subject is thin or a
    needed joint claim is missing, fire ONE grounded recovery fetch and re-persist
    the augmented set. Never fabricates tags (recovery grounds them in real
    co-occurrence) and never drops a diagram — downstream keeps its no-fabricate
    self-refusal. Fail-open: any error returns *claims* unchanged."""
    emit = emit or (lambda *_a: None)
    try:
        multi = len(subjects) >= 2
        # Joint recovery fires only for integration-shaped relationships. These are
        # the classifier's own kind enum (R3 taxonomy), NOT domain/task literals: a
        # competes/alternative task wants a comparison table, not a joint claim, so
        # forcing one there would itself fabricate. Mirrors `wants_integration`.
        needs_joint = multi and relationship.kind in _INTEGRATION_KINDS

        def _subj_n(s: str) -> int:
            return sum(1 for c in claims if s in (c.get("subjects") or []))

        def _joint_n(cl: list[dict[str, Any]]) -> int:
            return sum(1 for c in cl if len(c.get("subjects") or []) >= 2)

        thin = [s for s in subjects if _subj_n(s) < _MIN_CLAIMS_PER_SUBJECT]
        joint_missing = needs_joint and _joint_n(claims) < 1
        if not thin and not joint_missing:
            return claims

        dbg(f"research_first COVERAGE-GATE: thin={thin!r} joint_missing={joint_missing}")
        emit("coverage_recovery", {"thin": thin, "joint_missing": joint_missing})
        idx = _next_evidence_idx(evidence_dir)
        terms = " ".join((relationship.mechanism_terms or [])[:3])

        recovered: list[dict[str, Any]] = []
        for s in thin:
            idx, rows = _recover_claims(
                f"{s} {terms}".strip() or s, s, subjects, all_anchors,
                evidence_dir, idx, requirement, client, judge_client,
            )
            recovered.extend(rows)
        if joint_missing:
            a, b = subjects[0], subjects[1]
            # Search TERMS, not the prose mechanism sentence (100+ chars matches no
            # real page — same fix the RESEARCH joint loop applies).
            mech = terms or relationship.mechanism
            idx, rows = _recover_claims(
                f"{a} {b} {mech}".strip(), None, subjects, all_anchors,
                evidence_dir, idx, requirement, client, judge_client,
            )
            recovered.extend(rows)

        merged = claims + recovered
        _persist_claims(merged, ws_dir)

        still_thin = [s for s in subjects if sum(1 for c in merged if s in (c.get("subjects") or [])) < _MIN_CLAIMS_PER_SUBJECT]
        still_joint_missing = needs_joint and _joint_n(merged) < 1
        if still_thin or still_joint_missing:
            dbg(f"research_first COVERAGE-GATE: failed_partial still_thin={still_thin!r} still_joint_missing={still_joint_missing}")
            emit("coverage_failed_partial", {"still_thin": still_thin, "still_joint_missing": still_joint_missing})
        return merged
    except Exception as exc:  # noqa: BLE001 — the gate must never crash the run
        dbg(f"research_first COVERAGE-GATE: fail-open exc={exc!r}")
        try:
            # Fail-open, but still fail-VISIBLE: signal the gate failure so a caller
            # isn't left assuming coverage was met.
            (emit or (lambda *_a: None))("coverage_failed_partial", {"error": repr(exc)})
        except Exception:  # noqa: BLE001 — a failing emitter must not crash the gate
            pass
        return claims


def _claims_for_section(
    claims: list[dict[str, Any]], section: str, seq: int = 0
) -> list[dict[str, Any]]:
    """Cap claims per section, ROUND-ROBIN across subjects (keyed by each claim's
    first subject tag — generic, works for any subject count/name) so a
    claims-heavy subject can never starve a claims-light one out of the cap.
    ``claims[:cap]`` first-N reproduced "names two subjects, covers one" one
    stage later: subject-1's claims (ordered first by ``_build_claims``) filled
    the cap before subject-2's were ever considered."""
    cap = _MAX_CLAIMS_EVIDENCE if _EVIDENCE_SECTION_RE.search(section) else _MAX_CLAIMS_OTHER
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in claims:
        tags = c.get("subjects") or ["__untagged__"]
        # A claim tagged with >1 subject (a genuine joint claim — every joint-
        # loop claim, post the "never a single-subject tag" clamp) gets its
        # OWN bucket. Grouping it under tags[0] would silently inflate
        # whichever subject happens to be listed first and starve that
        # subject's own real per-subject claims of cap headroom.
        key = tags[0] if len(tags) == 1 else "__joint__"
        groups.setdefault(key, []).append(c)
    order = list(groups)
    # The same claims fed to every same-cap section made gemma (greedy) open each
    # section with the IDENTICAL lead claim — live: 4 sections shared a byte-
    # identical first sentence and the judge scored evidence-synthesis 0.0. Rotate
    # each subject's bucket by the section's outline index (`seq`) so a section
    # leads with a DIFFERENT claim per subject. Rotating *within* buckets (not the
    # flat pool) keeps the round-robin interleave, so a claims-light subject is
    # never starved out of the cap regardless of offset. Index (not an ord-sum of
    # the name — those collide on same-parity names, codex 2026-07-09) gives
    # distinct leads whenever #sections ≤ bucket size; below that the pigeonhole
    # is unavoidable (a size-N bucket has only N rotations) and leads degrade
    # gracefully — acceptable, since the live bug was zero-rotation, not collision.
    # ponytail: seq-mod-len ceiling; only tiny low-evidence buckets can still tie.
    off = seq
    for k in order:
        b = groups[k]
        r = off % len(b)
        groups[k] = b[r:] + b[:r]
    out: list[dict[str, Any]] = []
    i = 0
    while len(out) < cap and any(groups[k] for k in order):
        key = order[i % len(order)]
        if groups[key]:
            out.append(groups[key].pop(0))
        i += 1
    return out


# ---------------------------------------------------------------------------
# WRITE — one call per section from its claims; summary last (D4).
# ---------------------------------------------------------------------------


def _strip_unclosed_trailing_fence(text: str) -> str:
    """Drop a trailing, never-closed code fence a free-prose reply sometimes tacks
    on (observed live: gemma ended a plain-prose section with a bare "```python"
    and no body). An odd total fence count means the LAST "```" has no partner —
    everything from it onward is debris, not the prose the call asked for."""
    if text.count("```") % 2 == 0:
        return text
    return text[: text.rfind("```")].rstrip()


#: A quote line starting a heading/list/blockquote/fence, or any newline at
#: all, means embedding it raw would leak markdown structure into the document
#: (live bug: a multi-line pi README quote rendered as a giant mid-document
#: heading). Single-line, structure-free quotes are safe to leave inline.
_QUOTE_STRUCTURE_LINE_RE = re.compile(r"(?m)^[ \t]*(#|[*\-]\s|\d+\.|>|```)")


def _needs_blockquote(quote: str) -> bool:
    return "\n" in quote or bool(_QUOTE_STRUCTURE_LINE_RE.search(quote))


def _neutralize_embedded_quotes(text: str, claims: list[dict[str, Any]]) -> str:
    """Any verbatim QUOTE the model reproduced raw in *text* is rendered as a
    markdown blockquote when it carries structure (heading/list/fence lines or
    multiple lines) — text preserved verbatim for grounding, but it can't
    promote itself to a document heading. A quote a LATER claim shares with (or
    nests inside) an earlier one is embedded only once; the repeat is dropped
    since the surrounding prose already refers to it."""
    seen: list[str] = []
    for c in claims:
        quote = (c.get("quote") or "").strip()
        if not quote or quote not in text:
            continue
        norm = " ".join(quote.split()).lower()
        is_dup = any(norm in s or s in norm for s in seen)
        seen.append(norm)
        if is_dup:
            text = text.replace(quote, "", 1)
        elif _needs_blockquote(quote):
            blockquoted = "\n".join(f"> {ln}" for ln in quote.splitlines())
            text = text.replace(quote, blockquoted, 1)
    return text


_INLINE_URL_RE = re.compile(r"https?://\S+")
_PATCH_METADATA_LINE_RE = re.compile(
    r"(?m)^[ \t]*(?:(?:[-*+>]|\d+[.)])\s*)?"
    r"(?:"
    r"(?:ARTICLE_TITLE|POPULARITY|PUBLICATION|KEY_INSIGHT|PATCH_TARGET)\s*:.*"
    r"|URL\s*:\s*(?:https?://\S+|\(?none\)?)"
    r"|SEARCH\s*:\s*(?:ok|error)\b.*"
    r"|#{1,6}\s*RESEARCH_FINDING\b.*"
    r"|RESEARCH_FINDING(?:\s*:.*)?"
    r")$"
)


def _drop_generated_fenced_blocks(text: str) -> str:
    """WRITE sections ask for prose only; generated code/diagram fences are
    inserted by dedicated, gated splice functions later. Drop any spontaneous
    fenced block from the section writer so weak-model examples cannot corrupt
    section prose or satisfy the code/diagram contract through an ungated path."""
    return re.sub(r"```.*?```", "", text or "", flags=re.DOTALL).strip()


def _strip_patch_metadata_lines(text: str) -> str:
    """Remove raw search/patch ledger lines if a model echoes tool material."""
    return _PATCH_METADATA_LINE_RE.sub("", text or "").strip()


def _write_section(name: str, requirement: str, claims: list[dict[str, Any]], client: Any) -> str:
    if client is None:
        return "_(no content — no LLM client available)_"
    ev_lines = "\n".join(
        f'- CLAIM: {c["claim"]} | QUOTE: "{c["quote"]}" | URL: {c["url"]}' for c in claims
    ) or "(no claims gathered for this section — say so honestly)"
    prompt = (
        f"You are writing the '{name}' section of a research report.\n\n"
        f"TASK: {requirement}\n\n"
        "Write 2-4 prose paragraphs that SYNTHESIZE the claims "
        "below for the specific purpose of THIS section — connect and interpret "
        "them into an argument: draw out patterns, tensions, cause-and-effect, "
        "and what they imply. Do NOT enumerate or restate the claims one by one "
        "— a section that just lists facts has failed its job. When THIS section "
        "genuinely covers two or more DISTINCT aspects, you MAY separate them with "
        "`###` level-3 subheadings (never `#` or `##`, and never a subheading that "
        "just repeats the section's own title) — use one only when it clarifies "
        "structure, not as decoration. Use ONLY these "
        "claims as your factual basis — never invent a fact or a citation. Every "
        "paragraph must cite at least one claim's URL inline, copied EXACTLY from "
        "the claims below — never a URL from memory. Do not comment on the quality "
        "or sufficiency of the claims; if there is genuinely little to say, write "
        "less.\n\n"
        f"CLAIMS:\n{ev_lines}\n\nOutput ONLY the section body prose, nothing else."
    )
    try:
        reply = client.chat([{"role": "user", "content": prompt}])
        text = _strip_unclosed_trailing_fence(str(getattr(reply, "text", "") or "").strip())
    except Exception as exc:  # noqa: BLE001 — a bad section call must never break the run
        dbg(f"research_first _write_section: call failed section={name!r} exc={exc!r}")
        text = ""
    # Citation grounding is enforced once, post-WRITE, against the FULL claims
    # set (see _drop_ungrounded_sentences) — not here per-section. A per-
    # section check only sees that section's own round-robin claims slice,
    # which wrongly rejected a real citation to a claim ANOTHER section's
    # slice happened to hold (live: cratered 5 of 6 sections to empty).
    text = _strip_patch_metadata_lines(_drop_generated_fenced_blocks(text))
    text = _neutralize_embedded_quotes(text, claims)
    return text or "_(no content — evidence gathering failed for this section)_"


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


_FENCE_SPLIT_RE = re.compile(r"(```.*?```)", re.DOTALL)


def _drop_ungrounded_sentences(text: str, claims: list[dict[str, Any]]) -> str:
    """Deterministic post-WRITE invariant: every URL that survives into the
    artifact must belong to a claims DOMAIN — claims are the only citation
    currency for body prose. DOMAIN-level, not exact-URL (team-lead ruling,
    live data: 23/23 real drops were the SAME domain with a truncated path —
    e.g. a claim's own https://pi.dev/docs/latest/sdk cited back as
    https://pi.dev/docs/latest — never an actual fabrication; exact matching
    was over-strict for prose and cost real content for zero grounding gain).
    Cross-domain fabrication (wikipedia/piday.org, a weak model's training-
    data memory, not real tool output) still dies — it shares no domain with
    any claim. A sentence citing a bad-domain URL is dropped WHOLE, not just
    the citation; the drop is logged so a thin section is traceable, not
    silent. Checked against the FULL claims set, not the section's own
    round-robin slice — a real citation to a claim outside THIS section's
    allotment is still a real citation (a per-section version of this check
    wrongly starved 5 of 6 sections, live). References stays EXACT-URL
    (``_rebuild_references_from_claims``, unaffected by this function) —
    domain tolerance is for prose only.

    Fence-aware: splits on fenced code/mermaid blocks and leaves their
    interiors byte-for-byte untouched. This is called on raw section prose
    BEFORE this pipeline's own diagram/code splicing adds any fences — but a
    weak model's own response can embed an unprompted code block already
    (live: a fenced Python demo inside "Evidence and Analysis" prose got
    sentence-split and silently discarded whole over one placeholder URL
    inside it). Code is not prose to citation-check regardless of who added
    the fence."""
    allowed_domains = {_domain(c["url"]) for c in claims}

    def _drop_in_prose(prose: str) -> str:
        out_paragraphs = []
        for para in prose.split("\n\n"):
            kept = []
            for sent in _SENTENCE_SPLIT_RE.split(para):
                urls = [u.rstrip(").,;:\"'") for u in _INLINE_URL_RE.findall(sent)]
                bad = [u for u in urls if _domain(u) not in allowed_domains]
                if bad:
                    dbg(f"research_first _drop_ungrounded_sentences: dropped urls={bad!r} sentence={sent!r}")
                    continue
                kept.append(sent)
            if kept:
                out_paragraphs.append(" ".join(kept))
        return "\n\n".join(out_paragraphs)

    return "".join(
        part if part.startswith("```") else _drop_in_prose(part)
        for part in _FENCE_SPLIT_RE.split(text)
    )


def _drop_ungrounded_sentences_artifact_wide(text: str, claims: list[dict[str, Any]]) -> str:
    """Final ASSEMBLE-stage pass of the URL⊆claims invariant, over the WHOLE
    assembled document — belt-and-suspenders on top of the per-section/
    summary application in WRITE (team-lead: "every inline URL anywhere —
    Exec Summary included"). The References list is skipped since
    _rebuild_references_from_claims already guarantees it is URL⊆claims by
    construction (and it's a bullet list, not prose, to begin with)."""
    refs_idx = text.find("\n\n## References")
    head, tail = (text[:refs_idx], text[refs_idx:]) if refs_idx != -1 else (text, "")
    return _drop_ungrounded_sentences(head, claims) + tail


_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _norm_heading(s: str) -> str:
    """Collapse a heading to its comparable core — drop markdown/punctuation,
    fold whitespace, lowercase — so a punctuated heading matches the same
    skeleton section without punctuation."""
    return " ".join(re.sub(r"[^\w\s]", " ", s).split()).lower()


def _sanitize_section_headings(text: str, section_names: list[str]) -> str:
    """Ruling (c): keep model-emitted headings from colliding with the ``##``
    structure ``_assemble`` builds around every section (and from tripping
    ``_find_duplicate_headings``). Weak models echo the section's own title as a
    leading ``## ...`` and sometimes drop a sibling-section heading mid-body.
    Fence-aware normalization applied to each raw section/summary reply BEFORE
    any diagram/code fence is spliced in:

      - the LEADING heading (the section's self-heading echo) → stripped once;
      - an in-body heading whose text matches a skeleton section name (an echo
        of a sibling section) → dropped;
      - any other heading at ``#``/``##`` level → demoted to ``###`` so it can
        never sit at title/section level;
      - deeper headings (``###``+) and fenced-block interiors → untouched.

    Makes a duplicate ``##`` heading impossible at the source, so finalize's
    ``normalize_artifact`` dedupe path stays a cold safety net (never skipped,
    never relied on)."""
    banned = {_norm_heading(n) for n in section_names}
    seen_content = False
    leading_stripped = False
    out_parts: list[str] = []
    for part in _FENCE_SPLIT_RE.split(text):
        if part.startswith("```"):
            out_parts.append(part)
            seen_content = True
            continue
        part = re.sub(r"(?<=[.!?])\s*(#{1,6}\s+)", r"\n\1", part)
        lines: list[str] = []
        for ln in part.split("\n"):
            m = _HEADING_LINE_RE.match(ln)
            if not m:
                if ln.strip():
                    seen_content = True
                lines.append(ln)
                continue
            level, htext = m.group(1), m.group(2).strip()
            if not seen_content and not leading_stripped:
                leading_stripped = True  # section's own self-heading echo — drop once
                continue
            seen_content = True
            if _norm_heading(htext) in banned:
                continue  # sibling-section echo — drop
            lines.append(ln if len(level) > 2 else f"### {htext}")
        out_parts.append("\n".join(lines))
    return "".join(out_parts)


_FENCED_BODY_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)


def _fenced_code_compiles(block: str) -> bool:
    """True unless *block* is a PYTHON-labeled fence with a syntax error (live
    failure: gemma's generated example had a stray em-dash). ``compile()`` is
    the root-cause check — catches ANY syntax defect, not just this one
    character. Non-Python fences (real excerpts can be a mid-function slice
    that legitimately fails ``compile()`` standalone) pass unchecked."""
    m = _FENCED_BODY_RE.search(block)
    if not m or m.group(1).lower() not in ("", "python", "py"):
        return True
    try:
        compile(m.group(2), "<generated>", "exec")
        return True
    except SyntaxError:
        return False


def _splice_code(section_text: str, evidence_dir: Path, client: Any) -> str:
    from studio.structural_producer import _find_evidence_code, _guess_lang, _llm_code_from_evidence

    found = _find_evidence_code(evidence_dir)
    if found:
        url, excerpt = found
        block = f"Example from the source material ([source]({url})):\n\n```{_guess_lang(excerpt)}\n{excerpt}\n```"
    else:
        block = _llm_code_from_evidence(client, evidence_dir) or ""
        if block and not _fenced_code_compiles(block):
            block = ""
    # _llm_code_from_evidence only checks "```" is present, so an LLM reply with
    # an opening fence and no closer (observed live: gemma returned "```python"
    # with no body) would otherwise splice an unbalanced fence into the artifact.
    if block and block.count("```") % 2 != 0:
        block = ""
    return f"{section_text}\n\n{block}" if block else section_text


_EV_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9+]*)[ \t]*\n(.*?)```", re.DOTALL)


def _subject_evidence_code(evidence_dir: Path | None, urls: set[str]) -> tuple[str, str] | None:
    """``(url, excerpt)`` — real code from the subject's OWN fetched sources (evidence
    files whose header URL the subject cited). Mirrors how a researcher writes an
    example: copy the actual command/snippet from the docs, never invent an SDK.
    Prefers ANY fenced block (a 2-line `craft-cli run …` or `from pi import …` counts —
    codex: `_best_code_excerpt` needs a 10-line dense run and skips short SDK snippets);
    falls back to the dense-run heuristic. None when the sources carry no code."""
    from studio.structural_producer import _URL_LINE_RE, _best_code_excerpt, _iter_evidence_files

    dense_fallback: tuple[str, str] | None = None
    for path in _iter_evidence_files(evidence_dir):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _URL_LINE_RE.match(content)
        url = m.group(1) if m else ""
        if not url or url not in urls:
            continue
        body = content[m.end() :] if m else content
        fm = _EV_FENCE_RE.search(body)
        if fm and fm.group(2).strip():
            lang = fm.group(1) or _EV_LANG_HINT
            return url, f"```{lang}\n{fm.group(2).strip()}\n```"
        if dense_fallback is None:
            excerpt = _best_code_excerpt(body)
            if excerpt:
                dense_fallback = (url, f"```{_EV_LANG_HINT}\n{excerpt}\n```")
    return dense_fallback


_EV_LANG_HINT = "text"

#: Programming-language fences where an ``import``/``require`` line is real code, not
#: English prose. A CLI (```bash) or natural-language (```text) block is NEVER a
#: fabricated SDK, so the invented-import guard must not fire on it (codex: the old
#: guard dropped Craft's real `import my skills from Claude Code` NL instruction).
_CODE_FENCE_LANGS = frozenset({
    "python", "py", "javascript", "js", "typescript", "ts", "tsx", "jsx", "java",
    "go", "golang", "rust", "rs", "ruby", "rb", "c", "cpp", "csharp", "cs", "php",
    "swift", "kotlin", "scala",
})
_IMPORT_RE = re.compile(
    r"(?m)^\s*(?:import\s+[\w.{*]|from\s+[\w./@-]+\s+import\s|#include\s*[<\"]"
    r"|using\s+[\w.]+\s*;)|(?:^|[^\w.])require\(['\"]"
)


def _splice_subject_code(
    text: str, subject: str, claims: list[dict[str, Any]], client: Any,
    evidence_dir: Path | None = None,
) -> str:
    """One code example per subject. Prefers a REAL code block from the subject's own
    fetched sources; only if none exists does it synthesise — and then it is forbidden
    to invent an SDK/import, because a tool the docs show no code for (e.g. a CLI or
    natural-language app) must be demonstrated by its REAL interface, not a
    hallucinated API. Live: the Craft example invented `from craft_agents import
    Agent` — a Python SDK that exists in NO source — because the old prompt demanded
    'runnable' code from prose claims that named no API."""
    if client is None:
        return text
    subj_claims = [c for c in claims if subject in (c.get("subjects") or [])]
    if not subj_claims:
        return text
    # 1. Prefer the subject's OWN real code, copied from the docs (never invented).
    real = _subject_evidence_code(evidence_dir, {c["url"] for c in subj_claims})
    if real:
        url, block = real  # block is already a complete fenced markdown block
        source_line = f"Example: {subject} (from the source material, [source]({url}))."
    else:
        # 2. No evidenced code → the subject has no code API the docs demonstrate.
        #    Show its REAL interface (CLI / natural-language), and forbid imports.
        ev_lines = "\n".join(f"- {c['claim']} (URL: {c['url']})" for c in subj_claims[:10])
        prompt = (
            f"Show how a user actually invokes {subject}, using ONLY what the claims "
            "below describe. RULES: use ONLY commands, CLIs, functions, or config that "
            "the claims NAME. If the claims describe a command-line tool or a natural-"
            "language / GUI application (not a code library), show the REAL usage — its "
            "actual shell command(s) or the natural-language instructions — NOT an "
            "invented code API. NEVER write an import/require of a package the claims "
            "do not name. Output ONLY a single fenced block (```lang ... ```).\n\n"
            f"CLAIMS:\n{ev_lines}"
        )
        try:
            reply = client.chat([{"role": "user", "content": prompt}])
            block = str(getattr(reply, "text", "") or "").strip()
        except Exception as exc:  # noqa: BLE001 — a bad code call must never break the run
            dbg(f"research_first _splice_subject_code: call failed subject={subject!r} exc={exc!r}")
            return text
        if "```" not in block or block.count("```") % 2 != 0 or not _fenced_code_compiles(block):
            return text
        # Fabrication guard: the subject's docs showed NO code, so a program-language
        # example that imports a package is an invented SDK — drop it. Gated on the
        # fence LANGUAGE (codex): a ```bash CLI or ```text natural-language block is
        # never a fabricated SDK, and its prose ("import my skills from Claude Code")
        # must not trip an import regex.
        lang_m = re.match(r"\s*```[ \t]*([A-Za-z0-9+]*)", block)
        lang = (lang_m.group(1).lower() if lang_m else "")
        if lang in _CODE_FENCE_LANGS and _IMPORT_RE.search(block):
            dbg(f"research_first _splice_subject_code: dropped invented-import example subject={subject!r}")
            return text
        source_line = f"Example: {subject}."
    candidate = f"{text}\n\n{source_line}\n\n{block}"
    if len(lint_artifact(candidate)) > len(lint_artifact(text)):
        return text  # never ship a splice that introduces a NEW lint issue
    return candidate


def _grounded_mechanism_terms(claims: list[dict[str, Any]], terms: list[str]) -> list[str]:
    """The subset of the per-task ``mechanism_terms`` that literally appear in
    the claims (R3 — replaces the fixed interface enum with per-task LLM terms).
    Case-insensitive, plural-tolerant, order-preserving, deduped, lowercase.

    The FRAME relationship hypothesis is a SEARCH directive, never content by
    itself (team-lead: hypothesis directs search, never becomes content) — a
    term ships only once corroborated in the claims."""
    claim_text = " ".join(c.get("claim", "") for c in claims).lower()
    seen: list[str] = []
    for term in terms:
        t = term.strip().lower()
        if not t or t in seen:
            continue
        # Plural-tolerant: a claim saying "REST APIs" / "MCP servers" grounds the
        # singular term just as much (the trailing ``s?`` spans the plural).
        if re.search(r"\b" + re.escape(t) + r"s?\b", claim_text):
            seen.append(t)
    return seen


def _mechanism_terms_grounded(block: str, claims: list[dict[str, Any]], terms: list[str]) -> bool:
    """Rule (b), regrounded on per-task terms: any ``mechanism_term`` NAMED in
    the generated code must also appear in the claims — never a surface absent
    from evidence. The code side tokenizes on non-letter runs (``[a-zA-Z]+``),
    NOT ``\\b``: ``_`` is a regex word char, so a boundary regex would never
    isolate "mcp"/"webhook" inside the realistic snake_case identifiers
    ("call_mcp_server", "register_webhook_url") generated code uses. Falls open
    when the code names no term at all (nothing to check)."""
    block_tokens = set(re.findall(r"[a-zA-Z]+", block.lower()))
    claim_text = " ".join(c["claim"] for c in claims).lower()
    for term in terms:
        t = term.strip().lower()
        if not t:
            continue
        term_tokens = re.findall(r"[a-zA-Z]+", t)
        if term_tokens and all(tok in block_tokens for tok in term_tokens) and not re.search(
            r"\b" + re.escape(t) + r"s?\b", claim_text
        ):
            return False
    return True


def _splice_integration_code(
    text: str,
    subjects: list[str],
    claims: list[dict[str, Any]],
    relationship: "Relationship",
    client: Any,
) -> str:
    """One PROPOSED integration example — synthesized (a real combined example
    may not exist in sources), gated on: (a) compile(), (b) only mechanism terms
    named in claims, (c) captioned as proposed usage, never as quoted source.

    The prompt is steered to the CLAIM-GROUNDED per-task terms, not the raw FRAME
    hypothesis — the hypothesis's term is often absent from every claim, and code
    built around it fails the grounding gate every time (v43 live: 0/4). When no
    mechanism term is documented at all, no groundable example can exist → skip
    rather than fabricate."""
    if client is None or len(subjects) < 2:
        return text
    grounded_terms = _grounded_mechanism_terms(claims, relationship.mechanism_terms)
    if not grounded_terms:
        dbg("research_first _splice_integration_code: skipped — no claim-documented mechanism term")
        return text
    joint = [c for c in claims if len(c.get("subjects") or []) >= 2] or claims
    ev_lines = "\n".join(f"- {c['claim']} (URL: {c['url']})" for c in joint[:10])
    iface_phrase = ", ".join(grounded_terms)
    prompt = (
        f"Write a short, minimal PROPOSED usage example showing how "
        f"{' and '.join(subjects)} could be composed. Build it around one of "
        f"these mechanisms, which ARE documented in the claims: {iface_phrase}. "
        "Use ONLY mechanisms named in the claims below — never invent one absent "
        "from them (in particular, do NOT use one just because it is "
        "mentioned in a hypothesis). Output ONLY a single fenced code block, "
        "nothing else.\n\n"
        f"CLAIMS:\n{ev_lines}"
    )
    try:
        reply = client.chat([{"role": "user", "content": prompt}])
        block = str(getattr(reply, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 — a bad code call must never break the run
        dbg(f"research_first _splice_integration_code: call failed exc={exc!r}")
        return text
    if "```" not in block or block.count("```") % 2 != 0 or not _fenced_code_compiles(block):
        return text
    if not _mechanism_terms_grounded(block, claims, relationship.mechanism_terms):
        dbg("research_first _splice_integration_code: rejected — mechanism term not named in claims")
        return text
    via = relationship.mechanism or relationship.descriptor
    caption = (
        f"Proposed usage — {' + '.join(subjects)} composed via {via}. "
        "Illustrative only, synthesized from documented mechanisms above; not quoted source code."
    )
    candidate = f"{text}\n\n{caption}\n\n{block}"
    if len(lint_artifact(candidate)) > len(lint_artifact(text)):
        return text
    return candidate


def _integration_label(
    subjects: list[str],
    claims: list[dict[str, Any]],
    relationship: "Relationship",
) -> str | None:
    """A grounded label for a cross-subject edge, or ``None`` if ungrounded.
    Regrounded on the per-task ``mechanism_terms`` (R3), never a fixed interface
    enum: (a) a per-task mechanism term corroborated in a joint claim (top
    priority); (b) any joint claim at all → a generic label from the per-task
    descriptor (never the raw hypothesis sentence — a hypothesis directs search
    but never becomes content by itself); (c) EACH subject has a claim
    corroborating a per-task mechanism term — never invented from neither
    (team-lead rule 2). The ≤6-word cap keeps a label a name, not a sentence."""
    joint = [c for c in claims if len(c.get("subjects") or []) >= 2]
    grounded_joint = _grounded_mechanism_terms(joint, relationship.mechanism_terms)
    if grounded_joint:
        return grounded_joint[0]
    if joint:
        label = relationship.descriptor.strip().lower()
        return label if label and len(label.split()) <= 6 else None
    per_subject: list[str] = []
    for subject in subjects:
        subj_claims = [c for c in claims if subject in (c.get("subjects") or [])]
        terms = _grounded_mechanism_terms(subj_claims, relationship.mechanism_terms)
        if terms:
            per_subject.append(terms[0])
    if len(per_subject) >= len(subjects) >= 2:
        return " / ".join(dict.fromkeys(per_subject))
    return None


def _subject_feature_labels(
    subject: str,
    claims: list[dict[str, Any]],
    *,
    limit: int = 4,
    all_subjects: list[str] | None = None,
) -> list[str]:
    """Grounded fallback diagram nodes extracted from the subject's own claims."""
    known_subjects = all_subjects or [subject]
    subject_names = {s.lower() for s in known_subjects}
    subject_tokens = {
        token for name in known_subjects for token in _ANCHOR_TOKEN_RE.findall(name.lower())
    }
    labels: list[str] = []

    def add(label: str) -> None:
        label = re.sub(r"\s+", " ", label.strip("`'\" .,;:()[]{}"))
        if not (3 <= len(label) <= 48):
            return
        low = label.lower()
        # Reject a node that just restates the subject ("Pi") — but NOT a real
        # sub-component that merely contains the subject token ("pi-ai",
        # "pi-agent-core"): those are distinct architectural parts, and dropping
        # them for a substring match is what forced the prose-glue fallback.
        if low in subject_names or low in labels:
            return
        if not diagram_render._component_label_tokens(label):
            return
        if low not in [x.lower() for x in labels]:
            labels.append(label)

    for c in claims:
        claim_subjects = c.get("subjects") or []
        if claim_subjects != [subject]:
            continue
        claim = re.sub(r"https?://\S+", "", str(c.get("claim") or ""))
        for token in re.findall(r"`([^`]{3,48})`", claim):
            add(token)
        # Multi-word proper names ("Claude Agent SDK") or standalone all-caps
        # acronyms ("API", "MCP", "SDK") — but NOT a single Title-case word
        # ("Designed", "Harness"), which is sentence glue, not a component.
        for phrase in re.findall(
            r"\b[A-Z][A-Za-z0-9_-]*(?:\s+[A-Z][A-Za-z0-9_-]*)+\b|\b[A-Z]{2,6}\b", claim
        ):
            add(phrase)
        # Identifier-shaped tokens (hyphenated/dotted package names like pi-ai /
        # pi-agent-core, or CamelCase types like AgentContext) — NOT bare prose
        # words ("designed", "harness"), which are sentence glue. Shape-based and
        # generic: no task vocabulary, mirrors how a researcher reads package names.
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)+|[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+", claim):
            if token.lower() in subject_tokens or len(token) < 4:
                continue
            add(token)
        if len(labels) >= limit:
            break
    return labels[:limit]


def _fallback_subject_diagram(subject: str, claims: list[dict[str, Any]]) -> str | None:
    labels = _subject_feature_labels(subject, claims, all_subjects=[subject])
    if len(labels) < 2:
        return None
    sid = diagram_render._mermaid_safe_id(subject)
    lines = ["flowchart TD", f'    {sid}["{subject}"]']
    for i, label in enumerate(labels):
        nid = f"{sid}_N{i}"
        lines.append(f'    {sid} --> {nid}["{label.replace(chr(34), chr(39))}"]')
    return "\n".join(lines)


def _fallback_cluster_diagram(
    subjects: list[str],
    claims: list[dict[str, Any]],
    relationship: "Relationship",
) -> str | None:
    """Deterministic clustered integration diagram from grounded subject features."""
    if len(subjects) < 2:
        return None
    label = _integration_label(subjects, claims, relationship)
    if not label:
        return None
    components: list[tuple[str, str]] = []
    by_subject: dict[str, list[str]] = {}
    for subject in subjects[:6]:
        labels = _subject_feature_labels(subject, claims, limit=3, all_subjects=subjects[:6])
        if not labels:
            labels = [subject]
        by_subject[subject] = labels
        components.extend((name, subject) for name in labels)
    first_subjects = [s for s in subjects[:6] if by_subject.get(s)]
    if len(first_subjects) < 2:
        return None
    edges: list[tuple[str, str, str]] = [
        (by_subject[first_subjects[0]][0], by_subject[first_subjects[1]][0], label)
    ]
    return diagram_render._render_cluster_diagram(components, edges, subjects)


def _splice_diagram(
    section_text: str,
    subjects: list[str],
    claims: list[dict[str, Any]],
    client: Any,
    relationship: "Relationship",
) -> str:
    from studio.diagram_render import build_diagram_block

    body: str | None = None
    if client is not None and claims and len(subjects) >= 2:
        try:
            reply = client.chat(
                [{"role": "user", "content": diagram_render.build_cluster_components_prompt(subjects, claims, relationship.mechanism)}]
            )
            raw = str(getattr(reply, "text", "") or "")
        except Exception as exc:  # noqa: BLE001 — a bad diagram call must never break the run
            dbg(f"research_first _splice_diagram: call failed exc={exc!r}")
            raw = ""
        if raw:
            grounding_text = section_text + "\n" + " ".join(c["claim"] for c in claims)
            # KGGen/GraphRAG pattern (in diagram_render.render_subject_cluster_diagram):
            # the cross-subject EDGE comes from a grounded, directional triple the LLM
            # extracts (code assembles it), NOT the model's free-drawn edge, which
            # hallucinated direction+label. Falls back to the old LLM edge only when no
            # grounded triple is available. Structure (rule 1) AND grounding (rule 2): a
            # single-subject diagram or two disconnected islands both fail — no grounded
            # cross-edge means no diagram, not a fabricated one. A confirmed triple IS
            # the grounding; otherwise the label-grounding gate still decides.
            integration_label = _integration_label(subjects, claims, relationship)
            body = diagram_render.render_subject_cluster_diagram(
                raw,
                grounding_text,
                subjects=subjects,
                claims=claims,
                integration_label=integration_label,
                client=client,
            )
    if not body:
        body = _fallback_cluster_diagram(subjects, claims, relationship)
    if not body:
        return section_text
    from studio.structural_producer import _diagram_explanation_sentence

    # A short label alone trips artifact_lint's "no nearby explanatory prose"
    # check (structural_producer._produce_diagram hits the same requirement) —
    # reuse its deterministic explanation-sentence builder, not a bare caption.
    caption = f"Integration architecture: {' and '.join(subjects)}. {_diagram_explanation_sentence(body)}"
    candidate = f"{section_text}\n\n{caption}\n\n{build_diagram_block(body)}"
    if any("mermaid" in w.lower() for w in lint_artifact(candidate)):
        return section_text  # never ship a diagram that fails lint
    return candidate


#: Cap on comparison rows per subject — a table, not a claim dump.
_MAX_COMPARISON_ROWS = 4
_MAX_RELATION_ROWS = 14
_MAX_RELATION_TARGETS_PER_HEAD = 5
_MAX_RELATION_HEADS = 6


def _table_cell(text: str) -> str:
    """Make a claim safe inside a markdown table cell — escape pipes, flatten
    newlines."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _splice_comparison_table(
    text: str, subjects: list[str], claims: list[dict[str, Any]]
) -> str:
    """A deterministic comparison table for competing/alternative subjects (R3):
    one column per subject, cells drawn ONLY from that subject's own claims —
    grounded, never synthesized. Replaces the integration diagram/code for the
    competes/alternative branch. Fail-open to unchanged text for <2 subjects or
    thin claims (any subject with no grounded claim to compare)."""
    subs = subjects[:2]
    if len(subs) < 2:
        return text
    per_subject: dict[str, list[str]] = {}
    for s in subs:
        rows = [c["claim"] for c in claims if s in (c.get("subjects") or [])]
        if not rows:
            return text  # thin — nothing grounded to compare on this side
        per_subject[s] = rows[:_MAX_COMPARISON_ROWS]
    n = max(len(v) for v in per_subject.values())
    lines = [
        "| " + " | ".join(subs) + " |",
        "| " + " | ".join("---" for _ in subs) + " |",
    ]
    for i in range(n):
        cells = [per_subject[s][i] if i < len(per_subject[s]) else "" for s in subs]
        lines.append("| " + " | ".join(_table_cell(c) for c in cells) + " |")
    caption = f"Comparison: {' vs '.join(subs)} (from documented claims)."
    candidate = f"{text}\n\n{caption}\n\n" + "\n".join(lines)
    if len(lint_artifact(candidate)) > len(lint_artifact(text)):
        return text  # never ship a splice that introduces a NEW lint issue
    return candidate


def _head_is_subject_grounded(
    head: str, subjects: list[str], all_anchors: dict[str, list[str]]
) -> bool:
    """A relationship is table-worthy only when its SOURCE is one of the report's
    actual subjects or a named component of one (an anchor) — never an arbitrary
    structural token the model happened to emit (``AppMenu``, ``extensions``, ``It``).
    Generic: keyed purely on the run's own subjects/anchors, no domain literal. Uses
    the same whole-token match as the anti-fabrication grounding, so ``pi-agent-core``
    grounds on subject ``Pi`` and ``Craft Agents`` on ``Craft``."""
    h = head.lower()
    for s in subjects:
        if _token_in(s, h):
            return True
        if any(_token_in(anc, h) for anc in (all_anchors.get(s) or [])):
            return True
    return False


def _splice_relationship_table(
    text: str, subjects: list[str], all_anchors: dict[str, list[str]],
    claims: list[dict[str, Any]],
) -> str:
    """A deterministic relationship table from the claim ``relations`` triples (task 2).
    Facts already exist as prose claims; `relations` lifted them to quote-grounded
    ``head|rel|tail`` edges at extraction — here we tabulate the SUBJECT fan-out (one
    package/SDK to several targets: its components, providers, or dependencies) that a
    single diagram edge can't show. Generic — no provider/verb literal.

    Selection is what makes the table useful rather than noisy: keep only triples whose
    SOURCE grounds to a subject/anchor (drops arbitrary structural edges), keep only
    genuine fan-outs (a source with >=2 distinct targets), rank sources by fan-out size,
    and cap heads/targets/rows so the strongest architecture story leads instead of an
    alphabetical grab-bag. De-dups by (source, target); fail-opens to unchanged text
    (no grounded fan-out, or a splice that would introduce a new lint issue)."""
    # Bucket on a CASEFOLDED source key (keeping the first-seen display casing) so a
    # source spelled two ways across claim blocks ("Pi"/"pi") is ONE fan-out, not two
    # fragmented buckets that mis-trip the >=2 gate — reviewer MEDIUM. `seen` dedups on
    # the same lowered key, so both stay consistent.
    by_head: dict[str, tuple[str, list[tuple[str, str]]]] = {}  # keyfold -> (display, [(rel,tail)])
    seen: set[tuple[str, str]] = set()
    for c in claims:
        for r in (c.get("relations") or []):
            head, rel, tail = (r.get("head") or ""), (r.get("rel") or ""), (r.get("tail") or "")
            if not (head and rel and tail):
                continue
            if not _head_is_subject_grounded(head, subjects, all_anchors):
                continue
            key = (head.casefold(), tail.casefold())
            if key in seen:
                continue
            seen.add(key)
            _display, rts = by_head.setdefault(head.casefold(), (head, []))
            rts.append((rel, tail))
    fanned = {hk: v for hk, v in by_head.items() if len(v[1]) >= 2}
    if not fanned:
        return text  # no grounded fan-out — not table-worthy
    ranked = sorted(fanned.values(), key=lambda v: (-len(v[1]), v[0].lower()))
    lines = ["| Source | Relationship | Target |", "| --- | --- | --- |"]
    rows = 0
    for head, rt in ranked[:_MAX_RELATION_HEADS]:
        for rel, tail in rt[:_MAX_RELATION_TARGETS_PER_HEAD]:
            if rows >= _MAX_RELATION_ROWS:
                break
            lines.append("| " + " | ".join(_table_cell(x) for x in (head, rel, tail)) + " |")
            rows += 1
    caption = "Key relationships (from documented claims)."
    candidate = f"{text}\n\n{caption}\n\n" + "\n".join(lines)
    if len(lint_artifact(candidate)) > len(lint_artifact(text)):
        return text  # never ship a splice that introduces a NEW lint issue
    return candidate


def _pick_subject_home(written: dict[str, str], subject: str, exclude: set[str]) -> str | None:
    """Section whose WRITTEN body mentions *subject* the most — a subject's own
    architecture diagram belongs near its own content, not in a fixed section
    (generic: no hardcoded section/subject names)."""
    best, best_count = None, 0
    for name, text in written.items():
        if name in exclude:
            continue
        count = text.lower().count(subject.lower())
        if count > best_count:
            best, best_count = name, count
    return best


def _subject_components_prompt(subject: str, claims_text: str) -> str:
    """Same COMPONENT/EDGE contract as ``diagram_render.build_components_prompt``
    (so ``render_grounded_diagram`` parses it unchanged), but scoped to ONE
    subject — the generic whole-report version asks the same question for every
    subject, so two subjects sharing a section produced near-duplicate diagrams
    (live bug: two subjects' architecture diagrams came out near-identical when
    both landed in the same home section).

    Fed the subject's CLAIMS text, not home-section prose (option-b half-
    application fix, team-lead-verified live at v35): the grounding CHECK
    already read claims, but this prompt still asked the LLM to invent
    components from the WRITE model's often-thin section prose — a subject
    with 5+ named components in its claims ledger could still fail the
    diagram floor because the prose never repeated them. Same evidence the
    grounding check verifies against, so the model isn't asked to guess at
    material it was never shown."""
    return (
        f"List the architecture of {subject} specifically, based ONLY on the "
        "CLAIMS below, as plain lines. Use ONLY this exact format — no prose, no "
        "markdown, no code fences, nothing else:\n"
        "COMPONENT: <name> | <one-line role>\n"
        "EDGE: <name A> -> <name B> | <optional label>\n\n"
        f"Only include components that belong to {subject} — never a component "
        "belonging to another subject.\n\n"
        f"{diagram_render._COMPONENT_NAMING_RULE}\n\n"
        f"=== CLAIMS ===\n{claims_text}\n=== END CLAIMS ==="
    )


def _splice_subject_diagram(
    text: str, subject: str, claims: list[dict[str, Any]], client: Any
) -> str:
    """A single-subject architecture diagram (v7's diagram quality — real,
    literal-token-grounded components — was right; it just needed one diagram
    per subject and an integration diagram alongside them). Reuses
    ``diagram_render``'s existing flat single-diagram parse/render path; only
    the prompt is subject-scoped (see ``_subject_components_prompt``).

    Grounded against ALL of the subject's own claims (team-lead ruling,
    option b) — not the home section's rendered prose. The claims ledger IS
    the evidence base; rendered section prose is a downstream artifact of it
    whose richness depends on how chatty the WRITE model happened to be that
    run — exactly the weak-model-prose-variability class this pipeline
    otherwise eliminates by construction. Claims-grounding is deterministic
    given RESEARCH output and symmetric with how the integration diagram
    already grounds (joint claims). Home-section pick stays purely the
    SPLICE LOCATION — unrelated to what grounds the diagram's content.

    Option-b was HALF-applied until this fix (verified live, v35): only the
    grounding CHECK below read claims — the PROMPT still asked the LLM to
    invent components from ``text`` (home-section prose), so a subject whose
    claims named 5+ real components could still fail the diagram floor if the
    WRITE model's prose never repeated them. ``subj_claims`` is now computed
    FIRST and fed to both the prompt and the check — same evidence, asked and
    verified against consistently."""
    from studio.diagram_render import build_diagram_block, render_grounded_diagram

    if client is None:
        return text
    subj_claims = [c for c in claims if subject in (c.get("subjects") or [])]
    if not subj_claims:
        return text
    grounding_text = " ".join(c["claim"] for c in subj_claims)
    try:
        reply = client.chat([{"role": "user", "content": _subject_components_prompt(subject, grounding_text)}])
        raw = str(getattr(reply, "text", "") or "")
    except Exception as exc:  # noqa: BLE001 — a bad diagram call must never break the run
        dbg(f"research_first _splice_subject_diagram: call failed subject={subject!r} exc={exc!r}")
        return text
    body = render_grounded_diagram(raw, grounding_text) or _fallback_subject_diagram(subject, subj_claims)
    if not body:
        return text
    from studio.structural_producer import _diagram_explanation_sentence

    caption = f"Architecture: {subject}. {_diagram_explanation_sentence(body)}"
    candidate = f"{text}\n\n{caption}\n\n{build_diagram_block(body)}"
    if any("mermaid" in w.lower() for w in lint_artifact(candidate)):
        return text  # never ship a diagram that fails lint
    return candidate


def _summary_relationship_instruction(relationship: "Relationship | None") -> str:
    """The conclusion the summary must state, appropriate to the relationship
    KIND (R3): a cooperation mechanism, a comparison trade-off, independence, or
    (unknown) nothing extra. Always grounded in what the sections already say —
    the "never introduce" rule still applies."""
    if relationship is None:
        return ""
    kind = relationship.kind
    if kind in ("cooperates", "extends") and relationship.mechanism:
        return (
            " State your conclusion on how the subjects relate, drawing on what the "
            f"sections say about this hypothesis: {relationship.mechanism}."
        )
    if kind in ("competes", "alternative"):
        return (
            " State your conclusion on the key trade-offs between the subjects, "
            "drawing only on the comparison the sections already make."
        )
    if kind == "independent":
        return (
            " Note that the subjects address independent concerns; do not assert a "
            "relationship the sections do not support."
        )
    return ""


def _write_summary(
    requirement: str,
    written: dict[str, str],
    client: Any,
    relationship: "Relationship | None" = None,
) -> str:
    """Written LAST from the already-drafted body (D4) — it can only summarize
    what exists, killing the overclaiming-summary failure by construction."""
    if client is None:
        return "_(summary unavailable)_"
    body = "\n\n".join(f"### {name}\n{text[:1200]}" for name, text in written.items())
    # N>=2 subjects: the summary must state the relationship conclusion
    # appropriate to the classified kind, not just recap each subject in
    # isolation — grounded in what the sections already say.
    relationship_instruction = _summary_relationship_instruction(relationship)
    prompt = (
        "Write a 2-3 paragraph Executive Summary for the research report below. "
        "Summarize ONLY what the sections below actually say — never introduce a "
        f"claim, number, or conclusion that is not already stated in them.{relationship_instruction}\n\n"
        f"TASK: {requirement}\n\nREPORT BODY:\n{body}\n\n"
        "Output ONLY the summary prose, nothing else."
    )
    try:
        reply = client.chat([{"role": "user", "content": prompt}])
        text = _strip_unclosed_trailing_fence(str(getattr(reply, "text", "") or "").strip())
    except Exception as exc:  # noqa: BLE001 — a bad summary call must never break the run
        dbg(f"research_first _write_summary: call failed exc={exc!r}")
        text = ""
    return text or "_(summary unavailable)_"


# ---------------------------------------------------------------------------
# ASSEMBLE
# ---------------------------------------------------------------------------


def _assemble(title: str, summary: str, sections: list[str], written: dict[str, str]) -> str:
    parts = [f"# {title}", "", "## Executive Summary", "", summary.strip() or "_(summary unavailable)_"]
    for name in sections:
        if name.lower() == "executive summary":
            continue
        body = written.get(name, "_(to be completed)_").strip() or "_(to be completed)_"
        parts.append(f"## {name}\n\n{body}")
    return "\n\n".join(parts).rstrip() + "\n"


# References-last enforcement is single-sourced in artifact_text (it is also
# baked into normalize_artifact so every finalize path inherits it — the reorder
# that breaks the invariant, dedupe_sections, lives there too). Kept as a
# module-level alias so the ASSEMBLE call site and its tests stay put.
_enforce_references_last = references_last


def _find_duplicate_headings(text: str) -> list[str]:
    """``## heading`` texts that appear more than once — a tripwire, not a
    repair. This linear pipeline writes each section exactly once, so a
    duplicate should be structurally impossible; if one shows up, that
    construction guarantee broke somewhere and needs investigating, not a
    dedupe pass papering over it."""
    counts: dict[str, int] = {}
    for h in re.findall(r"(?m)^## (.+)$", text):
        counts[h] = counts.get(h, 0) + 1
    return [h for h, n in counts.items() if n > 1]


#: An H1 line (``# Heading``) — exactly one ``#`` then whitespace (``## `` never matches).
_H1_LINE_RE = re.compile(r"^#[^\S\n]+(.*)$")


def _demote_stray_h1(text: str) -> str:
    """ToC guarantee (G6): a document has exactly ONE H1 — its title. Keep the first
    H1 (the title) and demote every LATER ``# `` line to ``### `` (a leaked body
    heading the writer promoted to title level breaks the ToC). Fence-aware, so a
    ``#`` comment inside a code block is never touched. Artifact-wide backstop to the
    per-section ``_sanitize_section_headings`` — it also covers headings introduced
    AFTER section writing (splices/edits), which the per-section pass cannot see."""
    seen_title = False
    out: list[str] = []
    for i, seg in enumerate(_FENCE_SPLIT_RE.split(text)):
        if i % 2:  # a ```...``` fence — leave verbatim (code comments, mermaid)
            out.append(seg)
            continue
        lines: list[str] = []
        for ln in seg.split("\n"):
            m = _H1_LINE_RE.match(ln)
            if not m:
                lines.append(ln)
            elif not seen_title:
                seen_title = True  # the first H1 is the legitimate title
                lines.append(ln)
            else:
                lines.append(f"### {m.group(1)}")  # a later H1 is a leak → demote
        out.append("\n".join(lines))
    return "".join(out)


#: Trailing site-brand tails a fetched ``<title>`` carries ("... · GitHub",
#: "... | DeepWiki", "... - GitHub") — noise once the URL already names the host.
#: Structural (separator + short brand token), not a per-site allowlist, so it
#: generalises to any source without task/domain literals.
_TITLE_TAIL_RE = re.compile(r"\s*[|·\-–—]\s*(?:GitHub|GitLab|DeepWiki|Wikipedia|npm|PyPI|Medium)\s*$", re.IGNORECASE)
_MAX_TITLE_LEN = 110


def _clean_source_title(raw: str) -> str:
    """A fetched page ``<title>`` → a compact reference label: collapse whitespace,
    strip a trailing site-brand tail, cap length. Generic — no per-source rules."""
    title = re.sub(r"\s+", " ", raw).strip()
    prev = None
    while prev != title:  # a title can carry two brand tails ("... | X · GitHub")
        prev = title
        title = _TITLE_TAIL_RE.sub("", title).strip()
    if len(title) > _MAX_TITLE_LEN:
        title = title[: _MAX_TITLE_LEN - 1].rstrip() + "…"
    return title


def _url_title_map(evidence_dir: Path | None) -> dict[str, str]:
    """``{url: page-title}`` harvested from the fetched evidence files. Each
    ``source-*.md`` starts with ``URL: <url>`` (line 1) then the page ``<title>``
    (line 3) — the fetch layer's fixed header. Fail-open to ``{}`` (bare-URL
    references) when the dir is absent/unreadable or a file lacks the header."""
    titles: dict[str, str] = {}
    if not evidence_dir or not evidence_dir.exists():
        return titles
    for path in sorted(evidence_dir.glob("source-*.md")):
        try:
            head = path.read_text(encoding="utf-8", errors="replace").splitlines()[:3]
        except OSError:
            continue
        if not head or not head[0].startswith("URL: "):
            continue
        url = head[0][len("URL: "):].strip()
        title = _clean_source_title(head[2]) if len(head) >= 3 else ""
        if url and title:
            titles[url] = title
    return titles


def _rebuild_references_from_claims(
    text: str, claims: list[dict[str, Any]], titles: dict[str, str] | None = None
) -> str:
    """Replace the References section with the deduped set of claim URLs, rendered
    as a NUMBERED, TITLED list (``1. [<page title>](<url>)``) so a reader can tell
    what each source is — and so the number is a stable citation anchor. The title
    comes from the fetched-evidence header map (``titles``); a URL with no known
    title falls back to a bare-URL bullet, never an invented title.

    claims.jsonl is the ground truth of what was actually cited, so building
    References from it (instead of re-harvesting the rendered body text, as
    ``rebuild_references_section`` does for the old pipeline) means a link
    that only ever existed INSIDE an embedded quote can't leak in: no
    relative/wiki-internal links, no image/render URLs, no markup as a link
    title, no bare "source" placeholders — none of that is in claims. Same
    fail-open shape as the scraper it replaces: no heading or no claims →
    text unchanged."""
    m = _REFERENCES_HEADING_RE.search(text)
    if not m or not claims:
        return text
    titles = titles or {}
    nxt = re.compile(r"(?m)^##\s").search(text, m.end())
    refs_end = nxt.start() if nxt else len(text)
    urls = list(dict.fromkeys(c["url"] for c in claims if c.get("url")))
    lines = []
    for i, u in enumerate(urls, 1):
        title = titles.get(u)
        lines.append(f"{i}. [{title}]({u})" if title else f"{i}. {u}")
    entries = "\n".join(lines)
    rebuilt = f"{m.group(0)}\n\n{entries}\n"
    tail = text[refs_end:]
    return text[: m.start()] + rebuilt + ("\n" + tail.lstrip("\n") if tail.strip() else "")


def _citation_index(claims: list[dict[str, Any]]) -> dict[str, int]:
    """``{url: ref_num}`` in the SAME dedup order ``_rebuild_references_from_claims``
    numbers the References list — so an inline ``[N]`` marker points at reference N
    by construction (no dangling marker is possible)."""
    urls = dict.fromkeys(c["url"] for c in claims if c.get("url"))
    return {u: i for i, u in enumerate(urls, 1)}


#: Inline-citation forms the writer emits (it is told to cite a claim's URL inline,
#: copied verbatim): a markdown link, a parenthesised URL, or a bare URL. The paren
#: and bare forms eat a leading horizontal space so a STRIP (non-claim URL) leaves no
#: orphan space; a claim URL re-adds one before its marker.
_CIT_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_CIT_PAREN_URL_RE = re.compile(r"[^\S\n]*\(\s*(https?://[^)\s]+?)\s*\)")
_CIT_BARE_URL_RE = re.compile(r"[^\S\n]*(https?://[^\s)\]]+)")
#: Trailing punctuation to strip before an index lookup (and preserve after the marker).
_CIT_TRAILING = ".,;:!?)]}\"'"
#: Blank-line paragraph boundary (captured so split() keeps it verbatim for rejoin).
#: ``[^\S\n]`` is horizontal whitespace only, so a soft (single-\n) wrap stays one paragraph.
_PARA_BREAK_RE = re.compile(r"(\n[^\S\n]*\n)")


def _cit_lookup(url: str, index: dict[str, int]) -> int | None:
    return index.get(url) or index.get(url.rstrip(_CIT_TRAILING))


def _dedup_markers_per_paragraph(seg: str, valid_refs: set[int]) -> str:
    """Over-cite collapse (G4): within each paragraph (blank line resets), keep the
    first ``[N]`` and drop later repeats of the SAME marker, consuming the leading
    horizontal space so no orphan/double space is left. Subsumes the v1 adjacent
    ``[3][3]``/``[3] [3]`` case; the marker still resolves to reference N.

    Only a ``[N]`` whose number is an actual reference (``valid_refs`` = the citation
    index values) is treated as a marker — a bracketed integer that is NOT a citation
    (a year ``[2024]``, an out-of-range index) is left untouched (codex review)."""
    def _para(text: str) -> str:
        seen: set[str] = set()
        def _one(mo: "re.Match[str]") -> str:
            if int(mo.group(1)) not in valid_refs:
                return mo.group(0)  # not a citation marker → leave verbatim
            if mo.group(1) in seen:
                return ""  # drop repeat + its leading space
            seen.add(mo.group(1))
            return mo.group(0)  # first occurrence keeps its original spacing
        return re.sub(r"[^\S\n]*\[(\d+)\]", _one, text)

    return "".join(
        part if i % 2 else _para(part) for i, part in enumerate(_PARA_BREAK_RE.split(seg))
    )


def _mark_citation_segment(seg: str, index: dict[str, int]) -> str:
    """Render inline URLs in a NON-fenced prose segment. A claim URL becomes its ``[N]``
    marker; a URL that is NOT an exact claim URL is a fabricated citation (the writer
    hallucinated a plausible same-domain path domain-grounding can't catch, e.g.
    ``pi.dev/packages/pi-agent-workflows`` when only ``…/pi-agents`` was fetched) — it is
    STRIPPED, never mapped to a guessed marker and never left dangling. The sentence
    prose stays (it is domain-grounded); only the bad URL token goes."""
    def _md(mo: "re.Match[str]") -> str:
        n = _cit_lookup(mo.group(2), index)
        return f"{mo.group(1)} [{n}]" if n else mo.group(1)  # non-claim → keep label, drop link

    def _paren(mo: "re.Match[str]") -> str:
        n = _cit_lookup(mo.group(1), index)
        return f" [{n}]" if n else ""  # non-claim → strip the whole "(url)" incl. its leading space

    def _bare(mo: "re.Match[str]") -> str:
        url = mo.group(1)
        n = _cit_lookup(url, index)
        trail = url[len(url.rstrip(_CIT_TRAILING)):]  # sentence punctuation attached to the URL
        return f" [{n}]{trail}" if n else trail  # non-claim → keep only the trailing punctuation

    seg = _CIT_MD_LINK_RE.sub(_md, seg)   # [label](url) -> "label [N]" | "label"
    seg = _CIT_PAREN_URL_RE.sub(_paren, seg)  # (url) -> " [N]" | ""
    seg = _CIT_BARE_URL_RE.sub(_bare, seg)    # url -> " [N]" | ""
    seg = re.sub(r"[^\S\n]+([.,;:!?])", r"\1", seg)  # heal " ." left by a strip
    seg = re.sub(r"[^\S\n]{2,}", " ", seg)           # collapse double spaces (not newlines)
    return _dedup_markers_per_paragraph(seg, set(index.values()))  # over-cite collapse (G4), paragraph-scoped


def _apply_citation_markers(text: str, claims: list[dict[str, Any]]) -> str:
    """Render the writer's inline URL citations as numbered markers ``[N]`` pointing
    at the References list. Deterministic and reliable BECAUSE the writer already
    cited each source explicitly by URL (copied verbatim from a claim) — so this is
    a render of a declared citation, not an attribution guess (no false-attribution
    risk), and it also de-stuffs the raw-URL clutter (G4). Runs AFTER grounding drops
    (only grounded URLs remain). Never rewrites the References section or a fenced
    (code/mermaid) block. Fail-open: a URL absent from the index is left untouched;
    every emitted ``[N]`` has a matching reference by construction (shared index)."""
    index = _citation_index(claims)
    if not index:
        return text
    m = _REFERENCES_HEADING_RE.search(text)
    body, refs = (text[: m.start()], text[m.start():]) if m else (text, "")
    parts = _FENCE_SPLIT_RE.split(body)  # odd segments are ```...``` fences — leave verbatim
    marked = "".join(
        seg if i % 2 else _mark_citation_segment(seg, index) for i, seg in enumerate(parts)
    )
    return marked + refs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate_research_first(
    requirement: str,
    *,
    client: Any,
    judge_client: Any = None,  # gray-zone relevance judge for _is_offtopic (RESEARCH)
    workspace_root: Path,
    session_id: str,
    emit: EmitFn = None,
) -> str:
    """Run the 5-stage research-first pipeline and return the final markdown."""
    emit = emit or (lambda *_a: None)
    ws_dir = Path(workspace_root) / session_id
    evidence_dir = ws_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # 1. FRAME
    groups = extract_requirements(client, requirement)
    title = _derive_title_from_requirement(requirement)
    subjects = (
        _extract_subjects(groups, requirement=requirement)
        or _subjects_from_requirement(client, requirement)
        or [title]
    )
    code_needed = any(_CODE_SHAPED_RE.search(b) for g in groups for b in g)
    diagram_needed = any(_DIAGRAM_SHAPED_RE.search(b) for g in groups for b in g)
    sections = _build_sections(groups)
    dbg(
        f"research_first FRAME: subjects={subjects} sections={sections} "
        f"code_needed={code_needed} diagram_needed={diagram_needed}"
    )
    emit("frame", {"subjects": subjects, "sections": sections, "code_needed": code_needed, "diagram_needed": diagram_needed})

    # 2. RESEARCH
    emit("research", {"subjects": subjects})
    ledger, assumptions, relationship, all_anchors = _research(
        subjects, evidence_dir, requirement, judge_client, emit=emit
    )
    # GENERIC RULE (user): subjects[] and the classified relationship are outputs
    # every downstream producer consumes — for N>=2 subjects that actually relate
    # (kind != independent) the skeleton includes a relationship section BY
    # CONSTRUCTION, named from the per-task descriptor (R3), never emergent.
    sections = _ensure_relationship_section(sections, subjects, relationship)

    # 3. CLAIMS
    claims = _build_claims(ledger, subjects, client, ws_dir, all_anchors)
    emit("claims", {"sources": sum(len(v) for v in ledger.values()), "claims": len(claims)})
    claims = _coverage_gate(
        claims, subjects, relationship, all_anchors, evidence_dir, requirement,
        client, judge_client, ws_dir, emit=emit,
    )

    # 4. WRITE
    emit("write", {"sections": [s for s in sections if s.lower() not in _SKIP_WRITE]})
    relationship_home = _pick_relationship_home(sections, relationship)
    # R3 branch on the classified KIND: cooperates/extends/unknown warrant an
    # integration story (diagram + code); competes/alternative a comparison
    # table (no integration artifact); independent no cross-subject artifact at
    # all. Per-subject diagrams ship for every kind.
    multi = len(subjects) >= 2
    wants_integration = multi and relationship.kind in _INTEGRATION_KINDS
    wants_comparison = multi and relationship.kind in ("competes", "alternative")
    # The integration diagram lands in the dedicated relationship section; N=1
    # (and non-integration kinds) get per-subject diagrams only, placed below.
    diagram_home = relationship_home if (diagram_needed and wants_integration) else None
    code_home = _pick_home(sections, _CODE_HOME_RE) if code_needed and not multi else None
    scope_home = _pick_home(sections, _SCOPE_HOME_RE)
    # ASSEMBLE-phase diagram renders (integration triple + per-subject diagrams)
    # extract plain structured lines from ALREADY-gathered claims — the tool loop
    # degrades that output (live: the triple prompt returned a self-referential
    # "Pi | powered by | Pi SDK" under the tool wrapper, collapsing src==tgt and
    # dropping the grounded Craft->Pi edge to a freehand fallback). Render on the
    # bare client; only the section prose writer keeps tools for any late lookup.
    from studio.tools import base_client
    render_client = base_client(client)
    written: dict[str, str] = {}
    for seq, name in enumerate(sections):
        if name.lower() in _SKIP_WRITE:
            continue
        text = _write_section(name, requirement, _claims_for_section(claims, name, seq), client)
        # Ruling (c): strip the section's own self-heading echo + drop/demote any
        # stray in-body heading BEFORE splicing adds legitimate fences below, so a
        # duplicate ``##`` is impossible by construction (finalize dedupe stays cold).
        text = _sanitize_section_headings(text, sections)
        # URL⊆claims invariant (team-lead): checked against the FULL claims
        # set here, before any diagram/code fences get spliced in below.
        text = _drop_ungrounded_sentences(text, claims)
        if name == code_home:
            # N=1 only (see `multi` gate above) — the single-evidence-file path,
            # unchanged from before this round.
            text = _splice_code(text, evidence_dir, client)
        if name == diagram_home and wants_integration:
            text = _splice_diagram(text, subjects, claims, render_client, relationship)
            # The diagram shows the integration EDGE; the relationship table enumerates
            # the fan-out a single edge can't (one package/SDK -> several targets). Fail-
            # open when no subject-grounded fan-out exists, so a thin integration is
            # unaffected.
            text = _splice_relationship_table(text, subjects, all_anchors, claims)
        if name == relationship_home and wants_comparison:
            # competes/alternative: a grounded comparison table replaces the
            # integration diagram/code entirely (R3).
            text = _splice_comparison_table(text, subjects, claims)
        if name == scope_home and assumptions:
            # P4/D3 honesty: the resolved subject interpretation is a stated
            # assumption, not a silent guess — surfaced where a reader looks
            # for scope, not buried in a dbg line.
            text = text.rstrip() + "\n\n**Assumptions:**\n" + "\n".join(f"- {a}" for a in assumptions)
        # REBUILD-LESSONS §3: repair fence contamination at EVERY write boundary,
        # not only once at final assembly — a per-section defect must not survive
        # into a later section's own fence-balance reasoning.
        text, _ = _repair_fence_contamination(text)
        written[name] = text

    # Per-subject architecture diagrams (user emphasis: one diagram per subject
    # plus the integration diagram already spliced above) —
    # placed near each subject's own content, once every section has real text
    # to pick a home from.
    subject_homes: dict[str, str] = {}
    if diagram_needed:
        exclude = {diagram_home} if diagram_home else set()
        for subject in subjects:
            home = _pick_subject_home(written, subject, exclude)
            if home:
                subject_homes[subject] = home
                written[home] = _splice_subject_diagram(written[home], subject, claims, render_client)

    # Per-subject code examples + ONE integration example (user: "sample code
    # also needs to show the integration") — same iterate-subjects-then-
    # integration pattern as diagrams above. N=1 already got its code example
    # from the code_home/_splice_code path in the main loop; this is additive
    # for N>=2 only.
    code_subject_homes: dict[str, str] = {}
    if code_needed and multi:
        exclude = {relationship_home} if relationship_home else set()
        for subject in subjects:
            home = _pick_subject_home(written, subject, exclude)
            if home:
                code_subject_homes[subject] = home
                written[home] = _splice_subject_code(
                    written[home], subject, claims, client, evidence_dir
                )
        # The ONE integration example ships only for the integration kinds — a
        # competes/alternative report gets the comparison table instead.
        if wants_integration and relationship_home:
            written[relationship_home] = _splice_integration_code(
                written[relationship_home], subjects, claims, relationship, client
            )
    dbg(
        f"research_first WRITE: sections={len(written)} code_home={code_home!r} "
        f"diagram_home={diagram_home!r} subject_homes={subject_homes!r} "
        f"code_subject_homes={code_subject_homes!r} relationship_home={relationship_home!r} "
        f"kind={relationship.kind!r}"
    )

    summary = _drop_ungrounded_sentences(
        _sanitize_section_headings(
            _write_summary(requirement, written, client, relationship), sections
        ),
        claims,
    )

    # 5. ASSEMBLE
    text = _assemble(title, summary, sections, written)
    # v44 ordering fix: enforce References-last BEFORE rebuilding it — a spliced
    # or model-emitted section must never trail References (rebuild-from-claims
    # then finds it correctly at the tail).
    text = _enforce_references_last(text)
    # dedupe_sections (old hub/spoke pipeline) is deliberately NOT run here
    # (team-lead ruling): this linear pipeline writes each section exactly
    # once from ``sections``, so duplicate ## headings are impossible BY
    # CONSTRUCTION — dedupe_sections had nothing real to dedupe and could
    # only corrupt (proven live: it misread "#"-prefixed comment lines
    # inside a spliced code fence as headings and merged across the false
    # boundary, injecting prose into the middle of a code block). A guard
    # against an impossible state gets deleted, not fixed. Tripwire instead,
    # in case that construction guarantee is ever wrong:
    _dupes = _find_duplicate_headings(text)
    if _dupes:
        dbg(f"research_first ASSEMBLE: duplicate headings {_dupes!r} — construction guarantee violated")
    # ToC guarantee (G6): exactly one H1 (the title); demote any leaked body H1 to H3.
    text = _demote_stray_h1(text)
    if any(s.lower() == "references" for s in sections):
        text = _rebuild_references_from_claims(text, claims, _url_title_map(evidence_dir))
    text, _ = _repair_fence_contamination(text)
    text, _ = _repair_doubled_citations(text)
    # SCOPE NOTE 1 (team-lead): the URL⊆claims invariant applied per-section
    # and to the summary during WRITE only sees each call's own claims slice
    # — a section fed zero claims (or a caption/label added after that pass
    # ran) would be missed. One final, fence-aware sweep over the WHOLE
    # assembled document closes that gap.
    text = _drop_ungrounded_sentences_artifact_wide(text, claims)
    # Render the writer's inline URL citations as numbered [N] markers pointing at the
    # References list — AFTER grounding drops (only grounded URLs remain) so a stripped
    # sentence never leaves a dangling marker. De-stuffs raw-URL clutter (G4).
    text = _apply_citation_markers(text, claims)
    lints = lint_artifact(text)
    dbg(
        f"research_first ASSEMBLE: words={len(text.split())} "
        f"fences={text.count('```')} mermaid={text.count('```mermaid')} lints={len(lints)}"
    )
    emit("assemble", {"words": len(text.split()), "lints": len(lints)})
    return text
