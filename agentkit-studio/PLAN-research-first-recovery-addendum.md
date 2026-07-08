# Research-First Recovery Addendum

Status: planned. This addendum blocks the next official acceptance rerun until
the listed code-review blockers are fixed and tested.

## Current Read

The architecture is still viable, but the implementation is in WATCH state, not
CLEAR. No technology replacement is required yet. The next acceptance run should
not be launched until source coverage, relationship evidence, diagram labels,
and publish gates are tightened.

Internal code review requested changes on these blockers:

- Per-subject source and claim coverage can fail open: a subject may receive an
  empty source list and still continue into claims/write.
- N-subject relationship handling is incomplete: current relationship and
  fallback-diagram logic can collapse to the first two subjects.
- Summary generation can introduce an uncorroborated relationship mechanism.
- Fallback diagram labels can become generic filler instead of evidence-bearing
  labels.
- Internal pipeline markers such as `RESEARCH_FINDING` and `SEARCH` must be
  publish-blocking.

## External Patterns

Comparable research/report tools separate source planning or curation from
synthesis:

- OpenAI Deep Research lets the user add sources, proposes a research plan, and
  returns a cited structured report:
  https://help.openai.com/en/articles/10500283-deep-research-in-chatgpt
- Gemini Deep Research breaks complex research into steps, explores sources,
  and synthesizes findings:
  https://gemini.google/overview/deep-research/
- NotebookLM keeps answers grounded in uploaded sources with citations and exact
  quotes:
  https://notebooklm.google/
- Elicit Systematic Reviews follows protocol refinement, source gathering and
  screening, data extraction, and synthesis:
  https://elicit.com/solutions/systematic-review
- Microsoft 365 Copilot Researcher performs deeper research across web and work
  sources and produces source-cited reports:
  https://learn.microsoft.com/en-us/microsoft-365/copilot/researcher-agent

The shared design lesson is generic: synthesis should not start until the
system has visible source coverage and evidence contracts for the requested
subjects. This does not require topic-specific hardcoding.

## OpenScience Lessons

OpenScience reinforces the same recovery direction without requiring a stack
change:

- It advertises a full research loop: literature review, hypothesis, code,
  experiment, analysis, and write-up in one session.
- Its architecture separates agent runtime, tool layer, scientific connectors,
  skills, prompts, and optional review.
- Its scientific connector contract normalizes each source hit to stable fields
  such as id, title, summary, URL, score, and source-specific metadata.
- Its provenance layer records artifacts, runs, sources, and claims in a
  content-addressed DAG with typed edges: `produced`, `consumed`,
  `derived-from`, `supports`, and `refutes`.
- Its reviewer finding format is structured: claim, issue, severity, and
  evidence. Findings annotate lineage instead of mutating the reviewed artifact.
- Its optional final-review gate runs a blind reviewer for substantive
  research/science answers and asks it to trace claims, numbers, and citations
  to evidence.

AgentKit should copy the small patterns, not the platform:

- Make the existing source ledger stricter before WRITE instead of adding a new
  graph database.
- Normalize accepted sources to a minimum evidence contract: subject, id/title,
  URL, snippet/summary, score if available, and source kind.
- Represent claims and relationships as evidence-backed records before summary
  or diagram generation.
- Store review diagnostics as append-only metadata, then block publication only
  on generic fatal classes such as missing subject evidence or leaked internal
  markers.

## Research-App OSS Study (2026-07-07)

The second-pass study narrowed comparison to research/report-generation OSS
rather than generic evaluator frameworks. The useful references are:

- STORM (`stanford-oval/storm`, MIT): separates pre-writing research from
  article writing, using perspective-guided question asking, simulated
  conversations, outline generation, and cited article synthesis. Reuse the
  pattern of coverage-before-writing, not the whole STORM pipeline.
- PaperQA2 (`Future-House/paper-qa`, Apache-2.0): uses search -> evidence ->
  answer flow with source limits, scored summaries, contradiction handling, and
  reproduction/eval splits. Reuse its evidence-first answer assembly idea for
  claim and summary corroboration.
- GPT Researcher (`assafelovic/gpt-researcher`, Apache-2.0): uses planner /
  executor / publisher separation, source tracking, report export, and eval
  folders. Reuse its source-tracked assembly and app-native eval harness shape.
- Open Deep Research (`langchain-ai/open_deep_research`, MIT): exposes
  stage-separated research/report generation plus benchmark/eval wiring. Reuse
  its stage/config/eval separation as a calibration pattern, not its legacy
  implementations.
- OpenScience (`synthetic-sciences/openscience`, Apache-2.0): emphasizes
  persistent sessions, artifacts, connectors, provenance, and shareable research
  state. Reuse provenance state and artifact sidecars, not access/billing gates.
- Shepherd (`shepherd-agents/shepherd`, MIT, early alpha): records agent work as
  durable, inspectable, reversible execution traces with retained outputs that
  can be selected or discarded. Reuse this as a supervision/provenance pattern
  for probes, acceptance attempts, and reviewer gates; do not treat it as a
  source-selection, citation-grounding, or report-quality dependency.

Finding: no single OSS app solves all Attempt-11 blockers. The common reusable
solution is a first-class evidence/provenance layer between research and write:
sources and claims are selected first, relationship claims are represented
explicitly, synthesis consumes only evidence-backed records, and publish gates
block fatal structural or provenance failures. Diagram provenance is not solved
directly by these projects; AgentKit needs its own figure/label binding check.
Shepherd adds one useful operational lesson: keep generated changes as
reviewable retained outputs until gates select them. That supports safer
Attempt reruns and production promotion, but does not replace the evidence
gates above.

