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


def lint_artifact(text: str) -> list[str]:
    """Return content-validity weaknesses for *text* (empty list when clean).

    Checks (deterministic, order-stable, deduped):
      1. Malformed mermaid edge — a node glued to a ``|label|`` with no link operator.
      2. Unbalanced code fence — an odd number of ```` ``` ```` markers (a code/mermaid
         block is truncated or never closed).
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

    # Stable dedup — the same glued-edge line can recur; keep first occurrence order.
    seen: set[str] = set()
    out: list[str] = []
    for w in issues:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


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
