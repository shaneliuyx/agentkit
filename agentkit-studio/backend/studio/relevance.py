"""studio.relevance — narrow binary LLM relevance check for seeded sections.

Cross-task seeding (R10, ``studio.task_runs.similar_runs``) pulls a prior
run's artifact into a NEW, merely SIMILAR task — the seed may carry claims or
citations that belong to the prior topic, not the current one. Calibration
against a real contaminated run (a "catalog management" seed carried into a
"benchmarking autonomous coding agents" task, R10 sim=0.957) showed embedding
cosine similarity CANNOT separate this drift: the contaminated section scored
HIGHER against the wrong requirement (0.887) than against its own (0.869) —
both requirements share templated research-report framing, so cosine
saturates at any granularity (section, sentence). See WORKLOG for the full
calibration trace.

An LLM classifier can read the actual claim instead of averaging embeddings.
Kept BINARY and narrow (one section vs. one task, yes/no) rather than the
open-ended pairwise "which document is better" judge in ``epoch_gate.py``
(documented there as tie-prone even on strong models) — a constrained binary
classification is a simpler task shape and more reliable.

PROMPT CALIBRATION (real fixture, the same 8 catalog-management sections vs.
the real coding-agent requirement). IMPORTANT model correction: an earlier
note here recorded a "1/8 recall ceiling" and attributed it to gemma. That
figure was actually measured against ``qwen`` (Qwen2.5-Coder-14B, a VibeProxy
fallback), NOT the deployed model. Re-run against the ACTUAL deployed model
(``gemma-4-26B-A4B-it-heretic-4bit`` via oMLX, the ``gemma`` profile), even the
naive "is this relevant / YES-or-NO" baseline already scores 7/8 recall — the
old "1/8 on gemma" claim is false and has been removed. The winning prompt is
DYNAMIC-EXEMPLAR few-shot + evidence-extraction: it ties the naive baseline on
recall (7/8) while keeping precision tight (1/8 false-flag) and is more robust
than the alternatives (few-shot-alone regressed precision to 2/8;
evidence-alone dropped recall to 3/8; a static generic exemplar dropped recall
to 5/8). The one residual false-flag is the "References" section — a
bibliography list has no topical prose sentence to quote, a structural artifact
rather than a discrimination failure — so pure references/bibliography sections
are skipped (see ``_REFERENCE_HEADINGS``).

The prompt supplies a DYNAMIC negative exemplar: when a cross-task R10 seed
fired we know the seed's ORIGINAL requirement topic, so EXAMPLE A names that
real subject as the "different subject in the same broad field". When no seed
provenance is available it falls back to a generic phrase.

Runs ONCE per section per epoch, and NEVER inside ``rubric_score`` /
``rubric_scorecard_100`` (those are called many times per epoch — see
``runner.py``'s ``_editor_scored_issues`` call sites — an LLM call inside them
would be a real cost/latency regression). Callers compute this upstream,
where a live LLM client is already in scope, and thread the plain float
result into ``rubric_score(..., relevance_penalty=...)``.
"""
from __future__ import annotations

import re
from typing import Any

#: Sections shorter than this are placeholder/stub content — too little
#: signal to classify meaningfully, so they are skipped (not flagged).
_MIN_SECTION_CHARS = 40
#: Cap on how much of a section's body is sent to the judge — keeps the call
#: cheap/fast (narrow input surface, per coordinator design guidance).
_MAX_SECTION_CHARS = 1500
#: Section headings that are pure reference/bibliography lists — no topical
#: prose sentence to quote, so every candidate prompt false-flags them. Skip
#: (don't flag, don't count) rather than build a general classifier.
_REFERENCE_HEADINGS = ("references", "sources", "bibliography", "works cited", "citations")
#: Generic negative exemplar when no cross-task seed provenance is available.
_FALLBACK_SEED_TOPIC = "a different specific subject in the same broad field"
_VERDICT_RE = re.compile(r"VERDICT:\s*(RELEVANT|IRRELEVANT)", re.IGNORECASE)

#: Whole-doc cap for the coarse seed gate — gemma's ctx is 262k so this fits in
#: one call. Calibrated at 30k on the probe fixtures (tmp/probe_coarse_seed_gate.py).
_SEED_DOC_CAP = 30000
#: The gate's verdict token (model states summaries first, verdict on the last
#: line) — take the LAST match, same convention as the calibration probe.
_SEED_VERDICT_RE = re.compile(r"\b(NOT[_\s]?RELATED|RELATED)\b", re.IGNORECASE)


def _seed_gate_prompt(doc: str, task: str) -> str:
    """Calibration-winning strict "same SPECIFIC subject" prompt.

    Verbatim from ``tmp/probe_coarse_seed_gate.py::strict_prompt`` (the variant
    that scored 100% recall on cross-field mismatch and 0/6 false-reject; the
    naive "just ask if related" variant failed at 1/6 recall).
    """
    return (
        "You gate whether an existing document is a usable STARTING DRAFT for a "
        "NEW writing task, or whether it is about a DIFFERENT subject and should "
        "be discarded. Being in the same broad field is NOT enough — the document's "
        "core subject must be the SAME specific subject as the task.\n\n"
        "Step 1: In 2-3 sentences, summarize what SPECIFIC subject this document is about.\n"
        "Step 2: Name the specific subject of the TASK.\n"
        "Step 3: Decide if they are the SAME specific subject (not merely the same field).\n\n"
        f"DOCUMENT:\n{doc}\n\n"
        f"TASK: {task}\n\n"
        "End with a final line, exactly one of: RELATED or NOT_RELATED."
    )


