"""agentkit.skills.core — a gate-verified, semantically-retrieved skill library.

A *skill* is a reusable, validated procedure the agent learned from past
trajectories. This module is the second thin target over the shared optimizer in
``agentkit.evolve``: a skill's body is just another text artifact, so
``optimize_skill`` is ``evolve.optimize_text`` pointed at the skill body and
emitting a deployable best-skill file plus the baseline-vs-optimized delta — the
SkillOpt loop (microsoft/SkillOpt, MIT) run on agentkit's LEARN gate.

The library itself is propose -> verify -> save: nothing enters the trusted set
without passing ``agentkit.gates.Gate``. Retrieval is semantic, reusing the
injected ``Embedder`` and the cosine approach proven in ``agentkit.memory.store``
(no vendor import — the embedder is injected). The keep/discard and retrieval
control is deterministic; the LLM is only the injected skill proposer.

Ported and re-seamed from
``self-improving-agents-curriculum/scaffold/skills/library.py``: its
``backends.adapter.{chat,embed}`` / ``config.settings`` are replaced by an
injected ``types.LLMClient``, an injected ``types.Embedder``, and an explicit
directory. SkillOpt framing: "train skills like neural nets — epochs, minibatch,
validation gates, no weights; deploy the best artifact; report the pass-rate delta."
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentkit.evolve.core import (
    Evaluator,
    OptimizeResult,
    Proposer,
    optimize_text,
)
from agentkit.gates.core import Gate, Outcome, Verdict
from agentkit.types import Embedder, LLMClient, Message


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity over two float lists; 0 if either is the zero vector."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass(frozen=True)
class Skill:
    """An immutable, reusable agent skill.

    Attributes:
        name:        snake_case identifier (also the filename stem).
        description: one paragraph describing the skill.
        body:        the procedure text (ordered steps joined, or code) — the
                     optimizable artifact ``optimize_skill`` evolves.
        trigger:     natural-language description of when to use this skill.
        eval_score:  score on the eval set when the skill was validated.
        created_at:  unix timestamp.
        source_task: the task that generated this skill (metadata).
    """

    name: str
    description: str
    body: str
    trigger: str = ""
    eval_score: float = 0.0
    created_at: float = field(default_factory=time.time)
    source_task: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Skill":
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            body=data.get("body", ""),
            trigger=data.get("trigger", ""),
            eval_score=float(data.get("eval_score", 0.0)),
            created_at=float(data.get("created_at", 0.0)),
            source_task=data.get("source_task", ""),
        )

    def with_body(self, body: str, *, eval_score: float | None = None) -> "Skill":
        """Return a copy with a new body (immutable update for optimization)."""
        return Skill(
            name=self.name,
            description=self.description,
            body=body,
            trigger=self.trigger,
            eval_score=self.eval_score if eval_score is None else eval_score,
            created_at=self.created_at,
            source_task=self.source_task,
        )


_PROPOSE_SYSTEM = (
    "You are a skill extraction engine for a self-improving agent. Given a "
    "successful task trajectory, extract ONE reusable skill the agent "
    "demonstrated. The skill must generalize beyond this specific task and its "
    "steps must be concrete. Return ONLY valid JSON: "
    '{"name": "<snake_case, max 40 chars>", "description": "<one paragraph>", '
    '"trigger": "<when to use this>", "steps": ["step 1", "step 2"]}. '
    'If there is no reusable skill, return {"name": "none"}.'
)


def _parse_skill_proposal(raw: str, source_task: str) -> Skill | None:
    """Parse an LLM JSON reply into a ``Skill`` (None on failure / 'none')."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("```")
        ).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or str(data.get("name", "none")).lower() == "none":
        return None
    name = re.sub(r"[^a-z0-9_]", "_", str(data.get("name", "skill")).lower())[:40]
    steps = list(data.get("steps", []))
    body = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))
    return Skill(
        name=name or "skill",
        description=str(data.get("description", "")),
        body=body,
        trigger=str(data.get("trigger", "")),
        source_task=source_task,
    )


class SkillLibrary:
    """A directory-backed, gate-verified, semantically-retrieved skill library.

    Deps are injected (an ``Embedder`` for retrieval, a directory for storage);
    the optional skill proposer is an injected ``LLMClient``. The propose ->
    verify -> save discipline means ``add`` refuses to persist a skill the
    ``Gate`` did not ACCEPT.
    """

    def __init__(self, embedder: Embedder, directory: str | Path) -> None:
        self.embedder = embedder
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    # -- propose -----------------------------------------------------------

    def propose(
        self,
        client: LLMClient,
        trajectory_summary: str,
        source_task: str = "",
    ) -> Skill | None:
        """Ask the injected LLM to extract one reusable skill from a trajectory.

        Returns ``None`` (never raises) when nothing generalizable is found or
        the reply does not parse — a miss must not break the calling loop.
        """
        messages: list[Message] = [
            {"role": "system", "content": _PROPOSE_SYSTEM},
            {
                "role": "user",
                "content": f"Extract a reusable skill from this trajectory:\n\n{trajectory_summary}",
            },
        ]
        try:
            raw = getattr(client.chat(messages), "text", "") or ""
        except Exception:  # noqa: BLE001 - proposal failure is non-fatal
            return None
        return _parse_skill_proposal(raw, source_task)

    # -- verify + save -----------------------------------------------------

    def add(
        self,
        skill: Skill,
        *,
        gate: Gate,
        baseline_score: float,
        min_delta: float = 0.0,
    ) -> tuple[Verdict, Path | None]:
        """Verify a skill through the ``Gate``, saving it ONLY on ACCEPT.

        The skill body is offered to the gate as the proposal ``content`` (and,
        when it parses as python, as ``code`` so the gate's execute stage
        actually runs it). Returns ``(verdict, path)`` — ``path`` is None when
        the gate did not ACCEPT (REJECT/ESCALATE are never saved).
        """
        proposal: dict[str, Any] = {
            "type": "skill",
            "content": skill.body,
            "description": skill.description,
            "note": f"skill:{skill.name}",
        }
        if _is_python(skill.body):
            proposal["code"] = skill.body
        verdict = gate.run_gate(
            proposal, baseline_score=baseline_score, min_delta=min_delta
        )
        if verdict.status is not Outcome.ACCEPT:
            return verdict, None
        saved = skill.with_body(skill.body, eval_score=verdict.score)
        return verdict, self.save(saved)

    def save(self, skill: Skill) -> Path:
        """Persist a skill as paired ``<name>.json`` + ``<name>.md`` files."""
        json_path = self.directory / f"{skill.name}.json"
        md_path = self.directory / f"{skill.name}.md"
        json_path.write_text(json.dumps(skill.to_dict(), indent=2), encoding="utf-8")
        md_path.write_text(
            f"# Skill: {skill.name}\n\n"
            f"**Trigger:** {skill.trigger}\n\n"
            f"## Description\n\n{skill.description}\n\n"
            f"## Body\n\n{skill.body}\n\n"
            f"---\n*Eval score: {skill.eval_score:.3f} | "
            f"Source: {skill.source_task[:80]}*\n",
            encoding="utf-8",
        )
        return json_path

    # -- load + retrieve ---------------------------------------------------

    def load(self, name: str) -> Skill | None:
        """Load a skill by name, or None if it does not exist."""
        json_path = self.directory / f"{name}.json"
        if not json_path.exists():
            return None
        return Skill.from_dict(json.loads(json_path.read_text(encoding="utf-8")))

    def list(self) -> list[str]:
        """Return the names of all saved skills (sorted)."""
        return [p.stem for p in sorted(self.directory.glob("*.json"))]

    def retrieve(self, query: str, k: int = 3) -> list[Skill]:
        """Retrieve the top-k skills by semantic similarity to ``query``.

        Embeds ``trigger + " " + description`` per skill via the injected
        embedder (same approach as ``memory.store``) and ranks by cosine. Falls
        back to substring matching if the embedder is unavailable — a retrieval
        miss must not break the calling loop.
        """
        skills = [s for n in self.list() if (s := self.load(n)) is not None]
        if not skills:
            return []
        try:
            query_vec = self.embedder.embed([query])[0]
            texts = [f"{s.trigger} {s.description}" for s in skills]
            skill_vecs = self.embedder.embed(texts)
            scored = sorted(
                zip(skills, skill_vecs),
                key=lambda pair: _cosine(query_vec, pair[1]),
                reverse=True,
            )
            return [s for s, _ in scored[:k]]
        except Exception:  # noqa: BLE001 - degrade to keyword match, never break
            q = query.lower()
            matched = [
                s for s in skills
                if q in s.trigger.lower() or q in s.description.lower()
            ]
            return matched[:k]


def _is_python(text: str) -> bool:
    """True iff ``text`` parses as a python module (so the gate can run it)."""
    if not text.strip():
        return False
    try:
        compile(text, "<skill-body>", "exec")
        return True
    except SyntaxError:
        return False


def optimize_skill(
    skill: Skill,
    *,
    propose: Proposer,
    evaluate: Evaluator,
    gate: Gate,
    baseline_score: float,
    epochs: int,
    out_dir: str | Path,
    library: SkillLibrary | None = None,
    min_delta: float = 0.0,
    cwd: str | Path = ".",
) -> tuple[Skill, OptimizeResult]:
    """SkillOpt on agentkit's gate: optimize a skill body, deploy the best.

    ``optimize_text`` from ``evolve`` is pointed at the skill body. Each
    candidate body is admitted only by the gate (executed when it is python). On
    return the best body is written to ``out_dir/<name>.json`` + ``.md`` as the
    deployable artifact, and the baseline-vs-optimized delta is in the
    ``OptimizeResult``.

    Returns ``(best_skill, optimize_result)``.
    """
    proposal_code = (lambda text: text) if _is_python(skill.body) else None
    result = optimize_text(
        skill.body,
        propose=propose,
        evaluate=evaluate,
        gate=gate,
        baseline_score=baseline_score,
        epochs=epochs,
        min_delta=min_delta,
        cwd=cwd,
        proposal_type="skill",
        proposal_code=proposal_code,
    )
    best_skill = skill.with_body(result.best, eval_score=result.best_score)
    deployer = library or SkillLibrary(_NullEmbedder(), out_dir)
    if deployer.directory != Path(out_dir):
        deployer = SkillLibrary(deployer.embedder, out_dir)
    deployer.save(best_skill)
    return best_skill, result


class _NullEmbedder:
    """Embedder stub used only when ``optimize_skill`` deploys without a library."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


if __name__ == "__main__":
    import hashlib
    import tempfile

    from agentkit.sandbox.core import SubprocessSandbox

    class _HashEmbedder:
        def __init__(self, dim: int = 64) -> None:
            self.dim = dim

        def embed(self, texts: list[str]) -> list[list[float]]:
            out = []
            for t in texts:
                vec = [0.0] * self.dim
                for tok in t.lower().split():
                    h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
                    vec[h % self.dim] += 1.0
                out.append(vec)
            return out

    with tempfile.TemporaryDirectory() as d:
        lib = SkillLibrary(_HashEmbedder(), Path(d) / "SKILLS")

        # propose: a fake client returns a skill JSON.
        class _Client:
            def chat(self, messages, tools=None):  # type: ignore[no-untyped-def]
                from agentkit.types import ChatResult

                return ChatResult(
                    text=json.dumps({
                        "name": "binary_search",
                        "description": "Find an element in a sorted list quickly.",
                        "trigger": "searching a sorted collection",
                        "steps": ["set lo, hi", "loop until lo>hi", "compare mid"],
                    })
                )

        proposed = lib.propose(_Client(), "agent solved a sorted-search task")
        assert proposed is not None and proposed.name == "binary_search", proposed

        # add: gate-verify before save. Runnable python body + improving -> ACCEPT.
        runnable = proposed.with_body("print('ok')")
        gate = Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9, cwd=d)
        verdict, path = lib.add(runnable, gate=gate, baseline_score=0.5)
        assert verdict.status is Outcome.ACCEPT and path is not None, verdict
        assert "binary_search" in lib.list(), lib.list()

        # add a side-effecting body -> ESCALATE -> NOT saved.
        danger = Skill(name="reaper", description="x", body="import subprocess")
        v2, p2 = lib.add(danger, gate=gate, baseline_score=0.0)
        assert v2.status is Outcome.ESCALATE and p2 is None, v2
        assert "reaper" not in lib.list(), lib.list()

        # retrieve: semantic similarity surfaces the saved skill.
        hits = lib.retrieve("how do I search a sorted array", k=3)
        assert hits and hits[0].name == "binary_search", hits

        # optimize_skill: SkillOpt loop deploys the best body + reports delta.
        def _scorer(body: str) -> float:
            return min(1.0, body.count("step") / 3.0)

        seed = Skill(name="grow", description="d", body="print('step')")

        def _grow(best: str, _hist) -> str | None:  # type: ignore[no-untyped-def]
            return best.replace("print('step", "print('step step")

        out = Path(d) / "DEPLOY"
        best, res = optimize_skill(
            seed,
            propose=_grow,
            evaluate=_scorer,
            gate=Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: _scorer(p["content"]), cwd=d),
            baseline_score=_scorer(seed.body),
            epochs=3,
            out_dir=out,
        )
        assert res.delta >= 0.0 and (out / "grow.json").exists(), res
        assert best.eval_score == res.best_score, (best, res)

    print("skills.core self-check OK")
