"""studio.task_runs — cross-session task improvement history.

Records each run's score and weaknesses so subsequent sessions can edit-in-place
rather than regenerating from scratch. Backed by a stable SQLite DB at the
workspace root (never a tmpdir).

Three entry points for the runner:
  task_hash(req)                    → stable 12-char key
  TaskRunStore.latest(hash)         → prior run for a task (seed auto-improve)
  TaskRunStore.record(run)          → write score + weaknesses after a run
  score_result(result, req, client) → LLM 0-1 scorer
  mine_weaknesses_from_outputs(…)   → LLM weakness list from plain text outputs
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import sqlite3
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio.workspace import workspace_root


def _db_path() -> Path:
    root = workspace_root()
    path = root.parent / "task_runs.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def task_hash(requirement: str) -> str:
    """Stable 12-char key derived from the requirement text."""
    return hashlib.sha256(requirement.strip().lower().encode()).hexdigest()[:12]


def base_identity(requirement: str) -> str:
    """Stable lineage identity for a (possibly conversational) requirement (PLAN item 5).

    The GUI "Continue run" wraps the task in a conversational blob
    ("Original task: X\\n\\nContext from previous run: …\\n\\nFollow-up: Y") and the chat
    path prepends flattened turns then "[CURRENT REQUEST]: Z". Hashing those rotates
    ``task_hash`` on every continuation, so the re-run COLD-STARTS instead of seeding the
    prior artifact (no carry-forward, repairs never fire). Extract the ORIGINAL task so a
    continuation shares the first run's ``task_hash`` and actually continues the lineage.

    ponytail: this couples to the two GUI continuation formats. If those markers change,
    the robust upgrade is to thread an explicit ``base_requirement`` param end-to-end
    (frontend → /run → runner) instead of recovering it by string-matching here.
    """
    r = requirement or ""
    # Chat path: the bare current request follows the LAST "[CURRENT REQUEST]:" marker.
    m = re.search(r"\[CURRENT REQUEST\]:\s*(.+)\Z", r, re.DOTALL)
    if m:
        return m.group(1).strip()
    # "Continue run" blob: the original task follows "Original task:" up to a blank line.
    m = re.search(r"(?is)^\s*Original task:\s*(.+?)(?:\n\s*\n|\Z)", r)
    if m:
        return m.group(1).strip()
    return r.strip()


#: Query params that identify a session/campaign, not the resource — dropped when
#: canonicalizing a URL for verification matching so a tracked link still matches its
#: clean cached form (PLAN item 7).
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "source",
})
_URL_RE = re.compile(r"https?://\S+")

#: Matches a citation URL together with any enclosing wrapper so the WHOLE thing can be
#: collapsed — otherwise neutralizing only the bare URL leaves orphaned brackets/parens
#: like ``[(unverified))`` or ``((unverified))``. Alternatives are ordered widest-first:
#: paren-wrapped markdown link, plain markdown link, bare parenthesized URL, then a bare
#: URL (the original behavior). Inner URLs use ``[^)\s]+`` so the closing wrapper is not
#: swallowed; the final bare branch keeps ``\S+`` to preserve trailing-punctuation handling.
_CITATION_RE = re.compile(
    r"\(\[(?P<ptext>[^\]]*)\]\((?P<plurl>https?://[^)\s]+)\)\)"   # ([text](url))
    r"|\[(?P<ltext>[^\]]*)\]\((?P<lurl>https?://[^)\s]+)\)"        # [text](url)
    r"|\((?P<purl>https?://[^)\s]+)\)"                             # (url)
    r"|(?P<burl>https?://\S+)"                                     # bare url
)

#: Heals ALREADY-GARBLED markers left behind by the pre-fix orphaned-bracket bug
#: (entry 173) — e.g. ``([(unverified))``, ``[(unverified))``, ``((unverified))``.
#: Once that bug ran, the original URL was destroyed, so `_CITATION_RE` (which
#: matches on a live URL) can never find and re-collapse these — the garbling is
#: permanent scar tissue in already-generated prose, most visibly in artifacts
#: carried forward via auto_improve from BEFORE the fix. This is pure text
#: repair, independent of any verified-URL set, so it heals text even when
#: verification is unavailable (unlike `_CITATION_RE`'s fail-open gate below,
#: which is about not wrongly stripping a citation that simply couldn't be
#: checked — not about leaving known-garbage byte sequences on the page).
#: 1-2 leading `(`/`[` chars, the literal placeholder, then 1+ trailing `)` —
#: a bare, already-clean ``(unverified)`` has neither, so it never matches.
_GARBLED_UNVERIFIED_RE = re.compile(r"[(\[]{1,2}\(unverified\)\)+")


def _normalize_url(u: str) -> str:
    """Canonicalize a URL for set-membership matching (PLAN item 7).

    Forces scheme to ``https``, lowercases the host, drops a trailing slash, strips
    tracking query params, and removes the fragment — so ``http`` vs ``https``, a trailing
    slash, or a ``utm_*`` tag no longer makes a REAL citation look unverified (the
    false-unverified bug). Returns a lowercase fallback when the input is not parseable.
    """
    s = (u or "").strip().rstrip(".,)\"'>")
    try:
        p = urllib.parse.urlsplit(s)
    except ValueError:
        return s.lower()
    if not p.netloc:                       # not an absolute URL — nothing to canonicalize
        return s.lower()
    path = p.path.rstrip("/")
    kept = [
        (k, v) for k, v in urllib.parse.parse_qsl(p.query)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = urllib.parse.urlencode(kept)
    return urllib.parse.urlunsplit(("https", p.netloc.lower(), path, query, ""))


def neutralize_unverified_urls(
    text: str, verified_urls: list[str] | None, *, placeholder: str = "(unverified)"
) -> str:
    """Replace any cited URL NOT in the verified set with ``placeholder`` (PLAN item 3).

    The reducer can invent plausible URLs from real domains; the cache check DETECTS them
    but nothing strips them. This does, so the cleaned doc is what gets scored/served.

    FAIL-OPEN (critical): an empty/None verified set means verification was UNAVAILABLE
    (search down, cache unreadable) — not that every citation is fake. In that case nothing
    is changed, so a transient outage never blanks out every real citation. URLs are
    normalized (scheme/slash/tracking) before matching so a genuine link is not neutralized
    over a cosmetic format difference.

    Healing already-garbled markers (``_GARBLED_UNVERIFIED_RE``) runs FIRST and
    unconditionally — it repairs known-garbage byte sequences left by the
    pre-fix bug, not citations, so it is not gated by the fail-open check below
    (a text with no such marker is returned byte-identical either way).
    """
    if not text:
        return text or ""
    text = _GARBLED_UNVERIFIED_RE.sub(placeholder, text)
    verified = verified_urls or []
    if not verified:
        return text
    vset = {_normalize_url(u) for u in verified}

    def _sub(m: "re.Match[str]") -> str:
        # Markdown link (optionally paren-wrapped): collapse the WHOLE construct so no
        # orphaned [] / () fragment survives when the URL is unverified.
        link_url = m.group("plurl") or m.group("lurl")
        if link_url is not None:
            if _normalize_url(link_url) in vset:
                return m.group(0)                 # verified → keep link intact
            label = (m.group("ptext") or m.group("ltext") or "").strip()
            # A bare-URL label is not meaningful anchor text (and is itself unvouched),
            # so drop it; keep only genuine descriptive labels.
            if label and not label.lower().startswith(("http://", "https://")):
                return f"{label} {placeholder}"
            return placeholder

        # Bare parenthesized URL: replace the whole (url) so we never emit ((unverified)).
        purl = m.group("purl")
        if purl is not None:
            return m.group(0) if _normalize_url(purl) in vset else placeholder

        # Bare inline URL: original behavior — strip trailing punctuation and re-append it.
        raw = m.group("burl")
        core = raw.rstrip(".,)\"'>")
        trail = raw[len(core):]
        return raw if _normalize_url(core) in vset else placeholder + trail

    return _CITATION_RE.sub(_sub, text)


#: Any line carrying the `(unverified)` placeholder — a bare reference bullet, an
#: inline sentence, or a full citation entry — is a claim the pipeline could not
#: substantiate. Tagging it (``neutralize_unverified_urls``) localizes WHICH
#: citation failed; this removes the WHOLE line entirely, so nothing unverified
#: ships to the reader — not even flagged, just gone. Generic: matches on the
#: literal placeholder text only, no task/domain keywords. Distinct pass from
#: neutralization (that one only ever runs when ``verified_urls`` is non-empty,
#: i.e. verification genuinely ran and this citation failed — never a fail-open
#: guess), so a caller runs this unconditionally right after it.
_UNVERIFIED_LINE_RE = re.compile(r"(?m)^.*\(unverified\).*\n?")


def strip_unverified_lines(text: str) -> str:
    """Remove every line containing the ``(unverified)`` placeholder — the whole
    claim it was attached to, not just the tag. A citation that could not be
    substantiated is dropped entirely rather than shipped with a caveat."""
    if not text:
        return text or ""
    return _UNVERIFIED_LINE_RE.sub("", text)


#: A mined "weakness" matching this is actually a POSITIVE statement the miner hallucinated
#: as a gap (e.g. "The report is complete and satisfies all task constraints"). These wrongly
#: drive ``adjusted_score`` down and seed phantom fixes, so they are dropped (PLAN item 6).
_NON_WEAKNESS_RE = re.compile(
    r"(?i)("
    r"no (?:major |significant |further )?(?:weakness|gap|issue|concern|problem)(?:es|s)?\b"
    r"|^none\b"
    r"|satisf(?:y|ies|ied) all|meets all|fully (?:meets|satisfies|complete|addressed)"
    r"|is (?:complete|comprehensive|thorough|excellent|strong|robust|well[- ]?structured"
    r"|well[- ]?organized|well[- ]?sourced)"
    r"|complete and (?:satisf|meets|strong|comprehensive)"
    r"|no (?:further )?(?:improvement|change|action|work)s? (?:needed|required)"
    r"|nothing (?:is )?missing"
    r")"
)


def _is_non_weakness(w: str) -> bool:
    """True when a mined "weakness" is actually a success statement (miner hallucination).

    Grounding the eval (PLAN item 6): a positive assertion is not a gap, so counting it as
    one falsely depresses the score and seeds a fix for nothing. An empty string is also
    not an actionable weakness.
    """
    s = _norm_weakness(w)
    if not s:
        return True
    return bool(_NON_WEAKNESS_RE.search(s))


#: A weakness clause asserting something is ABSENT (vs. merely thin). Used to refute a
#: "missing X" claim deterministically when the doc demonstrably HAS X (PLAN N2).
_ABSENCE_RE = re.compile(
    r"(?i)\b(no|missing|lacks?|lacking|absent|without|omits?|omitted|"
    r"does not (?:include|have|contain)|doesn'?t (?:include|have|contain)|"
    r"fails to (?:include|provide))\b"
)
#: A weakness clause asserting the text is cut off (PLAN N3 — per-section truncation).
_TRUNCATION_RE = re.compile(
    r"(?i)(truncat|cut[- ]?off|cut off|incomplete|ends? abruptly|mid[- ]?sentence|"
    r"mid[- ]?word|unfinished|trails? off)"
)


def _section_ends_cleanly(
    doc: str, section_name: str, sections: list | None = None
) -> bool | None:
    """Does the named section end at a clean boundary? (PLAN N3.)

    Returns True/False when the section is found, ``None`` when it is not (caller then
    cannot refute a truncation claim against it). Concept-matched to the doc's headings so
    a slightly-renamed section still resolves. ``sections`` (pre-split ``[(heading, body)]``
    over the masked doc) may be passed to avoid re-masking/splitting per call."""
    from studio.rubric import _content_tokens, mask_fenced_code
    want = _content_tokens(section_name)
    if not want:
        return None
    if sections is None:
        from agentkit.artifacts.sections import split_sections
        sections = split_sections(mask_fenced_code(doc or ""))
    for heading, body in sections:
        if section_name.lower() in heading.lower() or (want & _content_tokens(heading)):
            return _ends_cleanly(f"{heading}\n{body}")
    return None


def refute_false_weaknesses(weaknesses: list[str], doc: str) -> list[str]:
    """Drop mined weaknesses that claim content is MISSING or TRUNCATED when the artifact
    demonstrably has it (PLAN N2/N3 — eval reliability).

    The miner re-hallucinates "lack of example code", "no conclusion / ends abruptly",
    "Executive Summary truncated" on reports that actually contain a code block, a clean
    Conclusion, and a complete summary. These phantoms depress ``adjusted_score`` and seed
    phantom fixes. Each claim is checked against a DETERMINISTIC fact about the doc; only a
    claim the fact CONTRADICTS is dropped — an unrefuted weakness always survives.
    """
    if not weaknesses or not (doc or "").strip():
        return weaknesses
    from agentkit.artifacts.sections import split_sections
    from studio.rubric import mask_fenced_code
    masked = mask_fenced_code(doc)
    _sections = split_sections(masked)   # split once; reused by every N3 truncation refute
    # A non-mermaid fenced block in the RAW doc = the report contains example code.
    has_code = bool(re.search(r"(?m)^\s*```(?!mermaid)\s*\w", doc))
    has_conclusion = bool(re.search(r"(?im)^#+\s*.*conclusion", masked))
    has_summary = bool(re.search(r"(?i)executive summary|abstract", masked))
    has_url = bool(re.search(r"https?://\S+", doc or ""))
    has_references = bool(re.search(r"(?im)^#+\s*.*references?", masked))
    has_placeholder = bool(
        re.search(
            r"(?i)(?:_\((?:pending|to be completed)\s*[-—][^)]*\)_|"
            r"\bplaceholder\b|no\s+specific\s+urls?\s+were\s+provided)",
            masked,
        )
    )
    doc_clean = _ends_cleanly(doc)
    from studio.rubric import _content_tokens

    def _section_has_real_content(section_name: str) -> bool | None:
        want = _content_tokens(section_name)
        if not want:
            return None
        for heading, body in _sections:
            if section_name.lower() in heading.lower() or (want & _content_tokens(heading)):
                content = "\n".join((body or "").splitlines()[1:]).strip()
                low_content = content.lower()
                content = re.sub(
                    r"(?im)^\s*_\((?:pending|to be completed)\s*[-—][^)]*\)_\s*$",
                    "",
                    content,
                ).strip()
                if not content:
                    return False
                if "placeholder" in low_content and len(content.split()) < 20:
                    return False
                return True
        return None

    kept: list[str] = []
    for w in weaknesses:
        body = _norm_weakness(w)            # strip the [## Section] tag, lowercase
        sec_m = re.match(r"\s*\[([^\]]+)\]", w)
        if (
            sec_m
            and sec_m.group(1).strip().lower() == "document"
            and re.search(r"(?i)\bplaceholder\b|no\s+specific\s+urls?\s+were\s+provided", body)
            and not has_placeholder
        ):
            continue
        if sec_m and re.search(r"(?i)\b(?:placeholder|empty|pending|not addressed)\b", body):
            sec_has_content = _section_has_real_content(sec_m.group(1))
            if sec_has_content is True:
                continue
        # N2: an ABSENCE claim ("no/missing X") refuted when the doc demonstrably has X.
        if _ABSENCE_RE.search(body):
            if (
                re.search(r"(?i)\b(citation|citations|url|urls|source|sources|references?)\b", body)
                and has_url
                and has_references
            ):
                continue
            if re.search(r"(?i)\b(example )?code|snippet|sample\b", body) and has_code:
                continue
            if "conclusion" in body and has_conclusion:
                continue
            if re.search(r"(?i)summary|abstract", body) and has_summary:
                continue
        if (
            "failure to integrate" in body
            and re.search(r"(?i)\b(citation|citations|url|urls|source|sources|references?)\b", body)
            and has_url
            and has_references
        ):
            continue
        # N3: a truncation claim — refute against the named section (if tagged) else the
        # whole document.
        if _TRUNCATION_RE.search(body):
            if sec_m and sec_m.group(1) not in ("document", ""):
                sec_clean = _section_ends_cleanly(doc, sec_m.group(1), _sections)
                if sec_clean is True:
                    continue
            elif doc_clean:
                continue
        kept.append(w)
    return kept


# Loop-closure check (DESIGN §11.4): a weakness re-recorded in this many DISTINCT
# prior runs of the same task was injected and never fixed — stop re-injecting it
# so a persistently-unfixable lesson (data doesn't exist, infra 503) cannot crowd
# out actionable ones forever.
REPEAT_LIMIT = 3

# similar_runs() rank penalty applied to R10 seed candidates whose recorded score
# never ran through the relevance-check (relevance_checked falsy — predates the
# feature). Deprioritizes, not excludes: subtracted from similarity only for ranking.
_RELEVANCE_UNCHECKED_PENALTY = 0.15


def _norm_weakness(w: str) -> str:
    """Normalize a weakness for recurrence counting across runs.

    Strips a leading "[section]" label, lowercases, and collapses whitespace so
    the same lesson phrased near-identically across runs counts as one. (Coarse by
    design — semantic drift is handled separately by _consolidate_weaknesses.)
    """
    s = re.sub(r"^\s*\[[^\]]*\]\s*", "", w or "")  # drop "[## Section]" / "[document]"
    return re.sub(r"\s+", " ", s).strip().lower()


# --- similarity retrieval helpers (R10: cross-task context history) -----------
# task_hash is an EXACT key — only the identical requirement's history is found.
# To also retrieve context from SIMILAR prior tasks we embed each run's
# requirement and rank by cosine, mirroring agentkit.memory.store's pattern.

def _vec_to_blob(vec: list[float]) -> bytes:
    import numpy as np
    return np.asarray(vec, dtype=np.float32).tobytes()


def _blob_to_vec(blob: bytes):
    import numpy as np
    return np.frombuffer(blob, dtype=np.float32)


def _cosine(a, b) -> float:
    import numpy as np
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0 or a.shape != b.shape:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


_CLEAN_END_CHARS = frozenset(".!?)]\"'`|>*-_")


def _ends_cleanly(text: str) -> bool:
    """True if ``text`` ends at a sentence/structure boundary (not truncated mid-line).

    Research reports end with reference lines like "- Author. 'Title.' https://url" where
    the last WORD is a URL, not the line itself — that counts as clean. This is the
    AUTHORITATIVE truncation signal: the miner must use it rather than infer truncation from
    a windowed excerpt boundary (the W3 false positive)."""
    stripped = text.rstrip()
    if not stripped:
        return False
    last_line = stripped.split("\n")[-1].strip()
    last_word = last_line.split()[-1] if last_line.split() else ""
    if last_word.startswith("http://") or last_word.startswith("https://"):
        return True
    return stripped[-1] in _CLEAN_END_CHARS


def verified_urls_in_cache(cache_data: dict, text: str) -> list[str]:
    """URLs cited in ``text`` that are REAL — present in the web cache as either a search
    result (list-valued entries' ``url`` fields) OR a fetched page (key
    ``fetch:{url}:{selector}``). The fetch entries were previously ignored, so a
    fetched-but-not-searched citation was wrongly flagged unverified (the W5 false positive).
    Order-preserving, deduped, trailing punctuation stripped."""
    import re as _re
    cached: set[str] = set()
    for k, v in cache_data.items():
        if isinstance(v, list):
            for r in v:
                if isinstance(r, dict) and "url" in r:
                    cached.add(r["url"])
        elif isinstance(k, str) and k.startswith("fetch:"):
            fu = k[len("fetch:"):].rsplit(":", 1)[0].strip()
            if fu.lower().startswith("http"):
                cached.add(fu)
    # PLAN item 7: match on the NORMALIZED form (scheme/trailing-slash/tracking-param
    # invariant) so a real citation that differs only by http↔https, a trailing slash, or a
    # utm_* tag is not wrongly flagged unverified. The ORIGINAL cited string is returned.
    cached_norm = {_normalize_url(u) for u in cached}
    seen: set[str] = set()
    out: list[str] = []
    for u in _re.findall(r"https?://\S+", text or ""):
        u = u.rstrip(".,)")
        if _normalize_url(u) in cached_norm and u not in seen:
            seen.add(u)
            out.append(u)
    return out


@dataclass
class TaskRun:
    task_hash: str
    session_id: str
    version: int
    score: float
    weaknesses: list[str]
    artifact_path: str
    requirement: str
    result_text: str = ""
    #: §14.4: snapshot of the hill-climb config this run used. Persisted as
    #: ``config_json`` so a later run of the same task can recover its epoch budget.
    config: dict = field(default_factory=dict)
    #: Evidence rows extracted from findings and retained for cross-run inspection/export.
    evidence: list[dict[str, Any]] = field(default_factory=list)
    #: True only when the relevance-check (studio.relevance.relevance_issues) actually
    #: RAN for this record's epoch — i.e. the recorded score accounted for cross-task
    #: contamination. Defaults False for runs that predate the feature (their score
    #: never had a relevance penalty applied), so similar_runs() can deprioritize them
    #: as R10 seeds (a pre-check run looks clean in the DB but may be contaminated).
    relevance_checked: bool = False
    #: Run lifecycle: "completed" (normal, scored) or "failed_partial" (the run died
    #: mid-flight and this row snapshots the partial artifact for carry-forward only —
    #: entry 166). Failed rows are excluded from every improvement signal (weaknesses,
    #: repeat-failures, similar-run seeds, config budget) but ARE eligible seed content
    #: via latest_with_content(). Kept last so positional TaskRun(...) call sites still work.
    status: str = "completed"


def evidence_rows_from_outputs(outputs: dict, *, max_chars: int = 8000) -> list[dict]:
    """Worker label→output map → evidence rows for ``TaskRun.evidence``, so a resumed run
    can re-feed the raw worker outputs to expand_underdeveloped_sections (which otherwise
    starts from ``evidence_json='[]'`` and has nothing to grow depth from). Bounded: each
    output is trimmed to ``max_chars`` so a single run row cannot bloat the DB. Rows carry
    ``kind='worker_output'`` so evidence-matrix consumers can distinguish them from the
    grounded finding rows recorded alongside them."""
    return [
        {"kind": "worker_output", "label": str(label), "output": (out or "")[:max_chars]}
        for label, out in (outputs or {}).items()
        if out
    ]


class TaskRunStore:
    """SQLite store for cross-session task run history."""

    def __init__(self, db_path: Path | None = None, embedder: Any = None) -> None:
        self._path = db_path or _db_path()
        # ``embedder`` (optional, agentkit.types.Embedder): when supplied, each
        # recorded run's requirement is embedded so similar_runs() can do
        # cosine-ranked cross-task retrieval (R10). Without it the store works
        # exactly as before (exact task_hash retrieval only).
        self._embedder = embedder
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_hash TEXT NOT NULL,
                session_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                score REAL NOT NULL,
                weaknesses_json TEXT NOT NULL DEFAULT '[]',
                artifact_path TEXT NOT NULL DEFAULT '',
                requirement TEXT NOT NULL DEFAULT '',
                result_text TEXT NOT NULL DEFAULT '',
                relevance_checked INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'completed',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migrate existing DBs that lack newer columns.
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(task_runs)").fetchall()}
        if "result_text" not in cols:
            self._conn.execute("ALTER TABLE task_runs ADD COLUMN result_text TEXT NOT NULL DEFAULT ''")
        if "requirement_embedding" not in cols:
            # NULL for existing rows; backfilled lazily by similar_runs().
            self._conn.execute("ALTER TABLE task_runs ADD COLUMN requirement_embedding BLOB")
        if "config_json" not in cols:
            # §14.4: per-task hill-climb config snapshot ('{}' for existing rows).
            self._conn.execute(
                "ALTER TABLE task_runs ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "evidence_json" not in cols:
            self._conn.execute(
                "ALTER TABLE task_runs ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "relevance_checked" not in cols:
            # Existing rows predate the relevance-check feature — default 0 ("not
            # checked / unknown"), so similar_runs() deprioritizes them as R10 seeds.
            self._conn.execute(
                "ALTER TABLE task_runs ADD COLUMN relevance_checked INTEGER NOT NULL DEFAULT 0"
            )
        if "status" not in cols:
            # entry 166: existing rows predate partial-persistence — they are all
            # completed runs, so 'completed' is the correct backfill default.
            self._conn.execute(
                "ALTER TABLE task_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'"
            )
        # Uniqueness on (task_hash, version) is the race guard: two concurrent
        # auto-improve runs of the same task both compute MAX(version)+1 and would
        # otherwise insert the SAME version, making latest() nondeterministic. The
        # unique index makes the second insert raise IntegrityError so record_versioned()
        # can retry with a fresh number. Best-effort on legacy DBs that already hold a
        # duplicate — the index just won't be created, atomic allocation still applies going forward.
        try:
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_runs_hash_version "
                "ON task_runs(task_hash, version)"
            )
        except sqlite3.IntegrityError:
            pass
        self._conn.commit()

    def next_version(self, task_hash_str: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(version) FROM task_runs WHERE task_hash = ?", (task_hash_str,)
        ).fetchone()
        return (row[0] or 0) + 1

    def record_versioned(self, run: TaskRun, *, retries: int = 5) -> int:
        """Atomically allocate the next version for ``run.task_hash`` and record it.

        Replaces the caller doing ``next_version()`` then ``record()`` as two steps —
        a window in which a concurrent run could read the same MAX(version) before
        either inserts. The UNIQUE(task_hash, version) index rejects a colliding
        insert; on that IntegrityError we recompute MAX+1 and retry. Returns the
        version actually recorded."""
        for attempt in range(retries):
            run.version = self.next_version(run.task_hash)
            try:
                self.record(run)
            except sqlite3.IntegrityError:
                self._conn.rollback()
                if attempt == retries - 1:
                    raise
                continue
            return run.version
        raise RuntimeError("record_versioned exhausted retries")  # unreachable

    def record(self, run: TaskRun) -> None:
        emb_blob = None
        if self._embedder is not None and run.requirement.strip():
            try:
                vec = self._embedder.embed([run.requirement])[0]
                emb_blob = _vec_to_blob(vec)
            except Exception:  # noqa: BLE001 — embedding is best-effort enrichment
                emb_blob = None
        self._conn.execute(
            """INSERT INTO task_runs
               (task_hash, session_id, version, score, weaknesses_json,
                artifact_path, requirement, result_text, requirement_embedding,
                config_json, evidence_json, relevance_checked, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run.task_hash,
                run.session_id,
                run.version,
                run.score,
                json.dumps(run.weaknesses),
                run.artifact_path,
                run.requirement,
                run.result_text,
                emb_blob,
                json.dumps(run.config or {}),
                json.dumps(run.evidence or []),
                int(bool(run.relevance_checked)),
                run.status or "completed",
            ),
        )
        self._conn.commit()

    def _row_to_run(self, row: tuple) -> TaskRun:
        evidence: list[dict[str, Any]] = []
        if len(row) > 8 and row[8]:
            try:
                parsed = json.loads(row[8])
            except (TypeError, ValueError):
                parsed = []
            if isinstance(parsed, list):
                evidence = [item for item in parsed if isinstance(item, dict)]
        return TaskRun(
            task_hash=row[0], session_id=row[1], version=row[2], score=row[3],
            weaknesses=json.loads(row[4]), artifact_path=row[5],
            requirement=row[6], result_text=row[7], evidence=evidence,
            # status appended after evidence_json in the standard SELECTs; absent from
            # similar_runs' row[:9] slice, where every candidate is already completed-filtered.
            status=row[9] if len(row) > 9 else "completed",
        )

    def latest(self, task_hash_str: str) -> TaskRun | None:
        row = self._conn.execute(
            """SELECT task_hash, session_id, version, score, weaknesses_json,
                      artifact_path, requirement, result_text, evidence_json, status
               FROM task_runs WHERE task_hash = ?
               ORDER BY version DESC LIMIT 1""",
            (task_hash_str,),
        ).fetchone()
        return self._row_to_run(row) if row else None

    def latest_config(self, task_hash_str: str) -> dict:
        """Return the most recent NON-EMPTY hill-climb config for this task (§14.4).

        Empty snapshots (``''`` / ``'{}'`` — recorded by non-hill-climb runs) are
        skipped so they never enable the epoch loop. Returns ``{}`` when no run of
        this task ever persisted a config.
        """
        # status='completed' guard (entry 166): a failed_partial row stores
        # config={"failure": ...} — non-empty but NOT a hill-climb budget. Without this
        # filter it would shadow a prior real config and silently drop the epoch budget.
        row = self._conn.execute(
            """SELECT config_json FROM task_runs
               WHERE task_hash = ? AND config_json NOT IN ('', '{}')
                     AND status = 'completed'
               ORDER BY version DESC LIMIT 1""",
            (task_hash_str,),
        ).fetchone()
        if not row or not row[0]:
            return {}
        try:
            parsed = json.loads(row[0])
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def best(self, task_hash_str: str) -> TaskRun | None:
        row = self._conn.execute(
            """SELECT task_hash, session_id, version, score, weaknesses_json,
                      artifact_path, requirement, result_text, evidence_json, status
               FROM task_runs WHERE task_hash = ?
               ORDER BY score DESC LIMIT 1""",
            (task_hash_str,),
        ).fetchone()
        return self._row_to_run(row) if row else None

    def session_recency(self, session_id: str) -> int:
        """Recency rank for a session = its max DB row id (autoincrement, higher = later).
        Breaks ties when several semantically-similar priors qualify as a seed — the latest
        run has the most accumulated work. Returns 0 for an unknown session."""
        row = self._conn.execute(
            "SELECT MAX(id) FROM task_runs WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def latest_with_content(self, task_hash_str: str, ws_root: Path | None = None) -> TaskRun | None:
        """Return most recent run with usable seed content.

        Prefers *latest* over *best-score*: LLM self-eval scores are noisy and
        the latest run has accumulated the most incremental work.

        Content is the on-disk ``artifact.md`` when it survives (richest — it can
        exceed ``result_text``), else the DB-persisted ``result_text`` for that run.
        The DB fallback is essential: Studio workspaces are ephemeral, so the prior
        session's artifact file is usually GONE by the next run (cross-session /
        post-restart). Returning None there silently cold-starts hill-climb and
        BYPASSES the keep/discard gate — a regressed epoch then overwrites the
        served deliverable with no anti-regression protection (DESIGN §14.6).

        NO status filter (entry 166): this MAY return a ``failed_partial`` row, and that
        is the carry-forward win — when a run died mid-flight its partial artifact is the
        best available seed, strictly better than cold-starting from nothing.
        """
        from studio.workspace import workspace_root as _ws_root  # noqa: PLC0415
        root = ws_root or _ws_root()
        for run in reversed(self.all_runs(task_hash_str)):
            art = root / run.session_id / "artifact.md"
            if art.exists() and art.stat().st_size > 0:
                return run
            if (run.result_text or "").strip():
                return run
        return None

    def all_runs(self, task_hash_str: str) -> list[TaskRun]:
        rows = self._conn.execute(
            """SELECT task_hash, session_id, version, score, weaknesses_json,
                      artifact_path, requirement, result_text, evidence_json, status
               FROM task_runs WHERE task_hash = ? ORDER BY version ASC""",
            (task_hash_str,),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def completed_runs(self, task_hash_str: str) -> list[TaskRun]:
        """Runs that finished and were scored — excludes ``failed_partial`` rows (entry 166).

        The improvement signal (weaknesses, repeat-failures) must derive only from real
        scored runs; a mid-flight death recorded a partial artifact for seeding, not a
        judged outcome, so its (empty) weaknesses/score must never feed forward.
        """
        return [r for r in self.all_runs(task_hash_str) if r.status == "completed"]

    def _backfill_embeddings(self, embedder: Any) -> None:
        """Embed any rows whose requirement_embedding is NULL (lazy migration).

        Existing rows predate the embedding column; embed them once on first
        similarity query so cross-task retrieval works over historical data too.
        """
        rows = self._conn.execute(
            "SELECT id, requirement FROM task_runs "
            "WHERE requirement_embedding IS NULL AND requirement != ''"
        ).fetchall()
        if not rows:
            return
        try:
            vecs = embedder.embed([r[1] for r in rows])
        except Exception:  # noqa: BLE001 — backfill is best-effort
            return
        for (row_id, _req), vec in zip(rows, vecs):
            self._conn.execute(
                "UPDATE task_runs SET requirement_embedding = ? WHERE id = ?",
                (_vec_to_blob(vec), row_id),
            )
        self._conn.commit()

    def similar_runs(
        self,
        requirement: str,
        embedder: Any,
        k: int = 5,
        min_similarity: float = 0.35,
        exclude_hash: str | None = None,
    ) -> list[tuple[TaskRun, float]]:
        """Retrieve context history from SIMILAR prior tasks (R10).

        Unlike exact-key methods (latest/best/all_runs filter on task_hash), this
        embeds ``requirement`` and cosine-ranks every prior task's requirement,
        returning up to ``k`` representative runs (the best-scoring run per
        distinct task_hash) above ``min_similarity``. ``exclude_hash`` drops the
        current task's own exact history (already covered by all_runs).

        Returns ``[(TaskRun, similarity), ...]`` sorted by similarity desc.
        Empty list when no embedder/embeddings or nothing clears the threshold.
        """
        if embedder is None or not requirement.strip():
            return []
        try:
            qvec = embedder.embed([requirement])[0]
        except Exception:  # noqa: BLE001
            return []
        self._backfill_embeddings(embedder)

        # One representative row per distinct task_hash: the best-scoring one
        # (its weaknesses are the most informative). Exclude the current task.
        rows = self._conn.execute(
            """SELECT task_hash, session_id, version, score, weaknesses_json,
                      artifact_path, requirement, result_text, evidence_json,
                      requirement_embedding, relevance_checked
               FROM task_runs
               WHERE requirement_embedding IS NOT NULL AND task_hash != ?
                     AND status = 'completed'
               ORDER BY score DESC""",
            (exclude_hash or "",),
        ).fetchall()

        best_by_hash: dict[str, tuple[TaskRun, float]] = {}
        for row in rows:
            thash = row[0]
            if thash in best_by_hash:  # already kept the top-scoring row (ORDER BY score DESC)
                continue
            sim = _cosine(qvec, _blob_to_vec(row[9]))
            if sim >= min_similarity:
                run = self._row_to_run(row[:9])
                run.relevance_checked = bool(row[10])
                best_by_hash[thash] = (run, sim)

        # Deprioritize (don't exclude — most legitimate runs also lack the flag,
        # since the relevance-check only runs on cross-task-seeded epochs) candidates
        # whose recorded score never accounted for cross-task contamination. A
        # relevance-checked run of comparable similarity now outranks an unchecked
        # one, so a pre-feature contaminated seed (e.g. s_98742f3026ee) loses to any
        # verified alternative — but is still available when it's the only close match.
        # The returned similarity stays RAW (callers threshold on it downstream).
        ranked = sorted(
            best_by_hash.values(),
            key=lambda t: t[1] - (0.0 if t[0].relevance_checked else _RELEVANCE_UNCHECKED_PENALTY),
            reverse=True,
        )
        return ranked[:k]

    def _consolidate_weaknesses(
        self, weaknesses: list[str], embedder: Any, sim_threshold: float = 0.85,
    ) -> list[str]:
        """Semantic near-duplicate consolidation (beyond exact-string dedup).

        Two weaknesses mined from different runs/tasks often say the same thing
        in different words ("no citations" vs "sources lack URLs"). Exact-string
        dedup keeps both; this drops a weakness when it is >= ``sim_threshold``
        cosine-similar to one already kept, preserving the earlier (higher-
        priority — exact-task-first) phrasing. No-op without an embedder.
        """
        if embedder is None or len(weaknesses) <= 1:
            return weaknesses
        try:
            vecs = embedder.embed(weaknesses)
        except Exception:  # noqa: BLE001 — consolidation is best-effort
            return weaknesses
        kept: list[str] = []
        kept_vecs: list[Any] = []
        for w, v in zip(weaknesses, vecs):
            if any(_cosine(v, kv) >= sim_threshold for kv in kept_vecs):
                continue  # near-duplicate of an already-kept lesson
            kept.append(w)
            kept_vecs.append(v)
        return kept

    def repeat_failures(self, exact_hash: str, limit: int = REPEAT_LIMIT) -> set[str]:
        """Normalized weaknesses re-recorded in >= ``limit`` distinct prior runs.

        The loop-closure signal (DESIGN §11.4): a weakness recorded this many times
        was injected and never fixed, so the **reducer** drops it from its handoff
        instead of grinding on it forever. Counting and the policy live here (the
        history layer); the reducer owns *applying* it before writing a handoff.
        """
        freq: dict[str, int] = {}
        for run in self.completed_runs(exact_hash):  # entry 166: failed rows carry no signal
            for nk in {_norm_weakness(w) for w in run.weaknesses}:  # per-run distinct
                if nk:
                    freq[nk] = freq.get(nk, 0) + 1
        return {nk for nk, c in freq.items() if c >= limit}

    def accumulated_weaknesses(
        self,
        requirement: str,
        exact_hash: str,
        embedder: Any = None,
        k_similar: int = 5,
        min_similarity: float = 0.35,
        consolidate_threshold: float = 0.85,
    ) -> list[str]:
        """Merge, dedup, and CONSOLIDATE weaknesses for the agent (R10).

        Pipeline: exact-task lessons first (most relevant), then lessons from
        semantically similar prior tasks → exact-string dedup → semantic
        consolidation (near-duplicate phrasings collapsed). With no embedder this
        degrades to exact-string-deduped exact-task weaknesses (prior behaviour).

        The loop-closure check (dropping repeat-failures) is NOT applied here — it
        is owned by the reducer at handoff time (§11.4), so a known-unfixable
        weakness is never emitted into the loop in the first place. See
        ``repeat_failures``.
        """
        seen: set[str] = set()
        merged: list[str] = []
        for run in self.completed_runs(exact_hash):  # entry 166: failed rows carry no signal
            for w in run.weaknesses:
                if w not in seen:
                    seen.add(w)
                    merged.append(w)
        for run, _sim in self.similar_runs(
            requirement, embedder, k=k_similar,
            min_similarity=min_similarity, exclude_hash=exact_hash,
        ):
            for w in run.weaknesses:
                if w not in seen:
                    seen.add(w)
                    merged.append(w)
        return self._consolidate_weaknesses(merged, embedder, consolidate_threshold)


def score_result(
    result: str,
    requirement: str,
    client: Any,
    verified_urls: list[str] | None = None,
) -> tuple[float, str]:
    """LLM 0-1 quality score for the session result.

    Returns ``(score, unmet_feedback)`` where ``unmet_feedback`` is a newline-joined
    list of criteria the scorer found NOT fully met.  Callers that only need the
    score can ignore the second element.  Returns ``(0.5, "")`` on failure.
    """
    if not result.strip():
        return 0.0, ""
    # Rubric-based scoring with criteria DERIVED FROM THE TASK (not hardcoded). A bare
    # "rate 0-1" prompt structurally caps ~0.75 (the model hedges with no criteria), so a
    # flawless output can never earn 1.0. But hardcoding rubric items (e.g. "must cite
    # articles") games the scorer toward one task shape. Instead we ask the model to first
    # derive the criteria THIS specific task implies, then score 0.2 per criterion met —
    # general across any task, and full marks only when the task is genuinely fully met.
    # Show the full output (capped only to avoid pathological inputs) so tail content and
    # the conclusion are visible.
    _cap = min(len(result), 20_000)
    _label = "full output" if len(result) <= _cap else f"first {_cap} chars"
    _today = datetime.date.today().isoformat()
    _url_note = ""
    if verified_urls:
        _url_note = (
            "VERIFIED SOURCES: the following URLs were confirmed real via actual web "
            "search (not fabricated) — treat them as genuine citations:\n"
            + "\n".join(f"  - {u}" for u in verified_urls[:80])
            + "\n"
        )
    prompt = (
        f"Today's date is {_today}. "
        "Score how well the OUTPUT fulfills the TASK, using a rubric you derive from the "
        "task itself.\n"
        "Step 1: from the TASK alone, identify the 5 most important criteria a complete, "
        "excellent response must satisfy (what this task genuinely demands — completeness, "
        "evidence/grounding, structure, directly answering the ask, etc.).\n"
        "Step 2: award 0.2 for EACH criterion the OUTPUT FULLY meets (partial = 0). Be "
        "strict: an incomplete, truncated, unsubstantiated, or off-task output loses points.\n"
        f"{_url_note}"
        f"TASK: {requirement[:400]}\n"
        f"OUTPUT ({_label}): {result[:_cap]}\n"
        "Reply with exactly this format:\n"
        "SCORE: <total 0.0-1.0>\n"
        "UNMET: <one short phrase per criterion NOT fully met, semicolon-separated, or NONE>"
    )
    try:
        resp = client.chat([{"role": "user", "content": prompt}])
        text = (getattr(resp, "text", "") or "").strip()
        score_m = re.search(r"SCORE:\s*(1\.0|0\.\d+)", text)
        score = float(score_m.group(1)) if score_m else 0.5
        unmet_m = re.search(r"UNMET:\s*(.+)", text)
        unmet_raw = unmet_m.group(1).strip() if unmet_m else ""
        unmet_feedback = "" if unmet_raw.upper() == "NONE" else unmet_raw
        return min(1.0, max(0.0, score)), unmet_feedback
    except Exception:  # noqa: BLE001
        pass
    return 0.5, ""


#: Moving-window miner sizing. _WINDOW = chars shown per LLM call; _STEP = advance between
#: windows (_WINDOW - _STEP = overlap); _MAX_WINDOWS caps cost on a very long report;
#: _MAX_WEAKNESSES caps the deduped union the next run turns into constraints.
_MINE_WINDOW = 12_000
_MINE_STEP = 10_000
_MINE_MAX_WINDOWS = 8
_MINE_MAX_WEAKNESSES = 8


def mine_weaknesses_from_outputs(
    outputs: dict[str, str],
    result: str,
    requirement: str,
    client: Any,
    scorer_feedback: str = "",
    verified_urls: list[str] | None = None,
) -> list[str]:
    """Extract weakness patterns from plain text phase outputs (no AgentResult needed).

    ``requirement`` is the task itself, so the miner judges against what the task
    genuinely demands rather than hardcoded assumptions about the output's shape.
    ``scorer_feedback`` is the semicolon-separated list of unmet criteria from
    ``score_result`` — passing it avoids re-deriving what the scorer already found.
    """
    _outputs_block = "\n\n---\n\n".join(f"[{k}]: {v[:400]}" for k, v in outputs.items())
    _today = datetime.date.today().isoformat()
    _scorer_section = (
        f"A quality scorer already evaluated this output and found these UNMET criteria:\n"
        f"{scorer_feedback}\n"
        f"Use these as your starting point — include them and add any further gaps you find.\n\n"
    ) if scorer_feedback else ""
    # Cache-as-oracle (§11.10): a URL present in the fetch cache was ACTUALLY
    # fetched from a live site, so it is real — the miner must not flag it as
    # unverified/fabricated. Verification is a FACT (in cache or not), not a
    # judgment.
    _verified_section = (
        "VERIFIED SOURCES — the following URLs were ACTUALLY FETCHED from live "
        "sites (they are in the fetch cache), so they are REAL, not fabricated. Do "
        "NOT flag these as unverified or unsubstantiated:\n"
        + "\n".join(f"  - {u}" for u in (verified_urls or [])[:80])
        + "\n\n"
    ) if verified_urls else ""
    # Deterministic truncation signal: do NOT let the LLM infer truncation from a windowed
    # excerpt — it mis-reads window edges as document cut-offs (the W3 false positive).
    # Compute whether the DOCUMENT actually ends cleanly and state it authoritatively.
    _end = (result or "").rstrip()
    _ends_clean = _ends_cleanly(result) if result else True
    _trunc_fact = (
        "DOCUMENT COMPLETENESS (computed — authoritative; trust over any excerpt boundary): "
        + (f"the document ends CLEANLY at {_end[-45:]!r} — it is NOT truncated; do not flag "
           "truncation.\n\n"
           if _ends_clean else
           f"the document ends at {_end[-45:]!r} WITHOUT terminal punctuation — it may be "
           "truncated.\n\n")
    ) if result else ""
    def _build_prompt(combined: str) -> str:
        return (
            f"Today's date is {_today}. "
            f"Review these agent phase outputs against the TASK. List up to 5 specific "
            f"weaknesses or gaps that would stop the output from fully satisfying the task. "
            f"{_trunc_fact}"
            f"Always check, regardless of task type:\n"
            f"1. Is the DOCUMENT truncated? Use the DOCUMENT COMPLETENESS fact above as the "
            f"authoritative answer — do NOT infer truncation from an excerpt's ragged start or "
            f"end (those are window boundaries, not the document).\n"
            f"2. Are claims substantiated and grounded rather than merely asserted? "
            f"Note: sources dated {_today[:7]} are current, not future.\n"
            f"3. If the TASK asks to FIND, DISCOVER, or LIST articles/sources/links: "
            f"does every cited article or source include an actual URL "
            f"(starting with http:// or https://)? Flag missing URLs as a weakness.\n"
            f"4. If the TASK asks for 'most popular' or 'top' items: is there evidence "
            f"(views, stars, engagement metrics) justifying the ranking?\n\n"
            f"{_scorer_section}"
            f"{_verified_section}"
            f"TASK: {requirement[:400]}\n\n{combined}\n\n"
            f"Return a JSON array of short strings. PREFIX each weakness with the artifact "
            f"SECTION it concerns, in square brackets — because the next run assigns each "
            f"section to one agent, and an agent can only fix weaknesses for its own "
            f"section. Use the section's verbatim heading (e.g. '## Sources'); use "
            f"'[document]' only for whole-document or structural issues that no single "
            f'section owns. Example: ["[## Sources] Missing URLs on three articles", '
            f'"[## Findings] Truncated mid-sentence", "[document] No conclusion section"]. '
            f"Return [] ONLY if the work is genuinely complete and strong."
        )

    # Moving window over the FULL document so no region sits in a blind spot. The old
    # head+tail (8K head + 4K tail) left the MIDDLE invisible — a 64K report's Methodology
    # /Conclusion at char ~55K fell between the two windows, so the miner falsely reported
    # PRESENT tail sections as "missing". Each window is labelled "chars A-B of T" so the
    # LLM reads it as an excerpt, not a document cut-off (the deterministic _trunc_fact stays
    # the authoritative truncation signal). One LLM call per window — cost scales with length,
    # bounded by _MINE_MAX_WINDOWS; overlap (_WINDOW - _STEP) keeps a boundary-straddling
    # section whole in at least one window.
    if not result or len(result) <= _MINE_WINDOW:
        _windows = [("[FINAL]", result or "")]
    else:
        _windows = []
        _T = len(result)
        _start = 0
        _n = 0
        while _start < _T and _n < _MINE_MAX_WINDOWS:
            _e = min(_start + _MINE_WINDOW, _T)
            _windows.append(
                (
                    f"[FINAL — window {_n + 1}, chars {_start}-{_e} of {_T}; an excerpt, "
                    f"NOT the document end]",
                    result[_start:_e],
                )
            )
            if _e >= _T:
                break
            _start += _MINE_STEP
            _n += 1

    def _mine_one(combined: str) -> list[str]:
        try:
            resp = client.chat([{"role": "user", "content": _build_prompt(combined)}])
            text = (getattr(resp, "text", "") or "").strip()
            # Greedy match for the OUTERMOST bracket pair — a lazy match (the old `.*?`)
            # stops at the first "]", truncating a weakness string that itself contains one.
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if m:
                return [str(x).strip() for x in json.loads(m.group()) if x][:5]
        except Exception:  # noqa: BLE001
            pass
        return []

    # Union across windows, deduped by normalized weakness so the same gap seen in two
    # overlapping windows is counted once.
    _seen: set[str] = set()
    _merged: list[str] = []
    for _lbl, _win in _windows:
        _combined = _outputs_block + (f"\n\n{_lbl}: {_win}" if _win else "")
        for _w in _mine_one(_combined):
            _k = _norm_weakness(_w)
            # PLAN item 6: drop miner hallucinations — a positive "the report is complete"
            # statement is not a gap, so it must not count as an unsolved weakness (which
            # would falsely depress adjusted_score and seed a phantom fix).
            if _k and _k not in _seen and not _is_non_weakness(_w):
                _seen.add(_k)
                _merged.append(_w)
    return _merged[:_MINE_MAX_WEAKNESSES]
