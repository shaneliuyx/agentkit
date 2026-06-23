"""studio.panels.evolve — evolve panel (SPEC §5.5 #3).

``evolve.distill_group`` runs Training-Free GRPO over a fan-out's candidate
rollouts: score each with an injected verifier, keep the natural-language
lessons from strictly-above-mean rollouts, demote below-mean ones to
counter-lessons. The control is fully deterministic and model-free (the verifier
is the only seam), so the panel produces a real result with no network.

Studio feeds a STAR/MESH phase's per-worker drafts in as rollouts. Since the
runner delegates each phase to a single-step sub-plan (it sees the reduced
output, not the per-worker drafts), the panel scores the candidate outputs it is
given — for the GUI this is the phase output plus any sibling phase outputs — by
length-proxy reward, emitting one ``evolve`` frame per distillation round.
"""

from __future__ import annotations

from agentkit.evolve.core import Rollout, distill_group

from studio.events import EvolveEvent


def _length_reward(rollout: Rollout) -> float:
    """A cheap, deterministic verifier: longer, more-developed lessons score
    higher. A length proxy is honest about being a proxy — the panel demonstrates
    the group-relative keep/discard mechanism, not a task-grounded metric."""
    return float(len(rollout.lesson.split()))


def build_evolve_event(round_n: int, candidates: list[str]) -> EvolveEvent:
    """Distill a group of candidate outputs → one ``EvolveEvent``.

    ``score`` is the group mean reward; ``delta`` is best-minus-mean (how far the
    top keeper beat the group); ``variant`` is the first kept lesson (the
    above-mean keeper), or "" when nothing beat the mean.
    """
    rollouts = [Rollout(lesson=c) for c in candidates if c and c.strip()]
    if not rollouts:
        return EvolveEvent(round=round_n, score=0.0, delta=0.0, variant="")

    dist = distill_group(rollouts, verifier=_length_reward)
    rewards = [_length_reward(r) for r in rollouts]
    best = max(rewards) if rewards else 0.0
    variant = dist.lessons[0] if dist.lessons else ""
    return EvolveEvent(
        round=round_n,
        score=round(dist.mean, 4),
        delta=round(best - dist.mean, 4),
        variant=variant[:200],
    )
