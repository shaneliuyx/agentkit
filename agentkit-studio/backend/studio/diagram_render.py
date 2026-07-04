"""Deterministic architecture-diagram renderer (Bug A / harness "A2" path).

WHY THIS EXISTS
---------------
The editor's structural-opportunity retry (``runner._editor_structural_retry``)
originally asked a weak local model (gemma-4-26B via oMLX) to do TWO hard things
in one turn: (1) generate *syntactically valid* mermaid, and (2) issue a
``patch_artifact`` tool call to splice it into the artifact. Live runs produced
**0 mermaid across 6 attempts** (session s_0a3669434b76) — the model reliably
failed one or both halves. A prompt-only fix ("Mermaid Safe Mode") measured 4/4
in an ISOLATED harness but still failed live, because the isolated harness never
exercised the tool-call insertion half — the real point of failure.

THE FIX (A2 — components list → deterministic renderer)
-------------------------------------------------------
Move the mermaid *syntax* out of the model's hands entirely. The model emits
only plain, unstructured lines it CAN reliably produce:

    COMPONENT: <name> | <one-line role>
    EDGE: <name A> -> <name B> | <optional label>

and THIS module (pure Python, no LLM) renders those lines into a guaranteed-valid
``flowchart TD`` block. The model never writes a bracket, a quote, or an arrow —
so the output is always syntactically valid by construction. Insertion is done by
the caller writing the artifact file directly, NOT via a model tool call.

GROUNDING GUARD — LITERAL TOKEN PRESENCE (empirically settled, HANDOFF DECISION A)
---------------------------------------------------------------------------------
The primary grounding is inherent: gemma builds the component/edge list BY READING
the report, so real nodes are **verbatim substrings of the prose by construction**.
The fabrication guard keeps a node only if ≥1 significant token (len≥4, case-
insensitive) of its label literally appears in the report.

This deliberately REVERSES the earlier embedding-cosine guard. That guard was
measured on the real v9 artifact and found INERT: BGE-M3 scores short entity
labels by DOMAIN, not presence, so a same-domain fabrication ("Stripe Billing
API", 0.540) TIES a real component ("Agentic logic", 0.540) — only ~0.03
separation, so no threshold rejects anything. Literal-token presence separated the
same data cleanly (real 4/4 tokens in the section, fabricated 0/3). The feared
"paraphrase/synonym" failure of literal matching does NOT materialize here,
precisely because the generator extracts labels FROM the prose rather than
inventing them. (Escape hatch, deferred until a real non-literal node is ever
observed: one cheap LLM-verify per zero-token-overlap residual node. Currently the
residual == the fabrications, so it would add cost for nothing — not added.)

A label with no significant token (all <4 chars, e.g. "AI", "DB") cannot be
literal-checked → it FALLS OPEN (kept). The accept gate (rubric/lint non-regression
+ strict opportunity-count drop) is the final backstop, so grounding must never
hard-reject what it cannot check.
"""
from __future__ import annotations

import re

#: Render caps (Mermaid Safe Mode). ``_MIN_NODES``: a "diagram" of 1-3 boxes is not
#: worth the accept-gate churn and usually signals the model failed to enumerate the
#: architecture. ``_MAX_NODES`` bounds rendered nodes so a runaway list stays a
#: readable flowchart (extras dropped in declared order — a partial diagram beats none).
_MIN_NODES = 4
_MAX_NODES = 15

#: Minimum length for a label token to count as a grounding signal. Below this,
#: tokens are glue words / acronyms too generic to discriminate real from fabricated.
_MIN_TOKEN_LEN = 4

#: A diagram's whole value is RELATIONSHIPS: N boxes with zero edges is a (worse) bullet
#: list, not a diagram — yet a grounded component list with no valid edge would otherwise
#: render as a "diagram" and pass the accept gate (codex review C2). Require at least this
#: many rendered edges or return None. ponytail: 1 is the correctness floor (0 = the defect);
#: raise to 2 if sparse one-edge diagrams prove low-value in live testing.
_MIN_EDGES = 1

_COMPONENT_RE = re.compile(r"COMPONENT:\s*([^|]+?)\s*(?:\|.*)?$")
#: EDGE endpoints are short labels containing no ``->``, so a non-greedy left side up
#: to the FIRST ``->`` is unambiguous.
_EDGE_RE = re.compile(r"EDGE:\s*(.+?)\s*->\s*([^|]+?)\s*(?:\|\s*(.*))?$")


def build_components_prompt(artifact_text: str) -> str:
    """Prompt the BARE (non-tool-augmented) client — asks ONLY for the plain
    COMPONENT/EDGE lines this module parses. No mermaid, no tool call, no prose:
    every hard part is done deterministically downstream. The report is the sole
    grounding source; the model is told to name only entities it discusses, and the
    literal-token grounding guard enforces it regardless."""
    return (
        "List the architecture of the system described in the REPORT below as "
        "plain lines. Use ONLY this exact format — no prose, no markdown, no code "
        "fences, nothing else:\n"
        "COMPONENT: <name> | <one-line role>\n"
        "EDGE: <name A> -> <name B> | <optional label>\n\n"
        "Rules:\n"
        f"- {_MIN_NODES} to {_MAX_NODES} components.\n"
        "- Every <name> MUST be an entity the report actually discusses — never "
        "invent a component the report does not describe.\n"
        "- Draw an EDGE only between two names you listed as COMPONENT lines.\n"
        "- Keep each <name> short (1-4 words); put detail in the role/label.\n\n"
        f"=== REPORT ===\n{artifact_text}\n=== END REPORT ==="
    )


