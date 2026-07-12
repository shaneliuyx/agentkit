"""studio.tools — ToolAugmentedClient: a tool loop over any LLMClient.

The architecture constraint (SPEC §5.2 / topology.dynamic): ``run_plan``'s
runners call ``client.chat()`` ONCE with no tool loop. To give a phase real tool
capability without touching ``run_plan``, ``ToolAugmentedClient`` SATISFIES
``agentkit.types.LLMClient`` and WRAPS an inner client (a ``StudioChatClient``):

  - It registers tool schemas (OpenAI tool format): ``web_search`` and
    ``web_fetch`` (built from ``web_toolkit``) plus ``read_file`` / ``write_file``
    confined to a per-session :class:`~studio.workspace.Workspace` (realpath jail).
  - ``.chat(messages, tools=None)`` merges those schemas into ``tools``, calls
    the inner client, and if the result carries a registered tool_call, executes
    it, appends a tool-result message, and re-calls — looping until no tool_call
    or a max-iter cap. Tokens accumulate across iterations.
  - It fires ``tool_call`` / ``tool_result`` events through injected callbacks
    (same pattern as ``on_usage``), carrying the current ``step_id``.

web_search degrades per web_toolkit precedence (SearXNG → Tavily → DDG); a
``SearchError`` is non-fatal (empty result + notice). web_fetch reads a page to
clean markdown; a missing scrapling CLI (``FetchError``) or a per-page failure
(``ok=False`` for 404/blocked) is non-fatal (error result + notice), and the
content is capped at ``_MAX_FETCH_CHARS`` so one huge page cannot flood context.
File tools are jailed: a path escaping the workspace returns an error result + a
notice, never raising, never a raw ``open()`` outside the workspace.
"""

from __future__ import annotations

import contextvars
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from agentkit.artifacts.occ import patch_artifact as _occ_patch
from agentkit.artifacts.occ import read_artifact as _occ_read
from agentkit.tools.artifact import ARTIFACT_TOOL_SCHEMAS
from agentkit.tools.fetch_cache import InFlightRegistry
from agentkit.types import ChatResult, Message

from studio.workspace import Workspace, WorkspaceError

#: Default number of web results to request per tool call.
_DEFAULT_RESULTS = 5
#: Hard cap on tool-loop iterations so a misbehaving model cannot loop forever.
# Raised 5→8: a multi-source research spoke needs search + several fetches AND a
# final synthesis turn. At 5, search + 4 fetches consumed every iteration and the
# worker exhausted the budget still in tool_use — it returned only its pre-tool
# preamble ("I'll fetch the articles now…") and emitted ZERO findings (the haiku
# no-op that flat-lined the hill-climb). 8 leaves room for ~6 fetches plus a natural
# tools-enabled synthesis turn; the iteration-exhaustion backstop in chat() still
# forces one tools-disabled synthesis call if even 8 are spent fetching.
_MAX_TOOL_ITERS = 8
#: Retry cap for transient rate-limit errors from the inner LLM client.
_MAX_RATE_RETRIES = 3
#: Character threshold at which context compaction fires inside the tool loop.
#: ~80K chars ≈ 20K tokens (4 chars/token heuristic). Fetch results run up to
#: 128K chars each; compaction prevents runaway context on multi-fetch loops.
_COMPACT_CHARS = 80_000
#: Studio-local size ceiling for edit_file (read-side AND write-side), independent
#: of agentkit core's shared MAX_OUTPUT_BYTES (64 KiB, used by read_file et al.).
#: edit_file reads/writes the WHOLE file, so both sides share this one threshold:
#: a read at/over the cap can't be distinguished from a truncated read, so editing
#: it would risk a truncated write-back (data loss) — refuse; and a write that would
#: exceed the cap is refused before touching disk. Deliberately NOT the core
#: constant: raising that would widen the blast radius to every jailed-read consumer.
_EDIT_FILE_MAX_BYTES = 100 * 1024


def _convo_chars(messages: list) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages)
class _ContextFetchCache:
    """Per-run view over the web_fetch cache, isolated by ``contextvars``.

    Finding 4: a single process-global dict let session B's citation "verify"
    against a URL that session A fetched — grounding (findings.py) treats any
    match in the cache as proof the page was read. Each Studio run executes on its
    own ``threading.Thread`` (studio.app worker), and a new thread starts with a
    fresh context, so keying the store on a ``ContextVar`` gives every run its own
    cache with no cross-session leakage. It also removes the concurrent-write /
    iterate race: reads snapshot the current context's dict before iterating, and
    only this run's thread ever writes into it.

    Behaves like a ``dict`` for every existing call site and test (``[]`` access,
    ``in``, ``bool()``, ``.values()``/``.items()``, ``.clear()``, iteration)."""

    _var: "contextvars.ContextVar[dict[str, tuple[str, int]]]" = contextvars.ContextVar(
        "studio_fetch_cache"
    )

    def _store(self) -> dict[str, tuple[str, int]]:
        try:
            return self._var.get()
        except LookupError:
            fresh: dict[str, tuple[str, int]] = {}
            self._var.set(fresh)
            return fresh

    def __getitem__(self, key: str) -> tuple[str, int]:
        return self._store()[key]

    def __setitem__(self, key: str, value: tuple[str, int]) -> None:
        self._store()[key] = value

    def __delitem__(self, key: str) -> None:
        del self._store()[key]

    def __contains__(self, key: object) -> bool:
        return key in self._store()

    def __iter__(self):
        return iter(list(self._store()))  # snapshot — no iterate-vs-write race

    def __len__(self) -> int:
        return len(self._store())

    def __bool__(self) -> bool:
        return bool(self._store())

    def get(self, key: str, default: object = None) -> object:
        return self._store().get(key, default)

    def values(self) -> list[tuple[str, int]]:
        return list(self._store().values())  # snapshot

    def keys(self) -> list[str]:
        return list(self._store().keys())

    def items(self) -> list[tuple[str, tuple[str, int]]]:
        return list(self._store().items())  # snapshot

    def clear(self) -> None:
        self._store().clear()

    def reset(self) -> None:
        """Install a fresh empty store in the current context (guards thread reuse)."""
        self._var.set({})


#: In-process cache for successful web_fetch results. Key: "url|selector".
#: Only successful fetches are cached (errors are not stored so retries work).
#: Context-isolated per run — see _ContextFetchCache.
_fetch_cache = _ContextFetchCache()

#: A QUOTE shorter than this carries no grounding signal (e.g. "the", "agents") —
#: it would match almost any page, so it never counts as substantiation.
_MIN_QUOTE_CHARS = 12


def _quote_in_cache(quote: str) -> bool:
    """True iff ``quote`` appears verbatim in any cached fetched page (Lever 1 guard).

    The fetch cache is the oracle for "did the worker actually read this?": a QUOTE
    that is a substring of a cached page is grounded; one that is not is fabricated.
    Whitespace is normalized on both sides (markdown re-wraps lines, so a real quote
    split across a newline still matches). An empty/very short quote never matches —
    it carries no grounding signal. Callers should only ACT on a False result when
    the cache is non-empty (an empty cache means "cannot verify", not "fabricated").
    """
    q = " ".join((quote or "").split())
    if len(q) < _MIN_QUOTE_CHARS:
        return False
    pages = [" ".join(content.split()).lower() for content, _n in _fetch_cache.values()]
    ql = q.lower()
    if any(ql in p for p in pages):
        return True
    # Fuzzy fallback: models reconstruct quotes with a dropped/added word at the edges,
    # so exact-substring misses real quotes (live: woven verbatim quotes never survived
    # merge — 0 in the artifact). Accept when a CONTIGUOUS run of >=70% of the quote's
    # words appears verbatim on a fetched page — enough to prove the page was read, while
    # a mere paraphrase (no long verbatim run) still fails.
    toks = ql.split()
    need = max(6, int(len(toks) * 0.7))
    if need > len(toks):
        return False
    return any(
        " ".join(toks[start:start + need]) in p
        for start in range(0, len(toks) - need + 1)
        for p in pages
    )


