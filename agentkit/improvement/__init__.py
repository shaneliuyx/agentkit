"""agentkit.improvement — cross-session improvement primitives.

Thin re-export layer so shared-library consumers can import from
``agentkit.improvement`` without depending directly on ``studio``.
The backing implementations live in ``studio.task_runs``.
"""

from __future__ import annotations


def _lazy():
    try:
        from studio.task_runs import (  # type: ignore[import]
            TaskRun,
            TaskRunStore,
            mine_weaknesses_from_outputs,
            score_result,
            task_hash,
        )
        return TaskRun, TaskRunStore, mine_weaknesses_from_outputs, score_result, task_hash
    except ImportError as exc:
        raise ImportError(
            "agentkit.improvement requires studio.task_runs — "
            "import from studio.task_runs directly when studio is available."
        ) from exc


def __getattr__(name: str):
    _exports = {
        "TaskRun", "TaskRunStore", "mine_weaknesses_from_outputs",
        "score_result", "task_hash",
    }
    if name in _exports:
        tr, trs, mine, score, th = _lazy()
        return {"TaskRun": tr, "TaskRunStore": trs,
                "mine_weaknesses_from_outputs": mine,
                "score_result": score, "task_hash": th}[name]
    raise AttributeError(f"module 'agentkit.improvement' has no attribute {name!r}")
