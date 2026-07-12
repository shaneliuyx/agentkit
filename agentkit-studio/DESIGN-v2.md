# AgentKit Studio — Hill-Climb / Section-Ownership Research-Report Architecture (DESIGN-v2)

> **⚠️ SUPERSEDED (2026-07-12) by [`DESIGN.md`](DESIGN.md).** This document describes the
> **legacy hub/spoke "Section-Ownership" phase-loop** architecture, which was retired as the
> live generation path (2026-07-05 ruling) and replaced by the linear `research_first` pipeline.
> The phase-loop still exists in code as the `STUDIO_DISABLE_RESEARCH_FIRST` rollback fallback
> (`studio/legacy_loop.py`) but is no longer the design authority. Kept as the legacy
> architecture-of-record; some architecture-neutral parts (persistence schema, SSE gotcha,
> agentkit boundary, rubric/UI) were carried into `DESIGN.md`.

**Purpose.** One coherent design authority for Studio's self-improving research-report
engine: how a goal becomes a multi-phase plan, how parallel agents own document
sections, how their work is assembled deterministically, how the deliverable is
scored, and how the whole thing hill-climbs across epochs and sessions without ever
regressing. (UI/API contract authority remains `SPEC.md`.)

This supersedes `DESIGN.md` and folds in the three planning docs
(`PLAN-section-ownership-architecture.md`, `PLAN-non-additive-restructuring.md`,
`PLAN-research-quality-backlog.md`). Where a PLAN proposed something now built, it is
documented as built with its function/file. Every "why this failed" lesson is
preserved — that rationale is the most valuable content here.

Code lives in `agentkit-studio/backend/studio/` (the orchestration shell) and the
`agentkit/` repo (reusable primitives), as noted per symbol.

---

## Table of contents

1. Core Invariants
2. Architecture — Role-Scoped Goal Knowledge
3. Topology Selection & the Uniform Per-Topology Reducer Contract
4. The Phase Loop, Epic Plan & Deterministic Section Assembly
5. Document-Processing Principle (Moving-Window) & Its Passes
6. Evaluation — Rubric, Adjusted Score, Weakness Mining & Refutation
7. The Hill-Climb Loop — Epochs, Seed Carry-Forward, Keep/Discard Gate, Lineage Hygiene
8. Observability — The Role I/O Contract on Disk
9. Persistence — `task_runs.db`
10. The SSE Run API
11. Shared-Library Boundary (`agentkit`)
12. UI — Loop Config, Rubric Panel, Chat Window
13. Memory Hygiene
14. Open Backlog (Deferred / Partial)
15. Decision Log & Changelog

---

## 1. Core Invariants

1. **One deliverable per task.** All phases read from and write patches to a single
   `artifact.md`. No phase reconstructs the document from scratch if a prior version
   exists.
2. **Completed work is never reassigned.** A `TaskLedger` carries done/remaining tasks
   across phase boundaries; hubs assign only from the remaining set.
3. **Crash-safe writes.** The deliverable is modified via atomic patch-apply
   (`write .tmp → rename`). A crash never leaves an empty file.
4. **Dynamic agent sizing.** Agent count is derived from task count, never hard-coded.
5. **CoT prompts everywhere.** Every hub prompt uses explicit numbered reasoning steps.
6. **Monotone accumulation (§11 invariant).** Content only enters the deliverable
   through a worker that found a real source. Absence of content is a tracked
   placeholder, never invented prose. The reducer applies deltas and never re-emits the
   whole document. The deliverable can only grow or hold, never shrink. "Worst case = no
   improvement" is a structural guarantee, not a guarded hope.
7. **Section is the single currency of the loop.** Every unit of work — a detected gap, a
   consolidated task, a hub assignment, a patch, a carried-forward weakness — is keyed by
   the artifact section it belongs to. Anything not tied to a section is unactionable.
8. **No whole-doc LLM echo (§5 moving-window principle).** No LLM call may take a large
   document and be expected to echo it back whole — a model truncates a long echo
   (verified: 68 KB → 36 KB). Doc processing must window → process → dedup → reassemble,
   or operate on a localized block + splice.
9. **Goal-knowledge follows the ROLE, not the agent** (§2): goal-aware hub, goal-blind
   worker, goal-aware reducer/verifier.

---

## 2. Architecture — Role-Scoped Goal Knowledge

> The original task is dangerous knowledge: a goal-aware worker drifts; a goal-aware
> whole-doc reducer regenerates and truncates. So goal-knowledge is scoped per role.

| Role | Knows the TASK? | Responsibility |
|------|-----------------|----------------|
| **Hub** (planner) | YES + the deliverable TEMPLATE | Review the WHOLE doc, compare vs template + accumulated weaknesses, decide what is missing (new sections / new content / weaknesses to fix), and **assign bounded jobs to agents** by section. |
| **Agent** (worker) | NO | Execute ONLY its assigned job: its section(s), a decomposed sub-task, weaknesses to fix, or a new section to create. Goal-blind ⇒ cannot drift; the assignment is closed. |
| **Reducer** | YES | Consolidate all agent artifacts AND **verify them against the hub's assignment** (was every assigned job actually done?), then assemble + review. |

Key properties:
- Agents get a small, explicit assignment — not the full task, not the full doc.
- The reducer **assembles deterministically** (no LLM echo of the whole doc) and
  **verifies coverage** against the hub's assignment list.
- A separate **cross-section synthesis pass** still sees the assembled whole (synthesis
  is inherently cross-section; do not silo it away — §5).

### 2.1 The two paths that were unified

Historically there were **two** assign/execute/reduce paths that differed exactly on the
role-knowledge concern:

- **Path A — hub / worker / PATCHES** (already section-owned). `prompts._build_hub_cot_prompt`
  (goal-aware, assigns by section), `prompts._build_worker_cot_prompt` (goal-blind, patches
  only its sections), `planning._parse_assigned` / `_dedupe_assignment`, reducer applies
  worker PATCHES via `findings._make_section_reducer` → `agentkit.artifacts.patcher.reduce_patches`.
  This was always the target model.
- **Path B — hill-climb STAR / executor** (the path that actually ran). STAR spokes were
  "research executors" that **got the full GOAL** + `artifact[:3000]` + weaknesses and emitted
  `RESEARCH_FINDING` blocks; the final reduce injected the **full seed doc** (`_art_ctx`) into
  an LLM "ADDITIVE MERGER" prompt that **LLM-merged the whole 68 KB doc** (verified truncation
  68 KB → 36 KB); the TASK was injected into every step (`desc = f"TASK: {task}\n\n{desc}"`).

**Unification (built — G1/G2/P2).**
- **P2 goal-blind workers** — `prompts._build_executor_prompt` no longer injects the raw GOAL
  (workers are driven by assignment + weaknesses), and the per-step `TASK:` injection is gated
  off for reducer-backed worker phases. Goal still steers via the hub, the keep/discard gate,
  and verification — never via the worker. Coherence was checked before landing (the §11.10
  drift risk).
- **G2 deterministic assembly** — BOTH whole-doc LLM echoes are removed: the Path-B "ADDITIVE
  MERGER" desc that injected the full seed (`_art_ctx`), and the Phase-2 `_refine_fn` (now gated
  to small docs only). Assembly is `reduce_patches` (section-keyed, deterministic); the LLM
  never re-emits the whole doc.

---

## 3. Topology Selection & the Uniform Per-Topology Reducer Contract

### 3.1 One contract, pluggable decomposition

STAR / MAP / MESH / PIPELINE / SINGLE now follow the **same** process. The uniform thing is
the **contract**, not the partition mechanics:

```
hub (goal-aware): decompose TASK -> bounded JOBS, assign each to one agent
  -> agent (goal-blind): execute ONLY its job, emit a section-anchored artifact
  -> reducer (goal-aware): assemble deterministically + verify every job was done
```

What VARIES per topology is the **decomposition strategy** — a pluggable function — because the
coordination semantics genuinely differ (do NOT flatten them into "section partition"):

| Topology | Job unit (what the hub assigns) | Reducer fold |
|----------|----------------------------------|--------------|
| **STAR / MAP** | a non-overlapping **section** set | literal section concat (patches commute) — draft-fold / STAR draft-fold |
| **MESH** (debate) | a **stance/critique target** on the same section(s) | bounded position-aware merge (agents overlap by design) |
| **PIPELINE** (stages) | a **stage** consuming the prior stage's output | terminal-stage capture (sequential, not parallel) |
| **SINGLE** | the whole (trivial / single-shot) | identity fold |

The invariant that generalizes is **"reducer verifies coverage against the assignment"**, not
"reducer concats". "Deterministic assembly" is literal concat ONLY for section-partitioned
STAR/MAP; MESH does a bounded merge, PIPELINE captures the terminal stage.

### 3.2 The topology contract (built — §4b)

`agentkit/topology/dynamic.py` exposes a `DecompositionStrategy` protocol with
`SingleStrategy` / `StarStrategy` / `MeshStrategy` / `PipelineStrategy` / `MapStrategy` behind a
single `run_plan` driver. The injected `_REDUCER` now routes through **every** topology
(SINGLE identity-fold, PIPELINE terminal-capture, MESH/MAP/STAR draft-fold), so the
section-aware assemble+verify contract is uniform. This is SRP: the driver owns the contract;
each strategy owns one topology's decomposition.

**Core reducer hook.** `run_plan(reducer: Callable[[list[str]], tuple[str,int]] | None)`;
`_run_star` (and now all strategies) call it instead of the generic synthesis when set.
Module global `_REDUCER` mirrors `_POOL_WORKERS` / `_MAX_SPOKES`. The `Section` semantics stay
in `studio` — no `Section` dataclass is threaded through core. Sections are already encoded as
the artifact's `##` markdown headings and weaknesses are `[## Section]`-tagged (§6.5), so the
markdown *is* the section-keyed structure; the reducer consumes artifact-text + tagged
weaknesses as context.

**Studio reducer.** `findings._make_section_reducer(client, artifact_text, weaknesses)` builds
the merge/refine/review closure; the runner passes it to `run_plan` whenever `_artifact_copied`
(hill-climb), reading `artifact.md` fresh each phase.

### 3.3 Selection is the orchestrator's job (goal-aware) — built

The selector already existed and is principled; the work was to stop discarding its verdict.

- **Primitive:** `agentkit.topology.core.select_topology(TaskSpec)` — a priority-ordered rule
  tree returning `TopologyChoice{topology, trigger, concurrency, rationale, questions_fired}`.
  Self-tests assert review→MESH, independent→STAR, ordered→PIPELINE, etc.
- **Inference:** `infer_spec(description, client)` (LLM) populates the `TaskSpec` booleans;
  `classify_step_topology(description)` is the model-free keyword fallback (MESH=compare/debate/
  vs, MAP=each/every, STAR=gather/search, PIPELINE=then/stage, else SINGLE).
- **Wiring:** `runner.py` calls `assign_topologies(plan, mode="auto", client=client, llm=use_llm)`
  — in `mode=="llm"` every phase routes through `infer_spec → select_topology`.

Selection heuristics and the existing `select_topology` rules:

| Phase shape | Topology | Rule that fires |
|-------------|----------|-----------------|
| independent, decomposable, agents need NOT communicate | STAR | Q3 `subtasks_independent` (+`needs_subdecomposition`→TREE) |
| needs debate / critique / adversarial review | MESH | Q5 `workers_challenge` |
| indivisible / strictly ordered, stage consumes prior | PIPELINE | Q3 ordered (default when not independent) |
| per-item over an upstream list ("each"/"every") | MAP | `classify_step_topology` MAP cue |
| trivial / single-shot / under-specified | SINGLE | Q1 `single_agent_sufficient` (conservative default) |
| multiple entry points / distinct identities | GATEWAY | Q7 (routing, upstream of fan-out) |
| cross-session / human-in-loop / restart | DURABLE_BOARD | Q4/Q8 |

### 3.4 The force-STAR override was deleted (built — E1)

**Was THE blocker.** The runner used to **force-override every step to STAR** whenever
`auto_improve` was on, because only STAR had the reducer that did the section-aware
merge/handoff — so under hill-climb the selection was computed and immediately thrown away.

Now that **every** topology carries the reducer contract (§3.2), the blanket force-STAR
override is **DELETED**: selection is honored for all topologies under hill-climb. Safe because
every topology has the reducer contract. (Earlier history, retained for context: §15 records the
fan-out breadth cap that kept the *forced* STAR from exploding — that cap is still load-bearing
for STAR/MAP fan-out generally.)