def _parse(raw: str) -> tuple[list[str], list[tuple[str, str, str]]]:
    """``(component_names, edges)`` from the model's plain lines. Order-preserving,
    de-duplicated (case-insensitive) component list; malformed lines are skipped,
    not errored on — a weak model interleaves stray prose and we simply ignore it."""
    comps: list[str] = []
    seen: set[str] = set()
    edges: list[tuple[str, str, str]] = []
    for ln in raw.splitlines():
        ln = ln.strip()
        mc = _COMPONENT_RE.match(ln)
        if mc:
            name = mc.group(1).strip()
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                comps.append(name)
            continue
        me = _EDGE_RE.match(ln)
        if me:
            edges.append((me.group(1).strip(), me.group(2).strip(), (me.group(3) or "").strip()))
    return comps, edges


#: Non-discriminating architecture-noise labels (codex review C3): a node named only
#: "System"/"Data"/"Process" carries no information and clutters the diagram — a real node
#: has a specific name ("Planner", "pi-agent-core"). Dropped when a label's ONLY significant
#: tokens are generic; a compound like "Data Pipeline" survives on its non-generic token.
_GENERIC_LABELS = frozenset({
    "system", "systems", "process", "processes", "data", "component", "components",
    "module", "modules", "service", "services", "framework", "frameworks", "tool",
    "tools", "platform", "platforms", "application", "applications", "layer", "layers",
    "output", "outputs", "input", "inputs", "result", "results", "object", "objects",
    "interface", "interfaces", "function", "functions", "pipeline", "pipelines",
})


def _significant_tokens(label: str) -> list[str]:
    """Lowercased alphanumeric tokens of ``label`` at least ``_MIN_TOKEN_LEN`` chars —
    the terms specific enough to ground against ("planner", "executor"). Short glue
    ("a", "the", "of") and bare acronyms ("AI", "DB") are dropped as non-discriminating."""
    return [t for t in re.findall(r"[a-z0-9]+", label.lower()) if len(t) >= _MIN_TOKEN_LEN]


def _ground(comps: list[str], artifact_text: str) -> list[str]:
    """Keep a component iff ≥1 DISCRIMINATING significant token of its label literally
    appears in the report prose (word-start match, so a plural/inflection of a real term
    still counts, while a token buried inside an unrelated word does not). Deterministic,
    zero-cost.

    Real nodes survive because the model extracts the list FROM the prose (verbatim by
    construction); a fabrication sharing no ≥4-char token with the report is dropped. A
    label with no significant token at all is un-checkable → kept (fall-open; the accept
    gate is the backstop). A label whose ONLY significant tokens are generic
    ("System", "Data Process") is dropped as non-discriminating (C3). See the module
    docstring for why literal grounding beats the embedding-cosine guard it replaces."""
    low = artifact_text.lower()
    kept: list[str] = []
    for name in comps[:_MAX_NODES]:
        sig = _significant_tokens(name)
        if not sig:
            kept.append(name)  # un-checkable short label (acronym) → fall open
            continue
        discriminating = [t for t in sig if t not in _GENERIC_LABELS]
        if not discriminating:
            continue  # generic-only label → drop (C3)
        if any(re.search(r"\b" + re.escape(t), low) for t in discriminating):
            kept.append(name)
    return kept


def _render(names: list[str], edges: list[tuple[str, str, str]]) -> str:
    """Names + edges → a guaranteed-valid mermaid ``flowchart TD`` body. Validity is
    by CONSTRUCTION: synthetic ``N0/N1`` ids (never the raw label), every label
    double-quoted, no styling, edges only between rendered nodes."""
    ids = {name.lower(): f"N{i}" for i, name in enumerate(names)}
    lines = ["flowchart TD"]
    for name in names:
        label = name.replace('"', "'")  # a stray quote can never break the node syntax
        lines.append(f'    {ids[name.lower()]}["{label}"]')
    for a, b, lab in edges:
        ida, idb = ids.get(a.lower()), ids.get(b.lower())
        if not ida or not idb or ida == idb:  # edge to a dropped/unknown node, or self-loop
            continue
        if lab:
            lines.append(f'    {ida} -->|"{lab.replace(chr(34), chr(39))}"| {idb}')
        else:
            lines.append(f"    {ida} --> {idb}")
    return "\n".join(lines)


