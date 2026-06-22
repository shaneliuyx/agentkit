"""agentkit.evolve.core — text-space optimization of an artifact against the gate.

THE KEY DESIGN (REPLAN §4, Phase 4): ``evolve/`` and ``skills/`` are the *same*
operation — text-space optimization of an artifact, admitted only by the LEARN
gate. This module is that shared primitive (``optimize_text``) plus the
prompt-evolution target built on top of it (``evolve_prompt`` /
``evolve_prompt_rho``). ``skills/`` is the second thin target, reusing the same
core.

The control logic is DETERMINISTIC and model-free: the keep/discard decision,
the archive, the strictly-improving check, and the baseline-vs-best delta are
all unit-testable without a network. The LLM is injected ONLY as the *mutation
proposer* (a ``Proposer`` callable); it never decides what is kept. A candidate
is admitted ONLY through ``gate.run_gate(...)``: a REJECT or ESCALATE candidate
is discarded, never auto-kept. This is the scaffold's L2 discipline, preserved.

Ported and re-seamed from
``self-improving-agents-curriculum/scaffold/evolve/loop.py`` (DGM keep/discard,
archive, weakness-targeting, and the RHO label-free self-preference mode). The
scaffold's ``backends.adapter.chat`` / ``backends.router.route`` /
``config.settings`` imports are replaced by agentkit's injected
``types.LLMClient`` and the injected ``agentkit.gates.Gate``. The framing of a
deployable best-artifact + a baseline-vs-optimized pass-rate delta is from
SkillOpt (microsoft/SkillOpt, MIT): "train skills like neural nets — epochs,
minibatch, validation gates, no weights."

DGM provenance: Darwin Gödel Machine (https://arxiv.org/abs/2505.22954) — applied
ONLY to prompts/config text, never weights, so it stays safe + reversible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agentkit.gates.core import Gate, Outcome
from agentkit.types import LLMClient, Message

# A Proposer takes (current_best_text, history_of_variants) and returns the next
# candidate text — or None when it has no proposal (parse failure, exhaustion).
# It is the ONLY injected-LLM seam in the loop; everything around it is model-free.
Proposer = Callable[[str, "tuple[Variant, ...]"], "str | None"]

# An Evaluator scores a candidate artifact in [0, 1]. Injected, deterministic
# from the loop's point of view (its number is trusted as the optimization metric).
Evaluator = Callable[[str], float]


@dataclass(frozen=True)
class Variant:
    """One immutable candidate produced during optimization.

    Attributes:
        text:   the candidate artifact text.
        score:  the evaluator score in [0, 1].
        epoch:  the 1-based epoch that produced it.
        parent: the text the proposer mutated from (the prior best).
        note:   a one-line mutation description (from the proposer, if any).
        status: the gate Outcome value ("accept" | "reject" | "escalate").
    """

    text: str
    score: float
    epoch: int
    parent: str = ""
    note: str = ""
    status: str = Outcome.REJECT.value


@dataclass(frozen=True)
class OptimizeResult:
    """The immutable outcome of an optimization run.

    Attributes:
        best:           the best (deployable) artifact text found.
        best_score:     the evaluator score of ``best``.
        baseline_score: the score the run started from.
        delta:          ``best_score - baseline_score`` (the headline win).
        archive:        the ordered tuple of accepted, strictly-improving variants.
        accepted:       how many candidates the gate ACCEPTED.
        epochs:         how many epochs ran.
    """

    best: str
    best_score: float
    baseline_score: float
    delta: float
    archive: tuple[Variant, ...] = ()
    accepted: int = 0
    epochs: int = 0


def optimize_text(
    artifact: str,
    *,
    propose: Proposer,
    evaluate: Evaluator,
    gate: Gate,
    baseline_score: float,
    epochs: int,
    min_delta: float = 0.0,
    cwd: str | Path = ".",
    proposal_type: str = "prompt",
    proposal_code: Callable[[str], str] | None = None,
) -> OptimizeResult:
    """Text-space keep/discard optimization of ``artifact`` against ``gate``.

    Each epoch: ``propose`` mutates the *current best* text; the candidate is
    scored by ``evaluate`` and admitted ONLY via ``gate.run_gate(...)``. A
    candidate is kept (best updated, archived) iff the gate returns ACCEPT *and*
    it strictly improves the running best. REJECT/ESCALATE candidates are
    discarded — never auto-kept. The control flow is fully deterministic; the
    only model call is inside ``propose``.

    Args:
        artifact:       the starting text (the baseline best).
        propose:        injected ``Proposer`` (the LLM mutation seam).
        evaluate:       injected ``Evaluator`` scoring a text in [0, 1].
        gate:           the LEARN ``Gate`` every candidate must pass.
        baseline_score: the score of ``artifact``.
        epochs:         number of mutation/keep-discard rounds.
        min_delta:      minimum improvement the gate's delta stage requires.
        cwd:            jailed working directory for the gate's sandbox stage.
        proposal_type:  the ``type`` field stamped on the gate proposal dict.
        proposal_code:  optional ``text -> code`` so the gate's execute stage
                        runs real code (skills/tools); default = no code, the
                        gate's execute stage is skipped for pure text.

    Returns:
        An ``OptimizeResult`` carrying the deployable best + baseline-vs-best delta.
    """
    best_text = artifact
    best_score = baseline_score
    archive: list[Variant] = []
    accepted = 0

    for epoch in range(1, epochs + 1):
        candidate_text = propose(best_text, tuple(archive))
        if not candidate_text or not candidate_text.strip():
            continue  # no proposal this epoch — model-free skip, never a crash

        try:
            candidate_score = evaluate(candidate_text)
        except Exception:  # noqa: BLE001 - a broken eval discards the candidate
            continue

        proposal: dict[str, Any] = {
            "type": proposal_type,
            "content": candidate_text,
            "note": f"epoch {epoch}",
        }
        if proposal_code is not None:
            proposal["code"] = proposal_code(candidate_text)

        # Admission is the gate's call alone. We pass the evaluator-derived score
        # by wrapping it in a gate whose evaluator returns it (see evolve_prompt),
        # OR rely on the gate's own evaluator. Here the gate re-scores via its
        # injected evaluator, so the loop and the gate agree on the metric.
        verdict = gate.run_gate(
            proposal, baseline_score=best_score, min_delta=min_delta
        )
        variant = Variant(
            text=candidate_text,
            score=candidate_score,
            epoch=epoch,
            parent=best_text,
            note=str(proposal.get("note", "")),
            status=verdict.status.value,
        )

        # Keep iff the gate ACCEPTED *and* it strictly improves the best. The
        # gate already enforces strict improvement against ``best_score``; the
        # explicit re-check keeps this loop correct even if a gate is swapped
        # for one with a laxer regression stage.
        if verdict.status is Outcome.ACCEPT and candidate_score > best_score:
            best_text = candidate_text
            best_score = candidate_score
            archive.append(variant)
            accepted += 1

    return OptimizeResult(
        best=best_text,
        best_score=best_score,
        baseline_score=baseline_score,
        delta=best_score - baseline_score,
        archive=tuple(archive),
        accepted=accepted,
        epochs=epochs,
    )


# ---------------------------------------------------------------------------
# Prompt-evolution target (DGM): optimize_text over a system prompt.
# ---------------------------------------------------------------------------

_MUTATION_SYSTEM = (
    "You are a meta-optimizer for an AI agent system prompt. Given the current "
    "system prompt and its performance, propose ONE targeted mutation that might "
    "improve task success. Make only ONE change. Never remove safety rules or "
    "gate instructions. Never add instructions to reach the internet or modify "
    "external files. Keep the prompt under 2000 characters. Output NOTHING but a "
    'single JSON object whose first character is "{": '
    '{"mutation_note": "<one sentence>", "mutated_prompt": "<the COMPLETE new prompt>"}'
)


def _extract_json_obj(raw: str) -> dict[str, Any] | None:
    """Best-effort JSON-object extraction from a (possibly chatty) reply."""
    text = (raw or "").strip()
    if "```" in text:
        text = "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("```")
        ).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def make_llm_proposer(
    client: LLMClient,
    *,
    weaknesses: str | None = None,
    extract_field: str = "mutated_prompt",
    system: str = _MUTATION_SYSTEM,
) -> Proposer:
    """Build a ``Proposer`` that asks an injected ``LLMClient`` for one mutation.

    Optional ``weaknesses`` (mined from reflection/failed trajectories) steers
    the mutation at observed failure patterns — the Self-Harness
    weakness-targeting link (https://arxiv.org/abs/2606.09498). The proposer
    never raises: a parse failure returns ``None`` and the epoch is skipped.
    """
    weakness_block = (
        f"\n\nObserved weaknesses to fix (target your mutation at THESE):\n{weaknesses}\n"
        if weaknesses and weaknesses.strip()
        else ""
    )

    def _propose(current_best: str, _history: tuple[Variant, ...]) -> str | None:
        messages: list[Message] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Current artifact:\n\n{current_best}\n{weakness_block}\n"
                    "Propose one mutation to improve task performance."
                ),
            },
        ]
        try:
            response = client.chat(messages)
            data = _extract_json_obj(getattr(response, "text", "") or "")
        except Exception:  # noqa: BLE001 - proposer failures are non-fatal
            return None
        if not isinstance(data, dict):
            return None
        candidate = str(data.get(extract_field, "")).strip()
        return candidate or None

    return _propose


def evolve_prompt(
    baseline_prompt: str,
    *,
    propose: Proposer,
    evaluate: Evaluator,
    gate: Gate,
    baseline_score: float,
    epochs: int,
    min_delta: float = 0.0,
    cwd: str | Path = ".",
) -> OptimizeResult:
    """DGM prompt evolution = ``optimize_text`` over a system prompt artifact.

    Thin wrapper that stamps ``proposal_type="prompt"`` and runs no code in the
    gate's execute stage (a system prompt has no code to execute). For
    weakness-targeting, build ``propose`` via ``make_llm_proposer(weaknesses=...)``.
    """
    return optimize_text(
        baseline_prompt,
        propose=propose,
        evaluate=evaluate,
        gate=gate,
        baseline_score=baseline_score,
        epochs=epochs,
        min_delta=min_delta,
        cwd=cwd,
        proposal_type="prompt",
    )


# ---------------------------------------------------------------------------
# Label-free self-preference (RHO, https://arxiv.org/abs/2606.05922).
# ---------------------------------------------------------------------------

_PREFERENCE_SYSTEM = (
    "You are comparing two AI agent artifacts for the SAME task. You have NO "
    "ground-truth label. Judge ONLY which is more correct, complete, and useful. "
    'Respond ONLY with JSON: {"winner": "A" | "B" | "tie"}'
)


def self_preference(
    client: LLMClient,
    candidate: str,
    baseline: str,
    *,
    judge_inputs: list[str],
) -> int:
    """Net label-free preference for ``candidate`` over ``baseline`` (RHO).

    For each input the injected LLM picks the better artifact with no label.
    Returns net wins: positive if the candidate is preferred, negative if the
    baseline is, 0 on a tie. Never raises — an unparseable judgment is a tie.
    """
    net = 0
    for item in judge_inputs:
        messages: list[Message] = [
            {"role": "system", "content": _PREFERENCE_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Input:\n{item}\n\nArtifact A:\n{candidate}\n\n"
                    f"Artifact B:\n{baseline}\n\nWhich is better?"
                ),
            },
        ]
        try:
            text = (getattr(client.chat(messages), "text", "") or "").strip()
            if text.startswith("```"):
                text = "\n".join(
                    ln for ln in text.splitlines() if not ln.strip().startswith("```")
                ).strip()
            winner = str(json.loads(text).get("winner", "tie")).upper()
        except Exception:  # noqa: BLE001 - inconclusive judgment is a tie
            winner = "TIE"
        if winner == "A":
            net += 1
        elif winner == "B":
            net -= 1
    return net


def evolve_prompt_rho(
    baseline_prompt: str,
    *,
    propose: Proposer,
    gate: Gate,
    client: LLMClient,
    judge_inputs: list[str],
    epochs: int,
    cwd: str | Path = ".",
) -> OptimizeResult:
    """Label-free prompt evolution (RHO): keep on self-preference, gate still applies.

    When there are no eval labels, the keep/discard decision is the agent's own
    pairwise self-preference (re-judged each epoch). The candidate's score is the
    *net positive* preference, so the same ``optimize_text`` machinery applies and
    the gate's regression/delta stages still enforce strict improvement. The
    safety/containment gate is never bypassed.
    """

    def _evaluate(candidate: str) -> float:
        net = self_preference(client, candidate, baseline_prompt, judge_inputs=judge_inputs)
        return float(max(net, 0))

    return optimize_text(
        baseline_prompt,
        propose=propose,
        evaluate=_evaluate,
        gate=gate,
        baseline_score=0.0,
        epochs=epochs,
        cwd=cwd,
        proposal_type="prompt",
    )


if __name__ == "__main__":
    import tempfile

    from agentkit.sandbox.core import SubprocessSandbox

    # Deterministic, model-free self-check: a proposer that appends a token each
    # epoch, an evaluator that scores by the count of that token, a gate whose
    # evaluator agrees. The loop should climb monotonically and archive accepts.
    def _scorer(text: str) -> float:
        return min(1.0, text.count("good") / 5.0)

    calls = {"n": 0}

    def _proposer(best: str, _hist: tuple[Variant, ...]) -> str | None:
        calls["n"] += 1
        return best + " good"

    with tempfile.TemporaryDirectory() as d:
        gate = Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: _scorer(p["content"]), cwd=d)
        result = optimize_text(
            "seed",
            propose=_proposer,
            evaluate=_scorer,
            gate=gate,
            baseline_score=_scorer("seed"),
            epochs=4,
        )
        assert result.best.count("good") == 4, result.best
        assert result.best_score > result.baseline_score, result
        assert result.delta > 0 and result.accepted == 4, result
        assert len(result.archive) == 4 and result.archive[0].status == "accept", result

        # A proposer that never improves -> zero accepts, best stays baseline.
        flat = optimize_text(
            "seed",
            propose=lambda b, h: "no signal here",
            evaluate=_scorer,
            gate=gate,
            baseline_score=_scorer("seed"),
            epochs=3,
        )
        assert flat.accepted == 0 and flat.best == "seed" and flat.delta == 0.0, flat

        # evolve_prompt wires the same core for a system-prompt artifact.
        ev = evolve_prompt(
            "base good good",
            propose=_proposer,
            evaluate=_scorer,
            gate=gate,
            baseline_score=_scorer("base good good"),
            epochs=2,
        )
        assert ev.best_score >= ev.baseline_score, ev

    print("evolve.core self-check OK")
