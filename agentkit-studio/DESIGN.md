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
Each worker emits a `PATCHES:` JSON block as suggestions.
A dedicated **Reducer** step (the final step of each phase) collects all
workers' suggestions, resolves conflicts, and performs one atomic write.

```
Phase N
  ├── Worker 1  →  PATCHES suggestion block (no file write)
  ├── Worker 2  →  PATCHES suggestion block (no file write)
  ├── Worker 3  →  PATCHES suggestion block (no file write)
  └── Reducer   →  collect all → resolve conflicts → atomic write → artifact.md
```

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
    llm_merge_fn=None,                   # optional: LLM call for complex conflicts
) -> ReduceResult:
    """
    Collect all patches, detect conflicts, resolve, return final text.

    1. Flatten patches preserving worker order (respects task assignment priority).
    2. For each patch: attempt apply on working_text.
       - Anchor found → apply, advance working_text.
       - Anchor missing → conflict: try llm_merge_fn if provided,
         else append with conflict marker.
    3. Deduplicate: skip patch if its content already exists verbatim in working_text.
    4. Return ReduceResult with final text and conflict log.
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

```
You are the Reducer for this phase. All worker agents have completed their tasks.

Step 1 — Read the current deliverable at {artifact_path}.

Step 2 — Read each worker's PATCHES suggestions (provided below).
  Worker 1 patches: {worker_1_patches}
  Worker 2 patches: {worker_2_patches}
  ...

Step 3 — Detect conflicts.
  For each patch, check if its anchor exists in the current text.
  For same-anchor patches from different workers, decide:
    - Both additive (inserts)? Concatenate.
    - One replaces, one inserts same anchor? Apply replace first, then re-check insert.
    - Anchor already removed? Note as conflict, append content with conflict marker.

Step 4 — Produce the final merged text.
  Apply all non-conflicting patches. For conflicts, append with a clear marker.

Step 5 — Emit the complete merged document as your output.
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

```python
# runner.py: parse EPIC_PLAN from planner output
epics = _parse_epic_plan(planner_output)

# Convert each epic to a Plan.step; branches become the TaskLedger seed
for epic in epics:
    step = Step(
        id=epic["id"],
        description=epic["description"],
        depends_on=tuple(epic["depends_on"]),
        topology=STAR,        # always fan-out within an epic
    )
    ledger.all_tasks.extend(
        TaskRecord(id=b["id"], description=b["description"])
        for b in epic["branches"]
    )
```

The hub for each epic receives `ledger.to_context_block()` showing which branches
were completed in prior epics and which remain for this epic.

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

`runner.py` carries one `TaskLedger` across all phases:

```python
ledger = TaskLedger(all_tasks=[], completed=[], in_flight=set())

for step in plan_obj.steps:
    # inject ledger + deliverable path into hub description
    desc = _inject_ledger_and_artifact(step.description, ledger, artifact_path)

    result = run_plan(sub_plan_with(desc), client, ...)

    # extract DONE markers and TASK_LIST from hub output
    new_tasks, done_ids = _parse_hub_output(sr.output)
    for t in new_tasks:
        if t not in ledger.all_tasks:
            ledger.all_tasks.append(t)
    for tid in done_ids:
        ledger.mark_done(tid)

    # extract and apply patches to artifact
    patches = _parse_patches(sr.output)
    if patches:
        apply_patches(artifact_path, patches)
```

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

def compute_n_agents(n_tasks: int, cfg: SizingConfig = SizingConfig()) -> int:
    """
    Derive agent count so each agent gets at most max_tasks_per_agent tasks.
    Last agent may receive fewer than min_tasks_per_agent (that is acceptable).

    Examples (max=5):
      n=3  -> 1 agent  (3 tasks)
      n=5  -> 1 agent  (5 tasks)
      n=6  -> 2 agents (5+1)
      n=10 -> 2 agents (5+5)
      n=11 -> 3 agents (5+5+1)
    """
    if n_tasks <= 0:
        return 1
    return max(1, math.ceil(n_tasks / cfg.max_tasks_per_agent))

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

`topology/core.py` STAR/MESH/MAP branches: remove explicit `n` parameter.
Receive `task_list` from hub output; call `compute_n_agents`:

```python
# Before (old):
return TopologyChoice(STAR, EXPLICIT, n=user_specified_n, ...)

# After (new):
from agentkit.topology.sizing import compute_n_agents
n = compute_n_agents(len(task_list), cfg=session.loop_config.sizing())
return TopologyChoice(STAR, DERIVED, n=n, ...)
```

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

Step 5 — Assign work items to agents.
  Rules:
    - Max {max_tasks_per_agent} items per agent
    - Last agent may receive fewer
    - No item assigned to more than one agent
  Emit TASK_LIST, ASSIGNED, and DONE blocks (JSON, as specified in §3.3).

Step 6 — Emit DELIVERABLE_PATH: {artifact_path}
  so all downstream agents know where to write patches.
```

### 5.2 Initial Hub (no prior deliverable — first run)

Same structure except Step 1 becomes:

```
Step 1 — No existing deliverable found.
  You will plan the creation of the first draft.
  Define the document structure: sections, their purpose, and the
  information needed to populate each section.
  List each section as a work item for Step 5 assignment.
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
    - Use exact anchor text from the CURRENT DELIVERABLE CONTENT above.
    - Each patch targets only sections you were assigned.
    - Do NOT rewrite sections assigned to other agents.
    - Prefer insert_after/append over replace — less conflict risk.
    - Use the PATCHES JSON format exactly (see §2.2).

Step 5 — Emit DONE markers for each task you completed.
  Format: DONE: ["task-id-1", "task-id-2"]

Step 6 — Emit your PATCHES block.
  The Reducer will collect all workers' patches, resolve conflicts,
  and perform the single atomic write.
```

---

## 6. Chat Window (replaces task textarea)

### 6.1 Frontend change

Remove `<textarea id="task-input">`. Add `<ChatPanel>` component:
- Multi-turn message thread (user + assistant messages)
- Submit sends full chat history as requirement context
- Assistant messages: goal suggestions, clarifying questions, confirmations
- `POST /session/goal` accepts `message_history: list[{role, content}]`

### 6.2 Backend flattening

```python
def flatten_chat_to_requirement(messages: list[dict]) -> str:
    """Concatenate messages into structured requirement context for the planner."""
    return "\n\n".join(
        f"[{m['role'].upper()}]: {m['content']}"
        for m in messages
        if m["role"] in ("user", "assistant")
    )
```

The planner receives this flattened string as its task description.
All prior refinements are visible to the planner, not just the last user message.

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
M1  agentkit.topology.sizing          compute_n_agents, assign_tasks, SizingConfig
M2  agentkit.orchestrator.ledger      TaskRecord, TaskLedger, to_context_block
M3  agentkit.artifacts.patcher        DocPatch, reduce_patches, write_artifact,
                                      cleanup_orphaned_tmp
M4  agentkit.artifacts.store          resolve_deliverable, latest_with_content
         depends on: M3
M5  agentkit.improvement.*            port TaskRunStore, score_result, mine_weaknesses
         depends on: M4
M6  agentkit.tools.fetch_cache        InFlightRegistry (phase-level URL dedup)
M7  studio/workspace.py               integrate M4; add resolve_deliverable
         depends on: M4, M5
M8  studio/runner.py                  epic plan parsing; phase loop: TaskLedger (M2),
                                      Reducer + patch apply (M3), deliverable resolve (M7),
                                      dynamic sizing (M1), InFlightRegistry (M6)
         depends on: M1, M2, M3, M6, M7
M9  studio/runner.py                  CoT hub + worker + Reducer prompts (§5, §2.2)
         depends on: M8
M10 studio/models.py                  LoopConfig with deliverable_path + sizing params
M11 frontend: ChatPanel               replace task textarea with multi-turn chat
M12 frontend: Loop config panel       deliverable_path field + sizing sliders
         depends on: M10
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
