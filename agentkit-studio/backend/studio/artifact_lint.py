"""studio.artifact_lint — deterministic content-validity checks (DESIGN §14.6).

The hill-climb loop optimizes a GAP metric (missing sections, missing sources) and
is structurally blind to MALFORMED content: a broken mermaid edge or a truncated
code block introduced once survives every epoch, because the weakness miner only
names what is *absent*, never what is *wrong*. `accept_rewrite` already permits an
in-place repair — what was missing is the SIGNAL.

`lint_artifact` supplies that signal: cheap, offline, deterministic checks that
return weakness strings in the same shape the miner emits (``[section] message``),
so they seed the next run's constraints and the reducer is told to fix them. No
mermaid JS engine — it targets the concrete, high-frequency malformations seen in
real runs, not a full grammar.
"""
from __future__ import annotations

import re
import ast

from studio.textutil import FENCE_LINE_RE, fence_rest_contaminated

#: A mermaid edge label `|...|` must attach to a link operator (``-->``, ``---``,
#: ``-.->``, ``==>`` …). When the char immediately before the opening ``|`` is a
#: node-identifier char, the node is glued straight to the label — the exact
#: "got 'PIPE'" parse error (``ToolSelector|Read|`` instead of ``-->|Read|``).
_MERMAID_GLUED_EDGE = re.compile(r"[\w\)\]]\|[^|\n]*\|")


def _section_at(lines: list[str], idx: int) -> str:
    """Nearest preceding markdown heading for line ``idx`` (for locating a defect)."""
    for j in range(idx, -1, -1):
        m = re.match(r"#{1,6}\s+(.+)", lines[j])
        if m:
            return m.group(1).strip()
    return "document"

_PLACEHOLDER_PATTERNS = (
    "no specific urls were provided",
    "placeholder for citations",
    "to be completed",
    "pending — needs sourced content",
    "pending - needs sourced content",
)

_CITATION_WALL_STARTERS = (
    "this validates",
    "this provides",
    "this demonstrates",
    "this establishes",
    "this illustrates",
    "this identifies",
    "establishes",
    "illustrates",
    "demonstrates",
)


