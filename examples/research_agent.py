"""examples/research_agent.py — the long-horizon RAG/memory reference agent.

This composes the WHOLE agentkit stack into one autonomous research reference
agent and emits structural performance numbers, all OFFLINE (a fake LLM client +
fake embedder, no network). The same agent runs MEASURED against a real backend
by swapping the injected ``client`` / ``embedder`` — nothing in this module
imports a vendor SDK.

Two callables it exposes:

  ``run_research``           — the TIERED agent. Per direction it (1) recalls
                               prior episodic memory (RAG), (2) deterministically
                               dispatches to a role and runs the ReAct loop with
                               only a COMPACTED brief injected by the
                               orchestrator, (3) parses findings, (4) records the
                               finding back to memory so later iterations recall
                               it. The orchestrator's diversity gate keeps
                               directions novel; the stall ladder stops the run.

  ``run_all_llm_baseline``   — the BASELINE: route everything to the LLM with the
                               ENTIRE accumulated transcript as context, no
                               memory recall, no compaction, no dispatch (always
                               Researcher). Same question, same direction count,
                               same dict shape — so the tiered-vs-all-LLM cost
                               delta is a fair, structural comparison.

The thesis the numbers back: deterministic tiering + compaction + memory recall
uses strictly fewer LLM tokens than full-context all-LLM routing.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass

from agentkit.agent.roles import dispatch, run_role
from agentkit.context import compact
from agentkit.memory.store import MemoryStore
from agentkit.orchestrator.loop import OrchestratorConfig
from agentkit.orchestrator.loop import run as orchestrate
from agentkit.orchestrator.state import (
    Finding,
    ProgressState,
    init_task,
    load_progress,
    read_directions,
    read_findings,
)
from agentkit.quality.verify import VerifyFinding, verify
from agentkit.types import Embedder, LLMClient

# Deterministic sub-question angles appended to the base question. Each angle is
# token-disjoint enough from the others that the orchestrator's Jaccard novelty
# gate accepts a fresh one each round (the base question's shared tokens are
# below the default 0.6 threshold once an angle word is added).
ANGLE_SUFFIXES: tuple[str, ...] = (
    "background origins history",
    "core method mechanism approach",
    "known limitations failures pitfalls",
    "related alternative competing work",
    "benchmarks empirical evaluation results",
    "future directions open problems",
    "practical deployment operational concerns",
    "theoretical foundations assumptions guarantees",
)

# Keys used in the returned dict, so callers/tests share one vocabulary.
KEY_FINDINGS = "findings"
KEY_VERIFY = "verify_findings"
KEY_ITERATIONS = "iterations"
KEY_LLM_CALLS = "llm_calls"
KEY_LLM_TOKENS = "llm_tokens"
KEY_PROGRESS = "progress"
KEY_MEMORY_EPISODIC = "memory_episodic"
KEY_MEMORY_HITS = "memory_recall_hits"


def _url_checker():
    """Return the offline-only ``FakeUrlChecker`` regardless of import style.

    Works whether this module is imported as ``examples.research_agent`` (tests
    add ``examples/`` to sys.path) or run directly as ``__main__``.
    """
    try:
        from fakes import FakeUrlChecker  # examples/ on sys.path
    except ImportError:
        from examples.fakes import FakeUrlChecker  # package-style import
    return FakeUrlChecker()


@dataclass(frozen=True)
class ResearchAgentConfig:
    """Tunable knobs for a reference-agent run."""

    max_rounds: int = 6
    diversity_threshold: float = 0.6
    use_memory: bool = True


# Common words stripped when deriving the short topic stub, so the stub is
# dominated by content words and stays small relative to the disjoint angle.
_STOPWORDS = frozenset(
    "a an the to of in on for and or how do does use using stay across many "
    "what why when is are be with that this their they it as at by from".split()
)


def _topic_stub(question: str, max_words: int = 4) -> str:
    """Derive a short, deterministic content-word stub from the question.

    Keeping the stub short is what makes each ``"<stub>: <angle>"`` direction
    dominated by its UNIQUE angle tokens, so the orchestrator's token-Jaccard
    novelty gate accepts a structurally-different direction each round rather
    than rejecting near-duplicates that merely repeat the full question.
    """
    words = [w for w in question.lower().split() if w.strip(",.?!;:")]
    content = [w.strip(",.?!;:") for w in words if w.strip(",.?!;:") not in _STOPWORDS]
    stub = " ".join(content[:max_words])
    return stub or "topic"


def _candidate_directions_factory(question: str):
    """Build a ``candidate_directions`` supplier that derives sub-questions.

    Each call offers the next not-yet-tried angle as ``"<stub>: <angle>"`` where
    ``<stub>`` is a short content-word topic from the question and ``<angle>`` is
    one of the token-disjoint ``ANGLE_SUFFIXES``. Because the stub is short, the
    direction is dominated by the angle's unique tokens, so the orchestrator's
    diversity gate accepts a fresh structurally-different direction each round.
    """
    stub = _topic_stub(question)

    def candidate_directions(
        progress: ProgressState, tried: list[str]
    ) -> list[str]:
        tried_set = set(tried)
        candidates: list[str] = []
        for angle in ANGLE_SUFFIXES:
            direction = f"{stub}: {angle}"
            if direction not in tried_set:
                candidates.append(direction)
        return candidates

    return candidate_directions


def _parse_findings(direction: str, answer: str) -> list[Finding]:
    """Parse a role's answer into one-or-more Findings.

    The fake answer is a single ``finding: <topic> <url>.`` sentence; we extract
    the first citation-like URL as evidence and keep the whole answer as the
    summary. Real backends produce richer prose; this parse stays robust to that
    because it only needs the answer text + any embedded URL.
    """
    evidence = ""
    for token in answer.split():
        cleaned = token.rstrip(".,;)")
        if cleaned.startswith(("http://", "https://")):
            evidence = cleaned
            break
    summary = answer.strip()
    if not summary:
        return []
    return [Finding(direction=direction, summary=summary, evidence=evidence)]


def build_spawn(
    client: LLMClient,
    embedder: Embedder,
    memory: MemoryStore | None,
    use_memory: bool = True,
):
    """Build a ``Spawn``-compatible worker for the orchestrator.

    For a chosen direction the worker:
      1. (if use_memory) recalls prior episodic memory via ``inject_context`` —
         this is the RAG recall tier;
      2. deterministically ``dispatch``-es the direction to a role and runs
         ``run_role`` over the ReAct loop, injecting only the orchestrator's
         compacted brief (``injected_context``) plus any recalled memory;
      3. parses the answer into Findings;
      4. (if use_memory) records each finding back to episodic memory so later
         iterations can recall it;
      5. returns ``(findings, metric)`` where ``metric`` is the count of NEW
         findings — it falls to 0 once the agent stops producing fresh evidence,
         which exercises the orchestrator's stall ladder.
    """

    def spawn(
        direction: str, injected_context: str, state_dir: str
    ) -> tuple[list[Finding], float]:
        # (1) RAG recall: pull the single most-relevant prior lesson for this
        # direction. We recall k=1 (not the default 4) on purpose: the
        # orchestrator already injects a COMPACTED brief of all in-run findings,
        # so recalling many full prior entries here would re-inject what the
        # brief already carries. The RAG tier's job is to surface the one
        # most-relevant cross-direction lesson, not to re-dump the transcript.
        recalled = ""
        if use_memory and memory is not None:
            recalled = memory.inject_context(direction, k=1)

        # The task the worker actually sees = direction + the orchestrator's
        # compacted brief + any recalled memory. The compactor keeps this small.
        task_parts = [direction]
        if injected_context:
            task_parts.append(injected_context)
        if recalled:
            task_parts.append(recalled)
        task = "\n\n".join(task_parts)

        # (2) deterministic dispatch -> role, then the existing ReAct loop with
        # an empty tool registry (the fake client answers directly).
        role = dispatch(direction)
        result = run_role(role, task, client, tools={}, memory=None)

        # (3) parse the answer into findings.
        findings = _parse_findings(direction, result.answer)

        # (4) record each finding so later iterations recall it (write side of
        # the experience layer).
        if use_memory and memory is not None:
            for f in findings:
                memory.add(
                    "episodic",
                    f.summary,
                    metadata={"direction": direction, "evidence": f.evidence},
                )

        # (5) metric = number of NEW findings this round.
        return findings, float(len(findings))

    return spawn


def _assemble_draft(findings: list[Finding]) -> str:
    """Writer-style concatenation of findings into a single draft text.

    Each finding becomes one sentence carrying its evidence URL inline so the
    verification pass can extract a citation per claim.
    """
    sentences: list[str] = []
    for f in findings:
        summary = f.summary.strip().rstrip(".")
        if f.evidence and f.evidence not in summary:
            sentences.append(f"{summary} {f.evidence}.")
        else:
            sentences.append(f"{summary}.")
    return " ".join(sentences)


def _count_recall_hits(memory: MemoryStore | None, directions: list[str]) -> int:
    """Count how many directions had a non-empty memory recall available.

    Structural recall metric: for each tried direction, does the store return a
    positively-similar prior memory? Re-running ``inject_context`` after the run
    measures how much accumulated experience would have been reused.
    """
    if memory is None:
        return 0
    hits = 0
    for direction in directions:
        if memory.inject_context(direction):
            hits += 1
    return hits


def run_research(
    question: str,
    client: LLMClient,
    embedder: Embedder,
    config: ResearchAgentConfig = ResearchAgentConfig(),
    state_dir: str | None = None,
) -> dict:
    """Run the tiered reference agent end-to-end and return structural metrics.

    Pipeline: build memory (if enabled) -> build the spawn worker -> drive the
    orchestrator over derived sub-question directions -> assemble a draft from
    all findings -> run the deterministic ``verify`` pass for source-grounding ->
    return a metrics dict.
    """
    if state_dir is None:
        state_dir = tempfile.mkdtemp(prefix="agentkit_research_")
    init_task(state_dir, task_spec=question)

    memory: MemoryStore | None = None
    if config.use_memory:
        memory = MemoryStore(f"{state_dir}/memory.db", embedder=embedder)

    spawn = build_spawn(client, embedder, memory, use_memory=config.use_memory)
    candidate_directions = _candidate_directions_factory(question)

    orch_config = OrchestratorConfig(
        max_rounds=config.max_rounds,
        max_seconds=1e9,
        diversity_threshold=config.diversity_threshold,
    )
    progress = orchestrate(
        state_dir, spawn=spawn, candidate_directions=candidate_directions,
        config=orch_config,
    )

    findings = read_findings(state_dir)

    # Assemble + verify (deterministic source-grounding; no LLM tier here).
    draft = _assemble_draft(findings)
    verify_findings: list[VerifyFinding] = verify(
        draft, checker=_url_checker(), client=None
    )

    directions = read_directions(state_dir)
    episodic = memory.count("episodic") if memory is not None else 0
    recall_hits = _count_recall_hits(memory, directions)

    return {
        KEY_FINDINGS: findings,
        KEY_VERIFY: verify_findings,
        KEY_ITERATIONS: progress.iteration,
        KEY_LLM_CALLS: getattr(client, "n_calls", 0),
        KEY_LLM_TOKENS: getattr(client, "total_tokens", 0),
        KEY_PROGRESS: progress,
        KEY_MEMORY_EPISODIC: episodic,
        KEY_MEMORY_HITS: recall_hits,
    }


def run_all_llm_baseline(
    question: str,
    client: LLMClient,
    config: ResearchAgentConfig = ResearchAgentConfig(),
    state_dir: str | None = None,
) -> dict:
    """The BASELINE: route every direction to the LLM with FULL context.

    Same question and same number of directions as ``run_research``, but:
      - NO memory recall,
      - NO compaction (the injected context is the ENTIRE accumulated
        transcript, grown verbatim each round),
      - NO deterministic dispatch (always the Researcher role).

    This is "route everything to the LLM with full context" — the cost the
    tiered agent is measured against. Returns the same dict shape.
    """
    if state_dir is None:
        state_dir = tempfile.mkdtemp(prefix="agentkit_baseline_")
    init_task(state_dir, task_spec=question)

    # Use the SAME directions the tiered run would try, in order, capped at the
    # round budget — a fair like-for-like comparison.
    candidate_directions = _candidate_directions_factory(question)
    directions = candidate_directions(load_progress(state_dir), [])[: config.max_rounds]

    from agentkit.agent.roles import RESEARCHER

    findings: list[Finding] = []
    transcript = ""  # grows verbatim — NO compaction.
    for direction in directions:
        # The injected context is the WHOLE accumulated transcript so far.
        task = f"{direction}\n\n{transcript}" if transcript else direction
        result = run_role(RESEARCHER, task, client, tools={}, memory=None)
        new = _parse_findings(direction, result.answer)
        findings.extend(new)
        # Append this round's question + answer to the running transcript.
        transcript = f"{transcript}\n[direction] {direction}\n[answer] {result.answer}".strip()

    draft = _assemble_draft(findings)
    verify_findings = verify(draft, checker=_url_checker(), client=None)

    return {
        KEY_FINDINGS: findings,
        KEY_VERIFY: verify_findings,
        KEY_ITERATIONS: len(directions),
        KEY_LLM_CALLS: getattr(client, "n_calls", 0),
        KEY_LLM_TOKENS: getattr(client, "total_tokens", 0),
        KEY_PROGRESS: load_progress(state_dir),
        KEY_MEMORY_EPISODIC: 0,
        KEY_MEMORY_HITS: 0,
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fakes import FakeEmbedder, FakeLLMClient

    sample_q = (
        "How do long-horizon agents use external memory to stay coherent "
        "across many reasoning steps?"
    )
    client = FakeLLMClient()
    embedder = FakeEmbedder()
    out = run_research(
        sample_q, client=client, embedder=embedder,
        config=ResearchAgentConfig(max_rounds=6, use_memory=True),
    )

    assert len(out[KEY_FINDINGS]) >= 1, out[KEY_FINDINGS]
    assert isinstance(out[KEY_VERIFY], list), "verify must have run"
    assert out[KEY_MEMORY_EPISODIC] > 0, "memory must have accumulated"
    assert out[KEY_LLM_TOKENS] > 0, "token cost must be computable offline"

    print(
        "research_agent self-check OK "
        f"(findings={len(out[KEY_FINDINGS])} "
        f"iterations={out[KEY_ITERATIONS]} "
        f"llm_calls={out[KEY_LLM_CALLS]} "
        f"llm_tokens={out[KEY_LLM_TOKENS]} "
        f"episodic={out[KEY_MEMORY_EPISODIC]} "
        f"recall_hits={out[KEY_MEMORY_HITS]} "
        f"verify_findings={len(out[KEY_VERIFY])})"
    )
