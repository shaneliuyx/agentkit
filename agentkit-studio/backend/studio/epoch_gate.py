"""studio.epoch_gate — Phase-1 keep/discard gate for hill-climb epochs (DESIGN §11.5).

Closes Studio's open-loop accept. Before this gate the runner wrote any epoch's
artifact back as long as it was not *shorter* than the seed (a length-only ratchet),
so a same-length-but-worse rewrite silently replaced a good report. This module makes
acceptance a CLOSED loop: an epoch is KEPT only if a label-free judge STRICTLY prefers
it over the prior best (the seed). On reject the prior is retained, so the
carry-forward seed never regresses ("worst case = no improvement, never regression").

Why label-free preference and not the score: the absolute hill-climb score
(`_weakness_score`, solved/total) is noisy and has changed across versions, and it is
reward-hackable (an empty artifact mines 0 weaknesses → 1.0). This gate never reads a
score — it reuses ``agentkit.evolve.self_preference`` (RHO pairwise preference), which
only answers "is the new epoch better than the prior?".

Kept deliberately tiny and dependency-light so it lives OUTSIDE the already-large
runner.py (see "runner.py optimize" follow-up in DESIGN §11.5).
"""
from __future__ import annotations

from typing import Any, Callable

#: (new, prior) -> net preference; >0 means the new artifact is better.
PreferFn = Callable[[str, str], int]


def accept_epoch(new_text: str, prior_text: str, prefer: PreferFn) -> bool:
    """Keep this epoch's artifact iff it strictly beats the prior best.

    Rules:
      * No prior (cold start)        -> accept (nothing to beat).
      * Empty new vs a real prior    -> reject (retain the prior good report).
      * Otherwise                    -> accept iff ``prefer(new, prior) > 0``.

    A tie keeps the prior, matching ``agentkit.evolve.core.optimize_text``'s
    "strictly improves the best" admission rule.
    """
    if not (prior_text or "").strip():
        return True
    if not (new_text or "").strip():
        return False
    return prefer(new_text, prior_text) > 0


def make_preference(base_client: Any, requirement: str) -> PreferFn:
    """Build a label-free preference fn from ``agentkit.evolve.self_preference``.

    Judges new-vs-prior against the task with no ground-truth label. Fails OPEN
    (returns +1 = accept) when the judge errors or the lib is absent, so the gate is
    never *worse* than the old ungated behavior — a judge outage cannot strand a real
    epoch's work.
    """
    def _prefer(new: str, prior: str) -> int:
        try:
            from agentkit.evolve.core import self_preference
            return self_preference(
                base_client, new, prior, judge_inputs=[requirement[:2000]]
            )
        except Exception:  # noqa: BLE001 — fail open: never strand a real epoch
            return 1
    return _prefer


def make_rubric_preference(
    verified_urls: Any = None,
    *,
    weights: Any = None,
    required_sections: Any = None,
    eps: float = 0.01,
) -> PreferFn:
    """Build a DETERMINISTIC preference fn from the research-report rubric (DESIGN §11.6).

    This is the DEFAULT gate judge: live testing proved an LLM "which is better?" judge
    ties a strong report with a stub even on sonnet (DESIGN §11.5 D4), whereas
    ``studio.rubric.rubric_score`` separates them cleanly and reproducibly. ``weights`` and
    ``required_sections`` are the GUI-supplied rubric config + deliverable template; ``eps``
    is a minimum margin so micro-noise can't flip keep/discard (mirrors optimize_text's
    ``min_delta``). ``verified_urls`` is the accuracy oracle.
    """
    from studio.rubric import rubric_score

    def _prefer(new: str, prior: str) -> int:
        d = (
            rubric_score(new, verified_urls, weights, required_sections)
            - rubric_score(prior, verified_urls, weights, required_sections)
        )
        return 1 if d > eps else (-1 if d < -eps else 0)
    return _prefer