def render_grounded_diagram(raw: str, artifact_text: str) -> str | None:
    """Parse the model's COMPONENT/EDGE lines, drop ungrounded components (literal-token
    presence in the report), and render a guaranteed-valid mermaid ``flowchart TD``
    body — or ``None`` if fewer than ``_MIN_NODES`` grounded components survive (adding
    a diagram then would mean inventing nodes).

    Returns the block BODY (starting ``flowchart TD``), NOT fenced — the caller owns
    fencing + placement via ``insert_diagram_block``."""
    comps, edges = _parse(raw)
    if not comps:
        return None
    kept = _ground(comps, artifact_text)
    if len(kept) < _MIN_NODES:
        return None
    body = _render(kept, edges)
    # Both rendered edge forms (``A --> B`` and ``A -->|"lab"| B``) contain ``-->``; node
    # labels never do. So ``-->`` count == rendered-edge count — reject an edgeless
    # component list (relationships are the point of a diagram; codex review C2).
    if body.count("-->") < _MIN_EDGES:
        return None
    return body


#: Section headings a diagram belongs under, most-specific first. The renderer places
#: the block at the TOP of the first matching section so it reads as part of that
#: section's body (not stranded after References). Falls back to just before the
#: References/Sources block, then to end-of-document.
_TARGET_HEADING_RE = re.compile(
    r"(?i)(architect|design|topolog|overview|system|component|key findings|evidence)"
)
_TAIL_HEADING_RE = re.compile(r"(?i)^#{1,6}\s+(references|sources|bibliography|citations)\b")
_HEADING_RE = re.compile(r"^#{1,6}\s+")


def insert_diagram_block(artifact_text: str, mermaid_body: str) -> str:
    """Insert a fenced ```mermaid block into ``artifact_text`` at a sensible spot.

    Placement priority (a diagram after the References list is technically "present"
    but useless — we want it in the relevant body section):
      1. Immediately after the FIRST body heading matching ``_TARGET_HEADING_RE``.
      2. Else immediately before the FIRST References/Sources heading.
      3. Else appended at end-of-document.

    Written into a section BODY, so the downstream ``_write_artifact_through_sections``
    section-split round-trip preserves it (the same round-trip the tool-augmented
    path already relies on)."""
    block = f"```mermaid\n{mermaid_body}\n```"
    lines = artifact_text.splitlines()

    for i, ln in enumerate(lines):  # (1) after a target body heading
        if _HEADING_RE.match(ln) and _TARGET_HEADING_RE.search(ln) and not _TAIL_HEADING_RE.match(ln):
            return "\n".join(lines[: i + 1] + ["", block, ""] + lines[i + 1 :])

    for i, ln in enumerate(lines):  # (2) before the References/Sources block
        if _TAIL_HEADING_RE.match(ln):
            return "\n".join(lines[:i] + [block, ""] + lines[i:])

    sep = "" if artifact_text.endswith("\n") else "\n"  # (3) end of document
    return f"{artifact_text}{sep}\n{block}\n"


def _demo() -> None:
    """Runnable self-check (no network, no embedder): render is valid+balanced; the
    literal-token guard drops a fabricated node and keeps grounded ones; an un-checkable
    short-token label falls open; insertion lands in a body section.
    `python -m studio.diagram_render`."""
    report = (
        "## Architecture\nThe Planner builds a plan. The Executor runs tools. The "
        "Memory store persists state. The Scorer grades the artifact.\n## References\n- x\n"
    )
    raw = (
        "COMPONENT: Planner | builds plan\nCOMPONENT: Executor | runs tools\n"
        "COMPONENT: Memory | persists state\nCOMPONENT: Scorer | grades output\n"
        "EDGE: Planner -> Executor\nEDGE: Executor -> Scorer\n"
    )
    body = render_grounded_diagram(raw, report)
    assert body and body.startswith("flowchart TD"), body
    assert body.count("[") == body.count("]") and "-->" in body

    # Literal-token guard: fabricated nodes share no >=4-char token with the report → dropped.
    fabricated = "\n".join(f"COMPONENT: Zorptron{i} | invented" for i in range(6))
    assert render_grounded_diagram(fabricated, report) is None

    # Mixed: only the grounded node survives; 1 grounded < _MIN_NODES → None.
    mixed = "COMPONENT: Planner | real\n" + fabricated
    assert render_grounded_diagram(mixed, report) is None

    # Edgeless component list → not a diagram (no relationships) → None.
    edgeless = "\n".join(f"COMPONENT: {n} | r" for n in ("Planner", "Executor", "Memory", "Scorer"))
    assert render_grounded_diagram(edgeless, report) is None

    # Un-checkable short-token labels (no >=4-char token) fall open — grounding must
    # not hard-reject what it cannot check; the accept gate is the backstop. (Needs an
    # edge to clear the _MIN_EDGES floor.)
    short = "\n".join(f"COMPONENT: AI{i} | x" for i in range(4)) + "\nEDGE: AI0 -> AI1\n"
    assert render_grounded_diagram(short, report) is not None

    out = insert_diagram_block(report, body)
    assert out.index("## Architecture") < out.index("```mermaid") < out.index("## References")
    print("diagram_render self-check OK")


if __name__ == "__main__":
    _demo()
