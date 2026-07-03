"""studio.requirement_compliance — generic explicit-requirement checker.

A task's free-text description often states EXPLICIT, checkable requirements —
"include a diagram", "include example code", "cite at least 3 sources", "keep
it under 800 words", "must cover X and Y". The deterministic rubric
(``studio.rubric``) scores generic research-report quality dimensions and is
BLIND to whatever the user literally asked for on this specific task, so a
generated artifact can score well while silently missing a stated requirement.

This module closes that gap the SAME way ``studio.relevance`` handles seed
contamination: a narrow LLM check whose plain ``(penalty, issues)`` result
threads into the existing rubric penalty + editor weakness-list machinery. It
is deliberately GENERIC — nothing here hardcodes "diagram"/"citation"/any
keyword; the model does dynamic extraction and verification from the actual
task text every run, so it works for any requirement a user might state.

Two calls with different cadence (mirrors seed-gate vs. per-section in
``relevance``):

* :func:`extract_requirements` — ONE call per RUN. The task text is static for
  a given ``task_hash``, so the caller computes this once and caches it (see
  runner.py's ``_epoch_requirements``), exactly as ``_seed_topic`` is threaded.
  It preserves OR/alternative STRUCTURE: the return is a list of requirement
  GROUPS, each group a list of interchangeable branches (a single-branch group
  is a plain mandatory requirement; a multi-branch group is an OR clause the
  user phrased as "X or Y", satisfied by ANY one branch).
* :func:`requirement_compliance_issues` — ONE call per EPOCH, on the assembled
  artifact. Verifies each BRANCH separately, then judges each GROUP: a group is
  SATISFIED when any branch is, unsatisfied only when NO branch is. It returns
  ``(penalty, hard_issues, quality_opportunities)``:

  * ``penalty`` — the unsatisfied-GROUP fraction (same [0,1] scale as
    ``relevance_issues``); feeds ``rubric_score(compliance_penalty=…)``.
  * ``hard_issues`` — genuine misses (a mandatory item unmet, or an OR group
    where NO branch is met) — the editor weakness list, exactly as before.
  * ``quality_opportunities`` — unsatisfied SIBLING branches of an OR group that
    is ALREADY satisfied by another branch. These are honest non-requirements:
    the OR is met, so they NEVER touch ``penalty`` or ``hard_issues``. They are
    surfaced only as optional "consider also" polish for the editor (e.g. the
    task said "example code OR a diagram", the artifact has code, so a diagram
    is a nice-to-have, not a miss). This is the split Codex's review asked for:
    keep hard compliance honest, but still let architecture-heavy reports
    reliably GAIN the visual they omitted without ever failing the requirement.

Fail-open on every error path (client down, timeout, unparseable reply, empty
input): a compliance check that fails must never block a run or fabricate a
weakness — mirrors ``relevance_issues`` / ``seed_doc_relevance``.
"""
from __future__ import annotations

import re
from typing import Any

#: Cap on task text sent to the extractor — a task description is short; this
#: only guards against a pathological paste.
_MAX_TASK_CHARS = 4000
#: Cap on artifact text sent to the verifier. gemma's ctx is 262k so this fits
#: in one call; kept bounded so the per-epoch call stays cheap.
# ponytail: a requirement about tail content (e.g. a diagram at char 45k) could
# be missed if the artifact exceeds this; raise the cap if that shows up in calibration.
_MAX_ARTIFACT_CHARS = 40000
#: Extractor emits one requirement per line, or this exact sentinel for none.
_NONE_SENTINEL = "NONE"
#: Strip list markers ("1.", "-", "*", "•") from an extracted line.
_LIST_PREFIX_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*")
#: Split OR-alternative branches the extractor put on one line ("A || B || C").
_ALT_SEP_RE = re.compile(r"\s*\|\|\s*")
_VERDICT_RE = re.compile(r"REQUIREMENT\s+(\d+)\s*:\s*(SATISFIED|NOT[_\s]?SATISFIED)", re.IGNORECASE)

