# AgentKit Studio — Architecture Design

Design authority: `SPEC.md` (UI/API contract).
This document covers the **internal execution architecture** — how phases coordinate,
how the deliverable evolves, how agents are sized, and what moves to shared libraries.

---

## 1. Core Invariants

1. **One deliverable per task.** All phases read from and write patches to a single
   `artifact.md`. No phase reconstructs the document from scratch if a prior version exists.
2. **Completed work is never reassigned.** A `TaskLedger` carries done/remaining tasks
   across phase boundaries; hubs assign only from the remaining set.
3. **Crash-safe writes.** The deliverable is modified via atomic patch-apply
   (`write .tmp → rename`). A crash never leaves an empty file.
4. **Dynamic agent sizing.** Agent count is derived from task count, never hard-coded.
5. **CoT prompts everywhere.** Every hub prompt uses explicit numbered reasoning steps.

---

## 2. Deliverable Lifecycle

### 2.1 Path Resolution (priority order)

```
1. Loop config panel   user sets explicit path ("improve THIS file")
                       overrides everything
        ↓ if not set
2. Hill-climb history  latest run with non-empty artifact.md for this task_hash
                       (NOT best by score — latest accumulates the most work;
                        score is noisy because LLM self-evaluation is unreliable)
        ↓ if no prior
3. Auto-create         workspace/{session_id}/artifact.md
                       created by first phase; path injected into context
```

```python
# studio/workspace.py
def resolve_deliverable(session, workspace, store) -> Path:
    # 1. explicit from loop config
    cfg = getattr(session, "loop_config", None) or {}
    if cfg.get("deliverable_path"):
        return Path(cfg["deliverable_path"])
    # 2. latest prior run with content
    if cfg.get("auto_improve", True):
        prior = store.latest_with_content(task_hash(session.requirement))
        if prior:
            return _copy_prior_artifact(prior, workspace)
    # 3. auto path (not yet written)
    return workspace.root / "artifact.md"
```

`TaskRunStore.latest_with_content()` — selects most recent run where
`workspace/{session_id}/artifact.md` exists and `stat().st_size > 0`:

```python
def latest_with_content(self, task_hash: str) -> TaskRun | None:
    for run in sorted(self.all_runs(task_hash),
                      key=lambda r: r.created_at, reverse=True):
        art = self._ws_root / run.session_id / "artifact.md"
        if art.exists() and art.stat().st_size > 0:
            return run
    return None
```

### 2.2 Patch-Based Modification

**Workers are stateless suggesters — they never write to disk.**
The hub assigns each worker a non-overlapping set of document sections so patches
commute by default.  Each worker emits a `PATCHES:` JSON block targeting only its
assigned sections.  A dedicated **Reducer** step (the final step of each phase)
collects all patches, resolves structural conflicts, then does a full-document
refinement pass before one atomic write.

```
Hub assigns workers by section (non-overlapping anchors):
  Worker 1 → "## Introduction", "## Background"
  Worker 2 → "## Results",      "## Analysis"
  Worker 3 → "## References",   "## Appendix"

Phase N
  ├── Worker 1  →  PATCHES for assigned sections (no file write)
  ├── Worker 2  →  PATCHES for assigned sections (no file write)
  ├── Worker 3  →  PATCHES for assigned sections (no file write)
  └── Reducer   →  collect → reduce_patches() → LLM refine pass → atomic write → artifact.md
```

**Section-assignment rule:** prefer `insert_after` / `append` ops over `replace` — additive
patches on distinct anchors are conflict-free by construction.  Reserve `replace` for cases
where a section must be wholly rewritten and no other worker touches that anchor.

#### DocPatch model (`agentkit.artifacts.DocPatch`)

```python
@dataclass
class DocPatch:
    op: Literal["replace", "insert_after", "insert_before", "append", "prepend", "delete"]
    anchor: str | None   # search string to locate position (None for append/prepend)
    content: str         # replacement or inserted text (empty string for delete)
    source: str = ""     # worker id — used for conflict reporting
```

#### Conflict types and resolution

| Conflict type | Example | Resolution |
|---|---|---|
| Same anchor, additive ops | Two workers `insert_after "## Sources"` | Concatenate both contents in task-assignment order |
| Same anchor, destructive | Worker A `replace "## Sources..."`, Worker B `insert_after "## Sources"` | Apply A first; re-check B's anchor in post-A text; if anchor still exists apply B, else append with conflict note |
| Anchor destroyed by prior patch | Worker A deletes text that Worker B uses as anchor | Orphaned patch: append content at end wrapped in `<!-- conflict: anchor not found -->` |
| Identical content | Two workers produce same insert | Deduplicate (skip second) |

#### Reducer algorithm (`agentkit.artifacts.reducer`)

```python
@dataclass
class ConflictNote:
    patch: DocPatch
    reason: str   # "anchor_destroyed" | "duplicate" | "ambiguous_anchor"

@dataclass
class ReduceResult:
    text: str
    conflicts: list[ConflictNote]   # empty = clean apply

def reduce_patches(
    current_text: str,
    patch_groups: list[list[DocPatch]],  # one list per worker, in assignment order
    llm_merge_fn=None,                   # optional: LLM call for per-conflict resolution
    llm_refine_fn=None,                  # optional: LLM call on final merged text (full-doc polish)
) -> ReduceResult:
    """
    Collect all patches, detect conflicts, resolve, then refine the merged document.

    Phase 1 — structural merge:
    1. Flatten patches preserving worker order (respects task assignment priority).
    2. For each patch: attempt apply on working_text.
       - Anchor found → apply, advance working_text.
       - Anchor missing → conflict: try llm_merge_fn if provided,
         else append with conflict marker.
    3. Deduplicate: skip patch if its content already exists verbatim in working_text.

    Phase 2 — document refinement:
    4. If llm_refine_fn provided: pass final merged text through it for a full-document
       polish (coherence, flow, gap-filling, conflict-marker cleanup).
    5. Return ReduceResult with refined text and conflict log.
    """
    working = current_text
    conflicts: list[ConflictNote] = []

    for patches in patch_groups:            # iterate workers in order
        for p in patches:
            if p.op in ("append", "prepend") or p.anchor is None:
                working = _apply_one(working, p)
                continue
            if p.anchor not in working:     # anchor destroyed by prior patch
                if llm_merge_fn:
                    working = llm_merge_fn(working, p)
                else:
                    marker = f"\n<!-- conflict({p.source}): anchor not found -->\n{p.content}"
                    working = working + marker
                    conflicts.append(ConflictNote(p, "anchor_destroyed"))
            elif p.op == "insert_after" and p.content in working:
                conflicts.append(ConflictNote(p, "duplicate"))  # skip
            else:
                working = _apply_one(working, p)

    return ReduceResult(text=working, conflicts=conflicts)
```

