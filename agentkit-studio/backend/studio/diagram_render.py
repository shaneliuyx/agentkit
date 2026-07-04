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

GROUNDING GUARD — SEMANTIC, NOT KEYWORD
---------------------------------------
The primary grounding is inherent: gemma builds the component/edge list BY READING
the report, so it already extracts the report's own (often implicit) structure.
The safety guard against fabrication is a SEMANTIC check, NOT literal keyword/
substring matching. Keyword matching is brittle — it would reject a validly-
grounded node like "Feedback Loop" whose meaning is paraphrased in the prose
("the agent iterates on its own critique") with no shared literal token. That is
the exact whack-a-mole this codebase already fixed once for ``_STRUCTURAL_OPP_RE``
(which needed an LLM-fallback classifier because a fixed vocabulary can't capture
implicit meaning). So we embed each node label and the report's section text with
the pipeline's existing BGE-M3 embedder and keep a node only if its max cosine
against any section clears ``_GROUND_COSINE_MIN`` — paraphrase and synonyms pass,
a node whose concept appears NOWHERE (even semantically) is rejected.

When no embedder is available in the retry context, we FALL OPEN — trust the
model's extraction and render all parsed components — because the accept gate
(rubric/lint non-regression + strict opportunity-count drop) is the final
backstop; a missing embedder must never hard-reject a real diagram.

