"""Tests for the reference agent (examples/research_agent.py) — offline proof.

Composition proof that the whole agentkit stack (orchestrator + roles + agent
loop + memory + verify + compactor) runs end-to-end with deterministic test
doubles (FakeLLMClient + FakeEmbedder, no network), and that the tiered design
(deterministic dispatch + compaction + memory recall) beats full-context
all-LLM routing on token cost.
"""

from __future__ import annotations

import sys
from pathlib import Path

# examples/ is not an installed package; add it to the path so the reference
# agent + fakes import cleanly in the test process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from fakes import FakeEmbedder, FakeLLMClient  # noqa: E402
from research_agent import (  # noqa: E402
    KEY_FINDINGS,
    KEY_ITERATIONS,
    KEY_LLM_TOKENS,
    KEY_MEMORY_EPISODIC,
    KEY_MEMORY_HITS,
    KEY_VERIFY,
    ResearchAgentConfig,
    run_all_llm_baseline,
    run_research,
)

from agentkit.orchestrator.state import Finding  # noqa: E402
from agentkit.quality.verify import VerifyFinding  # noqa: E402

QUESTION = (
    "How do long-horizon agents use external memory to stay coherent "
    "across many reasoning steps?"
)

# The token thesis is scale-dependent (the compactor only beats a quadratic
# full-context baseline once enough transcript accumulates to amortize its
# section/transcript framing overhead). At very small round counts (<= 4) the
# framing can briefly cost more than it saves, so the strict-token assertion
# uses a round count past the crossover — the same count the bench reports.
STRICT_TOKEN_ROUNDS = 8


def _run_tiered(state_dir: str, rounds: int = 6, use_memory: bool = True) -> dict:
    return run_research(
        QUESTION,
        client=FakeLLMClient(),
        embedder=FakeEmbedder(),
        config=ResearchAgentConfig(max_rounds=rounds, use_memory=use_memory),
        state_dir=state_dir,
    )


def test_run_research_produces_findings_and_runs_verify(tmp_path) -> None:
    out = _run_tiered(str(tmp_path / "tiered"))
    assert len(out[KEY_FINDINGS]) >= 1
    assert all(isinstance(f, Finding) for f in out[KEY_FINDINGS])
    # verify ran and returned a list of VerifyFinding (possibly empty).
    assert isinstance(out[KEY_VERIFY], list)
    assert all(isinstance(v, VerifyFinding) for v in out[KEY_VERIFY])
    assert out[KEY_ITERATIONS] >= 1


def test_memory_accumulates_when_enabled(tmp_path) -> None:
    out = _run_tiered(str(tmp_path / "mem"), use_memory=True)
    # Each productive round records its finding to episodic memory.
    assert out[KEY_MEMORY_EPISODIC] > 0


def test_tiered_uses_fewer_tokens_than_all_llm_baseline(tmp_path) -> None:
    # Core thesis: deterministic tiering + compaction + memory recall costs
    # STRICTLY FEWER estimated LLM tokens than routing every direction to the
    # LLM with the entire uncompacted transcript. Run at STRICT_TOKEN_ROUNDS
    # (past the compactor's small-scale crossover; see module docstring note).
    config = ResearchAgentConfig(max_rounds=STRICT_TOKEN_ROUNDS, use_memory=True)
    tiered = run_research(
        QUESTION, client=FakeLLMClient(), embedder=FakeEmbedder(),
        config=config, state_dir=str(tmp_path / "tiered"),
    )
    baseline = run_all_llm_baseline(
        QUESTION, client=FakeLLMClient(), config=config,
        state_dir=str(tmp_path / "baseline"),
    )
    assert tiered[KEY_LLM_TOKENS] < baseline[KEY_LLM_TOKENS], (
        tiered[KEY_LLM_TOKENS], baseline[KEY_LLM_TOKENS],
    )


def test_memory_recall_reuses_prior_findings(tmp_path) -> None:
    # Structural memory-recall quality: with memory enabled, prior findings are
    # recalled for later directions; with memory disabled, nothing is recalled.
    config = ResearchAgentConfig(max_rounds=STRICT_TOKEN_ROUNDS, use_memory=True)
    with_mem = run_research(
        QUESTION, client=FakeLLMClient(), embedder=FakeEmbedder(),
        config=config, state_dir=str(tmp_path / "with"),
    )
    without_mem = run_research(
        QUESTION, client=FakeLLMClient(), embedder=FakeEmbedder(),
        config=ResearchAgentConfig(max_rounds=STRICT_TOKEN_ROUNDS, use_memory=False),
        state_dir=str(tmp_path / "without"),
    )
    assert with_mem[KEY_MEMORY_HITS] >= 1
    assert without_mem[KEY_MEMORY_HITS] == 0
    assert with_mem[KEY_MEMORY_HITS] > without_mem[KEY_MEMORY_HITS]
