"""agentkit.loop — loop engineering shared library."""
from agentkit.loop.chain import ChainResult, LoopChain, LoopSpec, SpecResult
from agentkit.loop.goal import LoopGoal, StopVerdict, check_goal
from agentkit.loop.hill_climb import TraceWeakness, hill_climb_from_traces, mine_weaknesses

__all__ = [
    "LoopGoal", "StopVerdict", "check_goal",
    "TraceWeakness", "mine_weaknesses", "hill_climb_from_traces",
    "LoopSpec", "LoopChain", "ChainResult", "SpecResult",
]

from agentkit.loop.suggest import GoalSuggestion, suggest_goal_params, ChainSpecSuggestion, suggest_chain_spec

__all__ = [
    "LoopGoal", "StopVerdict", "check_goal",
    "LoopSpec", "SpecResult", "ChainResult", "LoopChain",
    "TraceWeakness", "mine_weaknesses", "hill_climb_from_traces",
    "GoalSuggestion", "suggest_goal_params", "ChainSpecSuggestion", "suggest_chain_spec",
]
