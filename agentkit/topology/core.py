"""agentkit.topology.core — pick a process topology from a task, build its DAG.

Pure, deterministic, 0 LLM. Encodes the Week 4.6 rules:
  - the trigger × topology design space (§2.5)
  - the 8-question decision ORDER (§2.7) — answer N constrains N+1, and routing
    (Q7) is resolved BEFORE per-task topology (the doc's key re-ordering insight)

`select_topology(spec)` walks the questions in priority order and returns a
`TopologyChoice` (topology + trigger + concurrency + the rule that fired).
`generate_dag(choice, subtasks)` emits the agentkit.runtime DAG shape
(`{"nodes": {...}, "edges": [...]}`) for that topology — the same format
`GraphStore.create_graph` consumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# -- Topology axis (§2.5). DAG-expressible ones map to runtime shapes; GATEWAY
#    and DURABLE_BOARD are trigger/state-level and degrade to a minimal DAG. --
SINGLE = "single"
PIPELINE = "pipeline"          # A→B→C ordered (lab: sequential)
STAR = "star"                  # 1→N→reduce  (lab: parallel / hierarchical)
TREE = "tree"                  # orchestrator→leaves, bounded depth (lab: hierarchical)
MESH = "mesh"                  # peer-to-peer (approximated as fan-out + reduce)
GATEWAY = "gateway"            # entry-point routing, upstream of fan-out
DURABLE_BOARD = "durable_board"  # cross-session / human-in-loop state

# -- Trigger axis (§2.5): when 1 agent becomes N. --
EXPLICIT = "explicit"
SEMANTIC = "semantic"
ROUTING = "routing"
QUEUE = "queue"


@dataclass(frozen=True)
class TaskSpec:
    """Answers to the §2.7 questions (+ the work itself). Booleans default to the
    cheap/no answer so an under-specified task lands on `Single` (Q1), never an
    unjustified fan-out."""

    task: str
    subtasks: tuple[str, ...] = ()
    # Q1: small change / strong-order / fuzzy → one agent is most stable
    single_agent_sufficient: bool = False
    # Q3: can sub-tasks run independently? (False ⇒ ordered ⇒ pipeline)
    subtasks_independent: bool = False
    # Q3/Tree: do sub-tasks need further decomposition (bounded-depth tree)?
    needs_subdecomposition: bool = False
    # Q5: must workers challenge each other? (multi-hypothesis ⇒ mesh)
    workers_challenge: bool = False
    # Q4/Q8: cross-turn / cross-day / human-in-loop / must survive restart
    cross_session: bool = False
    needs_human_in_loop: bool = False
    needs_recovery: bool = False
    # Q7: multiple entry points with different identities/permissions
    multiple_entry_points: bool = False
    # tuning
    max_tree_breadth: int = 4


@dataclass(frozen=True)
class TopologyChoice:
    """The verdict of `select_topology` — topology + trigger + the matching
    worker concurrency + a human-readable rationale (which question fired)."""

    topology: str
    trigger: str
    concurrency: int
    rationale: str
    questions_fired: tuple[str, ...] = field(default_factory=tuple)


def select_topology(spec: TaskSpec) -> TopologyChoice:
    """Walk the §2.7 questions in priority order; first match wins. The order is
    the doc's: routing (Q7) is upstream of topology; then single (Q1); then the
    durable/lifecycle questions (Q4/Q8); then challenge (Q5); then independence
    (Q3) splits star vs tree vs pipeline."""
    n = max(1, len(spec.subtasks))

    # Q7 — routing is resolved BEFORE any per-task fan-out (§2.7 insight).
    if spec.multiple_entry_points:
        return TopologyChoice(GATEWAY, ROUTING, 1,
                              "Q7: multiple entry points with distinct identities → "
                              "gateway routing is upstream of topology",
                              ("Q7",))

    # Q1 — can a single agent do it? Don't pay for multi-agent until forced.
    if spec.single_agent_sufficient or not spec.subtasks:
        return TopologyChoice(SINGLE, EXPLICIT, 1,
                              "Q1: a single agent suffices (small / strong-order / "
                              "fuzzy, or no sub-tasks) → Single",
                              ("Q1",))

    # Q4 / Q8 — cross-session / human-in-loop / restart-resilient → durable board.
    if spec.cross_session or spec.needs_human_in_loop or spec.needs_recovery:
        return TopologyChoice(DURABLE_BOARD, QUEUE, 1,
                              "Q4/Q8: cross-session, human-in-loop, or recovery "
                              "required → durable board (queue-triggered)",
                              ("Q4", "Q8"))

    # Q5 — must workers challenge each other? Mesh costs ~3-5× tokens; only here.
    if spec.workers_challenge:
        return TopologyChoice(MESH, EXPLICIT, n,
                              "Q5: workers must challenge each other "
                              "(multi-hypothesis) → mesh",
                              ("Q5",))

    # Q3 — independent sub-tasks fan out; ordered ones pipeline.
    if spec.subtasks_independent:
        if spec.needs_subdecomposition:
            return TopologyChoice(TREE, EXPLICIT, min(n, spec.max_tree_breadth),
                                  "Q3+: independent sub-tasks that need further "
                                  "decomposition → tree (bounded depth)",
                                  ("Q3",))
        return TopologyChoice(STAR, EXPLICIT, n,
                              "Q3: independent sub-tasks, no further decomposition "
                              "→ star fan-out / fan-in",
                              ("Q3",))
    return TopologyChoice(PIPELINE, EXPLICIT, 1,
                          "Q3: sub-tasks are ordered (output N feeds N+1) → pipeline",
                          ("Q3",))


# ---------------------------------------------------------------------------
# DAG generation — emit the agentkit.runtime {"nodes","edges"} shape.
# ---------------------------------------------------------------------------

def _node(prompt: str, *, llm: bool, model: str, sleep_s: float) -> dict:
    if llm:
        return {"type": "llm", "payload": {"prompt": prompt, "model": model}}
    return {"type": "tool", "payload": {"sleep_s": sleep_s}}


def generate_dag(
    choice: TopologyChoice,
    spec: TaskSpec,
    *,
    llm: bool = True,
    model: str = "gemma-4-26B-A4B-it-heretic-4bit",
    sleep_s: float = 0.1,
) -> tuple[dict, int]:
    """Build the `{"nodes","edges"}` DAG for the chosen topology, embedding the
    task's sub-tasks as node prompts. Returns (dag, concurrency)."""
    subs = list(spec.subtasks)
    top = choice.topology

    def nd(prompt: str) -> dict:
        return _node(prompt, llm=llm, model=model, sleep_s=sleep_s)

    if top == SINGLE or not subs:
        return {"nodes": {"agent": nd(spec.task)}, "edges": []}, 1

    if top == PIPELINE:
        nodes = {f"stage{i}": nd(s) for i, s in enumerate(subs, 1)}
        edges = [[f"stage{i}", f"stage{i + 1}"] for i in range(1, len(subs))]
        return {"nodes": nodes, "edges": edges}, 1

    if top in (STAR, MESH):
        # 1 dispatch → N leaves → 1 reduce. (Mesh is approximated as fan-out +
        # reduce; true peer-to-peer needs a message bus, out of DAG scope.)
        nodes = {"dispatch": nd(spec.task)}
        for i, s in enumerate(subs, 1):
            nodes[f"worker{i}"] = nd(s)
        nodes["reduce"] = nd("Synthesize the workers' results into a final answer.")
        edges = [["dispatch", f"worker{i}"] for i in range(1, len(subs) + 1)]
        edges += [[f"worker{i}", "reduce"] for i in range(1, len(subs) + 1)]
        return {"nodes": nodes, "edges": edges}, len(subs)

    if top == TREE:
        # orchestrator → leaves → gather: the leaves are synthesized by a join
        # node (matches the lab's hierarchical manager→workers→gather), so a
        # decomposing tree still produces one composed answer.
        breadth = min(len(subs), spec.max_tree_breadth)
        nodes = {"orchestrator": nd(spec.task)}
        for i, s in enumerate(subs[:breadth], 1):
            nodes[f"leaf{i}"] = nd(s)
        nodes["gather"] = nd("Synthesize the leaf findings into a final answer.")
        edges = [["orchestrator", f"leaf{i}"] for i in range(1, breadth + 1)]
        edges += [[f"leaf{i}", "gather"] for i in range(1, breadth + 1)]
        return {"nodes": nodes, "edges": edges}, breadth

    # GATEWAY / DURABLE_BOARD — trigger/state-level; minimal single-node DAG.
    return {"nodes": {"entry": nd(spec.task)}, "edges": []}, 1