#### Atomic write (Reducer only)

```python
def write_artifact(path: Path, text: str) -> None:
    """Atomic write via tmp+rename. Only the Reducer calls this."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(path)   # POSIX rename is atomic
    # crash before rename → original intact; .tmp orphaned → cleaned on startup
    # crash after rename  → new content fully applied
```

#### Startup cleanup (orphan guard)

```python
def cleanup_orphaned_tmp(workspace_root: Path) -> None:
    for tmp in workspace_root.rglob("*.tmp"):
        tmp.unlink(missing_ok=True)
```

Call at server startup in `app.py`.

#### Crash-safety matrix

| Crash point | Result |
|---|---|
| Worker crash | No file touched; Reducer skips that worker's patches |
| Reducer crash before `tmp.write_text` | Original artifact unchanged |
| Reducer crash during `tmp.write_text` | `.tmp` partial; original untouched |
| Reducer crash between write and rename | Original intact; `.tmp` cleaned on next start |
| Reducer crash after rename | New content fully applied |

#### Worker output format

Workers emit a fenced JSON block in their response text (no file access needed):

````
PATCHES:
```json
[
  {"op": "replace",
   "anchor": "## Section Title\n- placeholder",
   "content": "## Section Title\n- [Source Title](https://source-url)"},
  {"op": "insert_after",
   "anchor": "## Another Section",
   "content": "\n### New Subsection\n<content derived from task>"},
  {"op": "append",
   "anchor": null,
   "content": "\n## Additional Section\n<content derived from task>"}
]
```
````

Runner extracts the JSON array after `PATCHES:` per worker output.
Reducer receives `list[list[DocPatch]]` (one list per worker, ordered by
task assignment so priority is deterministic).

#### Reducer prompt (CoT)

> **SUPERSEDED by §11.** The "Output the COMPLETE updated artifact — every section"
> reducer below is the source of the regression (single-call full-document
> regeneration trends shorter than its input). §11 replaces it with an **additive
> merger that never re-emits the whole doc**. The prompt below is retained for
> historical context; new work follows §11.3.

> **Implementation note:** in code the two phases are split. **Phase 1
> (structural merge)** is done *mechanically* by `reduce_patches` — no LLM. Only
> **Phase 2 (refinement)** is LLM-driven, via `_build_reducer_refine_prompt`
> (`studio/runner.py`), whose numbered-step CoT (Steps 1–8) covers coherence,
> gap-filling, `<!-- conflict -->` cleanup, dedup, consistency, and quality. It
> is passed as `llm_refine_fn` into `reduce_patches`; an output shorter than 80%
> of the merged text is rejected (keeps the clean merge). The conceptual
> single-prompt version below remains a useful description of the whole job.

```
You are the Reducer for this phase. All worker agents have completed their tasks.
Your job is two-phase: first merge all patches structurally, then refine the
full document as an editor.

Step 1 — Read the current deliverable at {artifact_path}.

Step 2 — Read each worker's PATCHES suggestions (provided below).
  Worker 1 patches: {worker_1_patches}
  Worker 2 patches: {worker_2_patches}
  ...

Step 3 — Detect conflicts.
  For each patch, check if its anchor exists in the current text.
  For same-anchor patches from different workers, decide:
    - Both additive (inserts)? Concatenate in worker-assignment order.
    - One replaces, one inserts same anchor? Apply replace first, then re-check insert.
    - Anchor already removed? Note as conflict, append content with conflict marker.

Step 4 — Produce the merged text.
  Apply all non-conflicting patches in worker-assignment order.
  For conflicts, append with a `<!-- conflict -->` marker for visibility.

Step 5 — Refine and review the merged document.
  Read the full merged text as a document editor. Check and fix:
    - Coherence: does the document flow logically section to section?
    - Gaps: missing transitions, incomplete sentences, orphaned headings?
    - Conflict markers: resolve any `<!-- conflict -->` tags left from Step 4.
    - Redundancy: deduplicate content that multiple workers inserted identically.
    - Consistency: uniform terminology, citation style, heading hierarchy.
    - Quality: tighten prose, correct factual inconsistencies, improve clarity.
  This is a full editorial pass — produce the best possible document, not just
  a mechanical merge.

Step 6 — Emit the final refined document as your output.
  The system will write it to {artifact_path} atomically.
```

### 2.3 Epic-Based Plan Structure

The planner outputs a two-level plan to prevent phases from being too granular.
Each **epic** maps to one phase. Each epic contains **branches** (the tasks
distributed to parallel workers within that phase).

```
Plan  (example structure — epics and branches derived from the actual goal)
├── Epic 1: <Phase A>         ← phase 1 (runs serially, depends on nothing)
│   ├── Branch 1a: <independent subtask>
│   ├── Branch 1b: <independent subtask>
│   └── Branch 1c: <independent subtask>
│
├── Epic 2: <Phase B>         ← phase 2 (depends on Epic 1)
│   ├── Branch 2a: <independent subtask>
│   ├── Branch 2b: <independent subtask>
│   └── Branch 2c: <independent subtask>
│
└── Epic 3: <Phase C>         ← phase 3 (depends on Epic 2)
    ├── Branch 3a: <independent subtask>
    ├── Branch 3b: <independent subtask>
    ├── Branch 3c: <independent subtask>
    └── Branch 3d: <independent subtask>
```

Mapping to existing structures:
- Epic → `Plan.step` (one step per epic, with `depends_on` for sequencing)
- Branches → `TaskRecord` entries in the `TaskLedger` for that phase
- Branches are assigned to workers by the hub using `compute_n_agents`
- Reducer merges all worker patches at the end of each phase

#### Planner CoT Prompt

