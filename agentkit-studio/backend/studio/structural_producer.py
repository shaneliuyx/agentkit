"""studio.structural_producer — L0: deterministic finalize-time structural producer.

PLAN-codebase-simplification.md §8: every existing structural-content path (the
editor's diagram/code retry) runs generation BEHIND a conjunction of score/weak-count
accept gates the rubric was never designed to reward — five independent rejection
points that "almost never ship" a diagram or code block. Meanwhile
``rebuild_references_section`` (``studio.artifact_text``) — a deterministic
build -> validate -> insert -> fail-open finalize step judged on STRUCTURAL VALIDITY
ONLY — has worked on its first live run and every run since.

L0 applies the same pattern to the two structural requirement shapes
``studio.requirement_compliance`` already detects deterministically (code-shaped,
diagram-shaped): for each still-unmet branch, produce the content and splice it in
unconditionally, gated only on structural validity (fences balanced, no citation
lost, lint non-regression) — never on score or weakness-count movement. Burden of
proof moves from producer to rejector. Fail-open throughout: any error returns the
original text unchanged.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from studio.artifact_text import _norm_urls
from studio.requirement_compliance import (
    _CODE_SHAPED_RE,
    _DIAGRAM_SHAPED_RE,
    _MERMAID_BLOCK_RE,
    _has_code_fence,
)


def _dbg(msg: str) -> None:
    """Local copy of ``studio.runner._dbg`` (importing back would be circular)."""
    path = os.environ.get("OMC_THROUGHPUT_DEBUG")
    if not path:
        return
    try:
        with open(path, "a") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Acceptance gate — structural validity only (mirrors rebuild_references_section).
# ---------------------------------------------------------------------------

def _fences_balanced(text: str) -> bool:
    return len(re.findall(r"(?m)^\s*```", text or "")) % 2 == 0


def _accept(before: str, after: str) -> bool:
    """Structural-validity-only acceptance: fences balanced, no URL dropped, lint
    count no worse than before. NO score/weak-count condition — see module docstring."""
    if after == before:
        return True
    if not _fences_balanced(after):
        return False
    if not _norm_urls(before) <= _norm_urls(after):
        return False
    from studio.artifact_lint import lint_artifact

    return len(lint_artifact(after)) <= len(lint_artifact(before))


# ---------------------------------------------------------------------------
# Placement — shared by the code producer (diagram placement is owned by
# diagram_render.insert_diagram_block, reused as-is).
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_REFERENCES_HEADING_RE = re.compile(r"(?im)^#{1,6}\s+(references|sources|bibliography|citations)\b")


def _flatten_titles(dyn_sections: dict | None) -> list[str]:
    titles: list[str] = []
    for v in (dyn_sections or {}).values():
        if isinstance(v, str):
            titles.append(v)
        elif isinstance(v, (list, tuple, set)):
            titles.extend(str(t) for t in v)
    return titles


def _insert_under_section(text: str, block: str, dyn_sections: dict | None) -> str:
    """Insert *block* after the best-matching heading: a planner-designated
    code-shaped section name, else 'Evidence and Analysis', else before
    References, else end-of-document. Mirrors ``diagram_render.insert_diagram_block``."""
    lines = text.splitlines()
    candidates = [t for t in _flatten_titles(dyn_sections) if _CODE_SHAPED_RE.search(t)]
    for i, ln in enumerate(lines):
        m = _HEADING_RE.match(ln)
        if m and any(m.group(2).strip().lower() == c.strip().lower() for c in candidates):
            return "\n".join(lines[: i + 1] + ["", block, ""] + lines[i + 1 :])
    for i, ln in enumerate(lines):
        m = _HEADING_RE.match(ln)
        if m and "evidence" in m.group(2).lower() and "analysis" in m.group(2).lower():
            return "\n".join(lines[: i + 1] + ["", block, ""] + lines[i + 1 :])
    for i, ln in enumerate(lines):
        if _REFERENCES_HEADING_RE.match(ln):
            return "\n".join(lines[:i] + [block, ""] + lines[i:])
    sep = "" if text.endswith("\n") else "\n"
    return f"{text}{sep}\n{block}\n"


# ---------------------------------------------------------------------------
# Code producer — real grounded excerpt from evidence/, else one grounded LLM call.
# ---------------------------------------------------------------------------

_URL_LINE_RE = re.compile(r"\AURL:\s*(\S+)")
#: Lines carrying a real code token; a contiguous run of these (plus blank/indented
#: continuation lines) is the excerpt candidate.
_CODE_TOKEN_RE = re.compile(
    r"[{};]|=>|\bimport\s|\bdef\s|\bconst\s|\bfunction\s|\bclass\s|\breturn\s"
)
_MIN_EXCERPT_LINES = 10
_MAX_EXCERPT_LINES = 30


def _looks_like_continuation(line: str) -> bool:
    stripped = line.strip()
    return not stripped or bool(_CODE_TOKEN_RE.search(line)) or line[:1] in (" ", "\t")


#: Punctuation that only shows up densely in real code, never in prose that merely
#: mentions "the function of the ribosome" or "this class of algorithms".
_HARD_CODE_CHAR_RE = re.compile(r"[{}=();]")
_MIN_DENSITY = 0.6
_MIN_HARD_LINES = 3


def _is_code_dense(excerpt: str) -> bool:
    """Reject a candidate slice that is prose loosely matching the anchor/continuation
    heuristic (indentation + incidental keywords) rather than actual code: require most
    non-blank lines to carry a code token AND enough lines with real code punctuation."""
    lines = [ln for ln in excerpt.splitlines() if ln.strip()]
    if not lines:
        return False
    dense = sum(1 for ln in lines if _CODE_TOKEN_RE.search(ln))
    hard = sum(1 for ln in lines if _HARD_CODE_CHAR_RE.search(ln))
    return dense / len(lines) >= _MIN_DENSITY and hard >= _MIN_HARD_LINES


def _best_code_excerpt(content: str) -> str | None:
    """Longest contiguous ~10-30 line run anchored on a code-token line, accepted
    only if it actually reads as code (see ``_is_code_dense``)."""
    lines = content.splitlines()
    best_start, best_len = -1, 0
    i, n = 0, len(lines)
    while i < n:
        if _CODE_TOKEN_RE.search(lines[i]):
            j = i
            while j < n and _looks_like_continuation(lines[j]):
                j += 1
            k = j
            while k > i and not lines[k - 1].strip():
                k -= 1  # trim trailing blank lines
            if k - i > best_len:
                best_start, best_len = i, k - i
            i = j
        else:
            i += 1
    if best_len < _MIN_EXCERPT_LINES:
        return None
    end = min(best_start + _MAX_EXCERPT_LINES, best_start + best_len)
    excerpt = "\n".join(lines[best_start:end]).strip()
    return excerpt if excerpt and _is_code_dense(excerpt) else None


def _guess_lang(snippet: str) -> str:
    if re.search(r"^\s*def\s+\w+\(|^\s*import\s+\w+\s*$", snippet, re.MULTILINE):
        return "python"
    if re.search(r":\s*(string|number|boolean|void|any)\b|\binterface\s+\w+", snippet):
        return "ts"
    if re.search(r"\bconst\s|\bfunction\s|=>", snippet):
        return "js"
    return "text"


def _iter_evidence_files(evidence_dir: Path | None) -> list[Path]:
    if evidence_dir is None or not evidence_dir.is_dir():
        return []
    return sorted(evidence_dir.glob("source-*.md"))


def _find_evidence_code(evidence_dir: Path | None) -> tuple[str, str] | None:
    """``(url, excerpt)`` from the first evidence file with a groundable code block."""
    for path in _iter_evidence_files(evidence_dir):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _URL_LINE_RE.match(content)
        url = m.group(1) if m else ""
        body = content[m.end() :] if m else content
        excerpt = _best_code_excerpt(body)
        if excerpt and url:
            return url, excerpt
    return None


def _llm_code_from_evidence(client: Any, evidence_dir: Path | None) -> str | None:
    excerpts = []
    for path in _iter_evidence_files(evidence_dir)[:3]:
        try:
            excerpts.append(path.read_text(encoding="utf-8")[:2000])
        except OSError:
            continue
    if not excerpts:
        return None
    prompt = (
        "Using ONLY the evidence excerpts below, produce a minimal runnable code "
        "example grounded in what they describe. Output ONLY a single fenced code "
        "block (```language ... ```), nothing else.\n\n" + "\n\n---\n\n".join(excerpts)
    )
    try:
        reply = client.chat([{"role": "user", "content": prompt}])
        text = str(getattr(reply, "text", "") or "")
    except Exception:  # noqa: BLE001 — a bad LLM call must never break finalization
        return None
    return text.strip() if "```" in text else None


def _produce_code(text: str, evidence_dir: Path | None, client: Any, dyn_sections: dict | None) -> str | None:
    found = _find_evidence_code(evidence_dir)
    if found:
        url, excerpt = found
        block = (
            f"Example from the project source ([source]({url})):\n\n"
            f"```{_guess_lang(excerpt)}\n{excerpt}\n```"
        )
    else:
        generated = _llm_code_from_evidence(client, evidence_dir)
        if not generated:
            return None
        block = generated
    return _insert_under_section(text, block, dyn_sections)


# ---------------------------------------------------------------------------
# Diagram producer — the existing A2 machinery, unmodified.
# ---------------------------------------------------------------------------


def _produce_diagram(text: str, client: Any) -> str | None:
    from studio.diagram_render import (
        build_components_prompt,
        insert_diagram_block,
        render_grounded_diagram,
    )

    try:
        reply = client.chat([{"role": "user", "content": build_components_prompt(text)}])
        raw = str(getattr(reply, "text", "") or "")
    except Exception:  # noqa: BLE001 — a bad LLM call must never break finalization
        return None
    body = render_grounded_diagram(raw, text)
    if not body:
        return None
    return insert_diagram_block(text, body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def produce_missing_structures(
    artifact_text: str,
    requirements: list[list[str]],
    *,
    client: Any,
    evidence_dir: Path | None,
    dyn_sections: dict | None = None,
) -> tuple[str, dict]:
    """For each still-unmet code/diagram-shaped requirement branch, produce and
    splice in the content deterministically. Returns ``(new_text, stats)``;
    fail-open on any exception (returns *artifact_text* unchanged)."""
    stats: dict[str, Any] = {"code": "skipped", "diagram": "skipped", "reason": {}}
    cur = artifact_text or ""
    if not requirements or not cur.strip():
        _dbg(f"structural_producer: no-op (requirements={bool(requirements)} text={bool(cur.strip())})")
        return artifact_text, stats
    try:
        code_done = _has_code_fence(cur)
        diagram_done = bool(_MERMAID_BLOCK_RE.search(cur))
        for branches in requirements:
            code_branches = [b for b in branches if _CODE_SHAPED_RE.search(b)]
            diagram_branches = [b for b in branches if _DIAGRAM_SHAPED_RE.search(b)]
            if not code_branches and not diagram_branches:
                continue
            if (code_branches and code_done) or (diagram_branches and diagram_done):
                continue  # OR group already satisfied by another branch

            produced = False
            if code_branches and not code_done:
                if evidence_dir is None and client is None:
                    stats["code"] = "skipped"
                    stats["reason"]["code"] = "no evidence_dir and no client"
                else:
                    new_text = _produce_code(cur, evidence_dir, client, dyn_sections)
                    if new_text is not None and _accept(cur, new_text):
                        cur, code_done, produced = new_text, True, True
                        stats["code"] = "inserted"
                    else:
                        stats["code"] = "failed"
                        stats["reason"]["code"] = (
                            "acceptance gate rejected insertion" if new_text is not None
                            else "no groundable code source (evidence/LLM)"
                        )

            if not produced and diagram_branches and not diagram_done:
                if client is None:
                    stats["diagram"] = "skipped"
                    stats["reason"]["diagram"] = "no client"
                else:
                    new_text = _produce_diagram(cur, client)
                    if new_text is not None and _accept(cur, new_text):
                        cur, diagram_done = new_text, True
                        stats["diagram"] = "inserted"
                    else:
                        stats["diagram"] = "failed"
                        stats["reason"]["diagram"] = (
                            "acceptance gate rejected insertion" if new_text is not None
                            else "renderer produced no groundable diagram"
                        )
    except Exception as exc:  # noqa: BLE001 — must never break finalization
        _dbg(f"structural_producer: EXCEPTION {exc!r} — fail-open, returning original")
        return artifact_text, {"code": "skipped", "diagram": "skipped", "reason": {"exception": repr(exc)}}
    _dbg(f"structural_producer: {stats}")
    return cur, stats