### 3.5 Selection observability & planner intent (built — E2/E3/E4)

- **E2 rationale surfaced.** `dynamic.assign_topologies_with_choices` returns
  `TopologyChoice.rationale` + `questions_fired`; the runner threads them into `TopologyEvent`;
  the frontend shows them as the **topology-chip tooltip** (`TopologyStep.rationale`).
- **E3 selection-quality tests.** A mapping test over representative phase descriptions
  (review→MESH, independent gather→STAR, ordered "then/stage"→PIPELINE, "each"→MAP, trivial→
  SINGLE) against `classify_step_topology` AND `infer_spec → select_topology`, plus `infer_spec`
  routing.
- **E4 planner-level intent.** The epic planner emits a per-epic topology INTENT
  (`prompts._build_planner_cot_prompt` / `planning._plan_from_epics`), reconciled against the
  selector in the runner — so the goal-aware planner reasons about topology explicitly rather
  than relying only on a post-hoc per-step classifier.

### 3.6 Fan-out breadth cap (load-bearing for STAR/MAP)

**Root cause of the historical 18-spoke / ~790 K-token explosion.** `max_agents` /
`compute_n_agents` only set `run_plan`'s `max_workers`, which is **concurrency** (thread-pool
size) — NOT the number of spokes. The spoke COUNT is a different lever:
- **STAR / MESH** — `dynamic._facets(description, n)` split the step description on `,` / `and` /
  `vs`. The cap arg was applied as `parts[:max(n, len(parts))]` — an inverted cap that returned
  ALL parts. A ledger-stuffed hub prompt (74 gap items) split into ~18 facets → 18 STAR workers.
- **MAP** — `dynamic._run_map` spawned one worker per item (30 URLs → 30 workers).

**Fix (the COUNT lever).** `run_plan` gains `max_agents: int | None`. `_facets` hard-caps
`parts[:n]`; `_run_map` buckets items into ≤`max_agents` groups (`_bucket`, ceiling-split),
one worker per bucket; `max_agents=None` (CLI, no sizing) keeps MAP's one-worker-per-item shape
for back-compat. The runner passes BOTH levers: `max_workers` (concurrency, from remaining
count) and `max_agents` (breadth, the raw slider value — NOT the remaining-derived count, or a
phase with little remaining work would clamp its own breadth to 1). Conflating the two is what
hid the bug for a whole session; they are deliberately distinct.

---

## 4. The Phase Loop, Epic Plan & Deterministic Section Assembly

### 4.1 Deliverable lifecycle & path resolution

```
1. Loop config panel   user sets explicit path ("improve THIS file") — overrides everything
        ↓ if not set
2. Hill-climb history  latest run with non-empty content for this task_hash
                       (NOT best-score — latest accumulates the most work; score is noisy)
        ↓ if no prior
3. Auto-create         workspace/{session_id}/artifact.md (first phase; path injected)
```

`studio/workspace.py::resolve_deliverable(session, workspace, store)` implements this priority.
`TaskRunStore.latest_with_content(task_hash)` selects the most recent run with non-empty content
— and (per §15 / §14.6) falls back to the durable DB `result_text` when the transient
`artifact.md` is absent, writing that text as the seed.

### 4.2 Epic-based plan structure

The planner outputs a two-level plan so phases are not too granular. Each **epic** maps to one
phase; each epic contains **branches** (the parallel work within that phase).

```
Plan
├── Epic 1 (phase 1, depends on nothing)
│   ├── Branch 1a / 1b / 1c        (independent subtasks)
├── Epic 2 (phase 2, depends on Epic 1)
│   ├── Branch 2a / 2b
└── Epic 3 (phase 3, depends on Epic 2)
    └── Branch 3a / 3b / 3c / 3d
```

Mapping: Epic → `Plan.step` (with `depends_on`); Branches → `TaskRecord` entries in the
`TaskLedger`; branches assigned to workers via `compute_n_agents`; reducer merges all worker
patches at the end of each phase. Epic/branch titles are always derived from the specific goal —
never placeholder text ("Research Phase", "Fetch articles") unless those words genuinely fit.

`_parse_epic_plan(planner_output)` parses the `EPIC_PLAN` JSON. The phase loop is driven by
`plan_obj.steps`; the `TaskLedger` is **seeded up front** from those steps so `remaining()`
reflects real pending work from the first phase (without seeding, `remaining()` is structurally
always empty — this was the R1 fix).

**Planner CoT prompt** (`prompts._build_planner_cot_prompt`): Step 1 understand the goal /
"done" criteria; Step 2 identify 2–5 epics in dependency order (do NOT default to
Research→Analysis→Writing); Step 3 enumerate 6–15 independent branches per epic; Step 4 check
against the existing deliverable + weaknesses (branches address gaps, do not reconstruct); Step 5
emit `EPIC_PLAN` JSON; plus a per-epic topology INTENT (§3.5 E4) and a "each phase must be
DISTINCT" line (defense in depth against the duplicate-phase bug, §15/§14.5).

### 4.3 TaskRecord & TaskLedger

`agentkit/orchestrator/ledger.py`:
- `TaskRecord(id, description)` — `id` slug for dedup (set membership), `description` full text
  for the next hub to reason about coverage.
- `TaskLedger(all_tasks, completed, in_flight)` with `remaining()` (all minus completed minus
  in_flight), `mark_done`, `mark_in_flight` (collision guard while a phase runs), `add_task`
  (seed `all_tasks` up front), `to_context_block()` (COMPLETED / REMAINING block with an explicit
  "do not duplicate" instruction).

**Runner phase loop** carries one `TaskLedger` across all phases, seeded up front, marking each
phase in-flight while it runs and done after. Two levers per phase (§3.6): `max_workers =
compute_n_agents(n_remaining, sizing_cfg)` (concurrency) and `max_agents = sizing_cfg.max_agents`
(breadth). After workers finish, the reducer collects PATCHES blocks and applies them in ONE pass
(deterministic structural merge; LLM refine gated to small docs only — §2.1 G2).

> **Sub-task DONE reconciliation:** completion is tracked at **phase granularity** (`step.id`).
> The hub's `DONE:` JSON block is an agent-facing instruction; the runner does not parse it back
> into the ledger — a finished phase is marked done by id. Sufficient for "don't redo a completed
> phase."

### 4.4 Dynamic agent sizing

`agentkit/topology/sizing.py`:
```python
@dataclass
class SizingConfig:
    min_tasks_per_agent: int = 3   # UI default; overridden by Loop Config slider
    max_tasks_per_agent: int = 5   # UI default; overridden by Loop Config slider
    max_agents: int = 5            # HARD ceiling (menu slider; product spec 3..5)

def compute_n_agents(n_tasks, cfg) -> int:
    # ceil(n / max_tasks_per_agent), CLAMPED to max_agents
    # n=3→1, n=6→2, n=11→3, n=74→5 (capped; pre-fix this was ceil(74/5)=15 → explosion)
    return max(1, min(cfg.max_agents, math.ceil(n_tasks / cfg.max_tasks_per_agent)))

def assign_tasks(tasks, cfg) -> list[list]:  # partition; ceiling-div, earlier agents may get +1
```
The explicit `n` parameter is removed — worker count is derived from the ledger's remaining work.
`SizingConfig` values come from the **Loop Config UI panel** → `LoopConfig` → `sizing()`;
hard-coded defaults are never used directly by the runner.

### 4.5 Hub output protocol & non-overlapping assignment (R2)

Hub agents emit three structured JSON blocks parsed by regex (text outside is ignored):
- `TASK_LIST` — branch id + description list.
- `ASSIGNED` — `{agent_id: [branch-ids]}` (or `{sections, create}` — see §4.8 G4).
- `DONE` — branches from prior epics already complete.

**Enforcement (R2 — non-overlapping).** The hub CoT mandates one-section-per-agent assignment;
the `in_flight` set guards re-entry; AND the runner **validates the ASSIGNED block in code**:
`_parse_assigned` extracts agent→sections, `_dedupe_assignment` detects any section claimed by
≥2 agents and deterministically reassigns it **first-claim-wins** (earliest agent keeps it;
within-agent repeats collapse). The result is surfaced as a `GateEvent(name="worker-assignment",
outcome="pass"|"warn", …)` so violations are visible in the gate panel / Loop Doctor, not silent.
The dedupe runs post-phase (the hub + workers execute inside one `run_plan` call); it makes the
partition deterministic and auditable rather than re-prompting (which would add a round-trip).

### 4.6 Patch-based modification (the assembly primitive)

**Workers are stateless suggesters — they never write to disk.** The hub assigns each worker a
non-overlapping set of sections so patches commute. Each worker emits a `PATCHES:` JSON block
targeting only its sections. A dedicated **Reducer** step collects all patches and writes once.

`agentkit.artifacts.DocPatch(op, anchor, content, source)` with
`op ∈ {replace, insert_after, insert_before, append, prepend, delete}`. **Rule:** prefer
`insert_after` / `append` over `replace` — additive patches on distinct anchors are conflict-free
by construction; reserve `replace` for a wholly-rewritten section no other worker touches.

**Conflict resolution (`agentkit.artifacts.patcher.reduce_patches`):**

| Conflict | Resolution |
|---|---|
| Same anchor, additive ops | Concatenate both contents in task-assignment order |
| Same anchor, destructive | Apply replace first; re-check insert's anchor in post-replace text; apply or append with note |
| Anchor destroyed by prior patch | Orphaned patch appended at end wrapped in `<!-- conflict: anchor not found -->` |
| Identical content | Deduplicate (skip second) |

`reduce_patches(current_text, patch_groups, llm_merge_fn=None, llm_refine_fn=None) -> ReduceResult`
runs **Phase 1 (structural merge — mechanical, no LLM)** then optionally **Phase 2 (refinement —
LLM)**. Per §2.1 G2 the LLM refine is now gated to small docs only; the section reducer
(`_make_section_reducer`) is the active consolidation engine for hill-climb. An LLM refine whose
output is shorter than 80% of the merged text is rejected (keeps the clean structural merge).

L2 history (substantiation levers): the parser accepts a bare `RESEARCH_FINDING` (was
`##`-required, which silently produced 0 patches — the load-bearing no-op); missing-anchor inserts
are demoted to clean appends so findings actually reach the document.

**Atomic write (Reducer only).** `write_artifact(path, text)` = `tmp.write_text` → `tmp.rename`
(POSIX rename is atomic). `cleanup_orphaned_tmp(workspace_root)` runs at server startup.

Crash-safety matrix: worker crash → no file touched; reducer crash before/during `tmp.write_text`
→ original untouched; between write and rename → original intact, `.tmp` cleaned next start; after
rename → new content fully applied.

### 4.7 Reduce-time assignment verification (built — G3)

`planning.verify_assignment_coverage` checks each assigned section is present + populated after
assembly; unmet assignments become a **next-epoch weakness + a gate event**. This is reachable
because the runner synthesizes a **DETERMINISTIC assignment from the rubric template** when the
goal-blind executor emits no `ASSIGNED` block — so coverage verification has something to verify
against even on the executor path. This closes the loop the architecture wants: hub assigns →
reducer verifies → unmet feeds the next epoch.

### 4.8 New-section assignment (built — G4)

The hub prompt emits "create section Z" jobs; `planning._parse_assigned` accepts the
`{sections, create}` shape. One agent owns and populates a new section — replacing the purely
implicit `_merge_missing_sections` placeholder add (which still runs as a skeleton backstop,
§5.2 N1). G4 makes section creation an explicit, assignable, verifiable job.

### 4.9 Create and improve are one pipeline

The only difference is the starting point; the mechanism is identical.
```
Improve: start = prior doc (seeded)  → workers patch owned sections → reducer merges deltas
Create:  start = empty skeleton      → workers fill owned sections  → reducer merges deltas
```
**Create, phase 1 — build the SKELETON, not content.** Derive section headings + a one-line
intent per section FROM THE GOAL (no search needed → robust to a search outage). Each heading is
an owned, fillable section with a placeholder body (`## Results\n_(pending — needs sourced
content)_`). **Placeholders are first-class:** a skeleton-with-placeholders is an honest partial
deliverable — low-scored but improvable, and it doubles as the gap list. When all workers fail
(search down) on a create run, the deliverable is the skeleton + a visible "search unavailable"
notice — **never a fabricated report.** This is the discipline whose absence seeded the original
spiral.