```
You are the strategic planner for a multi-phase agent system.
The goal can be any type of task — research, writing, analysis, design,
code generation, data processing, or a mix. Do not assume a specific domain.
Your plan determines the phases and the parallel work within each phase.
Think step by step.

GOAL: {goal}
DELIVERABLE PATH: {artifact_path}
EXISTING DELIVERABLE: {artifact_summary_or_none}
ACCUMULATED WEAKNESSES: {weaknesses_block}

Step 1 — Understand the goal.
  What is the final deliverable? What form does it take?
  What does "done" look like for THIS specific goal?
  What quality bar must it meet?

Step 2 — Identify major work areas (epics).
  Break the goal into 2–5 natural phases of work, based on what the goal
  actually requires. Do not default to Research→Analysis→Writing unless
  those phases genuinely fit the goal.
  Examples of epic structures for different goal types:
    - Writing task:    Draft → Review → Polish
    - Data task:       Gather → Clean → Aggregate → Visualize
    - Design task:     Requirements → Prototype → Validate
    - Mixed task:      whatever logical sequence the goal demands
  Each epic must be large enough to justify a full phase — not a single tool call.
  Define the dependency order (which epics must precede others).

Step 3 — For each epic, enumerate branches.
  Each branch is one concrete, independently executable subtask that advances
  the epic's goal. Branches within an epic run in parallel — they must not
  depend on each other.
  Aim for 6–15 branches per epic (agent sizing handles the worker count).
  Each branch must be completable by one agent in one session with available tools.
  Branch descriptions must be specific to the goal — not generic templates.

Step 4 — Check against existing deliverable and weaknesses.
  If a deliverable exists: branches must address gaps and weaknesses only,
  not reconstruct what already exists.
  If no deliverable: Epic 1 branches should establish the initial structure.

Step 5 — Emit the epic plan in structured JSON:

EPIC_PLAN:
```json
{
  "epics": [
    {
      "id": "epic-1-id",
      "title": "Epic 1 Title (derived from goal — not a template)",
      "description": "What this phase accomplishes, specific to the goal",
      "depends_on": [],
      "branches": [
        {"id": "branch-1a", "description": "Specific subtask A for this goal"},
        {"id": "branch-1b", "description": "Specific subtask B for this goal"},
        {"id": "branch-1c", "description": "Specific subtask C for this goal"}
      ]
    },
    {
      "id": "epic-2-id",
      "title": "Epic 2 Title",
      "description": "What this phase accomplishes",
      "depends_on": ["epic-1-id"],
      "branches": [
        {"id": "branch-2a", "description": "Specific subtask A"},
        {"id": "branch-2b", "description": "Specific subtask B"}
      ]
    }
  ]
}
```
```

Epic and branch titles/descriptions are always derived from the specific goal —
never filled with placeholder text like "Research Phase" or "Fetch articles"
unless those words genuinely describe what the goal requires.

#### Runner integration

`_parse_epic_plan(planner_output)` parses the `EPIC_PLAN` JSON block. In the
**live runner**, the phase loop is driven by `plan_obj.steps` (the plan
decomposition); the `TaskLedger` is seeded **up front from those steps** so
`remaining()` reflects real pending work from the very first phase (see §3.2).

```python
# runner.py — seed the ledger with every planned phase BEFORE the loop
_ledger = TaskLedger()
for _s in plan_obj.steps:
    _ledger.add_task(TaskRecord(id=_s.id, description=_s.description[:120]))
```

The hub for each phase receives `_ledger.to_context_block()` showing which
phases completed earlier (COMPLETED) and which remain (REMAINING), with an
explicit "do not duplicate" instruction. Seeding up front is what makes the
REMAINING half non-empty — without it `remaining()` is structurally always empty.

---

### 2.4 Cross-Task Context Retrieval (R10)

`task_hash = sha256(requirement)` is an **exact** key — `latest`/`best`/`all_runs`
only find history for the *identical* requirement. To also carry lessons across
*similar* tasks, `TaskRunStore` embeds each run's requirement and retrieves the
most cosine-similar prior tasks.

```python
# studio/task_runs.py
class TaskRunStore:
    def __init__(self, db_path=None, embedder=None): ...   # embedder optional

    def similar_runs(self, requirement, embedder, k=5,
                     min_similarity=0.35, exclude_hash=None
                     ) -> list[tuple[TaskRun, float]]:
        """Embed `requirement`, cosine-rank every prior task's requirement,
        return the best-scoring run per distinct task_hash above threshold
        (excluding the current task's own exact history)."""

    def accumulated_weaknesses(self, requirement, exact_hash, embedder=None,
                               k_similar=5, min_similarity=0.35,
                               consolidate_threshold=0.85) -> list[str]:
        """Exact-task lessons first → similar-task lessons → exact-string dedup
        → semantic consolidation. This is the list fed to the hub prompt."""
```

- **Schema:** a `requirement_embedding BLOB` column on `task_runs` (float32
  vector). Existing rows are NULL; `_backfill_embeddings(embedder)` embeds them
  lazily on first similarity query. New runs are embedded on `record()` when an
  embedder is wired.
- **Consolidation (dedup + merge):** `_consolidate_weaknesses` drops a weakness
  when it is `>= consolidate_threshold` cosine-similar to one already kept, so
  "no citations" and "sources lack URLs" collapse to one lesson — not just
  exact-string dedup.
- **Wiring:** the runner builds `TaskRunStore(embedder=self._embedder)` and calls
  `accumulated_weaknesses(requirement, _thash, embedder=...)` when assembling the
  hub's weakness block. Degrades to exact-string-deduped exact-task lessons when
  no embedder is available.
- **Graceful failure:** embedding/backfill/consolidation are best-effort
  (`try/except`); a down embedder never breaks a run.

---

## 3. Phase-to-Phase Work Synchronization

### 3.1 TaskRecord and TaskLedger

