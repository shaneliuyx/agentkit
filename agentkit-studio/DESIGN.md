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

#### Reducer algorithm (`agentkit.artifacts.patcher`)

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

    # TWO levers (§4.4): concurrency vs breadth.
    n_remaining = max(1, len(ledger.remaining()))
    max_workers = compute_n_agents(n_remaining, sizing_cfg)  # how many run at once
    max_agents  = sizing_cfg.max_agents                      # how many SPOKES exist

    ledger.mark_in_flight(step.id)             # collision guard during the phase
    result = run_plan(sub_plan, client, max_workers=max_workers, max_agents=max_agents)
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
    max_agents: int = 5            # HARD ceiling on agent count (menu slider).
                                   # Product spec: 3..5. NOTE: clamping
                                   # compute_n_agents alone does NOT stop the
                                   # explosion — it bounds CONCURRENCY only. The
                                   # spoke COUNT is a separate lever (see §4.4);
                                   # max_agents must ALSO be passed to run_plan.

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
sizing_cfg  = session.loop_config.sizing()
max_workers = compute_n_agents(n_remaining, cfg=sizing_cfg)   # concurrency
result = run_plan(sub_plan, client,
                  max_workers=max_workers,
                  max_agents=sizing_cfg.max_agents)            # breadth (§4.4)
```

`assign_tasks` partitions an explicit task list when one is in hand; the runner
path above sizes from the remaining count. Either way the rule is identical:
≥`min`/≤`max` tasks per agent, last agent may be smaller, no caller-specified `n`.

### 4.3 Configuration

Flow: **Loop Config UI panel → `LoopConfig` → `SizingConfig` → `compute_n_agents`**

User sets sliders in the UI before running. Values travel in `POST /session` body
as `loop_config`. Runner calls `session.loop_config.sizing()` to get `SizingConfig`.

### 4.4 Fan-out breadth cap & hill-climb topology (2026-06-27)

**Root cause of the 18-spoke / ~790K-token explosion.** `max_agents` /
`compute_n_agents` only set `run_plan`'s `max_workers`, which is **concurrency**
(thread-pool size) — NOT the number of spokes. The spoke COUNT is a *different*
lever the cap never touched:

- **STAR / MESH** — `dynamic._facets(description, n)` derived breadth by splitting
  the step description on `","` / `" and "` / `" vs "`. The cap arg `n` was applied
  as `parts[:max(n, len(parts))]` — i.e. an inverted cap that returned **all**
  parts. A ledger-stuffed hub prompt (74 gap items) splits into ~18 facets →
  18 STAR workers (+1 reduce) regardless of `max_workers=5`.
- **MAP** — `dynamic._run_map` spawned one worker per item from
  `_extract_items(upstream)` (30 URLs → 30 workers), also independent of
  `max_workers`.

**Fix (the COUNT lever).** `run_plan` gains `max_agents: int | None`:

- `_facets` now hard-caps: `parts[:n]` (the `max(n,len)` bug is removed). STAR/MESH
  facets ≤ `max_agents`.
- `_run_map` buckets items into ≤`max_agents` groups (`_bucket`, ceiling-split);
  one worker per bucket. With `max_agents=None` (CLI, no sizing) MAP keeps its
  one-worker-per-item shape for back-compat.
- The runner passes BOTH levers (§3.2 / §4.2): `max_workers` (concurrency, from
  remaining count) and `max_agents` (breadth, the raw slider value — NOT the
  remaining-derived count, or a phase with little remaining work would clamp its
  own breadth to 1).

`max_workers` and `max_agents` are deliberately **distinct**: conflating them is
what hid the bug for a whole session. Verified offline: `_facets(18-item desc, 5)
→ 5`; STAR `n_agents` 18 → 6 (5 workers + reduce). Section-consolidation (§11.4)
shrinks the ledger INPUT but never bounded the spoke count — only this cap does.

**Hill-climb forces STAR on every phase.** Topology is auto-derived per phase
(`assign_topologies` overwrites the epic STAR placeholder; a "compare …" phase
becomes MESH, a "write …" phase SINGLE). But only **STAR has the reducer** that
does the section-aware merge/refine/review needed to accumulate the artifact
across phases and epochs. So when `hill_climb_config.auto_improve` is on, the
runner overrides **every** phase to STAR (right after `assign_topologies`). The
breadth cap above keeps the forced STAR from exploding. MESH/PIPELINE/SINGLE are
left for non-hill-climb runs. (Tests: `test_hill_climb_forces_star_topology`,
`test_no_hill_climb_keeps_auto_topology`.)

### 4.5 Section-keyed handoff & the STAR reducer (DESIGN target)

**Decision (2026-06-27):** the phase-to-phase handoff is **section-keyed**, and
the STAR reducer is the active consolidation engine — not a generic synthesis.

- **Handoff** = `[ Section{ id, document_text, weaknesses[] }, … ]` — each section
  carries its current document AND its associated weakness list. That whole
  structure flows phase → phase (and seeds the next epoch), replacing the flat
  `outputs[step.id] = sr.output` text handoff.
- **STAR reducer (B2 — lives in the topology's reduce step, not the orchestrator):**
  for each section it **merges / refines / reviews** every assigned worker's
  output into that section's document, using the section's weakness list as the
  review checklist. The merge logic is injected by the orchestrator via a generic
  **reducer hook** on `run_plan` (keeps `agentkit` core domain-free: core only
  learns "use this reducer instead of the default synthesis"; the section/weakness
  semantics stay in `studio`).
- **No `Section` dataclass is threaded through core.** Sections are already
  encoded as the artifact's `##` markdown headings, and weaknesses are already
  `[## Section]`-tagged (§11.4). The reducer consumes artifact-text + tagged
  weaknesses as context — the markdown *is* the section-keyed structure.
- **Every phase** runs this reduce and writes the section-keyed artifact back with
  a grow-only anti-regression ratchet (`len(out) >= _seed_len`, then
  `_seed_len = len(out)`), so the document is monotonic across phases and epochs.

