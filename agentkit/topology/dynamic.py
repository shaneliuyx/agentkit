"""agentkit.topology.dynamic — Phase 8: dynamic per-step topology.

A plan (`agentkit.planner.Plan`) is a DAG of steps. Phase 8 lets *each step*
run under its OWN topology: a "compare X and Y" step fans out to a MESH of
debating peers, a "gather sources" step fans out to a STAR, an ordered
multi-stage step runs as a PIPELINE, and an ordinary step runs as a single
agent. The plan stays one DAG; only each step's *internal* execution shape
varies.

Three deterministic-first pieces, same spirit as the rest of agentkit:

  1. ``classify_step_topology(description) -> str`` — a pure, model-free
     keyword-cue classifier (the P33 ``classify_question`` PATTERN, reused for
     step→topology). 0 LLM.
  2. ``assign_topologies(plan, *, mode, ...)`` — annotate every step with a
     topology. ``manual`` is a fixed rule (single, or a caller-supplied one).
     ``auto`` derives per step: deterministic via ``classify_step_topology`` by
     default; optionally richer via ``infer_spec``→``select_topology`` when a
     client is injected AND ``llm=True``.
  3. ``run_plan(plan, client, ...)`` — execute each step under its assigned
     topology, respecting ``depends_on`` order, fanning out via the existing
     topology primitives (``MessageBus`` for MESH, parallel workers for STAR,
     a sequential chain for PIPELINE). The P39 ``FanoutBudget`` is an OPTIONAL
     injected bound (default OFF — local tokens are free; only cloud sets a
     ceiling).

Design axioms (agentkit): frozen dataclasses, injected deps, deterministic
control flow, no mutation of the input ``Plan``.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any

from agentkit.orchestrator.fanout import BudgetExceeded, FanoutBudget
from agentkit.planner.core import Plan, PlanStep
from agentkit.topology.a2a import MessageBus
from agentkit.topology.core import (
    MAP,
    MESH,
    PIPELINE,
    SINGLE,
    STAR,
    TaskSpec,
    select_topology,
)
from agentkit.topology.infer import infer_spec
from agentkit.types import LLMClient, Message

# Modes for assign_topologies.
MODE_MANUAL = "manual"
MODE_AUTO = "auto"

# Deterministic keyword cues for the step→topology classifier. Same shape as
# tiered.classify_question: minimal token cues, model-free, first-match-wins.
# Order matters — MESH (challenge/debate) is checked before STAR (gather) so a
# "compare and collect" step is treated as a debate, not a plain fan-out.
_MESH_CUES = frozenset(
    "compare comparison contrast contrasting debate versus vs differ "
    "difference differences tradeoff tradeoffs".split()
)
_STAR_CUES = frozenset(
    "gather search survey collect find research scan enumerate aggregate "
    "sources".split()
)
_PIPELINE_CUES = frozenset(
    "then after pipeline stage stages sequentially sequential first next "
    "finally subsequently".split()
)


def _content_words(text: str) -> set[str]:
    """Lowercase alphanumeric tokens — the cue-matching surface."""
    return {
        w for w in "".join(c if c.isalnum() else " " for c in text.lower()).split()
        if w
    }


def classify_step_topology(description: str) -> str:
    """Classify one step description into a topology kind (PURE, 0 LLM).

    Keyword-cue heuristic, mirroring ``tiered.classify_question``:
      - compare / contrast / debate / versus / vs  → MESH
      - gather / search / survey / collect / find   → STAR
      - then / after / pipeline / stage             → PIPELINE
      - otherwise                                   → SINGLE (one agent)

    ``vs`` is also matched as a standalone token (``redis vs postgres``). The
    default is SINGLE so an under-specified step never triggers an unjustified
    fan-out — same conservative default as the §2.7 rule tree.
    """
    toks = _content_words(description)
    if toks & _MESH_CUES or " vs " in f" {description.lower()} ":
        return MESH
    # MAP: "fetch each URL" / "analyze each article" / "summarize every result"
    # — independent per-item workers over an upstream list. "each"/"every" is the
    # reliable signal: it almost always means "one operation per item from prior
    # step". Checked before STAR so concrete per-item steps don't fall into the
    # abstract facet fan-out. "map" alone also routes here.
    if "each" in toks or "every" in toks or "map" in toks:
        return MAP
    if toks & _STAR_CUES:
        return STAR
    if toks & _PIPELINE_CUES:
        return PIPELINE
    return SINGLE


def assign_topologies(
    plan: Plan,
    *,
    mode: str,
    client: LLMClient | None = None,
    llm: bool = False,
    fixed: str | None = None,
) -> Plan:
    """Return a NEW Plan whose every step carries an assigned ``topology``.

    Args:
        plan:   The input Plan (never mutated).
        mode:   ``"manual"`` or ``"auto"``.
                - ``manual``: every step gets ``fixed`` (default ``SINGLE``).
                  Rule-based, ZERO LLM, ignores ``client``/``llm``.
                - ``auto``: derive each step's topology from its description.
                  Default deterministic via ``classify_step_topology`` (0 LLM).
                  If ``client`` is given AND ``llm=True``, each step is routed
                  via ``infer_spec(description, client)`` → ``select_topology``
                  for richer (LLM-inferred) topology choice.
        client: Optional LLMClient (only used when ``mode='auto'`` and ``llm``).
        llm:    Opt-in to the LLM auto-spec path (requires ``client``).
        fixed:  The topology used for every step in ``manual`` mode
                (default ``SINGLE``).

    Returns:
        A new frozen Plan with ``PlanStep.topology`` set on every step.

    Raises:
        ValueError: if ``mode`` is unknown, or ``llm=True`` with no ``client``.
    """
    if mode == MODE_MANUAL:
        chosen = fixed or SINGLE
        new_steps = tuple(replace(s, topology=chosen) for s in plan.steps)
        return replace(plan, steps=new_steps)

    if mode == MODE_AUTO:
        use_llm = llm and client is not None
        if llm and client is None:
            raise ValueError("assign_topologies(mode='auto', llm=True) requires a client")

        new_steps = []
        for s in plan.steps:
            if use_llm:
                assert client is not None  # narrowed above
                spec = infer_spec(s.description, client)
                top = select_topology(spec).topology
            else:
                top = classify_step_topology(s.description)
            new_steps.append(replace(s, topology=top))
        return replace(plan, steps=tuple(new_steps))

    raise ValueError(f"unknown mode {mode!r}; expected {MODE_MANUAL!r} or {MODE_AUTO!r}")


# ---------------------------------------------------------------------------
# -- Per-step execution -----------------------------------------------------
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StepRun:
    """The result of executing one plan step under its assigned topology."""

    step_id: str
    description: str
    topology: str
    output: str
    n_agents: int          # how many agent invocations this step fanned out to
    tokens: int            # summed reported tokens for this step's invocations
    wall_s: float


@dataclass(frozen=True)
class DynamicPlanResult:
    """The result of running a whole plan with per-step topologies."""

    task: str
    runs: tuple[StepRun, ...]
    total_tokens: int
    wall_s: float

    @property
    def by_id(self) -> dict[str, StepRun]:
        return {r.step_id: r for r in self.runs}


def _chat(client: LLMClient, prompt: str, *, system: str | None = None) -> tuple[str, int]:
    """One LLM call → (text, tokens). Errors surface as text, never swallowed."""
    msgs: list[Message] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    try:
        res = client.chat(msgs)
    except Exception as exc:  # surface, don't swallow
        return f"[error: {exc}]", 0
    return (res.text or "").strip(), int(getattr(res, "total_tokens", 0) or 0)


# Default fan-out breadth when a step's description does not enumerate peers.
_DEFAULT_PEERS = 3
_PIPELINE_STAGES = ("Outline the approach.", "Develop the details.",
                    "Produce the final result.")


def _run_single(client: LLMClient, step: PlanStep, upstream: str) -> tuple[str, int, int]:
    """Single agent: one LLM call on the step (+ upstream context). → (text, n, tokens)."""
    prompt = _with_upstream(step.description, upstream)
    text, tok = _chat(client, prompt)
    return text, 1, tok


def _run_star(
    client: LLMClient, step: PlanStep, upstream: str, *,
    budget: FanoutBudget | None,
) -> tuple[str, int, int]:
    """STAR: fan out independent workers in parallel, then reduce. → (text, n, tokens)."""
    facets = _facets(step.description, _DEFAULT_PEERS)
    base = _with_upstream(step.description, upstream)

    def work(facet: str) -> tuple[str, int]:
        return _chat(client, f"{base}\n\nFocus specifically on: {facet}")

    results: list[tuple[str, int]] = _parallel_map(work, facets)
    tokens = 0
    drafts = []
    for text, tok in results:
        _charge(budget, tok)
        tokens += tok
        drafts.append(text)
    synthesis = "\n\n".join(f"[worker {i + 1}] {d}" for i, d in enumerate(drafts))
    final, rtok = _chat(
        client,
        f"Synthesize these independent findings into one answer:\n\n{synthesis}",
    )
    _charge(budget, rtok)
    return final, len(facets) + 1, tokens + rtok


def _run_mesh(
    client: LLMClient, step: PlanStep, upstream: str, *,
    budget: FanoutBudget | None,
) -> tuple[str, int, int]:
    """MESH: peers draft (round 1), READ each other via a MessageBus, REVISE
    (round 2), then reduce. The cross-read is what distinguishes mesh from a
    star fan-out — peers debate. → (text, n, tokens)."""
    facets = _facets(step.description, _DEFAULT_PEERS)
    base = _with_upstream(step.description, upstream)
    bus = MessageBus()
    tokens = 0

    # Round 1 — each peer drafts a hypothesis in parallel.
    def draft(idx_facet: tuple[int, str]) -> tuple[int, str, int]:
        i, facet = idx_facet
        text, tok = _chat(
            client, f"{base}\n\nArgue this angle: {facet}",
        )
        return i, text, tok

    for i, text, tok in _parallel_map(draft, list(enumerate(facets, 1))):
        bus.post(f"peer{i}", text, round=1)
        _charge(budget, tok)
        tokens += tok

    # Round 2 — each peer revises after reading EVERY other peer's round-1.
    def revise(idx_facet: tuple[int, str]) -> tuple[int, str, int]:
        i, facet = idx_facet
        peers = bus.context(reader=f"peer{i}")
        text, tok = _chat(
            client,
            f"{base}\n\nYour angle: {facet}\n\nYour peers said:\n{peers}\n\n"
            "Reconsider and give your refined position.",
        )
        return i, text, tok

    revised = []
    for i, text, tok in _parallel_map(revise, list(enumerate(facets, 1))):
        _charge(budget, tok)
        tokens += tok
        revised.append(text)

    debate = "\n\n".join(f"[peer {i + 1}] {d}" for i, d in enumerate(revised))
    final, rtok = _chat(
        client,
        f"Synthesize this debate into one balanced recommendation:\n\n{debate}",
    )
    _charge(budget, rtok)
    return final, 2 * len(facets) + 1, tokens + rtok


def _run_pipeline(
    client: LLMClient, step: PlanStep, upstream: str, *,
    budget: FanoutBudget | None,
) -> tuple[str, int, int]:
    """PIPELINE: ordered stages, each fed the previous stage's output. → (text, n, tokens)."""
    base = _with_upstream(step.description, upstream)
    carry = ""
    tokens = 0
    for stage in _PIPELINE_STAGES:
        prompt = f"{base}\n\nStage: {stage}"
        if carry:
            prompt += f"\n\nPrevious stage produced:\n{carry}"
        carry, tok = _chat(client, prompt)
        _charge(budget, tok)
        tokens += tok
    return carry, len(_PIPELINE_STAGES), tokens


