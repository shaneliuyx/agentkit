"""studio.rubric — research-report evaluation rubric (DESIGN §14.2).

WHY this exists: live testing (DESIGN §14.1 D4) showed an LLM "which report is better?"
judge is unreliable for large reports — haiku AND sonnet both tied a 58 KB sourced
report with a 4.5 KB stub. The loop-engineering literature says the same: "LLM-as-judge
can be gamed or collude; put a DETERMINISTIC check in the cycle wherever one exists."

So the scoring standard is a STRUCTURED, mostly-deterministic rubric, synthesized from
established research-report evaluation frameworks:
  * DEER (arXiv:2512.17776) & DeepResearch-Bench (arXiv:2506.11763): deep-research report
    quality = completeness / correctness / helpfulness, scored per concrete criterion.
  * CRAAP test (Currency, Relevance, Authority, Accuracy, Purpose) — source credibility.
  * Academic report rubrics: sourcing/citation, evidence depth, methodology transparency,
    structure, directly answering the task.

The SAME rubric is dual-use:
  1. TEMPLATE — what a good report must contain (feeds worker constraints / planner).
  2. SCORING STANDARD — `rubric_score()` in [0,1], deterministic and reproducible.
  3. GATE SIGNAL — `epoch_gate` compares rubric_score(new) vs rubric_score(prior).

Deterministic by design: every signal is computed from the text (and the optional
verified-URL oracle from the web cache), so the same report always scores the same — no
model call, no hedging, no version drift. LLM-per-criterion scoring can be layered on
later for the subjective dimensions (correctness/helpfulness); kept out here on purpose.
"""
from __future__ import annotations

import re
from typing import Iterable

_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
_HEADING_RE = re.compile(r"(?m)^#{1,4}\s+\S")
_HEADING_TEXT_RE = re.compile(r"(?m)^#{1,4}\s+(.+)$")
_BLOCKQUOTE_RE = re.compile(r"(?m)^\s*>\s+\S")
_FENCE_RE = re.compile(r"^\s*```")


def mask_fenced_code(text: str) -> str:
    """Blank out fenced code regions, preserving line count (PLAN N4).

    Heading scans (``^#{1,6}``) otherwise count a python comment like
    ``# --- MOCK ---`` inside a ```` ```python ```` block as an H1, polluting the
    structure signal, ``sections_present``, and gap detection. Replacing each
    fenced line (fences included) with an empty line keeps every real heading's
    line position intact while hiding in-code ``#`` lines. An unterminated fence
    masks to end-of-text (a truncated block has no real headings anyway).
    """
    if "```" not in (text or ""):
        return text or ""
    out: list[str] = []
    in_fence = False
    for ln in (text or "").split("\n"):
        if _FENCE_RE.match(ln):
            in_fence = not in_fence
            out.append("")          # the fence line itself is not a heading
            continue
        out.append("" if in_fence else ln)
    return "\n".join(out)

#: Stopwords dropped when reducing a section name / heading to its content tokens, so
#: "Evidence and Analysis" → {evidence, analysi}. Keeps the head nouns that carry meaning.
_SECTION_STOP = {"and", "or", "the", "a", "an", "of", "to", "for", "in", "on", "with", "by"}


def _content_tokens(s: str) -> set[str]:
    """Significant words of a heading / section name, singular-normalized (a trailing 's'
    stripped). Used for concept-level section matching: a required section is "covered"
    when a real heading shares a content word, so synonym headings ("Verified Sources" for
    "Source References", "Core Finding" for "Key Findings") are not penalised as missing."""
    return {
        w.rstrip("s")
        for w in re.findall(r"[a-z]+", s.lower())
        if w not in _SECTION_STOP and len(w) > 2
    }

#: Criterion weights (sum = 1.0). Sourcing/verification dominate — the report's own
#: thesis is that VERIFIED sourcing is the bottleneck, and it's the most gameable.
# PLAN item 1B: the loop was climbing to a copy-paste local optimum because the rubric
# rewarded quote DENSITY (evidence_depth) and had no analysis criterion at all. Add an
# `analysis` criterion and DOWN-WEIGHT raw quote density (0.20 → 0.12), so the loop climbs
# toward synthesis/interpretation, not pasting. (The LLM-judged version of this signal —
# G-Eval/RAGAS — is PLAN item 2, deliberately not built here; this is the deterministic
# proxy that keeps the rubric reproducible and model-free.)
_WEIGHTS = {
    "sourcing": 0.22,        # CRAAP authority: distinct cited sources
    "verification": 0.22,    # CRAAP accuracy: sources actually verified (cache oracle)
    "evidence_depth": 0.12,  # correctness/helpfulness: direct quotes / concrete evidence
    "analysis": 0.18,        # synthesis: interpretation + cross-source comparison (item 1B)
    "structure": 0.14,       # completeness: summary + sections + conclusion
    "methodology": 0.12,     # completeness: methodology/scope transparency + non-thin body
}
_TARGET_SOURCES = 8          # DEER "inclusion of requested items": reward up to N sources
_TARGET_QUOTES = 8           # direct-evidence density target
_TARGET_WORDS = 1500         # body-depth floor (a stub must not score full marks)
_TARGET_ANALYSIS = 6         # analytical/comparative discourse markers for full analysis credit

