# PLAN — Section-ownership + deterministic assembly + assignment verification

_Created 2026-06-29. Design discussion (NOT yet implemented). Target architecture from
the user; current-state grounded by reading the live code (prompts.py / runner.py /
findings.py). Priority key: P0 blocks correctness · P1 important · P2 cleanup._

## 1. Target architecture (role-scoped goal knowledge)

> Goal-knowledge follows the ROLE, not the agent.

| Role | Knows the original TASK? | Responsibility |
|------|--------------------------|----------------|
| **Hub** (planner) | YES + the deliverable TEMPLATE | Review the WHOLE doc, compare vs template + accumulated weaknesses, decide what's missing (new sections / new content / weaknesses to fix), and **assign bounded jobs to agents** (by section). |
| **Agent** (worker) | NO | Execute ONLY its assigned job: its section(s), a decomposed sub-task, weaknesses to fix, or a new section to create. Goal-blind → cannot drift; the assignment is closed. |
| **Reducer** | YES | Consolidate all agent artifacts AND **verify them against the hub's assignment** (was every assigned job actually done?), then assemble + review. |

Key properties wanted:
- Agents get a small, explicit assignment — not the full task, not the full doc.
- Reducer **assembles deterministically** (no LLM echo of the whole doc) and **verifies coverage** against the hub's assignment list.
- A separate **cross-section synthesis pass** still sees the assembled whole (synthesis is inherently cross-section; do not silo it away).

## 2. Current state (grounded in code)

There are **two** assign/execute/reduce paths, and they differ exactly on the user's concern.

### Path A — hub/worker/PATCHES (already implements section-ownership)
- `prompts._build_hub_cot_prompt`: hub is goal-aware, reads the deliverable, **assigns by
  section** (non-overlapping, verbatim headings, max-N/agent), emits `TASK_LIST`/`ASSIGNED`.