def _extract_items(text: str) -> list[str]:
    """Extract a concrete item list from upstream step output.

    Tries (in order): JSON array, URL regex, markdown bullets/numbered list,
    non-empty lines. Returns an empty list when the text carries no parseable
    list so the caller can fall back gracefully.
    """
    import json
    import re

    text = text.strip()
    if not text:
        return []

    # JSON array
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x) for x in data if x]
    except (json.JSONDecodeError, ValueError):
        pass

    # URLs (most common case: search results piped into a fetch step)
    urls = re.findall(r'https?://[^\s<>"\')\]]+', text)
    if urls:
        seen: set[str] = set()
        deduped = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        return deduped

    # Markdown bullet / numbered list
    items = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:[-*+]|\d+[.):]) +(.+)", line)
        if m:
            items.append(m.group(1).strip())
    if items:
        return items

    # Last resort: non-empty lines
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _run_map(
    client: LLMClient, step: PlanStep, upstream: str, *,
    budget: FanoutBudget | None,
) -> tuple[str, int, int]:
    """MAP: fan out one independent worker per item extracted from upstream.

    Items are URLs, file paths, IDs, or any list found in the prior step's
    output. Workers are parallel and isolated — no MessageBus, no cross-reads.
    Falls back to SINGLE when upstream is empty or yields no parseable list.
    """
    items = _extract_items(upstream)
    if not items:
        # No list in upstream — degrade gracefully to a single call.
        text, tok = _chat(client, _with_upstream(step.description, upstream))
        return text, 1, tok

    base = step.description

    def work(item: str) -> tuple[str, int]:
        return _chat(client, f"{base}\n\nItem: {item}")

    results: list[tuple[str, int]] = _parallel_map(work, items)
    tokens = 0
    drafts = []
    for text, tok in results:
        _charge(budget, tok)
        tokens += tok
        drafts.append(text)

    synthesis = "\n\n".join(f"[item {i + 1}] {d}" for i, d in enumerate(drafts))
    final, rtok = _chat(
        client,
        f"Synthesize these per-item results into one answer:\n\n{synthesis}",
    )
    _charge(budget, rtok)
    return final, len(items) + 1, tokens + rtok


