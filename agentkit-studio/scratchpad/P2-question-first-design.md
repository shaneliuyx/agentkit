# P2 — Question-first planning (spec)

**Date:** 2026-07-11 · **Slice:** §12 slice 12 · **Risk:** medium (prompt-level, generation behavior)

## Objective

Research is driven by the requirement's explicit **questions**, not subject-generic
gathering. A report answers what was asked, not merely "describes the subjects".

## Problem (grounded in current code)

`_research` (`research_first.py:721`) loops per subject; queries are subject-generic
(`research_first.py:767`):
```python
queries = [f'"{descriptor}"', f"{descriptor} {anchor_phrase}", f"{subject} {anchor_phrase} example"]
```
A requirement "how does X handle concurrency and what are its limits?" never issues a
concurrency or limits query. Those answers appear only if a general page happens to
carry them → specific sub-questions are silently unaddressed, with no coverage signal.

## Success metric (verifiable)

On a 2+-question requirement: **each extracted question has ≥1 grounded claim that
answers it.** Measured by a per-question coverage dimension in `coverage.json`;
live-verified on a multi-question task. Today: no per-question signal exists.

## Design fork

| Option | What changes | Cost | Verdict |
|---|---|---|---|
| **A — question queries feed the existing per-subject loop** | FRAME extracts questions, tags each to a subject (or `__joint__`); `_research` derives that subject's queries from its questions instead of bare descriptor+anchors. Reuses the whole gated fetch loop + coverage ledger. | low (query construction only) | **RECOMMENDED** |
| B — questions as a first-class research unit | new per-question fetch loop alongside per-subject; ledger keyed by question | high — doubles fetch loops (wall-clock on slow oMLX), question-spanning-subjects breaks anchor gating | over-built |
| C — questions steer CLAIMS extraction only | keep subject-generic search; pass questions into `_extract_claims_from_source` | lowest | does NOT fix root gap (an answer never *fetched* can't be extracted) |

**Recommendation: A.** It is the "questions drive queries" essence with least structural
risk — the per-subject loop, offtopic/anchor/name gates, and coverage ledger all stay;
only *what gets searched* changes. C leaves the root failure (unfetched answers) intact;
B adds real wall-clock cost on the oMLX bottleneck for marginal gain.

## Approach A — concrete plan

1. **FRAME: `_extract_questions(client, requirement, subjects) -> dict[str, list[str]]`**
   — LLM decomposes the requirement into research questions, each tagged to a subject
   (or `__joint__` for cross-subject questions). Deterministic validation: question is
   non-empty, ≤ ~15 words, references a known subject/anchor or is joint. Fail-open to
   `{}` (→ current subject-generic behavior, zero regression).

2. **RESEARCH: derive queries from questions.** In `_research`, when a subject has
   questions, build its query list from them (`f"{descriptor} {question}"` +
   anchor-grounded variants) instead of the bare-anchor triple; keep the bare-anchor
   query as a fallback so a subject with no good questions still gathers. All existing
   gates (`_is_offtopic`/`_anchor_hits`/`_subject_name_absent`) unchanged — questions
   only change the query strings entering `_run_query`.

3. **Coverage: per-question dimension.** `coverage.json` gains
   `questions: [{q, subject, answered: bool}]`; `answered` = ≥1 claim whose subject
   matches and whose text/quote covers the question's key terms. Emitted on the existing
   `coverage` SSE event. Feeds a new **editorial row E-Q** (question-coverage) later —
   NOT this slice; this slice only records the dimension.

4. **Fail-open everywhere.** No questions extracted → byte-identical to today. A
   question loop error → fall back to the subject-generic queries. Never stalls research.

## Test plan

- Unit: `_extract_questions` validation (drops whole-task-shaped / subject-absent /
  overlong; fail-open `{}`); query-derivation (questions present → question queries,
  empty → bare-anchor triple unchanged — **byte-identical** regression lock).
- Unit: per-question coverage `answered` join (claim matches question terms → True;
  no matching claim → False).
- Live: a 2-question requirement → each question `answered: true` in coverage.json.

## Result (live-verified 2026-07-11, s_3455ed3e225f, v92 0.646 completed)

- **Primary mechanism ACCEPTED:** FRAME decomposed 5 questions (Pi×1, Craft×1,
  joint×3); they DROVE the search queries (SSE log: question-derived searches);
  per-question coverage recorded in `coverage.json __questions__`. Run recorded
  clean on lineage `492bae60177b`, E3/E4 pass, no regression.
- **Coverage `answered` = lexical lower-bound (honest caveat).** Reported 3/5;
  verification against real claims showed ~4/5 true — one proven false-negative
  (Pi "agentic logic" Q answered by "Agent Loop / State Management" claims but 0
  stem-overlap) + one genuine thin spot (only 1 joint claim for the "optimal
  architectural design" Q). `answered=True` is trustworthy; `False` may be a
  vocabulary mismatch. Never over-reports.
- **Deferred (YAGNI):** embedding-cosine upgrade for `answered` — pull forward when
  the E-Q editorial row GATES on the flag (precision matters then; nothing gates now).

## Non-goals (this slice)

- E-Q editorial gate wiring (rides P2's coverage dimension, separate slice).
- Reformulation of *questions* (D2 already reformulates *queries* on 0-source).
- Interactive question confirmation (non-interactive only).
