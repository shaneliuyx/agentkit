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


def _broken_link_issues(text: str) -> list[str]:
    issues: list[str] = []
    if "((unverified)" in text.lower() or "](unverified" in text.lower():
        issues.append("[document] Malformed or explicitly unverified markdown link remains.")
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

    return _dedupe(issues)


if __name__ == "__main__":  # pragma: no cover — runnable self-check
    bad = (
        "## Design\n\n```mermaid\ngraph TD\n"
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
    print("artifact_lint self-check OK")