_DISPATCH = {
    SINGLE: lambda c, s, u, budget: _run_single(c, s, u),
    MAP: lambda c, s, u, budget: _run_map(c, s, u, budget=budget),
    STAR: lambda c, s, u, budget: _run_star(c, s, u, budget=budget),
    MESH: lambda c, s, u, budget: _run_mesh(c, s, u, budget=budget),
    PIPELINE: lambda c, s, u, budget: _run_pipeline(c, s, u, budget=budget),
}


def run_plan(
    plan: Plan,
    client: LLMClient,
    *,
    budget: FanoutBudget | None = None,
    max_workers: int = 4,
) -> DynamicPlanResult:
    """Execute every step under its assigned topology, in ``depends_on`` order.

    Steps must already be annotated (call ``assign_topologies`` first); an
    un-annotated step (``topology is None``) defaults to ``SINGLE``. Each step's
    upstream dependency outputs are threaded into its prompt so the DAG's data
    flow is respected. Topology dispatch:

      - SINGLE   → one ``client.chat`` call.
      - STAR     → parallel independent workers + a reduce call.
      - MESH     → two debate rounds over a ``MessageBus`` + a reduce call.
      - PIPELINE → ordered stages, each fed the prior stage.

    ``budget`` (P39 ``FanoutBudget``) is OPTIONAL and OFF by default — for a
    local backend tokens are free; only a cloud caller injects a ceiling. When
    set, every fan-out child's tokens are charged and ``BudgetExceeded`` aborts
    the run, surfacing partial results in the raised error's context is left to
    the caller (we re-raise).

    Args:
        plan:        A Plan whose steps carry ``.topology`` (assign first).
        client:      The injected LLMClient.
        budget:      Optional FanoutBudget ceiling (default None = unbounded).
        max_workers: Parallelism for STAR/MESH fan-out (default 4).

    Returns:
        DynamicPlanResult with one StepRun per step.

    Raises:
        BudgetExceeded: if a ``budget`` ceiling is crossed mid-fan-out.
    """
    global _POOL_WORKERS
    _POOL_WORKERS = max_workers
    t0 = time.perf_counter()
    outputs: dict[str, str] = {}
    runs: list[StepRun] = []
    total_tokens = 0

    # depends_on order: plan.steps is already topologically sorted by plan(),
    # but we honour deps explicitly to be robust to any ordering.
    for step in _topo_order(plan.steps):
        upstream = "\n\n".join(
            f"[{dep}] {outputs.get(dep, '')}" for dep in step.depends_on
            if outputs.get(dep)
        )
        topology = step.topology or SINGLE
        runner = _DISPATCH.get(topology, _DISPATCH[SINGLE])
        st = time.perf_counter()
        text, n_agents, tokens = runner(client, step, upstream, budget)
        outputs[step.id] = text
        total_tokens += tokens
        runs.append(StepRun(
            step_id=step.id,
            description=step.description,
            topology=topology,
            output=text,
            n_agents=n_agents,
            tokens=tokens,
            wall_s=time.perf_counter() - st,
        ))

    return DynamicPlanResult(
        task=plan.task,
        runs=tuple(runs),
        total_tokens=total_tokens,
        wall_s=time.perf_counter() - t0,
    )