#: Diagram-shaped requirement phrasing ("include a diagram", "design architecture",
#: "topology map") is held to a stricter SATISFIED bar than prose-shaped requirements:
#: a real gemma run showed the verifier marking such a branch SATISFIED on a markdown
#: TABLE + descriptive prose alone, with no actual diagram anywhere in the artifact —
#: the requirement is honestly ambiguous between "discuss the architecture" and "show
#: a visual of it", and the verifier's evidence-grounded SATISFIED call was defensible
#: per the literal wording, but it means the structural-editor retry never even gets a
#: chance to run (the opportunity never exists upstream). Deliberately keyword-narrow
#: (not a general "structural" classifier) so it never touches a requirement that
#: never asked for a visual in the first place — see module docstring for the
#: OR-opportunity split this feeds into.
_DIAGRAM_SHAPED_RE = re.compile(
    r"(?i)\b(diagram|architecture|topology|blueprint|wireframe|flow\s*chart)\b"
)
_MERMAID_BLOCK_RE = re.compile(r"```mermaid\b")


class ComplianceCheckUnavailable(Exception):
    """Raised (only in ``strict=True``) when a compliance check could not actually
    run — client down, LLM error, or unparseable reply. Lets a caller that needs
    to distinguish "verified: nothing found" from "verification failed" (the editor
    opportunity-recount tie-breaker) treat the fail-open case as UNKNOWN instead of
    misreading an empty result as a real zero."""


def _extract_prompt(task: str) -> str:
    return (
        "You extract the EXPLICIT, CHECKABLE requirements a user literally stated "
        "in a task description.\n\n"
        "A CHECKABLE requirement is a concrete, verifiable instruction about the "
        "DELIVERABLE — something you could inspect the finished document and "
        "objectively confirm was done or not done. Examples: 'include a diagram', "
        "'include example code', 'cite at least 3 sources', 'keep it under 800 "
        "words', 'must cover both X and Y', 'include a comparison table'.\n\n"
        "Some requirements are phrased as an ALTERNATIVE — the user offers a "
        "CHOICE where satisfying ANY ONE option is enough ('include example code "
        "OR a design architecture', 'a table or a chart'). For such a requirement, "
        "put ALL the alternative options on ONE line separated by ' || ' (e.g. "
        "'include example code || include a design architecture'). A requirement "
        "with no alternative goes on its own line as a single check.\n\n"
        "Do NOT list vague quality goals that cannot be objectively checked "
        "('be thorough', 'make it good', 'research it well', 'be clear'). Do NOT "
        "invent requirements the task did not state. Extract ONLY what is "
        "literally asked for.\n\n"
        f"TASK:\n{task}\n\n"
        "List each explicit checkable requirement on its own line (using ' || ' "
        "between the options when the requirement is a choice), worded as a short "
        "verifiable check. Output ONLY the list, nothing else. If the task states "
        f"NO explicit checkable requirement, output exactly: {_NONE_SENTINEL}"
    )


def extract_requirements(client: Any, task_text: str) -> list[list[str]]:
    """Enumerate the EXPLICIT, checkable requirements literally stated in *task_text*.

    ONE LLM call — call once per run and cache (the task text is static for a
    given ``task_hash``). Returns a list of requirement GROUPS: each group is a
    list of interchangeable branches. A single-branch group is a plain mandatory
    requirement; a multi-branch group is an OR clause ("X or Y") satisfied by ANY
    one branch. Returns ``[]`` when the task states none (or on any failure —
    fail-open).
    """
    if client is None or not (task_text or "").strip():
        return []
    try:
        reply = client.chat([{
            "role": "user",
            "content": _extract_prompt(task_text.strip()[:_MAX_TASK_CHARS]),
        }])
        answer = str(getattr(reply, "text", "") or "")
    except Exception:  # noqa: BLE001 — extraction failure must never strand a run
        return []
    groups: list[list[str]] = []
    for line in answer.splitlines():
        req = _LIST_PREFIX_RE.sub("", line).strip()
        if not req or req.upper() == _NONE_SENTINEL:
            continue
        branches = [b.strip() for b in _ALT_SEP_RE.split(req) if b.strip()]
        if branches:
            groups.append(branches)
    return groups


def _normalize_groups(requirements: Any) -> list[list[str]]:
    """Coerce the requirements arg to ``list[list[str]]`` (groups of branches).

    Accepts the structured shape ``extract_requirements`` now returns AND a plain
    ``list[str]`` (each string → a single-branch mandatory group), so a caller or
    cached value in the older flat shape still works."""
    groups: list[list[str]] = []
    for item in requirements or []:
        if isinstance(item, str):
            s = item.strip()
            if s:
                groups.append([s])
        else:
            branches = [b.strip() for b in item if b and str(b).strip()]
            if branches:
                groups.append(branches)
    return groups


