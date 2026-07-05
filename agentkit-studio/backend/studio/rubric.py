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
from dataclasses import dataclass
from typing import Iterable, Mapping

# S1: moved to studio.textutil.mask_fenced_code; re-exported here (name unchanged)
# so every existing `from studio.rubric import mask_fenced_code` keeps working.
from studio.textutil import mask_fenced_code

_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
_HEADING_RE = re.compile(r"(?m)^#{1,4}\s+\S")
_HEADING_TEXT_RE = re.compile(r"(?m)^#{1,4}\s+(.+)$")
_BLOCKQUOTE_RE = re.compile(r"(?m)^\s*>\s+\S")

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

SCORING_CATEGORIES = (
    "Scope and research framing",
    "ToC completeness",
    "Source quality",
    "Citation integrity",
    "Evidence synthesis",
    "Analytical depth",
    "Practical usefulness",
    "Code quality / examples",
    "Diagrams and tables",
    "Readability and formatting",
    "Reflection and limitations",
    "Governance and safety",
)

_CATEGORY_SIGNALS = {
    "Scope and research framing": "structure",
    "ToC completeness": "structure",
    "Source quality": "sourcing",
    "Citation integrity": "verification",
    "Evidence synthesis": "analysis",
    "Analytical depth": "analysis",
    "Practical usefulness": "analysis",
    "Code quality / examples": "structure",
    "Diagrams and tables": "structure",
    "Readability and formatting": "structure",
    "Reflection and limitations": "methodology",
    "Governance and safety": "methodology",
}

_DEEP_TECHNICAL_POINTS = {
    "Scope and research framing": 8,
    "ToC completeness": 7,
    "Source quality": 12,
    "Citation integrity": 12,
    "Evidence synthesis": 12,
    "Analytical depth": 10,
    "Practical usefulness": 10,
    "Code quality / examples": 8,
    "Diagrams and tables": 6,
    "Readability and formatting": 6,
    "Reflection and limitations": 5,
    "Governance and safety": 4,
}

_PROFILE_CATEGORY_POINTS: dict[str, dict[str, float]] = {
    "deep_technical": _DEEP_TECHNICAL_POINTS,
    "technical": _DEEP_TECHNICAL_POINTS,
    "general": {
        "Scope and research framing": 10,
        "ToC completeness": 10,
        "Source quality": 14,
        "Citation integrity": 14,
        "Evidence synthesis": 14,
        "Analytical depth": 10,
        "Practical usefulness": 8,
        "Code quality / examples": 0,
        "Diagrams and tables": 0,
        "Readability and formatting": 8,
        "Reflection and limitations": 7,
        "Governance and safety": 5,
    },
    "market": {
        "Scope and research framing": 10,
        "ToC completeness": 8,
        "Source quality": 14,
        "Citation integrity": 12,
        "Evidence synthesis": 14,
        "Analytical depth": 10,
        "Practical usefulness": 12,
        "Code quality / examples": 0,
        "Diagrams and tables": 4,
        "Readability and formatting": 6,
        "Reflection and limitations": 5,
        "Governance and safety": 5,
    },
    "policy": {
        "Scope and research framing": 10,
        "ToC completeness": 8,
        "Source quality": 12,
        "Citation integrity": 12,
        "Evidence synthesis": 12,
        "Analytical depth": 10,
        "Practical usefulness": 10,
        "Code quality / examples": 0,
        "Diagrams and tables": 0,
        "Readability and formatting": 6,
        "Reflection and limitations": 10,
        "Governance and safety": 10,
    },
    "literature_review": {
        "Scope and research framing": 10,
        "ToC completeness": 8,
        "Source quality": 16,
        "Citation integrity": 16,
        "Evidence synthesis": 14,
        "Analytical depth": 12,
        "Practical usefulness": 4,
        "Code quality / examples": 0,
        "Diagrams and tables": 0,
        "Readability and formatting": 6,
        "Reflection and limitations": 10,
        "Governance and safety": 4,
    },
    "academic": {
        "Scope and research framing": 10,
        "ToC completeness": 8,
        "Source quality": 16,
        "Citation integrity": 16,
        "Evidence synthesis": 14,
        "Analytical depth": 12,
        "Practical usefulness": 4,
        "Code quality / examples": 0,
        "Diagrams and tables": 0,
        "Readability and formatting": 6,
        "Reflection and limitations": 10,
        "Governance and safety": 4,
    },
    "competitive": {
        "Scope and research framing": 10,
        "ToC completeness": 8,
        "Source quality": 12,
        "Citation integrity": 12,
        "Evidence synthesis": 12,
        "Analytical depth": 10,
        "Practical usefulness": 12,
        "Code quality / examples": 0,
        "Diagrams and tables": 6,
        "Readability and formatting": 6,
        "Reflection and limitations": 6,
        "Governance and safety": 6,
    },
    "product": {
        "Scope and research framing": 10,
        "ToC completeness": 8,
        "Source quality": 12,
        "Citation integrity": 12,
        "Evidence synthesis": 12,
        "Analytical depth": 10,
        "Practical usefulness": 14,
        "Code quality / examples": 0,
        "Diagrams and tables": 4,
        "Readability and formatting": 6,
        "Reflection and limitations": 6,
        "Governance and safety": 6,
    },
}


