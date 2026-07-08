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
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from studio.artifact_lint import lint_artifact
from studio.artifact_text import (
    _derive_title_from_requirement,
    _REFERENCES_HEADING_RE,
    _repair_doubled_citations,
    _repair_fence_contamination,
)
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
#: "evidence" match, kept as a separate constant since code and diagram can route
#: to different sections in the same report).
_CODE_HOME_RE = re.compile(r"(?i)evidence|analysis|implementation|example|walkthrough|code")
_DIAGRAM_HOME_RE = re.compile(r"(?i)architect|design|topolog|overview|system|component|key findings|evidence")
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
_MAX_JOINT_QUERIES = 2
_MAX_SOURCES_PER_JOINT_QUERY = 2
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
    except Exception:  # noqa: BLE001 — a bad fallback call must never stall FRAME
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


_INTEGRATION_SECTION_RE = re.compile(r"(?i)integrat")


def _ensure_integration_section(sections: list[str], subjects: list[str]) -> list[str]:
    """N>=2 subjects → the skeleton MUST include an integration/relationship
    section BY CONSTRUCTION (user: "all the flow needs to consider their
    relationships" — never emergent, same principle as disambiguation and the
    relationship-hypothesis step). No-op for <2 subjects or an already-present
    integration-shaped section (an explicit "include an X Integration section"
    branch from _build_sections)."""
    if len(subjects) < 2 or any(_INTEGRATION_SECTION_RE.search(s) for s in sections):
        return sections
    out = list(sections)
    out.insert(-1, f"{' and '.join(subjects)} Integration")
    return out


def _pick_integration_home(sections: list[str]) -> str | None:
    """The dedicated integration section if one exists (always true for N>=2
    subjects post ``_ensure_integration_section``)."""
    return next((s for s in sections if _INTEGRATION_SECTION_RE.search(s)), None)


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


def _resolve_relationship(
    subjects: list[str], resolved: dict[str, str], requirement: str, judge_client: Any
) -> tuple[str, list[str]]:
    """Same architecture as disambiguation, one step later: subject IDENTITY
    can't be assumed (why we disambiguate first), and subject RELATIONSHIP
    can't be emergent either (a single-subject diagram in a multi-subject report
    was the proof of that). One
    call, given the resolved descriptors, hypothesizes the integration
    mechanism + what to verify — it DIRECTS research and the diagram's edge
    candidates; it never becomes content by itself (grounding rules elsewhere
    are unchanged). Pairwise only; fails open to a neutral sentinel."""
    if judge_client is None or len(subjects) < 2:
        return "composed side-by-side, mechanism unknown", []
    d0, d1 = resolved[subjects[0]], resolved[subjects[1]]
    prompt = (
        f"TASK: {requirement[:400]}\n\n"
        f"Subject A: {subjects[0]} — {d0}\nSubject B: {subjects[1]} — {d1}\n\n"
        "Hypothesize how A and B relate for THIS task — which documented "
        "surface of one (an API, CLI, MCP, SDK, or extension point) could "
        "drive, host, or call the other, and the likely direction of "
        "composition. Answer in exactly this format:\n"
        "MECHANISM: <short hypothesis, e.g. 'A calls B via an MCP server'>\n"
        "VERIFY: <comma-separated terms to search for to confirm this>"
    )
    try:
        reply = judge_client.chat([{"role": "user", "content": prompt}])
        text = str(getattr(reply, "text", "") or "")
    except Exception:  # noqa: BLE001 — a bad relationship call must never stall research
        return "composed side-by-side, mechanism unknown", []
    m = re.search(r"MECHANISM:\s*(.+)", text)
    v = re.search(r"VERIFY:\s*(.+)", text)
    mechanism = m.group(1).strip() if m else "composed side-by-side, mechanism unknown"
    verify_terms = [t.strip() for t in (v.group(1).split(",") if v else []) if t.strip()]
    return mechanism, verify_terms


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