```python
# agentkit/orchestrator/ledger.py

@dataclass(frozen=True)
class TaskRecord:
    id: str           # slug — used for dedup (set membership)
    description: str  # full text — what the next hub reads to reason about coverage

@dataclass
class TaskLedger:
    all_tasks: list[TaskRecord]       # full universe for this run
    completed: list[TaskRecord]       # done by prior phases (ordered, readable)
    in_flight: set[str]               # ids currently assigned (collision guard)

    def remaining(self) -> list[TaskRecord]:
        done_ids = {t.id for t in self.completed} | self.in_flight
        return [t for t in self.all_tasks if t.id not in done_ids]

    def mark_done(self, task_id: str) -> None:
        rec = next((t for t in self.all_tasks if t.id == task_id), None)
        if rec and rec not in self.completed:
            self.completed.append(rec)
        self.in_flight.discard(task_id)

    def mark_in_flight(self, task_id: str) -> None:
        self.in_flight.add(task_id)          # collision guard while a phase runs

    def add_task(self, record: TaskRecord) -> None:
        if not any(t.id == record.id for t in self.all_tasks):
            self.all_tasks.append(record)    # used to seed all_tasks up front

    def to_context_block(self) -> str:
        """Serialised as human+machine readable block for injection into hub prompt."""
        done = "\n".join(f"- [{t.id}] {t.description}" for t in self.completed)
        remaining = "\n".join(f"- [{t.id}] {t.description}" for t in self.remaining())
        return (
            f"COMPLETED TASKS FROM PRIOR PHASES:\n{done or '(none)'}\n\n"
            f"REMAINING TASKS (do not duplicate the above):\n{remaining or '(none)'}"
        )
```

### 3.2 Runner Phase Loop

`runner.py` carries one `TaskLedger` across all phases. It is **seeded up front**
from the planned phases, then each phase is marked in-flight while it runs and
done after — so `remaining()` and the COMPLETED/REMAINING context block are
always accurate:

```python
ledger = TaskLedger()
for s in plan_obj.steps:                       # seed BEFORE the loop
    ledger.add_task(TaskRecord(id=s.id, description=s.description[:120]))

for step in plan_obj.steps:
    # hub CoT prompt (with ledger.to_context_block() + artifact_path) is built
    # for STAR/MAP fan-out phases and prepended to the step description.

    # worker count is derived from remaining work (DESIGN §4):
    n_remaining = max(1, len(ledger.remaining()))
    max_workers = compute_n_agents(n_remaining, sizing_cfg)

    ledger.mark_in_flight(step.id)             # collision guard during the phase
    result = run_plan(sub_plan, client, max_workers=max_workers)
    ledger.mark_done(step.id)                  # completed → carried to next phase

# After workers finish, the Reducer collects PATCHES blocks from the phase
# outputs and applies them in ONE pass (two-phase: structural merge + LLM refine):
patch_groups = [_parse_patches_from_output(o) for o in outputs.values()]
patch_groups = [g for g in patch_groups if g]
if patch_groups:
    refine_fn = _make_refine_fn(base_client, goal, art_path) if use_llm else None
    rr = reduce_patches(current_text, patch_groups, llm_refine_fn=refine_fn)
    if rr.text:
        write_artifact(art_file, rr.text)      # atomic tmp+rename
```

> **Note on sub-task DONE reconciliation:** completion is tracked at **phase
> granularity** (`step.id`). The hub's `DONE:` JSON block (§3.3) is an
> agent-facing instruction; the runner does not parse it back into the ledger —
> a finished phase is marked done by id. This keeps the ledger simple and is
> sufficient for the "don't redo a completed phase" guarantee.

### 3.3 Hub Output Protocol

Hub agents emit three structured blocks:

````
TASK_LIST:
```json
[
  {"id": "branch-1a", "description": "Specific subtask A derived from the goal and epic"},
  {"id": "branch-1b", "description": "Specific subtask B derived from the goal and epic"},
  {"id": "branch-1c", "description": "Specific subtask C derived from the goal and epic"},
  {"id": "branch-1d", "description": "Specific subtask D derived from the goal and epic"},
  {"id": "branch-1e", "description": "Specific subtask E derived from the goal and epic"}
]
```

ASSIGNED:
```json
{
  "agent_1": ["branch-1a", "branch-1b", "branch-1c"],
  "agent_2": ["branch-1d", "branch-1e"]
}
```

DONE:
```json
["branch-from-prior-epic-x", "branch-from-prior-epic-y"]
```
````

Runner parses these blocks via regex; text outside the blocks is ignored.

> **Enforcement (R2 — non-overlapping assignment):** the hub CoT prompt mandates
> non-overlapping, one-section-per-agent assignment (§5.1 Step 5), the `in_flight`
> set guards a phase from re-entry while running, AND the runner now **validates
> the `ASSIGNED` block in code**: `_parse_assigned` extracts agent→sections and
> `_dedupe_assignment` detects any section claimed by ≥2 agents, deterministically
> reassigning it **first-claim-wins** (the earliest agent in assignment order
> keeps it; within-agent repeats collapse too). The result is surfaced as a
> `GateEvent(name="worker-assignment", outcome="pass"|"warn", …)` so violations
> are visible in the gate panel / Loop Doctor, not silent. The dedupe runs
> post-phase (the hub + workers execute inside one `run_plan` call); it makes the
> partition deterministic and auditable rather than re-prompting (which would add
> a round-trip). Pure-function core in `studio/runner.py`, tested in
> `test_m8_m9_helpers.py`.

---

## 4. Dynamic Agent Sizing

### 4.1 Algorithm (`agentkit.topology.sizing`)

`SizingConfig` values are set from the **Loop Config UI panel** (§9) and stored
in `LoopConfig`. The defaults below apply only when no session config is present.
Users adjust them via sliders before each run — not in code.

```python
# agentkit/topology/sizing.py

import math
from dataclasses import dataclass

@dataclass
class SizingConfig:
    min_tasks_per_agent: int = 3   # UI default; overridden by Loop Config panel slider
    max_tasks_per_agent: int = 5   # UI default; overridden by Loop Config panel slider
    max_agents: int = 5            # HARD ceiling on agent count (menu slider). A
                                   # flooded task list can never explode the topology
                                   # (2026-06-27 gap-flood fix). Product spec: 3..5.

def compute_n_agents(n_tasks: int, cfg: SizingConfig = SizingConfig()) -> int:
    """
    Derive agent count so each agent gets at most max_tasks_per_agent tasks,
    then CLAMP to max_agents. Last agent may receive fewer than min_tasks_per_agent.

    Examples (max_tasks=5, max_agents=5):
      n=3  -> 1 agent  (3 tasks)
      n=6  -> 2 agents (5+1)
      n=11 -> 3 agents (5+5+1)
      n=74 -> 5 agents (capped — pre-fix this was ceil(74/5)=15 → topology explosion)
    """
    if n_tasks <= 0:
        return 1
    return max(1, min(cfg.max_agents, math.ceil(n_tasks / cfg.max_tasks_per_agent)))

def assign_tasks(
    tasks: list, cfg: SizingConfig = SizingConfig()
) -> list[list]:
    """Partition tasks across agents; returns list-of-lists (one per agent).
    Distribution is ceiling-div so earlier agents may get one extra task."""
    n = compute_n_agents(len(tasks), cfg)
    size = math.ceil(len(tasks) / n) if n else len(tasks)
    return [tasks[i * size:(i + 1) * size] for i in range(n)]
```

