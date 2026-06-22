"""agentkit.skills — a gate-verified, semantically-retrieved skill library.

The second thin target over the shared ``agentkit.evolve`` optimizer: a skill's
body is a text artifact, so ``optimize_skill`` is ``optimize_text`` pointed at it,
emitting a deployable best-skill file + the baseline-vs-optimized delta (the
SkillOpt loop on agentkit's LEARN gate). The library is propose -> verify -> save
(nothing enters the trusted set without passing the ``Gate``) with semantic
retrieval over an injected ``Embedder``.

Ported from ``self-improving-agents-curriculum/scaffold/skills/library.py``;
SkillOpt framing from microsoft/SkillOpt (MIT).
"""

from agentkit.skills.core import (
    Skill,
    SkillLibrary,
    optimize_skill,
)

__all__ = [
    "Skill",
    "SkillLibrary",
    "optimize_skill",
]
