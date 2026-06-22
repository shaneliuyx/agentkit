# Re-plan — agentkit as a self-improving, config-driven agent library

> Supersedes the framing in [`PROPOSAL-superagent.md`](./PROPOSAL-superagent.md)
> (kept for history). That doc asked "how do we reach deer-flow parity?"; this
> one asks "what does agentkit need so an agent can **improve itself by editing
> config files, safely?**" — grounded in a re-read of agentkit, the curriculum
> vault, `agent-prep`, and `self-improving-agents-curriculum/scaffold/`.

## 1. Thesis

**Config files are the agent's policy surface. The agent edits them. A
deterministic, non-overridable gate + a sandbox are the only things it cannot
edit.**

One sentence, all five principles:

| Principle | How this plan satisfies it |
|---|---|
| **P1 — if a file can define it, don't code it** | Roles, tools, model-routing, topology become declarative files. `config/` generalizes the pattern `topology/config.py` already proves (config ↔ JSON ↔ emitted code). |
| **P2 — agent generates/modifies config under guardrails** | The agent writes/mutates those files; **every** write passes `gates/` (LLM-non-overridable) and executes in `sandbox/` before it is trusted. |
| **P3 — self-improving, self-planning is best** | `evolve/` (DGM prompt/config mutation, keep-discard), `planner/` (task → graph config), `skills/` (propose→verify→save). Hosted by the existing `orchestrator` long-horizon loop. |
| **P4 — min LLM cost, max efficiency, keep accuracy** | Deterministic-first axiom is unchanged (it is already agentkit's core). Self-improvement loops are many-call but ~free on rate-limited oMLX/VibeProxy; opt-in + backend-aware. The gate's regression + delta checks ARE the accuracy guard. |
| **P5 — exposed API highly abstractive + easy** | One facade: `SelfImprovingAgent.from_config(dir)` wires memory+runtime+orchestrator+evolve+gates+skills behind a single object. Protocol seams stay the extension points. |

## 2. What agentkit already has (reuse — do NOT rebuild)

8 modules / ~6.2k LOC. Axiom already shipped: *a cheap deterministic stage gates
the expensive LLM stage* — that **is** P4.

| Module | Reused as |
|---|---|
| `context` | inter-iteration compaction handoff (already wired into `orchestrator`) |
| `memory` | skill + lesson retrieval substrate (embedder seam reused by `skills/`) |
| `runtime` | the DAG `planner/` emits configs **into**; durable, survives `kill -9` |
| `agent` (loop/router/roles/batch) | execution; `roles` becomes config-loaded (see §4) |
| `orchestrator` (stall/diversity/select/loop) | **the host for the self-improvement loop** — already does long-horizon control |
| `quality` (`verify`) | source-grounding (distinct from `gates/`; see §4 note) |
| `backends` (`CliLLMClient`) | flat-rate model access — the economic premise for cheap evolve loops |
| `types` | Protocol seams; the abstraction layer P5 leans on |
| `topology` (`core/config/infer/pipeline/a2a`) | **P1 already proven here**: `config.py` round-trips JSON and `emit_topologies_py` generates code; `infer.select_topology` chooses by task |

**Verify-first finding:** P1 is not greenfield. `topology/config.py` is the
reference implementation of "file defines it, runtime emits it." The work is to
**generalize that one pattern**, not invent it.

## 3. Gap analysis

| Capability | In agentkit? | Plan |
|---|---|---|
| Declarative topology | ✅ `topology/config.py` | generalize to roles/tools/routing |
| Declarative roles | ❌ `agent/roles.py` = code presets (`RESEARCHER`…) | move to `config/roles/*.yaml` |
| Declarative tool catalog | ❌ | `config/tools/*.yaml` + registry loader |
| LEARN verification gate | ❌ (`quality/verify` is source-grounding, not a learn-gate) | port `scaffold/verification/gates.py` |
| Execution sandbox | ❌ (designed in proposal Ph1, unbuilt) | build `sandbox/` (`_BaseEnv`-validated seam) |
| Prompt/config self-evolution | ❌ | port `scaffold/evolve/loop.py` |
| Self-planning | ❌ | new `planner/` → emits runtime graph config |
| Skill library | ❌ | port `scaffold/skills/library.py` |
| Agent-generated tool **code** | ❌ | `evolve/codegen` (youtu EDP 46) — sandbox-confined |

## 4. Proposed modules

Each leverages an existing seam. Port = lift from `scaffold/` and swap its
`backends.adapter`/`config.settings` imports for agentkit's `types.LLMClient`
Protocol + `config/`.

- **`config/`** — declarative policy layer. YAML/JSON for `roles`, `tools`,
  `routing`, `topology`. A loader builds runtime objects from files; a writer
  round-trips objects → files (the `topology/config.py` pattern, generalized).
  `agent/roles.py` presets become the *default* `config/roles/*.yaml`, not code.
- **`sandbox/`** *(prerequisite for codegen)* — `Sandbox` Protocol:
  `run(cmd|code, *, timeout, cwd) -> ExecResult`. `SubprocessSandbox`
  (argv-not-shell, cwd-jailed, output-capped, net-policy via a `net_guard`
  seam ported from scaffold) + optional `DockerSandbox`. `_BaseEnv`
  (E2B/SWE-ReX/browser/local) is the named backend roadmap.
- **`gates/`** — the LEARN gate. Pipeline: **syntax → sandbox-execute
  (does it run?) → regression (eval ≥ baseline) → safety (LLM flag = hard
  reject) → delta (worth keeping?)** → `ACCEPT | REJECT | ESCALATE`.
  **Intentionally not overridable by the LLM** (the scaffold's L2 discipline).
  Every ACCEPT is committed (revertible audit trail).
- **`evolve/`** — DGM-style mutation of **config + prompts** (never weights).
  Keep/discard archive, Self-Harness weakness-targeting (reflection → next
  mutation), RHO label-free self-preference for the no-eval-label case. Each
  candidate goes through `gates/`.
- **`evolve/codegen`** *(the risk line you opted into)* — agent writes a new
  tool: query → schema → code → **sandbox-validate → in-loop debugger repair**
  → `gates/` → register (youtu EDP 46). **Read-only generated tools may
  auto-register; any side-effecting tool (write/pay/deploy) ESCALATEs to human.**
- **`planner/`** — task → subtask DAG → **emits a `runtime` graph config**
  (file, not code). Self-planning that feeds the durable runtime; reuses
  `topology/infer` for shape selection.
- **`skills/`** — propose → verify (`gates/`) → save procedure library;
  semantic retrieval over the existing `memory` embedder.
- **facade** — `SelfImprovingAgent.from_config(dir)` (see §7).

> **Note — two different "verify"s.** `quality/verify` checks *output* is
> grounded in sources. `gates/` checks a *self-modification* is safe to keep.
> Different inputs, different consumers; keep them separate modules.

## 5. Security model (load-bearing — codegen is enabled)

Self-edit without this section is self-destruction. The guardrail is two
deterministic layers the LLM cannot bypass:

1. **`sandbox/` — containment.** No generated code or tool runs outside it.
   argv-not-shell (no injection), cwd jail, wall-clock timeout, output cap,
   network policy (`net_guard`: default-deny egress, allowlist per tool).
   Docker tier for hard isolation when the task warrants it.
2. **`gates/` — admission.** Nothing the agent proposes (prompt, config, skill,
   tool, plan) enters the trusted set without passing the pipeline in §4.
   `ESCALATE` is a first-class outcome: ambiguous or side-effecting changes
   stop for a human. The gate code is **not** reachable from the agent's tool
   surface — it is L2 deterministic, by construction.
3. **Reversibility.** Every ACCEPT is a commit. The improvement history is a
   git log; any regression is one `revert` away. (scaffold `commit_wrapper`.)

Build order is therefore non-negotiable: **`sandbox/` and `gates/` ship before
`evolve/codegen`.** Shipping codegen first = a loaded gun with no safety.

## 6. Build order + effort

| Phase | Ships | Unblocks | Size |
|---|---|---|---|
| 1 | `config/` (roles+tools+routing as files; loader/writer) | P1 in full | M |
| 2 | `sandbox/` (Subprocess + net_guard; Docker optional) | safe execution | M |
| 3 | `gates/` (LEARN pipeline + commit audit) | every self-edit | M |
| 4 | `skills/` + `evolve/` (prompt/config mutation) | P3 self-improve | L (mostly port) |
| 5 | `planner/` (task → graph config) | P3 self-plan | M |
| 6 | `evolve/codegen` (sandboxed tool-gen, EDP 46) | agent-authored tools | M (gated) |
| 7 | `SelfImprovingAgent` facade + docs | P5 | S |

Phases 1–3 are the spine; 4–6 are the capability; 7 is the polish. 4 is
largest but mostly lift-and-reseam from the proven scaffold.

## 7. Public API sketch (P5 — abstractive + easy)

```python
from agentkit import SelfImprovingAgent

# All policy in ./agent_config/ (roles/*.yaml, tools/*.yaml, routing.yaml).
agent = SelfImprovingAgent.from_config("./agent_config", backend=cli_client)

agent.run("research X and write a brief")        # normal task execution

# Opt-in self-improvement (free on flat-rate backends):
agent.improve(eval_set, budget="50_calls")       # evolve + gates, auto-committed
agent.skills.list()                              # curated, gate-passed procedures
```

The agent edits files under `./agent_config/`; the user reviews changes as a
git diff. Nothing magic, nothing hidden — the policy is on disk, the history is
in the log.

## 8. Deliberately NOT building (scope discipline)

- **No weight/code self-modification of agentkit itself.** Evolution touches the
  agent's *config and prompts*; generated *tool* code is sandbox-confined and
  gate-admitted, never patched back into the library.
- **No new vendor lock-in.** Everything behind `types` Protocols; backends stay
  injectable.
- **No always-on evolution.** `improve()` is explicit and budgeted; the default
  agent is deterministic-first and cheap.
- **No bespoke config DSL.** Plain YAML/JSON; if a value never changes, it stays
  a constant (don't config-ify the stable — EDP 47 anti-pattern).

## 9. One-line pitch

agentkit becomes the library where **your agent's behavior is a folder of config
files it can improve on its own — behind a sandbox it can't escape and a gate it
can't override.**

---

*Provenance: EDP 45 (group-relative experience), 46 (auto-tool-gen + debugger),
47 (declarative topology) in the curriculum vault; `scaffold/` modules
`evolve/loop.py`, `verification/gates.py`, `skills/library.py`; agentkit
`topology/config.py` as the P1 reference pattern.*