### 4.2 Integration

The explicit `n` parameter is removed; the worker count is **derived**. In the
live runner the count comes from the ledger's remaining work for the phase
(seeded up front, §3.2), capped into `run_plan`'s `max_workers`:

```python
# runner.py — derive worker count, never hard-code n
from agentkit.topology.sizing import compute_n_agents
n_remaining = max(1, len(ledger.remaining()))
max_workers = compute_n_agents(n_remaining, cfg=session.loop_config.sizing())
result = run_plan(sub_plan, client, max_workers=max_workers)
```

`assign_tasks` partitions an explicit task list when one is in hand; the runner
path above sizes from the remaining count. Either way the rule is identical:
≥`min`/≤`max` tasks per agent, last agent may be smaller, no caller-specified `n`.

### 4.3 Configuration

Flow: **Loop Config UI panel → `LoopConfig` → `SizingConfig` → `compute_n_agents`**

User sets sliders in the UI before running. Values travel in `POST /session` body
as `loop_config`. Runner calls `session.loop_config.sizing()` to get `SizingConfig`.
Hardcoded defaults in `SizingConfig` are never used directly by the runner.

```python
@dataclass
class LoopConfig:
    deliverable_path: str | None = None  # from UI "Deliverable Path" field
    auto_improve: bool = True            # from UI toggle
    min_tasks_per_agent: int = 3         # from UI slider — sent in POST /session
    max_tasks_per_agent: int = 5         # from UI slider — sent in POST /session

    def sizing(self) -> SizingConfig:
        return SizingConfig(
            min_tasks_per_agent=self.min_tasks_per_agent,
            max_tasks_per_agent=self.max_tasks_per_agent,
        )
```

---

## 5. Hub CoT Prompt Structure

All hub prompts use explicit numbered reasoning steps.

### 5.1 Initial Hub (deliverable exists)

```
You are the planning hub for a multi-phase agent system.
Think through each step carefully before acting.

CONTEXT:
  Goal: {goal}
  Deliverable: {artifact_path}
  Prior completed tasks: {ledger.to_context_block()}
  Accumulated weaknesses from prior runs:
{weaknesses_block}

Step 1 — Read the existing deliverable structure.
  Identify its current sections, coverage depth, and citation quality.
  List what is present and what is thin or missing.

Step 2 — Compare against the goal.
  For each gap found in Step 1, write one sentence explaining why it falls
  short of the goal. Be specific (e.g. "Section 2 has no primary source URLs").

Step 3 — Generalize the weaknesses.
  For each weakness in the ACCUMULATED WEAKNESSES block, restate it as a
  universal requirement that applies across topics
  (e.g. "missing citations" -> "every factual claim must include a live URL").
  Universal requirements survive across different topics and runs.

Step 4 — Define this phase's work items.
  Combine gaps (Step 2) and universal requirements (Step 3).
  Each work item must be:
    - Additive or corrective (not reconstructive)
    - Independently assignable to one agent
    - Completable in one LLM call with web tools
  Do NOT include items already in COMPLETED TASKS.

Step 5 — Assign work items to agents by document section.
  Rules:
    - Each agent owns a non-overlapping set of sections (e.g. "## Results", "## Analysis").
      Assign by section heading, NOT by topic — "improve Section X" not "cover Topic Y".
      Section-scoped assignment guarantees non-overlapping anchors so patches commute.
    - Max {max_tasks_per_agent} sections per agent
    - Last agent may receive fewer
    - No section assigned to more than one agent
    - Tell each agent its exact section headings (verbatim from the document) so its
      PATCHES anchors are unambiguous.
  Emit TASK_LIST, ASSIGNED, and DONE blocks (JSON, as specified in §3.3).

Step 6 — Emit DELIVERABLE_PATH: {artifact_path}
  so all downstream agents know where to write patches.
```

### 5.2 Initial Hub (no prior deliverable — first run)

Same structure except Step 1 becomes:

```
Step 1 — No existing deliverable found.
  You will plan the creation of the first draft.
  Define the document structure: section headings and their purpose.
  Assign one or more sections to each agent (Step 5 rules still apply).
  Each agent will create its sections from scratch via PATCHES with op=append.
  After this phase the deliverable will be created at: {artifact_path}
```

### 5.3 Worker Agent CoT Prompt

Workers are **stateless suggesters** — they emit patch suggestions only.
They do not write to disk. A Reducer applies the patches after all workers finish.

```
You are a worker agent. You will suggest changes to a shared
document. Do NOT write to any file — emit patch suggestions only.
Think step by step.

TASK ASSIGNMENTS:
{task_list_for_this_agent}

CURRENT DELIVERABLE CONTENT:
{artifact_current_text}

Step 1 — For each assigned task, state what you need to find or verify.

Step 2 — Execute: use web_search and web_fetch to gather evidence.
  For each source: note the URL, title, and key facts extracted.

Step 3 — Assess completeness.
  For each task, have you found sufficient evidence?
  If not, perform one additional search before proceeding.

Step 4 — Draft your patch suggestions.
  Rules:
    - Use the exact section heading string as your anchor (e.g. "## Results").
      Copy the heading verbatim from CURRENT DELIVERABLE CONTENT — do not paraphrase.
    - Each patch targets only sections you were assigned.
    - Do NOT write patches for sections assigned to other agents.
    - Prefer insert_after/append over replace — additive patches on distinct
      anchors commute; replace patches on the same anchor conflict.
    - Use the PATCHES JSON format exactly (see §2.2).

Step 5 — Emit DONE markers for each task you completed.
  Format: DONE: ["task-id-1", "task-id-2"]

Step 6 — Emit your PATCHES block.
  The Reducer will: (1) collect all workers' patches, (2) resolve conflicts,
  (3) perform a full editorial refinement pass, then (4) atomic write to disk.
```

---

## 6. Chat Window (replaces task textarea)