def _url_in_cache(url: str) -> bool:
    """True iff ``url`` was actually fetched this run (its page is in the fetch cache).

    This is the GROUNDING oracle for a finding: a URL whose page was fetched is real;
    one that was never fetched is fabricated. It is stricter and more reliable than the
    quote check — models reconstruct quotes imperfectly (so a verbatim-substring test
    drops real, fetched sources), but the URL they fetched is exact. Cache keys are
    "url|selector"; match on the url segment.
    """
    u = (url or "").strip().rstrip("/").lower()
    if not u:
        return False
    for k in _fetch_cache:
        ck = k.split("|", 1)[0].strip().rstrip("/").lower()
        # Tolerant match: models emit URLs with trailing-slash / fragment / case
        # drift vs the fetched key, so exact == discards real sources. Containment
        # either way covers those near-misses without matching unrelated URLs.
        if ck and (u == ck or u in ck or ck in u):
            return True
    return False


def _page_for_url(url: str) -> str:
    """Cached page text for ``url`` ("" when never fetched). Lets a caller judge a
    finding by its SOURCE — the fetched page cannot self-rationalize the way finding
    prose can.

    EXACT normalized-key match (trailing-slash / fragment / case tolerant), NOT the
    containment match ``_url_in_cache`` uses. This returns CONTENT that the caller
    attributes to *this exact URL* (evidence file, topical verdict), so a substring
    hit — asking ``…/topic`` while the cache holds ``…/topic-extra`` — would return a
    different page's body and misattribute it (corrupt evidence → corrupt claims).
    The tolerant containment is correct only for ``_url_in_cache``'s grounding bool,
    where a near-miss merely avoids dropping a real source; here it corrupts."""
    u = (url or "").strip().rstrip("/").split("#", 1)[0].lower()
    if not u:
        return ""
    for k, (content, _n) in _fetch_cache.items():
        ck = k.split("|", 1)[0].strip().rstrip("/").split("#", 1)[0].lower()
        if ck and ck == u:
            return str(content or "")
    return ""


_MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB download cap for a PDF source


def _fetch_pdf_text(url: str) -> str | None:
    """Extract text from a PDF URL so it can be read like any other source — the
    moving-window extractor then processes the full document exactly like a README.
    Downloads the bytes (capped), verifies the ``%PDF`` magic, and joins per-page
    text via pypdf. Returns None on any failure (fail-open: the caller drops an
    ungroundable source, never crashes)."""
    from io import BytesIO
    from urllib.request import Request, urlopen

    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001 — pypdf absent → cannot read PDFs, drop stands
        return None
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (agentkit-studio)"})
        with urlopen(req, timeout=30) as resp:  # noqa: S310 — http(s) asserted by caller
            data = resp.read(_MAX_PDF_BYTES + 1)
    except Exception:  # noqa: BLE001 — network/timeout failure is non-fatal
        return None
    if len(data) > _MAX_PDF_BYTES or not data.startswith(b"%PDF"):
        return None  # oversized, or not actually a PDF
    try:
        reader = PdfReader(BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip() or None
    except Exception:  # noqa: BLE001 — malformed/encrypted PDF → drop
        return None


def _fetch_page(url: str) -> tuple[str, tuple[str, int] | None]:
    """Pure fetch for one URL → ``(cache_key, page | None)``; NEVER touches ``_fetch_cache``.

    Extracted verbatim from ``prefetch_url`` so the serial (``prefetch_url``) and parallel
    (``findings._prefetch_cited``) paths share ONE fetch implementation — same URL
    normalization, ``f"{u}|"`` key, ok/empty-content handling, ``_MAX_FETCH_CHARS``
    truncation, and non-fatal exception behavior. Cache access is the CALLER's job so
    worker threads (which run under a fresh ``contextvars`` context) never read/write the
    single-writer ``_fetch_cache``. ``page`` is None for a non-http, unreachable, empty,
    or errored fetch (the caller drops it, keeping a fabricated citation ungrounded)."""
    u = (url or "").strip().rstrip(".,)")
    key = f"{u}|"
    if not u.lower().startswith("http"):
        return key, None
    if u.split("?", 1)[0].lower().endswith(".pdf") or "/pdf/" in u.lower():
        # A PDF source (``.pdf`` suffix or an arXiv-style ``/pdf/<id>`` path): extract
        # its text so it reads like a README/HTML page. web_fetch would return binary
        # garbage for a PDF. ``_fetch_pdf_text`` verifies the %PDF magic and returns
        # None when the URL is not actually a PDF — so we fall through to the normal
        # HTML path in that case rather than dropping the source.
        text = _fetch_pdf_text(u)
        if text:
            return key, (text[:_MAX_FETCH_CHARS], len(text.encode("utf-8")))
    try:
        from web_toolkit import web_fetch
    except Exception:  # noqa: BLE001 — toolkit absent → cannot ground, drop stands
        return key, None
    try:
        res = web_fetch(u, selector=None)
    except Exception:  # noqa: BLE001 — network/backend failure is non-fatal
        return key, None
    if not getattr(res, "ok", False):
        return key, None
    content = getattr(res, "content", "") or ""
    if not content:
        return key, None
    n_bytes = getattr(res, "bytes", 0) or len(content.encode("utf-8"))
    return key, (content[:_MAX_FETCH_CHARS], n_bytes)


def prefetch_url(url: str) -> bool:
    """Fetch ``url`` and store its page in the fetch cache (key ``"url|"``), grounding a
    finding that CITED a searched-but-unfetched URL. Returns True if the page is now
    cached. Best-effort and side-effecting: a cache hit returns True with NO network
    call (so it is test-safe when the URL is pre-cached); a 404 / unreachable / non-http
    URL returns False and stays uncached — so a fabricated citation is still dropped.

    Serial wrapper: ``_fetch_page`` + a single-writer ``_fetch_cache`` store. The parallel
    reducer path (``findings._prefetch_cited``) reuses the same ``_fetch_page`` and does
    its own serial store, so both paths ground identically."""
    u = (url or "").strip().rstrip(".,)")
    if u.lower().startswith("http") and f"{u}|" in _fetch_cache:
        return True  # cache hit, no network (test-safe)
    key, page = _fetch_page(url)
    if page is None:
        return False
    _fetch_cache[key] = page
    return True
#: Detects "narration instead of execution" — the LLM describes its plan instead
#: of calling the tool. The tool loop injects a forcing turn when this fires.
_PLANNING_RE = re.compile(
    r"\b("
    r"about\s+to\s+(?:fetch|search|retrieve|verify|check)|"
    r"will\s+now\s+(?:fetch|search|retrieve|verify|check)|"
    r"going\s+to\s+(?:fetch|search|retrieve|verify|check)|"
    r"plan(?:ning)?\s+to\s+(?:fetch|search|retrieve|verify|check)|"
    r"next[,\s]+I\s+will\b|"
    r"I\s+will\s+(?:now\s+)?(?:fetch|search|retrieve|verify)\b|"
    r"proceed(?:ing)?\s+to\s+(?:fetch|search|verify)"
    r")",
    re.IGNORECASE,
)

#: Detects a URL in a would-be final answer. A weak model (gemma) skips research
#: entirely and emits a plausible-looking citation it never fetched — the loop
#: injects a grounding-forcing turn (once) so it searches instead of fabricating.
_CITED_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")


def _is_rate_limit(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(x in msg for x in ("rate", "429", "too many", "limit exceeded", "overloaded"))
#: Ceiling on fetched page content (chars ≈ bytes for ASCII-dominant markdown) so
#: a pathologically huge page cannot flood the model's context; truncation is noted
#: in the summary. Raised 32K→128K: real source articles run 30-60KB, and cutting
#: them at 32K starved the agent of the very content it needs to cite/quote accurately
#: (a contributor to unverifiable-citation weaknesses). 128K still bounds a runaway page.
_MAX_FETCH_CHARS = 128 * 1024

#: The OpenAI tool schema advertised to the model.
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for up-to-date information. Returns ranked results "
            "with title, url, and snippet. Use for facts you are unsure about or "
            "that may have changed recently."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "results": {
                    "type": "integer",
                    "description": f"Max results (default {_DEFAULT_RESULTS}).",
                },
            },
            "required": ["query"],
        },
    },
}

