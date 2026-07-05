# REBUILD LESSONS — binding constraints for research_first (distilled from full dev history, 2026-07-05)

Sources: 60+ branch commits, PLAN §1–§15, DESIGN entries, session memories, 6 live
runs on task 492bae60177b. Every item below was PAID FOR once. The rebuild repeats
none of them.

## 1. Weak-model realities (gemma is the target; design for it, don't blame it)
- Probe-verified: gemma CAN do deep analysis through the production path (0.17→0.83)
  — if output is shallow, the app's contract is the ceiling, not the model.
- Given a section-rewrite prompt, gemma echoes the WHOLE document (every window,
  every run) — never ask it to "rewrite section X in place"; give it a narrow
  generation task with only the inputs it needs (claims → section).
- It emits tool calls as ```json fenced blocks, not tags; leaks assistant meta-prose
  ("Once you provide the text, I will…") into content; writes refusal-prose when it
  has zero sources (0 sources = fabrication risk, never "just thin"). Auto-fetch for
  it (ec12252) — it will not fetch on its own.
- Long prompts: compact context deterministically on every call (00983b9); timeouts
  grind — keep per-call inputs bounded (claims subset, not the whole corpus).
- It pre-draws diagrams when the prompt names them (memory: gemma-predraws) — fine,
  but then VALIDATE them (broken mermaid edges are worker-born, 5/5 repairable by a
  lint-surfacing repair turn; deterministic regex repair was rejected).

## 2. Grounding and citations
- Grounding ≠ relevance: a fetched, quote-verified junk page passes grounding —
  judge relevance against the CACHED PAGE with the density floor + gray-zone judge
  (calibrated bands live in findings.py; reuse, don't re-derive).
- Prefetch cited URLs BEFORE any sanitize/judge step reads the cache (164a34d — the
  oracle read an empty cache and kept junk).
- Quotes arrive with literal \n escape floods — normalize at parse (75e62c7).
- Verbatim-substring quote check: whitespace-normalize both sides first.
- References = deterministic bibliography REBUILT from body citations (4541797);
  never LLM-maintained.
- Pass the CLEAN base requirement to any stem/overlap logic — iteration-prefixed or
  template-suffixed text dilutes densities ~10x.

## 3. Structure (the erasure/duplication scar tissue)
- ``` fence + URL on one line = swallowed document; repair at EVERY write boundary
  (per-step normalize + finalize), and REPAIRS BEFORE ANY FORMATTER — mdformat/
  Flowmark/PyMarkdown all launder broken fences into valid-wrapped garbage (tested).
- ONE source of truth for the document text. The old core kept three (scored text /
  artifact.md / section files) — both erasers and the duplicate-section disease
  lived in the seams. research_first: a single string, written to disk at stage
  boundaries only, never round-tripped through a splitter mid-generation.
- Never re-split assembled text back into sections during generation
  (split→merge manufactured duplicate ## headings + dropped blocks).
- Every accept gate that guards a rewrite MUST check: URL set preserved (norm_urls
  superset), fence count not reduced, no invented headings (mask fenced code first
  — # comments in code look like headings), length ratio bounds.
- Sub-sections need a responsible owner or they ship as empty headings (c5e6030).
- Title resolution: don't word-cap mid-noun-phrase (3b0c300).

## 4. Verification (order of authority)
- Deterministic gates FIRST, LLM judgment second. Judges wobble run-to-run on
  identical content (covers-Craft SATISFIED↔NOT). A SATISFIED verdict on a
  structural/coverage requirement is only trusted past a deterministic shape check
  (fence exists / mermaid exists / subject has cited substance).
- Verdicts without an ACTOR change nothing: 3 runs of honest NOT_SATISFIED produced
  0 Craft searches. Every check must name who acts on failure, at what stage.
- Coverage by construction beats coverage by verdict: research every extracted
  subject unconditionally; the compliance check is the backstop, not the mechanism.
- Observability from day one: stage-boundary dbg lines with COUNTS (sources per
  subject, claims, fences per stage). The fences= trace found in one run what
  static analysis couldn't in hours. 0.00s stage timer = swallowed exception.
- A crashed run must never record a numeric score (two 0.0 rows poisoned seeding);
  fail-open may skip an action but must never RECORD a pass.

## 5. Prompt discipline
- Few-shot examples must NEVER share content with plausible live tasks — the
  extraction prompt's own example ("include example code OR a design architecture")
  made the model mislabel this exact task's AND as OR for days.
- Constraint ordering: general prohibitions BEFORE specific rules, with an explicit
  "the rules above define what X includes" bridge — recency wins in small models.
- No hardcoded task/subject strings anywhere (test-enforced); no phrase-list guards
  — structural checks only.
- When adding a constraint to fix an excess (quote walls), BOUND it — the
  one-sentence findings contract was an over-correction that became the depth
  ceiling for weeks.

## 6. Process
- Probe-before-wire: verify a capability hypothesis with a live probe BEFORE
  building on it (the "weak model can't analyze" assumption was false and cost a
  detour; the "strong-model reducer" was built on it).
- Editor-style text retries cannot fix structural/coverage misses (two runs of
  burned rounds, opp count never moved) — route those to producers/searches.
- Memoize repeated judge calls per (input-hash) within a run — 17 identical
  12-branch compliance rounds in one editor phase.
- oMLX: pre-warm the embedder before gemma runs or the memory enforcer evicts the
  chat model mid-request and the server aborts.