#: Appended when a branch was downgraded by the diagram-shape gate below — an
#: editor given only the bare branch text (e.g. "include design architecture")
#: reasonably reads an EXISTING table/prose section as already addressing it
#: and revises that instead of adding the missing diagram (real observed
#: behavior: a live gemma run repeatedly edited an existing "Modular
#: Architecture Overview" table rather than adding a mermaid block). Naming
#: the required FORM here — not any task content — closes that gap generically.
_DIAGRAM_REQUIRED_NOTE = (
    " (a real diagram — a fenced ```mermaid``` block — is required; an existing "
    "table or prose description does not satisfy this)"
)


def _hard_issue_str(branches: list[str], *, downgraded: bool = False) -> str:
    note = _DIAGRAM_REQUIRED_NOTE if downgraded else ""
    if len(branches) == 1:
        return (
            f"stated task requirement not satisfied: '{branches[0]}'{note} "
            f"(compliance check: NOT_SATISFIED — the artifact does not fulfil it)"
        )
    joined = " OR ".join(f"'{b}'" for b in branches)
    return (
        f"stated task requirement not satisfied: none of the stated alternatives "
        f"were met ({joined}){note} (compliance check: NOT_SATISFIED — the artifact "
        f"fulfils no branch of this OR requirement)"
    )


def _opportunity_str(branch: str, *, downgraded: bool = False) -> str:
    note = _DIAGRAM_REQUIRED_NOTE if downgraded else ""
    return (
        f"Explicit alternative not included: {branch}{note} (an OR-requirement already "
        f"satisfied by another branch — optional polish, not a requirement miss)"
    )


def _verify_prompt(requirements: list[str], artifact: str) -> str:
    numbered = "\n".join(f"{n}. {r}" for n, r in enumerate(requirements, 1))
    # Evidence-grounded verdicts: demanding the model NAME the concrete evidence
    # before marking SATISFIED is what fixed the naive prompt's false negatives in
    # calibration (a doc with every real citation stripped was wrongly passed as
    # "citations present" until grounding was required). Mirrors the winning
    # "quote the on-topic sentence" design in studio.relevance.
    return (
        "You verify whether a written DOCUMENT satisfies each EXPLICIT requirement "
        "from a task. Judge ONLY from the document text below — never assume "
        "anything that is not actually present in it.\n\n"
        f"REQUIREMENTS:\n{numbered}\n\n"
        f"DOCUMENT:\n{artifact}\n\n"
        "For EACH requirement, look for CONCRETE evidence in the document that "
        "fulfils it. Mark SATISFIED only if you can point to that real evidence "
        "(quote or name it). Mark NOT_SATISFIED if the requirement is missing, "
        "empty, or met only by a placeholder / heading with no real content behind "
        "it. Respond with one line per requirement in EXACTLY this format:\n"
        "REQUIREMENT <number>: SATISFIED — <the concrete evidence you found>\n"
        "or\n"
        "REQUIREMENT <number>: NOT_SATISFIED — <what is missing>"
    )