**Topic-agnostic skeleton (§15/§14.5).** `_build_skeleton` emits a FIXED high-level
topic-agnostic ToC (`rubric.DEFAULT_TEMPLATE`) for every goal with a content-derived title
placeholder — no LLM call, no template-reuse for structure (deterministic + outage-robust). Was
leaking the topic from a reused saved skeleton (a prior "Loop Engineering" report) verbatim.
`DEFAULT_TEMPLATE`: Executive Summary, Background and Scope, Key Findings, Evidence and Analysis,
Methodology, Limitations and Open Questions, Conclusion and Recommendations, Source References.

### 4.10 Failure handling

```
zero patches this phase     → do NOT write; seed/skeleton preserved (no-improvement)
all workers SEARCH:error    → halt the phase + GateEvent("search-unavailable", "fail"); record no score
```
Distinct cases (a broken-search run must not masquerade as a finished one): found-nothing = silent
no-op; all-error = halt + notice.

### 4.11 Cross-task context retrieval (R10)

`task_hash = sha256(requirement.strip().lower())[:12]` is an **exact** key. To carry lessons
across *similar* tasks, `TaskRunStore` embeds each run's requirement and retrieves the most
cosine-similar prior tasks:
- `similar_runs(requirement, embedder, k=5, min_similarity=0.35, exclude_hash=None)` — best run
  per distinct `task_hash` above threshold, excluding the current task's exact history.
