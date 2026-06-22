"""agentkit.evolve — text-space optimization of an artifact against the gate.

The shared optimizer primitive (``optimize_text``) plus the DGM prompt-evolution
target built on it (``evolve_prompt`` / ``evolve_prompt_rho``). The keep/discard
CONTROL is deterministic and model-free; the injected LLM is ONLY the mutation
proposer, and every candidate is admitted solely through the LEARN ``Gate``.
``skills/`` is the second thin target over the same core.

Ported from ``self-improving-agents-curriculum/scaffold/evolve/loop.py``; the
epochs/validation-gate/deployable-best-artifact framing follows SkillOpt
(microsoft/SkillOpt, MIT).
"""

from agentkit.evolve.core import (
    Evaluator,
    OptimizeResult,
    Proposer,
    Variant,
    evolve_prompt,
    evolve_prompt_rho,
    make_llm_proposer,
    optimize_text,
    self_preference,
)

__all__ = [
    "optimize_text",
    "OptimizeResult",
    "Variant",
    "Proposer",
    "Evaluator",
    "evolve_prompt",
    "evolve_prompt_rho",
    "make_llm_proposer",
    "self_preference",
]