def _demo() -> None:
    """Self-check: the doc's canonical task shapes map to the right topology."""
    # Q1 — small/fuzzy single task
    assert select_topology(TaskSpec("rename a var")).topology == SINGLE
    # Q3 independent — PR review: security/tests/perf in parallel → star
    pr = TaskSpec("review PR", subtasks=("security", "tests", "perf"),
                  subtasks_independent=True)
    assert select_topology(pr).topology == STAR
    # Q3 ordered — locate→fix→test → pipeline
    fix = TaskSpec("fix bug", subtasks=("locate", "write fix", "add test"),
                   subtasks_independent=False)
    assert select_topology(fix).topology == PIPELINE
    # Q5 — multi-hypothesis debug → mesh
    dbg = TaskSpec("why login fails", subtasks=("frontend", "token", "session"),
                   subtasks_independent=True, workers_challenge=True)
    assert select_topology(dbg).topology == MESH
    # Q7 — multiple entry points → gateway (upstream of everything)
    gw = TaskSpec("assistant", subtasks=("a", "b"), subtasks_independent=True,
                  multiple_entry_points=True)
    assert select_topology(gw).topology == GATEWAY
    # Q4/Q8 — cross-session research → durable board
    board = TaskSpec("multi-day migration", subtasks=("x", "y"),
                     subtasks_independent=True, cross_session=True)
    assert select_topology(board).topology == DURABLE_BOARD
    # Tree — independent + needs decomposition
    tree = TaskSpec("big build", subtasks=("a", "b", "c"),
                    subtasks_independent=True, needs_subdecomposition=True)
    assert select_topology(tree).topology == TREE

    # DAG generation shapes
    dag, conc = generate_dag(select_topology(pr), pr, llm=False)
    assert conc == 3 and "reduce" in dag["nodes"]
    assert sum(1 for e in dag["edges"] if e[1] == "reduce") == 3
    dag2, conc2 = generate_dag(select_topology(fix), fix, llm=False)
    assert conc2 == 1 and dag2["edges"] == [["stage1", "stage2"], ["stage2", "stage3"]]
    print("topology.core._demo OK")


if __name__ == "__main__":
    _demo()