- `prompts._build_worker_cot_prompt`: worker gets ONLY its `TASK ASSIGNMENTS` + current doc;
  patches only its sections; section-scoped weakness rule ("fix a `[## X]` weakness only if
  you own X"). **This is the target model — agent is already goal-blind + section-scoped.**
- `planning._parse_assigned` / `_dedupe_assignment`: parse the hub's assignment.
- Reducer applies worker `PATCHES` via the patcher (`findings._make_section_reducer` →
  `agentkit.artifacts.patcher.reduce_patches`) — additive, section-keyed.

### Path B — hill-climb STAR/executor (the path that actually ran in s_eeff6e911cd6)
- `prompts._build_executor_prompt`: STAR spokes are "research executors" that **DO get the
  full `GOAL`** + `artifact[:3000]` + weaknesses, emit `RESEARCH_FINDING` blocks.
- The final reduce step injects the **full seed doc** (`_art_ctx`) into an LLM "ADDITIVE
  MERGER" prompt → the reducer **LLM-merges the whole 68KB doc**. This is the truncation
  risk (verified: whole-doc LLM repair truncated 68KB → 36KB).
- The TASK is injected into every step (`desc = f"TASK: {task}\n\n{desc}"`).

### What neither path does
- **Reducer does not verify against the hub assignment.** It merges what arrived; it never
  checks "agent was assigned section X / new-section Y — did it deliver?" (the user's ask).
- New-section creation is handled by `_merge_missing_sections` (deterministic skeleton add),
  not by an explicit hub→agent "create section Z" assignment.

## 3. Gap analysis (target vs current)

| # | Target behavior | Current | Gap |
|---|-----------------|---------|-----|
| G1 | Agents goal-blind, section-scoped | Path A: yes · Path B: **no** (executor gets full goal, reducer gets full doc) | **Unify hill-climb onto Path A's hub-assign + worker-patch model** (P0) |
| G2 | Reducer assembles deterministically | Path A: patch-apply (mostly) · Path B: LLM-merge full doc | Make reduce = deterministic section assembly; LLM only reviews (P0 — kills truncation/regression class) |
| G3 | Reducer verifies vs assignment | neither | Add coverage check: hub assignment → reduce-time verify each job done; unmet → next-epoch weakness (P1) |
| G4 | Hub assigns new-section creation explicitly | implicit `_merge_missing_sections` | Hub emits "create section Z" as an assignable job; an agent owns + populates it (P1) |
| G5 | Cross-section synthesis preserved | `_synthesize_analysis` runs post-merge on whole doc | Keep as-is — do NOT silo it per section (P0 constraint, not a change) |

## 4. Proposed changes (phased; reuse existing infra)

Reuse `agentkit.artifacts.sections` (`split_sections`, `section_hash`) + `patcher`
(`reduce_patches`) — section identity across rewrites (heading drift) is the hard part and is
already solved there. Prefer the in-memory section map over files-per-section unless on-disk
inspectability is explicitly wanted (YAGNI — the patcher already absorbs the I/O).

- **P0 — unify the hill-climb reduce onto deterministic section assembly (G1+G2).**
  Replace the Path-B LLM "additive merger" with: workers emit section-scoped patches →
  `reduce_patches` applies them → reducer **concatenates sections in document order**
  (deterministic). The reducer LLM, if used at all, only *reviews/comments*, never re-emits
  the whole doc. Removes the truncation/verbatim-regression failure mode at the root.

- **P1 — reduce-time assignment verification (G3).**
  Thread the hub's `ASSIGNED` list into the reducer. After assembly, deterministically check
  each assigned (section | new-section | weakness) was addressed (section present / changed /
  weakness lint cleared). Unmet assignments become the next epoch's seeded weaknesses — closes
  the loop the user wants (hub assigns → reducer verifies).

- **P1 — explicit new-section assignment (G4).**
  Hub, comparing doc vs template, emits "create section Z" as an assignable job; one agent
  owns and populates it (vs the current implicit `_merge_missing_sections` placeholder).

- **P2 — drop the full-`GOAL` injection from the worker/executor prompt (G1 detail).**
  Once workers are driven purely by the hub assignment, remove `GOAL:` from
  `_build_executor_prompt` / the per-step `TASK:` injection **for worker steps only** (keep it
  for hub + reducer steps). Validate no coherence regression (the §11.10 risk) before landing.

## 4b. Topology uniformity — one contract, pluggable decomposition (P0 framing)

Requirement: STAR / MAP / MESH / PIPELINE must follow the SAME process regardless of path.
Today they don't — the runner gates the hub CoT to "STAR/MAP ONLY" and MESH/PIPELINE skip
section assignment entirely; hill-climb adds a third (executor) behavior on top.

The uniform thing is the **CONTRACT**, not the partition mechanics:

```
hub (goal-aware): decompose TASK -> bounded JOBS, assign each to one agent
  -> agent (goal-blind): execute ONLY its job, emit a section-anchored artifact
  -> reducer (goal-aware): assemble deterministically + verify every job was done
```

What VARIES per topology is the **decomposition strategy** (a pluggable function), because the
coordination semantics genuinely differ — do NOT flatten them into "section partition":

| Topology | Job unit (what the hub assigns) | Notes |
|----------|--------------------------------|-------|
| STAR / MAP | a non-overlapping **section** set | the existing model — patches commute |
| MESH (debate) | a **stance/critique target** on the same section(s) | agents overlap by design; reducer arbitrates/merges positions, doesn't just concat |
| PIPELINE (stages) | a **stage** consuming the prior stage's output | sequential, not parallel; "assignment" = stage contract, reducer = final-stage capture |

Design implication: extract a `DecompositionStrategy` (per topology) + a single shared
`assign → execute → assemble+verify` driver. STAR/MAP reuse the section strategy that already
exists; MESH/PIPELINE get strategies that respect their semantics but obey the SAME contract
(goal-blind workers, goal-aware hub+reducer, reduce-time verification). This is SRP: the
driver owns the contract; each strategy owns one topology's decomposition.

Caveat: MESH and PIPELINE outputs are NOT trivially concatenable. "Deterministic assembly"
(G2) is literal-concat ONLY for section-partitioned STAR/MAP; for MESH the reducer still does
a (bounded, position-aware) merge, for PIPELINE it captures the terminal stage. The INVARIANT
that generalizes is "reducer verifies coverage against the assignment", not "reducer concats".

**Topology SELECTION is the orchestrator's job (goal-aware).** The selector ALREADY EXISTS and
is principled — the work is to stop discarding its verdict, not to build it.

Selection heuristics (what we want), and the EXISTING code that already implements each:

| Phase shape | Topology | `select_topology` rule that fires |
|-------------|----------|-----------------------------------|
| independent, decomposable, agents need NOT communicate | **STAR** | Q3 `subtasks_independent` (+`needs_subdecomposition`→TREE) |
| needs debate / critique / adversarial review | **MESH** | Q5 `workers_challenge` |
| indivisible / strictly ordered, stage consumes prior | **PIPELINE** | Q3 ordered (default when not independent) |
| per-item over an upstream list ("each"/"every") | **MAP** | `classify_step_topology` MAP cue |
| trivial / single-shot / under-specified | **SINGLE** | Q1 `single_agent_sufficient` (conservative default) |
| multiple entry points / distinct identities | **GATEWAY** | Q7 (routing, upstream of fan-out) |
| cross-session / human-in-loop / restart | **DURABLE_BOARD** | Q4/Q8 |

### Current state (grounded in code)
- **Primitive (exists, principled):** `agentkit.topology.core.select_topology(TaskSpec)` — a
  priority-ordered rule tree returning `TopologyChoice{topology, trigger, concurrency,
  rationale, questions_fired}`. Its own self-tests already assert review→MESH, independent→STAR,
  ordered→PIPELINE, etc.
- **Inference:** `infer_spec(description, client)` (LLM) populates the `TaskSpec` booleans;
  `classify_step_topology(description)` is the model-free keyword fallback (MESH=compare/
  debate/vs, MAP=each/every, STAR=gather/search, PIPELINE=then/stage, else SINGLE).
- **Wiring (exists):** `runner.py:473` calls `assign_topologies(plan, mode="auto",
  client=client, llm=use_llm)` — so in `mode=="llm"` Studio ALREADY routes each phase through
  `infer_spec→select_topology`. Per-phase task-driven selection is live.
- **THE BLOCKER:** `runner.py:483–487` then **force-overrides every step to `STAR`** whenever
  `auto_improve` is on — with the comment that only STAR's reducer does the section-aware
  merge/handoff, so "auto-derived topology would silently break the improvement loop." So under
  hill-climb the selection is computed and immediately thrown away.
- Rationale is not surfaced: `TopologyEvent` emits the chosen topology but not
  `TopologyChoice.rationale`/`questions_fired`.

### Gap → executable tasks (ordered; each test-gated)
- **E1 — unblock (depends on §4b reducer contract).** Give MESH / PIPELINE / SINGLE a reducer
  that satisfies the assemble+verify contract (MESH: position-aware arbitration; PIPELINE:
  terminal-stage capture; SINGLE: identity). THEN **delete the `runner.py:483–487` force-STAR
  override** so the already-computed selection is honored under hill-climb. This is the whole
  point — topology selection becomes real the moment the reducer is no longer STAR-only.
- **E2 — surface the rationale (observability, ties to §4c).** Thread
  `TopologyChoice.rationale` + `questions_fired` into `TopologyEvent` and the hub I/O record, so
  every phase logs WHY its topology was chosen (auditable, debuggable).
- **E3 — selection-quality test.** Add a mapping test over representative phase descriptions
  (review→MESH, independent gather→STAR, ordered "then/stage"→PIPELINE, "each"→MAP,
  trivial→SINGLE) against `classify_step_topology` AND `infer_spec→select_topology`; if
  `infer_spec` under-populates the `TaskSpec` booleans, tighten its prompt. Mirrors the existing
  `core.py` self-tests.
- **E4 (optional) — planner-level intent.** Have the epic planner emit a topology INTENT per
  phase, reconciled with `select_topology`, so the goal-aware planner reasons about topology
  explicitly rather than relying on a post-hoc per-step classifier.

Net: selection is ~done; E1 (remove the STAR override, enabled by the per-topology reducer
contract) is the one change that makes topology actually task-driven end-to-end.

## 4c. Observability = the role I/O contract (disk-backed artifacts)

The structured I/O log IS the role contract made observable. Today `runner.py` (~1196) writes
ONE record per step and INLINES the full prompt + output (the reducer record carried the whole
68 KB doc → 144 KB records). New rule: **artifacts live on disk; I/O records carry only a
name/path + small structured fields.**

Per-role record schema (`agent_io.jsonl`, one record per role-invocation):

```jsonc
// AGENT  — goal-blind executor
{ "role": "agent", "step": "...", "topology": "star", "agent_id": "...",
  "input":  { "requirement": "<assigned job text>", "target_doc": "<path>" },
  "output": { "artifacts": ["<path>", ...] },          // verifiable artifacts, by path
  "tokens": 0 }

// HUB  — goal-aware planner (input shape == agent's)
{ "role": "hub", "step": "...", "topology": "star",
  "input":  { "requirement": "<goal+template>", "target_doc": "<path>" },
  "output": { "assignments": [ { "agent_id": "...", "job": {...} }, ... ] },
  "tokens": 0 }

// REDUCER — goal-aware consolidator/verifier
{ "role": "reducer", "step": "...",
  "output": { "new_weaknesses": [ ... ], "score": 0.0,
              "handoff_artifacts": ["<path>", ...] },
  "tokens": 0 }
```

Rules:
- NO artifact body in any `input`/`output` — only `target_doc` / `artifacts` / `handoff_artifacts`
  PATHS. Bodies are written to disk (workspace `io/` or the section files) and referenced.
- `agent.output.artifacts` are the VERIFIABLE units the reducer checks against the hub's
  `assignments` (ties G3 verification to concrete files).
- Keeps records small + greppable, and makes the role contract auditable offline.

Scope note: disk-backed bodies + `role` tagging + the hub/reducer schemas are achievable at the
current step granularity. True PER-AGENT records (one per spoke) require `run_plan` to surface
per-agent I/O — a fan-out change, sequenced with the topology-driver work (§4b).

## 4d. Cross-cutting: moving-window for ALL doc processing (P0 principle)

Rule: **no LLM call may take a large document and be expected to echo it back whole.** A model
truncates a long echo (verified: 68 KB → 36 KB). Any doc-processing operation must
**window → process each window → reassemble deterministically** (or operate on a localized
block + splice, like `_repair_lints`). Whole-doc-in / whole-doc-out is banned for anything that
can exceed a few KB.

Status of current doc-processing ops against this rule:
- `mine_weaknesses_from_outputs` — ✅ already moving-window (`_MINE_WINDOW`/`_MINE_STEP`).
- `score_result` — windows to 20 K (bounded; acceptable — it reads, doesn't echo).
- `_repair_lints` — ✅ block-level + deterministic splice (fixed).
- **`_synthesize_analysis` (item 1A, SHIPPED) — ❌ whole-doc echo.** On a large doc the model
  truncates; the length/URL guardrail then REJECTS it → synthesis SILENTLY NO-OPS at scale.
  Same trap as the repair bug; it was only ever verified on a toy input. **Must be reworked to
  section/window-wise synthesis** (synthesize per section/window, reassemble) — and because
  synthesis is cross-section (§5 caveat), windows must overlap or carry a short cross-section
  summary so comparison still works.
- **reducer additive-merge (Path B) — ❌ whole-doc LLM merge.** Replaced by deterministic
  section assembly per §4/§4b (G2).

Implication: the deterministic-assembly + section-strategy work (§4b) and a windowed-synthesis
rework are the mechanism that makes this principle hold everywhere.

**Dedup on reassembly (required side-effect mitigation).** Overlapping windows emit duplicate
results — the same comment/finding/weakness straddling a boundary appears in two windows. So
the rule is `window → process → DEDUP → reassemble`, never reassemble raw. Reuse existing
dedup, do not reinvent:
- lexical: normalized-text key (`task_runs._norm_weakness` pattern) drops verbatim dupes.
- semantic: cosine ≥ 0.85 over embeddings (already used for weaknesses + F1 finding-dedup in
  `_make_section_reducer`) drops near-duplicate rephrasings.
Windowed synthesis must apply the same two-stage dedup to its per-window analysis/comments; the
reducer's coverage-verify (G3) likewise dedups before counting an assignment "done" twice.

## 4e. Newly-found gaps (from the s_1e90e8b824b0 llm-mode run review)

Concrete defects observed in a real llm-mode hill-climb run, not yet covered above. Each is
independent of the big redesign and shippable on its own.

- **N1 — doubled / Frankenstein structure (P1).** `_merge_missing_sections` token-matches the
  template; when the agent organized under DIFFERENT names (e.g. "Design Architecture",
  "Implementation Methodology", "Example Code") the template sections (Background / Key Findings
  / Evidence / Limitations / Source References) did NOT match → it APPENDED all of them, so the
  served doc carried TWO parallel skeletons (numbered §1–§5 AND the template set). Fix: before
  appending a template section, check concept-coverage against the WHOLE doc (not just heading
  token-match); if the concept is already covered under another name, don't re-add. Reconcile to
  ONE outline. (Relates to §2 section identity.)

- **N2 — miner hallucinates MISSING/TRUNCATED content that is PRESENT (P0 eval reliability).**
  Verified false weaknesses in that run: "lack of example code" (a python block existed),
  "no conclusion / ends abruptly" (a Conclusion section existed; doc ended cleanly), "Executive
  Summary truncated '…and st'" (it was complete). Item-6 `_is_non_weakness` only drops POSITIVE
  statements; `sections_present` only guards TEMPLATE section names. Fix: verify a mined
  "missing/absent/truncated X" claim against the artifact before counting it — generalize the
  present-content guard beyond template names (code blocks, conclusion, summary), and treat a
  miner truncation claim as authoritative ONLY when the deterministic check agrees. These
  phantoms depress `adjusted_score` and seed phantom fixes. (Extends PLAN item 6 + the
  PLAN-non-additive "false weakness" premise.)

- **N3 — per-section truncation is unguarded (P1).** `_ends_cleanly`/`_trunc_fact` check only
  the DOCUMENT end, so the miner re-hallucinates mid-section truncation (the W3 class). Fix:
  compute a per-section terminal-punctuation signal, or refute the claim deterministically.

- **N4 — code-fence `#` comments mis-counted as headings (P2).** Lint/rubric heading scans
  (`^#{1,6}`) count python comments like `# --- MOCK ... ---` inside a ```` ```python ```` block
  as H1/H2 headings, polluting the structure signal and `sections_present`. Fix: skip fenced
  code regions in heading detection (the lint already tracks fence state — reuse it).

- **N5 — post-run repair was silent → un-diagnosable; now instrumented (P0, in progress).**
  In the llm-mode run the deployed `_repair_lints` did not clean the mermaid, but nothing logged
  whether it ran/fired/threw. Added `_dbg("repair_lints: lints_before=… changed=… lints_after=…")`
  (+ EXCEPTION) to `backend/tmp/throughput.log`. Open: restart + re-run (deferred) → read the
  line → if `changed=False`, pin repair to a capable model (gemma) regardless of session model;
  if it never ran, fix the gate. Also done this session: repair no longer gated on `use_llm`
  (output validity is mode-independent) — NEEDS live verification.

- **N6 — verification protocol (process, P1).** Bug was mis-diagnosed 3× because "unit tests
  pass + function works in isolation" was treated as "the deployed system does it." Rule: for any
  fix to runtime behavior, verify against the RUNNING backend — check the serving process start
  time vs the change (`ps -o lstart` on the `:8770` PID), restart if stale, and confirm on a real
  re-run, not just pytest + an isolated function call.

## 5. Risks / caveats

- **Don't over-silo (G5).** Synthesis/analysis (PLAN item 1A/1B) needs the whole assembled
  doc. Section isolation applies to GENERATION/PATCHING, not to the synthesis pass.
- **Section identity.** Headings drift between epochs; anchor on `section_hash`, not raw
  heading strings, when verifying/splicing.
- **Coherence (§11.10).** Removing the goal from workers risks drift; mitigated because the
  goal-aware hub now hands a closed, explicit assignment. Gate P2 on a before/after coherence
  check.
- **Two paths → one.** The biggest win is collapsing Path B onto Path A; verify no
  non-hill-climb run regresses when the executor/merger path is retired.

## 6. Verification plan
- Reduce path: feed a known multi-section seed + worker patches → assert deterministic concat
  equals expected (no LLM), all sections present, no truncation.
- Verification: assign a job that the worker does NOT deliver → assert reducer flags it unmet
  and it surfaces as a next-epoch weakness.
- Coherence (P2): run the same task with goal-blind workers vs goal-aware; compare rubric +
  section coverage to confirm no drift.
