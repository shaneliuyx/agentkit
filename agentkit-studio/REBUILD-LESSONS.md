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

## 7. Second sweep — older history (commits 61–194, pre-today eras)

- **Depth relaxation was already tried and under-delivered ONCE**: f6f6c65 "relax
  reducer depth caps to cure report shallowness" needed 9d30b13 "make reducer depth
  relaxations actually deliver" and the ceiling still survived to today (ccabed3
  names the three roots: findings.py 268/327/333). Task #38 MUST read those commits
  first — a third blind relaxation is the exact repeat-mistake this doc exists to
  prevent. research_first sidesteps it by not writing through findings patches at
  all (sections synthesize from claims directly).
- **Duplicate phases had TWO causes**: goal injection into planner input (1cb702f,
  the real one — keep the Goal OUT of planner/decomposer input) and bare-"and"
  splitting (d5f21c3). Fix both classes, not the first one found.
- Episodic memory can poison prompts with past refusals (9a69327) — never feed
  raw failure text back as context.
- Completion caps truncated artifacts mid-report (710b1fb) — size output budgets
  to the artifact, and reducers must preserve source URLs under pressure.
- Spokes once PLANNED instead of EXECUTING (21d82d3) — worker prompts must demand
  the artifact content itself, never a plan for it; cache-as-oracle for citations.
- Strip assistant preamble at EVERY surface (artifact + displayed + stored:
  75e2a2e, 684c040) — meta-prose leaks anywhere text is persisted.
- Partial-write hazard: roll back the on-disk artifact when a mid-write sync fails
  (8f919f4) — stage-boundary writes must be atomic-or-reverted.
- Refactor scope-loss killed the editor pass silently for days (d5ebe76: an
  extraction dropped judge_client from scope) — after ANY extraction refactor,
  verify each formerly-inline variable is actually threaded (S2's ledger now
  guards this class at runtime).
- Diagram grounding: embedding-cosine guards were INERT (7a7f049 replaced with
  literal-token grounding); reject edgeless diagrams (843f a44); few-shot
  adjudicator is the settled technique for presentation detection (16dd8ac).
- Cap structured LLM control outputs (ffbaafc) — unbounded JSON control replies
  wedge parsing.
