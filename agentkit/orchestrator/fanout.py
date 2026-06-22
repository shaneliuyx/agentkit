"""agentkit.orchestrator.fanout — P39 fan-out cost aggregation + parent ceiling.

``stall.exceeds_budget`` bounds ROUNDS and WALL-SECONDS — neither of which sees
how many tokens a fan-out's children burned. A per-child ``max_tokens`` cap does
NOT bound the total: N children at the per-child limit is an N× blow-up. The
bound has to live at the PARENT — aggregate every child's cost into one running
sum and abort the whole fan-out the moment that sum crosses a ceiling.

``FanoutBudget`` is the parent-level running sum. Child token counts come from
``ChatResult.total_tokens`` (the ``ChatResponse`` protocol exposes ``total_tokens``),
so ``add(result)`` reads usage straight off the result — no network, no model.
A plain ``int`` cost is also accepted for callers whose result type does not
carry usage (inject your own ``cost_of``).

This is the deterministic, LLM-free sibling of ``exceeds_budget``: same shape
(a pure predicate ``exceeds_fanout_budget``) plus a small mutable accumulator.
Mirrors deer-flow ``subagents/token_collector.py`` (sum to parent, check the
running sum) and the W4.6 lab ``src/cost_ceiling.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentkit.types import ChatResponse


def cost_of(result: ChatResponse | int) -> int:
    """Token cost of one child result (PURE, model-free).

    Reads ``total_tokens`` off a ``ChatResponse`` (e.g. ``ChatResult``); accepts
    a plain ``int`` unchanged so callers without usage on their result type can
    inject a deterministic cost.
    """
    if isinstance(result, int):
        return result
    return int(result.total_tokens)


def exceeds_fanout_budget(spent: float, ceiling: float) -> bool:
    """Return True when the summed child spend has crossed the ceiling (PURE).

    Sibling of ``stall.exceeds_budget`` (rounds/wall-seconds): this one bounds
    the aggregate child token spend. Strictly-greater so a fan-out that lands
    exactly on the ceiling still completes.
    """
    return spent > ceiling


@dataclass
class FanoutBudget:
    """Parent-level running token sum with a hard ceiling.

    ``add(result)`` adds the child's ``total_tokens`` (or an injected int) to the
    running ``spent_total`` and raises ``BudgetExceeded`` the instant the
    *aggregate* crosses ``ceiling`` — the whole point is the check is on the SUM,
    so N cheap children still trip it, which N per-child caps never would.
    """

    ceiling: float
    spent_total: float = 0.0

    def add(self, result: ChatResponse | int) -> None:
        """Charge one child's cost to the running sum; raise if it crosses."""
        self.spent_total += cost_of(result)
        if self.exceeds():
            raise BudgetExceeded(self.spent_total, self.ceiling)

    def spent(self) -> float:
        """The aggregate child token spend so far."""
        return self.spent_total

    def exceeds(self, ceiling: float | None = None) -> bool:
        """True when the running sum has crossed ``ceiling`` (default: own)."""
        return exceeds_fanout_budget(
            self.spent_total, self.ceiling if ceiling is None else ceiling
        )

    @property
    def remaining(self) -> float:
        return max(0.0, self.ceiling - self.spent_total)


class BudgetExceeded(RuntimeError):
    """Raised by ``FanoutBudget.add`` when the running sum crosses the ceiling."""

    def __init__(self, spent: float, ceiling: float) -> None:
        super().__init__(f"fan-out token spend {spent} exceeded ceiling {ceiling}")
        self.spent = spent
        self.ceiling = ceiling


if __name__ == "__main__":
    from agentkit.types import ChatResult

    # cost_of reads total_tokens off a ChatResult and passes ints through.
    assert cost_of(ChatResult(text="hi", total_tokens=7)) == 7
    assert cost_of(13) == 13

    # Under-ceiling: 3 children at 100 each = 300 < 350 → completes, no raise.
    under = FanoutBudget(ceiling=350)
    for _ in range(3):
        under.add(ChatResult(total_tokens=100))
    assert under.spent() == 300 and under.exceeds() is False, under

    # Over-ceiling: N=10 children at k=100; N*k = 1000 > 350. Trips on child 4
    # (running sum 400 > 350) and reports the SUMMED cost — a per-child cap of
    # 100 would never have bounded the 1000-token total.
    over = FanoutBudget(ceiling=350)
    aborted_at = None
    for i in range(10):
        try:
            over.add(ChatResult(total_tokens=100))
        except BudgetExceeded as exc:
            aborted_at = i
            assert exc.spent == 400 and exc.ceiling == 350, exc
            break
    assert aborted_at == 3 and over.spent() == 400, (aborted_at, over.spent())

    # Pure predicate sibling of exceeds_budget.
    assert exceeds_fanout_budget(400, 350) is True
    assert exceeds_fanout_budget(350, 350) is False

    print("fanout self-check OK")
