"""studio.research_first — research-first generation pipeline.

PLAN-codebase-simplification.md §9-§11: the existing seed-and-patch loop drafts
sections FIRST and treats research as patch material for whatever the seed already
says, so a task naming two subjects ("study Pi and Craft") can silently research
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
    _repair_doubled_citations,
    _repair_fence_contamination,
    dedupe_sections,
    rebuild_references_section,
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


# ---------------------------------------------------------------------------
# FRAME
# ---------------------------------------------------------------------------


def _extract_subjects(groups: list[list[str]]) -> list[str]:
    """Named subjects from "covers <subject>" branches (extraction item 5),
    order-preserving, deduped. Empty when the task names no specific subject."""
    subjects: list[str] = []
    for group in groups:
        for branch in group:
            if not _COVERS_SHAPED_RE.search(branch):
                continue
            subject = _COVERS_SHAPED_RE.sub("", branch, count=1).strip(" :.-")
            if subject and subject.lower() not in (s.lower() for s in subjects):
                subjects.append(subject)
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
    interpretation dominates the web (the mathematical constant "pi", the
    dictionary word "craft"), not the one THIS task means — the requirement's
    own domain words shape the discovery query instead. Returns
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


_ANCHOR_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


def _anchor_hits(content: str, anchors: list[str]) -> int:
    """Count resolved ANCHORS with >=1 constituent word present in *content* —
    the signal the generic topical floor can't give: a page can be dense in
    domain vocabulary yet share none of the specific anchors a disambiguation
    judge resolved for THIS subject. TOKEN-level, not exact-phrase: a judge
    invents a descriptive phrase ("API/MCP server connections") that a real
    README paraphrases rather than repeats verbatim — live failure: exact-phrase
    matching dropped the actual target repo (craft-agents-oss) over this."""
    low = content.lower()
    hits = 0
    for anchor in anchors:
        tokens = _ANCHOR_TOKEN_RE.findall(anchor.lower())
        if any(re.search(r"\b" + re.escape(t) + r"\b", low) for t in tokens):
            hits += 1
    return hits


def _research(
    subjects: list[str],
    evidence_dir: Path,
    requirement: str,
    judge_client: Any,
    *,
    emit: EmitFn,
) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    """Per subject: disambiguate FIRST (never search the bare subject name
    alone), then fetch up to ``_MAX_SOURCES_PER_SUBJECT`` distinct-domain,
    on-topic pages using the resolved descriptor+anchors. Plus 1-2 joint queries
    under the ``"__joint__"`` key for integration material. Returns
    ``(ledger, assumptions)`` — the resolved interpretation of each subject, for
    an honest Scope-section disclosure (P4/D3)."""
    from studio.textutil import content_word_stems

    emit = emit or (lambda *_a: None)
    domain_words = content_word_stems(_base_task_text(requirement))
    ledger: dict[str, list[dict[str, str]]] = {}
    resolved: dict[str, str] = {}
    assumptions: list[str] = []
    idx = 0
    for subject in subjects:
        descriptor, anchors = _disambiguate_subject(subject, domain_words, requirement, judge_client)
        resolved[subject] = descriptor
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
                # Offtopic floor judges against the CLEAN base requirement's
                # domain words (REBUILD-LESSONS §2), not the template-diluted one.
                if not content or _is_offtopic(url, domain_words, requirement, judge_client):
                    continue
                # The topical floor passes anything domain-adjacent (real failure:
                # craft.do's marketing copy is dense in "craft"/"agents" but names
                # none of the judge's resolved anchors) — a page matching ZERO
                # anchors is dropped even though it cleared the floor.
                if disambiguated and _anchor_hits(content, anchors) == 0:
                    dbg(f"research_first RESEARCH: anchor-mismatch drop url={url!r} subject={subject!r}")
                    continue
                seen_domains.add(domain)
                sources.append({"url": url, "content": content})
        ledger[subject] = sources
        dbg(f"research_first RESEARCH: subject={subject!r} queries={len(queries[:_MAX_QUERIES_PER_SUBJECT])} fetched={len(sources)}")

    if len(subjects) >= 2:
        d0, d1 = resolved[subjects[0]], resolved[subjects[1]]
        joint: list[dict[str, str]] = []
        joint_queries = [f"{d0} {d1} integration", f"{d0} and {d1} together"]
        for query in joint_queries[:_MAX_JOINT_QUERIES]:
            results = _search(query)
            emit("research_query", {"subject": "__joint__", "query": query, "n_results": len(results)})
            for r in results[:_MAX_SOURCES_PER_JOINT_QUERY]:
                url = str(getattr(r, "url", "") or "").strip()
                if not url:
                    continue
                idx += 1
                content = _fetch_and_store(url, evidence_dir, idx)
                if not content or _is_offtopic(url, domain_words, requirement, judge_client):
                    continue
                joint.append({"url": url, "content": content})
        ledger["__joint__"] = joint
        dbg(f"research_first RESEARCH: joint queries={len(joint_queries[:_MAX_JOINT_QUERIES])} fetched={len(joint)}")
    return ledger, assumptions


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
        if not norm_quote or norm_quote not in norm_content:
            continue  # unverifiable — dropped, never woven ungrounded
        sm = re.search(r"SUBJECTS:\s*(.+)", block)
        tags = [s.strip() for s in (sm.group(1).split(",") if sm else []) if s.strip()]
        # Source selection is gated by anchor/topic checks; per-claim SUBJECTS tags
        # were not — a single-subject loop vets a source against ONLY its own
        # anchors, so any tag other than exactly that subject is unvetted (live:
        # pypi/agent-framework, fetched under the "Pi" loop, tagged ['Pi', 'Craft']
        # — the spurious "Craft" was never checked against Craft's anchors at all).
        # A joint-fetched source (loop_subject=None) legitimately spans subjects.
        if loop_subject and tags != [loop_subject]:
            tags = [loop_subject]
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
        key = (c.get("subjects") or ["__untagged__"])[0]
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


def _write_section(name: str, requirement: str, claims: list[dict[str, Any]], client: Any) -> str:
    if client is None:
        return "_(no content — no LLM client available)_"
    ev_lines = "\n".join(
        f'- CLAIM: {c["claim"]} | QUOTE: "{c["quote"]}" | URL: {c["url"]}' for c in claims
    ) or "(no claims gathered for this section — say so honestly)"
    prompt = (
        f"You are writing the '{name}' section of a research report.\n\n"
        f"TASK: {requirement}\n\n"
        "Use ONLY the claims below as your factual basis — never invent a fact. "
        "Write 2-4 plain prose paragraphs (no headings). Every paragraph that "
        "states a fact must cite at least one claim's URL inline. If the claims "
        "are insufficient, say so honestly instead of fabricating.\n\n"
        f"CLAIMS:\n{ev_lines}\n\nOutput ONLY the section body prose, nothing else."
    )
    try:
        reply = client.chat([{"role": "user", "content": prompt}])
        text = _strip_unclosed_trailing_fence(str(getattr(reply, "text", "") or "").strip())
    except Exception as exc:  # noqa: BLE001 — a bad section call must never break the run
        dbg(f"research_first _write_section: call failed section={name!r} exc={exc!r}")
        text = ""
    return text or "_(no content — evidence gathering failed for this section)_"


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


def _fallback_subject_diagram(subjects: list[str]) -> str | None:
    """Deterministic 2+-node diagram built directly from the extracted subjects —
    guaranteed grounded (the nodes ARE the subjects) and guaranteed valid mermaid.
    Used only when the LLM-driven component list fails to ground (§10.1 WRITE)."""
    if len(subjects) < 2:
        return None
    from studio.diagram_render import _render

    edges = [(subjects[0], subjects[1], "integrates with")]
    return _render(subjects[:6], edges)


def _splice_diagram(
    section_text: str, subjects: list[str], claims: list[dict[str, Any]], client: Any
) -> str:
    from studio.diagram_render import build_diagram_block, render_grounded_diagram

    body: str | None = None
    joint = [c for c in claims if len(c.get("subjects") or []) >= 2] or claims
    if client is not None and joint and subjects:
        ev_lines = "\n".join(f"- {c['claim']} (URL: {c['url']})" for c in joint[:10])
        prompt = (
            "List the architecture/integration of the subjects below as plain "
            "lines — no prose, no markdown, no code fences:\n"
            "COMPONENT: <name> | <one-line role>\nEDGE: <name A> -> <name B> | <optional label>\n\n"
            f"Components MUST include both: {', '.join(subjects[:2])}. Draw at least "
            "one edge between them showing how they integrate.\n\n"
            f"EVIDENCE:\n{ev_lines}"
        )
        try:
            reply = client.chat([{"role": "user", "content": prompt}])
            raw = str(getattr(reply, "text", "") or "")
        except Exception as exc:  # noqa: BLE001 — a bad diagram call must never break the run
            dbg(f"research_first _splice_diagram: call failed exc={exc!r}")
            raw = ""
        if raw:
            body = render_grounded_diagram(raw, section_text + "\n" + " ".join(subjects))
    if not body:
        body = _fallback_subject_diagram(subjects)
    if not body:
        return section_text
    candidate = f"{section_text}\n\n{build_diagram_block(body)}"
    if any("mermaid" in w.lower() for w in lint_artifact(candidate)):
        return section_text  # never ship a diagram that fails lint
    return candidate


def _write_summary(requirement: str, written: dict[str, str], client: Any) -> str:
    """Written LAST from the already-drafted body (D4) — it can only summarize
    what exists, killing the overclaiming-summary failure by construction."""
    if client is None:
        return "_(summary unavailable)_"
    body = "\n\n".join(f"### {name}\n{text[:1200]}" for name, text in written.items())
    prompt = (
        "Write a 2-3 paragraph Executive Summary for the research report below. "
        "Summarize ONLY what the sections below actually say — never introduce a "
        "claim, number, or conclusion that is not already stated in them.\n\n"
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
    subjects = _extract_subjects(groups) or [title]
    code_needed = any(_CODE_SHAPED_RE.search(b) for g in groups for b in g)
    diagram_needed = any(_DIAGRAM_SHAPED_RE.search(b) for g in groups for b in g)
    sections = _build_sections(groups)
    dbg(
        f"research_first FRAME: subjects={subjects} sections={sections} "
        f"code_needed={code_needed} diagram_needed={diagram_needed}"
    )
    emit("frame", {"subjects": subjects, "sections": sections, "code_needed": code_needed, "diagram_needed": diagram_needed})

    # 2. RESEARCH
    ledger, assumptions = _research(subjects, evidence_dir, requirement, judge_client, emit=emit)

    # 3. CLAIMS
    claims = _build_claims(ledger, subjects, client, ws_dir)
    emit("research", {"sources": sum(len(v) for v in ledger.values()), "claims": len(claims)})

    # 4. WRITE
    code_home = _pick_home(sections, _CODE_HOME_RE) if code_needed else None
    diagram_home = _pick_home(sections, _DIAGRAM_HOME_RE) if diagram_needed else None
    scope_home = _pick_home(sections, _SCOPE_HOME_RE)
    written: dict[str, str] = {}
    for name in sections:
        if name.lower() in _SKIP_WRITE:
            continue
        text = _write_section(name, requirement, _claims_for_section(claims, name), client)
        if name == code_home:
            text = _splice_code(text, evidence_dir, client)
        if name == diagram_home:
            text = _splice_diagram(text, subjects, claims, client)
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
    dbg(f"research_first WRITE: sections={len(written)} code_home={code_home!r} diagram_home={diagram_home!r}")

    summary = _write_summary(requirement, written, client)

    # 5. ASSEMBLE
    text = _assemble(title, summary, sections, written)
    # A weak local model sometimes echoes the whole section skeleton inside one
    # section's own response ("gemma spokes re-emit the whole document" — see
    # dedupe_sections' docstring); collapse those duplicates before any other
    # repair runs so downstream lints see the real, single-copy structure.
    text = dedupe_sections(text)
    if any(s.lower() == "references" for s in sections):
        text = rebuild_references_section(text)
    text, _ = _repair_fence_contamination(text)
    text, _ = _repair_doubled_citations(text)
    lints = lint_artifact(text)
    dbg(
        f"research_first ASSEMBLE: words={len(text.split())} "
        f"fences={text.count('```')} mermaid={text.count('```mermaid')} lints={len(lints)}"
    )
    emit("assemble", {"words": len(text.split()), "lints": len(lints)})
    return text
