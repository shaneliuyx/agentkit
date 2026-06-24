"""Tests for agentkit.loop.chain — LoopChain + LoopSpec."""
import pytest

from agentkit.loop.chain import ChainResult, LoopChain, LoopSpec


def _runner(label: str):
    def run(ctx: dict) -> dict:
        return {"label": label}
    return run


def test_linear_chain_runs_in_order():
    calls: list[str] = []

    def r_a(ctx: dict) -> dict:
        calls.append("a")
        return {"a": 1}

    def r_b(ctx: dict) -> dict:
        calls.append("b")
        return {"b": 2}

    result = (
        LoopChain()
        .add(LoopSpec("a", r_a))
        .add(LoopSpec("b", r_b, depends_on=("a",)))
        .run()
    )
    assert calls == ["a", "b"]
    assert result.status == "done"
    assert result.outputs["a"] == {"a": 1}
    assert result.outputs["b"] == {"b": 2}


def test_upstream_output_injected():
    def r_a(ctx: dict) -> dict:
        return {"value": 42}

    def r_b(ctx: dict) -> dict:
        assert ctx["_a_output"] == {"value": 42}, f"expected upstream output, got {ctx}"
        return {"got": True}

    (
        LoopChain()
        .add(LoopSpec("a", r_a))
        .add(LoopSpec("b", r_b, depends_on=("a",)))
        .run()
    )


def test_duplicate_name_raises():
    chain = LoopChain().add(LoopSpec("a", _runner("a")))
    with pytest.raises(ValueError, match="duplicate"):
        chain.add(LoopSpec("a", _runner("a")))


def test_unknown_dependency_raises():
    with pytest.raises(ValueError, match="unknown spec"):
        LoopChain().add(LoopSpec("a", _runner("a"), depends_on=("missing",)))


def test_chain_result_type():
    result = LoopChain().add(LoopSpec("x", _runner("x"))).run()
    assert isinstance(result, ChainResult)
    assert result.status == "done"


def test_initial_ctx_passed_to_runner():
    received: dict = {}

    def r(ctx: dict) -> dict:
        received.update(ctx)
        return {}

    LoopChain().add(LoopSpec("x", r)).run(initial_ctx={"seed": "hello"})
    assert received.get("seed") == "hello"


def test_spec_result_count():
    chain = (
        LoopChain()
        .add(LoopSpec("a", _runner("a")))
        .add(LoopSpec("b", _runner("b"), depends_on=("a",)))
    )
    result = chain.run()
    assert len(result.results) == 2
    assert result.results[0].name == "a"
    assert result.results[1].name == "b"