# ---------------------------------------------------------------------------
# -- helpers ----------------------------------------------------------------
# ---------------------------------------------------------------------------

#: Worker count for fan-out pools (set by run_plan; module-level so the small
#: pure helpers don't need it threaded through every signature).
_POOL_WORKERS = 4


def _with_upstream(description: str, upstream: str) -> str:
    if upstream:
        return f"{description}\n\nContext from prior steps:\n{upstream}"
    return description


def _charge(budget: FanoutBudget | None, tokens: int) -> None:
    """Charge a child's cost to the optional parent budget (no-op if OFF)."""
    if budget is not None:
        budget.add(tokens)  # raises BudgetExceeded when the running sum crosses


def _parallel_map(fn, items: list) -> list:
    """Run ``fn`` over ``items`` with a small thread pool, preserving order.

    A single item runs inline (no pool) — keeps the common SINGLE-ish path
    deterministic and cheap. The pool only overlaps genuine fan-out, matching
    the runtime ``run_graph`` philosophy.
    """
    if len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(_POOL_WORKERS, len(items))) as ex:
        return list(ex.map(fn, items))


def _facets(description: str, n: int) -> tuple[str, ...]:
    """Derive ``n`` fan-out angles from a step description (PURE, 0 LLM).

    If the description enumerates subjects ("X and Y", "X vs Y", a comma list),
    use those; otherwise fall back to ``n`` generic critique angles so a vague
    step still fans out to a real debate/survey. Deterministic — the facet text
    is just a prompt steering nudge, not load-bearing logic.
    """
    import re

    # Comparison-style "A vs B" / "A versus B" / "A and B".
    parts = re.split(r"\s+(?:vs\.?|versus|and)\s+", description, flags=re.IGNORECASE)
    parts = [p.strip(" .,:;") for p in parts if p.strip(" .,:;")]
    if len(parts) >= 2:
        return tuple(parts[:max(n, len(parts))])

    # Comma list.
    commas = [p.strip() for p in description.split(",") if p.strip()]
    if len(commas) >= 2:
        return tuple(commas[:max(n, len(commas))])

    # Fallback: n generic angles.
    angles = ("the strongest case for it", "the strongest case against it",
              "the practical trade-offs", "edge cases and risks",
              "the simplest viable option")
    return angles[:n]