#: Fetch a web page's main text as clean markdown (peer to web_search).
WEB_FETCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Fetch a web page's main text content as clean markdown (use after "
            "web_search to read a result)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL (include https://)."},
                "selector": {
                    "type": "string",
                    "description": "Optional CSS selector to extract only a region.",
                },
            },
            "required": ["url"],
        },
    },
}

#: Grep budget for search_evidence: cap matches + context so a local oMLX model's
#: context is never flooded (same spirit as the 2500-char excerpt cap in
#: _final_evidence_dossier). A few lines around each hit, not the whole file.
_MAX_EVIDENCE_MATCHES = 10
_EVIDENCE_CONTEXT_LINES = 3
_MAX_EVIDENCE_CONTEXT_CHARS = 800

#: Cap on paths returned by the glob tool (consistent with the evidence caps: keep
#: a local model's context bounded). Truncation is flagged in the result.
_MAX_GLOB_RESULTS = 100
#: Default scope for search_evidence when no ``glob`` arg is given (backward compat).
_DEFAULT_EVIDENCE_GLOB = "evidence/*.md"

#: Grep across the session's fetched evidence files (jailed to evidence/).
SEARCH_EVIDENCE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_evidence",
        "description": (
            "Grep across the fetched evidence files (evidence/*.md) gathered this "
            "run. A single keyword matches per line; a multi-word query matches "
            "when most of its words co-occur in one passage. Returns up to 10 "
            "matches, each with file, line number, the source url, and a few lines "
            "of surrounding context (never a whole file). Use to find grounding "
            "content — a quote, statistic, or citation — when a section needs more "
            "depth or a claim needs support; cite the returned url. Pass 'glob' to "
            "widen the search to other workspace files (e.g. 'sections/*.md' or "
            "'**/*.md'); omit it to search evidence only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Case-insensitive substring/keyword to search for.",
                },
                "context_lines": {
                    "type": "integer",
                    "description": f"Lines of context around each hit (default {_EVIDENCE_CONTEXT_LINES}).",
                },
                "glob": {
                    "type": "string",
                    "description": (
                        "Optional workspace-relative glob selecting which files to search "
                        f"(default '{_DEFAULT_EVIDENCE_GLOB}'). Widen to search the whole workspace."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}

#: Read a file from the per-session workspace (jailed).
READ_FILE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file from your workspace. The path is relative to "
            "the workspace; paths escaping it are rejected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path."},
            },
            "required": ["path"],
        },
    },
}

#: Write a file into the per-session workspace (jailed, side-effecting).
WRITE_FILE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Write a UTF-8 text file into your workspace, creating parent "
            "directories as needed. The path is relative to the workspace; paths "
            "escaping it are rejected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path."},
                "content": {"type": "string", "description": "File contents to write."},
            },
            "required": ["path", "content"],
        },
    },
}

#: Edit a file in the per-session workspace by unique find/replace (jailed).
#: Contract mirrors Claude Code / DeepAgents' edit_file: old_string must be
#: unique unless replace_all is set. Unlike patch_artifact this has NO OCC — it
#: is a plain file editor (artifact.md's concurrent-mutation is patch_artifact's
#: special case, not a generic editor's concern).
EDIT_FILE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Edit a UTF-8 text file in your workspace by replacing old_string with "
            "new_string. old_string must appear EXACTLY ONCE in the file (else an "
            "error), unless replace_all=true replaces every occurrence. The path is "
            "relative to the workspace; paths escaping it are rejected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Workspace-relative file path."},
                "old_string": {"type": "string", "description": "Exact text to find (must be unique)."},
                "new_string": {"type": "string", "description": "Replacement text."},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace every occurrence instead of requiring uniqueness (default false).",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
}

#: List files in the per-session workspace matching a glob pattern (jailed).
GLOB_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "glob",
        "description": (
            "List files in your workspace matching a glob pattern (e.g. 'sections/*.md' "
            "or '**/*.md'). Returns up to 100 workspace-relative paths. Optional 'path' "
            "scopes the search to a subdirectory. Paths escaping the workspace are rejected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.md'."},
                "path": {
                    "type": "string",
                    "description": "Optional workspace-relative subdirectory to scope within (default: workspace root).",
                },
            },
            "required": ["pattern"],
        },
    },
}

#: Artifact OCC tool schemas sourced from agentkit.tools.artifact.
#: Re-exported here for callers that import from studio.tools directly.
#: read_artifact is overridden to SECTION-SCOPED reads (token-cap, §11.4): the
#: full document is never dumped at once. No arg -> a cheap section index; a
#: ``section`` arg -> that one section. Per-section hashes let an agent re-read a
#: section only when it actually changed (a patch to one section never busts the
#: others' cache the way a whole-doc hash would).
import copy as _copy  # noqa: E402  (late: placed next to the schema it deep-copies)

READ_ARTIFACT_TOOL = _copy.deepcopy(ARTIFACT_TOOL_SCHEMAS[0])
_raf = READ_ARTIFACT_TOOL["function"]
_raf["description"] = (
    "Read the deliverable WITHOUT dumping the whole document. With NO arguments, "
    "returns a cheap SECTION INDEX: [{section, hash, chars}] listing every '##' "
    "section. Pass section='## Exact Heading' (verbatim from the index) to read "
    "ONE section's full body + its hash. Re-read a section only if its hash "
    "changed since you last saw it — this keeps context small."
)
_raf.setdefault("parameters", {}).setdefault("properties", {})["section"] = {
    "type": "string",
    "description": "Exact '## Heading' (verbatim from the index) to read one section. Omit for the index.",
}
PATCH_ARTIFACT_TOOL = ARTIFACT_TOOL_SCHEMAS[1]

#: Registry of every tool schema, keyed by advertised name. Used by ``_schemas``
#: when an explicit ``offer_tools`` allowlist is set (the editor pass restricts to
#: read_file + search_evidence + read_artifact + patch_artifact + edit_file + glob).
_ALL_TOOL_SCHEMAS: list[dict[str, Any]] = [
    WEB_SEARCH_TOOL,
    WEB_FETCH_TOOL,
    READ_FILE_TOOL,
    WRITE_FILE_TOOL,
    EDIT_FILE_TOOL,
    GLOB_TOOL,
    SEARCH_EVIDENCE_TOOL,
    READ_ARTIFACT_TOOL,
    PATCH_ARTIFACT_TOOL,
]