#: Discourse markers of analysis & cross-source comparison — interpretation rather than
#: quotation. Their density is the deterministic `analysis` signal (PLAN item 1B): a report
#: that only pastes sources has few; one that synthesizes ("in contrast", "this implies",
#: "compared to", "the trade-off") has many.
_ANALYSIS_MARKERS = re.compile(
    r"(?i)\b("
    r"however|therefore|thus|hence|whereas|nonetheless|consequently|as a result"
    r"|in contrast|on the other hand|by comparison|compared (?:to|with)|relative to|unlike"
    r"|this (?:suggests|implies|indicates|means|shows)|which (?:suggests|implies|indicates)"
    r"|trade[- ]?off|the (?:key |main )?(?:implication|takeaway|insight)"
    r"|more (?:effective|reliable|robust|mature|popular) than|differ(?:s|ent|ence)?"
    r")\b"
)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def score_breakdown(
    text: str,
    verified_urls: Iterable[str] | None = None,
    required_sections: Iterable[str] | None = None,
) -> dict[str, float]:
    """Per-criterion sub-scores in [0,1] for ``text``. All deterministic.

    ``verified_urls`` (from studio.task_runs.verified_urls_in_cache) is the accuracy
    oracle: URLs confirmed real via the web cache. When absent, verification falls back
    to in-text "verified/fetched" markers so the signal degrades instead of vanishing.

    ``required_sections`` is the DELIVERABLE TEMPLATE the rubric is attached to (a list of
    expected section headings, GUI-supplied). When given, the structure score becomes the
    fraction of template sections actually present — so "good" means "matches the agreed
    deliverable shape", not a generic heuristic. When absent, falls back to
    summary+conclusion+heading-density.
    """
    t = text or ""
    # N4: hide fenced-code regions from every heading/structure scan so an in-code
    # `# comment` is not mistaken for a markdown heading. Body-text checks (URLs,
    # quotes, words, analysis markers) still run over the raw text.
    tm = mask_fenced_code(t)
    tml = tm.lower()
    urls = list(dict.fromkeys(_URL_RE.findall(t)))           # distinct, order-preserving
    n_urls = len(urls)
    verified = set(verified_urls or [])
    if verified:
        n_verified = sum(1 for u in urls if u.rstrip(".,)") in verified)
    else:                                                     # heuristic fallback
        n_verified = min(n_urls, len(re.findall(r"(?i)verif|fetched", t)))

    headings = _HEADING_RE.findall(tm)
    has_summary = bool(re.search(r"(?i)executive summary|abstract", tm))
    has_conclusion = bool(re.search(r"(?im)^#+\s*conclusion", tm))
    has_method = bool(re.search(r"(?i)methodolog|scope|limitation", tm))
    quotes = len(_BLOCKQUOTE_RE.findall(t)) + t.count('"') // 2
    words = len(t.split())
    n_analysis = len(_ANALYSIS_MARKERS.findall(t))

    req = [s.strip() for s in (required_sections or []) if s and s.strip()]
    if req:                                                   # template-coverage structure
        # Concept-level coverage (DESIGN §14.2 calibration): a required section counts as
        # present if the exact phrase appears OR some heading shares a content word with it.
        # Exact substring alone scored a genuinely good report 0.5 because its headings used
        # real-world synonyms ("Verified Sources" for "Source References", "Core Finding" for
        # "Key Findings") — penalising vocabulary, not a missing section.
        heading_toks: set[str] = set()
        for _h in _HEADING_TEXT_RE.findall(t):
            heading_toks |= _content_tokens(_h)
        structure = sum(
            1 for s in req if s.lower() in tml or (_content_tokens(s) & heading_toks)
        ) / len(req)
    else:
        structure = (
            (1.0 if has_summary else 0.0)
            + (1.0 if has_conclusion else 0.0)
            + _clamp01(len(headings) / 6.0)
        ) / 3.0

    return {
        "sourcing": _clamp01(n_urls / _TARGET_SOURCES),
        "verification": _clamp01(n_verified / _TARGET_SOURCES),
        "evidence_depth": _clamp01(quotes / _TARGET_QUOTES),
        "analysis": _clamp01(n_analysis / _TARGET_ANALYSIS),
        "structure": structure,
        "methodology": (1.0 if has_method else 0.0) * 0.5 + _clamp01(words / _TARGET_WORDS) * 0.5,
    }


def sections_present(text: str, required_sections: Iterable[str] | None) -> list[str]:
    """Subset of ``required_sections`` actually present in ``text`` — concept-aware and over
    the FULL text (no window). A section counts as present if its exact phrase appears OR a
    heading shares a content word with it (same matcher as the `structure` score).

    Used to override the windowed LLM scorer/miner: those see only a 20K/6K slice, so they
    falsely report tail sections (e.g. Methodology/Conclusion at char 54K of a 64K report) as
    "missing". This deterministic full-text check is the source of truth for section presence.
    """
    req = [s.strip() for s in (required_sections or []) if s and s.strip()]
    if not req:
        return []
    tm = mask_fenced_code(text or "")           # N4: ignore in-code `#` lines
    tl = tm.lower()
    heading_toks: set[str] = set()
    for _h in _HEADING_TEXT_RE.findall(tm):
        heading_toks |= _content_tokens(_h)
    return [s for s in req if s.lower() in tl or (_content_tokens(s) & heading_toks)]