@dataclass(frozen=True)
class ScoreProfile:
    report_type: str
    scoring_matrix: tuple[dict[str, object], ...]


_CATEGORY_TEMPLATE_TERMS = {
    "Scope and research framing": ("scope", "question", "context", "background", "summary", "abstract"),
    "ToC completeness": (),
    "Source quality": ("source", "reference", "evidence", "bibliography", "citation"),
    "Citation integrity": ("reference", "citation", "source", "url", "evidence"),
    "Evidence synthesis": ("evidence", "finding", "analysis", "theme", "insight"),
    "Analytical depth": ("analysis", "tradeoff", "implication", "comparison", "evaluation", "risk"),
    "Practical usefulness": ("recommendation", "roadmap", "implementation", "option", "blueprint", "action"),
    "Code quality / examples": ("code", "example", "implementation", "walkthrough", "listing"),
    "Diagrams and tables": ("diagram", "table", "matrix", "scorecard", "landscape", "comparison"),
    "Readability and formatting": (),
    "Reflection and limitations": ("limitation", "uncertainty", "reflection", "open question", "future"),
    "Governance and safety": ("governance", "safety", "security", "privacy", "legal", "regulatory", "equity", "cost"),
}
_TARGET_SOURCES = 8          # DEER "inclusion of requested items": reward up to N sources
_TARGET_QUOTES = 8           # direct-evidence density target
_TARGET_WORDS = 1500         # body-depth floor (a stub must not score full marks)
_TARGET_ANALYSIS = 6         # analytical/comparative discourse markers for full analysis credit
_TARGET_PRACTICAL = 6        # action/risk markers for full practical-usefulness credit

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

_PRACTICAL_MARKERS = re.compile(
    r"(?i)\b("
    r"recommend(?:ation|ed|s)?|next step|action item|roadmap|implementation|implement"
    r"|mitigat(?:e|ion)|risk|trade[- ]?off|owner|timeline|priority|checklist"
    r"|roll(?:out|back)|pilot|measure|metric|budget|cost|dependency|constraint"
    r")\b"
)

_CATEGORY_WEAKNESS_HINTS = {
    "Practical usefulness": (
        "add concrete implementation risks, mitigations, sequencing, and next actions"
    ),
}


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


def _category_matches_template(category: str, template: Iterable[str] | None) -> bool:
    terms = _CATEGORY_TEMPLATE_TERMS[category]
    if not terms:
        return True
    text = " ".join(str(s).lower() for s in (template or []) if str(s).strip())
    return not text or any(term in text for term in terms)


def _category_applies_to_section(category: str, section: str) -> bool:
    terms = _CATEGORY_TEMPLATE_TERMS[category]
    if not terms:
        return True
    text = str(section or "").lower()
    return any(term in text for term in terms)


