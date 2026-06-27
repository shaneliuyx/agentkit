"""Tests for agentkit.topology.sizing."""

from agentkit.topology.sizing import SizingConfig, assign_tasks, compute_n_agents


def test_compute_n_agents_zero():
    assert compute_n_agents(0) == 1


def test_compute_n_agents_at_max():
    cfg = SizingConfig(max_tasks_per_agent=5)
    assert compute_n_agents(5, cfg) == 1


def test_compute_n_agents_one_over():
    cfg = SizingConfig(max_tasks_per_agent=5)
    assert compute_n_agents(6, cfg) == 2


def test_compute_n_agents_exact_multiple():
    cfg = SizingConfig(max_tasks_per_agent=5)
    assert compute_n_agents(10, cfg) == 2


def test_compute_n_agents_ceil():
    cfg = SizingConfig(max_tasks_per_agent=5)
    assert compute_n_agents(11, cfg) == 3


def test_assign_tasks_empty():
    result = assign_tasks([])
    assert result == [[]]


def test_assign_tasks_single_agent():
    cfg = SizingConfig(max_tasks_per_agent=5)
    tasks = ["a", "b", "c"]
    result = assign_tasks(tasks, cfg)
    assert len(result) == 1
    assert result[0] == tasks


def test_assign_tasks_two_agents():
    cfg = SizingConfig(max_tasks_per_agent=5)
    tasks = list(range(6))
    result = assign_tasks(tasks, cfg)
    assert len(result) == 2
    assert sum(len(b) for b in result) == len(tasks)


def test_assign_tasks_all_tasks_covered():
    cfg = SizingConfig(max_tasks_per_agent=3)
    tasks = list(range(10))
    buckets = assign_tasks(tasks, cfg)
    flat = [t for b in buckets for t in b]
    assert flat == tasks