**Implementation.**
- Core hook: `run_plan(reducer: Callable[[list[str]], tuple[str,int]] | None)`.
  `_run_star` calls it instead of the generic synthesis when set; module global
  `_REDUCER` (mirrors `_POOL_WORKERS`/`_MAX_SPOKES`).
- Studio: `_make_section_reducer(client, artifact_text, weaknesses)` builds the
  merge/refine/review closure; the runner passes it to `run_plan` whenever
  `_artifact_copied` (hill-climb), reading artifact.md fresh each phase.
- Per-phase writeback generalized from the `is_last` block to every phase with
  the grow-only ratchet. Subsumes the earlier "pin last phase" idea — every
  hill-climb phase is STAR + section-aware, so no special last-phase casing.

**Status — LANDED.** Tests: `test_run_plan_star_uses_injected_reducer`,
`test_run_plan_reducer_default_is_generic_synthesis` (core),
`test_section_reducer_merges_with_artifact_and_weaknesses` (studio).

**`is_last` is load-bearing — do NOT remove.** Two distinct roles, both essential:
1. **Weakness lifecycle** (`if _gaps and not is_last`, `runner.py:1426` + post-loop
   `mine_weaknesses_from_outputs` → `_store.record`): non-last phase gaps re-enter
   the in-run ledger (retried THIS run); last-phase gaps are NOT re-queued → they
   stay unsolved → mined from the final output → persisted to the weakness DB for
   the NEXT run. A weakness that got solved is absent from the final output → not
   mined → not written back → it drops off. This is the cross-epoch hand-off.
2. **Document-merge prompt** (`if is_last and upstream`, `runner.py:1104`): shapes
   the last-phase WORKER prompts (a different stage than the injected reducer,
   which consolidates worker drafts) — so it is not redundant with §4.5's reducer.
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

### 11.10 Hardening pass (2026-06-27) — live-run-surfaced fixes

A GUI hill-climb run surfaced a chain of *carried-forward-state* defects. Each
fix below was correct in isolation yet exposed the next, because hill-climb
propagates state (artifact + weaknesses) across runs — so forward-generation
fixes are not enough; inherited state must be sanitized/normalized too.

- **Score = solved / total over the weakness set, SEMANTIC matching**
  (`runner._weakness_score`, `studio/runner.py`). The old LLM self-eval emitted
  impossible values (rated a good report 0.1). The weakness-ratio replaced it —
  but matching by normalized STRING counted a weakness the miner merely re-worded
  ("no comparative metrics" → "no systematic ranking") as *solved*, inflating the
  score on an UNCHANGED artifact. Matching is now embedding-cosine (≥0.85): a
  prior weakness is solved only if NO open weakness is semantically similar; an
  open weakness is "new" only if it matches no prior. `no weakness ⇒ 1.0`. Falls
  back to string match without an embedder. Verified on the unchanged v26→v27:
  0.44 (string) → 0.20 (semantic).
- **Artifact preamble sanitizer** (`runner._strip_preamble`). A reducer that
  prepended commentary ("The artifact is complete… Weaknesses addressed: ✅…
  Remaining concern:") poisoned `artifact.md`; the grow-only ratchet then LOCKED
  it into the seed forever (a clean-up that shortens the doc reads as a
  regression), so the poison propagated byte-identical across runs. Strip
  everything before the first `#` heading at every artifact boundary (seed copy —
  resets `_seed_len` to the clean baseline — reducer read, write-back). That
  commentary belongs in the chat's surfaced `_unresolved_block`, never the doc.
  Self-heals a poisoned seed on the next load.
- **Date awareness** (`runner._today_note`). Agents were date-blind and flagged
  current-year sources as "future-dated" credibility problems. Inject today's
  date into hub/worker/reducer prompts (a per-run constant — a tool call would be
  a wasted round-trip).
- **Reducer output hygiene.** The §4.5 section reducer prompt forbids any
  preamble/status/checklist — emit only the document, starting at its first
  heading.
- **Section-scoped `read_artifact`** (`studio/tools.py`). The tool returned the
  full ~38K artifact on EVERY call; agents called it 26+× per phase → ~1M input
  tokens. It now returns a cheap SECTION INDEX (`[{section, hash, chars}]`) with
  no args and ONE section's body+hash with `section='## Heading'` (deterministic
  `_split_sections`, 0 LLM). Per-section hashes let an agent re-read a section
  only when it changed; the agent self-dedups via hashes in its own context.
  Measured: index ≈ 1398 chars vs a 38121-char dump (27×). `web_fetch` dedup was
  *rejected by design* — its cache already serves each agent the content it needs;
  stubbing a repeat would starve a cross-agent reader.
- **DAG sync (frontend).** (1) `read_artifact x26` no longer balloons tokens.
  (2) Agent count is emitted at `phase_start` (planned = sizing cap + 1), so the
  DAG shows agents as RUNNING up front instead of a default-3 guess that only
  corrected — already settled — at `phase_done`. (3) A run-status badge
  (`running` / `finalizing — verify · score · improve`) stays until the terminal
  `done` event, so the diagram never reads complete while post-phase work runs.
  (4) The DAG legend pills derive from the live `s.phases` (same source as the
  diagram nodes), not the durable GraphStore's own status, which drifted out of
  sync. (5) A running node uses a SOLID glowing border (dashed read as inactive
  for the hub/reduce role markers); late-mounted spoke nodes reveal-animate.

---

## 12. Persistence — `task_runs.db` (cross-session hill-climb store)

A single SQLite file (`backend/tmp/task_runs.db`) is the entire durable state.
One row per *run* of a *task*; it is what makes hill-climb work across sessions.

