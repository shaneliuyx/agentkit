"""End-to-end integration test for agentkit's self-improving stack (re-plan §7b).

This is the ONE cohesive end-to-end test that drives the WHOLE library through
the self-improving loop with FAKES ONLY — no network, no vendor SDK. It composes
the real public modules (selfimproving facade -> roles + evolve + gates + skills
+ codegen + planner -> durable runtime) and asserts each stage of the flow.

GateGuard facts (instruction: "write agentkit e2e self-improving test, re-plan
Phase 7b"):
  - importers: pytest collection only; no production code imports this test.
  - public API: ``test_*`` functions on ``TestSelfImprovingE2E``; no exported
    symbols. Drives ``SelfImprovingAgent.from_config / run / improve / skills /
    forge_tool`` and ``planner.plan / plan_to_graph_config`` against
    ``runtime.GraphStore``.
  - data schema: a ``FakeLLM`` (deterministic ``LLMClient``) and a
    ``FakeEmbedder`` (deterministic ``Embedder``); a ``tmp_path`` config dir
    holding ``roles/*.json`` and ``skills/``; an ``eval_set`` of ``(task,
    expected)`` string pairs; injected ``proposer`` / ``evaluate`` to force a
    deterministic gated ACCEPT.
  - instruction: write agentkit e2e self-improving test, re-plan Phase 7b.

The headline assertion (stage 3) is the gated self-edit: after an ACCEPTed
``improve`` run, the role's config file ON DISK has its ``system_prompt``
rewritten — the agent edited its own config behind a sandbox + gate it cannot
override (P2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentkit.codegen import GeneratedTool
from agentkit.config.roles import load_role
from agentkit.evolve.core import OptimizeResult, Variant
from agentkit.gates.core import Gate, Outcome
from agentkit.runtime.graph_store import PENDING, READY, GraphStore
from agentkit.sandbox.core import SubprocessSandbox
from agentkit.selfimproving import SelfImprovingAgent
from agentkit.skills.core import Skill, SkillLibrary
from agentkit.types import ChatResult, Message


# ---------------------------------------------------------------------------
# Deterministic fakes — no network, no vendor. Each returns canned values.
# ---------------------------------------------------------------------------
class FakeLLM:
    """A deterministic ``LLMClient``.

    For a plain run it returns a fixed final answer. When it sees a codegen
    proposer system prompt it returns a canned ``SCHEMA: + ```python````` reply
    whose code is read-only (a pure add) or side-effecting (subprocess),
    selected by which capability was requested.
    """

    def __init__(self, answer: str = "the answer") -> None:
        self.answer = answer
        self.calls: list[list[Message]] = []

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        self.calls.append(messages)
        system = ""
        user = ""
        for m in messages:
            if m.get("role") == "system":
                system = str(m.get("content", ""))
            elif m.get("role") == "user":
                user = str(m.get("content", ""))

        # codegen tool-author proposer: emit SCHEMA + fenced python in the exact
        # parse format codegen.parse_proposal expects.
        if "tool author" in system.lower():
            if "subprocess" in user.lower() or "shell" in user.lower():
                return ChatResult(text=_forge_reply("run_cmd", _SIDE_EFFECTING_CODE))
            return ChatResult(text=_forge_reply("add_numbers", _CLEAN_CODE))

        return ChatResult(text=self.answer, total_tokens=3)


class FakeEmbedder:
    """A deterministic bag-of-words ``Embedder`` (token-count buckets)."""

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                vec[hash(tok) % self.dim] += 1.0
            out.append(vec)
        return out


# Canned codegen bodies (mirrors the proven test_codegen.py fixtures).
_CLEAN_CODE = "a, b = 2, 3\nprint(a + b)\n"
_SIDE_EFFECTING_CODE = "import subprocess\nsubprocess.run(['echo', 'hi'])\n"


def _forge_reply(name: str, code: str) -> str:
    """A codegen proposer reply: SCHEMA marker + fenced python code block."""
    schema = json.dumps(
        {"name": name, "description": "add two numbers", "parameters": {}}
    )
    return f"SCHEMA:\n{schema}\n\nCODE:\n```python\n{code}\n```\n"


# A role config seeded into tmp_path/roles so from_config loads it from disk and
# improve() can write the gated self-edit back to its exact file.
def _seed_role(roles_dir: Path) -> Path:
    roles_dir.mkdir(parents=True, exist_ok=True)
    path = roles_dir / "researcher.json"
    path.write_text(
        json.dumps(
            {
                "name": "Researcher",
                "system_prompt": "Find sources.",
                "tools": [],
                "difficulty": "medium",
            }
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# The end-to-end test.
# ---------------------------------------------------------------------------
class TestSelfImprovingE2E:
    """Drive the whole library through the self-improving loop with fakes only."""

    def test_end_to_end_self_improving_loop(self, tmp_path: Path) -> None:
        backend = FakeLLM(answer="the answer")
        embedder = FakeEmbedder()

        # --- stage 1: from_config materializes roles into tmp_path/roles ------
        role_path = _seed_role(tmp_path / "roles")
        agent = SelfImprovingAgent.from_config(
            tmp_path, backend=backend, embedder=embedder
        )
        assert isinstance(agent, SelfImprovingAgent)
        assert "Researcher" in agent.roles, agent.roles
        # role_paths maps the loaded role to its on-disk file (for write-back).
        assert agent.role_paths.get("Researcher") == role_path

        # --- stage 2: run returns an AgentResult with the fake answer ---------
        result = agent.run("Find papers on retrieval-augmented generation")
        assert result.answer == "the answer", result
        assert backend.calls, "the backend must have been called by run()"

        # --- stage 3 (headline): gated self-edit persists to disk -------------
        before_prompt = load_role(role_path).system_prompt
        better_prompt = before_prompt + " Cite every claim with a source."

        # Injected proposer (evolve Proposer: (best, history) -> str | None) and
        # evaluator force a deterministic, strictly-better candidate -> ACCEPT.
        def proposer(current_best: str, history: tuple[Variant, ...]) -> str | None:
            return better_prompt

        def evaluate(prompt: str) -> float:
            return 1.0 if "cite" in prompt.lower() else 0.0

        out = agent.improve(
            [("task", "expected")],
            role="Researcher",
            proposer=proposer,
            evaluate=evaluate,
            epochs=2,
        )
        assert isinstance(out, OptimizeResult)
        assert out.delta > 0.0 and out.accepted >= 1, out
        assert out.best == better_prompt, out
        # In-memory role updated...
        assert agent.roles["Researcher"].system_prompt == better_prompt
        # ...AND the config FILE on disk was rewritten (the gated self-edit, P2).
        persisted = load_role(role_path).system_prompt
        assert persisted == better_prompt, persisted
        assert persisted != before_prompt, "self-edit must have changed the file"

        # A worse proposer keeps the now-improved baseline and rewrites nothing.
        snapshot = role_path.read_bytes()
        flat = agent.improve(
            [("task", "expected")],
            role="Researcher",
            proposer=lambda b, h: "no signal at all",
            evaluate=evaluate,
            epochs=2,
        )
        assert flat.accepted == 0 and role_path.read_bytes() == snapshot, flat

        # --- stage 4: skills — gate-verified add, then semantic retrieve ------
        lib = agent.skills
        assert isinstance(lib, SkillLibrary)
        skill = Skill(
            name="binary_search",
            description="Find an element in a sorted list quickly.",
            trigger="searching a sorted collection",
            body="print('ok')",  # runnable, read-only -> the gate can ACCEPT it
        )
        skill_gate = Gate(
            sandbox=SubprocessSandbox(),
            evaluator=lambda proposal: 0.9,
            cwd=str(tmp_path),
        )
        verdict, saved = lib.add(skill, gate=skill_gate, baseline_score=0.5)
        assert verdict.status is Outcome.ACCEPT and saved is not None, verdict
        assert "binary_search" in lib.list(), lib.list()
        hits = lib.retrieve("how do I search a sorted array")
        assert hits and hits[0].name == "binary_search", hits

        # --- stage 5: forge_tool — read-only ACCEPTs, side-effecting ESCALATEs -
        good_tool = agent.forge_tool("add two numbers")
        assert isinstance(good_tool, GeneratedTool)
        assert good_tool.verdict is not None
        assert good_tool.verdict.status is Outcome.ACCEPT, good_tool.verdict
        # An ACCEPTed tool is registrable (mirrors ToolForge.register's rule).
        registry: dict[str, GeneratedTool] = {}
        if good_tool.verdict.status is Outcome.ACCEPT:
            registry[good_tool.name] = good_tool
        assert good_tool.name in registry, registry

        bad_tool = agent.forge_tool("run a shell command via subprocess")
        assert bad_tool.verdict is not None
        assert bad_tool.verdict.status is Outcome.ESCALATE, bad_tool.verdict
        # An ESCALATEd tool is NOT auto-registered.
        registry2: dict[str, GeneratedTool] = {}
        if bad_tool.verdict.status is Outcome.ACCEPT:
            registry2[bad_tool.name] = bad_tool
        assert registry2 == {}, registry2

        # --- stage 6: planner -> durable runtime ------------------------------
        plan_obj, store, run_id = self._plan_to_runtime(tmp_path)
        # Two-step linear plan: s1 ready, s2 pending until s1 is done.
        states = store.node_states(run_id)
        assert states["s1"] == READY and states["s2"] == PENDING, states

        claimed = store.claim_ready_node(run_id, "worker-1")
        assert claimed is not None and claimed.name == "s1", claimed
        promoted = store.mark_done(run_id, "s1", {"tokens": 1})
        # Completing s1 unlocks the dependent s2 (durable, self-planned config).
        assert promoted == ["s2"], promoted
        assert store.node_states(run_id)["s2"] == READY
        next_node = store.claim_ready_node(run_id, "worker-1")
        assert next_node is not None and next_node.name == "s2", next_node
        store.mark_done(run_id, "s2", {})
        assert store.run_status(run_id) == "done"

    # -- helper: build a plan, lower it to a graph config, and start a run -----
    @staticmethod
    def _plan_to_runtime(tmp_path: Path):
        from agentkit.planner.core import plan, plan_to_graph_config

        plan_obj = plan("fetch data and parse it")
        assert len(plan_obj.steps) >= 2, plan_obj.steps
        dag = plan_to_graph_config(plan_obj)
        assert "nodes" in dag and "edges" in dag, dag

        store = GraphStore(str(tmp_path / "runtime.db"))
        graph_id = store.create_graph("e2e_plan", dag)
        run_id = store.start_run(graph_id, "e2e-self-improving")
        return plan_obj, store, run_id


if __name__ == "__main__":  # pragma: no cover - direct run convenience
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        TestSelfImprovingE2E().test_end_to_end_self_improving_loop(Path(d))
    print("e2e self-improving self-check OK", file=sys.stderr)