- `accumulated_weaknesses(...)` — exact-task lessons → similar-task lessons → exact-string dedup →
  semantic consolidation (`_consolidate_weaknesses`, cosine ≥ 0.85 drops a near-dup so "no
  citations" and "sources lack URLs" collapse to one lesson). This is the list fed to the hub.
- Schema: a `requirement_embedding BLOB` column; legacy rows are NULL and lazily backfilled
  (`_backfill_embeddings`) on first similarity query — avoids a blocking migration. New runs embed
  on `record()` when an embedder is wired.
- Graceful failure: embedding/backfill/consolidation are best-effort (`try/except`); a down
  embedder never breaks a run.

### 4.12 InFlightRegistry (pending-fetch dedup)

`agentkit/tools/fetch_cache.py::InFlightRegistry.get_or_fetch(key, fetch_fn)` prevents same-URL
duplicate HTTP calls from parallel workers in the same phase: the first caller fetches; concurrent
callers wait on a `threading.Event` and share the result.

---

## 5. Document-Processing Principle (Moving-Window) & Its Passes

> **Rule (§4d):** no LLM call may take a large document and be expected to echo it back whole. Any
> doc-processing operation must **window → process each window → DEDUP → reassemble
> deterministically** (or operate on a localized block + splice). Whole-doc-in / whole-doc-out is
> banned for anything that can exceed a few KB. (Verified failure: a model truncated 68 KB → 36 KB.)

Status of the doc-processing ops against this rule:
- `mine_weaknesses_from_outputs` — moving-window (`_MINE_WINDOW` / `_STEP` / `_MAX_WINDOWS`, §6).
- `score_result` — windows to 20 K (bounded; acceptable — it reads, doesn't echo).
- `_repair_lints` — block-level + deterministic splice.
- `_synthesize_analysis` — reworked to windowed (see §5.4).
- reducer additive-merge (Path B whole-doc LLM merge) — replaced by deterministic section
  assembly (§2.1 G2 / §4.6).

**Dedup on reassembly (required).** Overlapping windows emit duplicate results — the same
comment/finding/weakness straddling a boundary appears in two windows. So the rule is `window →
process → DEDUP → reassemble`, never reassemble raw. Two-stage dedup, reusing existing code:
lexical normalized-text key (`task_runs._norm_weakness` pattern) drops verbatim dupes; semantic
cosine ≥ 0.85 over embeddings drops near-duplicate rephrasings. URL-bearing paragraphs are NEVER
dropped (`_dedup_paragraphs`).

### 5.1 Outline integrity passes (built — N1)

`artifact_text` module:
- `_merge_missing_sections` — appends an absent template section ONLY after a **concept-coverage
  check against the WHOLE body** (not just heading token-match), and does NOT bolt the template
  onto a doc that already has its own outline. Fixes the "doubled / Frankenstein structure" bug
  (the agent organized under "Design Architecture / Implementation Methodology / Example Code"; the
  old token-match against the template's "Background / Key Findings / …" failed → it APPENDED all
  of them → two parallel skeletons in one served doc).
- `reconcile_outline` — drops empty template duplicates.
- `dedupe_sections` — collapses DUPLICATE populated headings to the richest body. Fixes the
  gemma-echo + grow-only accumulation that stacked each template section 8–10× (the reducer
  preserves verbatim; grow-only never removes; so each epoch re-appended).
- `_split_glued_headings` / `normalize_artifact` — un-glue mid-line headings (`## A### B` → two
  blocks) — the "wrong format" fix.

**Wiring (critical):** at **seed** (`reconcile_outline` → `normalize_artifact`), **after every
phase writeback** (keeps the INTERMEDIATE artifact clean, not only the final one), and at **run
finalization**.

> The "heading glued to plain prose" case (a heading with no marker boundary, e.g. text running
> straight into a heading with no `#`) is **not deterministically splittable** — no marker to
> anchor on. Tracked, not fixed. (See §14 partial.)

### 5.2 Per-section ratchet & section identity

- `agentkit.artifacts.sections.split_sections` — deterministic `##` split (0 LLM) that keys
  per-section hashes. `section_hash` anchors section identity across heading drift between epochs
  — verify/splice on the hash, not raw heading strings.
- `accept_rewrite(old, new)` (F2) — relaxes the writeback ratchet from whole-document grow-only to
  **per-section grow-only**: a reviser may REPLACE one section (repair, ranking table, dedup) even
  when net length shrinks, while still guaranteeing no non-empty sourced section is deleted/emptied.
  Must land before any ranking-table `replace` or the old whole-doc ratchet rejects it.

### 5.3 Windowed synthesis (built — §4d)

`artifact_text._synthesize_analysis` → `_synthesize_windowed` / `_synthesize_block`: per-section
rewrite, no whole-doc echo, then two-stage dedup on reassembly. Synthesis is cross-section (§5.6
caveat) so windows overlap / carry a short cross-section summary so comparison still works. The old
whole-doc-echo synthesis silently NO-OPED at scale (the model truncated a long echo, the length/URL
guardrail then REJECTED it) — verified only on a toy input; same trap as the repair bug.

### 5.4 Instructor-tone readability refine (built — NEW, final-epoch only)

`artifact_text._refine_readability` — a FINAL instructor-tone pass that rewrites the report
paragraph-by-paragraph into clear teaching prose with more analysis/reflection, **every citation
intact**. It **supersedes the plain analysis pass**.
- §4d-windowed (per section/paragraph, no whole-doc echo).
- Runs **only on the FINAL epoch** to bound cost.
- Each section is **rejected if it drops a URL or shrinks** (anti-regression).
- The directive (`_DIRECTIVE_READABILITY`) was chosen by a **gemma A/B/C/D eval** on a real seed
  section scored on citation-retention + an LLM readability judge: the "instructor" style + a
  "CITATIONS ARE SACRED" reinforcement won (4/4 citations retained, readability judge #1).

This is the analysis/synthesis layer the backlog asked for (the report previously only
copy-pasted sources verbatim — citations treated as *content* instead of *provenance*): synthesis
with no citation regression.

#### 5.4a Post-gate finalization — refinement must reach the SERVED artifact (built)

The refinement passes (normalize/dedup, mermaid repair, readability) originally ran in `_postrun`
**before** the keep/discard gate. When the gate then REVERTED to the prior seed
(`_art_file.write_text(_seed_text)`), it overwrote the just-repaired/refined file with the raw
seed — so a reverted epoch served the **unrepaired, unrefined** document (the reported
served-broken-mermaid: repair ran and succeeded, then the revert clobbered it; the broken seed was
re-served every epoch and never self-healed). **Fix:** a post-gate finalization re-applies
`normalize_artifact` → `_repair_lints` → (final-epoch) `_refine_readability` to **whatever the gate
kept** (new epoch OR reverted seed) and writes it. normalize/repair are idempotent; readability is
bounded by its URL + `min_ratio` guards. **Lesson (generalizes §5.5):** a fix that lands on the
wrong side of the gate is repaid every epoch — refinement must be applied to the SERVED winner, not
to a pre-gate candidate that a revert discards.

#### 5.4b Cleaned artifact is the SEED; full grounded is the archive (design)

The cleaned (readable + deduped, shorter) version is written to **`artifact.md`** — which is the
**seed for the next turn** and the scored artifact — while the full grounded version is preserved as
**`result.md`** (archive / full-evidence reference). Rationale (the clinching one): `artifact.md`
seeds the next round, so if it held the un-deduped grounded wall, every round would **re-inherit the
wall and redo the dedup from scratch → the cleanup is thrown away each epoch** (the same
persist-or-repay failure as §5.4a's mermaid revert). **General rule for the loop: any cleanup
(dedup, normalize, repair, readability) must land on the SEED, or it is wasted.** Dropping only
*redundant* findings (same-URL / already-in-doc) keeps every DISTINCT citation and the section
anchors, so seeding from the cleaned version neither starves the loop nor breaks the patcher's
anchor matching — clean seed in, clean doc out, compounding the right way.

#### 5.4c Reducer-side finding dedup — thin the wall at the SOURCE (planned)

Readability alone CANNOT fix the "quote-wall" (one `This defines…: "quote" (cite)` sentence per
finding, ~30 of them, many citing the same URL): summarizing a URL-dense block on a local model
drops a citation, and the unconditional URL guard then **rejects** the rewrite → the wall survives
verbatim. The fix belongs in the reducer (`findings._make_section_reducer` /
`_findings_to_patches`), thinning the wall **before it is built** (where dropping a redundant
same-URL finding is not a regression — the section never had it): (1) **same-URL merge** — group
surviving findings by `_normalize_url`, keep one per URL (richest verified quote); (2)
**against-doc dedup** — drop a finding whose URL is already cited in the target section unless it
carries a genuinely new claim (cosine `<0.85` vs that section when an embedder is wired), extending
the existing F1 `dedupe_findings` (which only dedups within one phase's drafts); (3) **per-section
density cap** — ≤K findings woven per `PATCH_TARGET` per phase; (4) **deterministic scaffolding
strip** in `_findings_to_patches` — remove a leading `This (defines|provides|establishes|…):` prefix
so even surviving findings read as prose, not a template. Order: dedup at the reducer (structural,
deterministic, cheap) → readability at the render (stylistic, LLM); GEval/RAGAS (§14) later replaces
the quantity-biased rubric so the gate itself becomes quality-aware.

### 5.5 Mermaid / lint repair (built — N5)

`artifact_text._repair_lints` is **un-gated from `use_llm`** (output validity is mode-independent)
and instrumented (`_dbg("repair_lints: lints_before=… changed=… lints_after=…")` → throughput.log,
+ exception capture). Verified live on the real artifact: 2 malformed edges → 0.

Companion deterministic validator `studio.artifact_lint` (malformed mermaid edge, unbalanced code
fence) emits each defect as a weakness — surfaced in the GUI and seeded into the next run's
constraints. The reducer prompt gains a NARROW repair exception ("fix ONLY these flagged blocks in
place, everything else verbatim") fed by linting. **Lesson:** a loop only ever fixes what its
weakness signal can name; an additive optimizer needs an explicit, bounded licence to repair, or
defects (a malformed mermaid edge, a truncated code block) become immortal across every epoch.

### 5.6 Don't over-silo (G5)

Synthesis/analysis needs the whole assembled doc. Section isolation applies to GENERATION /
PATCHING, **not** to the synthesis pass. The cross-section synthesis pass still sees the assembled
whole; the moving-window mechanism (overlap + carry-forward summary) is what lets it do so without
a whole-doc echo.

### 5.7 Honest ranking synthesizer (F4)

`agentkit.artifacts.ranking.synthesize_ranking_table(findings, metrics)` replaces the
source-selection section with an **honest SPLIT presentation** (wired as `runner._apply_ranking`):
- A **Measured popularity** table ranks only sources with a real, independently-verifiable metric
  (citations, stars).
- A separate **Reported / unranked** listing holds sources with only a stated claim or no public
  number — the two are **never ranked together** (mixing a citation count with a view count is
  apples-to-oranges an evaluator flags).
- It **never invents a metric**: a source with no public number shows `—`. A mostly-`—` table with
  marked gaps is the CORRECT output when metrics genuinely do not exist — not a failure to paper
  over. A leading one-line methodology note states how many sources are measurable.

Numbers trace to source: a fetched citation/star count (`agentkit.artifacts.metrics.Metric`), a
stated-but-`reported` claim (`parse_stated`, never re-derived), or in-corpus reference frequency.
F4a metric acquisition (`source_metric` — Semantic Scholar citationCount, GitHub stars) is
**rate-limit-safe**: cache-first under `metric:<url>` in `.web_cache.json` (TTL), serialize +
backoff (reuse `tools.py::_is_rate_limit`), optional `SEMANTIC_SCHOLAR_API_KEY`, batch endpoint
for all arxiv ids in ONE request, degrade to the salience proxy on persistent 429/timeout — **never
block the run**. (Keyless S2 returned HTTP 429 on a 2-request burst.)

### 5.8 Substantiation levers (L1–L3, woven evidence)

| Lever | Mechanism | Effect |
|---|---|---|
| L1 — worker substantiation | Executor emits `RESEARCH_FINDING` blocks only (report prose forbidden); tool loop 5→8 iters + a forced tools-disabled synthesis turn on iteration exhaustion | Worker no longer cut off mid-`tool_use` returning only a preamble |
| L2 — patch-based reducer | Parser accepts a bare `RESEARCH_FINDING`; missing-anchor inserts demoted to clean appends | Findings reach the document instead of silently dropping |
| L3 — woven evidence | Dual-oracle grounding (URL-fetched OR quote-verified), tolerant URL match + fuzzy quote match; fetch-density prefetch of cited-but-uncached URLs | Findings surviving a phase 2 → 26, doc +7.4 K |

Plus **copy-paste-verbatim refinement**: the source's own words are the evidence — the
fabrication-prone CLAIM-rephrase step is dropped. F1 finding dedup
(`agentkit.artifacts.dedup.dedupe_findings`, cosine ≥ 0.85, lexical fallback) collapses the
26-finding dump before patching.

> **Verified finding (v29..v33).** Grounding throughput went broken → 26 findings/phase and the
> doc grew 38 K → 46 K, but the score held at 0.64–0.67. The binding constraint is **not
> grounding** but **structure** — ranking / metrics / completeness and inherited truncation —
> which additive levers cannot fix by construction. This is why F4 (ranking) and the analysis pass
> exist.

### 5.9 Local-model format tolerance (oMLX fenced tool calls)

qwen / oMLX models emit tool calls as fenced JSON (` ```json {"name","arguments"} ``` `) and
findings as JSON objects — not the `<tool_call>`-tagged / native `tool_calls` and plain
`RESEARCH_FINDING:` lines the parsers expected. A live qwen run therefore `fetched=0`, grounded
nothing, and degraded to refusal-prose (score ~0.24), while a Haiku probe on the same task fetched
11 pages — i.e. "these frameworks can't be verified" was a **capability failure dressed as
principle**. Fixes: `_parse_inline_tool_calls` (`studio/tools.py`) parses fenced/bare JSON tool
calls (guarded by registered-tool names so stray JSON can't fire a tool); `_parse_findings`
(`runner.py`) parses JSON-wrapped `RESEARCH_FINDING` alongside plain text. Verified: qwen
`fetched 0 → 1`, the run **0.236 → 0.80**. **Symptom-first lesson:** 0 sources ⇒ suspect the
worker tool-call FORMAT before blaming the input or the loop.

---

## 6. Evaluation — Rubric, Adjusted Score, Weakness Mining & Refutation

### 6.1 Why solved/total was retired

The original scorer was LLM self-eval (rated a good report 0.1). It was replaced by
`_weakness_score = solved/total` over the mined weakness set — but that **punished thoroughness**
(more mined weaknesses → lower score even as the doc improved) and **rewarded an empty doc** (no
weaknesses → 1.0). It also jittered: with ~5 items, one extra unsolved weakness swings the score
0.13–0.20 on miner noise; v25 0.80 (38 K) → v36 0.67 (64 K) is a BIGGER, more complete document
with a LOWER score — issue churn, not a quality regression. `_weakness_score` is **retired from the
main path**.

### 6.2 The deterministic rubric (`studio/rubric.py`)

The recorded score, the convergence/delta signal, the template-save gate (`≥ 0.6`), AND the epoch
keep/discard judge are now **one metric end to end**. Synthesized from report-quality literature
(DEER arXiv:2512.17776; DeepResearch-Bench arXiv:2506.11763; the CRAAP test; academic rubrics).

| Criterion | Weight | Signal (deterministic) |
|---|---|---|
| sourcing | 0.25 | # distinct cited source URLs (target 8) |
| verification | 0.25 | # URLs confirmed real via web cache (`verified_urls_in_cache`) |
| evidence_depth | 0.20 | direct-quote / blockquote density |
| structure | 0.15 | fraction of template sections present (concept-aware) |
| methodology | 0.15 | methodology/scope present + non-thin body (word floor) |

`rubric_score(text, verified_urls, weights, required_sections) -> [0,1]` (weighted sum);
`resolve_weights` L1-normalizes a partial GUI override and drops unknown keys. On the real
fixtures where the live LLM judge tied (haiku & sonnet), the rubric cleanly separates good = 0.925
vs thin = 0.4531.

**Structure matching is concept-aware** (`rubric.sections_present` / `_content_tokens`): a required
section counts as covered if the exact phrase appears OR a heading shares a content word with it.
The DB report skeleton scored only 0.5 under exact-substring matching because it used real-world
synonyms ("Verified Sources" for "Source References", "Core Finding" for "Key Findings") — a good
report penalized for vocabulary, not a missing section. After the fix it scores 1.0; good fixture
stays 1.0, thin stays separated.

**Two distinct "template" mechanisms** (do not confuse): the **Rubric template**
(`rubric.DEFAULT_TEMPLATE` / `rubric_config.template`) is a flat list of ~6 canonical section names,
authored/GUI-edited, used for `structure` scoring + generation steering; the **DB skeleton**
(`studio/templates.py`, table `report_templates`) is a full 40+ heading tree learned from a real
report (`extract_skeleton` saved when `_score ≥ 0.6`), keyed by requirement embedding
(`find_template`, threshold 0.6), used to seed the FIRST document's structure. Concept-aware
`structure` matching is what lets a report generated from the (good) DB skeleton still score 1.0
against the rubric template — they no longer need shared exact heading vocabulary.

### 6.3 N4 fence-aware headings (built)

`rubric.mask_fenced_code` — heading/structure scans (`score_breakdown`, `sections_present`,
`artifact_text._detect_gaps`) ignore `#` lines inside code fences. Python comments like
`# --- MOCK ... ---` inside a ` ```python ` block were mis-counted as H1/H2 headings, polluting the
structure signal and `sections_present`. The lint already tracks fence state — reused here.

### 6.4 `adjusted_score` — couple the score to the weaknesses (built — §14.7)

A re-run once scored **1.0 with 8 open weaknesses** (a malformed mermaid, fabricated URLs, zero
inline citations). Incoherent: the rubric is a deterministic QUANTITY checklist blind to
correctness — it can't parse a mermaid, it counts the *word* "verified" rather than cache matches
(so fabricated URLs pass), and it counts URLs anywhere (so an end-bibliography satisfies "sourcing"
with zero inline citations). `rubric.adjusted_score(base, weaknesses)` couples them: the invariant
is **ANY open weakness ⇒ score < 1.0** (hard ceiling 0.92), plus a graduated capped penalty
(`0.04`/weakness, `+0.06` for an objective lint defect, floor 0.6×). Example: the 1.0 / 8-weakness
case now records **0.62**. The runner records `adjusted_score(rubric_base, _weaknesses)` AFTER the
keep/discard gate, from the clean `_scored_text` (before any annotation). **Lesson:** a score that
can't see what the critic sees will always disagree with it; the metric must be a function of the
same evidence the weaknesses are mined from.

### 6.5 Weakness mining — section-bound, moving-window, deduped

Weaknesses are still mined — they are the IMPROVEMENT SIGNAL that seeds the next run's constraints
— but they no longer determine the score. They are surfaced BELOW the report in the result view
(`HillClimbEvent.weaknesses`, rendered as a separate block by the frontend `ResultWindow`); they are
NEVER concatenated into `result_output`, so the deliverable stays clean.

- **Moving-window miner.** `mine_weaknesses_from_outputs` sweeps the FULL document in overlapping
  ~12 K windows (step 10 K, ≤8 windows), mining each and union-deduping. SYMPTOM it fixed: V36 (64 K)
  recorded "Required section 'Methodology'/'Conclusion' is missing" although both exist (`##
  Methodology` @ 54,764, `## Conclusion` @ 55,929) — the old 8 K-head + 4 K-tail miner never saw the
  MIDDLE. Small docs (≤12 K) still take ONE call.
- **Section-bound weaknesses.** Each weakness is prefixed with the section it concerns
  (`[## Sources] missing URLs`; `[document]` for whole-doc/structural issues). On the next run the
  hub assigns sections, and each worker routes weaknesses by label: a `[## Section]` weakness is
  fixed ONLY by the agent that owns that section; a `[document]` weakness is fixed by EVERY agent
  within its OWN sections (a global bar — grounding, no truncation, consistent terminology —
  applied per-section). The `[document]` broadcast is why a structural weakness with no single owner
  still gets acted on — it is handed to all agents, conflict-free under the additive-patch contract.
- **Semantic dedup** (`runner._run_inner`, cosine ≥ 0.85). The miner once surfaced the SAME
  popularity-ranking gap under two section prefixes — exact-string dedup kept both, depressing the
  count score. Verified with BGE-M3: two popularity weaknesses cos = 0.948 → collapse; a distinct
  citation-redundancy weakness cos = 0.765 → kept separate.
- **Scorecard-generated weaknesses.** The unified 100-point scorecard is also a deterministic
  weakness source. `rubric.scorecard_weaknesses` converts low category rows into short
  section-prefixed weaknesses using the frozen `scoring_template`: section-related categories become
  `[## Section] Scoring gap: ...`, while universal/whole-document categories become `[document] ...`.
  These are prepended to the normal mined/lint/publish weaknesses, recorded in `TaskRunStore`, and
  accumulated by `TaskRunStore.accumulated_weaknesses` for the next run. Workers still receive only
  weaknesses relevant to their assigned section plus `[document]` bars; reducers receive the full
  weakness set and full scoring matrix for whole-artifact judgment.

Weakness lifecycle:

1. At task creation/planning, the full frozen scoring matrix is appended to planner-visible task
   requirements so the hub can create tasks against the same rules later used for measurement.
2. During fan-out, `planning._section_focus_text` crops scoring rules and weaknesses by section
   relatedness. Universal rules and `[document]` weaknesses are repeated for every worker; unrelated
   section rules are not shown to that worker. A run-local `remaining_scoring_matrix` shrinks after
   each reducer pass, so later workers do not receive requirements already achieved by earlier
   phases.
3. During reduction/finalization, the reducer always sees the FULL frozen scoring matrix, not the
   shrinking worker matrix. Reducers and final scoring measure the whole artifact against all
   profile/template rules.
4. After each reducer/writeback, achieved scorecard rows are removed from `remaining_scoring_matrix`.
   Unachieved rows become section-routable weaknesses. Phase outputs are also mined for weaknesses,
   deterministic lints/publish-gate issues are prepended, and false weaknesses are refuted.
5. Recorded weaknesses seed the next run, where the same section routing sends each fix to the agent
   that owns the affected section.

### 6.6 False-weakness refutation (built — N2/N3)

The LLM miner hallucinates MISSING/TRUNCATED content that is PRESENT. Verified false weaknesses in a
real run: "lack of example code" (a python block existed), "no conclusion / ends abruptly" (a
Conclusion existed; doc ended cleanly), "Executive Summary truncated '…and st'" (it was complete).

- `task_runs.refute_false_weaknesses` — drops phantom "missing example code / no conclusion /
  truncated summary" weaknesses when the doc demonstrably has them (generalizes the present-content
  guard beyond template names: code blocks, conclusion, summary), treating a miner truncation claim
  as authoritative ONLY when the deterministic check agrees.
- `_section_ends_cleanly` — per-section truncation refutation. The older `_ends_cleanly` /
  `_trunc_fact` checked only the DOCUMENT end, so the miner re-hallucinated mid-section truncation
  (the W3 class).
- **Deterministic section filter** (`runner._run_inner`): after mining, any missing/absent-section
  weakness for a section that `rubric.sections_present` (concept-aware, full text) confirms present
  is dropped. (The scorer still windows at 20 K and its UNMET seeds the miner, so the false claim can
  echo through; this filter cleans the section part.)

Verified on the real report with a live haiku judge: 4 false "missing section" weaknesses dropped;
4 genuine ones remained.

> **Verification verdict that grounds this (PLAN-non-additive §0).** Of the 5 v33 mined weaknesses,
> W3 ("truncated mid-sentence '…loops that prom'") was FALSE — the sentence completes at offset
> 8015; W5 (ReAct/Reflexion URLs "not fetched") was FALSE — the disk cache shows them fetched. Only
> W1 (no ranking by popularity) and W2 (only one source has a metric) were real-and-open; W4 (13
> `<!-- conflict -->` markers) was real-but-already-fixed (anchor-demotion `805318f`). Root causes
> of the false ones: the scorer/miner showed only `verified_urls[:20]` (the artifact had 34+ URLs →
> real citations invisible → flagged "not in the verified list"), and `_verified_urls` built its set
> only from search-result entries, ignoring `fetch:` cache entries — so fetched-but-not-searched URLs
> were invisible too. Make the eval trustworthy BEFORE building structural features against it.

### 6.7 Template steers generation (built — §14.2)

When `session.rubric_config["template"]` is set, the section list is appended to `requirement`
("Structure the deliverable with these sections…") **after** `_base_requirement` is captured — so
it never changes `task_hash`. Injected **only when explicitly configured**: defaulting to
`DEFAULT_TEMPLATE` would force report headings onto non-research tasks and compromise their quality
(the no-compromise constraint).

### 6.8 The keyword rubric is shallow (open)

A quantity checklist will always be gameable. The durable upgrade (deferred, §14) is an LLM-judge
with an explicit rubric (DeepEval `GEval` for coherence/analysis/task-fit) + a RAGAS-style
faithfulness/groundedness check against the fetched `.web_cache.json` pages, replacing the
keyword-counting `verification` and `evidence_depth` parts. `adjusted_score` is the deterministic
bridge until then.

---

## 7. The Hill-Climb Loop — Epochs, Seed Carry-Forward, Keep/Discard Gate, Lineage Hygiene

### 7.1 Epoch heartbeat — one Run auto-iterates to `max_epochs` (§14.4)

`max_epochs` used to be a dead label (each Run did exactly ONE pass; reaching `max_epochs` required
pressing Run repeatedly). Now `run(requirement)` wraps `_run_inner` in an epoch loop:
- `_run_inner` RETURNS its per-epoch outcome `EpochResult(version, score, delta, status)` instead of
  emitting the terminal `done`; `run()` decides continue/stop and emits the single terminal `done`
  after the loop. One SSE stream spans all epochs; the per-pass `HillClimbEvent(epoch=version, …)`
  drives the frontend timeline.
- **Carry-forward.** Each pass seeds from the prior via `auto_improve` (`latest_with_content` →
  prior doc + weaknesses). Pass N records before pass N+1's seed-lookup, so N+1 **improves N's
  document incrementally** — not a from-scratch rebuild. The per-section ratchet (§5.2 F2) guarantees
  no regression across passes.
- **Re-entrancy.** Each epoch prefixes its step ids with the epoch index (`e2:s3`) so every pass is a
  distinct sub-DAG; the graph store is reset per epoch.
- **Guard / back-compat.** The loop engages only when `auto_improve` is on AND `max_epochs > 1`;
  otherwise a single pass exactly as before. Always runs ≥ 1 pass.

### 7.2 Early stop = plateau, NOT cumulative version (§14.8)

**Bug.** Running one epoch then another did NOT equal running with `max_epochs=2`. The loop stopped
on `EpochResult.status == "converged"`, set when `_version >= max_epochs`. But `_version =
next_version(task_hash) = MAX(version)+1` over **every prior run of the task** — the cumulative
all-time version, not the epoch index within this run. On a task at version 5, the first epoch of a
`max_epochs=2` run records version 6, `6 >= 2` → converged → break after one pass.

**Fix.** `run()` breaks only on a genuine `"plateau"` (`version > 1 and delta < min_improvement`,
default 0.02) or cancel; the per-run epoch count is already bounded by `range(max_epochs)`, so
`converged` must not also gate it. The `converged` label is retained for the timeline but no longer
controls the loop. **Lesson:** a counter that means "how many times ever" can't double as "how many
times now" — loop control must read a per-invocation index, not a persistent monotonic one. (Open
cosmetic follow-up, §14: the displayed status still reads "converged" mid-improvement on a task with
history.)

### 7.3 The keep/discard gate (close the open loop) — §14.1

Context: the self-improvement path was an **open loop** — it accepted every epoch's artifact as
long as it was not shorter (a length-only ratchet), with no quality keep/discard. The genuine
optimizer (`agentkit.evolve.optimize_text` / `loop.hill_climb`) was not wired in.

- **D1 — `task_hash` identity is the BASE requirement (goal-invariant).** `_run_inner` prepended the
  goal/constraints block into the requirement BEFORE hashing, so attaching a goal forked the lineage
  → cold-start v1, no carry-forward, weakness-score 0/N = 0.00. Hash `_base_requirement` captured
  before the goal block (and before the per-iteration prefix). Guard:
  `test_task_hash_invariant_to_attached_goal`.
- **D2 — epoch keep/discard gate.** `studio/epoch_gate.py` (`accept_epoch`, `make_preference`): at
  the epoch boundary the new artifact is KEPT only if a **label-free judge strictly prefers it over
  the seed**; otherwise the prior is restored. Worst case = prior good report retained → quality
  cannot regress. The gate never reads the absolute score (noisy, changes across versions). Placed in
  its own module to avoid growing `runner.py` (D5).
- **D3 — hardened `self_preference` parsing (agentkit modified).** With the strict `{"winner":…}`
  parser the judge tied a 58 KB good report with a 4.5 KB stub in both directions — because
  haiku/sonnet answer in markdown prose ("**Artifact A is significantly better**") and `json.loads`
  failed → silent TIE. `agentkit/evolve/core.py::_extract_winner` now resolves a verdict via trailing
  `VERDICT:` line → embedded JSON → prose → TIE; `_PREFERENCE_SYSTEM` asks for a trailing `VERDICT:`
  line. The judgment was correct; only extraction was lossy.
- **D4 — LLM pairwise preference is an unreliable gate; use a STRUCTURED rubric.** Live on real
  fixtures: haiku strict-JSON → TIE (the D3 bug); haiku hardened+rubric → TIE (model hedges "neither
  acceptable"); **sonnet hardened+rubric → TIE**. Asking an LLM "which full report is better?" is not
  a reliable keep/discard signal even on a strong model with a rubric — it hedges to tie. A
  deterministic proxy (verified-source density) separates them cleanly. So the gate's `prefer` fn is
  a **structured rubric**, deterministic where possible; LLM only PER-CRITERION (never "which is
  better overall"), verdict token FIRST so a `max_tokens` cap can't truncate it.
  `make_rubric_preference(verified_urls, weights, required_sections)` is the default gate judge for
  large artifacts; `self_preference` stays available but is not the default. `accept_epoch` machinery
  is unchanged; only the injected `prefer` changes.

Reuse vs rebuild: reused as-is — `loop.goal.check_goal`, `loop.chain.LoopChain`,
`evolve.self_preference`; modified in agentkit — `self_preference` parsing (D3); not yet wired
(deferred) — `optimize_text` to own the epoch loop + autonomous heartbeat.

### 7.4 The gate must key on the DURABLE record, not the ephemeral artifact (§14.6)

**Symptom.** Enabling hill-climb made output *worse*: a cross-session re-run served a 0.122 /
28-line stub over a prior 0.4148 / 122-line report — the regression the gate exists to forbid, yet
recorded and downloaded.

**Root cause — "never copied because never written," not "deleted."** The gate fired only `if
_artifact_copied and _seed_text.strip()`. Both came solely from the prior session's on-disk
`artifact.md` — a *transient* working file written only on the reducer/patch path. The *durable*
per-session deliverable is `result.md` (== the DB `result_text`, recorded for every run). A
raw-synthesis run (an oMLX model that dumped findings instead of patching) finalizes `result.md` but
never writes `artifact.md`. Evidence: of 1524 workspaces only 62 had `artifact.md`; the 0.80 runs had
one, the regressed 0.12–0.41 runs did not; no `unlink`/`rmtree`/`rename` of it exists. So the seed
lookup found no file → `_artifact_copied=False` → **gate skipped** → the regressed epoch served
unprotected and became the carried-forward "latest."

**Fix.** Seed (and the gate) now fall back to the DB `result_text` when `artifact.md` is absent:
`latest_with_content` returns a run whose `result_text` is non-empty even with no file, and the
runner writes that text as the seed (`_artifact_copied=True`). The gate fires whenever ANY prior
exists; only a true cold start (version 1) accepts unconditionally. **Lesson:** a closed loop
guarded by a volatile precondition is an open loop most of the time — gate on the durable record (the
DB row), not the ephemeral artifact.

Two related defects from the same run:
- **A no-seed run degraded BELOW a clean cold start.** The patch-or-silent worker contract was
  injected under `if _prior:` (a prior run exists) not `if _artifact_copied:` (a doc was seeded). So
  v2 was told "patch sections in the artifact, find nothing → output NOTHING" with no artifact to
  patch → a weak model went silent → the reducer kept nothing → a 28-line scrap (0.12) below the
  clean cold-start v1 (0.41). **Fix:** gate the edit contract on `_artifact_copied`, not `_prior` —
  no seed ⇒ generate cleanly.
- **Hill-climb could not CREATE a missing section.** The full template skeleton is laid down only
  `if not _artifact_copied` (cold start). A seeded run inherits the seed's structure and the reducer
  only PATCHES existing headings — so a rubric-template section absent from the seed (e.g.
  "Limitations and Open Questions") was mined as a weakness every epoch yet never created. **Fix:**
  `_merge_missing_sections` appends each absent template section as an empty heading + placeholder
  before the phase loop (concept-aware so a renamed-but-present section is not duplicated; no-op on
  cold start). (Superseded/strengthened by the N1 outline-integrity work, §5.1.)

### 7.5 `is_last` is load-bearing — do NOT remove

Two distinct roles, both essential:
1. **Weakness lifecycle** (`if _gaps and not is_last` + post-loop `mine_weaknesses_from_outputs` →
   `_store.record`): non-last phase gaps re-enter the in-run ledger (retried THIS run); last-phase
   gaps are NOT re-queued → mined from the final output → persisted to the weakness DB for the NEXT
   run. A weakness that got solved is absent from the final output → not mined → drops off. This is
   the cross-epoch hand-off.
2. **Document-merge prompt** (`if is_last and upstream`): shapes the last-phase WORKER prompts (a
   different stage than the injected reducer) — not redundant with the §3.2 reducer.

### 7.6 Gap routing — section is the unit of work (§11.4)

`_detect_gaps` flags only **empty / placeholder** sections — NOT "prose without an inline URL". The
old rule mis-flagged every well-formed section of a properly-cited report (citations live in a
References section), producing ~74 false gaps that exploded agent sizing to 18 and burned 2.5 M
tokens. Each gap is tagged with its nearest top-level (h1/h2) section, then **consolidated to
distinct top-level sections** (`_gap_sections`) before sizing/routing: 74 raw sub-gaps collapse to
the ~6 real sections that own them. Sizing is driven by consolidated sections, never raw gap count,
and clamped by `SizingConfig.max_agents` — so a noisy gap list can no longer inflate the topology
(the shared-ledger-poisoning fix).

Routing: gap in non-last phase → one `TaskRecord` PER SECTION in the `TaskLedger` → handed to phase
N+1's hub (re-judged within its own bounded sizing, this run). Gap in last phase → SECTION-BOUND
weakness in `task_runs.db` (`[## Section] issue`) → next run's hill-climb injects each weakness only
to the agent that owns its section (feeds the §4.11 `accumulated_weaknesses` pipeline). Reducer gaps
are a **better weakness source** than `mine_weaknesses_from_outputs`: concrete and grounded in what
the reducer actually saw.

### 7.7 Loop closure — surface, never hide (repeat-failures)

Without closure, recorded weaknesses *recur* across runs (observed v8→v24: same "unverifiable
sources / truncation / no metrics") because the detector that re-finds a weakness does not know it
was already injected and not fixed. A **repeat-failure** = a weakness recorded in ≥ `REPEAT_LIMIT`
(3) distinct prior runs (`TaskRunStore.repeat_failures`, over normalized text, section label
stripped). It is **not dropped and not hidden**:
1. NEVER drop it — it flows through the normal handoff (ledger → next phase), so the LAST phase gets
   a final attempt with full-document context + search.
2. After the run, any repeat-failure still open is APPENDED below the result as a "## ⚠️ Known
   unresolved issues" block (`_unresolved_block`), shown in the chat window.
3. It is scored BEFORE the block is appended, so the honesty footer never inflates/deflates the
   score.

**Transparency over silent convergence:** a genuinely unfixable weakness (the data does not exist,
an infra 503) is surfaced every run until resolved, rather than swept away after N rounds.

### 7.8 Termination

The run records `gaps_remaining`. Hill-climb stops when `gaps_remaining == 0` (done), OR two
consecutive runs reduce it by zero (genuinely stuck), OR `max_epochs` reached (hard cap), OR a
plateau (§7.2).

### 7.9 Lineage hygiene & inherited-state sanitization

Hill-climb propagates state (artifact + weaknesses) across runs — forward-generation fixes are not
enough; inherited state must be sanitized/normalized too. Each fix below was correct in isolation
yet exposed the next.

- **Seed-lineage hygiene (root cause documented).** `task_runs.latest_with_content` seeds from the
  LATEST lineage artifact, so a bloated/cancelled prior run poisons the next seed. Mitigated by the
  **seed-time `normalize_artifact`** (§5.1 wiring) — the seed is cleaned (reconcile_outline →
  normalize) before it ever feeds a run.
- **Semantic weakness-score matching (§14.10).** `runner._weakness_score` matched by normalized
  STRING — counting a weakness the miner merely re-worded ("no comparative metrics" → "no systematic
  ranking") as *solved*, inflating the score on an UNCHANGED artifact. Now embedding-cosine (≥0.85):
  a prior weakness is solved only if NO open weakness is semantically similar; an open weakness is
  "new" only if it matches no prior; `no weakness ⇒ 1.0`; string fallback without an embedder.
  Verified on unchanged v26→v27: 0.44 (string) → 0.20 (semantic). (Now retired from the main score
  path per §6.1, but the matching logic informs dedup.)
- **Artifact preamble sanitizer** (`runner._strip_preamble`). A reducer that prepended commentary
  ("The artifact is complete… Weaknesses addressed: ✅… Remaining concern:") poisoned `artifact.md`;
  the grow-only ratchet then LOCKED it into the seed forever (a clean-up that shortens the doc reads
  as a regression). Strip everything before the first `#` heading at every artifact boundary (seed
  copy — resets `_seed_len` to the clean baseline — reducer read, write-back). That commentary
  belongs in the chat's `_unresolved_block`, never the doc. Self-heals a poisoned seed on next load.
  The §4.5 / §3.2 reducer prompt also forbids any preamble/status/checklist — emit only the
  document, starting at its first heading.
- **Date awareness** (`runner._today_note`). Agents were date-blind and flagged current-year sources
  as "future-dated" credibility problems. Inject today's date into hub/worker/reducer prompts (a
  per-run constant — a tool call would be a wasted round-trip).
- **Section-scoped `read_artifact`** (`studio/tools.py`). The tool returned the full ~38 K artifact
  on EVERY call; agents called it 26+× per phase → ~1 M input tokens. It now returns a cheap SECTION
  INDEX (`[{section, hash, chars}]`) with no args and ONE section's body+hash with `section='##
  Heading'` (deterministic `_split_sections`, 0 LLM). Per-section hashes let an agent re-read a
  section only when it changed; the agent self-dedups via hashes. Measured: index ≈ 1398 chars vs a
  38121-char dump (27×). `web_fetch` dedup was *rejected by design* — its cache already serves each
  agent the content it needs; stubbing a repeat would starve a cross-agent reader.
- **Anti-regression write guard (backstop, §11.8).** A write that would make `len(artifact) <
  len(seed)` is rejected (both write paths). With the additive contract in place this should never
  trigger; it exists to catch any path that bypasses the contract.
- **Duplicate-phase bug was goal injection, not the planner (§14.5).** A goal whose `end_state`
  overlapped the requirement produced duplicate phases (the runner prepended `Goal: {end_state}`;
  when that ≈ the task, the planner split the doubled text on "and"). Proven deterministically:
  `plan(goal+requirement)` → 5 dup phases vs `plan(requirement)` → 3 clean. **Fix:** the planner
  reads `_plan_requirement = base + deliverable-template` (no goal); the goal still steers via the
  worker goal + gate + verification. Belt-and-suspenders kept: content-dedup in `_plan_from_epics`,
  a path-agnostic `_dedupe_plan_steps` at the choke point, a "each phase must be DISTINCT" prompt
  line. **Lesson:** reproduce the exact input the failing run *constructs* — the cause was an input
  transform upstream of every layer first patched.

---

## 8. Observability — The Role I/O Contract on Disk

The structured I/O log IS the role contract made observable. The old code wrote ONE record per step
and INLINED the full prompt + output (the reducer record carried the whole 68 KB doc → 144 KB
records). New rule: **artifacts live on disk; I/O records carry only a name/path + small structured
fields.**

### 8.1 `agent_io.jsonl` — role-tagged records (built — §4c)

Each phase appends its INPUT (the full prompt the agent saw) and OUTPUT to `agent_io.jsonl` in the
session workspace — the fastest way to see goal/intent stacking, an upstream fold, recalled poison,
or a worker that refused vs produced. Best-effort; never breaks the run. Records are role-tagged:

```jsonc
// AGENT — goal-blind executor
{ "role": "agent", "step": "...", "topology": "star", "agent_id": "...",
  "input":  { "requirement": "<assigned job text>", "target_doc": "<path>" },
  "output": { "artifacts": ["<path>", ...] }, "tokens": 0 }

// HUB — goal-aware planner (input shape == agent's)
{ "role": "hub", "step": "...", "topology": "star",
  "input":  { "requirement": "<goal+template>", "target_doc": "<path>" },
  "output": { "assignments": [ { "agent_id": "...", "job": {...} } ] }, "tokens": 0 }

// REDUCER — goal-aware consolidator/verifier (a run-summary record carries new_weaknesses + score)
{ "role": "reducer", "step": "...",
  "output": { "new_weaknesses": [...], "score": 0.0, "handoff_artifacts": ["<path>", ...] },
  "tokens": 0 }
```

Rules: NO artifact body in any `input`/`output` — only `target_doc` / `artifacts` /
`handoff_artifacts` PATHS. Bodies are written to disk (`io/<step>.{in,out}.md`); records reference
them. `agent.output.artifacts` are the VERIFIABLE units the reducer checks against the hub's
`assignments` (ties G3 verification, §4.7, to concrete files). Keeps records small + greppable, and
makes the role contract auditable offline.

True per-spoke records are surfaced via the new `StepRun.agent_io` trail emitted by `run_plan`.

> **Partial (honest status):** disk-backed bodies + `role` tagging + the hub/reducer schemas exist
> at the **current step granularity**. True ONE-RECORD-PER-SPOKE (one per fan-out agent) is at step
> granularity — per-spoke I/O exists via `StepRun.agent_io`, but a single record per spoke is a
> remaining fan-out change sequenced with the topology-driver work.

### 8.2 DAG sync (frontend, §14.6)

(1) `read_artifact x26` no longer balloons tokens. (2) Agent count is emitted at `phase_start`
(planned = sizing cap + 1) so the DAG shows agents RUNNING up front instead of a default-3 guess that
only corrected at `phase_done`. (3) A run-status badge (`running` / `finalizing — verify · score ·
improve`) stays until the terminal `done` event, so the diagram never reads complete while
post-phase work runs. (4) DAG legend pills derive from the live `s.phases` (same source as the
diagram nodes), not the durable GraphStore's status (which drifted). (5) A running node uses a SOLID
glowing border (dashed read as inactive); late-mounted spoke nodes reveal-animate. Plus the
topology-chip tooltip (`TopologyStep.rationale`, §3.5) and topology-badge overlap CSS (`flex-wrap`).

---

## 9. Persistence — `task_runs.db`

A single SQLite file (`backend/tmp/task_runs.db`) is the entire durable cross-session state. One row
per *run* of a *task*.

```sql
CREATE TABLE task_runs (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  task_hash             TEXT    NOT NULL,             -- sha256(requirement.strip().lower())[:12]
  session_id            TEXT    NOT NULL,
  version               INTEGER NOT NULL,             -- 1,2,3… per task_hash (epoch lineage)
  score                 REAL    NOT NULL,             -- adjusted rubric score (§6.4)
  weaknesses_json       TEXT    NOT NULL DEFAULT '[]',-- ["[## Section] gap", …] section-tagged
  artifact_path         TEXT    NOT NULL DEFAULT '',
  requirement           TEXT    NOT NULL DEFAULT '',
  created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
  result_text           TEXT    NOT NULL DEFAULT '',  -- deliverable shown in chat (preamble-stripped)
  requirement_embedding BLOB,                         -- R10 cross-task similarity (§4.11)
  config_json           TEXT                          -- hill-climb config snapshot (§14.4)
);
```

Design rules:
- **`task_hash` is the lineage key.** The SAME requirement (case/space-normalized) shares a hash
  across sessions, so a later run finds the prior artifact + weaknesses and improves them — that IS
  hill-climb. Change the wording → new hash → fresh lineage.
- **`version` is monotonic per `task_hash`** (`next_version` = max+1).
- **Seed = LATEST-with-content, not best-score** (`latest_with_content`, with the DB `result_text`
  fallback, §7.4). Self-eval scores were noisy; the latest run carries the most accumulated work. The
  artifact is sanitized (`_strip_preamble` + `normalize_artifact`) on seed so inherited corruption
  can't propagate.
- **`requirement_embedding`** powers R10 (no-op without an embedder).
- **`config_json`** (`record()` snapshots the hill-climb config; `latest_config(task_hash)` returns
  the most recent). On a new `auto_improve` run with no explicit session config, the runner seeds
  the config from `latest_config` — so a requirement remembers its own epoch budget across sessions
  and **survives a backend restart**, keyed by the same `task_hash` lineage. Captured at the same
  point as `_base_requirement` so attaching a goal/template never forks the identity (D1).
- Store is `TaskRunStore` (`studio/task_runs.py`) — pure SQLite + numpy cosine, no ORM. Methods:
  `record`, `all_runs`, `latest_with_content`, `repeat_failures`, `similar_runs`,
  `accumulated_weaknesses`, `next_version`, `latest_config`.

---

## 10. The SSE Run API

The GUI is one client; the same three calls drive a run from any script. All against the backend on
`:8770` (the headless E2E port; SPEC may differ).

1. **`POST /session`** → `{session_id}`. Body:
   ```json
   {"llm": {"profile": "haiku"}, "embed": {}, "mode": "llm",
    "budget": {"ceiling": null}, "tools_enabled": true,
    "loop_config": {"auto_improve": true, "max_agents": 5,
                    "min_tasks_per_agent": 3, "max_tasks_per_agent": 5}}
   ```
   `mode`: `"llm"` (epic planner) or `"auto"` (deterministic). `tools_enabled` gates
   web_search/web_fetch — **off ⇒ the loop fabricates** (no real sources).
2. **`POST /session/{id}/hill-climb`** (optional) — `{auto_improve, max_epochs, min_improvement,
   score_metric, max_agents, min_tasks_per_agent, max_tasks_per_agent}`. The Agent-Sizing sliders
   ride here and are synced into `loop_config` (the runner reads sizing from `loop_config`).
3. **`POST /session/{id}/rubric`** (optional) — `{weights, template}`; `GET /rubric/defaults` seeds
   the panel (§12).
4. **`GET /run/{id}?requirement=<urlencoded>`** → an **SSE stream** (ordered contract, SPEC §4:
   `session → plan → topology → graph → (per phase: phase_start, router, token…, phase_done) →
   verify → loopdoctor → hill_climb → done`). Optional `history` = JSON `[{role,content}]` for
   multi-turn.

> **GOTCHA — drain the stream fully.** The runner records the row *as the SSE stream is consumed*. A
> client that disconnects early (e.g. `urllib` raising `IncompleteRead`, or closing on the first
> `done`) cancels the server-side `StreamingResponse` generator → **the run may never record to
> `task_runs.db`**. A browser `EventSource` holds the connection open and is fine; a script MUST read
> every line until the server closes the stream.

---

## 11. Shared-Library Boundary (`agentkit`)

Studio is the orchestration shell; `agentkit` is the reusable engine. Shared, reusable mechanisms
live in `agentkit/` (pure, no studio import); client/embedder injected via Protocol (mirroring
`reduce_patches(..., llm_refine_fn=)`).

| Component | agentkit target |
|---|---|
| Dynamic agent sizing | `agentkit.topology.sizing` |
| TaskRecord + TaskLedger | `agentkit.orchestrator.ledger` |
| DocPatch + reduce_patches + write_artifact + cleanup_orphaned_tmp | `agentkit.artifacts.patcher` |
| DeliverableStore (resolve/latest_with_content) | `agentkit.artifacts.store` |
| split_sections / section_hash / accept_rewrite | `agentkit.artifacts.sections` |
| Finding dedup (F1) | `agentkit.artifacts.dedup` |
| Metric acquisition (F4a) | `agentkit.artifacts.metrics` |
| Ranking table (F4b) | `agentkit.artifacts.ranking` |
| Finding dataclass + Embedder Protocol | `agentkit.artifacts.types` |
| TaskRunStore / score_result / mine_weaknesses / similar_runs / accumulated_weaknesses | `agentkit.improvement.*` |
| DecompositionStrategy + run_plan driver + select_topology | `agentkit.topology.{dynamic,core}` |
| self_preference / optimize_text / check_goal / LoopChain | `agentkit.evolve` / `agentkit.loop` |
| InFlightRegistry (URL dedup) | `agentkit.tools.fetch_cache` |
| Context compaction; OpenAIEmbedder retry | `agentkit.context` / `agentkit.backends.openai_compat` (already there) |

`Finding(url, title, quote, why, popularity, patch_target, quote_verified, grounded)` — `quote` is
verbatim copy-paste evidence (already grounded). `Embedder` is a `Protocol` with `embed(texts) ->
list[list[float]]`. Studio's `_research_findings_to_patches` parses via `parse_findings(text) ->
list[Finding]` → F1 dedup → patches.

> **Follow-up (D5, tracked):** `runner.py` (~2.1 k lines) should be decomposed (record/scoring,
> seed/auto-improve, reduce/writeback → modules). D2 logic was deliberately placed in
> `studio/epoch_gate.py` to avoid growing it.

---

## 12. UI — Loop Config, Rubric Panel, Chat Window

### 12.1 Loop Config panel

```
[ Loop Settings ]
  Deliverable
    Path: [____________]  Browse...
    ○ Use latest prior artifact (hill-climb)   ← default
    ○ Create new artifact each run
  Agent Sizing
    Min tasks per agent: [3] slider 1–10
    Max tasks per agent: [5] slider 1–10
```
`deliverable_path` empty + "Use latest prior" → resolution falls through to hill-climb (§4.1
priority 2). Empty + "Create new" → always auto-create (priority 3). `LoopConfig(deliverable_path,
auto_improve, min_tasks_per_agent, max_tasks_per_agent).sizing() -> SizingConfig`.

### 12.2 Rubric panel (built — §14.2)

A "rubric" tab in the Loop Config dialog (`LoopConfigPanel.tsx`): seeds from `GET /rubric/defaults`,
renders a slider per criterion and an editable template section list, POSTs `{weights, template}`
to `POST /session/{id}/rubric`. Stores locally via `runStore.configuredRubric` (preserved across
`beginRun`). **No hardcoded criterion keys** — the panel iterates whatever the defaults endpoint
returns (`Object.entries(rubricWeights)`), so a new backend criterion needs zero frontend change.
Weights normalized server-side (`resolve_weights`). `Session.rubric_config = {"weights": {...},
"template": [...]}`. The runner gate reads it → `make_rubric_preference(...)`, falling back to
defaults when unset.

### 12.3 Chat window (replaces task textarea)

The main task `<textarea>` is replaced by `<ChatPanel>` (`frontend/src/components/hud/ChatPanel.tsx`):
multi-turn thread (`role: "user" | "assistant"`); on submit, prior completed turns are forwarded as
`history` (`openRunStream(sessionId, req, callbacks, history)` appends `&history=<json>` to the SSE
URL). Backend flattening: `studio/session.py::flatten_chat_to_requirement(messages)` concatenates
messages into structured planner context; wired on `GET /run/{session_id}` which accepts an optional
JSON-encoded `history` query param. Malformed `history` falls back to the bare `requirement` (the
single-textarea contract). All prior refinements are visible to the planner.

---

## 13. Memory Hygiene

**Episodic memory poisoned the prompt with the agent's own refusals (§14.6).** The MEMORY panel kept
recalling worker refusals ("I appreciate you sharing this, but I need to clarify… these appear to be
duplicate statements of intent…") at score ~0.84. Cause: `MemoryTracker` wrote EVERY phase output to
a **shared** store (`workspace_root().parent/shared_memory.db`, one DB across all runs despite the
"per-run" docstring) with no filter, and `recall()` injected the top-5 by similarity into every later
phase — so a refusal stored once resurfaced at high similarity (it matches the very task that
provoked it) and primed the next worker to echo it: a persistent cross-run poison loop. Measured:
`shared_memory.db` held 365 memories, 44 refusals (~12 %). **Fix:**
`studio.panels.memory._is_low_value_memory` drops refusal / clarification / failure-narration
openers in `record()`; the 44 poison rows were purged (365 → 321). **Lesson:** a memory that cannot
distinguish a *finding* from a *refusal* will, over runs, teach every agent to refuse — curating what
ENTERS memory matters as much as curating the report; the recall signal is only as clean as the
write filter.

Local services the backend assumes: oMLX `:8000` (chat + BGE-M3 embeddings; `OpenAIEmbedder` retries
3× with backoff so the memory panel degrades gracefully when oMLX is down), SearXNG `:8080` (primary
search; precedence SearXNG → Tavily → DDG, falling through on empty OR down). Web-search wiring is
REQUIRED for research loops to cite real sources — missing toolkit ⇒ the loop silently runs with no
web search and fabricates citations, capping the score ~0.30 (the #1 cause of a report that "looks
fine" but can't score).

---

## 14. Open Backlog (Deferred / Partial)

Honest status of what is NOT fully built. **A 2026-06-29 code audit re-checked the whole backlog
against the real source and de-listed five items that were already shipped** (the entries had been
carried over verbatim from the original PLAN wishlists and were stale).

**Resolved — verified as built in the 2026-06-29 audit (no longer backlog):**
- **Fabricated-URL guard / repair.** `task_runs.neutralize_unverified_urls` exists and is WIRED
  (`runner.py`, fail-open, after `_verified_urls`, before scoring/mining); the reducer prevention
  instruction is present in the reduce prompt ("CITE ONLY a URL you fetched; never invent or alter
  one"). Normalization (`_normalize_url`: `http`↔`https`, trailing slash, tracking params) is applied
  before matching in both `verified_urls_in_cache` and the neutralizer.
- **"Continue run" preserves lineage.** `task_runs.base_identity` strips the GUI continuation wrapper
  (`[CURRENT REQUEST]:` / `Original task:`) and is used at both seed-lookup and record time, so a
  continued run keeps the stable BASE `task_hash` (no cold-start fork).
- **`converged` status label.** `planning._epoch_status` already computes the label from the per-run
  `epoch_idx`, NOT the cumulative `_version` (docstring: "Both labels are per-run, never cumulative").
  The earlier "set whenever `_version >= max_epochs`" claim was wrong.
- **Per-spoke I/O records.** `run_plan` surfaces a per-invocation `StepRun.agent_io` trail (one record
  per worker/stage + the fan-in), and the runner writes per-spoke `agent_io.jsonl` rows from it — so
  the role I/O contract is at spoke granularity, not just step granularity.

**Partial (built at coarser granularity than the ideal target):**
- **MESH position-aware arbitration (§3.1).** Approximated via two debate-rounds (each peer reads every
  other peer's round-1 over the `MessageBus`) + the section reducer's bounded additive merge; a true
  position-aware *arbitration* fold (weigh competing stances, not just merge) is not yet implemented.
- **Heading-glued-to-plain-prose un-glue (§5.1).** `_split_glued_headings` handles `## A### B`
  (marker present); a heading glued directly to prose with no marker boundary is not deterministically
  splittable — tracked, not fixed.

**Deferred (designed, not wired) — the real remaining work:**
- **`optimize_text` owning the epoch loop** (§7.3 reuse). `agentkit.evolve.optimize_text` EXISTS but
  Studio still hand-rolls the per-epoch advance; the autonomous heartbeat is not yet wired (a risky
  refactor of a working loop — gate behind regression tests).
- **LLM-judge scoring upgrade (§6.8).** DeepEval `GEval` (coherence/analysis/task-fit) + RAGAS
  faithfulness/groundedness against `.web_cache.json`, replacing the keyword-counting `verification`
  / `evidence_depth`. `adjusted_score` is the bridge. **`deepeval` + `ragas` are not installed** —
  needs new deps + an LLM-judge integration.
- **Workers must actually fetch (deepest root).** Weak local models report "found sources, about to
  fetch" and return status only — the reducer then REFUSES (the 685-byte stub) or FABRICATES. Harden
  the oMLX fenced-tool-call path further (extends §5.9) so a worker's `web_fetch` actually runs.
- **Per-task cache scoping.** Normalization-before-match is DONE (see Resolved). What remains: the
  `.web_cache.json` is still SHARED/polluted across runs (a citation can match a different task's
  fetch). Scope the cache per-task (or stamp entries with the fetching run).

**Cut (verification killed it):**
- **F3 anti-truncation / section repair** — W3 ("truncated mid-sentence") was a miner hallucination;
  the v33 document has no real truncation (the quote completes at offset 8015). Parked; revisit ONLY
  on a verified mid-sentence cut in the *document*.

**Process rule (N6).** A bug was mis-diagnosed 3× because "unit tests pass + function works in
isolation" was treated as "the deployed system does it." For any fix to runtime behavior, verify
against the RUNNING backend — check the serving process start time vs the change (`ps -o lstart` on
the `:8770` PID), restart if stale, confirm on a real re-run, not just pytest + an isolated function
call.

---

## 15. Decision Log & Changelog

Newest-relevant grouped. The architectural spec above is the authority; this is the chronological
trail of *why*. (§-numbers in parentheses reference the original `DESIGN.md` §14 entries this merges.)

### 15.1 This-session modifications (2026-06-29) — section-ownership + topology uniformity

- **N4 fence-aware headings** — `rubric.mask_fenced_code`; heading/structure scans
  (`score_breakdown`, `sections_present`, `artifact_text._detect_gaps`) ignore `#` inside code
  fences (§6.3).
- **N1 outline integrity** — `_merge_missing_sections` (concept-coverage vs body; don't bolt a
  template onto a doc with its own outline), `reconcile_outline`, `dedupe_sections` (collapse
  duplicate populated headings — fixes gemma-echo + grow-only 8–10× stacking),
  `_split_glued_headings` / `normalize_artifact`. Wired at seed, after every phase writeback, and at
  finalization (§5.1).
- **N2/N3 eval reliability** — `task_runs.refute_false_weaknesses` + `_section_ends_cleanly` drop
  phantom "missing example code / no conclusion / truncated summary" weaknesses and per-section
  truncation claims when the doc demonstrably has them (§6.6).
- **N5 mermaid repair** — `artifact_text._repair_lints` un-gated from `use_llm` + instrumented;
  verified live (2 malformed edges → 0) (§5.5).
- **G2 deterministic assembly** — removed BOTH whole-doc LLM echoes (Path-B "ADDITIVE MERGER"
  `_art_ctx`, and Phase-2 `_refine_fn` now gated to small docs only); assembly is `reduce_patches`,
  section-keyed (§2.1, §4.6).
- **G3 reduce-time verification** — `planning.verify_assignment_coverage`; unmet → next-epoch
  weakness + gate event; reachable via a DETERMINISTIC assignment synthesized from the rubric template
  when the executor emits no `ASSIGNED` block (§4.7).
- **G4 new-section assignment** — hub emits "create section Z" jobs; `planning._parse_assigned`
  accepts `{sections, create}` (§4.8).
- **P2 goal-blind workers** — `prompts._build_executor_prompt` no longer injects the raw GOAL;
  per-step `TASK:` injection gated off for reducer-backed worker phases (§2.1).
- **§4b topology contract** — `agentkit/topology/dynamic.py` `DecompositionStrategy` protocol +
  Single/Star/Mesh/Pipeline/Map strategies behind one `run_plan` driver; the injected `_REDUCER`
  routes through every topology (§3.2).
- **E1** — the blanket force-STAR override in the runner is DELETED; selection honored for all
  topologies under hill-climb (safe because every topology has the reducer contract) (§3.4).
- **E2** — `dynamic.assign_topologies_with_choices` surfaces rationale + `questions_fired`; runner
  threads into `TopologyEvent`; frontend topology-chip tooltip (§3.5).
- **E3** — selection-quality tests (classify + select_topology mapping + infer_spec routing).
- **E4** — epic planner emits a per-epic topology INTENT
  (`prompts._build_planner_cot_prompt` / `planning._plan_from_epics`), reconciled in the runner.
- **§4c observability** — `agent_io.jsonl` role-tagged (hub/agent/reducer + run-summary reducer
  record carrying `new_weaknesses` + score); bodies to `io/<step>.{in,out}.md` (records carry PATHS);
  per-spoke trail via `StepRun.agent_io` surfaced by `run_plan` (§8.1).
- **§4d moving-window** — `artifact_text._synthesize_analysis` → `_synthesize_windowed` /
  `_synthesize_block` (per-section rewrite, no whole-doc echo) + two-stage dedup on reassembly
  (`_dedup_paragraphs`, URL-bearing paragraphs never dropped) (§5.3).
- **Readability refine (NEW)** — `artifact_text._refine_readability`; final instructor-tone pass,
  windowed, final-epoch only, citation-sacred, reject-on-URL-drop-or-shrink; directive
  (`_DIRECTIVE_READABILITY`) chosen by a gemma A/B/C/D eval (citation-retention + LLM readability
  judge). Supersedes the plain analysis pass (§5.4).
- **Seed-lineage hygiene** — root cause documented: `latest_with_content` seeds from the LATEST
  lineage artifact, so a bloated/cancelled prior run poisons the next seed; mitigated by the seed-time
  `normalize_artifact` (§7.9).
- **Frontend** — `TopologyStep.rationale` tooltip + topology-badge overlap CSS (`flex-wrap`).
- **Test infra** — `pyproject.toml` `filterwarnings` silences the benign starlette `httpx` / FastAPI
  `on_event` deprecations.

### 15.2 Score decoupled from weaknesses + agent-I/O logging (§14.7, 2026-06-29)
`rubric.adjusted_score(base, weaknesses)`: any open weakness ⇒ score < 1.0 (ceiling 0.92, graduated
capped penalty). Recorded score is `adjusted_score(rubric_base, _weaknesses)`. The 1.0 / 8-weakness
case now records 0.62. `agent_io.jsonl` diagnostics added. Root causes: (A) a "Continue run"
conversational requirement rotated `task_hash` → cold start, repairs never fired; (B) the rubric is a
quantity checklist blind to correctness (§6.4).

### 15.3 `max_epochs` is a per-run budget, not a cumulative-version ceiling (§14.8, 2026-06-29)
`run()` breaks only on a genuine plateau or cancel; `converged` (`_version >= max_epochs` over
all-time versions) no longer gates the loop (§7.2).

### 15.4 The keep/discard gate was keyed on a file that usually doesn't exist (§14.6, 2026-06-28)
Gate on the durable DB `result_text`, not the ephemeral `artifact.md` (§7.4). Related: edit contract
gated on `_artifact_copied` not `_prior`; `_merge_missing_sections` creates absent template sections;
`artifact_lint` + reducer repair exception for malformed mermaid / code fences (§5.5); memory poison
filter `_is_low_value_memory` (§13).

### 15.5 Local-model format tolerance + topic-agnostic skeleton (§14.5, 2026-06-28)
oMLX fenced-JSON tool calls + JSON-wrapped `RESEARCH_FINDING` parsed (qwen `fetched 0 → 1`, run
0.236 → 0.80) (§5.9). `_build_skeleton` emits a fixed topic-agnostic ToC (§4.9). Duplicate-phase bug
was goal injection, not the planner — planner reads base + template, no goal (§7.9).
`gemma-4-26B-A4B-it-heretic-4bit` registered in `PROFILES` via `shared_bridge.py`.

### 15.6 Epoch heartbeat — one Run auto-iterates (§14.4, 2026-06-28)
`run()` wraps `_run_inner` in an epoch loop; `_run_inner` returns `EpochResult`; per-epoch step-id
namespacing (`e2:s3`); `config_json` persistence survives restart (§7.1, §9).

### 15.7 Substantiation levers + non-additive structure (§14.3, 2026-06-28)
L1/L2/L3 grounding levers (§5.8); F4 honest ranking synthesizer (`agentkit.artifacts.ranking`, §5.7);
F2 per-section ratchet (`agentkit.artifacts.sections.accept_rewrite`, §5.2). Verified finding: the
binding constraint is **structure**, not grounding — additive levers can't move it.

### 15.8 Research-report rubric is the recorded score (§14.2, 2026-06-28)
`rubric_score` (deterministic, full-text, §6.2) is the recorded score / convergence signal /
template-save gate / keep/discard judge — one metric end to end. `_weakness_score` (solved/total)
retired (it punished thoroughness, rewarded an empty doc). Concept-aware structure matching;
moving-window miner; semantic weakness dedup; weaknesses surfaced below the report, never concatenated
in. Rubric panel + template-steers-generation built (§12.2, §6.7).

### 15.9 Loop-engineering closure decision log (§14.1, 2026-06-28)
D1 `task_hash` = base requirement; D2 `epoch_gate.accept_epoch` keep/discard gate; D3 hardened
`self_preference` parsing; D4 LLM pairwise preference unreliable → structured rubric gate; D5
`runner.py` decomposition tracked (§7.3, §11).

### 15.10 Grounded accumulation & regression-free improvement (§11, foundational)
The §1 invariants 6–8 and the worker/reducer/gap-routing contracts (§4.6, §4.7, §4.9, §4.10, §7.6,
§7.7). Origin: a search outage produced thin "search unavailable" narration that the full-rewrite
reducer synthesized into the deliverable, overwriting a good 28 KB grounded report; `latest_with_
content` then seeded the degraded doc forward — a death spiral. Root cause: the reducer regenerated
the whole document in one LLM call (bounded by output tokens, biased toward summarization, trends
shorter than its input). The §11 design makes regression **impossible by construction**, not merely
guarded.

### 15.11 Earlier foundational decisions (§10 table)
- Deliverable seed: **latest with content**, not best-score — score is noisy (LLM self-eval); latest
  has most accumulated work.
- Task tracking: **both** ID + description (`TaskRecord`) — ID for dedup, description for reasoning.
- Document modification: **patch-based, atomic rename** — crash-safe, no empty-file risk, incremental.
- Agent count: **derived from task count** — hard-coded n is arbitrary; task-driven sizing
  self-calibrates.
- Weakness generalization: **hub CoT step (LLM)** — avoids brittle code heuristics.
- Requirement input: **chat (multi-turn)** — captures refinements; full context to the planner.
- Shared-library boundary: **agentkit for reusable primitives**; studio = orchestration shell.
- Context history scope (R10): exact task **+ similar tasks** (cosine over requirement embeddings) — a
  brand-new requirement has no exact history; similar prior tasks carry transferable lessons.
- Weakness list to agent (R10): dedup (exact) **+ consolidate** (semantic near-dup merge).
- Deliverable seed embedding: lazy backfill of legacy rows — avoids a blocking migration.
- Phase-2 refine acceptance: length guard (≥80% of merged) + best-effort — a flaky refine never
  corrupts a clean structural merge.
- Worker section non-overlap (R2): prompt-enforced **+ code-validated** (`_dedupe_assignment`,
  first-claim-wins, gate check).
- Ledger seeding (R1): seed `all_tasks` from `plan_obj.steps` up front — else `remaining()` is
  structurally always empty.
- Reducer role: merger + editorial + gap-flagger, **never a generator** — a reducer that re-emits the
  whole doc truncates it away.
- Worker on no result: **silent no-op**, never failure-prose — prose about being blocked becomes
  "content" the reducer synthesizes into a thin doc (the root of the regression).
- Search-error vs found-nothing: distinct — found-nothing = no-op; all-error = halt + notice.
- New/missing content: reducer flags a **gap**; a worker (with search) fills it — the reducer has no
  search tool, so anything it invents is ungrounded.
- Create == improve: same additive pipeline; create starts from a skeleton, improve from the prior
  doc — removes the special-cased "one LLM writes the whole report" path that seeded the spiral.

---

### Lessons index (the most reusable rationale)

- A closed loop guarded by a volatile precondition is an open loop most of the time — gate on the
  durable record, not the ephemeral artifact (§7.4).
- A score that can't see what the critic sees will always disagree with it — the metric must be a
  function of the same evidence the weaknesses are mined from (§6.4).
- A counter that means "how many times ever" can't double as "how many times now" (§7.2).
- A loop only ever fixes what its weakness signal can name; an additive optimizer needs an explicit,
  bounded licence to repair, or defects become immortal (§5.5).
- Reproduce the exact input the failing run *constructs* — the cause may be an input transform
  upstream of every layer you first patch (§7.9 duplicate-phase).
- Curating what ENTERS memory matters as much as curating the report — the recall signal is only as
  clean as the write filter (§13).
- A capability failure can masquerade as a principled conclusion — 0 sources ⇒ suspect the worker
  tool-call FORMAT before blaming the input or the loop (§5.9).
- Asking an LLM "which is better overall?" hedges to TIE even on a strong model with a rubric — put a
  deterministic check in wherever one exists; use the LLM only per-criterion (§7.3 D4).
- No whole-doc-in/whole-doc-out: a model truncates a long echo (68 KB → 36 KB) — window → process →
  dedup → reassemble (§5).
- Make the eval trustworthy BEFORE building structural features against it — a hallucinated weakness
  directly suppresses the score (§6.6).
- The binding constraint is often structure, not grounding — additive levers cannot move a structural
  ceiling (§5.8).
- **Verify the backlog against the code before building it** — a 2026-06-29 audit found 5 of 9 "open"
  items were already shipped (§15.12). A doc's wishlist drifts from reality; re-grep before you start.

### 15.12 Backlog re-audit against source (2026-06-29)

Before starting the remaining backlog, every §14 item was re-checked against the real code. **Five were
already implemented** and were promoted out of backlog (§14 "Resolved"): the fabricated-URL guard
(`neutralize_unverified_urls` wired + reducer prevention instruction), "Continue run" lineage
(`base_identity` strips the wrapper → stable `task_hash`), the `converged` label (`_epoch_status` uses
the per-run `epoch_idx`), URL normalization-before-match (`_normalize_url`), and per-spoke I/O records
(`StepRun.agent_io` surfaced by `run_plan`, written per spoke). The genuinely-remaining work is MESH
arbitration, `optimize_text` reuse, GEval/RAGAS judging (deps not installed), local-model fetch
reliability, and per-task cache scoping. **Lesson:** doc status fields decay; trust a code grep over a
prose "Deferred."

### 5.4a–c / 15.13 Refinement persistence & the quote-wall (2026-06-29)

Two coupled decisions after the live-run review (full detail in §5.4a–c):
1. **Post-gate finalization (built).** Refinement (normalize/dedup, mermaid repair, readability) now
   runs AFTER the keep/discard gate on the kept winner, so a revert can no longer clobber it — the
   root cause of the served-broken-mermaid (repair ran, the gate's revert to the raw seed overwrote
   it).
2. **Cleaned artifact is the seed (design).** `artifact.md` = readable+deduped (seed + scored);
   `result.md` = grounded-full (archive). Because the seed drives the next round, the cleaned version
   MUST be the seed — else the dedup is redone every epoch and wasted. General rule: **any cleanup
   must land on the seed.**
3. **Reducer-side finding dedup (planned, §5.4c).** Readability cannot collapse the URL-dense
   quote-wall (the citation guard rejects any summarization that drops a URL), so thin it at the
   source: same-URL merge + against-doc dedup + per-section cap + scaffolding strip in the reducer.
   **Lesson:** fix size at the source (deterministic, where a drop isn't a regression), polish at the
   render; a quantity-biased gate will always punish a post-hoc shrink.