def seed_doc_relevance(client: Any, seed_text: str, requirement: str) -> bool:
    """Coarse whole-document seed gate: keep this seed, or drop it?

    ONE LLM call at seed-resolution time (vs. the per-section
    :func:`relevance_issues` that runs each epoch): summarize the whole seeded
    document, summarize the task, and judge whether they are the SAME specific
    subject. Returns ``True`` to KEEP the seed (verdict RELATED), ``False`` to
    DROP it (verdict NOT_RELATED, i.e. cross-field contamination).

    Additive to — not a replacement for — :func:`relevance_issues`: a whole-doc
    RELATED verdict can still coexist with real section-level contamination, so
    the per-section check keeps running regardless of this gate's verdict.

    Fail-open: any error or unparseable reply returns ``True`` (keep the seed).
    A parse error must never silently blank a document — mirrors
    :func:`relevance_issues`'s fail-open convention.
    """
    if client is None or not (seed_text or "").strip() or not (requirement or "").strip():
        return True
    try:
        reply = client.chat([{
            "role": "user",
            "content": _seed_gate_prompt(seed_text[:_SEED_DOC_CAP], requirement.strip()),
        }])
        answer = str(getattr(reply, "text", "") or "")
        hits = _SEED_VERDICT_RE.findall(answer)
        if not hits:
            return True  # unparseable → fail-open (keep seed)
        return not hits[-1].upper().replace(" ", "_").startswith("NOT")
    except Exception:  # noqa: BLE001 — a gate failure must never strand a run
        return True


def _build_prompt(topic: str, seed_topic: str, body: str) -> str:
    """The calibration-winning dynamic-exemplar few-shot + evidence prompt."""
    return (
        f"Two labeled examples (the report topic is: '{topic}'):\n\n"
        f"EXAMPLE A — this paragraph is about '{seed_topic}', which is a DIFFERENT "
        f"subject from the report topic even though both are in the same broad "
        f"field. Correct answer: IRRELEVANT.\n"
        f"EXAMPLE B — this paragraph is genuinely about '{topic}' itself. Correct "
        f"answer: RELEVANT.\n\n"
        f"Now apply the same standard, grounded in textual evidence.\n\n"
        f"You decide whether a paragraph belongs in a report on a SPECIFIC topic "
        f"by looking for TEXTUAL EVIDENCE, not by judging whether the general "
        f"field is related.\n\n"
        f"REPORT TOPIC: {topic}\n\n"
        f"PARAGRAPH:\n{body}\n\n"
        f"Task: Quote the ONE sentence from the paragraph that directly addresses "
        f"the report topic above. Do not paraphrase; copy an exact sentence. If NO "
        f"sentence in the paragraph is actually about the report topic (even if it "
        f"is about a related or adjacent subject in the same field), then no such "
        f"sentence exists.\n\n"
        f"Respond in exactly this format:\n"
        f"QUOTE: <the exact sentence, or the word NONE if no sentence addresses the topic>\n"
        f"VERDICT: <RELEVANT if you quoted a real on-topic sentence, IRRELEVANT if QUOTE is NONE>"
    )


def relevance_issues(
    client: Any,
    sections: dict[str, str],
    requirement: str,
    seed_topic: str | None = None,
) -> tuple[float, list[str]]:
    """Binary-classify each section against *requirement*; return ``(penalty, issues)``.

    Input to the LLM is deliberately narrow: the task requirement and ONE
    section's content — no other sections, no whole document, no comparison
    artifact. When *seed_topic* is given (the ORIGINAL requirement topic of the
    cross-task R10 seed this content came from) it grounds the DYNAMIC negative
    exemplar; otherwise a generic phrase is used.

    ``penalty`` is the fraction of judged sections flagged off-topic, in
    [0, 1] — feed straight to ``rubric_score(text, relevance_penalty=penalty)``.
    ``issues`` are human-readable strings, same shape as
    ``scorecard_weaknesses``/``lint_artifact`` output, for the editor's
    combined weakness list.

    Fail-open: any error (client down, bad/short section, malformed reply)
    skips that section rather than blocking the run — this check must never
    strand or fail an epoch.
    """
    if client is None or not sections or not (requirement or "").strip():
        return 0.0, []
    topic = requirement.strip()
    seed = (seed_topic or "").strip() or _FALLBACK_SEED_TOPIC
    checked = 0
    flagged: list[str] = []
    for title, body in sections.items():
        if (title or "").strip().lower() in _REFERENCE_HEADINGS:
            continue  # bibliography list — no topical sentence to quote, skip not flag
        body = (body or "").strip()
        if len(body) < _MIN_SECTION_CHARS:
            continue  # too short to meaningfully judge — skip, not flag
        checked += 1
        try:
            reply = client.chat([{
                "role": "user",
                "content": _build_prompt(topic, seed, body[:_MAX_SECTION_CHARS]),
            }])
            answer = str(getattr(reply, "text", "") or "")
            m = _VERDICT_RE.search(answer)
            if m and m.group(1).upper() == "IRRELEVANT":
                flagged.append(
                    f"section '{title}' appears unrelated to the current task "
                    f"(relevance check: IRRELEVANT)"
                )
            # unparseable reply → do NOT flag (fail-open, matches epoch_gate.py)
        except Exception:  # noqa: BLE001 — one bad classification never blocks a run
            checked -= 1
            continue
    if not checked:
        return 0.0, []
    return round(len(flagged) / checked, 4), flagged
