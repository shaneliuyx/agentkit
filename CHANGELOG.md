# Changelog

All notable changes to agentkit. Format loosely follows Keep a Changelog.

## [0.1.0] — unreleased

The initial library: studied agent-system patterns extracted/ported into a
lean, dependency-light, Protocol-seamed package. Built in passes; test count
grew monotonically and every prior pass stayed green.

### Core (extracted + hardened from measured lab code)
- `types` — `Embedder` / `LLMClient` / `ChatResponse` Protocol seams; `ChatResult`; `Message`.
- `context` — **new**: deterministic, zero-LLM conversation compaction porting the
  [pi-vcc](https://github.com/sting8k/pi-vcc) pattern (sticky/volatile sections,
  rolling transcript, `merge`). Benchmarked: 73.3% reduction @ 400 msgs, ~1.6 ms, deterministic.
- `memory` — `MemoryStore` generalized from the lab with an **injected** `Embedder`
  (was hardcoded oMLX); plus a pure deterministic `extract` tier.
- `runtime` — durable DAG (`graph_store`, `file_lock`, `scheduler`) extracted near-verbatim
  from `agent-prep` lab-04.6.
- `agent` — ReAct `loop` generalized to an injected `LLMClient` + tool registry; `router` verbatim.
- → 22 tests.

### Orchestrator + batch + CLI backend
- `orchestrator` — long-horizon autonomy porting Deli_AutoResearch + IdeaScout:
  pure `stall` (assess/pivot/escalate), pure `diversity` (token-Jaccard novelty),
  pure `select` (rubric cascade), file-state `state`, and a `loop` that wires
  `context.compact()` as the inter-iteration handoff.
- `agent/batch` — resilient, resumable batch runner (IdeaScout `run_autoretry` pattern).
- `backends/cli` — `CliLLMClient` over a subprocess CLI; argv-not-shell (no injection).
- → 50 tests.

### Roles + quality
- `agent/roles` — `AgentRole` config over the one `run_agent` + `dispatch`; four feynman
  presets (Researcher/Reviewer/Writer/Verifier).
- `quality/verify` — source-grounding pass (deterministic citation/link checks + optional
  LLM claim-support), severity-graded (feynman Verifier / Deli pattern D).
- → 71 tests.

### Reference agent + docs
- `examples/research_agent.py` + `examples/fakes.py` — the whole stack composed into a
  long-horizon RAG/memory research agent; offline composition proof + `--backend omlx`
  measured run. `bench/bench_reference_agent.py` emits structural numbers
  (tiered 5417 vs baseline 6987 tokens = 22.5%; compaction 57.3%; recall 8-vs-0).
- `docs/DESIGN.md` — full per-module design doc + design axiom + 5-source provenance.
- `README.md` refreshed to the 8-module reality.
- `pytest.mark` markers registered; `bench_reference_agent` return-type fixed.
- → **75 tests**.

### Deferred
- Measured oMLX/CLI run (real wall-time, token usage, answer quality).
- Optional real-tokenizer swap for the `len//4` benchmark heuristic.
