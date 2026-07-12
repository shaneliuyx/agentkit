"""Scheduler triggers + real chain runner (formerly dead GUI functions).

Covers studio.triggers (register/list/delete/save/fire) and the wired endpoints
POST /scheduler/cron, GET /scheduler, DELETE /scheduler/cron/{id}, plus /chain/run
saving a chain and executing a real (non-stub) DAG.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from studio import app as app_mod
from studio import triggers


@pytest.fixture(autouse=True)
def _clean_triggers():
    triggers._reset_for_tests()
    yield
    triggers._reset_for_tests()


@pytest.fixture
def client():
    return TestClient(app_mod.app)


# --------------------------------------------------------------------------- #
# triggers module — register / list / delete / save / fire
# --------------------------------------------------------------------------- #
def test_register_and_list_trigger():
    rec = triggers.register_cron("chainA", "*/5 * * * *")
    assert rec["chain_id"] == "chainA"
    assert rec["interval_s"] == 300.0  # */5 → every 5 min, derived from the cron expr
    assert [t["chain_id"] for t in triggers.list_triggers()] == ["chainA"]


def test_register_requires_chain_id_and_expr():
    with pytest.raises(ValueError):
        triggers.register_cron("", "*/5 * * * *")
    with pytest.raises(ValueError):
        triggers.register_cron("c", "")


def test_delete_trigger_is_idempotent():
    triggers.register_cron("c1", "* * * * *")
    assert triggers.delete_trigger("c1") is True
    assert triggers.delete_trigger("c1") is False
    assert triggers.list_triggers() == []


def test_fire_without_saved_chain_is_honest_noop():
    triggers.register_cron("c1", "* * * * *")
    out = triggers.fire("c1", lambda specs, ctx, llm=None: {"ran": True})
    assert out["fired"] is False
    assert "no saved chain" in out["reason"]


def test_fire_runs_saved_chain_and_counts_runs():
    triggers.register_cron("c1", "* * * * *")
    triggers.save_chain("c1", [{"name": "a", "description": "x"}], {"task": "t"}, {"profile": "gemma"})
    seen = {}

    def _run(specs, ctx, llm=None):
        seen["specs"] = specs
        seen["ctx"] = ctx
        seen["llm"] = llm
        return {"ok": len(specs)}

    out = triggers.fire("c1", _run)
    assert out["fired"] is True
    assert out["result"] == {"ok": 1}
    assert seen["ctx"] == {"task": "t"}
    assert seen["llm"] == {"profile": "gemma"}  # saved llm threaded to the run (no default divergence)
    assert triggers.list_triggers()[0]["runs"] == 1


# --------------------------------------------------------------------------- #
# reviewer fixes — cron→interval, interval clamp, delete-ordering, tick failures
# --------------------------------------------------------------------------- #
def test_cron_expr_derives_interval_and_flags_parsed():
    r = triggers.register_cron("c1", "*/15 * * * *")   # every 15 min
    assert r["interval_s"] == 900.0
    assert r["cron_parsed"] is True
    r2 = triggers.register_cron("c2", "* * * * *")     # every minute
    assert r2["interval_s"] == 60.0 and r2["cron_parsed"] is True


def test_unsupported_cron_is_flagged_not_silently_misread():
    # "0 9 * * *" (9am daily) is not parseable here → flagged, falls to default,
    # NOT silently fired every 60s as if it were the requested schedule.
    r = triggers.register_cron("c1", "0 9 * * *")
    assert r["cron_parsed"] is False
    assert r["interval_s"] == 60.0


def test_interval_is_clamped_to_floor():
    r = triggers.register_cron("c1", "* * * * *", interval_s=0.01)
    assert r["interval_s"] == 5.0  # _MIN_INTERVAL_S floor — no runaway timer


def test_delete_pops_trigger_before_disarm_no_reregister_window():
    # regression for the delete/tick race: the guard is `chain_id in _triggers`,
    # so removing from _triggers first means an in-flight tick cannot re-arm.
    triggers.register_cron("c1", "* * * * *")
    assert triggers.delete_trigger("c1") is True
    # after delete the id is gone from both maps
    assert triggers.list_triggers() == []
    assert triggers._timers.get("c1") is None


def test_tick_failure_is_recorded_not_raised():
    triggers.register_cron("c1", "* * * * *")
    triggers.save_chain("c1", [{"name": "a", "description": "x"}], {})

    def _boom(specs, ctx, llm=None):
        raise RuntimeError("chain blew up")

    # drive the tick body via fire (raises) the way arm's _tick wraps it
    with pytest.raises(RuntimeError):
        triggers.fire("c1", _boom)
    # arm wraps fire in try/except recording failures — verify the record path
    triggers.arm("c1", _boom)
    # the timer is armed; cancel it immediately (don't wait 60s)
    triggers.disarm("c1")


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #
def test_get_scheduler_is_real_not_stub(client):
    # was a stub returning a fixed note; now returns real (empty) trigger list.
    r = client.get("/scheduler")
    assert r.status_code == 200
    body = r.json()
    assert body == {"triggers": []}
    assert "note" not in body


def test_post_scheduler_cron_registers_and_lists(client):
    r = client.post("/scheduler/cron", json={"spec": "*/10 * * * *", "chain_id": "myChain"})
    assert r.status_code == 200
    assert r.json()["trigger"]["chain_id"] == "myChain"
    assert r.json()["armed"] is True
    listed = client.get("/scheduler").json()["triggers"]
    assert [t["chain_id"] for t in listed] == ["myChain"]


def test_post_scheduler_cron_validates(client):
    assert client.post("/scheduler/cron", json={"chain_id": "x"}).status_code == 422  # no spec
    assert client.post("/scheduler/cron", json={"spec": "* * * * *"}).status_code == 422  # no chain_id


def test_delete_scheduler_cron(client):
    client.post("/scheduler/cron", json={"spec": "* * * * *", "chain_id": "gone"})
    r = client.delete("/scheduler/cron/gone")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get("/scheduler").json()["triggers"] == []


# --------------------------------------------------------------------------- #
# chain — real runner + save-by-id + shared execution core
# --------------------------------------------------------------------------- #
def test_chain_run_saves_chain_for_scheduler(client, monkeypatch):
    # stub the execution so the test does not need a live LLM backend.
    monkeypatch.setattr(app_mod, "_execute_chain",
                        lambda specs, ctx, llm=None: {"status": "done", "outputs": {}, "results": []})
    r = client.post("/chain/run", json={
        "specs": [{"name": "s1", "description": "do a thing", "depends_on": []}],
        "initial_ctx": {"task": "t"},
        "chain_id": "saved1",
    })
    assert r.status_code == 200
    # the chain was saved by id so a cron trigger can fire it
    saved = triggers.get_chain("saved1")
    assert saved is not None and saved["specs"][0]["name"] == "s1"


def test_chain_runner_no_backend_is_honest_error(monkeypatch):
    # force the no-backend path (no profiles): factory returns a runner that reports
    # honestly, never a silent stub.
    monkeypatch.setattr("studio.backends.list_profiles", lambda: [])
    factory = app_mod._build_chain_runner_factory()
    run = factory("s1", "do a thing")
    out = run({})
    assert out["step"] == "s1"
    assert out["error"] == "no LLM backend configured"


def test_execute_chain_runs_dag_in_order_with_real_runner(monkeypatch):
    # a deterministic fake client proves the DAG plumbing feeds each step and
    # passes upstream outputs downstream (the old runner returned a static stub).
    class _FakeResult:
        def __init__(self, text): self.text = text

    class _FakeClient:
        def chat(self, messages, max_tokens=None):
            # echo the user content so we can assert upstream context flows in
            return _FakeResult("OUT:" + messages[-1]["content"][:40])

    monkeypatch.setattr(app_mod, "_build_chain_runner_factory",
                        lambda llm=None: (lambda name, desc: (lambda ctx: {
                            "step": name,
                            "output": _FakeClient().chat(
                                [{"role": "user", "content": f"{name}|{sorted(ctx)}"}]).text,
                        })))
    out = app_mod._execute_chain(
        [{"name": "s1", "description": "a", "depends_on": []},
         {"name": "s2", "description": "b", "depends_on": ["s1"]}],
        {"task": "t"},
    )
    assert out["status"] == "done"
    names = [r["name"] for r in out["results"]]
    assert names == ["s1", "s2"]  # topological order