def _research(
    subjects: list[str],
    evidence_dir: Path,
    requirement: str,
    judge_client: Any,
    *,
    emit: EmitFn,
) -> tuple[dict[str, list[dict[str, str]]], list[str], str, list[str]]:
    """Per subject: disambiguate FIRST (never search the bare subject name
    alone), then fetch up to ``_MAX_SOURCES_PER_SUBJECT`` distinct-domain,
    on-topic pages using the resolved descriptor+anchors. Once all subjects are
    resolved, hypothesize their RELATIONSHIP (mechanism + verify terms) and
    derive the joint queries FROM it, under the ``"__joint__"`` ledger key.
    Returns ``(ledger, assumptions, mechanism, verify_terms)`` — the resolved
    interpretation of each subject and the relationship hypothesis, for an
    honest Scope-section disclosure (P4/D3) and the diagram's edge candidates.
    ``verify_terms`` lets a downstream consumer corroborate the hypothesis
    against real evidence before ever printing it as content (team-lead: a
    hypothesis directs search/prompts but never becomes content by itself)."""
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

    mechanism = "composed side-by-side, mechanism unknown"
    verify_terms: list[str] = []
    if len(subjects) >= 2:
        mechanism, verify_terms = _resolve_relationship(subjects, resolved, requirement, judge_client)
        assumptions.append(f"Hypothesized relationship: {mechanism}.")
        dbg(f"research_first RESEARCH: relationship subjects={subjects} mechanism={mechanism!r}")

        d0, d1 = resolved[subjects[0]], resolved[subjects[1]]
        joint: list[dict[str, str]] = []
        # Joint queries are derived from the RELATIONSHIP HYPOTHESIS (mechanism
        # + verify terms), not generic subject-A+subject-B concatenation —
        # replacing the anchor-derived version, which still left joint fetches
        # thin (team-lead: "that's why joint fetches have been thin").
        verify_words = " ".join(verify_terms[:4]) or mechanism
        joint_queries = [f"{d0} {d1} {mechanism}".strip(), f"{d0} {d1} {verify_words}".strip()]
        # The joint loop had no anchor gate at all before this — only the
        # generic offtopic floor. A page can be broadly on-topic yet name
        # neither subject's anchors at all; gate on the UNION of every
        # subject's anchors (a joint page only needs to connect to one side
        # to be a real integration source, not both).
        union_anchors = [a for subject in subjects for a in all_anchors.get(subject, [])]
        for query in joint_queries[:_MAX_JOINT_QUERIES]:
            results = _search(query)
            emit("research_query", {"subject": "__joint__", "query": query, "n_results": len(results)})
            for r in results[:_MAX_SOURCES_PER_JOINT_QUERY]:
                url = str(getattr(r, "url", "") or "").strip()
                if not url:
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
        ledger["__joint__"] = joint
        dbg(f"research_first RESEARCH: joint queries={len(joint_queries[:_MAX_JOINT_QUERIES])} fetched={len(joint)}")
    return ledger, assumptions, mechanism, verify_terms


# ---------------------------------------------------------------------------
# CLAIMS — one grounded extraction call per fetched source.
# ---------------------------------------------------------------------------

_CLAIM_BLOCK_RE = re.compile(r"\n(?=CLAIM:)")


def _extract_claims_from_source(
    url: str, content: str, subjects: list[str], client: Any, *, loop_subject: str | None = None
) -> list[dict[str, Any]]:
    """3-6 (claim, quote, subject-tags) rows from one fetched page. A claim whose
    quote is not a verbatim substring of the cached page is dropped — deterministic
    grounding, no judge call (§11.3 D1)."""
    if client is None or not (content or "").strip():
        return []
    prompt = (
        "Extract 3-6 factual CLAIMS from the SOURCE TEXT below. Each claim MUST be "
        "grounded in a QUOTE copied VERBATIM from the source (10-40 words, exact "
        "wording). Output ONE block per claim in exactly this format, nothing else:\n\n"
        "CLAIM: <one sentence>\nQUOTE: <verbatim quote from the source>\n"
        f"SUBJECTS: <comma-separated subset of: {', '.join(subjects) or '(none named)'}>\n\n"
        f"=== SOURCE ({url}) ===\n{content[:_CLAIM_SOURCE_CHARS]}\n=== END SOURCE ==="
    )
    try:
        reply = client.chat([{"role": "user", "content": prompt}])
        text = str(getattr(reply, "text", "") or "")
    except Exception as exc:  # noqa: BLE001 — a bad claim call must never break the run
        dbg(f"research_first _extract_claims_from_source: call failed url={url!r} exc={exc!r}")
        return []

    norm_content = " ".join(content.split()).lower()
    out: list[dict[str, Any]] = []
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
            or norm_quote not in norm_content
            or len(quote) > _MAX_QUOTE_CHARS
            or _is_markup_dense_quote(quote)
        ):
            continue  # unverifiable, oversized, or markup noise — never woven in
        sm = re.search(r"SUBJECTS:\s*(.+)", block)
        tags = [s.strip() for s in (sm.group(1).split(",") if sm else []) if s.strip()]
        # Source selection is gated by anchor/topic checks; per-claim SUBJECTS tags
        # were not — a single-subject loop vets a source against ONLY its own
        # anchors, so any tag other than exactly that subject is unvetted (live:
        # a package page fetched under one subject loop was tagged with another
        # subject that had never been checked against its own anchors).
        if loop_subject:
            if tags != [loop_subject]:
                tags = [loop_subject]
        else:
            # A joint-fetched source was found via the RELATIONSHIP query, not
            # any one subject's anchors — it should never carry a single-subject
            # tag (live: one joint source got tagged with one subject on most
            # claims and both subjects on another, purely from an LLM extraction
            # call with nothing to clamp it).
            tags = list(subjects)
        out.append({"claim": claim, "quote": quote, "url": url, "subjects": tags})
    return out


