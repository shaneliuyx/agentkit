"""Tests for studio.models.LoopConfig."""

from studio.models import LoopConfig


def test_defaults():
    cfg = LoopConfig()
    assert cfg.auto_improve is True
    assert cfg.deliverable_path is None
    assert cfg.min_tasks_per_agent == 3
    assert cfg.max_tasks_per_agent == 5


def test_sizing_returns_sizing_config():
    from agentkit.topology.sizing import SizingConfig
    cfg = LoopConfig(min_tasks_per_agent=2, max_tasks_per_agent=8)
    sizing = cfg.sizing()
    assert isinstance(sizing, SizingConfig)
    assert sizing.min_tasks_per_agent == 2
    assert sizing.max_tasks_per_agent == 8


def test_from_dict_empty():
    cfg = LoopConfig.from_dict({})
    assert cfg.auto_improve is True
    assert cfg.deliverable_path is None


def test_from_dict_partial():
    cfg = LoopConfig.from_dict({"auto_improve": False, "deliverable_path": "/tmp/out.md"})
    assert cfg.auto_improve is False
    assert cfg.deliverable_path == "/tmp/out.md"


def test_from_dict_full():
    cfg = LoopConfig.from_dict({
        "auto_improve": True,
        "deliverable_path": "/out/report.md",
        "min_tasks_per_agent": 2,
        "max_tasks_per_agent": 10,
    })
    assert cfg.min_tasks_per_agent == 2
    assert cfg.max_tasks_per_agent == 10


def test_from_dict_ignores_unknown_keys():
    cfg = LoopConfig.from_dict({"unknown_key": "ignored"})
    assert cfg.auto_improve is True