Ported from ``tmp/mvp_harness`` (the validated A2 render candidate); grounding
upgraded from the harness's keyword check to the semantic check per review.
"""
from __future__ import annotations

import re

#: Render caps (Mermaid Safe Mode). ``_MIN_NODES``: a "diagram" of 1-3 boxes is not
#: worth the accept-gate churn and usually signals the model failed to enumerate the
#: architecture. ``_MAX_NODES`` bounds rendered nodes so a runaway list stays a
#: readable flowchart (extras dropped in declared order — a partial diagram beats none).
_MIN_NODES = 4
_MAX_NODES = 15

#: Cosine floor for the SEMANTIC grounding guard (BGE-M3). Tuned empirically on the
#: real v9 artifact (tmp/a2_tune_threshold.py): genuine nodes for that agent-dev
#: report ("Agent Loop", "Craft", "Executor", "Reflect") score ~0.5-0.75 against
#: their section; an injected fabrication ("Blockchain Ledger", "Quantum Encryption")
#: scores ~0.2-0.3. 0.40 sits in the gap — real nodes pass, fabricated nodes fail —
#: with margin on both sides. This is the calibration knob; re-tune if the embedder
#: model changes.
_GROUND_COSINE_MIN = 0.40

_COMPONENT_RE = re.compile(r"COMPONENT:\s*([^|]+?)\s*(?:\|.*)?$")
#: EDGE endpoints are short labels containing no ``->``, so a non-greedy left side up
#: to the FIRST ``->`` is unambiguous.
_EDGE_RE = re.compile(r"EDGE:\s*(.+?)\s*->\s*([^|]+?)\s*(?:\|\s*(.*))?$")
#: Split the artifact into per-section chunks (grounding compares a label against the
#: section text, per review) — any markdown heading level starts a new chunk.
_SECTION_SPLIT_RE = re.compile(r"(?m)^#{1,6}\s+")


def build_components_prompt(artifact_text: str) -> str:
    """Prompt the BARE (non-tool-augmented) client — asks ONLY for the plain
    COMPONENT/EDGE lines this module parses. No mermaid, no tool call, no prose:
    every hard part is done deterministically downstream. The report is the sole
    grounding source; the model is told to name only entities it discusses, and the
    semantic grounding guard enforces it regardless."""
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


def _cosine(a, b) -> float:
    import numpy as np
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0 or a.shape != b.shape:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _ground(comps: list[str], artifact_text: str, embedder, min_cosine: float) -> list[str]:
    """Keep only components semantically present in the report. FALL OPEN (keep all)
    when there is no embedder or the embed call fails — the accept gate is the
    backstop, and a missing embedder must never hard-reject a real diagram."""
    comps = comps[:_MAX_NODES]
    if embedder is None:
        return comps
    try:
        sections = [s.strip() for s in _SECTION_SPLIT_RE.split(artifact_text) if s.strip()]
        sections = sections or [artifact_text]
        sec_vecs = embedder.embed(sections)
        label_vecs = embedder.embed(comps)
    except Exception:  # noqa: BLE001 — embedder down → trust the model's extraction
        return comps
    kept: list[str] = []
    for name, lv in zip(comps, label_vecs):
        best = max((_cosine(lv, sv) for sv in sec_vecs), default=0.0)
        if best >= min_cosine:
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


def render_grounded_diagram(
    raw: str, artifact_text: str, *, embedder=None, min_cosine: float = _GROUND_COSINE_MIN
) -> str | None:
    """Parse the model's COMPONENT/EDGE lines, drop semantically-ungrounded
    components, and render a guaranteed-valid mermaid ``flowchart TD`` body — or
    ``None`` if fewer than ``_MIN_NODES`` grounded components survive (adding a
    diagram then would mean inventing nodes).

    Returns the block BODY (starting ``flowchart TD``), NOT fenced — the caller owns
    fencing + placement via ``insert_diagram_block``."""
    comps, edges = _parse(raw)
    if not comps:
        return None
    kept = _ground(comps, artifact_text, embedder, min_cosine)
    if len(kept) < _MIN_NODES:
        return None
    return _render(kept, edges)


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


class _StubEmbedder:
    """Self-check embedder (no network): any text mentioning a grounded concept →
    shared axis 0 (so a grounded label and its section score cosine 1.0); each
    DISTINCT non-grounded string → its own private axis (so a fabricated label matches
    neither a grounded section nor an ungrounded 'References' section)."""

    _GROUNDED = ("planner", "executor", "memory", "scorer")

    def __init__(self) -> None:
        self._other: dict[str, int] = {}

    def embed(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            v = [0.0] * 129
            if any(g in low for g in self._GROUNDED):
                v[0] = 1.0
            else:
                idx = self._other.setdefault(low.strip(), len(self._other))
                v[1 + (idx % 128)] = 1.0
            out.append(v)
        return out


def _demo() -> None:
    """Runnable self-check (no network): render is valid+balanced; the SEMANTIC guard
    drops a fabricated node; fall-open (no embedder) keeps everything; insertion lands
    in a body section. `python -m studio.diagram_render`."""
    report = (
        "## Architecture\nThe Planner builds a plan. The Executor runs tools. The "
        "Memory store persists state. The Scorer grades the artifact.\n## References\n- x\n"
    )
    raw = (
        "COMPONENT: Planner | builds plan\nCOMPONENT: Executor | runs tools\n"
        "COMPONENT: Memory | persists state\nCOMPONENT: Scorer | grades output\n"
        "EDGE: Planner -> Executor\nEDGE: Executor -> Scorer\n"
    )
    emb = _StubEmbedder()

    body = render_grounded_diagram(raw, report, embedder=emb)
    assert body and body.startswith("flowchart TD"), body
    assert body.count("[") == body.count("]") and "-->" in body

    # Semantic guard: fabricated nodes (orthogonal embedding) are dropped → below floor.
    fabricated = "\n".join(f"COMPONENT: Zorptron{i} | invented" for i in range(6))
    assert render_grounded_diagram(fabricated, report, embedder=emb) is None

    # Mixed: only grounded survive; 1 grounded < floor → None.
    mixed = "COMPONENT: Planner | real\n" + fabricated
    assert render_grounded_diagram(mixed, report, embedder=emb) is None

    # Fall open (no embedder): trust extraction, keep all → renders.
    assert render_grounded_diagram(fabricated, report, embedder=None) is not None

    out = insert_diagram_block(report, body)
    assert out.index("## Architecture") < out.index("```mermaid") < out.index("## References")
    print("diagram_render self-check OK")


if __name__ == "__main__":
    _demo()
