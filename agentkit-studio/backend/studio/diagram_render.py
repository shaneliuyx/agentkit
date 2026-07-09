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

Short all-caps acronym labels (for example "API", "MCP", "DB") are checked
against their lowercase acronym form. Other labels with no significant token are
dropped as sentence glue rather than falling open.
"""
from __future__ import annotations

import re
from typing import Any

from studio.textutil import dbg

#: Render caps (Mermaid Safe Mode). ``_MIN_NODES``: a "diagram" of 1-3 boxes is not
#: worth the accept-gate churn and usually signals the model failed to enumerate the
#: architecture. ``_MAX_NODES`` bounds rendered nodes so a runaway list stays a
#: readable flowchart (extras dropped in declared order — a partial diagram beats none).
_MIN_NODES = 4
_MAX_NODES = 15

#: Minimum length for a label token to count as a grounding signal. Below this,
#: tokens are glue words or too short to discriminate real from fabricated.
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
        "- Every <name> must be a PROPER named component (module, class, package, "
        "service, or interface) — never a bare verb, adjective, or generic "
        "sentence word.\n"
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
    "repository", "repositories", "contains", "including", "various", "over", "files",
    "examples",
})


def _significant_tokens(label: str) -> list[str]:
    """Lowercased alphanumeric tokens of ``label`` at least ``_MIN_TOKEN_LEN`` chars —
    the terms specific enough to ground against ("planner", "executor"). Short glue
    ("a", "the", "of") are dropped as non-discriminating."""
    return [t for t in re.findall(r"[a-z0-9]+", label.lower()) if len(t) >= _MIN_TOKEN_LEN]


def _short_acronym(label: str) -> str | None:
    """Groundable form for short real acronym labels; ``The``/``And`` stay invalid."""
    stripped = label.strip()
    if re.fullmatch(r"[A-Z0-9]{2,6}", stripped):
        return stripped.lower()
    return None


def _discriminating_tokens(label: str) -> list[str]:
    sig = _significant_tokens(label)
    if not sig:
        acronym = _short_acronym(label)
        return [acronym] if acronym else []
    return [t for t in sig if t not in _GENERIC_LABELS]


def _ground(comps: list[str], artifact_text: str) -> list[str]:
    """Keep a component iff ≥1 DISCRIMINATING significant token of its label literally
    appears in the report prose (word-start match, so a plural/inflection of a real term
    still counts, while a token buried inside an unrelated word does not). Deterministic,
    zero-cost.

    Real nodes survive because the model extracts the list FROM the prose (verbatim by
    construction); a fabrication sharing no ≥4-char token with the report is dropped. A
    short all-caps acronym label is checked against its acronym token; other labels with
    no significant token are dropped. A label whose ONLY significant tokens are generic
    ("System", "Data Process") is dropped as non-discriminating (C3). See the module
    docstring for why literal grounding beats the embedding-cosine guard it replaces."""
    low = artifact_text.lower()
    kept: list[str] = []
    for name in comps[:_MAX_NODES]:
        discriminating = _discriminating_tokens(name)
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


def build_diagram_block(mermaid_body: str) -> str:
    """The fenced ```mermaid block wrapper — the ONE place this format string is
    built. ``structural_producer._produce_diagram`` finds its own inserted block
    via a literal ``str.replace`` on this exact construction, so a change here
    must stay byte-identical between callers."""
    return f"```mermaid\n{mermaid_body}\n```"


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
    block = build_diagram_block(mermaid_body)
    lines = artifact_text.splitlines()

    for i, ln in enumerate(lines):  # (1) after a target body heading
        if _HEADING_RE.match(ln) and _TARGET_HEADING_RE.search(ln) and not _TAIL_HEADING_RE.match(ln):
            return "\n".join(lines[: i + 1] + ["", block, ""] + lines[i + 1 :])

    for i, ln in enumerate(lines):  # (2) before the References/Sources block
        if _TAIL_HEADING_RE.match(ln):
            return "\n".join(lines[:i] + [block, ""] + lines[i:])

    sep = "" if artifact_text.endswith("\n") else "\n"  # (3) end of document
    return f"{artifact_text}{sep}\n{block}\n"


#: A subject-tagged component line: ``COMPONENT: <name> | <subject> | <role>``.
_DIA_COMPONENT_RE = re.compile(r"COMPONENT:\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|.*$")
_DIA_EDGE_RE = re.compile(r"EDGE:\s*(.+?)\s*->\s*([^|]+?)\s*(?:\|\s*(.*))?$")
_MAX_DIA_NODES = 15
#: A cross-subject edge is grounded only if the evidence NAMES the relationship's
#: mechanism — never invented from neither a joint claim nor a per-task mechanism
#: term corroborated on each side (team-lead rule 2). The vocabulary is per-task
#: (``relationship.mechanism_terms``, R3), no longer a fixed enum.


#: Generic component-naming guidance shared by every diagram prompt. Names the
#: MORPHOLOGY of a real architectural component (proper module/class/package/
#: service names) versus sentence glue — categorically, never by listing the
#: task's actual weak words (that would game a specific failing case rather than
#: fix the class). Root cause it addresses: with rich claims naming real
#: components (e.g. an "AgentContext" class, a hyphenated core package), a weak
#: model still emitted bare prose words ("Designed", "Commits") lifted from a
#: claim sentence, and literal-token grounding could not tell them apart because
#: both appear in the prose.
_COMPONENT_NAMING_RULE = (
    "Each <name> must be a PROPER named component — a module, class, package, "
    "service, or interface as named in the evidence (multi-word names, CamelCase "
    "identifiers, and hyphenated/dotted package names are all good). Never use a "
    "bare verb, adjective, participle, or generic sentence word as a name."
)


def build_cluster_components_prompt(
    subjects: list[str], claims: list[dict[str, Any]], mechanism: str = ""
) -> str:
    subj_list = ", ".join(subjects)
    joint = [c for c in claims if len(c.get("subjects") or []) >= 2]
    others = [c for c in claims if len(c.get("subjects") or []) < 2]
    ev_lines = "\n".join(
        f"- [{','.join(c.get('subjects') or [])}] {c['claim']} (URL: {c['url']})" for c in others[:12]
    )
    # The cross-subject edge is drawn from the JOINT CLAIMS — the only evidence that
    # actually states the relationship — never from the FRAME hypothesis. Live: the
    # hypothesis guessed "Pi calls Craft via SDK task delegation" (backwards; reality
    # is Craft embeds the Pi SDK), and feeding it as a "mechanism to look for" made the
    # diagram draw that wrong-direction edge, contradicting the grounded joint claims.
    # A hypothesis directs SEARCH, it must never shape the drawn artifact.
    if joint:
        ranked_joint = sorted(
            joint, key=lambda c: _relationship_claim_score(c, subjects), reverse=True
        )
        joint_lines = "\n".join(f"- {c['claim']}" for c in ranked_joint[:6])
        edge_rule = (
            "Draw the cross-subject EDGE to reflect EXACTLY the JOINT EVIDENCE below — "
            "use the two components it names and the DIRECTION it asserts (if it says "
            "'X uses/embeds Y', the edge is X -> Y). Do NOT reverse the direction and "
            "do NOT invent a mechanism the joint evidence does not state.\n\n"
            f"JOINT EVIDENCE (states the relationship):\n{joint_lines}\n\n"
        )
    else:
        hint = f"A candidate mechanism to look for in the evidence: {mechanism}\n\n" if mechanism else ""
        edge_rule = (
            "Include at least one EDGE connecting a component from one subject to a "
            "component from the OTHER subject, labeled with the actual integration "
            "mechanism (e.g. an API, CLI, MCP, or extension-point interface) NAMED in "
            f"the evidence below — never invent a mechanism.\n\n{hint}"
        )
    return (
        "List the architecture of the subjects below as plain lines — no prose, "
        "no markdown, no code fences:\n"
        f"COMPONENT: <name> | <subject, EXACTLY one of: {subj_list}> | <one-line role>\n"
        "EDGE: <name A> -> <name B> | <optional label>\n\n"
        f"Cover BOTH subjects ({subj_list}), several components each. {edge_rule}"
        f"{_COMPONENT_NAMING_RULE}\n\n"
        f"EVIDENCE:\n{ev_lines}"
    )


def _parse_cluster_diagram(
    raw: str, subjects: list[str]
) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    """``(components, edges)`` — components is ``[(name, subject)]``, order-
    preserving, deduped by subject+lowercase name; a component whose subject
    doesn't match one of *subjects* (loosely, case/substring-insensitive) is
    dropped."""
    components: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    edges: list[tuple[str, str, str]] = []
    for ln in raw.splitlines():
        ln = ln.strip()
        m = _DIA_COMPONENT_RE.match(ln)
        if m:
            name, subj_raw = m.group(1).strip(), m.group(2).strip().lower()
            matched = next((s for s in subjects if s.lower() == subj_raw), None) or next(
                (s for s in subjects if s.lower() in subj_raw or subj_raw in s.lower()), None
            )
            if not (name and matched):
                continue
            key = (matched.lower(), name.lower())
            if key not in seen:
                seen.add(key)
                components.append((name, matched))
            continue
        m = _DIA_EDGE_RE.match(ln)
        if m:
            edges.append((m.group(1).strip(), m.group(2).strip(), (m.group(3) or "").strip()))
    return components, edges


def _ground_components(components: list[tuple[str, str]], grounding_text: str) -> list[tuple[str, str]]:
    """Keep a component iff >=1 significant (>=4 char) token of its name is
    literally present in *grounding_text* — same literal-token principle as
    ``diagram_render._ground`` (reused directly, not re-derived)."""
    low = grounding_text.lower()
    kept = []
    for name, subject in components[:_MAX_DIA_NODES]:
        discriminating = _component_label_tokens(name)
        if discriminating and any(re.search(r"\b" + re.escape(t), low) for t in discriminating):
            kept.append((name, subject))
    return kept


def _cross_cluster_edges(
    components: list[tuple[str, str]], edges: list[tuple[str, str, str]]
) -> list[tuple[str, str, str]]:
    """Edges whose two endpoints belong to DIFFERENT subject clusters."""
    subjects_of: dict[str, list[str]] = {}
    for name, subject in components:
        subjects_of.setdefault(name.lower(), [])
        if subject not in subjects_of[name.lower()]:
            subjects_of[name.lower()].append(subject)
    out = []
    for a, b, label in edges:
        a_subjects, b_subjects = subjects_of.get(a.lower(), []), subjects_of.get(b.lower(), [])
        pairs = [(sa, sb) for sa in a_subjects for sb in b_subjects if sa != sb]
        if len(pairs) == 1:
            out.append((a, b, label))
    return out


#: Generic English relationship cues (a component USES/EMBEDS/POWERS another) — used
#: to surface the joint claim that states the relationship above incidental co-mentions.
#: Language-level, not task vocabulary (same category as a programming-language set).
_REL_VERB_RE = re.compile(
    r"\b(uses?|utiliz\w+|embed\w+|integrat\w+|power\w+|calls?|wrap\w+|leverag\w+|"
    r"combin\w+|depend\w+|support\w+|extend\w+|implement\w+|provid\w+|expos\w+|"
    r"built\s+on|based\s+on|backend\s+(?:for|of)|run\w*\s+on|plugs?\s+into|"
    r"compatible\s+with|works?\s+with|side\s+by\s+side)\b",
    re.IGNORECASE,
)


def _relationship_claim_score(claim: dict[str, Any], subjects: list[str]) -> int:
    text = str(claim.get("claim") or "").lower()
    return len(_REL_VERB_RE.findall(text)) + sum(1 for s in subjects if s.lower() in text)


#: Structural stopwords dropped before grounding a RELATION phrase — a use-verb is
#: kept (it IS the stated mechanism and must ground against the cited claim).
_TRIPLE_STOPWORDS = frozenset({
    "the", "and", "for", "with", "via", "both", "into", "that", "this", "its", "are", "was",
})


def _extract_relation_triple(  # noqa: PLR0911
    subjects: list[str], joint_claims: list[dict[str, Any]], client: Any
) -> tuple[str, str, str, str] | None:
    """A grounded, directional ``(source_subject, relation, target_subject)`` triple
    for the integration edge — the KGGen/GraphRAG pattern (LLM extracts an ordered
    SPO triple; code assembles the edge) rather than letting the model free-draw the
    edge, which hallucinated BOTH direction and label (live: 'Pi -> Craft' labelled
    'craft agents' when the evidence says Craft embeds the Pi SDK). Two guardrails,
    both required to return a triple (else None → caller keeps the old LLM edge):
      • CON-1 (over-general/invented relation, per iText2KG's merge risk): the
        source/target must BE the two subjects and the RELATION must carry >=1
        content token that literally appears in a joint claim.
      • CON-2 (ambiguous direction): an independent positional cross-check — in an
        active-voice claim 'A <verb> B' the source precedes the target; the triple's
        direction must agree, else it is unconfirmed and rejected.
    Generic: keyed on the two ``subjects`` + joint-claim text, no task literal."""
    if client is None or len(subjects) < 2 or not joint_claims:
        return None
    a, b = subjects[0], subjects[1]
    # Rank so the joint claims that actually STATE a relationship lead (not version/
    # preset noise); the LLM then cites WHICH one it used (provenance — Triple Context
    # Restoration, arXiv 2501.15378), and relation + direction are validated against
    # THAT single claim, so one incidentally-grounded token can't launder an invented
    # phrase (codex). Generic — keyed on subjects + joint-claim text, no task literal.
    ranked = sorted(
        joint_claims, key=lambda c: _relationship_claim_score(c, subjects), reverse=True
    )[:6]
    jl = "\n".join(f"- {c['claim']}" for c in ranked)
    prompt = (
        f"The claims below state how {a} and {b} relate. Output EXACTLY ONE line:\n"
        "SOURCE | RELATION | TARGET\n"
        f"SOURCE and TARGET are {a} or {b}: SOURCE uses/embeds/calls/powers the other, "
        "TARGET is the one used. RELATION is that mechanism as a short verb phrase "
        "(2-4 words) COPIED from a claim. Use ONLY what the claims state; if none "
        f"states a directed relationship, output the single word NONE.\n\nCLAIMS:\n{jl}"
    )
    try:
        reply = client.chat([{"role": "user", "content": prompt}])
        text = str(getattr(reply, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 — a bad triple call never breaks the run
        dbg(f"diagram_render _extract_relation_triple: call failed exc={exc!r}")
        return None
    line = next((ln for ln in text.splitlines() if ln.count("|") >= 2), "")
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 3:
        return None
    raw_src, rel, raw_tgt = parts[0], parts[1], parts[2]

    def _as_subject(x: str) -> str | None:
        xl = x.lower()
        return next((s for s in subjects if s.lower() in xl or xl in s.lower()), None)

    src_s, tgt_s = _as_subject(raw_src), _as_subject(raw_tgt)
    if not src_s or not tgt_s or src_s == tgt_s:
        return None
    rel_tokens = [t for t in re.findall(r"[a-z]{3,}", rel.lower()) if t not in _TRIPLE_STOPWORDS]
    if not rel_tokens:
        return None
    # Provenance (codex / Triple-Context-Restoration): CODE selects the ONE confirming
    # claim — the highest-ranked joint claim that names BOTH subjects AND contains every
    # RELATION content token — rather than trusting a weak model to cite it (asking
    # gemma for a claim number made it pick a worse claim, live). CON-1 (relation
    # grounded) is then folded into that selection: an invented phrase like 'sdk
    # delegation' matches no claim → None.
    confirming = next(
        (c["claim"] for c in ranked
         if src_s.lower() in c["claim"].lower() and tgt_s.lower() in c["claim"].lower()
         and all(t in c["claim"].lower() for t in rel_tokens)),
        None,
    )
    if not confirming:
        dbg(f"diagram_render _extract_relation_triple: no confirming claim for rel={rel!r}")
        return None
    # CON-2: direction confirmed by an explicit active/passive pattern in that claim
    # ('A uses B' AND 'B is used by A'); no pattern → reject rather than guess.
    if not _direction_ok(src_s, tgt_s, confirming):
        dbg(f"diagram_render _extract_relation_triple: direction unconfirmed {src_s!r}->{tgt_s!r}")
        return None
    return (src_s, " ".join(rel.split())[:40], tgt_s, confirming)


#: A passive-voice cue ('is used by', 'powered by', 'is embedded by') — its presence
#: between target and source flips the surface order back to source→target.
_PASSIVE_RE = re.compile(
    r"\b(?:is|are|was|were|be|being|been)\s+\w+ed\s+by\b|"
    r"\b(?:used|powered|embedded|driven|backed|built|based)\s+(?:on\s+|by\s+)",
    re.IGNORECASE,
)


def _direction_ok(src_s: str, tgt_s: str, claim: str) -> bool:
    """``src_s -> tgt_s`` confirmed by an explicit pattern in the CITED claim:
    active ``{src} <verb> {tgt}`` (src before tgt, no passive marker between) OR
    passive ``{tgt} ... <verb>ed by {src}`` (tgt before src with a passive marker).
    No explicit pattern → False, so an ambiguous direction is dropped, never guessed."""
    low = claim.lower()
    s, t = src_s.lower(), tgt_s.lower()
    si, ti = low.find(s), low.find(t)
    if si < 0 or ti < 0:
        return False
    seg = low[min(si, ti): max(si, ti) + max(len(s), len(t))]
    passive = bool(_PASSIVE_RE.search(seg))
    return (si < ti) != passive  # src-before-tgt active, or tgt-before-src passive


def _pick_endpoint(
    subject: str, components: list[tuple[str, str]], confirming: str, rel: str
) -> str | None:
    """The *subject*'s component best NAMED by the cited claim / relation (prefer a
    node whose token appears there — e.g. an 'SDK'/'backend' node over an incidental
    config constant); else its first grounded component (codex: first-component alone
    picked a noisy 'OPENAI_COMPAT_…' node instead of 'Craft Agents')."""
    subj_comps = [n for n, s in components if s == subject]
    if not subj_comps:
        return None
    hay = (confirming + " " + rel).lower()
    return max(
        subj_comps,
        key=lambda n: sum(1 for tok in _component_label_tokens(n) if tok in hay),
    )


def _triple_cross_edge(
    triple: tuple[str, str, str, str] | None, components: list[tuple[str, str]]
) -> list[tuple[str, str, str]] | None:
    """Deterministic cross-cluster edge from the grounded triple: connect the SOURCE
    subject's best-named component to the TARGET subject's, in the triple's direction,
    labelled with its relation. None if either subject has no grounded component (then
    the caller keeps the LLM-derived edge)."""
    if not triple:
        return None
    src_s, rel, tgt_s, confirming = triple
    src_comp = _pick_endpoint(src_s, components, confirming, rel)
    tgt_comp = _pick_endpoint(tgt_s, components, confirming, rel)
    if not src_comp or not tgt_comp:
        return None
    return [(src_comp, tgt_comp, rel)]


def _mermaid_safe_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", text) or "S"


def _render_cluster_diagram(
    components: list[tuple[str, str]], edges: list[tuple[str, str, str]], subjects: list[str]
) -> str:
    """Names + edges → a guaranteed-valid mermaid ``flowchart TD`` with one
    ``subgraph`` per subject cluster. Valid by construction: synthetic ``Nx``
    ids (never the raw label), every label quoted, edges only between rendered
    nodes — same pattern as ``_render``, extended with clusters."""
    ids: dict[tuple[str, str], str] = {}
    ids_by_name: dict[str, list[tuple[str, str]]] = {}
    lines = ["flowchart TD"]
    i = 0
    for subject in subjects:
        names = [name for name, s in components if s == subject]
        if not names:
            continue
        lines.append(f'    subgraph {_mermaid_safe_id(subject)}["{subject}"]')
        for name in names:
            nid = f"N{i}"
            i += 1
            key = (subject.lower(), name.lower())
            ids[key] = nid
            ids_by_name.setdefault(name.lower(), []).append(key)
            lines.append(f'        {nid}["{name.replace(chr(34), chr(39))}"]')
        lines.append("    end")
    for a, b, label in edges:
        a_keys = ids_by_name.get(a.lower(), [])
        b_keys = ids_by_name.get(b.lower(), [])
        pairs = [(ak, bk) for ak in a_keys for bk in b_keys if ak[0] != bk[0]]
        if not pairs:
            pairs = [(ak, bk) for ak in a_keys for bk in b_keys]
        if not pairs:
            continue
        ak, bk = pairs[0]
        ida, idb = ids.get(ak), ids.get(bk)
        if not ida or not idb or ida == idb:
            continue
        if label:
            lines.append(f'    {ida} -->|"{label.replace(chr(34), chr(39))}"| {idb}')
        else:
            lines.append(f"    {ida} --> {idb}")
    return "\n".join(lines)


_FEATURE_STOPWORDS = {
    "about", "agent", "agents", "allow", "allows", "also", "and", "based",
    "because", "being", "both", "built", "called", "calls", "can", "connect",
    "connects", "contains", "could", "data", "design", "develop",
    "development", "docs", "documentation", "examples", "files", "from",
    "has", "have", "include", "includes", "including", "into", "like",
    "local", "main", "minimal",
    "named", "over", "platform", "provides", "repository", "repositories",
    "report", "research", "source", "sources", "subject", "support",
    "supports", "task", "tasks", "that", "their", "through", "used", "uses",
    "using", "various", "with", "workflow", "workflows",
}


def _component_label_tokens(label: str) -> list[str]:
    """Discriminating lowercase tokens that make a diagram label component-like.

    This is intentionally generic: it rejects sentence glue and architecture
    nouns by category, not task-specific names. Short all-caps acronyms (API,
    MCP) are allowed because they are common real component/interface labels.
    """
    sig = _significant_tokens(label)
    if not sig:
        stripped = label.strip()
        return [stripped.lower()] if re.fullmatch(r"[A-Z0-9]{2,6}", stripped) else []
    return [t for t in sig if t not in _GENERIC_LABELS and t not in _FEATURE_STOPWORDS]


def render_subject_cluster_diagram(
    raw: str,
    grounding_text: str,
    *,
    subjects: list[str],
    claims: list[dict[str, Any]],
    integration_label: str | None = None,
    client: Any = None,
) -> str | None:
    """Build a subject-clustered integration mermaid BODY (flowchart TD with one
    subgraph per subject) from the model's COMPONENT/EDGE lines + the grounded
    directional triple. Returns the body or None. Caller owns caption/lint/splice.
    `integration_label` is the pre-computed fallback edge label (research_first
    passes `_integration_label(...)` so the Relationship type never enters here)."""
    components, edges = _parse_cluster_diagram(raw, subjects)
    components = _ground_components(components, grounding_text)
    joint = [c for c in claims if len(c.get("subjects") or []) >= 2]
    triple = _extract_relation_triple(subjects, joint, client)
    cross = _triple_cross_edge(triple, components) or _cross_cluster_edges(components, edges)
    if len(components) >= 2 and cross and (triple or integration_label):
        return _render_cluster_diagram(components, cross, subjects)
    return None


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

    # Short all-caps acronym labels survive only when the report actually names them.
    acronym_report = report + "\nAPI DB MCP CLI are named interfaces.\n"
    short = "\n".join(f"COMPONENT: {n} | x" for n in ("API", "DB", "MCP", "CLI")) + "\nEDGE: API -> DB\n"
    assert render_grounded_diagram(short, acronym_report) is not None

    out = insert_diagram_block(report, body)
    assert out.index("## Architecture") < out.index("```mermaid") < out.index("## References")
    print("diagram_render self-check OK")


if __name__ == "__main__":
    _demo()