def requirement_compliance_issues(
    client: Any,
    extracted_requirements: Any,
    artifact_text: str,
    *,
    strict: bool = False,
) -> tuple[float, list[str], list[str]]:
    """Judge *artifact_text* per requirement GROUP; return
    ``(penalty, hard_issues, quality_opportunities)``.

    ONE LLM call per epoch: the model reads the assembled artifact once and marks
    each BRANCH of each group SATISFIED / NOT_SATISFIED. Group verdict = OR over
    its branches (satisfied if ANY branch is). From that:

    * ``penalty`` — the fraction of GROUPS with NO branch satisfied, in [0, 1];
      feed straight to ``rubric_score(text, compliance_penalty=penalty)``.
    * ``hard_issues`` — one string per unsatisfied group (a mandatory item unmet,
      or an OR group where no branch is met), for the editor weakness list.
    * ``quality_opportunities`` — one string per unsatisfied SIBLING branch of a
      group that IS satisfied by another branch. NEVER counted in ``penalty`` or
      ``hard_issues``: the requirement is honestly met, so these are optional
      polish only (see module docstring).

    ``extracted_requirements`` may be the structured ``list[list[str]]`` or a
    plain ``list[str]`` (each → a single-branch group). Fail-open: any error or
    unparseable reply returns ``(0.0, [], [])`` — a broken check must never block
    a run or fabricate weaknesses. When ``strict=True`` those same fail-open paths
    instead raise ``ComplianceCheckUnavailable`` so a caller can tell "verified:
    nothing" from "could not verify" (the empty tuple is ambiguous otherwise).
    """
    groups = _normalize_groups(extracted_requirements)
    if client is None or not groups or not (artifact_text or "").strip():
        if strict:
            raise ComplianceCheckUnavailable("no client / no requirements / empty artifact")
        return 0.0, [], []
    # Verify every branch independently. flat[i] = (group_index, branch_text).
    flat: list[tuple[int, str]] = [
        (gi, branch) for gi, branches in enumerate(groups) for branch in branches
    ]
    branch_texts = [b for _, b in flat]
    try:
        reply = client.chat([{
            "role": "user",
            "content": _verify_prompt(branch_texts, artifact_text[:_MAX_ARTIFACT_CHARS]),
        }])
        answer = str(getattr(reply, "text", "") or "")
    except Exception as exc:  # noqa: BLE001 — one bad verification never blocks a run
        if strict:
            raise ComplianceCheckUnavailable("verifier LLM call failed") from exc
        return 0.0, [], []
    verdicts: dict[int, bool] = {}  # 1-based flat branch index → satisfied?
    for m in _VERDICT_RE.finditer(answer):
        idx = int(m.group(1))
        if 1 <= idx <= len(flat):
            verdicts[idx] = "NOT" not in m.group(2).upper()
    if not verdicts:
        if strict:
            raise ComplianceCheckUnavailable("verifier reply had no parseable verdicts")
        return 0.0, [], []  # unparseable → fail-open
    if strict and len(verdicts) < len(flat):
        # Partial parse: the model answered but dropped/garbled a verdict for some
        # branch it was asked to verify. Non-strict skips such a branch per-group
        # (fail-open); strict must NOT — an unverified branch could be the very
        # OR-sibling the recount counts, so an artificially-low (possibly 0) count
        # would misread "never checked" as "satisfied". Raise at the same
        # granularity as the whole-response fail-open cases above.
        raise ComplianceCheckUnavailable(
            f"verifier parsed only {len(verdicts)} of {len(flat)} branch verdicts (partial parse)"
        )
    # Deterministic diagram-shape gate (post-parse, pre-aggregation): a SATISFIED
    # verdict on a diagram-shaped branch is only trusted if the artifact actually
    # contains a real structural block. This does not change how many verdicts were
    # PARSED (strict-mode's partial-parse check above is unaffected) — it only
    # overrides the boolean value of specific already-parsed verdicts before they
    # feed group aggregation, so a mandatory diagram-shaped requirement correctly
    # becomes a hard issue and an OR-sibling correctly becomes a quality opportunity.
    diagram_downgraded: set[int] = set()
    if not _MERMAID_BLOCK_RE.search(artifact_text or ""):
        for fi, (_, branch) in enumerate(flat, 1):
            if verdicts.get(fi) and _DIAGRAM_SHAPED_RE.search(branch):
                verdicts[fi] = False
                diagram_downgraded.add(fi)
    # Aggregate branch verdicts back per group (only branches that got a verdict).
    per_group: dict[int, list[tuple[str, bool, bool]]] = {}
    for fi, (gi, branch) in enumerate(flat, 1):
        if fi in verdicts:
            per_group.setdefault(gi, []).append((branch, verdicts[fi], fi in diagram_downgraded))
    hard_issues: list[str] = []
    quality_opportunities: list[str] = []
    counted = 0
    unsatisfied = 0
    for gi, branches in enumerate(groups):
        bv = per_group.get(gi)
        if not bv:
            continue  # no parseable verdict for this group → not counted (fail-open per group)
        counted += 1
        if any(sat for _, sat, _ in bv):
            # Group satisfied. For a multi-branch (OR) group, each unmet sibling
            # is an optional opportunity — NEVER a hard miss.
            if len(branches) > 1:
                quality_opportunities.extend(
                    _opportunity_str(branch, downgraded=downgraded)
                    for branch, sat, downgraded in bv if not sat
                )
        else:
            unsatisfied += 1
            group_downgraded = any(downgraded for _, _, downgraded in bv)
            hard_issues.append(_hard_issue_str(branches, downgraded=group_downgraded))
    penalty = round(unsatisfied / counted, 4) if counted else 0.0
    return penalty, hard_issues, quality_opportunities
