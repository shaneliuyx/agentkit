"""studio.export — serialize a finished Studio run to a loop-library loop (M9).

Closes the loop: Studio both *consumes* published loops (M7 seeding) and
*produces* them. A finished run carries everything a publishable loop needs —
the plan steps (→ the loop's flat ``steps``), per-step topology (→ step
annotations), the requirement (→ ``description``/``useWhen``), the Loop Doctor
checks (→ ``verification``), and the budget (→ a bounded-spend note in ``why``).

The output is the REAL catalog loop shape (verified against catalog.json
schemaVersion 2): ``slug, title, category{slug,label}, description, useWhen,
prompt, verification{title,detail}, steps[], why, keywords[]``. ``number``,
``author``, ``published`` etc. are catalog-assigned at publish time and omitted
here — the exported loop is an unpublished draft a human edits before
contributing, so it round-trips conceptually rather than claiming authorship.

This module is PURE: it serializes a ``RunSnapshot`` value and touches nothing.
"""

from __future__ import annotations

import re
from typing import Any

from studio.session import RunSnapshot

#: Tokens too common to carry signal as loop keywords.
_STOP = frozenset(
    "the a an is are to of in on for and or with this that build make use using "
    "do does run loop agent workflow when how into from your you it a an".split()
)


def _slugify(text: str, *, fallback: str = "studio-run") -> str:
    """Lowercase, hyphenated slug from free text (catalog slug shape)."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or fallback


def _title(requirement: str) -> str:
    """A short human title from the requirement (first clause, capped)."""
    head = requirement.strip().splitlines()[0].strip() if requirement.strip() else ""
    head = head[:80].rstrip()
    return head[:1].upper() + head[1:] if head else "Studio run"


def _keywords(requirement: str, steps: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    """Content keywords from the requirement + step descriptions (deduped)."""
    text = requirement + " " + " ".join(str(s.get("description", "")) for s in steps)
    raw = "".join(c if c.isalnum() else " " for c in text.lower()).split()
    seen: list[str] = []
    for tok in raw:
        if tok not in _STOP and len(tok) > 2 and tok not in seen:
            seen.append(tok)
        if len(seen) >= limit:
            break
    return seen


def _verification(checks: list[dict[str, Any]]) -> dict[str, str]:
    """Build the loop's ``verification`` block from the Loop Doctor checks.

    ``title`` states the success gate; ``detail`` lists each audit dimension's
    status (and any suggested repair) so the published loop carries the same
    bounded/checked/safe/terminating contract the run was audited against.
    """
    passed = [c for c in checks if c.get("status") == "pass"]
    title = (
        f"All {len(checks)} loop-doctor checks pass."
        if checks and len(passed) == len(checks)
        else f"{len(passed)} of {len(checks)} loop-doctor checks pass."
    )
    lines = []
    for c in checks:
        line = f"{c.get('name')}: {c.get('status')}"
        if c.get("fix"):
            line += f" — {c['fix']}"
        lines.append(line)
    detail = "; ".join(lines) if lines else "No audit recorded."
    return {"title": title, "detail": detail}


def _steps_with_topology(steps: list[dict[str, Any]], topology: dict[str, str]) -> list[str]:
    """Flatten plan steps to the loop's ``steps`` list, annotating fan-out.

    A finite DAG IS the stop condition, so each step keeps its dependency +
    topology shape inline (e.g. "(mesh; after s1)") — the annotation is how the
    flat catalog ``steps`` field preserves the run's structure for a re-import.
    """
    out: list[str] = []
    for s in steps:
        desc = str(s.get("description", "")).strip()
        topo = topology.get(str(s.get("id")), "")
        deps = [str(d) for d in (s.get("depends_on") or [])]
        notes = []
        if topo and topo != "single":
            notes.append(topo)
        if deps:
            notes.append("after " + ", ".join(deps))
        if notes:
            desc = f"{desc} ({'; '.join(notes)})"
        out.append(desc)
    return out


def run_to_loop(snapshot: RunSnapshot) -> dict[str, Any]:
    """Serialize a finished ``RunSnapshot`` into a loop-library loop dict.

    PURE. The returned dict is the catalog loop shape (an unpublished draft):
    ``{slug, title, category{slug,label}, description, useWhen, prompt,
    verification{title,detail}, steps[], why, keywords[]}``.
    """
    requirement = snapshot.requirement
    steps = snapshot.plan_steps
    title = _title(requirement)
    bounded = snapshot.budget_ceiling is not None
    budget_note = (
        f" Bounded by a {snapshot.budget_ceiling:g}-token fan-out ceiling."
        if bounded
        else " Set a token ceiling before publishing to bound fan-out spend."
    )
    return {
        "slug": _slugify(title),
        "title": title,
        "category": {"slug": "engineering", "label": "Engineering"},
        "description": (
            f"A Studio-exported agent loop that decomposes '{requirement.strip()}' "
            f"into {len(steps)} bounded, verified phases."
        ),
        "useWhen": (
            f"Use this whenever you need to: {requirement.strip()}"
            if requirement.strip()
            else "Use this for a repeatable multi-phase agent run."
        ),
        "prompt": requirement.strip(),
        "verification": _verification(snapshot.loopdoctor_checks),
        "steps": _steps_with_topology(steps, snapshot.topology),
        "why": (
            "Exported from a finished AgentKit Studio run: each phase is gate-checked "
            "and the final output is verified, so the loop ties its outcome to "
            "observable checks rather than memory." + budget_note
        ),
        "keywords": _keywords(requirement, steps),
    }