## Required Recovery Changes

1. Add a fail-visible per-subject coverage gate before WRITE.

   Each requested subject must have at least one accepted source and at least
   one claim-bearing evidence path before section writing starts. One generic
   targeted recovery query is allowed for a zero-source subject. If recovery
   still leaves a subject empty, stop with a `failed_partial` diagnostic instead
   of writing weak fallback prose.

2. Replace first-pair relationship handling with a generic N-subject evidence
   model.

   Build pairwise or all-subject relationship records for every requested
   subject set. Relationship labels should come from a small generic vocabulary:
   `cooperates`, `competes`, `independent`, or `unknown`. Only corroborated
   relationships may enter summaries, diagrams, or code-oriented sections.

3. Require evidence-bearing diagram labels.

   Fallback diagram labels must be derived from cited evidence or accepted
   claims. Reject filler labels such as articles, generic nouns, and unlabeled
   topic fragments. If useful labels cannot be grounded, omit that node/edge or
   fail visibly.

4. Make internal markers publish-blocking.

   Any internal pipeline token, search marker, placeholder, or metadata echo in
   the final artifact should block publication and return a diagnostic.

## MVP Harness

`backend/studio/research_quality_mvp.py` is the offline test harness for this
addendum. It reads the existing `artifact.md` / `result.md` plus `claims.jsonl`
workspace shape and reports four core gates plus two Attempt-11 extension gates.
It is intentionally a diagnostic harness, not a production runtime gate:
promotion must move individual checks into the existing `research_first` /
finalize boundaries rather than wiring this whole module into generation.

- `subject_evidence`: every requested subject has at least one URL-backed
  claim.
- `relationship_evidence`: every subject pair has at least one URL-backed joint
  claim.
- `n_subject_relationship_evidence`: three-or-more-subject reports have an
  all-subject relationship claim, not only pairwise evidence.
- `diagram_label_quality`: non-subject Mermaid labels overlap accepted
  URL-backed claim/quote evidence and do not use generic filler labels.
- `summary_mechanism_grounding`: executive-summary relationship mechanisms do
  not introduce non-generic terms absent from URL-backed relationship claims.
- `internal_marker_leaks`: final artifacts do not expose pipeline markers.

Observed calibration:

- `s_86350e8b9b76` fails subject evidence, relationship evidence, and internal
  marker gates.
- `s_798eeda98bd1` passes subject and relationship evidence, but fails diagram
  label quality and internal marker gates.

This is enough for an MVP: the gates distinguish known failure modes without
changing production behavior.

The MVP also includes gate canaries: known-bad fixtures that must fail each
gate. If a future gate implementation becomes too loose, these tests fail
before the gate is promoted into production.

The 2026-07-07 extension applies the research-app study to Attempt 11 by adding
MVP checks for:

- `n_subject_relationship_evidence`: three-or-more-subject reports require an
  all-subject relationship claim, not only first-pair or pairwise evidence.
- `summary_mechanism_grounding`: summary sentences that name multiple subjects
  cannot introduce mechanism words absent from relationship claims.
- Mermaid edge-label grounding: diagram edge labels are checked alongside node
  labels, so a generic or invented connector cannot pass just because subject
  nodes are grounded.

Observed extended-MVP results:

- `s_86350e8b9b76`: artifact fails `subject_evidence`,
  `relationship_evidence`, and `internal_marker_leaks`; gate health passes all
  canaries.
- `s_798eeda98bd1`: artifact passes subject and relationship evidence but fails
  `diagram_label_quality` and `internal_marker_leaks`; gate health passes all
  canaries.

Validation: TDD red state showed five expected failures before implementation,
then two additional review-driven false-pass regressions before tightening, then
one 3-subject grounding edge before scoping grounding to all URL-backed
multi-subject claims.
After implementation, `test_research_quality_mvp.py test_report_quality.py`
passes (`37 passed`), `py_compile studio/research_quality_mvp.py` is clean, and
both calibrated workspace runs produce machine-readable artifact + gate-health
JSON.

Promotion plan:

1. Keep the MVP read-only until one more controlled acceptance run proves score
   and artifact-quality improvement.
2. Move the subject and relationship evidence checks into the `research_first`
   FRAME/RESEARCH/CLAIMS boundary before WRITE.
3. Move diagram-label validation into fallback diagram construction.
4. Move marker-leak validation into the final publish gate.
5. Keep the JSON report shape for drift comparison and reviewer diagnostics.

## Validation Checklist

- Add a zero-source subject diagnostic test.
- Keep the malformed whole-task subject branch drop generic.
- Add a three-subject relationship regression.
- Prove uncorroborated relationship mechanisms cannot enter the summary. ✅ MVP
  canary exists; production promotion pending.
- Prove fallback labels reject filler labels and uncited Mermaid edge labels. ✅
  MVP canaries exist; production promotion pending.
- Prove internal markers are publish-blocking. ✅ MVP canary exists; production
  promotion pending.
- Run the targeted `test_research_first.py` slice.
- Run routing/finalize regression slices that can expose marker leaks.
- Run the full backend suite and compare drift against the previous comparable
  run.
- Only then run one controlled acceptance rerun and compare quality, coverage,
  topology, artifacts, logs, scores, and failure modes against the previous run.

## Assumptions

- Do not hardcode task names, subject names, topic-specific prose, or fixed
  templates in production code.
- Do not add dependencies unless a later reviewed plan proves the current stack
  cannot meet the requirements.
- Do not run another official acceptance attempt before these blockers pass.