#: Tools that MUTATE state, derived from the schemas above (never a task/domain
#: keyword) — used to detect a model that keeps calling read-only tools without
#: ever committing an edit. See ``_READ_ONLY_STREAK_THRESHOLD`` below.
_WRITE_TOOL_NAMES: frozenset[str] = frozenset(
    s["function"]["name"] for s in (WRITE_FILE_TOOL, EDIT_FILE_TOOL, PATCH_ARTIFACT_TOOL)
)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Deterministically split markdown into top-level ('##') sections (0 LLM).

    Returns ordered (heading, body) pairs; body INCLUDES the heading line and any
    nested '###' subsections beneath it. Content before the first '##' (the title
    + intro) is the first pair, keyed '(intro)'.
    """
    import re
    out: list[tuple[str, str]] = []
    head: str | None = None
    buf: list[str] = []
    for line in text.splitlines(keepends=True):
        if re.match(r"^##\s", line):
            if head is not None or buf:
                out.append((head or "(intro)", "".join(buf)))
            head = line.strip()
            buf = [line]
        else:
            buf.append(line)
    if head is not None or buf:
        out.append((head or "(intro)", "".join(buf)))
    return out


def _section_hash(body: str) -> str:
    """Stable short content hash for one section (dedup key)."""
    import hashlib
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]

#: Callbacks: (step_id, tool, args) and (step_id, tool, summary, n_results, notice).
OnToolCall = Callable[[str, str, dict[str, Any]], None]
OnToolResult = Callable[[str, str, str, int, str, bool], None]


def web_toolkit_available() -> bool:
    """True iff ``web_toolkit.web_search`` can be imported (tools-enabled gate)."""
    try:
        from web_toolkit import web_search  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


#: Backends without OpenAI structured function-calling (e.g. oMLX/Qwen) emit the
#: call as inline text in a <execute>/<tools>/<tool_call> tag instead of a
#: `tool_calls` field. Parse that so tools fire on those backends too — otherwise
#: the call blob silently leaks into the answer and the tool never runs.
_INLINE_TOOL_RE = re.compile(r"<(execute|tools?|tool_call)>(.*?)</\1>", re.DOTALL)
#: oMLX local models (qwen2.5-coder etc.) emit the call as a fenced code block
#: (```json {...} ```) rather than <tag>-wrapped — without this they fetch nothing.
_FENCED_RE = re.compile(r"```[a-zA-Z_]*\s*\n?(.*?)```", re.DOTALL)


def _parse_inline_tool_calls(
    text: str, names: set[str]
) -> list[tuple[str, dict[str, Any]]]:
    """Extract ``(name, args)`` calls a model emitted as inline JSON (no native
    ``tool_calls`` field). Recognizes three shapes a non-function-calling backend uses:
    ``<execute|tools|tool_call>{...}</...>`` tags, ```` ```json {...} ```` fenced
    blocks (oMLX/qwen), and a bare top-level ``{...}`` object. Each candidate is
    json-parsed and kept ONLY if it names a registered tool — so stray JSON/prose
    can't fire a tool. Returns ``[]`` on any parse miss; deduped so the same call in
    two forms fires once.
    """
    candidates: list[str] = [inner for _tag, inner in _INLINE_TOOL_RE.findall(text)]
    candidates += _FENCED_RE.findall(text)
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)

    out: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for inner in candidates:
        try:
            obj = json.loads(inner.strip())
        except Exception:  # noqa: BLE001 - a non-JSON blob is simply not a tool call
            continue
        if not isinstance(obj, dict):
            continue
        name = obj.get("name")
        args = obj.get("arguments", obj.get("parameters", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:  # noqa: BLE001
                args = {}
        if name in names and isinstance(args, dict):
            key = (name, json.dumps(args, sort_keys=True))
            if key not in seen:
                seen.add(key)
                out.append((name, args))
    return out


def base_client(client: Any) -> Any:
    """The underlying non-tool client for deterministic sub-prompts that must NOT
    enter the web-search tool loop.

    ASSEMBLE-phase renders (diagram triple/component extraction, per-subject
    diagrams) operate on already-gathered claims — routing them through the tool
    loop measurably degrades the plain structured output (live: gemma answered a
    ``SOURCE | RELATION | TARGET`` prompt as ``Pi | powered by | Pi SDK`` under the
    tool wrapper vs. the correct ``Craft Agents | utilizes | Pi`` bare, silently
    breaking the grounded cross-subject edge). Unwraps a ``ToolAugmentedClient``
    (recursively, in case of double-wrap) to its inner client; returns any other
    client unchanged. Keeps the private ``_inner`` attribute encapsulated in this
    module rather than reached into from callers."""
    inner = client
    while isinstance(inner, ToolAugmentedClient):
        inner = inner._inner
    return inner


class ToolAugmentedClient:
    """An ``LLMClient`` that runs a ``web_search`` tool loop over an inner client.

    ``search_fn`` / ``fetch_fn`` are injectable (tests pass stubs so NO network is
    hit); the defaults lazily import ``web_toolkit.web_search`` / ``web_fetch``.
    ``step_id`` is read at call time via ``step_id_getter`` so tool events carry
    the live phase id.
    """

    def __init__(
        self,
        inner: Any,
        *,
        on_tool_call: OnToolCall | None = None,
        on_tool_result: OnToolResult | None = None,
        step_id_getter: Callable[[], str] | None = None,
        search_fn: Callable[..., list[Any]] | None = None,
        fetch_fn: Callable[..., Any] | None = None,
        workspace: Workspace | None = None,
        artifact_path: Path | None = None,
        offer_tools: set[str] | None = None,
        max_iters: int = _MAX_TOOL_ITERS,
        max_searches: int | None = None,
        max_successful_fetches: int | None = None,
        context_compact: bool = True,
        auto_fetch_top_results: int = 0,
        auto_fetch_page_chars: int = 12_000,
    ) -> None:
        self._inner = inner
        self._on_tool_call = on_tool_call
        self._on_tool_result = on_tool_result
        self._step_id_getter = step_id_getter or (lambda: "")
        self._search_fn = search_fn
        self._fetch_fn = fetch_fn
        self._workspace = workspace
        self._artifact_path = artifact_path
        self._offer_tools = offer_tools
        self._max_iters = max_iters
        self._max_searches = max_searches
        self._max_successful_fetches = max_successful_fetches
        self._context_compact = context_compact
        self._auto_fetch_top_results = max(0, int(auto_fetch_top_results))
        # Per-page slice spliced into the search message. A tiny slice (the
        # original 2500) is fine for RELEVANCE triage but forces the model to
        # paraphrase when the quotable sentence sits deeper — verified live: a
        # 57K-char fetched article produced a stitched near-quote that fails
        # verbatim grounding. Sized by the caller to the model's section window.
        self._auto_fetch_page_chars = max(1_000, int(auto_fetch_page_chars))
        #: Per-instance registry — blocks concurrent threads from fetching the
        #: same URL simultaneously (dog-pile prevention within one run).
        self._in_flight = InFlightRegistry()

    @property
    def _schemas(self) -> list[dict[str, Any]]:
        """The tool schemas advertised this run. File tools appear only when a
        workspace is wired; artifact tools only when artifact_path is set.

        When ``offer_tools`` is set (the editor pass), it is an explicit allowlist:
        exactly the named schemas are advertised (the caller is responsible for
        wiring the workspace/artifact_path those tools need)."""
        if self._offer_tools is not None:
            return [s for s in _ALL_TOOL_SCHEMAS if s["function"]["name"] in self._offer_tools]
        schemas = [WEB_SEARCH_TOOL, WEB_FETCH_TOOL]
        if self._workspace is not None:
            schemas += [READ_FILE_TOOL, WRITE_FILE_TOOL]
        if self._artifact_path is not None:
            schemas += [READ_ARTIFACT_TOOL, PATCH_ARTIFACT_TOOL]
        return schemas

    @property
    def _tool_names(self) -> set[str]:
        return {s["function"]["name"] for s in self._schemas}

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """Run the inner client with web_search available, looping on tool_calls.

        Accumulates ``total_tokens`` across every inner call; returns the final
        assistant ChatResult once the model stops calling the tool (or the cap is
        hit). The web_search schema is merged into any caller-supplied ``tools``.
        """
        merged_tools = list(tools or []) + self._schemas
        names = self._tool_names
        convo = list(messages)
        total_tokens = 0
        last: ChatResult | None = None
        call_seq = 0  # globally-unique, valid-char tool_call ids across the chat
        budget_state = {"web_search": 0, "web_fetch_success": 0}
        # True only when the model STOPPED calling tools on its own (Anthropic's
        # end_turn) — i.e. it produced a real final answer. False means the loop ran
        # out of iterations while the model was still in tool_use, so `last.text` is
        # only its pre-tool preamble ("I'll fetch the articles now…"), never the
        # synthesis. See the iteration-exhaustion synthesis guard below.
        stopped_naturally = False
        # A model offered a write tool that keeps calling only READ-only ones
        # never triggers the narration-forcing branch below (it IS calling tools,
        # just never a mutating one) — real evidence: gemma re-read the same
        # sections 24/24 times across 3 structural-retry attempts, never once
        # calling patch_artifact. `_write_names` is empty (nothing to force)
        # when no write tool is offered this call at all.
        _write_names = names & _WRITE_TOOL_NAMES
        _read_only_streak = 0
        _force_threshold = max(2, self._max_iters // 2)
        _forced_grounding = False  # the fabricated-citation forcing turn fires at most once

        for _ in range(self._max_iters):
            result = self._chat_inner(convo, merged_tools)
            total_tokens += getattr(result, "total_tokens", 0) or 0
            last = result

            tool_calls = [
                (name, args)
                for (name, args) in (getattr(result, "tool_calls", None) or [])
                if name in names
            ]
            if not tool_calls:
                # Backends without structured function-calling (oMLX/Qwen) emit the
                # call as inline tagged text — parse it so tools still fire.
                tool_calls = _parse_inline_tool_calls(result.text or "", names)
            if not tool_calls:
                # Detect "narration instead of execution": the LLM described
                # what it plans to do (e.g. "I will now fetch…") without
                # actually calling the tool. Inject a forcing turn so the
                # next iteration executes rather than plans again.
                text_so_far = (result.text or "").strip()
                if (
                    text_so_far
                    and names
                    and _ < self._max_iters - 1
                    and _PLANNING_RE.search(text_so_far)
                ):
                    convo.append({"role": "assistant", "content": text_so_far})
                    convo.append({
                        "role": "user",
                        "content": (
                            "Stop describing what you plan to do. "
                            "Execute NOW — call the appropriate tool immediately."
                        ),
                    })
                    continue
                # Fabricated-citation guard: a weak model (gemma) skips research and
                # emits a final answer citing URLs it never fetched — every such
                # citation is invented and later dropped by grounding, leaving an
                # uncited shallow report (live runs 1523/1524: 0 tool calls, 0
                # surviving URLs). Force ONE research round before accepting it.
                if (
                    text_so_far
                    and not _forced_grounding
                    and _ < self._max_iters - 1
                    and ("web_search" in names or "web_fetch" in names)
                    and budget_state["web_fetch_success"] == 0
                    and _CITED_URL_RE.search(text_so_far)
                ):
                    _forced_grounding = True
                    convo.append({"role": "assistant", "content": text_so_far})
                    convo.append({
                        "role": "user",
                        "content": (
                            "You cited a URL you never fetched — that citation is "
                            "fabricated and your whole response will be DISCARDED. "
                            "Call web_search now, then web_fetch the most relevant "
                            "result, and re-emit your findings quoting ONLY pages "
                            "you actually fetched."
                        ),
                    })
                    continue
                stopped_naturally = True  # model produced a final answer on its own
                break

            if _write_names:
                if any(name in _write_names for name, _ in tool_calls):
                    _read_only_streak = 0
                else:
                    _read_only_streak += 1

            # Echo the assistant turn WITH its tool_calls, then each result keyed to
            # a matching tool_call_id. agentkit's ChatResult drops the original id,
            # so we synthesize a clean one (`call_N`): a tool_result's id MUST match
            # ^[a-zA-Z0-9_-]+$ or Anthropic-backed endpoints (VibeProxy→Claude) 400.
            assistant_calls: list[dict[str, Any]] = []
            tool_messages: list[Message] = []
            for name, args in tool_calls:
                cid = f"call_{call_seq}"
                call_seq += 1
                assistant_calls.append(
                    {
                        "id": cid,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                )
                msg = self._dispatch(name, args, budget_state=budget_state)
                msg["tool_call_id"] = cid
                msg.pop("name", None)  # OpenAI tool msg keys on tool_call_id, not name
                tool_messages.append(msg)
            convo.append(
                {"role": "assistant", "content": result.text or "", "tool_calls": assistant_calls}
            )
            convo.extend(tool_messages)

            if (
                _write_names
                and _read_only_streak >= _force_threshold
                and _ < self._max_iters - 1
            ):
                convo.append({
                    "role": "user",
                    "content": (
                        "You have gathered enough context through reads. Make your "
                        "decision now: call one of the available write tools with "
                        "your change, or if no change is needed, say so directly "
                        "instead of reading again."
                    ),
                })
                _read_only_streak = 0

            # Context compaction: prevent runaway context when fetch results
            # (up to 128K chars each) accumulate across iterations. compact()
            # with keep=0 summarizes everything into a brief transcript; we
            # re-inject the original task so the model never loses its goal.
            if self._context_compact and _convo_chars(convo) > _COMPACT_CHARS:
                try:
                    from agentkit.context import compact as _ak_compact
                    _task_msg = convo[0]
                    _cr = _ak_compact(convo, keep=0)
                    if _cr.text and _cr.est_tokens_after < _cr.est_tokens_before:
                        convo = [_task_msg, {"role": "user", "content": _cr.text}]
                except Exception:
                    pass  # best-effort; never break the tool loop

        text = (last.text if last else "") or ""
        # Force one tools-disabled synthesis turn when the model never produced a real
        # final answer. Two cases, BOTH bugs that left the artifact ungrounded:
        #   1. tool-calls-only final response  → last.text is empty.
        #   2. iteration exhaustion mid-tool_use → last.text is only the PREAMBLE
        #      ("I'll fetch the articles now…"), non-empty but containing no findings.
        # Anthropic's loop emits the synthesis only on the post-tool-result end_turn
        # pass; a worker capped at _max_iters never reaches it. Guarding on empty-text
        # alone (the old code) was bypassed by case 2's preamble, so haiku workers
        # fetched real pages then returned narration and emitted zero RESEARCH_FINDINGs.
        if (not text.strip() or not stopped_naturally) and convo:
            synthesis_convo = list(convo) + [{
                "role": "user",
                "content": (
                    "You have gathered sufficient information. Do not call any more tools "
                    "and do not say you need more information. Produce your final answer "
                    "for the task now, in EXACTLY the output format the task specified — if "
                    "it asked for specific blocks or fields, emit those and nothing else. "
                    "Do not restart, describe your process, or switch to a different format."
                ),
            }]
            synth = self._chat_inner(synthesis_convo, [])
            total_tokens += getattr(synth, "total_tokens", 0) or 0
            synth_text = (synth.text or "").strip()
            if synth_text:  # keep prior text if synthesis came back empty (no worse)
                text = synth_text
        remaining = list(last.tool_calls) if last else []
        # Strip executed tool calls from the surfaced result.
        remaining = [(n, a) for (n, a) in remaining if n not in names]
        return ChatResult(text=text, total_tokens=total_tokens, tool_calls=remaining)

    # -- rate-limit retry --------------------------------------------------

    def _chat_inner(self, convo: list, tools: list) -> "ChatResult":
        """Inner client call with exponential-backoff retry on rate-limit errors.

        Parallel STAR/MESH workers share one API key; if two concurrent calls
        hit the per-minute token limit, Anthropic returns 429 (or the client
        silently returns text=None).  Retry up to _MAX_RATE_RETRIES times with
        doubling waits (1 s, 2 s, 4 s) so the caller sees a real result instead
        of an empty string that derails synthesis.
        """
        result = ChatResult(text="", total_tokens=0, tool_calls=[])
        for attempt in range(_MAX_RATE_RETRIES + 1):
            try:
                result = self._inner.chat(convo, tools=tools)
            except Exception as exc:
                if attempt < _MAX_RATE_RETRIES and _is_rate_limit(exc):
                    time.sleep(2 ** attempt)
                    continue
                raise
            # Silent rate-limit: inner client swallowed the 429 and returned
            # text=None with no tool_calls — nothing useful came back.
            if result.text is None and not (getattr(result, "tool_calls", None) or []):
                if attempt < _MAX_RATE_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
            return result
        return result  # final attempt — return whatever we have

    # -- dispatch ----------------------------------------------------------

    def _dispatch(
        self,
        name: str,
        args: dict[str, Any],
        *,
        budget_state: dict[str, int] | None = None,
    ) -> Message:
        """Route a tool call to its executor; emit events; return the tool msg."""
        step_id = self._step_id_getter()
        if self._on_tool_call:
            self._on_tool_call(step_id, name, dict(args))
        if name == "web_search" and budget_state is not None:
            if self._max_searches is not None and budget_state["web_search"] >= self._max_searches:
                return self._budget_rejection(step_id, name, self._max_searches, "search calls")
            budget_state["web_search"] += 1
        if name == "web_fetch" and budget_state is not None:
            if (
                self._max_successful_fetches is not None
                and budget_state["web_fetch_success"] >= self._max_successful_fetches
            ):
                return self._budget_rejection(
                    step_id, name, self._max_successful_fetches, "successful fetches"
                )
        if name == "web_search":
            msg = self._run_search(step_id, args)
            # Weak-model backstop (root cause of the uncited/shallow reports and
            # the missing evidence/ dossier): gemma searches but then cites
            # fabricated URLs instead of fetching the results — prefetch failure
            # drops every citation. Deterministically fetch the top result pages
            # HERE and splice their content into the search tool message, so the
            # model's next turn holds real page text under real URLs (and the
            # fetch cache lets grounding + the evidence dossier verify them).
            if self._auto_fetch_top_results > 0:
                msg = self._auto_fetch_after_search(step_id, msg, budget_state)
            return msg
        if name == "web_fetch":
            msg = self._run_fetch(step_id, args)
            if budget_state is not None and self._tool_message_success(msg):
                budget_state["web_fetch_success"] += 1
            return msg
        if name == "read_file":
            return self._run_read(step_id, args)
        if name == "search_evidence":
            return self._run_search_evidence(step_id, args)
        if name == "write_file":
            return self._run_write(step_id, args)
        if name == "edit_file":
            return self._run_edit(step_id, args)
        if name == "glob":
            return self._run_glob(step_id, args)
        if name == "read_artifact":
            return self._run_read_artifact(step_id, args)
        if name == "patch_artifact":
            return self._run_patch_artifact(step_id, args)
        # Unknown tool: report it back so the model can recover.
        return self._tool_message(name, {"error": f"unknown tool {name!r}"})

    def _auto_fetch_after_search(
        self,
        step_id: str,
        search_msg: Message,
        budget_state: dict[str, int] | None,
    ) -> Message:
        """Fetch the top N result pages of a web_search and splice the content
        into the search tool message.

        Runs through ``_run_fetch`` so every page is cached (grounding +
        evidence dossier see it), emitted as a tool event (TOOLS panel), and
        counted against the fetch budget. Fail-open per page."""
        try:
            payload = json.loads(str(search_msg.get("content") or "{}"))
        except Exception:  # noqa: BLE001 - malformed search message → nothing to fetch
            return search_msg
        results = payload.get("results") or []
        fetched: list[dict[str, str]] = []
        for r in results[: self._auto_fetch_top_results]:
            url = str((r or {}).get("url", "")).strip()
            if not url:
                continue
            if (
                budget_state is not None
                and self._max_successful_fetches is not None
                and budget_state["web_fetch_success"] >= self._max_successful_fetches
            ):
                break
            if self._on_tool_call:
                self._on_tool_call(step_id, "web_fetch", {"url": url, "auto": True})
            msg = self._run_fetch(step_id, {"url": url})
            if not self._tool_message_success(msg):
                continue
            if budget_state is not None:
                budget_state["web_fetch_success"] += 1
            try:
                content = str(json.loads(str(msg.get("content") or "{}")).get("content", ""))
            except Exception:  # noqa: BLE001
                content = ""
            if content:
                fetched.append({"url": url, "content": content[: self._auto_fetch_page_chars]})
        if fetched:
            payload["fetched_pages"] = fetched
            payload["notice"] = (
                (payload.get("notice") or "")
                + " Top results were fetched for you — quote and cite ONLY these"
                " fetched_pages URLs in your findings."
            ).strip()
            return self._tool_message("web_search", payload)
        return search_msg

    def _budget_rejection(self, step_id: str, name: str, limit: int, label: str) -> Message:
        notice = (
            f"{name} {label} budget exhausted ({limit}). "
            "Use the gathered evidence and produce the requested final answer."
        )
        self._emit_result(step_id, name, notice, 0, notice, rejected=True)
        return self._tool_message(name, {"error": notice, "budget_exhausted": True})

    @staticmethod
    def _tool_message_success(msg: Message) -> bool:
        try:
            payload = json.loads(str(msg.get("content") or "{}"))
        except Exception:  # noqa: BLE001 - malformed tool messages are not successes
            return False
        return "error" not in payload

    def _emit_result(
        self,
        step_id: str,
        name: str,
        summary: str,
        n: int,
        notice: str,
        rejected: bool = False,
    ) -> None:
        if self._on_tool_result:
            self._on_tool_result(step_id, name, summary, n, notice, rejected)

    @staticmethod
    def _tool_message(name: str, payload: dict[str, Any]) -> Message:
        return {"role": "tool", "name": name, "content": json.dumps(payload, default=str)}

    # -- web_search --------------------------------------------------------

    def _run_search(self, step_id: str, args: dict[str, Any]) -> Message:
        """Execute one web_search, emit a result event, return the tool message."""
        query = str(args.get("query", "")).strip()
        n = int(args.get("results", _DEFAULT_RESULTS) or _DEFAULT_RESULTS)
        results, notice = self._search(query, n)
        payload = [r.to_dict() if hasattr(r, "to_dict") else r for r in results]
        summary = "; ".join(
            f"{p.get('title', '')} ({p.get('url', '')})" for p in payload[:3]
        )
        self._emit_result(step_id, "web_search", summary, len(payload), notice)
        return self._tool_message("web_search", {"results": payload, "notice": notice})

    # -- web_fetch ---------------------------------------------------------

    def _run_fetch(self, step_id: str, args: dict[str, Any]) -> Message:
        """Fetch one page to markdown, emit a result event, return the tool message.

        A missing scrapling CLI or a per-page failure (ok=False) is non-fatal: the
        tool message carries an ``error`` and the event a ``notice``, so the loop
        continues. ``rejected`` stays False — a fetch failure is degradation, not a
        jail rejection. Content is capped at ``_MAX_FETCH_CHARS`` (truncation noted).
        """
        url = str(args.get("url", "")).strip()
        selector = args.get("selector")
        selector = str(selector).strip() if selector else None
        _cache_key = f"{url}|{selector or ''}"
        if _cache_key in _fetch_cache:
            content, n_bytes = _fetch_cache[_cache_key]
            error = ""
        else:
            # Block concurrent threads from fetching the same URL simultaneously.
            # The first caller fetches; concurrent callers wait and share the result.
            content, n_bytes, error = self._in_flight.get_or_fetch(
                _cache_key, lambda: self._fetch(url, selector)
            )
            if not error:
                _fetch_cache[_cache_key] = (content, n_bytes)
        if error:
            self._emit_result(step_id, "web_fetch", f"fetch failed: {error}", 0, error)
            return self._tool_message("web_fetch", {"url": url, "error": error})
        truncated = len(content) > _MAX_FETCH_CHARS
        if truncated:
            content = content[:_MAX_FETCH_CHARS]
        host = _host_of(url)
        summary = f"fetched {_fmt_bytes(n_bytes)} from {host}"
        if truncated:
            summary += f" (truncated to {_fmt_bytes(_MAX_FETCH_CHARS)})"
        self._emit_result(step_id, "web_fetch", summary, 1, "")
        return self._tool_message(
            "web_fetch", {"url": url, "content": content, "bytes": n_bytes, "truncated": truncated}
        )

    def _fetch(self, url: str, selector: str | None) -> tuple[str, int, str]:
        """Run the injected fetch_fn (or web_toolkit.web_fetch); never raises.

        Returns ``(content, bytes, error)``. A ``FetchError`` (scrapling missing),
        an import failure, any other exception, or a ``FetchResult`` with
        ``ok=False`` all collapse to ``("", 0, "<reason>")`` so the loop continues.
        """
        fn = self._fetch_fn
        if fn is None:
            try:
                from web_toolkit import web_fetch as fn  # type: ignore
            except Exception as exc:  # noqa: BLE001
                return "", 0, f"web_fetch unavailable: {exc}"
        try:
            res = fn(url, selector=selector)
        except Exception as exc:  # noqa: BLE001 - FetchError (scrapling missing) / backend down
            return "", 0, f"web_fetch degraded: {exc}"
        if not getattr(res, "ok", False):
            return "", 0, getattr(res, "error", "") or "fetch failed"
        content = getattr(res, "content", "") or ""
        n_bytes = getattr(res, "bytes", 0) or len(content.encode("utf-8"))
        return content, n_bytes, ""

    # -- file tools (jailed) -----------------------------------------------

    def _run_read(self, step_id: str, args: dict[str, Any]) -> Message:
        """read_file inside the workspace jail; an escape → error result + notice."""
        path = str(args.get("path", ""))
        if self._workspace is None:
            self._emit_result(step_id, "read_file", "no workspace", 0, "tools disabled")
            return self._tool_message("read_file", {"error": "no workspace configured"})
        try:
            text, n_bytes = self._workspace.read(path)
        except WorkspaceError as exc:
            self._emit_result(
                step_id, "read_file", f"rejected: {exc}", 0, str(exc), rejected=True
            )
            return self._tool_message("read_file", {"error": str(exc)})
        summary = f"read {_fmt_bytes(n_bytes)} from {path}"
        self._emit_result(step_id, "read_file", summary, 1, "")
        return self._tool_message("read_file", {"path": path, "content": text, "bytes": n_bytes})

    def _run_search_evidence(self, step_id: str, args: dict[str, Any]) -> Message:
        """Grep the session's fetched ``evidence/*.md`` files (jailed to the workspace).

        Returns up to ``_MAX_EVIDENCE_MATCHES`` matches, each ``{file, line,
        context}`` with a few lines around the hit (``grep -C``) — never a whole
        file, so a local model's context is not flooded. Reads go through the
        workspace jail; the manifest ``fetched-sources.json`` (no content) is
        skipped. An empty query or a missing evidence dir returns cleanly.

        The optional ``glob`` arg widens which files are scanned (e.g.
        ``'sections/*.md'`` or ``'**/*.md'``); omitted it defaults to
        ``evidence/*.md`` — the original behaviour, so existing callers are unchanged.
        """
        query = str((args or {}).get("query", "")).strip()
        if self._workspace is None:
            self._emit_result(step_id, "search_evidence", "no workspace", 0, "tools disabled")
            return self._tool_message("search_evidence", {"error": "no workspace configured"})
        if not query:
            return self._tool_message("search_evidence", {"error": "query must not be empty"})
        try:
            ctx = int((args or {}).get("context_lines", _EVIDENCE_CONTEXT_LINES) or _EVIDENCE_CONTEXT_LINES)
        except (TypeError, ValueError):
            ctx = _EVIDENCE_CONTEXT_LINES
        ctx = max(0, min(ctx, 10))
        scope = str((args or {}).get("glob", "")).strip() or _DEFAULT_EVIDENCE_GLOB
        root = self._workspace.root
        try:
            files = sorted(p for p in root.glob(scope) if p.is_file())
        except (OSError, ValueError):
            files = []
        # Tokenized matching: a single keyword keeps the fast per-line substring
        # path; a multi-word query matches when a majority of its tokens co-occur
        # within one context window (Bug A: contiguous substrings like
        # "methodology and structure" almost never sit on one raw line).
        tokens = [t for t in query.lower().split() if t]
        # ceil(0.6 * n): 2-of-2, 2-of-3, 3-of-4, 3-of-5 — tolerant of one absent
        # or misspelled token without matching on a single incidental word.
        need = max(1, (3 * len(tokens) + 4) // 5)
        matches: list[dict[str, Any]] = []
        for path in files:
            if len(matches) >= _MAX_EVIDENCE_MATCHES:
                break
            try:
                rel = str(path.relative_to(root))
                text, _n = self._workspace.read(rel)
            except (WorkspaceError, ValueError):
                continue
            # Bug B: attach the source URL so a hit is citable without fabrication.
            # Evidence files are written as "URL: <url>\n\n<content>" (runner.py).
            first = text.splitlines()[0] if text else ""
            url = first[len("URL:"):].strip() if first.startswith("URL:") else ""
            lines = text.splitlines()
            i = 0
            while i < len(lines):
                if len(matches) >= _MAX_EVIDENCE_MATCHES:
                    break
                line_l = lines[i].lower()
                lo = max(0, i - ctx)
                hi = min(len(lines), i + ctx + 1)
                if len(tokens) <= 1:
                    # Single keyword: per-line substring (anchor stays line-precise).
                    hit = bool(tokens) and tokens[0] in line_l
                    advance = 1
                else:
                    # Multi-word: anchor a line carrying >=1 token, require a
                    # majority of distinct tokens somewhere in its window.
                    window_l = "\n".join(lines[lo:hi]).lower()
                    hit = any(t in line_l for t in tokens) and (
                        sum(1 for t in set(tokens) if t in window_l) >= need
                    )
                    advance = hi - i if hit else 1  # skip past window to avoid dupes
                if not hit:
                    i += 1
                    continue
                context = "\n".join(lines[lo:hi])
                if len(context) > _MAX_EVIDENCE_CONTEXT_CHARS:
                    context = context[:_MAX_EVIDENCE_CONTEXT_CHARS].rstrip() + " [truncated]"
                matches.append(
                    {"file": rel, "line": i + 1, "context": context, "url": url}
                )
                i += advance
        summary = f"{len(matches)} match(es) for {query!r} in {len(files)} file(s)"
        self._emit_result(step_id, "search_evidence", summary, len(matches), "")
        return self._tool_message("search_evidence", {"query": query, "matches": matches})

    def _run_write(self, step_id: str, args: dict[str, Any]) -> Message:
        """write_file inside the workspace jail; an escape → error result + notice.

        The containment check runs before any byte is written, so a rejected
        write leaves the filesystem untouched.
        """
        path = str(args.get("path", ""))
        content = str(args.get("content", ""))
        if self._workspace is None:
            self._emit_result(step_id, "write_file", "no workspace", 0, "tools disabled")
            return self._tool_message("write_file", {"error": "no workspace configured"})
        try:
            n_bytes, shown = self._workspace.write(path, content)
        except WorkspaceError as exc:
            self._emit_result(
                step_id, "write_file", f"rejected: {exc}", 0, str(exc), rejected=True
            )
            return self._tool_message("write_file", {"error": str(exc)})
        summary = f"wrote {_fmt_bytes(n_bytes)} to {shown}"
        self._emit_result(step_id, "write_file", summary, 1, "")
        return self._tool_message("write_file", {"path": shown, "bytes": n_bytes})

    def _run_edit(self, step_id: str, args: dict[str, Any]) -> Message:
        """edit_file inside the workspace jail: unique find/replace, no OCC.

        ``old_string`` must occur exactly once (0 → "not found"; >1 without
        ``replace_all`` → "not unique"). Every failure is a returned error result,
        never a raise (mirrors read_file/write_file). Reads then writes back through
        the jail, so an escaping path touches nothing on disk.
        """
        path = str(args.get("file_path", ""))
        old = str(args.get("old_string", ""))
        new = str(args.get("new_string", ""))
        replace_all = bool(args.get("replace_all", False))
        if self._workspace is None:
            self._emit_result(step_id, "edit_file", "no workspace", 0, "tools disabled")
            return self._tool_message("edit_file", {"error": "no workspace configured"})
        if not old:
            return self._tool_message("edit_file", {"error": "old_string must not be empty"})
        try:
            text, n_bytes = self._workspace.read(path, max_bytes=_EDIT_FILE_MAX_BYTES)
        except WorkspaceError as exc:
            self._emit_result(step_id, "edit_file", f"rejected: {exc}", 0, str(exc), rejected=True)
            return self._tool_message("edit_file", {"error": str(exc)})
        # The read is capped at _EDIT_FILE_MAX_BYTES; a read AT/OVER the cap is
        # indistinguishable from a truncated one, so editing it would risk a
        # truncated write-back. Refuse rather than corrupt (data-loss guard).
        if n_bytes >= _EDIT_FILE_MAX_BYTES:
            notice = f"file too large to edit safely (>={_EDIT_FILE_MAX_BYTES}B)"
            self._emit_result(step_id, "edit_file", notice, 0, notice)
            return self._tool_message("edit_file", {"error": notice})
        count = text.count(old)
        if count == 0:
            notice = f"old_string not found in {path}"
            self._emit_result(step_id, "edit_file", notice, 0, notice)
            return self._tool_message("edit_file", {"error": notice})
        if count > 1 and not replace_all:
            notice = f"old_string not unique, {count} occurrences found (use replace_all=true)"
            self._emit_result(step_id, "edit_file", notice, 0, notice)
            return self._tool_message("edit_file", {"error": notice})
        edited = text.replace(old, new)
        # Write-side guard: a replacement that inflates the file past the cap would
        # persist an oversized file the read-side guard could then never re-open to
        # edit. Refuse before touching disk (same studio-local threshold as the read).
        if len(edited.encode("utf-8")) > _EDIT_FILE_MAX_BYTES:
            notice = f"edit would exceed max file size (>{_EDIT_FILE_MAX_BYTES}B)"
            self._emit_result(step_id, "edit_file", notice, 0, notice)
            return self._tool_message("edit_file", {"error": notice})
        try:
            n_written, shown = self._workspace.write(path, edited)
        except WorkspaceError as exc:
            self._emit_result(step_id, "edit_file", f"rejected: {exc}", 0, str(exc), rejected=True)
            return self._tool_message("edit_file", {"error": str(exc)})
        replaced = count if replace_all else 1
        summary = f"edited {shown} ({replaced} replacement{'s' if replaced != 1 else ''})"
        self._emit_result(step_id, "edit_file", summary, replaced, "")
        return self._tool_message(
            "edit_file", {"path": shown, "replacements": replaced, "bytes": n_written}
        )

    def _run_glob(self, step_id: str, args: dict[str, Any]) -> Message:
        """List workspace files matching a glob pattern (jailed to the workspace).

        Returns up to ``_MAX_GLOB_RESULTS`` workspace-relative paths. An optional
        ``path`` scopes to a subdirectory (jail-checked). Results are relative to the
        workspace root and any hit that resolves outside it is dropped (never leaks an
        absolute host path), so a pattern that reaches outside yields nothing.
        """
        pattern = str(args.get("pattern", "")).strip()
        subdir = str(args.get("path", "") or "").strip()
        if self._workspace is None:
            self._emit_result(step_id, "glob", "no workspace", 0, "tools disabled")
            return self._tool_message("glob", {"error": "no workspace configured"})
        if not pattern:
            return self._tool_message("glob", {"error": "pattern must not be empty"})
        root = self._workspace.root
        base = root
        if subdir:
            try:
                base = self._workspace._resolve_inside(subdir)
            except WorkspaceError as exc:
                self._emit_result(step_id, "glob", f"rejected: {exc}", 0, str(exc), rejected=True)
                return self._tool_message("glob", {"error": str(exc)})
        try:
            hits = sorted(p for p in base.glob(pattern) if p.is_file())
        except (OSError, ValueError) as exc:
            return self._tool_message("glob", {"error": f"glob failed: {exc}"})
        rels: list[str] = []
        for p in hits:
            try:
                rels.append(str(p.resolve().relative_to(root)))
            except ValueError:
                continue  # jail-safe: silently drop anything outside the workspace root
        truncated = len(rels) > _MAX_GLOB_RESULTS
        rels = rels[:_MAX_GLOB_RESULTS]
        summary = f"{len(rels)} match(es) for {pattern!r}"
        if truncated:
            summary += f" (capped at {_MAX_GLOB_RESULTS})"
        self._emit_result(step_id, "glob", summary, len(rels), "")
        return self._tool_message("glob", {"pattern": pattern, "matches": rels, "truncated": truncated})

    # -- artifact tools (OCC + file locking) ------------------------------

    def _run_read_artifact(self, step_id: str, args: dict[str, Any]) -> Message:
        """Section-scoped artifact read under the per-path lock (§11.4 token-cap).

        No ``section`` arg -> a cheap section INDEX ([{section, hash, chars}]); a
        ``section`` arg -> that one section's body + hash. The full document is
        never dumped at once — that was the read_artifact x26 input-token bomb.
        """
        if self._artifact_path is None:
            return self._tool_message("read_artifact", {"error": "no artifact_path configured"})
        try:
            result = _occ_read(self._artifact_path)
        except OSError as exc:
            return self._tool_message("read_artifact", {"error": str(exc)})
        sections = _split_sections(result.content)
        requested = str((args or {}).get("section", "")).strip()
        if requested:
            want = requested.lstrip("# ").strip().lower()
            for head, body in sections:
                if head == requested or head.lstrip("# ").strip().lower() == want:
                    self._emit_result(
                        step_id, "read_artifact",
                        f"read section {head!r} ({len(body)} chars)", len(body), "",
                    )
                    return self._tool_message(
                        "read_artifact",
                        {"section": head, "content": body, "hash": _section_hash(body)},
                    )
            return self._tool_message(
                "read_artifact",
                {"error": f"section {requested!r} not found", "available": [h for h, _ in sections]},
            )
        index = [{"section": h, "hash": _section_hash(b), "chars": len(b)} for h, b in sections]
        self._emit_result(
            step_id, "read_artifact",
            f"index: {len(index)} sections (doc {len(result.content)} chars, not dumped)",
            len(index), "",
        )
        return self._tool_message("read_artifact", {"index": index, "doc_hash": result.hash})

    def _run_patch_artifact(self, step_id: str, args: dict[str, Any]) -> Message:
        """Atomically apply a find/replace patch with OCC hash check (via agentkit.artifacts.occ).

        Returns success+new_hash on match, or hash_mismatch+fresh-content on conflict.
        The caller (worker) uses the returned content+hash to re-analyze and retry.
        """
        if self._artifact_path is None:
            return self._tool_message("patch_artifact", {"error": "no artifact_path configured"})
        find = str(args.get("find", ""))
        replace = str(args.get("replace", ""))
        expected_hash = str(args.get("expected_hash", ""))
        if not find:
            return self._tool_message("patch_artifact", {"error": "find must not be empty"})
        try:
            result = _occ_patch(self._artifact_path, find, replace, expected_hash)
        except OSError as exc:
            return self._tool_message("patch_artifact", {"error": str(exc)})
        if not result.success:
            notice = "conflict" if result.reason == "hash_mismatch" else "not found"
            self._emit_result(step_id, "patch_artifact", f"{result.reason} — retry", 0, notice)
            payload: dict[str, Any] = {"success": False, "reason": result.reason, "new_hash": result.new_hash}
            if result.content is not None:
                payload["content"] = result.content
            if result.reason == "find_not_matched":
                payload["hint"] = f"'{find[:60]}' not found in artifact"
            return self._tool_message("patch_artifact", payload)
        self._emit_result(step_id, "patch_artifact", f"patched {len(find)} chars hash={result.new_hash}", 1, "")
        return self._tool_message("patch_artifact", {"success": True, "new_hash": result.new_hash})

    def _search(self, query: str, n: int) -> tuple[list[Any], str]:
        """Run the injected search_fn (or web_toolkit.web_search); never raises.

        Returns ``(results, notice)``. A ``SearchError`` / import failure yields
        ``([], "<reason>")`` so the tool loop continues with an empty result.
        """
        fn = self._search_fn
        if fn is None:
            try:
                from web_toolkit import web_search as fn  # type: ignore
            except Exception as exc:  # noqa: BLE001
                return [], f"web_search unavailable: {exc}"
        try:
            return list(fn(query, results=n)), ""
        except Exception as exc:  # noqa: BLE001 - SearchError / backend down
            return [], f"web_search degraded: {exc}"


def _fmt_bytes(n: int) -> str:
    """Human-readable byte count for tool-result summaries (e.g. '1.2KB')."""
    if n < 1024:
        return f"{n}B"
    return f"{n / 1024:.1f}KB"


def _host_of(url: str) -> str:
    """Host portion of a URL for tool-result summaries (full url stays out of the
    surfaced summary). Falls back to the raw string if it does not parse."""
    from urllib.parse import urlparse

    try:
        return urlparse(url).netloc or url
    except Exception:  # noqa: BLE001
        return url