def _dedupe(items: list[str]) -> list[str]:
    """Stable dedup — keep first occurrence order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _heading_key(heading: str) -> str:
    s = re.sub(r"^#+\s*", "", heading)
    s = re.sub(r"^\d+[.)]\s*", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _duplicate_heading_issues(text: str) -> list[str]:
    try:
        from studio.rubric import mask_fenced_code
        masked = mask_fenced_code(text)
    except Exception:  # noqa: BLE001 - lint must be fail-open
        masked = text
    headings = re.findall(r"(?m)^(#{1,6}\s+.+)$", masked or "")
    counts: dict[str, int] = {}
    first: dict[str, str] = {}
    for h in headings:
        key = _heading_key(h)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, h.strip())
    return [
        f"[document] Duplicate section heading appears {count} times: {first[key]}"
        for key, count in counts.items()
        if count > 1
    ]


def _placeholder_issues(text: str) -> list[str]:
    low = (text or "").lower()
    issues: list[str] = []
    for pat in _PLACEHOLDER_PATTERNS:
        if pat in low:
            issues.append(f"[document] Placeholder text remains in report: {pat}")
    return issues


#: A CLEAN, already-healed (entry 173) bare `(unverified)` marker is well-formed
#: text — but it still means a citation failed verification and shipped to the
#: user with no visibility into the fix loop (lint_artifact never flagged it, so
#: it never entered the editor's weakness list). The negative lookbehind excludes
#: the garbled forms above (`((unverified)`, `[(unverified)`), which already get
#: their own message — this only matches the clean, single-paren form.
_CLEAN_UNVERIFIED_RE = re.compile(r"(?<![(\[])\(unverified\)")


def _broken_link_issues(text: str) -> list[str]:
    issues: list[str] = []
    if "((unverified)" in text.lower() or "](unverified" in text.lower():
        issues.append("[document] Malformed or explicitly unverified markdown link remains.")
    if _CLEAN_UNVERIFIED_RE.search(text or ""):
        issues.append("[document] Citation marked (unverified) remains visible in report text.")
    for m in re.finditer(r"\[[^\]]+\]\(([^)]*)\)", text or ""):
        target = (m.group(1) or "").strip()
        if not target or "unverified" in target.lower():
            issues.append("[document] Markdown link has empty or unverified target.")
    return issues


def _citation_wall_issues(text: str) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    streak = 0
    for para in paragraphs:
        first = re.sub(r"^[>*\-\s]+", "", para).strip().lower()
        if first.startswith(_CITATION_WALL_STARTERS):
            streak += 1
            if streak >= 3:
                return [
                    "[document] Citation wall: 3+ consecutive source-summary paragraphs "
                    "begin with scaffolding phrases instead of synthesized prose."
                ]
        else:
            streak = 0
    return []


def _orphaned_code_issues(text: str) -> list[str]:
    lines = (text or "").split("\n")
    issues: list[str] = []
    in_fence = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("# Example usage"):
            issues.append(
                f"[{_section_at(lines, i)}] Code fragment appears outside a fenced code block: "
                f"{stripped[:60]}"
            )
            continue
        if re.match(r"^(agent\s*=|class\s+\w+|def\s+\w+\(|import\s+\w+|from\s+\w+\s+import\s+)", stripped):
            issues.append(
                f"[{_section_at(lines, i)}] Code-looking line appears outside a fenced code block: "
                f"{stripped[:60]}"
            )
    return issues


def _citation_free_section_issues(text: str) -> list[str]:
    try:
        from agentkit.artifacts.sections import split_sections
        from studio.rubric import mask_fenced_code
        sections = split_sections(mask_fenced_code(text or ""))
    except Exception:  # noqa: BLE001
        return []
    issues: list[str] = []
    for heading, body in sections:
        hlow = heading.lower()
        if any(skip in hlow for skip in ("reference", "appendix", "glossary", "title")):
            continue
        words = re.findall(r"\w+", body or "")
        if len(words) > 150 and "http://" not in body and "https://" not in body:
            issues.append(f"[{heading.lstrip('# ').strip()}] Long evidence-bearing section has no citation URL.")
    return issues


#: E2 (L1): a non-structural section shorter than this many body words is a stub —
#: a heading with no substantive content behind it. Module const, never per-task.
_STUB_WORD_FLOOR = 25


def _stub_section_issues(text: str, floor: int = _STUB_WORD_FLOOR) -> list[str]:
    """Sections whose body is a thin stub (< ``floor`` words). Structural sections
    (code / mermaid fence or a table) are exempt — their content is not prose words.
    Reference/appendix/glossary/contents headings are skipped (legitimately short).
    ``split_sections`` returns the heading INSIDE the body, so strip it before the
    word count. Fail-open to ``[]``. ponytail: an entirely EMPTY heading is tolerated
    as a container; E1/E11 cover other structure defects."""
    try:
        from agentkit.artifacts.sections import split_sections
    except Exception:  # noqa: BLE001
        return []
    issues: list[str] = []
    for heading, body in split_sections(text or ""):
        hlow = heading.lower()
        if any(skip in hlow for skip in
               ("reference", "appendix", "glossary", "title", "contents")):
            continue
        b = body or ""
        if "```" in b or "\n|" in b:  # code / mermaid / table → structural content
            continue
        b = re.sub(r"^#{1,6}\s+.*\n?", "", b, count=1)      # drop the heading echo
        b = re.sub(r"https?://\S+", " ", b)                  # URLs aren't prose
        words = re.findall(r"\w+", b)
        if 0 < len(words) < floor:
            issues.append(
                f"[{heading.lstrip('# ').strip()}] Section is a stub "
                f"({len(words)} words < {floor} floor); needs substantive content."
            )
    return issues


def _fenced_blocks(text: str) -> list[tuple[str, str, int, int]]:
    lines = (text or "").splitlines()
    blocks: list[tuple[str, str, int, int]] = []
    start = None
    lang = ""
    body: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            if start is None:
                start = i
                lang = stripped[3:].strip().lower()
                body = []
            else:
                blocks.append((lang, "\n".join(body), start, i))
                start = None
                lang = ""
                body = []
            continue
        if start is not None:
            body.append(line)
    return blocks


def _table_issues(text: str) -> list[str]:
    lines = (text or "").splitlines()
    issues: list[str] = []
    for i in range(len(lines) - 1):
        header = lines[i].strip()
        sep = lines[i + 1].strip()
        if not (header.startswith("|") and header.endswith("|")):
            continue
        if not (sep.startswith("|") and sep.endswith("|")):
            continue
        if not re.fullmatch(r"[\s|:\-]+", sep):
            continue
        header_cells = [c.strip() for c in header.strip("|").split("|")]
        sep_cells = [c.strip() for c in sep.strip("|").split("|")]
        if len(header_cells) < 2:
            continue
        valid_sep = (
            len(sep_cells) == len(header_cells)
            and all(re.fullmatch(r":?-{3,}:?", c or "") for c in sep_cells)
        )
        if not valid_sep:
            issues.append(
                f"[{_section_at(lines, i)}] Malformed markdown table separator: {sep[:80]}"
            )
            continue
        rows: list[list[str]] = []
        j = i + 2
        while j < len(lines):
            row = lines[j].strip()
            if not (row.startswith("|") and row.endswith("|")):
                break
            rows.append([c.strip() for c in row.strip("|").split("|")])
            j += 1
        if rows and all(
            not any(cell and cell.lower() not in {"-", "—", "n/a", "tbd"} for cell in row)
            for row in rows
        ):
            issues.append(f"[{_section_at(lines, i)}] Empty comparison table has no real cell content.")
    return issues


def _mermaid_explanation_issues(text: str) -> list[str]:
    lines = (text or "").splitlines()

    def prose_near(start: int, end: int) -> bool:
        lo = max(0, start - 3)
        hi = min(len(lines), end + 4)
        for idx in list(range(lo, start)) + list(range(end + 1, hi)):
            s = lines[idx].strip()
            if not s or s.startswith(("#", "```", "|", "-", "*", ">")):
                continue
            if re.match(r"^(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram)", s):
                continue
            if len(re.findall(r"[A-Za-z]{3,}", s)) >= 4:
                return True
        return False

    issues: list[str] = []
    for lang, _body, start, end in _fenced_blocks(text):
        if lang == "mermaid" and not prose_near(start, end):
            issues.append(
                f"[{_section_at(lines, start)}] Mermaid diagram has no nearby explanatory prose."
            )
    return issues


def _python_code_issues(text: str) -> list[str]:
    lines = (text or "").splitlines()
    issues: list[str] = []
    for lang, body, start, _end in _fenced_blocks(text):
        if lang not in {"python", "py"}:
            continue
        try:
            ast.parse(body or "\n")
        except SyntaxError as exc:
            issues.append(
                f"[{_section_at(lines, start)}] Python code block has syntax error: "
                f"line {exc.lineno or '?'} {exc.msg}"
            )
    return issues


def _references_terminal_issues(text: str) -> list[str]:
    """References should not be followed by new report body sections."""
    headings = list(re.finditer(r"(?m)^(#{1,6})\s+(.+)$", text or ""))
    for i, match in enumerate(headings):
        title = match.group(2).strip().lower()
        if title not in {"references", "reference", "sources", "bibliography"}:
            continue
        trailing = headings[i + 1:]
        for nxt in trailing:
            nxt_title = nxt.group(2).strip().lower()
            if nxt_title in {"appendix", "appendices", "glossary"}:
                continue
            return [
                "[document] References section is followed by additional report "
                f"content: {nxt.group(0).strip()}"
            ]
    return []


def _fence_contamination_issues(text: str) -> list[str]:
    """A fence marker (```` ``` ````) with trailing content glued onto the same
    line — e.g. a closing fence immediately followed by a citation URL. Breaks
    markdown rendering and is the shared trigger for the deterministic repair in
    ``studio.artifact_text``."""
    lines = (text or "").split("\n")
    issues: list[str] = []
    is_opening = True
    for m in FENCE_LINE_RE.finditer(text or ""):
        if fence_rest_contaminated(m.group("rest"), is_opening=is_opening):
            line_idx = text.count("\n", 0, m.start())
            issues.append(
                f"[{_section_at(lines, line_idx)}] Fence line carries trailing "
                f"content after the ``` marker: {lines[line_idx].strip()[:60]}"
            )
        is_opening = not is_opening
    return issues