### 6.1 Frontend change

The main task `<textarea>` is replaced by the `<ChatPanel>` component
(`frontend/src/components/hud/ChatPanel.tsx`, mounted in `App.tsx`):
- Multi-turn message thread (`role: "user" | "assistant"`)
- On submit, prior completed turns are forwarded as `history` (the new
  user/assistant pair is appended locally, not sent in `history`)
- `openRunStream(sessionId, req, callbacks, history)` (`api/sse.ts`) appends
  `&history=<json>` to the SSE URL when history is non-empty

### 6.2 Backend flattening

```python
# studio/session.py
def flatten_chat_to_requirement(messages: list[dict]) -> str:
    """Concatenate messages into structured requirement context for the planner."""
    return "\n\n".join(
        f"[{m['role'].upper()}]: {m['content']}"
        for m in messages
        if m.get("role") in ("user", "assistant")
    )
```

Wiring is on **`GET /run/{session_id}`** (`studio/app.py`), which accepts an
optional JSON-encoded `history` query param:

```python
@app.get("/run/{session_id}")
async def get_run(session_id, requirement, history=None):
    if history:
        turns = json.loads(history)                       # untrusted → guarded
        flat = flatten_chat_to_requirement(turns)
        if flat:
            requirement = f"{flat}\n\n[CURRENT REQUEST]: {requirement}"
    ...
```

Malformed `history` falls back to the bare `requirement` (single-textarea
contract). All prior refinements are visible to the planner, not just the last
user message.

---

## 7. Shared Library Candidates (agentkit repo)

| Component | Current location | agentkit target |
|---|---|---|
| Dynamic agent sizing | *(new)* | `agentkit.topology.sizing` |
| TaskRecord + TaskLedger | *(new in runner)* | `agentkit.orchestrator.ledger` |
| DocPatch + apply_patches | *(new in workspace)* | `agentkit.artifacts.patcher` |
| DeliverableStore (read/write/path/resolve) | `studio/workspace.py` | `agentkit.artifacts.store` |
| TaskRunStore | `studio/task_runs.py` | `agentkit.improvement.store` |
| score_result | `studio/task_runs.py` | `agentkit.improvement.scorer` |
| mine_weaknesses_from_outputs | `studio/task_runs.py` | `agentkit.improvement.miner` |
| similar_runs / accumulated_weaknesses (R10 cross-task retrieval) | `studio/task_runs.py` | `agentkit.improvement.store` |
| Context compaction | `agentkit/context.py` | already there |
| OpenAIEmbedder retry | `agentkit/backends/openai_compat.py` | already there |
| InFlightRegistry (URL dedup) | `studio/tools.py` | `agentkit.tools.fetch_cache` |

### 7.1 InFlightRegistry (pending-fetch dedup)

Prevents same-URL duplicate HTTP calls from parallel workers in the same phase.
First caller fetches; concurrent callers wait and share the result:

```python
# agentkit/tools/fetch_cache.py

import threading

class InFlightRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._results: dict[str, object] = {}

    def get_or_fetch(self, key: str, fetch_fn):
        with self._lock:
            if key in self._results:
                return self._results[key]          # already done
            if key in self._events:
                event = self._events[key]          # in-flight: wait
            else:
                event = threading.Event()
                self._events[key] = event
                event = None                       # I am the fetcher

        if event is not None:
            event.wait()
            return self._results[key]

        try:                                       # fetcher path
            result = fetch_fn()
            with self._lock:
                self._results[key] = result
            return result
        finally:
            with self._lock:
                ev = self._events.pop(key, None)
            if ev:
                ev.set()                           # unblock waiters
```

---

## 8. Implementation Order

```
M1  agentkit.topology.sizing          compute_n_agents, assign_tasks, SizingConfig          ✓ DONE
M2  agentkit.orchestrator.ledger      TaskRecord, TaskLedger, to_context_block               ✓ DONE
M3  agentkit.artifacts.patcher        DocPatch, reduce_patches, write_artifact,              ✓ DONE
                                      cleanup_orphaned_tmp
M4  agentkit.artifacts.store          resolve_deliverable, latest_with_content               ✓ DONE
         depends on: M3
M5  agentkit.improvement.*            port TaskRunStore, score_result, mine_weaknesses       ✓ DONE
         depends on: M4
M6  agentkit.tools.fetch_cache        InFlightRegistry (phase-level URL dedup)               ✓ DONE
M7  studio/workspace.py               integrate M4; add resolve_deliverable wrapper          ✓ DONE
         depends on: M4, M5
M8  studio/runner.py                  epic plan parsing; phase loop: TaskLedger (M2),        ✓ DONE
                                      Reducer + patch apply (M3), deliverable resolve (M7),
                                      dynamic sizing (M1), InFlightRegistry (M6)
         depends on: M1, M2, M3, M6, M7
M9  studio/runner.py                  CoT hub + worker + Reducer prompts (§5, §2.2)          ✓ DONE
         depends on: M8                 _parse_epic_plan, _build_planner_cot_prompt,
                                        _build_hub_cot_prompt, _parse_patches_from_output
M10 studio/models.py                  LoopConfig with deliverable_path + sizing params       ✓ DONE
M11 frontend: ChatPanel               replace task textarea with multi-turn chat              ✓ DONE
M12 frontend: Loop config panel       deliverable_path field + sizing sliders                 ✓ DONE
         depends on: M10
M13 studio/session.py + app.py        flatten_chat_to_requirement; GET /run?history=          ✓ DONE
    frontend: sse.ts + ChatPanel        forward prior turns as history (R9 end-to-end)
         depends on: M11
M14 studio/task_runs.py               R10 cross-task retrieval: requirement_embedding column, ✓ DONE
                                      similar_runs, accumulated_weaknesses (dedup +
                                      semantic consolidation); runner wires the embedder
         depends on: M5
M15 studio/runner.py                  R1/R2 fixes: seed TaskLedger from plan_obj.steps        ✓ DONE
                                      up front; mark_in_flight per phase (live collision
                                      guard); reducer refine prompt → numbered-step CoT (R7)
         depends on: M8, M9
```

---

## 9. Loop Config Panel (UI additions)