def _scale_points(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    total = sum(float(row["points"]) for row in rows)
    if total <= 0:
        return rows
    return [{**row, "points": float(row["points"]) * 100.0 / total} for row in rows]


def default_scoring_matrix(
    report_type: str | None = None,
    template: Iterable[str] | None = None,
) -> list[dict[str, object]]:
    """Frozen run-start scoring categories.

    Profiles share one category vocabulary; only weights vary. Each category maps to an
    existing deterministic rubric signal so this stays reproducible and small.
    """
    key = (report_type or "general").strip().lower().replace("-", "_")
    weights = _PROFILE_CATEGORY_POINTS.get(key, _PROFILE_CATEGORY_POINTS["general"])
    rows = []
    for category in SCORING_CATEGORIES:
        points = float(weights.get(category, 0.0))
        if points <= 0 or not _category_matches_template(category, template):
            continue
        rows.append({
            "category": category,
            "points": points,
            "applicable": True,
            "signal": _CATEGORY_SIGNALS[category],
        })
    return _scale_points(rows)


def scoring_rules_for_sections(
    scoring_matrix: Iterable[Mapping[str, object]] | None,
    sections: Iterable[str],
) -> list[dict[str, object]]:
    """Subset scoring rules for a section worker, keeping universal rules everywhere."""
    section_list = [str(s) for s in sections if str(s).strip()]
    rules = resolve_scoring_matrix(scoring_matrix)
    return [
        dict(rule)
        for rule in rules
        if any(_category_applies_to_section(str(rule["category"]), section) for section in section_list)
    ]


def format_scoring_rules(
    scoring_matrix: Iterable[Mapping[str, object]] | None,
    *,
    sections: Iterable[str] | None = None,
) -> str:
    """Compact prompt block for scoring rules."""
    rules = (
        scoring_rules_for_sections(scoring_matrix, sections)
        if sections is not None
        else [dict(rule) for rule in resolve_scoring_matrix(scoring_matrix)]
    )
    if not rules:
        return "- (none for this assigned section)"
    return "\n".join(
        f"- {rule['category']} ({float(rule['points']):.1f} pts): satisfy via {rule['signal']}"
        for rule in rules
    )


def resolve_score_profile(
    report_type: str | None = None,
    template: Iterable[str] | None = None,
) -> ScoreProfile:
    """Return the frozen score profile selected by report type."""
    key = (report_type or "general").strip().lower().replace("-", "_")
    return ScoreProfile(key, tuple(default_scoring_matrix(key, template)))


def resolve_scoring_matrix(
    matrix: Iterable[Mapping[str, object]] | None = None,
    template: Iterable[str] | None = None,
) -> list[dict[str, object]]:
    """Normalize user/API scoring-matrix payloads to the shared 12-category vocabulary."""
    if matrix is None:
        return default_scoring_matrix(template=template)
    rows: list[dict[str, object]] = []
    for row in matrix or []:
        category = str(row.get("category") or "").strip()
        if category not in SCORING_CATEGORIES or not _category_matches_template(category, template):
            continue
        signal = str(row.get("signal") or _CATEGORY_SIGNALS[category]).strip()
        if signal not in _WEIGHTS:
            signal = _CATEGORY_SIGNALS[category]
        try:
            points = float(row.get("points", row.get("weight", 0.0)))
        except (TypeError, ValueError):
            points = 0.0
        if points <= 0:
            continue
        rows.append({
            "category": category,
            "points": max(0.0, points),
            "applicable": True,
            "signal": signal,
        })
    return _scale_points(rows)


def rubric_score(
    text: str,
    verified_urls: Iterable[str] | None = None,
    weights: dict[str, float] | None = None,
    required_sections: Iterable[str] | None = None,
    *,
    relevance_penalty: float = 0.0,
    compliance_penalty: float = 0.0,
) -> float:
    """Weighted research-report quality score in [0,1] (deterministic).

    Reproducible and model-free — the same report always yields the same number, which is
    exactly what the noisy/changing LLM scorer and the gameable solved/total metric were
    not. ``weights`` is the GUI-tunable per-criterion weighting and ``required_sections``
    the GUI-supplied deliverable TEMPLATE (both from the session ``rubric_config``); omitted
    → defaults. Use as the scoring standard and as the epoch keep/discard signal.

    ``relevance_penalty`` is an optional precomputed [0,1] fraction — the share of a
    seeded artifact's sections a binary LLM classification (``studio.relevance``, run
    ONCE per epoch upstream) flagged as off-topic for THIS task, e.g. cross-task R10
    seed contamination. Subtracted from the weighted base score, clamped to [0,1].
    Defaults to 0.0 (no penalty) so existing callers without this context are
    unaffected. This function itself stays pure/deterministic — it makes NO network
    call; the classification happens once, upstream, by whoever already holds a live
    LLM client (rubric_score is called many times per epoch — see runner.py's
    ``_editor_scored_issues`` call sites — so it must never trigger one itself).

    ``compliance_penalty`` is the analogous precomputed [0,1] fraction from
    ``studio.requirement_compliance`` — the share of the task's EXPLICIT stated
    requirements the artifact fails to satisfy. Same subtract-and-clamp
    treatment as ``relevance_penalty`` (the two stack); defaults to 0.0.
    """
    parts = score_breakdown(text, verified_urls, required_sections)
    w = resolve_weights(weights)
    base = sum(w[k] * parts[k] for k in w)
    penalty = max(0.0, relevance_penalty) + max(0.0, compliance_penalty)
    return round(_clamp01(base - penalty), 4)


def rubric_scorecard_100(
    text: str,
    verified_urls: Iterable[str] | None = None,
    *,
    required_sections: Iterable[str] | None = None,
    scoring_matrix: Iterable[Mapping[str, object]] | None = None,
    weights: dict[str, float] | None = None,
    relevance_penalty: float = 0.0,
    compliance_penalty: float = 0.0,
) -> dict[str, object]:
    """Unified score surface: original deterministic signals plus scoring matrix.

    The original rubric remains the calibrated optimization signal; this scorecard
    exposes the same base signals through the frozen profile/template matrix.

    ``relevance_penalty`` / ``compliance_penalty`` — see ``rubric_score``; both also
    derate ``total`` by the same fraction of ``max_points`` so the 100-point
    scorecard stays consistent with the base score.
    """
    parts = score_breakdown(text, verified_urls, required_sections)
    base_score = rubric_score(
        text, verified_urls, weights, required_sections,
        relevance_penalty=relevance_penalty,
        compliance_penalty=compliance_penalty,
    )
    matrix = resolve_scoring_matrix(scoring_matrix)
    rows: list[dict[str, object]] = []
    total = 0.0
    max_points = 0.0
    for row in matrix:
        points = float(row["points"])
        signal = str(row["signal"])
        signal_score = _category_signal_score(str(row["category"]), signal, parts, text)
        earned = round(points * signal_score, 2)
        total += earned
        max_points += points
        rows.append({**row, "score": earned, "signal_score": signal_score})
    total = max(0.0, total - (max(0.0, relevance_penalty) + max(0.0, compliance_penalty)) * max_points)
    return {
        "score": round(total, 2),
        "max_score": round(max_points, 2),
        "base_score": base_score,
        "signals": parts,
        "categories": rows,
    }


def _category_signal_score(
    category: str,
    signal: str,
    parts: Mapping[str, float],
    text: str,
) -> float:
    if category == "Practical usefulness":
        return _clamp01(len(_PRACTICAL_MARKERS.findall(text or "")) / _TARGET_PRACTICAL)
    return float(parts[signal])


def scorecard_weaknesses(
    scorecard: Mapping[str, object],
    scoring_template: Iterable[str] | None = None,
    *,
    min_signal_score: float = 0.75,
    limit: int = 6,
) -> list[str]:
    """Turn weak scorecard rows into section-routable weaknesses for the next run."""
    weaknesses: list[str] = []
    sections = [str(s) for s in (scoring_template or []) if str(s).strip()]
    rows = scorecard.get("categories", []) if isinstance(scorecard, Mapping) else []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            signal_score = float(row.get("signal_score", 1.0))
        except (TypeError, ValueError):
            continue
        if signal_score >= min_signal_score:
            continue
        category = str(row.get("category") or "").strip()
        if not category:
            continue
        try:
            score = float(row.get("score", 0.0))
            points = float(row.get("points", 0.0))
        except (TypeError, ValueError):
            score, points = 0.0, 0.0
        owners = [
            section for section in sections
            if _category_applies_to_section(category, section)
        ] or ["document"]
        for owner in owners:
            prefix = "[document]" if owner == "document" else f"[## {owner.removeprefix('## ').strip()}]"
            hint = _CATEGORY_WEAKNESS_HINTS.get(category, "improve the section against this rule")
            weaknesses.append(
                f"{prefix} Scoring gap: {category} scored {score:.1f}/{points:.1f}; "
                f"{hint}."
            )
            if len(weaknesses) >= limit:
                return weaknesses
    return weaknesses


def remaining_scoring_matrix(
    scorecard: Mapping[str, object],
    *,
    min_signal_score: float = 0.75,
) -> list[dict[str, object]]:
    """Return unachieved scorecard rows for the next phase's prompt budget."""
    rows = scorecard.get("categories", []) if isinstance(scorecard, Mapping) else []
    remaining: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            signal_score = float(row.get("signal_score", 1.0))
        except (TypeError, ValueError):
            continue
        if signal_score >= min_signal_score:
            continue
        remaining.append({
            "category": str(row.get("category") or ""),
            "points": float(row.get("points") or 0.0),
            "applicable": True,
            "signal": str(row.get("signal") or _CATEGORY_SIGNALS.get(str(row.get("category") or ""), "structure")),
        })
    return [row for row in remaining if row["category"] in SCORING_CATEGORIES and row["points"] > 0]


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