def lint_artifact(text: str) -> list[str]:
    """Return content-validity weaknesses for *text* (empty list when clean).

    Checks are deterministic, order-stable, and intentionally conservative:
      1. Malformed mermaid edge — a node glued to a ``|label|`` with no link operator.
      2. Unbalanced code fence — an odd number of ```` ``` ```` markers.
      3. Duplicate section headings / repeated outlines.
      4. Placeholder reference or unfinished-section text.
      5. Malformed or explicitly unverified markdown links.
      6. Citation walls: repeated source-summary scaffolding instead of synthesis.
      7. Code fragments outside fenced blocks.
      8. Long evidence-bearing sections without citation URLs.
      9. Malformed/empty markdown tables.
      10. Mermaid diagrams without nearby explanatory prose.
      11. Obvious Python fenced-block syntax errors.
      12. References section followed by more body sections.
      13. Fence line carries trailing content after the ``` marker.
    """
    if not text:
        return []
    lines = text.split("\n")
    issues: list[str] = []

    in_mermaid = False
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped.startswith("```"):
            lang = stripped[3:].strip().lower()
            in_mermaid = lang == "mermaid" if not in_mermaid else False
            continue
        if in_mermaid and _MERMAID_GLUED_EDGE.search(ln):
            issues.append(
                f"[{_section_at(lines, i)}] Malformed mermaid edge — a node is glued "
                f"to a |label| with no link operator (use 'A -->|label| B'): "
                f"{stripped[:60]}"
            )

    if text.count("```") % 2 != 0:
        issues.append(
            "[document] Unbalanced code fence (```): a code or mermaid block is "
            "truncated or never closed."
        )

    issues.extend(_duplicate_heading_issues(text))
    issues.extend(_placeholder_issues(text))
    issues.extend(_broken_link_issues(text))
    issues.extend(_citation_wall_issues(text))
    issues.extend(_orphaned_code_issues(text))
    issues.extend(_citation_free_section_issues(text))
    issues.extend(_table_issues(text))
    issues.extend(_mermaid_explanation_issues(text))
    issues.extend(_python_code_issues(text))
    issues.extend(_references_terminal_issues(text))
    issues.extend(_fence_contamination_issues(text))

    return _dedupe(issues)


if __name__ == "__main__":  # pragma: no cover — runnable self-check
    bad = (
        "## Design\n\nThis diagram explains the tool-selection flow.\n\n```mermaid\ngraph TD\n"
        "    ToolSelector -->|Search| WebTool\n"
        "    ToolSelector|Read| ReadTool\n```\n"
    )
    got = lint_artifact(bad)
    assert any("Malformed mermaid edge" in w for w in got), got
    assert "Design" in got[0], got
    good = bad.replace("ToolSelector|Read|", "ToolSelector -->|Read|")
    assert lint_artifact(good) == [], lint_artifact(good)
    # odd fences → unbalanced
    assert any("Unbalanced" in w for w in lint_artifact("```python\nx = 1\n"))
    # E2 stub floor: a thin non-structural section is flagged; a code-dense one is not.
    stub_doc = "## Analysis\n\nToo short.\n\n## Details\n\n```python\n" + "x = 1\n" * 40 + "```\n"
    stubs = _stub_section_issues(stub_doc)
    assert any("Analysis" in w for w in stubs), stubs
    assert not any("Details" in w for w in stubs), stubs
    print("artifact_lint self-check OK")