def resolve_weights(weights: dict[str, float] | None) -> dict[str, float]:
    """Merge a GUI-supplied partial weight override onto the defaults, keep only known
    criteria, and L1-normalize so the score stays in [0,1]. An empty/None override → the
    built-in defaults. Unknown keys are ignored (forward/backward compatible)."""
    merged = dict(_WEIGHTS)
    for k, v in (weights or {}).items():
        if k in merged:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv >= 0:
                merged[k] = fv
    total = sum(merged.values()) or 1.0
    return {k: v / total for k, v in merged.items()}


def rubric_score(
    text: str,
    verified_urls: Iterable[str] | None = None,
    weights: dict[str, float] | None = None,
    required_sections: Iterable[str] | None = None,
) -> float:
    """Weighted research-report quality score in [0,1] (deterministic).

    Reproducible and model-free — the same report always yields the same number, which is
    exactly what the noisy/changing LLM scorer and the gameable solved/total metric were
    not. ``weights`` is the GUI-tunable per-criterion weighting and ``required_sections``
    the GUI-supplied deliverable TEMPLATE (both from the session ``rubric_config``); omitted
    → defaults. Use as the scoring standard and as the epoch keep/discard signal.
    """
    w = resolve_weights(weights)
    parts = score_breakdown(text, verified_urls, required_sections)
    return round(sum(w[k] * parts[k] for k in w), 4)


#: Weakness-penalty calibration (DESIGN §14.7). The rubric measures STRUCTURE/QUANTITY
#: and is blind to correctness — it returned 1.0 on a report with a malformed mermaid,
#: fabricated URLs, and no inline citations. A perfect score alongside open weaknesses is
#: incoherent, so the recorded score is penalised by the unaddressed weaknesses.
_PER_WEAKNESS = 0.04     # graduated cost per remaining weakness
_PER_HARD = 0.06         # extra cost for an OBJECTIVE defect (deterministic lint)
_MAX_PENALTY = 0.6       # floor: even a weak report keeps some structural credit
_CEIL_WITH_WEAKNESS = 0.92   # hard ceiling — ANY open weakness ⇒ not a perfect score


def _is_hard_defect(weakness: str) -> bool:
    """A deterministic, objective defect (not an LLM opinion) — weighed double."""
    wl = weakness.lower()
    return (
        "malformed mermaid" in wl
        or "unbalanced code fence" in wl
        or "do not match verified" in wl   # fabricated / uncached citations
        or "does not match verified" in wl
    )


def adjusted_score(base: float, weaknesses: Iterable[str] | None) -> float:
    """Couple the deterministic rubric to remaining weaknesses (DESIGN §14.7).

    Invariant (user requirement): if ANY weakness remains, the score is strictly below
    1.0 — a report with open defects is never "perfect". On top of that hard ceiling a
    graduated, capped penalty makes the score track the count and severity of what is
    still wrong, so an improving doc with fewer weaknesses scores higher even when the
    structural rubric has already saturated. Deterministic lints (malformed mermaid,
    unbalanced fence, fabricated citations) are objective, so they cost extra. With no
    weaknesses the rubric is returned unchanged.
    """
    ws = [w for w in (weaknesses or []) if w and w.strip()]
    if not ws:
        return round(base, 4)
    hard = sum(1 for w in ws if _is_hard_defect(w))
    penalty = min(_MAX_PENALTY, _PER_WEAKNESS * len(ws) + _PER_HARD * hard)
    return round(min(base * (1.0 - penalty), _CEIL_WITH_WEAKNESS), 4)


#: Default criterion weights exposed for the GUI rubric panel (so the UI can render the
#: same defaults the scorer uses). Treat as read-only.
DEFAULT_WEIGHTS = dict(_WEIGHTS)

#: Default deliverable TEMPLATE for a research report — the section skeleton a good report
#: should contain (synthesized from DEER/DeepResearch-Bench + academic report structure).
#: The GUI seeds its editable template field from this; the loop uses it BOTH to steer
#: generation (expected sections) and to score the `structure` criterion (coverage).
# High-level, TOPIC-AGNOSTIC research-report table of contents. Generic on purpose:
# these sections fit any subject so the report's topic is carried by its CONTENT, not
# named by the template (the loop-eng drift came from reusing a topic-specific skeleton).
# Mirrors standard research/technical-report structure (exec summary → background →
# findings → analysis → methodology → limitations → conclusion → references) and covers
# every rubric criterion (summary, findings, evidence, sourcing, methodology, conclusion).
from studio.report_profiles import GENERIC_RESEARCH_PROFILE

DEFAULT_TEMPLATE = list(GENERIC_RESEARCH_PROFILE.sections)
