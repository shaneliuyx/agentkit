"""agentkit.selfimproving — the SelfImprovingAgent facade (REPLAN §7, Phase 7 P5).

GateGuard facts (instruction: "build agentkit SelfImprovingAgent facade, re-plan
Phase 7"):
  - importers: ``agentkit/__init__.py`` (export wired by the maintainer, not
    here); end users via ``from agentkit import SelfImprovingAgent``;
    ``tests/test_selfimproving.py``.
  - public API: ``SelfImprovingAgent`` with classmethod ``from_config(config_dir,
    *, backend, embedder=None, memory_path=None)`` and instance methods
    ``run(task)``, ``improve(eval_set, *, role=..., budget=...)``, ``forge_tool``,
    ``selected_role``, plus the ``skills`` property.
  - data schema: holds ``roles: dict[str, AgentRole]``, ``role_paths: dict[str,
    Path]`` (so an improved role is written back to its exact file), ``config_dir:
    Path``, ``backend: LLMClient``, ``embedder: Embedder | None``, ``memory:
    MemoryStore | None``. ``eval_set`` is ``list[tuple[str, str]]``.
  - instruction: build agentkit SelfImprovingAgent facade, re-plan Phase 7.

P5 — the highly abstractive, easy public API: one object wires roles + memory +
runtime + evolve + gates + skills behind ``from_config``. Everything underneath
is the deterministic-first, injected-deps machinery agentkit already ships; this
file only composes it.

P2 — the agent edits its own config under guardrails: ``improve`` runs a budgeted
``evolve`` loop where EVERY candidate passes the LEARN ``Gate`` (sandbox +
eval-set evaluator). When a strictly-better variant is ACCEPTed, the improved
role is written BACK to its config file via ``dump_role`` — gated and auditable
on disk (review it as a git diff).

Deterministic-first: dispatch + the keep/discard control are model-free; the LLM
appears only as the injected ``backend`` (run) and the injected ``proposer``
(improve). ``improve`` is opt-in and budgeted; the default agent just runs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from agentkit.agent.loop import AgentResult
from agentkit.agent.roles import AgentRole, dispatch, run_role
from agentkit.config.roles import (
    dump_role,
    load_default_roles,
    load_roles,
)
from agentkit.context.compactor import compact
from agentkit.evolve.core import (
    Evaluator,
    OptimizeResult,
    Proposer,
    evolve_prompt,
    make_llm_proposer,
)
from agentkit.gates.core import Gate
from agentkit.memory.store import MemoryStore
from agentkit.sandbox.core import SubprocessSandbox
from agentkit.skills.core import SkillLibrary
from agentkit.types import Embedder, LLMClient

# Subdirectory names under config_dir (declarative policy on disk).
_ROLES_SUBDIR = "roles"
_SKILLS_SUBDIR = "skills"

# Default budget for a single improve() run when none is supplied.
_DEFAULT_EPOCHS = 5

# "<N>_calls" budget strings map directly to epoch counts (one proposal/epoch).
_BUDGET_RE = re.compile(r"(\d+)")


class SelfImprovingAgent:
    """One object wiring roles + memory + runtime + evolve + gates + skills.

    Construct via :meth:`from_config`. The agent's behavior is the folder of
    config files at ``config_dir``; ``improve`` edits those files in place behind
    the sandbox + gate it cannot override.
    """

    def __init__(
        self,
        *,
        roles: dict[str, AgentRole],
        role_paths: dict[str, Path],
        config_dir: Path,
        backend: LLMClient,
        embedder: Embedder | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self.roles = roles
        self.role_paths = role_paths
        self.config_dir = config_dir
        self.backend = backend
        self.embedder = embedder
        self.memory = memory

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config_dir: str | Path,
        *,
        backend: LLMClient,
        embedder: Embedder | None = None,
        memory_path: str | Path | None = None,
    ) -> "SelfImprovingAgent":
        """Wire an agent from a config folder (``roles/*.yaml|json``).

        Loads every role under ``config_dir/roles``; if that folder is empty or
        absent, falls back to the shipped default roles (the feynman ensemble).
        ``config_dir`` is retained because ``improve`` edits files under it.

        Args:
            config_dir:  the policy folder (holds ``roles/`` and ``skills/``).
            backend:     the injected ``LLMClient`` used to run tasks.
            embedder:    optional ``Embedder`` enabling memory + skills retrieval.
            memory_path: optional SQLite path for a ``MemoryStore`` (needs an
                         ``embedder``); when None, the agent runs without memory.

        Returns:
            A wired ``SelfImprovingAgent``.
        """
        config_dir = Path(config_dir)
        roles_dir = config_dir / _ROLES_SUBDIR

        roles: dict[str, AgentRole] = {}
        role_paths: dict[str, Path] = {}
        if roles_dir.is_dir():
            roles = load_roles(roles_dir)
            role_paths = _map_role_paths(roles_dir, roles)
        if not roles:
            roles = load_default_roles()
            role_paths = {}  # defaults are package files; improve() materializes.

        memory: MemoryStore | None = None
        if memory_path is not None:
            if embedder is None:
                raise ValueError("memory_path requires an embedder")
            memory = MemoryStore(memory_path, embedder=embedder)

        return cls(
            roles=roles,
            role_paths=role_paths,
            config_dir=config_dir,
            backend=backend,
            embedder=embedder,
            memory=memory,
        )

    # -- run ---------------------------------------------------------------

    def selected_role(self, task: str) -> AgentRole:
        """Deterministically pick the role for ``task`` (model-free dispatch)."""
        return dispatch(task, roles=tuple(self.roles.values()))

    def run(self, task: str, *, max_rounds: int | None = None) -> AgentResult:
        """Dispatch ``task`` to a role and run it with memory + compaction.

        Dispatch is the deterministic keyword heuristic (no LLM needed to choose
        the role). Memory (when wired) injects relevant past lessons before the
        loop; the loop's trajectory is compacted into an episodic memory after.
        """
        role = self.selected_role(task)
        result = run_role(
            role,
            task,
            client=self.backend,
            memory=self.memory,
            max_rounds=max_rounds,
        )
        self._record(task, result)
        return result

    def _record(self, task: str, result: AgentResult) -> None:
        """Compact the trajectory and store it as an episodic memory (if wired)."""
        if self.memory is None:
            return
        messages = [
            {"role": "user", "content": task},
            {"role": "assistant", "content": result.answer},
        ]
        summary = compact(messages, keep=0).text or result.answer
        try:
            self.memory.add("episodic", summary, metadata={"task": task})
        except Exception:  # noqa: BLE001 - a memory write must not break run()
            pass

    # -- improve (the payoff) ---------------------------------------------

    def improve(
        self,
        eval_set: list[tuple[str, str]],
        *,
        role: str | None = None,
        budget: str | int | None = None,
        epochs: int | None = None,
        proposer: Proposer | LLMClient | None = None,
        evaluate: Evaluator | None = None,
        min_delta: float = 0.0,
    ) -> OptimizeResult:
        """Evolve a role's system prompt through the gate; rewrite it on ACCEPT.

        Every candidate prompt is admitted ONLY by the LEARN ``Gate``
        (``SubprocessSandbox`` + an evaluator built from ``eval_set``). When a
        strictly-better variant is ACCEPTed, the improved role is written BACK to
        its config file via ``dump_role`` (P2: gated, auditable self-edit on
        disk). The baseline-vs-best delta is returned in the ``OptimizeResult``.

        Opt-in + budgeted; deterministic-first (the keep/discard control is
        model-free — the only LLM is the injected ``proposer``).

        Args:
            eval_set:  ``(task, expected)`` pairs; builds the default evaluator.
            role:      which role to improve (defaults to the first loaded role).
            budget:    ``"<N>_calls"`` or an int → number of mutation epochs.
            epochs:    explicit epoch count (overrides ``budget``).
            proposer:  an evolve ``Proposer`` OR an ``LLMClient`` (wrapped via
                       ``make_llm_proposer``); defaults to wrapping ``backend``.
            evaluate:  optional ``prompt -> [0,1]`` evaluator; defaults to a
                       deterministic substring-recall scorer over ``eval_set``.
            min_delta: minimum improvement the gate's delta stage requires.

        Returns:
            The ``OptimizeResult`` (baseline-vs-best delta + accepted archive).
        """
        role_name = role if role is not None else next(iter(self.roles))
        if role_name not in self.roles:
            raise KeyError(f"unknown role {role_name!r}; have {sorted(self.roles)}")
        target = self.roles[role_name]

        n_epochs = _resolve_epochs(epochs, budget)
        evaluator = evaluate or _default_evaluator(eval_set)
        propose = _resolve_proposer(proposer, self.backend)

        sandbox = SubprocessSandbox()
        # The gate re-scores via the same evaluator so loop + gate agree on the
        # metric; a system prompt has no code, so the execute stage is skipped.
        gate = Gate(
            sandbox=sandbox,
            evaluator=lambda proposal: evaluator(str(proposal.get("content", ""))),
            cwd=str(self.config_dir),
        )

        result = evolve_prompt(
            target.system_prompt,
            propose=propose,
            evaluate=evaluator,
            gate=gate,
            baseline_score=evaluator(target.system_prompt),
            epochs=n_epochs,
            min_delta=min_delta,
            cwd=str(self.config_dir),
        )

        if result.delta > 0.0 and result.best != target.system_prompt:
            improved = AgentRole(
                name=target.name,
                system_prompt=result.best,
                tools=target.tools,
                difficulty=target.difficulty,
                output_schema=target.output_schema,
            )
            self.roles[role_name] = improved
            self._persist_role(role_name, improved)

        return result

    def _persist_role(self, role_name: str, role: AgentRole) -> None:
        """Write an improved role back to its config file (materialize if new)."""
        path = self.role_paths.get(role_name)
        if path is None:
            roles_dir = self.config_dir / _ROLES_SUBDIR
            roles_dir.mkdir(parents=True, exist_ok=True)
            path = roles_dir / f"{role_name.lower()}.json"
            self.role_paths[role_name] = path
        dump_role(role, path)

    # -- skills ------------------------------------------------------------

    @property
    def skills(self) -> SkillLibrary:
        """A ``SkillLibrary`` over the injected embedder + ``config_dir/skills``."""
        if self.embedder is None:
            raise ValueError("skills require an embedder; pass one to from_config")
        return SkillLibrary(self.embedder, self.config_dir / _SKILLS_SUBDIR)

    # -- forge_tool (optional, lazy codegen) -------------------------------

    def forge_tool(self, query: str, *, max_repairs: int | None = None) -> Any:
        """Forge an agent-authored tool via ``agentkit.codegen`` (if installed).

        Lazily imported so the facade does not hard-depend on codegen. Raises a
        clear ``RuntimeError`` when codegen is unavailable.
        """
        try:
            from agentkit.codegen import ToolForge
        except Exception as exc:  # noqa: BLE001 - surface a clear message
            raise RuntimeError(
                "codegen not installed: agentkit.codegen is unavailable "
                f"({exc}); install it to use forge_tool()"
            ) from exc

        gate = Gate(
            sandbox=SubprocessSandbox(),
            evaluator=lambda proposal: 1.0,
            cwd=str(self.config_dir),
        )
        forge = ToolForge(client=self.backend, sandbox=SubprocessSandbox(), gate=gate)
        if max_repairs is None:
            return forge.forge(query)
        return forge.forge(query, max_repairs=max_repairs)


# ---------------------------------------------------------------------------
# Module-private helpers (deterministic, no LLM)
# ---------------------------------------------------------------------------

def _map_role_paths(roles_dir: Path, roles: dict[str, AgentRole]) -> dict[str, Path]:
    """Map each loaded role name to the file it came from (for write-back)."""
    suffixes = (".yaml", ".yml", ".json")
    by_name: dict[str, Path] = {}
    from agentkit.config.roles import load_role

    for path in sorted(p for p in roles_dir.iterdir() if p.suffix.lower() in suffixes):
        try:
            loaded = load_role(path)
        except Exception:  # noqa: BLE001 - a bad sibling file must not break mapping
            continue
        if loaded.name in roles:
            by_name[loaded.name] = path
    return by_name


def _resolve_epochs(epochs: int | None, budget: str | int | None) -> int:
    """Turn an explicit epoch count or a ``budget`` into a positive epoch count."""
    if epochs is not None:
        return max(1, int(epochs))
    if isinstance(budget, int):
        return max(1, budget)
    if isinstance(budget, str):
        match = _BUDGET_RE.search(budget)
        if match:
            return max(1, int(match.group(1)))
    return _DEFAULT_EPOCHS


def _default_evaluator(eval_set: list[tuple[str, str]]) -> Evaluator:
    """Build a deterministic substring-recall scorer over ``eval_set``.

    Fraction of ``expected`` answers whose text appears in the prompt — a cheap,
    model-free signal so ``improve`` is usable with no injected evaluator. Real
    use injects a task-grounded ``evaluate``; this keeps the default honest and
    free.
    """
    expecteds = [str(expected).strip().lower() for _, expected in eval_set if expected]

    def _evaluate(prompt: str) -> float:
        if not expecteds:
            return 0.0
        text = prompt.lower()
        hits = sum(1 for e in expecteds if e and e in text)
        return hits / len(expecteds)

    return _evaluate


def _resolve_proposer(
    proposer: Proposer | LLMClient | None, backend: LLMClient
) -> Proposer:
    """Coerce ``proposer`` into an evolve ``Proposer``.

    Accepts an evolve ``Proposer`` callable directly, an ``LLMClient`` (wrapped
    via ``make_llm_proposer``), or None (wraps ``backend``).
    """
    if proposer is None:
        return make_llm_proposer(backend)
    if hasattr(proposer, "chat"):
        return make_llm_proposer(proposer)  # type: ignore[arg-type]
    return proposer  # type: ignore[return-value]


if __name__ == "__main__":  # pragma: no cover - runnable self-check with fakes
    import json
    import tempfile

    from agentkit.config.roles import load_role
    from agentkit.types import ChatResult

    class _FixedClient:
        def chat(self, messages, tools=None):  # type: ignore[no-untyped-def]
            return ChatResult(text="ok", total_tokens=1)

    class _Proposer:
        def __init__(self, prompt):
            self.prompt = prompt

        def chat(self, messages, tools=None):  # type: ignore[no-untyped-def]
            return ChatResult(text=json.dumps(
                {"mutation_note": "n", "mutated_prompt": self.prompt}))

    class _HashEmbedder:
        def embed(self, texts):  # type: ignore[no-untyped-def]
            return [[float(len(t))] for t in texts]

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        roles_dir = root / _ROLES_SUBDIR
        roles_dir.mkdir(parents=True)
        role_path = roles_dir / "researcher.json"
        role_path.write_text(json.dumps({
            "name": "Researcher", "system_prompt": "BASE", "tools": [],
            "difficulty": "medium",
        }))

        agent = SelfImprovingAgent.from_config(
            root, backend=_FixedClient(), embedder=_HashEmbedder())
        assert set(agent.roles) == {"Researcher"}, agent.roles

        res = agent.run("Find papers on X")
        assert res.answer == "ok", res

        assert agent.selected_role("Draft a report").name == "Researcher"  # only role

        def _eval(prompt: str) -> float:
            return min(1.0, prompt.count("good") / 2.0)

        better = "BASE good good"
        out = agent.improve(
            [("good", "good")], role="Researcher",
            proposer=_Proposer(better), evaluate=_eval, epochs=2)
        assert out.delta > 0 and out.accepted >= 1, out
        assert load_role(role_path).system_prompt == better, "role file must be rewritten"

        # A worse proposer keeps the (now-improved) baseline + does not rewrite.
        before = role_path.read_bytes()
        out2 = agent.improve(
            [("good", "good")], role="Researcher",
            proposer=_Proposer("no signal"), evaluate=_eval, epochs=2)
        assert out2.accepted == 0 and role_path.read_bytes() == before, out2

        lib = agent.skills
        assert isinstance(lib, SkillLibrary) and lib.list() == []

    print("OK selfimproving — from_config + run + gated improve (file rewrite) + skills")