def _build_claims(
    ledger: dict[str, list[dict[str, str]]],
    subjects: list[str],
    client: Any,
    ws_dir: Path,
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for key, sources in ledger.items():
        loop_subject = None if key == "__joint__" else key
        default_tags = list(subjects) if key == "__joint__" else [key]
        for src in sources:
            for c in _extract_claims_from_source(
                src["url"], src["content"], subjects, client, loop_subject=loop_subject
            ):
                if not c["subjects"]:
                    c["subjects"] = default_tags
                claims.append(c)
    try:
        with (ws_dir / "claims.jsonl").open("w", encoding="utf-8") as fh:
            for c in claims:
                fh.write(json.dumps(c) + "\n")
    except OSError:
        pass
    n_sources = sum(len(v) for v in ledger.values())
    dbg(f"research_first CLAIMS: {len(claims)} claims from {n_sources} sources")
    return claims


def _claims_for_section(claims: list[dict[str, Any]], section: str) -> list[dict[str, Any]]:
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
        "Use ONLY the claims below as your factual basis — never invent a fact "
        "or a citation. Write 2-4 plain prose paragraphs (no headings). Every "
        "paragraph must cite at least one claim's URL inline, copied EXACTLY "
        "from the claims below — never a URL from memory. Do not comment on "
        "the quality or sufficiency of the claims; if there is little to say, "
        "write less.\n\n"
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


def _splice_subject_code(text: str, subject: str, claims: list[dict[str, Any]], client: Any) -> str:
    """One code example per subject (same pattern as ``_splice_subject_diagram``)
    — grounded in ONLY that subject's own claims, compile-gated. ponytail: skips
    the raw-evidence-file preference ``_splice_code`` has (no subject tag on
    disk to filter evidence/*.md by) and always synthesizes from claims text;
    add evidence-file filtering if scoring shows the synthesis path is weaker."""
    if client is None:
        return text
    subj_claims = [c for c in claims if subject in (c.get("subjects") or [])]
    if not subj_claims:
        return text
    ev_lines = "\n".join(f"- {c['claim']} (URL: {c['url']})" for c in subj_claims[:10])
    prompt = (
        f"Using ONLY the claims below about {subject}, produce a minimal runnable "
        "code example grounded in what they describe. Output ONLY a single "
        "fenced code block (```language ... ```), nothing else.\n\n"
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
    candidate = f"{text}\n\nExample: {subject}.\n\n{block}"
    if len(lint_artifact(candidate)) > len(lint_artifact(text)):
        return text  # never ship a splice that introduces a NEW lint issue
    return candidate


def _integration_interfaces_grounded(block: str, claims: list[dict[str, Any]]) -> bool:
    """Rule (b): an interface word (api/cli/mcp/...) appearing in the generated
    code must also appear somewhere in the claims — never an invented surface
    absent from evidence. Tokenizes on non-letter runs (NOT ``_INTERFACE_WORD_RE``
    directly): ``\\b`` treats "_" as a word char, so it would never isolate "mcp"
    inside the realistic snake_case identifier "call_mcp_server" — the exact
    shape generated Python code uses. Falls open when the code names no
    interface word at all — nothing to check."""
    tokens = set(re.findall(r"[a-zA-Z]+", block.lower()))
    code_interface_words = {t for t in tokens if _INTERFACE_WORD_RE.fullmatch(t)}
    if not code_interface_words:
        return True
    claim_text = " ".join(c["claim"] for c in claims).lower()
    return any(w in claim_text for w in code_interface_words)


def _grounded_interface_words(claims: list[dict[str, Any]]) -> list[str]:
    """Interface words (api/cli/mcp/...) that literally appear in the claims —
    the ONLY interfaces a synthesized integration example may name. Order-
    preserving over ``_INTERFACE_WORD_RE``'s alternation, deduped, lowercase.

    The FRAME relationship hypothesis ("...via an SDK...") is a SEARCH directive,
    never code content (team-lead: hypothesis directs search, never becomes
    content). Seeding the code prompt with the raw hypothesis mechanism made the
    model write `import pi_sdk` around an SDK no claim documents, and the
    grounding gate then correctly rejected every sample (v43 live: 0/4). Steering
    the prompt to the claim-grounded interfaces instead removes the contradiction
    at the source."""
    claim_text = " ".join(c.get("claim", "") for c in claims).lower()
    seen: list[str] = []
    # Same alternation as _INTERFACE_WORD_RE but with an optional plural — a
    # claim that says "REST APIs" / "MCP servers" documents the api/mcp
    # interface just as much as the singular form (the shared regex's trailing
    # \b can't span the plural "s"; the code-side gate keeps the strict form).
    for m in _INTERFACE_WORD_PLURAL_RE.finditer(claim_text):
        w = m.group(1).lower()
        if w not in seen:
            seen.append(w)
    return seen


def _splice_integration_code(
    text: str, subjects: list[str], claims: list[dict[str, Any]], mechanism: str, client: Any
) -> str:
    """One PROPOSED integration example — synthesized (a real combined example
    may not exist in sources), gated on: (a) compile(), (b) only interfaces
    named in claims, (c) captioned as proposed usage, never as quoted source.

    The prompt is steered to the CLAIM-GROUNDED interfaces, not the raw FRAME
    hypothesis — the hypothesis's interface (e.g. "SDK") is often absent from
    every claim, and code built around it fails the grounding gate every time
    (v43 live: 0/4). When no interface is documented at all, no groundable
    example can exist → skip rather than fabricate."""
    if client is None or len(subjects) < 2:
        return text
    grounded_ifaces = _grounded_interface_words(claims)
    if not grounded_ifaces:
        dbg("research_first _splice_integration_code: skipped — no claim-documented interface")
        return text
    joint = [c for c in claims if len(c.get("subjects") or []) >= 2] or claims
    ev_lines = "\n".join(f"- {c['claim']} (URL: {c['url']})" for c in joint[:10])
    iface_phrase = ", ".join(grounded_ifaces)
    prompt = (
        f"Write a short, minimal PROPOSED usage example showing how "
        f"{' and '.join(subjects)} could be composed. Build it around one of "
        f"these interfaces, which ARE documented in the claims: {iface_phrase}. "
        "Use ONLY interfaces named in the claims below — never invent one absent "
        "from them (in particular, do NOT use an interface just because it is "
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
    if not _integration_interfaces_grounded(block, claims):
        dbg("research_first _splice_integration_code: rejected — interface not named in claims")
        return text
    caption = (
        f"Proposed usage — {' + '.join(subjects)} composed via {mechanism}. "
        "Illustrative only, synthesized from documented interfaces above; not quoted source code."
    )
    candidate = f"{text}\n\n{caption}\n\n{block}"
    if len(lint_artifact(candidate)) > len(lint_artifact(text)):
        return text
    return candidate


#: A subject-tagged component line: ``COMPONENT: <name> | <subject> | <role>``.
_DIA_COMPONENT_RE = re.compile(r"COMPONENT:\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|.*$")
_DIA_EDGE_RE = re.compile(r"EDGE:\s*(.+?)\s*->\s*([^|]+?)\s*(?:\|\s*(.*))?$")
_MAX_DIA_NODES = 15
#: A cross-subject edge is grounded only if the evidence NAMES an integration
#: mechanism — never invented from neither a joint claim nor a documented
#: interface on each side (team-lead rule 2).
#: The one place the integration-mechanism vocabulary is spelled out — generic
#: interface terms (never task/subject names). Both the strict edge-grounding
#: regex and the plural-tolerant claim-scan regex are built from this single
#: alternation so the vocabulary is defined exactly once.
_INTERFACE_WORD_ALT = r"api|cli|mcp|sdk|extension|plugin|webhook|connector|integrat\w*|interface"
_INTERFACE_WORD_RE = re.compile(rf"(?i)\b({_INTERFACE_WORD_ALT})\b")
#: Plural-tolerant form for scanning documented interfaces in claim prose (see
#: _grounded_interface_words); group(1) is the singular base.
_INTERFACE_WORD_PLURAL_RE = re.compile(rf"(?i)\b({_INTERFACE_WORD_ALT})s?\b")


#: Generic component-naming guidance shared by every diagram prompt. Names the
#: MORPHOLOGY of a real architectural component (proper module/class/package/
#: service names) versus sentence glue — categorically, never by listing the
#: task's actual weak words (that would game a specific failing case rather than
#: fix the class). Root cause it addresses: with rich claims naming real
#: components (e.g. an "AgentContext" class, a hyphenated core package), a weak
#: model still emitted bare prose words ("Designed", "Commits") lifted from a
#: claim sentence, and literal-token grounding could not tell them apart because
#: both appear in the prose.
_COMPONENT_NAMING_RULE = (
    "Each <name> must be a PROPER named component — a module, class, package, "
    "service, or interface as named in the evidence (multi-word names, CamelCase "
    "identifiers, and hyphenated/dotted package names are all good). Never use a "
    "bare verb, adjective, participle, or generic sentence word as a name."
)


def _diagram_prompt(subjects: list[str], claims: list[dict[str, Any]], mechanism: str = "") -> str:
    subj_list = ", ".join(subjects)
    ev_lines = "\n".join(
        f"- [{','.join(c.get('subjects') or [])}] {c['claim']} (URL: {c['url']})" for c in claims[:16]
    )
    # The relationship hypothesis (FRAME) DIRECTS which edge to look for; it
    # never becomes content by itself — the evidence-only instruction below is
    # unchanged, and _integration_label's grounding gate still decides what
    # actually ships regardless of what this hint suggests.
    hint = f"A candidate mechanism to look for in the evidence: {mechanism}\n\n" if mechanism else ""
    return (
        "List the architecture of the subjects below as plain lines — no prose, "
        "no markdown, no code fences:\n"
        f"COMPONENT: <name> | <subject, EXACTLY one of: {subj_list}> | <one-line role>\n"
        "EDGE: <name A> -> <name B> | <optional label>\n\n"
        f"Cover BOTH subjects ({subj_list}), several components each. Include at "
        "least one EDGE connecting a component from one subject to a component "
        "from the OTHER subject, labeled with the actual integration mechanism "
        "(e.g. an API, CLI, MCP, or extension-point interface) NAMED in the "
        f"evidence below — never invent a mechanism the evidence doesn't name.\n\n"
        f"{_COMPONENT_NAMING_RULE}\n\n{hint}"
        f"EVIDENCE:\n{ev_lines}"
    )


def _parse_cluster_diagram(
    raw: str, subjects: list[str]
) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    """``(components, edges)`` — components is ``[(name, subject)]``, order-
    preserving, deduped by subject+lowercase name; a component whose subject
    doesn't match one of *subjects* (loosely, case/substring-insensitive) is
    dropped."""
    components: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    edges: list[tuple[str, str, str]] = []
    for ln in raw.splitlines():
        ln = ln.strip()
        m = _DIA_COMPONENT_RE.match(ln)
        if m:
            name, subj_raw = m.group(1).strip(), m.group(2).strip().lower()
            matched = next((s for s in subjects if s.lower() == subj_raw), None) or next(
                (s for s in subjects if s.lower() in subj_raw or subj_raw in s.lower()), None
            )
            if not (name and matched):
                continue
            key = (matched.lower(), name.lower())
            if key not in seen:
                seen.add(key)
                components.append((name, matched))
            continue
        m = _DIA_EDGE_RE.match(ln)
        if m:
            edges.append((m.group(1).strip(), m.group(2).strip(), (m.group(3) or "").strip()))
    return components, edges


def _ground_components(components: list[tuple[str, str]], grounding_text: str) -> list[tuple[str, str]]:
    """Keep a component iff >=1 significant (>=4 char) token of its name is
    literally present in *grounding_text* — same literal-token principle as
    ``diagram_render._ground`` (reused directly, not re-derived)."""
    low = grounding_text.lower()
    kept = []
    for name, subject in components[:_MAX_DIA_NODES]:
        discriminating = _component_label_tokens(name)
        if discriminating and any(re.search(r"\b" + re.escape(t), low) for t in discriminating):
            kept.append((name, subject))
    return kept


def _cross_cluster_edges(
    components: list[tuple[str, str]], edges: list[tuple[str, str, str]]
) -> list[tuple[str, str, str]]:
    """Edges whose two endpoints belong to DIFFERENT subject clusters."""
    subjects_of: dict[str, list[str]] = {}
    for name, subject in components:
        subjects_of.setdefault(name.lower(), [])
        if subject not in subjects_of[name.lower()]:
            subjects_of[name.lower()].append(subject)
    out = []
    for a, b, label in edges:
        a_subjects, b_subjects = subjects_of.get(a.lower(), []), subjects_of.get(b.lower(), [])
        pairs = [(sa, sb) for sa in a_subjects for sb in b_subjects if sa != sb]
        if len(pairs) == 1:
            out.append((a, b, label))
    return out


def _integration_label(
    subjects: list[str],
    claims: list[dict[str, Any]],
    mechanism: str = "",
    verify_terms: list[str] | None = None,
) -> str | None:
    """A grounded label for a cross-subject edge, or ``None`` if ungrounded.
    (a) a joint-tagged claim naming an integration mechanism (top priority,
    unchanged); (b) the relationship HYPOTHESIS may upgrade a generic
    "integration" label, but ONLY when independently corroborated — its own
    words (or the verify terms it proposed checking for) actually appear in
    the joint claims' text, not just asserted. A hypothesis directs search/
    prompts but never becomes content by itself (team-lead) — printing an
    uncorroborated mechanism string as the edge label would violate that;
    (c) any joint claim at all, uncorroborated (generic label); (d) EACH
    subject has its own claim naming a documented interface (API/CLI/MCP/...)
    — never invented from neither (team-lead rule 2)."""
    joint = [c for c in claims if len(c.get("subjects") or []) >= 2]
    for c in joint:
        m = _INTERFACE_WORD_RE.search(c["claim"])
        if m:
            return m.group(1).lower()
    if joint:
        if mechanism:
            # Exclude bare subject-name tokens — a mechanism sentence always
            # names both subjects ("Subject A calls Subject B via...") and so does every
            # joint claim, so that overlap alone would corroborate ANY
            # mechanism string, rubber-stamping the hypothesis instead of
            # checking it.
            subject_tokens = frozenset(
                token
                for subject in subjects
                for token in _ANCHOR_TOKEN_RE.findall(subject.lower())
            )
            joint_text = " ".join(c["claim"] for c in joint)
            candidates = [mechanism, *(verify_terms or [])]
            if _anchor_hits(joint_text, candidates, exclude=subject_tokens) > 0:
                # Corroboration decides WHETHER to upgrade; it never licenses
                # printing the hypothesis sentence verbatim (v40 live defect:
                # a 9-word hypothesis rendered as the cross-edge label). A
                # label is a mechanism name — long mechanisms reduce to their
                # interface word, then a corroborated verify term, else stay
                # generic.
                if len(mechanism.split()) <= 6:
                    return mechanism
                im = _INTERFACE_WORD_RE.search(mechanism)
                if im:
                    return im.group(1).lower()
                for term in verify_terms or []:
                    if _anchor_hits(joint_text, [term], exclude=subject_tokens) > 0:
                        return term
        return "integration"
    per_subject: dict[str, str] = {}
    for subject in subjects:
        for c in claims:
            if subject in (c.get("subjects") or []):
                m = _INTERFACE_WORD_RE.search(c["claim"])
                if m:
                    per_subject[subject] = m.group(1).lower()
                    break
    if len(per_subject) >= len(subjects) >= 2:
        return " / ".join(dict.fromkeys(per_subject.values()))
    return None


def _mermaid_safe_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", text) or "S"


def _render_cluster_diagram(
    components: list[tuple[str, str]], edges: list[tuple[str, str, str]], subjects: list[str]
) -> str:
    """Names + edges → a guaranteed-valid mermaid ``flowchart TD`` with one
    ``subgraph`` per subject cluster. Valid by construction: synthetic ``Nx``
    ids (never the raw label), every label quoted, edges only between rendered
    nodes — same pattern as ``diagram_render._render``, extended with clusters."""
    ids: dict[tuple[str, str], str] = {}
    ids_by_name: dict[str, list[tuple[str, str]]] = {}
    lines = ["flowchart TD"]
    i = 0
    for subject in subjects:
        names = [name for name, s in components if s == subject]
        if not names:
            continue
        lines.append(f'    subgraph {_mermaid_safe_id(subject)}["{subject}"]')
        for name in names:
            nid = f"N{i}"
            i += 1
            key = (subject.lower(), name.lower())
            ids[key] = nid
            ids_by_name.setdefault(name.lower(), []).append(key)
            lines.append(f'        {nid}["{name.replace(chr(34), chr(39))}"]')
        lines.append("    end")
    for a, b, label in edges:
        a_keys = ids_by_name.get(a.lower(), [])
        b_keys = ids_by_name.get(b.lower(), [])
        pairs = [(ak, bk) for ak in a_keys for bk in b_keys if ak[0] != bk[0]]
        if not pairs:
            pairs = [(ak, bk) for ak in a_keys for bk in b_keys]
        if not pairs:
            continue
        ak, bk = pairs[0]
        ida, idb = ids.get(ak), ids.get(bk)
        if not ida or not idb or ida == idb:
            continue
        if label:
            lines.append(f'    {ida} -->|"{label.replace(chr(34), chr(39))}"| {idb}')
        else:
            lines.append(f"    {ida} --> {idb}")
    return "\n".join(lines)


_FEATURE_STOPWORDS = {
    "about", "agent", "agents", "allow", "allows", "also", "and", "based",
    "because", "being", "both", "built", "called", "calls", "can", "connect",
    "connects", "contains", "could", "data", "design", "develop",
    "development", "docs", "documentation", "examples", "files", "from",
    "has", "have", "include", "includes", "including", "into", "like",
    "local", "main", "minimal",
    "named", "over", "platform", "provides", "repository", "repositories",
    "report", "research", "source", "sources", "subject", "support",
    "supports", "task", "tasks", "that", "their", "through", "used", "uses",
    "using", "various", "with", "workflow", "workflows",
}


def _component_label_tokens(label: str) -> list[str]:
    """Discriminating lowercase tokens that make a diagram label component-like.

    This is intentionally generic: it rejects sentence glue and architecture
    nouns by category, not task-specific names. Short all-caps acronyms (API,
    MCP) are allowed because they are common real component/interface labels.
    """
    from studio.diagram_render import _GENERIC_LABELS, _significant_tokens

    sig = _significant_tokens(label)
    if not sig:
        stripped = label.strip()
        return [stripped.lower()] if re.fullmatch(r"[A-Z0-9]{2,6}", stripped) else []
    return [t for t in sig if t not in _GENERIC_LABELS and t not in _FEATURE_STOPWORDS]


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
        if low in subject_names or any(name in low for name in subject_names) or low in labels:
            return
        if not _component_label_tokens(label):
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
        for phrase in re.findall(r"\b[A-Z][A-Za-z0-9_-]*(?:\s+[A-Z][A-Za-z0-9_-]*){0,3}\b", claim):
            add(phrase)
        for word in _ANCHOR_TOKEN_RE.findall(claim.lower()):
            if word in subject_tokens or word in _FEATURE_STOPWORDS or len(word) < 4:
                continue
            add(word.replace("-", " ").title())
        if len(labels) >= limit:
            break
    return labels[:limit]


def _fallback_subject_diagram(subject: str, claims: list[dict[str, Any]]) -> str | None:
    labels = _subject_feature_labels(subject, claims, all_subjects=[subject])
    if len(labels) < 2:
        return None
    sid = _mermaid_safe_id(subject)
    lines = ["flowchart TD", f'    {sid}["{subject}"]']
    for i, label in enumerate(labels):
        nid = f"{sid}_N{i}"
        lines.append(f'    {sid} --> {nid}["{label.replace(chr(34), chr(39))}"]')
    return "\n".join(lines)


def _fallback_cluster_diagram(
    subjects: list[str],
    claims: list[dict[str, Any]],
    mechanism: str = "",
    verify_terms: list[str] | None = None,
) -> str | None:
    """Deterministic clustered integration diagram from grounded subject features."""
    if len(subjects) < 2:
        return None
    label = _integration_label(subjects, claims, mechanism, verify_terms)
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
    return _render_cluster_diagram(components, edges, subjects)


def _splice_diagram(
    section_text: str,
    subjects: list[str],
    claims: list[dict[str, Any]],
    client: Any,
    mechanism: str = "",
    verify_terms: list[str] | None = None,
) -> str:
    from studio.diagram_render import build_diagram_block

    body: str | None = None
    if client is not None and claims and len(subjects) >= 2:
        try:
            reply = client.chat([{"role": "user", "content": _diagram_prompt(subjects, claims, mechanism)}])
            raw = str(getattr(reply, "text", "") or "")
        except Exception as exc:  # noqa: BLE001 — a bad diagram call must never break the run
            dbg(f"research_first _splice_diagram: call failed exc={exc!r}")
            raw = ""
        if raw:
            components, edges = _parse_cluster_diagram(raw, subjects)
            grounding_text = section_text + "\n" + " ".join(c["claim"] for c in claims)
            components = _ground_components(components, grounding_text)
            cross = _cross_cluster_edges(components, edges)
            # Structure (rule 1) AND grounding (rule 2): a single-subject diagram
            # or two disconnected islands both fail here — no grounded cross-edge
            # means no diagram, not a fabricated one.
            if len(components) >= 2 and cross and _integration_label(subjects, claims, mechanism, verify_terms):
                body = _render_cluster_diagram(components, cross, subjects)
    if not body:
        body = _fallback_cluster_diagram(subjects, claims, mechanism, verify_terms)
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
        f"{_COMPONENT_NAMING_RULE}\n\n"
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


def _write_summary(requirement: str, written: dict[str, str], client: Any, mechanism: str = "") -> str:
    """Written LAST from the already-drafted body (D4) — it can only summarize
    what exists, killing the overclaiming-summary failure by construction."""
    if client is None:
        return "_(summary unavailable)_"
    body = "\n\n".join(f"### {name}\n{text[:1200]}" for name, text in written.items())
    # N>=2 subjects: the summary must state the relationship conclusion, not
    # just recap each subject in isolation — grounded in what the sections
    # already say (the "never introduce" rule above still applies).
    relationship_instruction = (
        f" State your conclusion on how the subjects relate, drawing on what the "
        f"sections say about this hypothesis: {mechanism}." if mechanism else ""
    )
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


def _rebuild_references_from_claims(text: str, claims: list[dict[str, Any]]) -> str:
    """Replace the References section with the deduped set of claim URLs —
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
    nxt = re.compile(r"(?m)^##\s").search(text, m.end())
    refs_end = nxt.start() if nxt else len(text)
    urls = list(dict.fromkeys(c["url"] for c in claims if c.get("url")))
    entries = "\n".join(f"- {u}" for u in urls)
    rebuilt = f"{m.group(0)}\n\n{entries}\n"
    tail = text[refs_end:]
    return text[: m.start()] + rebuilt + ("\n" + tail.lstrip("\n") if tail.strip() else "")


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
    # GENERIC RULE (user): subjects[] and the FRAME relationship are outputs
    # every downstream producer consumes — for N>=2 subjects the skeleton
    # includes an integration/relationship section BY CONSTRUCTION, never
    # emergent (same principle as disambiguation and the relationship step).
    sections = _ensure_integration_section(sections, subjects)
    dbg(
        f"research_first FRAME: subjects={subjects} sections={sections} "
        f"code_needed={code_needed} diagram_needed={diagram_needed}"
    )
    emit("frame", {"subjects": subjects, "sections": sections, "code_needed": code_needed, "diagram_needed": diagram_needed})

    # 2. RESEARCH
    emit("research", {"subjects": subjects})
    ledger, assumptions, mechanism, verify_terms = _research(
        subjects, evidence_dir, requirement, judge_client, emit=emit
    )

    # 3. CLAIMS
    claims = _build_claims(ledger, subjects, client, ws_dir)
    emit("claims", {"sources": sum(len(v) for v in ledger.values()), "claims": len(claims)})

    # 4. WRITE
    emit("write", {"sections": [s for s in sections if s.lower() not in _SKIP_WRITE]})
    integration_home = _pick_integration_home(sections)
    # N>=2 subjects: the integration diagram lands in the dedicated integration
    # section (this round supersedes the earlier "design-architecture section"
    # placement); N=1 keeps the old design/architecture-section pick.
    multi = len(subjects) >= 2
    diagram_home = (integration_home if diagram_needed and multi else None) or (
        _pick_home(sections, _DIAGRAM_HOME_RE) if diagram_needed else None
    )
    code_home = _pick_home(sections, _CODE_HOME_RE) if code_needed and not multi else None
    scope_home = _pick_home(sections, _SCOPE_HOME_RE)
    written: dict[str, str] = {}
    for name in sections:
        if name.lower() in _SKIP_WRITE:
            continue
        text = _write_section(name, requirement, _claims_for_section(claims, name), client)
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
        if name == diagram_home:
            text = _splice_diagram(text, subjects, claims, client, mechanism, verify_terms)
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
                written[home] = _splice_subject_diagram(written[home], subject, claims, client)

    # Per-subject code examples + ONE integration example (user: "sample code
    # also needs to show the integration") — same iterate-subjects-then-
    # integration pattern as diagrams above. N=1 already got its code example
    # from the code_home/_splice_code path in the main loop; this is additive
    # for N>=2 only.
    code_subject_homes: dict[str, str] = {}
    if code_needed and multi:
        exclude = {integration_home} if integration_home else set()
        for subject in subjects:
            home = _pick_subject_home(written, subject, exclude)
            if home:
                code_subject_homes[subject] = home
                written[home] = _splice_subject_code(written[home], subject, claims, client)
        if integration_home:
            written[integration_home] = _splice_integration_code(
                written[integration_home], subjects, claims, mechanism, client
            )
    dbg(
        f"research_first WRITE: sections={len(written)} code_home={code_home!r} "
        f"diagram_home={diagram_home!r} subject_homes={subject_homes!r} "
        f"code_subject_homes={code_subject_homes!r} integration_home={integration_home!r}"
    )

    summary = _drop_ungrounded_sentences(
        _sanitize_section_headings(
            _write_summary(requirement, written, client, mechanism), sections
        ),
        claims,
    )

    # 5. ASSEMBLE
    text = _assemble(title, summary, sections, written)
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
    if any(s.lower() == "references" for s in sections):
        text = _rebuild_references_from_claims(text, claims)
    text, _ = _repair_fence_contamination(text)
    text, _ = _repair_doubled_citations(text)
    # SCOPE NOTE 1 (team-lead): the URL⊆claims invariant applied per-section
    # and to the summary during WRITE only sees each call's own claims slice
    # — a section fed zero claims (or a caption/label added after that pass
    # ran) would be missed. One final, fence-aware sweep over the WHOLE
    # assembled document closes that gap.
    text = _drop_ungrounded_sentences_artifact_wide(text, claims)
    lints = lint_artifact(text)
    dbg(
        f"research_first ASSEMBLE: words={len(text.split())} "
        f"fences={text.count('```')} mermaid={text.count('```mermaid')} lints={len(lints)}"
    )
    emit("assemble", {"words": len(text.split()), "lints": len(lints)})
    return text