```
[ Loop Settings ]
  ─────────────────────────────────────────────
  Deliverable
    Path:  [________________________________]  Browse...
    ○ Use latest prior artifact (hill-climb)   ← default
    ○ Create new artifact each run

  Agent Sizing
    Min tasks per agent:  [3] ──── slider 1–10
    Max tasks per agent:  [5] ──── slider 1–10
  ─────────────────────────────────────────────
```

`deliverable_path` empty + "Use latest prior" → resolution falls through to
hill-climb (§2.1 priority 2). Empty + "Create new" → always auto-create (§2.1 priority 3).

---

## 10. Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Deliverable seed: latest vs best-score | Latest with content | Score is noisy (LLM self-eval); latest has most accumulated work |
| Task tracking: IDs vs descriptions | Both (TaskRecord) | ID for dedup, description for agent reasoning |
| Document modification | Patch-based, atomic rename | Crash-safe; no empty file risk; incremental by design |
| Agent count source | Derived from task count | Hard-coded n is arbitrary; task-driven sizing is self-calibrating |
| Weakness generalization | Hub CoT step (LLM) | Avoids brittle code heuristics; LLM generalizes naturally |
| Requirement input | Chat (multi-turn) | Captures refinements; full context visible to planner |
| Shared library boundary | agentkit for reusable primitives | Studio = orchestration shell; agentkit = reusable engine |
| Context history scope (R10) | Exact task **+ similar tasks** (cosine over requirement embeddings) | A brand-new requirement has no exact history; similar prior tasks still carry transferable lessons |
| Weakness list to agent (R10) | Dedup (exact) **+ consolidate** (semantic near-dup merge) | Different phrasings of the same lesson would otherwise bloat the prompt and dilute signal |
| Deliverable seed embedding | Lazy backfill of legacy rows | Avoids a blocking migration over the whole DB; embeds on first similarity query |
| Phase-2 refine acceptance | Length guard (≥80% of merged) + best-effort | Rejects truncated/empty LLM responses; a flaky refine never corrupts a clean structural merge |
| Worker section non-overlap (R2) | Prompt-enforced **+ code-validated** (`_dedupe_assignment`, first-claim-wins, emitted as a gate check) | Prompt sets intent; the post-phase validator makes the partition deterministic + auditable without a re-prompt round-trip (see §3.3 enforcement) |
| Ledger seeding (R1) | Seed `all_tasks` from `plan_obj.steps` up front | Without it `remaining()` is structurally always empty; seeding makes COMPLETED/REMAINING real across phases |
| Reducer role (§11) | Merger + editorial + gap-flagger, **never a generator** | A reducer that re-emits the whole doc truncates/summarizes it away; an additive merger cannot regress it |
| Worker on no result (§11) | **Silent no-op** (emit no patch), never failure-prose | Prose about being blocked becomes "content" the reducer synthesizes into a thin doc — the root of the regression |
| Search-error vs found-nothing (§11) | Distinct: found-nothing = no-op; all-error = **halt + notice** | A broken-search run must not masquerade as a finished one |
| New/missing content (§11) | Reducer flags a **gap**; a worker (with search) fills it | Reducer has no search tool — anything it invents is ungrounded |
| Gap routing (§11) | Non-last phase → TaskLedger (this run); last phase → **weaknesses DB** (next run) | Every gap has a destination; reducer gaps are concrete, beating LLM-mined weaknesses |
| Create == improve (§11) | Same additive pipeline; create starts from a **skeleton**, improve from the prior doc | Removes the special-cased "one LLM writes the whole report" path that seeded the spiral |

---

## 11. Grounded Accumulation & Regression-Free Improvement

