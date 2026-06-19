"""bench/bench_reference_agent.py — the reference-agent metric harness.

Runs the long-horizon RAG/memory reference agent OFFLINE (FakeLLMClient +
FakeEmbedder, no network) and prints the headline structural numbers:

  - LLM calls:            tiered vs all-LLM baseline
  - est LLM tokens:       tiered vs baseline + % reduction
  - compaction reduction: % over the accumulated research transcript
  - memory recall:        # directions with a reusable prior lesson,
                          with-memory vs without-memory
  - iterations run

Every number here is REAL-now and structural: call counts, the ~4-chars/token
estimate, compaction %, and recall counts are all computed deterministically
offline. Wall-time, real token usage, and answer QUALITY are DEFERRED — they
require a real backend (oMLX :8000 or a CLI client), which the ``--backend``
flag wires up so the measured run is one flag away.

Run:
    python bench/bench_reference_agent.py                 # offline (default)
    python bench/bench_reference_agent.py --backend omlx  # real oMLX (needs env)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make the sibling examples/ package importable (it is not an installed package).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from agentkit.context import compact  # noqa: E402
from agentkit.types import Embedder, LLMClient  # noqa: E402
from agentkit.orchestrator.loop import _render_findings_as_messages  # noqa: E402
from fakes import FakeEmbedder, FakeLLMClient  # noqa: E402
from research_agent import (  # noqa: E402
    KEY_ITERATIONS,
    KEY_LLM_CALLS,
    KEY_LLM_TOKENS,
    KEY_MEMORY_HITS,
    ResearchAgentConfig,
    run_all_llm_baseline,
    run_research,
)

# A higher round count than the dataclass default so the deterministic
# compactor has enough accumulated transcript to amortize its framing overhead
# and clearly beat the quadratic full-context baseline (see the crossover note
# in tests/test_reference_agent.py — at very small round counts the compactor's
# section/transcript framing can briefly cost more than it saves).
BENCH_ROUNDS = 8

SAMPLE_QUESTION = (
    "How do long-horizon agents use external memory to stay coherent "
    "across many reasoning steps?"
)


def _compaction_reduction(question: str) -> tuple[int, int, float]:
    """Compact the accumulated research transcript once and report the reduction.

    Runs the tiered agent to accumulate findings, renders them as the orchestrator
    would, then compacts. Returns ``(before, after, pct_reduction)``.
    """
    result = run_research(
        question, client=FakeLLMClient(), embedder=FakeEmbedder(),
        config=ResearchAgentConfig(max_rounds=BENCH_ROUNDS, use_memory=True),
    )
    messages = _render_findings_as_messages(result["findings"])
    compacted = compact(messages, keep=0)
    before = compacted.est_tokens_before
    after = compacted.est_tokens_after
    pct = 100.0 * (1.0 - after / max(1, before))
    return before, after, pct


def _run_fake() -> None:
    """The offline structural-metrics run (default)."""
    config = ResearchAgentConfig(max_rounds=BENCH_ROUNDS, use_memory=True)

    tiered = run_research(
        SAMPLE_QUESTION, client=FakeLLMClient(), embedder=FakeEmbedder(),
        config=config,
    )
    baseline = run_all_llm_baseline(
        SAMPLE_QUESTION, client=FakeLLMClient(), config=config,
    )
    no_memory = run_research(
        SAMPLE_QUESTION, client=FakeLLMClient(), embedder=FakeEmbedder(),
        config=ResearchAgentConfig(max_rounds=BENCH_ROUNDS, use_memory=False),
    )

    t_tokens = tiered[KEY_LLM_TOKENS]
    b_tokens = baseline[KEY_LLM_TOKENS]
    token_reduction = 100.0 * (1.0 - t_tokens / max(1, b_tokens))

    before, after, comp_pct = _compaction_reduction(SAMPLE_QUESTION)

    rows = [
        ("question", SAMPLE_QUESTION[:48] + "..."),
        ("iterations run (tiered)", f"{tiered[KEY_ITERATIONS]}"),
        ("LLM calls  tiered / baseline",
         f"{tiered[KEY_LLM_CALLS]} / {baseline[KEY_LLM_CALLS]}"),
        ("est LLM tokens  tiered / baseline",
         f"{t_tokens} / {b_tokens}"),
        ("token reduction (tiered vs baseline)", f"{token_reduction:.1f}%"),
        ("compaction tokens  before / after", f"{before} / {after}"),
        ("compaction reduction", f"{comp_pct:.1f}%"),
        ("memory recall hits  with / without",
         f"{tiered[KEY_MEMORY_HITS]} / {no_memory[KEY_MEMORY_HITS]}"),
        ("LLM backend", "fake (offline, deterministic)"),
    ]
    width = max(len(k) for k, _ in rows)
    lines = ["", "agentkit reference-agent benchmark (offline)", "-" * 58]
    lines += [f"{k.ljust(width)} : {v}" for k, v in rows]
    lines.append("-" * 58)
    # bench output is the artifact; printing here is intentional (not lib code).
    print("\n".join(lines))

    print(
        "\nNOTE: these are REAL-now STRUCTURAL numbers (call counts, "
        "~4-chars/token\nestimates, compaction %, recall counts). Wall-time, "
        "real token usage,\nand answer QUALITY require a real backend — run "
        "`python bench/bench_reference_agent.py --backend omlx` with oMLX :8000 up."
    )


def _build_real_backend(backend: str) -> tuple[LLMClient, Embedder]:
    """Construct a real (client, embedder) pair for a measured run.

    This is a thin wired stub: it raises a clear, actionable error if the
    environment is not configured, and it NEVER touches the network in the
    default run or in tests. Wiring the real path is intentionally one flag
    away — the SAME run_research / run_all_llm_baseline functions execute.
    """
    if backend == "cli":
        from agentkit.backends import CliLLMClient

        cmd = os.environ.get("AGENTKIT_CLI_CMD")
        if not cmd:
            raise SystemExit(
                "Real CLI backend requested but AGENTKIT_CLI_CMD is not set. "
                "Set AGENTKIT_CLI_CMD (e.g. 'codex exec' or 'claude -p') and an "
                "EMBED_BASE_URL for the embedder, then re-run."
            )
        # The CLI client is a real, network-free adapter (it just holds the
        # command); constructing it here proves the wiring without I/O.
        client = CliLLMClient(cmd=cmd)
        assert client.cmd == cmd  # constructed, ready to inject
        # The embedder for a CLI backend must still be wired explicitly; we do
        # not silently substitute a fake on the real path.
        raise SystemExit(
            "CLI client constructed, but no real Embedder is wired. Set "
            "EMBED_BASE_URL / OMLX_BASE_URL and provide a real Embedder adapter "
            "to run measured. (Offline default needs no env: drop --backend.)"
        )

    # backend == "omlx"
    base_url = os.environ.get("OMLX_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    embed_url = os.environ.get("EMBED_BASE_URL")
    if not base_url or not embed_url:
        raise SystemExit(
            "Real oMLX backend requested but env is not configured. Set "
            "OMLX_BASE_URL (or OPENAI_BASE_URL) for the chat model and "
            "EMBED_BASE_URL for the embedder, then re-run. The offline default "
            "(drop --backend) needs no env and never touches the network."
        )
    # Constructing the concrete OpenAI/oMLX adapters is deliberately left to the
    # caller's environment; agentkit never imports a vendor SDK at module load.
    raise SystemExit(
        "oMLX env detected, but the concrete OpenAI-compatible client/embedder "
        "adapters are constructed by the operator (agentkit injects, never "
        "imports vendors). Wire them here to run measured against oMLX :8000."
    )


def _run_real(backend: str) -> None:
    """Run the SAME functions against a real backend (measured)."""
    client, embedder = _build_real_backend(backend)  # raises if unconfigured
    config = ResearchAgentConfig(max_rounds=BENCH_ROUNDS, use_memory=True)
    tiered = run_research(SAMPLE_QUESTION, client=client, embedder=embedder,
                          config=config)
    baseline = run_all_llm_baseline(SAMPLE_QUESTION, client=client, config=config)
    print(f"tiered tokens={tiered[KEY_LLM_TOKENS]} "
          f"baseline tokens={baseline[KEY_LLM_TOKENS]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", choices=["fake", "omlx", "cli"], default="fake",
        help="fake (offline, default) | omlx | cli (real backends, need env)",
    )
    args = parser.parse_args()
    if args.backend == "fake":
        _run_fake()
    else:
        _run_real(args.backend)


if __name__ == "__main__":
    main()