```sql
CREATE TABLE task_runs (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  task_hash             TEXT    NOT NULL,           -- sha256(requirement.strip().lower())[:12]
  session_id            TEXT    NOT NULL,           -- the run that produced this row
  version               INTEGER NOT NULL,           -- 1,2,3… per task_hash (the hill-climb epoch lineage)
  score                 REAL    NOT NULL,           -- §11.10 weakness-ratio: solved/total, 1.0 = no weakness
  weaknesses_json       TEXT    NOT NULL DEFAULT '[]', -- ["[## Section] gap", …] section-tagged (§11.4)
  artifact_path         TEXT    NOT NULL DEFAULT '',-- workspace artifact.md for this run
  requirement           TEXT    NOT NULL DEFAULT '',-- the original task text
  created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
  result_text           TEXT    NOT NULL DEFAULT '',-- the deliverable shown in chat (preamble-stripped, §11.10)
  requirement_embedding BLOB                        -- vector for R10 cross-task similarity (§2.4)
);
```

**Design rules.**
- **`task_hash` is the lineage key.** The SAME requirement (case/space-normalized)
  shares a hash across sessions, so a later run finds the prior artifact + its
  weaknesses and improves them — that IS hill-climb. Change the wording → new
  hash → fresh lineage.
- **`version` is monotonic per `task_hash`.** `next_version(task_hash)` = max+1.
- **Seed = LATEST-with-content, not best-score** (`latest_with_content`). Self-eval
  scores were noisy; the latest run carries the most accumulated work (the weakness
  ratio is more trustworthy now, but latest is still the seed). The artifact is
  sanitized (`_strip_preamble`) on seed so inherited corruption can't propagate.
- **`requirement_embedding`** powers R10 (`similar_runs` / `accumulated_weaknesses`):
  weaknesses from *semantically similar* prior tasks carry forward, not only the
  exact `task_hash`. No-op without an embedder.
- **Store is `TaskRunStore` (`studio/task_runs.py`).** Pure SQLite + numpy cosine;
  no ORM. `record()`, `all_runs()`, `latest_with_content()`, `repeat_failures()`,
  `accumulated_weaknesses()`, `next_version()`.

## 13. Driving a run programmatically (the SSE API)

The GUI is one client; the same three calls drive a run from any script (used for
headless E2E / verification). All against the backend on `:8770`.

1. **`POST /session`** → `{session_id}`. Body:
   ```json
   {"llm": {"profile": "haiku"}, "embed": {}, "mode": "llm",
    "budget": {"ceiling": null}, "tools_enabled": true,
    "loop_config": {"auto_improve": true, "max_agents": 5,
                    "min_tasks_per_agent": 3, "max_tasks_per_agent": 5}}
   ```
   `mode`: `"llm"` (epic planner) or `"auto"` (deterministic). `tools_enabled`
   gates web_search/web_fetch — **off ⇒ the loop fabricates** (no real sources).
2. **`POST /session/{id}/hill-climb`** (optional) — `{auto_improve, max_epochs,
   min_improvement, score_metric, max_agents, min_tasks_per_agent,
   max_tasks_per_agent}`. The Agent-Sizing sliders ride here too and are synced
   into `loop_config` (the runner reads sizing from `loop_config`, not
   `hill_climb_config`).
3. **`GET /run/{id}?requirement=<urlencoded>`** → an **SSE stream** (the ordered
   event contract, SPEC §4: `session → plan → topology → graph → (per phase:
   phase_start, router, token…, phase_done) → verify → loopdoctor → hill_climb →
   done`). Optional `history` = JSON `[{role,content}]` for multi-turn.

> **GOTCHA — drain the stream fully.** The runner records the row *as the SSE
> stream is consumed*. A client that disconnects early (e.g. `urllib` raising
> `IncompleteRead`, or closing on the first `done`) cancels the server-side
> `StreamingResponse` generator → **the run may never record to `task_runs.db`**.
> A browser `EventSource` holds the connection open and is fine; a script MUST
> read every line until the server closes the stream.

```python
# minimal headless driver (stdlib only)
import json, urllib.request, urllib.parse
B = "http://localhost:8770"
def post(p, body):
    r = urllib.request.Request(B+p, json.dumps(body).encode(),
                               {"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(r).read())
sid = post("/session", {"llm": {"profile": "haiku"}, "embed": {}, "mode": "llm",
           "tools_enabled": True, "budget": {"ceiling": None},
           "loop_config": {"auto_improve": True, "max_agents": 5}})["session_id"]
post(f"/session/{sid}/hill-climb", {"auto_improve": True, "max_epochs": 2, "max_agents": 5})
q = urllib.parse.quote("your requirement here")
with urllib.request.urlopen(f"{B}/run/{sid}?requirement={q}", timeout=1800) as r:
    for line in r:            # <- drain EVERY line or the run won't record
        pass
```

---

## 14. Decision Log & Changelog (2026-06-28)

*Chronological as-built decisions and changes elaborating §11 (hill-climb). Numbered separately from the §11 architecture spec above.*

### 14.1 Loop-Engineering Closure — Decision Log

Context: an evaluation of Studio against the loop-engineering literature found the
self-improvement path was an **open loop** — it accepted every epoch's artifact as long
as it was not *shorter* (a length-only ratchet), with no quality keep/discard. The
genuine optimizer (`agentkit.evolve.optimize_text` / `loop.hill_climb`) was **not wired
in**; Studio reimplemented a thinner version. Decisions below, each grounded in a test.

#### D1 — task_hash identity is the BASE requirement (goal-invariant)
`runner._run_inner` prepended the goal/constraints block into the requirement BEFORE
hashing, so attaching a goal forked the hill-climb lineage → cold-start v1, no artifact
carry-forward, weakness-score `0/N = 0.00`. **Decision:** hash `_base_requirement`
captured before the goal block (and before the per-iteration prefix). Verified: the good
lineage `find the most popular…report` already hashes to `4ca9b03811b7`; a goal-attached
run now rejoins it. Guard: `tests/test_runner.py::test_task_hash_invariant_to_attached_goal`.

#### D2 — Epoch keep/discard gate (close the open loop)
**Decision:** new module `studio/epoch_gate.py` (`accept_epoch`, `make_preference`). At
the epoch boundary the new artifact is KEPT only if a **label-free judge strictly prefers
it over the seed** (prior best); otherwise the prior is restored. Worst case = prior good
report retained → quality cannot regress. The gate never reads the absolute score (which
is noisy and has changed across versions); it reuses `agentkit.evolve.self_preference`
(RHO pairwise preference). Placed in its own module to avoid growing `runner.py` (see D5).
Guards: `test_accept_epoch_keep_discard_gate`, `test_accept_epoch_with_real_reports`.