> **Why this section exists.** A search outage produced thin "search unavailable"
> narration that the full-rewrite reducer synthesized into the deliverable,
> overwriting a good 28 KB grounded report; `latest_with_content` then seeded the
> degraded doc forward — a death spiral. Root cause: the reducer **regenerates the
> whole document in one LLM call** (§2.2 / runner.py:863 — "Output the COMPLETE
> updated artifact"), which is bounded by output tokens and biased toward
> summarization, so it *trends shorter than its input*. This section defines the
> design that makes regression **impossible by construction**, not merely guarded.

### 11.1 Core invariant

**Content only enters the deliverable through a worker that found a real source.
Absence of content is a tracked placeholder, never invented prose. The reducer
applies deltas and never re-emits the whole document.** Therefore the deliverable
is a monotone accumulation of grounded facts: it can only grow or hold, never
shrink. "Worst case = no improvement" becomes a structural guarantee.

### 11.2 Worker contract (supersedes the §5.3 prose-friendly worker)

Each worker owns a non-overlapping set of sections (§3.3 assignment). Per section:

```
- Found grounded, sourced content  → emit a PATCH (op=insert_after/replace on the
                                      section anchor; URL required).
- Found nothing relevant           → emit NOTHING for that section (no patch).
- Never write a sentence explaining why you couldn't. Silence = no change.

Status line (machine-readable, one per worker):
  FINDINGS: <n>            # n sourced findings this worker produced
  SEARCH: ok | error       # 'error' iff the search tool itself failed (quota/down)
```

A worker's body is *only* `PATCHES: [...]` (possibly empty). The "blocker
narration" failure mode is forbidden.

### 11.3 Reducer contract (replaces the full-rewrite reducer)

The reducer **never regenerates the document**. It:

1. **Applies** worker patches to the existing doc via `reduce_patches` (additive
   `DocPatch` merge — §2.2 Phase 1). It does not re-emit unchanged sections.
2. **Editorial pass** runs *only on the merged result* (dedup, ordering,
   transitions, citation formatting) — and may not delete sourced content or
   shorten a section below its pre-merge length.
3. **Detects gaps** — sections that are thin, placeholder, or have unsourced
   claims — and emits them as structured, actionable items
   (`"§Verifier: 3 claims lack source URLs"`), never filling them itself.

### 11.4 Gap routing — the section is the unit of work

**Section is the single currency of the loop.** Every unit of work — a detected
gap, a consolidated task, a hub assignment, a patch, a carried-forward weakness —
is keyed by the artifact section it belongs to. This is load-bearing: an agent may
only patch sections **assigned to it** (§11.2 + the ASSIGNED validation, §3.3), so
any work item that is *not* tied to a section is unactionable — it would be handed
to an agent with no mandate to touch it. Keying everything by section also makes
the bound **`n_agents ≤ n_sections`** propagate everywhere for free.

**Detection → consolidation (the 2026-06-27 gap-flood fix).** `_detect_gaps`
flags only **empty / placeholder** sections — NOT "prose without an inline URL"
(that old rule mis-flagged every well-formed section of a properly-cited report,
which keeps its citations in a References section, producing ~74 false gaps that
exploded agent sizing to 18 and burned 2.5M tokens). Each gap is tagged with its
nearest **top-level (h1/h2)** section. Before anything is sized or routed, gaps are
**consolidated to distinct top-level sections** (`_gap_sections`): 74 raw sub-gaps
collapse to the ~6 real sections that own them.

```
gap detected in phase N (tagged with its top-level section):
  consolidate → distinct sections needing work          (bounded by the document)
  N is not the last phase  → one TaskRecord PER SECTION in the TaskLedger
                             → handed forward to phase N+1's hub, which RE-JUDGES
                               and assigns within its own bounded sizing (this run)
  N is the last phase       → record as SECTION-BOUND weaknesses in task_runs.db,
                               format "[## Section] issue"
                             → next run's hill-climb injects each weakness only to
                               the agent that owns its section
                               (feeds the §2.4 accumulated_weaknesses pipeline,
                                deduped + consolidated)
```

**Sizing is driven by consolidated sections, never raw gap count**, and clamped by
`SizingConfig.max_agents` (menu-configurable, default 5 — the "≥3 ≤5 agents"
product spec). So a noisy gap list can no longer inflate the topology: this is the
shared-ledger-poisoning fix (a completed phase's count cannot be re-expanded by a
later flood). `compute_n_agents` returns `min(max_agents, ceil(n / max_per_agent))`.

**Section-bound weaknesses.** `mine_weaknesses_from_outputs` prefixes each weakness
with the section it concerns (`[## Sources] missing URLs`; `[document]` only for
whole-doc/structural issues). On the next run the hub assigns sections, and each
worker routes weaknesses by their label:

```
[## Section] weakness  → ONLY the agent that owns that section fixes it
                         (an agent cannot patch a section it does not own).
[document]   weakness  → EVERY agent fixes it within its OWN assigned sections
                         (a global bar — grounding, no truncation, consistent
                         terminology — applied per-section). No agent edits a
                         section outside its set, so the broadcast stays
                         conflict-free under the additive-patch contract (§11.3).
```

The `[document]` broadcast is why a structural weakness with no single owner still
gets acted on: instead of being orphaned, it is handed to **all** agents, each of
whom enforces it on the sections it holds. This bounds weakness count by section,
and closes the loop: detection → consolidation → assignment → modification →
carry-forward all share the section key.

**Loop-closure check — surface, never hide.** Without closure, recorded weaknesses
*recur* across runs (observed v8→v24: same "unverifiable sources / truncation / no
metrics") because the detector that re-finds a weakness does not know it was
*already injected and not fixed*. A **repeat-failure** is a weakness recorded in
**≥ `REPEAT_LIMIT` (3) distinct prior runs** of this task (`TaskRunStore.
repeat_failures`, counted over normalized text, section label stripped).

A repeat-failure is **not dropped and not hidden** — that would silently strand
work the user never sees. Instead:

```
1. NEVER drop it. It flows through the normal handoff (ledger → next phase), so
   the LAST phase gets a final attempt with full-document context + search.
2. After the run, any repeat-failure STILL recorded this run (attempted again,
   incl. the last phase, and still open) is APPENDED below the result as a
   "## ⚠️ Known unresolved issues" block — shown in the chat window so the user
   knows exactly what could not be resolved (_unresolved_block, runner end).
3. It is scored BEFORE the block is appended, so the honesty footer never
   inflates or deflates the quality score.
```

This is reducer/run-end owned (the producer), and it is **transparency over
silent convergence**: a genuinely unfixable weakness (the data does not exist, an
infra 503) is surfaced to the user every run until resolved, rather than swept
away after N rounds. `repeat_failures` (history/policy) lives in `TaskRunStore`;
the run end owns rendering the user-facing report.

Reducer gaps are a **better weakness source** than `mine_weaknesses_from_outputs`:
they are concrete and grounded in what the reducer actually saw, so the next run's
constraints are specific and the hill-climb genuinely converges.

### 11.5 Create and improve are one pipeline

The only difference is the starting point; the mechanism is identical.

```
Improve:  start = prior doc (seeded)   → workers patch owned sections → reducer merges deltas
Create:   start = empty skeleton       → workers fill owned sections  → reducer merges deltas

Create, phase 1 — build the SKELETON, not content:
  Derive section headings + a one-line intent per section FROM THE GOAL (no search
  needed → robust to a search outage). Each heading becomes an owned, fillable
  section with a placeholder body:  "## Results\n_(pending — needs sourced content)_"
```

**Placeholders are first-class.** A skeleton-with-placeholders is an honest partial
deliverable: low-scored but improvable, and it doubles as the gap list. When all
workers fail (search down) on a create run, the deliverable is the skeleton + a
visible "search unavailable" notice — **never a fabricated report.** This is the
discipline whose absence seeded the original spiral.

### 11.6 Failure handling

```
zero patches this phase          → do NOT write; seed/skeleton preserved (no-improvement)
all workers report SEARCH:error  → halt the phase + emit a GateEvent("search-unavailable",
                                   outcome="fail"); do not record a degraded score
```

### 11.7 Termination (loop-until-gaps-dry)

The run records `gaps_remaining` (count from §11.4). Hill-climb stops when:

- `gaps_remaining == 0` (done), OR
- two consecutive runs reduce `gaps_remaining` by zero (genuinely stuck), OR
- `max_epochs` reached (hard cap from the Loop Config panel).

### 11.8 Backstop (defense in depth)

The anti-regression **write guard** remains as a belt-and-suspenders check: a
write that would make `len(artifact) < len(seed)` is rejected (both write paths).
With §11.1–11.5 in place this should never trigger; it exists to catch any path
that bypasses the additive contract.

### 11.9 Implementation order

```
A  worker contract: patch-or-silent + SEARCH status; skeleton-on-create        (stops the regression source)
B  route the seeded research path through reduce_patches (retire runner.py:863) (stops full-doc regeneration)
C  reducer gap-detection → TaskLedger (non-last) / weaknesses DB (last)         (convergence)
D  termination on gaps-dry; placeholder-aware scoring (% sections sourced)      (bounded loop)
```