def _topo_order(steps: tuple[PlanStep, ...]) -> list[PlanStep]:
    """Return steps in a dependency-respecting order (Kahn). The Plan is already
    validated acyclic by ``planner.plan``; this is belt-and-suspenders so
    ``run_plan`` is correct even on a hand-built Plan."""
    by_id = {s.id: s for s in steps}
    indeg = {s.id: len(s.depends_on) for s in steps}
    ready = [s for s in steps if indeg[s.id] == 0]  # preserves input order
    order: list[PlanStep] = []
    seen: set[str] = set()
    while ready:
        s = ready.pop(0)
        if s.id in seen:
            continue
        seen.add(s.id)
        order.append(s)
        for other in steps:
            if s.id in other.depends_on:
                indeg[other.id] -= 1
                if indeg[other.id] == 0:
                    ready.append(by_id[other.id])
    # Any leftover (shouldn't happen on a validated DAG) appended in input order.
    for s in steps:
        if s.id not in seen:
            order.append(s)
    return order


def _demo() -> None:
    """Assert-based self-check — deterministic pieces only (no network/LLM)."""
    from agentkit.planner.core import plan as make_plan

    # 1. classify_step_topology — one assertion per keyword class + default.
    assert classify_step_topology("Compare vector RAG and GraphRAG") == MESH
    assert classify_step_topology("redis vs postgres") == MESH
    assert classify_step_topology("gather sources on the topic") == STAR
    assert classify_step_topology("search the web and collect findings") == STAR
    assert classify_step_topology("first do X then do Y as a pipeline") == PIPELINE
    assert classify_step_topology("write a short recommendation") == SINGLE
    print("OK: classify_step_topology per keyword class")

    # 2. PlanStep.topology back-compat — default None, settable.
    s = PlanStep(id="s1", description="x")
    assert s.topology is None
    s2 = replace(s, topology=MESH)
    assert s2.topology == MESH and s.topology is None  # immutable, copy-on-write
    print("OK: PlanStep.topology back-compat + immutability")

    # 3. assign_topologies manual — every step gets the fixed topology, 0 LLM.
    # A numbered list decomposes into clean per-clause steps.
    p = make_plan("1. compare vector RAG and GraphRAG "
                  "2. write a short recommendation")
    assert len(p.steps) == 2, p.steps
    man = assign_topologies(p, mode=MODE_MANUAL)
    assert all(st.topology == SINGLE for st in man.steps)
    man_star = assign_topologies(p, mode=MODE_MANUAL, fixed=STAR)
    assert all(st.topology == STAR for st in man_star.steps)
    assert p is not man and p.steps[0].topology is None  # input untouched
    print("OK: assign_topologies manual")

    # 4. assign_topologies auto-deterministic — derives from description, 0 LLM.
    auto = assign_topologies(p, mode=MODE_AUTO)
    tops = {st.description: st.topology for st in auto.steps}
    # The "compare ..." step → MESH; the "write a recommendation" step → SINGLE.
    assert any(t == MESH for t in tops.values()), tops
    assert any(t == SINGLE for t in tops.values()), tops
    print("OK: assign_topologies auto-deterministic")

    # 5. run_plan dispatch — single vs fan-out with a fake client (no network).
    class FakeClient:
        def __init__(self) -> None:
            self.n = 0

        def chat(self, messages, tools=None):
            from agentkit.types import ChatResult
            self.n += 1
            last = messages[-1]["content"]
            return ChatResult(text=f"reply#{self.n}", total_tokens=5)

    single_plan = assign_topologies(make_plan("write a summary"), mode=MODE_MANUAL)
    fc1 = FakeClient()
    r1 = run_plan(single_plan, fc1)
    assert len(r1.runs) == 1 and r1.runs[0].topology == SINGLE
    assert r1.runs[0].n_agents == 1 and fc1.n == 1
    print("OK: run_plan single-step dispatch (1 agent, 1 call)")

    mesh_plan = make_plan("compare X and Y")
    mesh_plan = assign_topologies(mesh_plan, mode=MODE_AUTO)
    assert mesh_plan.steps[0].topology == MESH
    fc2 = FakeClient()
    r2 = run_plan(mesh_plan, fc2)
    # MESH fans out: 2*peers + 1 reduce > a single call.
    assert r2.runs[0].n_agents > 1 and fc2.n > 1
    assert r2.runs[0].topology == MESH
    print(f"OK: run_plan fan-out dispatch (MESH, {r2.runs[0].n_agents} agents, {fc2.n} calls)")

    # 6. FanoutBudget bound (optional) — a tight ceiling aborts the fan-out.
    fc3 = FakeClient()
    try:
        run_plan(mesh_plan, fc3, budget=FanoutBudget(ceiling=7))  # 2 children = 10 > 7
        raise AssertionError("expected BudgetExceeded")
    except BudgetExceeded:
        pass
    print("OK: optional FanoutBudget bound aborts fan-out")

    print("topology.dynamic._demo OK")


if __name__ == "__main__":
    _demo()