#### D3 — Hardened `self_preference` parsing (agentkit modified)
**Real-report test finding:** with the strict `{"winner":…}` JSON parser, the judge tied
a 58 KB good report with a 4.5 KB stub in BOTH directions — because haiku/sonnet answer in
markdown prose ("**Artifact A is significantly better**") and `json.loads` failed →
silent TIE. **Decision:** `agentkit/evolve/core.py::_extract_winner` resolves a verdict via
trailing `VERDICT:` line → embedded JSON → prose → TIE, and `_PREFERENCE_SYSTEM` now asks
for a trailing `VERDICT:` line. The judge's *judgment* was correct all along; only
extraction was lossy.

#### D4 — OPEN: LLM pairwise preference is an unreliable gate; use a STRUCTURED rubric
**Findings (live, real 58 KB good vs 4.5 KB thin report fixtures):**
- haiku, strict-JSON parser → TIE (D3 parser bug: judge said "A better" in prose).
- haiku, hardened parser + rubric → TIE (model *hedges*: "neither acceptable").
- **sonnet, hardened parser + rubric → TIE.**

Conclusion: asking an LLM "which full report is better?" is **not a reliable keep/discard
signal even on a strong model with a rubric** — it hedges to tie. This matches the
literature ("LLM-as-judge can be gamed or collude; put a DETERMINISTIC check in wherever
one exists"). A deterministic proxy (verified-source density) *does* separate the two
cleanly (`test_accept_epoch_with_real_reports`).

**Decision/direction:** the scoring standard and the gate's `prefer` fn should be a
**structured rubric**, deterministic where possible:
  * deterministic: # of VERIFIED source URLs (cross-checked vs web cache), citation
    density, methodology section present, structure/section completeness, length band;
  * LLM only PER-CRITERION (not "which is better overall"), and ask for the verdict
    token FIRST so a max_tokens cap can't truncate it.
This same rubric defines the research-report **template** the loop targets. `self_preference`
stays available but is NOT the default gate judge for large artifacts. (Supersedes the
earlier "self_preference is the gate" plan — disproven by the three TIE results above.)
The `studio/epoch_gate.accept_epoch` machinery is unchanged; only the injected `prefer`
implementation changes (`make_preference` → a rubric scorer diff).

#### D5 — Follow-up: `runner.py` is too large
`runner.py` (~2.1k lines) should be decomposed (record/scoring block, seed/auto-improve
block, reduce/writeback block → modules). Tracked, not yet done; D2 logic was deliberately
placed in `studio/epoch_gate.py` rather than added inline to avoid making it worse.

#### Reuse vs. rebuild
Reused as-is: `loop.goal.check_goal` (deterministic verifier), `loop.chain.LoopChain`
(goal-gated driver), `evolve.self_preference` (D2 gate). Modified in agentkit: `self_preference`
parsing (D3). Not yet wired (planned Phase 3): `optimize_text` to own the epoch loop +
autonomous heartbeat, replacing Studio's hand-rolled single-epoch advance.

---

### 14.2 Research-report rubric — scoring standard + deliverable template

Resolves §14.1 D4. The keep/discard gate's default judge is now a **deterministic
research-report rubric** (`studio/rubric.py`), not an LLM preference. Synthesized from web
research on report-quality standards:
- DEER (arXiv:2512.17776) & DeepResearch-Bench (arXiv:2506.11763): deep-research report
  quality = completeness / correctness / helpfulness, scored per concrete criterion.
- CRAAP test (Currency, Relevance, Authority, Accuracy, Purpose) — source credibility.
- Academic report rubrics (sourcing, evidence depth, methodology, structure).

#### Criteria (default weights, GUI-tunable)
| Criterion | Weight | Signal (deterministic) |
| --- | --- | --- |
| sourcing | 0.25 | # distinct cited source URLs (target 8) |
| verification | 0.25 | # URLs confirmed real via web cache (`verified_urls_in_cache`) |
| evidence_depth | 0.20 | direct quotes / blockquotes density |
| structure | 0.15 | fraction of **template** sections present (else summary+conclusion+headings) |
| methodology | 0.15 | methodology/scope present + non-thin body (word floor) |

`rubric_score(text, verified_urls, weights, required_sections) -> [0,1]`, weighted sum;
`resolve_weights` L1-normalizes a partial GUI override and drops unknown keys.

#### Verified result (the point)
On the real fixtures the live LLM judge tied (haiku & sonnet), the rubric scores
**good = 0.925 vs thin = 0.4531** — a clean, reproducible separation. Guarded by
`tests/test_rubric.py` (5 tests) + `test_accept_epoch_with_real_reports`.

#### Rubric is GUI-input + attached to a deliverable template
- `Session.rubric_config = {"weights": {criterion: float}, "template": [section, ...]}`.
- API: `POST /session/{id}/rubric` (set), `GET /rubric/defaults` (seed the panel).
- The **template** (`DEFAULT_TEMPLATE`: Executive Summary, Key Findings, Evidence and
  Analysis, Source References, Methodology, Conclusion) is dual-use: it defines the
  deliverable's expected sections AND drives the `structure` criterion (coverage).
- The runner gate reads `session.rubric_config` → `make_rubric_preference(verified_urls,
  weights=…, required_sections=…)`. Falls back to defaults when unset.

#### Frontend rubric panel — BUILT (2026-06-28)
A "rubric" tab in the Loop Config dialog (`LoopConfigPanel.tsx`):
- Seeds from `GET /rubric/defaults`, renders a slider per criterion and an
  editable template section list, POSTs `{weights, template}` to
  `POST /session/{id}/rubric`. Stores locally via `runStore.configuredRubric`
  (preserved across `beginRun`, mirrors goal/hill-climb).
- **No hardcoded criterion keys** — the panel iterates whatever the defaults
  endpoint returns (`Object.entries(rubricWeights)`), so a new backend criterion
  needs zero frontend change. Weights normalized server-side (`resolve_weights`).
- Typechecks clean (only pre-existing `TS2882` CSS side-effect import warnings).

#### Template → GENERATION wiring — BUILT (2026-06-28)
The deliverable template now STEERS generation, not just scoring. In
`runner._run_inner`, when `session.rubric_config["template"]` is set, the section
list is appended to `requirement` ("Structure the deliverable with these sections…")
**after** `_base_requirement` is captured — so it never changes `task_hash`. Injected
**only when explicitly configured**: defaulting to `DEFAULT_TEMPLATE` would force
report headings (Executive Summary, Methodology…) onto non-research tasks and
compromise their generation quality (the no-compromise constraint). Guarded by
`test_rubric_template_steers_generation` (asserts sections reach the `plan` event's
task when set, absent when unset). 51 backend tests pass.

#### Structure matching — concept-aware (FIXED 2026-06-28)
`structure` now counts a required section as covered if the exact phrase appears OR a
heading shares a content word with it (`_content_tokens`). WHY: the DB report skeleton
(`studio/templates.py`, a real good report) scored only **0.5** against the default rubric
template under exact-substring matching — its headings used real-world synonyms ("Verified
Sources" for "Source References", "Core Finding" for "Key Findings"), so a genuinely good
report was penalised for vocabulary, not for a missing section. After the fix the same
skeleton scores **structure = 1.0**; good fixture stays 1.0 and thin stays 0.478 (its
structure rises to ~0.83 via loose token overlap, but structure is only 0.15 weight and
sourcing+verification+evidence = 0.70 correctly tank the thin total — separation intact).

#### Two distinct "template" mechanisms (do not confuse them)
There are TWO templates with overlapping purpose; the structure fix above reconciles them:
| | **Rubric template** (`rubric.DEFAULT_TEMPLATE` / `rubric_config.template`) | **DB skeleton** (`studio/templates.py`, table `report_templates`) |
| --- | --- | --- |
| Shape | flat list of ~6 canonical section NAMES | full heading TREE (40+ nested `#`/`##`/`###`) extracted from a real report |
| Source | authored / GUI-edited (the agreed deliverable shape) | learned — `extract_skeleton(result)` saved when `_score ≥ 0.6` (runner:2073) |
| Keyed by | nothing (one set per session) | requirement embedding (cosine match, `find_template`, threshold 0.6) |
| Used for | `structure` scoring + generation steering (§14.2 #2) | seed the FIRST document's structure (runner:478), per-requirement |

Both now steer report STRUCTURE, so they are competing signals: the skeleton says "Verified
Sources", the rubric injection says "Source References". Concept-aware `structure` matching is
what lets a report generated from the (good) DB skeleton still score 1.0 against the rubric
template — they no longer have to share exact heading vocabulary, only the concepts. Empirical
check: the one stored skeleton went 0.5 → 1.0 against `DEFAULT_TEMPLATE` after the fix.

#### Windowed-judge false "missing section" — moving-window miner + section filter (2026-06-28)
SYMPTOM: V36 of the loop-engineering report (`result (12).md`, 64,389 chars) recorded
weaknesses "Required section 'Methodology'/'Conclusion' is missing" although both exist
(`## Methodology` @ char 54,764, `## Conclusion` @ 55,929). ROOT CAUSE: the LLM scorer
(`score_result`, 20K window) and miner (`mine_weaknesses_from_outputs`, old 8K-head + 4K-tail)
never saw the document's MIDDLE/tail, so they reported present sections as missing — the same
window-blindness class as the old "verified URLs flagged as fabricated". The #2 template
injection amplified it by naming the exact sections to hunt for.

FIX (two parts):
1. **Moving-window miner** — the miner now sweeps the FULL document in overlapping ~12K
   windows (step 10K, ≤8 windows), mining each and union-deduping. For the 64K report that's
   7 windows; `## Methodology`/`## Conclusion` land in window 5 (chars 50K–62K), previously
   invisible. Small docs (≤12K) still take ONE call (no added cost). Sizing constants
   `_MINE_WINDOW/_STEP/_MAX_WINDOWS/_MAX_WEAKNESSES` in `task_runs.py`.
2. **Deterministic section filter** (`runner._run_inner`) — the scorer STILL windows at 20K
   and its UNMET is fed to the miner as a seed, so the false claim echoes through even with the
   moving window. After mining, any missing/absent-section weakness for a section that
   `rubric.sections_present` (concept-aware, full text) confirms present is dropped.

VERIFIED on the real report with a live haiku judge: the 4 false "missing section" weaknesses
were dropped; 4 genuine ones remained (popularity ranking incomplete, thin synthesis, citation
redundancy, indefensible "most popular"). `rubric_score` (full-text) = 1.0. Guards:
`tests/test_miner_window.py` (3) + `test_sections_present_is_concept_aware_over_full_text`.

RESIDUAL (follow-up): `score_result` itself still windows at 20K — its discarded SCORE is
harmless, but its UNMET feedback pollutes the miner (the filter cleans the section part only).
Candidate fix: give the scorer the same full-text section/truncation oracle, or moving-window it.

#### Semantic weakness dedup + why solved/total "regresses" (2026-06-28)
OBSERVED: the loop-engineering task (`4ca9b03811b7`) recorded scores that oscillate while the
artifact grows monotonically — v25 0.80 (38K chars) → v36 0.67 (64K chars). Same task, BIGGER
and more complete document, LOWER score. This is NOT a quality regression: `_weakness_score =
solved/total` over a ~5-item LLM-mined set measures issue churn between epochs, not absolute
quality. With ~5 items, one extra unsolved weakness swings the score 0.13–0.20, so the metric
jitters on miner noise. The deterministic `rubric_score` of v36 = 1.0.

Two amplifiers, both fixed:
1. **Duplicate weaknesses** — the miner surfaced the SAME popularity-ranking gap under two
   section prefixes (`[## Source Selection…]` and `[## Key Findings]`); exact-string dedup kept
   both → two unsolved items depress solved/total. Added **semantic dedup** in
   `runner._run_inner` (cosine ≥ 0.85, the same threshold `_weakness_score` uses; keeps the
   first). VERIFIED with BGE-M3: the two popularity weaknesses cos = 0.948 → collapse; the
   distinct citation-redundancy weakness cos = 0.765 → kept separate.
2. The moving-window miner finds MORE real tail weaknesses than the old head+tail, which can
   push solved/total DOWN even as the document improves — the paradox of a count-based score:
   better verification lowers it. This is the core argument for making `rubric_score` (not
   solved/total) the recorded/convergence metric — see the decoupling gap below.

#### Rubric is now the recorded score (solved/total retired, 2026-06-28)
RESOLVED the decoupling: `rubric_score` (deterministic, full-text) is now the score RECORDED
(`task_runs.score`), the hill-climb delta/convergence signal, the template-save gate
(`≥ 0.6`), AND the epoch keep/discard judge — one metric end to end, no more gate-vs-score
disagreement. `_weakness_score` (solved/total) is retired from the main path: it punished
thoroughness (more mined weaknesses → lower score even as the doc improved) and rewarded an
empty doc (no weaknesses → 1.0). Computed in `runner._run_inner` AFTER the keep/discard gate
so it scores the artifact actually kept, from the clean `_scored_text` (before any annotation).

Weaknesses are still mined (moving-window) — they are the IMPROVEMENT SIGNAL that seeds the
next run's constraints — but no longer determine the score. They are surfaced BELOW the report
in the result view via `HillClimbEvent.weaknesses` (frontend `ResultWindow` renders them as a
separate block); they are NEVER concatenated into `result_output`, so the deliverable document
(saved / downloaded / recorded as `result_text` / next-run seed) stays clean.

VERIFIED on `result (12).md` with live haiku + BGE-M3: recorded score = rubric **1.0** (was the
noisy 0.67 solved/total); remaining weaknesses = 2 distinct real issues (popularity ranking,
citation redundancy) after the duplicate pair collapsed via semantic dedup. 274 backend tests
pass; frontend tsc clean.

### 14.3 Substantiation levers + non-additive structure

Two sibling efforts landed against the same hill-climb run. The **additive**
levers raise grounding; the **structural** mechanisms attack the score ceiling
the levers cannot move.

#### Substantiation Levers 1–3 (additive) — `agentkit` shared libs + runner

| Lever | Mechanism | Effect |
|---|---|---|
| L1 — worker substantiation | Executor emits `RESEARCH_FINDING` blocks only (report prose forbidden); tool loop 5→8 iters + a forced tools-disabled synthesis turn on iteration exhaustion | Worker no longer cut off mid-`tool_use` returning only a preamble |
| L2 — patch-based reducer | Parser accepts a bare `RESEARCH_FINDING` (was `##`-required → 0 patches, the load-bearing no-op); missing-anchor inserts demoted to clean appends | Findings actually reach the document instead of silently dropping |
| L3 — woven evidence | Dual-oracle grounding (URL-fetched **OR** quote-verified), tolerant URL match + fuzzy quote match; fetch-density prefetch of cited-but-uncached URLs | Findings surviving a phase went **2 → 26**, doc **+7.4K**; real fetched sources stop being exact-match dropped |

Plus **copy-paste-verbatim refinement**: the source's own words are the
evidence — the fabrication-prone CLAIM-rephrase step is dropped.

> **Verified finding (v29..v33).** Grounding throughput went broken → 26
> findings/phase and the doc grew 38K → 46K, but the score held at **0.64–0.67**.
> The binding constraint is **not grounding** (the handoff's premise) but
> **structure** — ranking / metrics / completeness and inherited truncation —
> which additive levers cannot fix by construction.

#### F4 — honest ranking synthesizer (`agentkit.artifacts.ranking`)

`synthesize_ranking_table(findings, metrics)` replaces the source-selection
section with an **honest SPLIT presentation**:

- A **Measured popularity** table ranks only sources with a real,
  independently-verifiable metric (citations, stars).
- A separate **Reported / unranked** listing holds sources with only a stated
  claim or no public number — the two are **never ranked together** (mixing a
  citation count with a view count is apples-to-oranges an evaluator flags).
- It **never invents a metric**: a source with no public number shows `—`. A
  mostly-`—` table with marked gaps is the CORRECT output when metrics genuinely
  do not exist, not a failure to paper over. A leading one-line methodology note
  states how many sources are actually measurable.

Numbers trace to source: a fetched citation/star count, a stated-but-`reported`
claim (`parse_stated`, never re-derived), or in-corpus reference frequency.
Backed by `agentkit.artifacts.metrics` (`Metric`); wired in `runner.py` as
`_apply_ranking(doc, findings)`.

#### F2 — per-section ratchet (`agentkit.artifacts.sections`)

`accept_rewrite` relaxes the writeback ratchet from whole-document grow-only to
**per-section grow-only**: a reviser may REPLACE one section (repair, ranking
table, dedup) even when net length shrinks, while still guaranteeing no sourced
section is deleted. `split_sections` is the deterministic `##` split (0 LLM)
that keys the per-section hashes.

### 14.4 Epoch heartbeat — one Run auto-iterates to `max_epochs` (2026-06-28)

**Problem.** `max_epochs` was a dead label. Each Run did exactly ONE pass:
`_run_inner` produced a document, scored it, recorded v+1, emitted a
`HillClimbEvent` whose `status` flipped to `converged` only when the DB version
count happened to reach `max_epochs` — and **nothing consumed that status to run
another pass**. Reaching `max_epochs` required the user (or a driver script) to
press Run repeatedly. `hill_climb_config` also lived only in memory on `Session`,
so it was lost on every backend restart.

**Design — drive the loop in `run()`.**

1. **Loop location.** `run(requirement)` wraps `_run_inner` in an epoch loop;
   `_run_inner` is refactored to RETURN its per-epoch outcome
   (`EpochResult(version, score, delta, status)`) instead of emitting the
   terminal `done` itself. `run()` decides continue/stop and emits the single
   terminal `done` AFTER the loop. One SSE stream spans all epochs; the existing
   `HillClimbEvent(epoch=version, …)` per pass is what the frontend timeline
   already renders.

2. **Carry-forward (unchanged).** Each pass seeds from the prior via the existing
   `auto_improve` path (`latest_with_content` → prior `artifact.md` + weaknesses).
   Because pass N records its artifact before pass N+1's seed-lookup, epoch N+1
   **improves epoch N's document incrementally** — it is NOT a from-scratch
   rebuild. The per-section ratchet (§14.3 F2) guarantees no regression across
   passes.

3. **Early stop = plateau.** After each pass `run()` breaks when:
   - `status == "plateau"` — `version > 1 and delta < min_improvement` (the gain
     no longer pays for the tokens), **or**
   - `status == "converged"` — `version >= max_epochs`, **or**
   - the run was cancelled.

   The loop always runs **≥ 1** pass. `min_improvement` (default 0.02) is the
   plateau threshold; both it and `max_epochs` come from the hill-climb config.

4. **Re-entrancy — per-epoch step-id namespacing.** `_run_inner` emits DAG /
   phase events keyed by `step_id`; replaying it in-process would collide ids and
   corrupt the frontend graph. Each epoch prefixes its step ids with the epoch
   index (e.g. `e2:s3`) so every pass is a distinct sub-DAG; `HillClimbEvent.epoch`
   keeps the score timeline unambiguous. The graph store is reset per epoch.

5. **Guard / back-compat.** The loop engages only when `auto_improve` is on AND
   `max_epochs > 1`. Otherwise `run()` does a single `_run_inner` pass exactly as
   before — non-hill-climb tasks are untouched.

**Persistence — per-task in `task_runs.db`.** Add a `config_json` column to
`task_runs` (`ALTER TABLE … ADD COLUMN`, same migration pattern as
`requirement_embedding`). `record()` snapshots the hill-climb config used for
that run; `TaskRunStore.latest_config(task_hash)` returns the most recent
snapshot. On a new run with `auto_improve` and **no** explicit session config,
the runner seeds the config from `latest_config(task_hash)` — so a requirement
remembers its own epoch budget across sessions and **survives a backend
restart**, keyed by the same `task_hash` lineage the artifact carry-forward uses
(§12). Config is captured at the same point as `_base_requirement` so attaching a
goal/template never forks the task identity (§14.1 D1).

### 14.5 Local-model format tolerance + topic-agnostic skeleton (2026-06-28)

**Local (oMLX) models speak a different format than the harness assumed.**
`qwen2.5-coder` emits tool calls as fenced JSON (` ```json {"name","arguments"} ``` `)
and findings as JSON objects — not the `<tool_call>`-tagged / native `tool_calls`
and plain `RESEARCH_FINDING:` lines the parsers expected. A live Pi/Craft run with
the qwen worker therefore `fetched=0`, grounded nothing, and degraded to
refusal-prose (score ~0.24), while a Haiku probe on the *same* task fetched 11
pages and grounded the topic immediately — i.e. the run's "these frameworks can't
be verified" conclusion was a **capability failure dressed as principle**, not a
real input problem. Two parser fixes restore tolerance:

- `_parse_inline_tool_calls` (`studio/tools.py`) now parses **fenced/bare JSON
  tool calls** in addition to `<tag>` blocks, guarded by registered-tool names so
  stray JSON can't fire a tool.
- `_parse_findings` (`runner.py`) now parses **JSON-wrapped `RESEARCH_FINDING`**
  (fenced/bare, incl. a `RESEARCH_FINDING` wrapper key) alongside the plain-text
  format, through the same grounding oracle.

Verified end-to-end: qwen `fetched 0 → 1`, the Pi/Craft run **0.236 → 0.80**.

**Topic-agnostic skeleton.** That report drifted to a *"Loop Engineering"*
title/ToC: `_build_skeleton` (a) LLM-derived goal-specific headings, (b) reused a
semantically-similar **saved** skeleton (a prior loop-eng report) verbatim, and
(c) used the goal text as the title — the topic leaked from the template, not the
content. `_build_skeleton` now emits a **FIXED high-level, topic-agnostic ToC**
(`rubric.DEFAULT_TEMPLATE`) for every goal with a content-derived title
placeholder; no LLM call, no template-reuse for structure (deterministic +
outage-robust). `DEFAULT_TEMPLATE` expanded to the standard research-report
sections: Executive Summary, Background and Scope, Key Findings, Evidence and
Analysis, Methodology, Limitations and Open Questions, Conclusion and
Recommendations, Source References — generic, covering every rubric criterion.

**Backend menu.** `gemma-4-26B-A4B-it-heretic-4bit` (oMLX :8000) registered in the
`PROFILES` dropdown via `shared_bridge.py` (Studio-side; the shared lib is
untouched).

**Duplicate phases — goal injection, NOT the planner.** A goal whose `end_state`
overlaps the requirement produced duplicate phases (the Pi/Craft run: `"Craft…"` /
`"create a report"` twice). The runner prepended `Goal: {end_state}` to the
requirement for steering; when that ≈ the task, the planner split the **doubled**
text on "and" into duplicate phases. Proven deterministically:
`plan(goal+requirement)` → **5 dup phases** vs `plan(requirement)` → **3 clean**.
*Every* planner (deterministic, gemma-epic) is clean on a clean input — the bug
was the input, not the renderer.

**Fix:** the planner now reads `_plan_requirement = base + deliverable-template`
(**no goal**). The goal still steers via the worker goal + keep/discard gate +
verification; the template stays in the planner input (distinct section names
never duplicate the task, so rubric-template steering is preserved). Two earlier
fixes were upstream-blind and are kept only as belt-and-suspenders: content-dedup
inside `_plan_from_epics` (LLM path only — a seeded/auto run still duplicated),
then a path-agnostic `_dedupe_plan_steps` at the choke point after all planner
paths converge (right placement, but exact-match can't collapse the *near*-dups a
slightly-different goal yields). A planner-prompt "each phase must be DISTINCT"
line is also defense in depth. **Lesson:** reproduce the exact input the failing
run *constructs* — the cause was an input transform (goal prepend), upstream of
every layer first patched.

### 14.6 Hill-climb regression — the keep/discard gate was keyed on a file that usually doesn't exist (2026-06-28)

**Symptom.** Enabling hill-climb made output *worse*: a cross-session re-run of
the Pi/Craft task served a 0.122 / 28-line stub over the prior 0.4148 / 122-line
report. The keep/discard gate (§14.1) exists precisely to forbid this — yet the
regression was recorded and downloaded.

**Root cause — "never copied because never written," not "deleted."** The gate
only fires `if _artifact_copied and _seed_text.strip()`. Both came **solely from
the prior session's on-disk `artifact.md`**. But `artifact.md` is a *transient
working file* written only on the reducer/patch path; the *durable* per-session
deliverable is `result.md` (== the DB `result_text`, recorded for every run). A
raw-synthesis run (e.g. an oMLX model that dumped findings instead of patching
sections) finalizes `result.md` but **never writes `artifact.md`**. Evidence: of
1524 workspaces only 62 had `artifact.md`; the 0.80 runs had one, the regressed
0.12–0.41 runs did not; no `unlink`/`rmtree`/`rename` of it exists in studio, and
the prior session dir was intact (held `result.md`). So the seed lookup found no
file → `_artifact_copied=False` → **gate skipped** → the regressed epoch was
served unprotected and became the carried-forward "latest."

**Fix.** Seed (and therefore the gate) now falls back to the DB `result_text`
when `artifact.md` is absent: `TaskRunStore.latest_with_content` returns a run
whose `result_text` is non-empty even with no file, and the runner writes that
text as the current workspace's `artifact.md` seed (`_artifact_copied=True`,
`_seed_text` set). The gate fires whenever *any* prior exists; only a true cold
start (version 1) accepts unconditionally. Net: "worst case = prior good report
retained" holds across sessions/restarts/raw-synthesis runs, and cross-session
auto-improve actually accumulates (its documented intent). **Lesson:** a closed
loop guarded by a volatile precondition is an open loop most of the time — gate
on the durable record (the DB row), not the ephemeral artifact.

**Related defect — a no-seed run degraded BELOW a clean cold start.** Same task,
version 2: a prior *run* existed but its *doc* was never seeded (no `artifact.md`,
and at the time, no DB fallback). The weakness/"patch-or-silent" worker contract
was injected under `if _prior:` (a prior run exists), not `if _artifact_copied:`
(a doc was seeded). So v2 was told *"find missing data, emit RESEARCH_FINDING
patches targeting sections in the artifact, find nothing → output NOTHING"* — with
no artifact to patch. A weak model went silent → the reducer kept nothing → a
28-line scrap (0.12) **below** the clean cold-start v1 (0.41). **Fix:** gate the
edit contract on `_artifact_copied`, not `_prior` — no seed ⇒ generate cleanly,
weaknesses still steer the planner softly. (With the DB-fallback above,
`_artifact_copied` is now true whenever any prior has `result_text`, so this is the
belt to that suspenders.)

**Related defect — hill-climb could not CREATE a missing section.** The full
template skeleton (every required heading) is laid down only `if not
_artifact_copied` — i.e. only on a cold start. A *seeded* run inherits the seed's
structure; the reducer PATCHES existing headings (`PATCH_TARGET: <heading in the
artifact>`) and never injects a new one. So a rubric-template section absent from
the seed (e.g. "Limitations and Open Questions") was mined as a weakness *every
epoch* yet never created — there was no heading to patch. **Fix:**
`_merge_missing_sections` appends each absent template section as an empty heading
+ placeholder before the phase loop, so the additive pipeline has a target to fill.
Concept-aware (`sections_present`) so a renamed-but-present section is not
duplicated; no-op on cold start (the skeleton already has every section).

**Related defect — the loop GROWS but never REPAIRS (append-only in practice).**
A malformed mermaid edge / truncated code block introduced once survived every
epoch. Two layers: (1) the reducer prompt is explicitly additive — *"never a
rewriter… PRESERVE every section VERBATIM… output length STRICTLY >= input"*; (2)
the weakness miner only names what is **missing**, never what is **malformed**, so
the defect was never even surfaced. (`accept_rewrite` at the code level already
permitted a content-preserving rewrite — the ban was in the prompt + the absent
signal, not the ratchet.) **Fix:** a deterministic validator `studio.artifact_lint`
(malformed mermaid edge, unbalanced code fence) emits the defect as a weakness —
surfaced in the GUI and seeded into the next run's constraints — and the reducer
prompt gains a NARROW repair exception ("fix ONLY these flagged blocks in place,
everything else verbatim") fed by linting the seed. **Lesson:** a loop only ever
fixes what its weakness signal can name; an additive optimizer needs an explicit,
bounded licence to repair, or defects become immortal.

**Related defect — episodic memory poisoned the prompt with the agent's own
refusals.** The MEMORY panel kept recalling worker refusals ("I appreciate you
sharing this, but I need to clarify… these appear to be duplicate statements of
intent…") at score ~0.84. Cause: `MemoryTracker` writes EVERY phase output to a
**shared** store (`workspace_root().parent/shared_memory.db`, one DB across all
runs — despite the "per-run" docstring) with **no filter**, and `recall()` injects
the top-5 by similarity into every later phase. So a refusal stored once resurfaces
at high similarity (it matches the very task that provoked it) in every future run
and primes the next worker to echo it — a persistent, cross-run poison loop.
Measured: `shared_memory.db` held 365 memories, **44 refusals (~12 %)**. **Fix:**
`studio.panels.memory._is_low_value_memory` drops refusal / clarification /
failure-narration openers in `record()`, and the 44 existing poison rows were
purged (365 → 321). **Lesson:** a memory that cannot distinguish a *finding* from a
*refusal* will, over runs, teach every agent to refuse — curating what ENTERS
memory matters as much as curating the report; the recall signal is only as clean
as the write filter.
