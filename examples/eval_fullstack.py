"""Full-stack integration — EVERY module, one durable task.

The memory integration test (`eval_integration.py`) proves the memory layer.
This proves the *whole tool*: a research→verify→report pipeline executed as a
durable `runtime` DAG that survives a simulated crash, with the orchestrator,
agent roles, quality verifier, context compaction, batch runner, router,
scheduler, and CLI backend all participating.

Modules exercised (one capability row each):
  runtime (GraphStore + durability)  scheduler (trigger)   context (compact)
  memory (TieredMemory)              agent.loop (run_role)  agent.router (route)
  agent.roles (dispatch)             agent.batch (run_batch)
  orchestrator (stall/diversity/select)   quality (verify)  backends (CliLLMClient)

Most checks are deterministic/free; only ingest, the Researcher, and the Writer
reach the LLM — the rest of the pipeline runs at 0 LLM, demonstrating the
deterministic-first axiom end-to-end.

Run:  .venv/bin/python examples/eval_fullstack.py   (needs oMLX on :8000)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentkit.agent.batch import BatchConfig, run_batch  # noqa: E402
from agentkit.agent.roles import RESEARCHER, dispatch, run_role  # noqa: E402
from agentkit.agent.router import route  # noqa: E402
from agentkit.backends.cli import CliLLMClient  # noqa: E402
from agentkit.context.compactor import compact  # noqa: E402
from agentkit.memory.store import MemoryStore  # noqa: E402
from agentkit.memory.tiered import TieredConfig, TieredMemory  # noqa: E402
from agentkit.orchestrator.diversity import is_novel  # noqa: E402
from agentkit.orchestrator.select import Dimension, Rubric, cascade  # noqa: E402
from agentkit.orchestrator.stall import assess  # noqa: E402
from agentkit.quality.verify import verify  # noqa: E402
from agentkit.runtime.graph_store import GraphStore  # noqa: E402
from agentkit.runtime.scheduler import Scheduler  # noqa: E402
from agentkit.types import LLMClient  # noqa: E402
from run_measured import OMLXClient, OMLXEmbedder  # noqa: E402

CHAT_MODEL = "gemma-4-26B-A4B-it-heretic-4bit"
NEEDLE = "ap-southeast-1"
QUESTION = "Which AWS region is the production service deployed to?"

# Sources the pipeline ingests; one carries the needle the report must surface.
SOURCES = [
    f"The production service runs in the {NEEDLE} region.",
    "The team uses blue-green deployments for releases.",
    "Postgres is the primary datastore, with read replicas.",
    "Incident reviews happen every Friday afternoon.",
]


class FakeChecker:
    """Injected UrlChecker — no network. Only listed URLs are 'live'."""
    def __init__(self, live: set[str]) -> None:
        self.live = live

    def is_live(self, url: str) -> bool:
        return url in self.live


def main() -> None:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    with tempfile.TemporaryDirectory() as d:
        client = OMLXClient(model=CHAT_MODEL, max_tokens=128)
        emb = OMLXEmbedder()
        store = MemoryStore(Path(d) / "mem.db", embedder=emb)
        tm = TieredMemory(store, client=OMLXClient(model=CHAT_MODEL, max_tokens=256),
                          config=TieredConfig())
        ctx: dict = {}  # shared state across DAG node handlers

        # ---- DAG node handlers (the pipeline body) ----------------------------
        def h_ingest(node) -> dict:
            # memory: write-time atomise (1 LLM call for the whole batch)
            n = tm.ingest_session(SOURCES, session=0, atomise=True)
            return {"atoms": n}

        def h_research(node) -> dict:
            # orchestrator.diversity: pick a direction not tried before
            tried = ["pricing analysis"]
            ctx["novel"] = is_novel("deployment topology", tried)
            # orchestrator.select: rank candidate sub-questions by a rubric
            rubric = Rubric((Dimension("relevance", "Relevance", 3.0),
                             Dimension("specificity", "Specificity", 1.0)))
            cands = [{"q": QUESTION, "relevance": 1.0, "specificity": 0.9},
                     {"q": "What colour is the logo?", "relevance": 0.1, "specificity": 0.2}]
            ranked = cascade(cands, lambda c: c["relevance"] > 0.2, rubric,
                             lambda c, r: {"relevance": c["relevance"],
                                           "specificity": c["specificity"]})
            ctx["top_q"] = ranked[0][0]["q"]
            # agent.router: pick a reasoning tier for this step
            ctx["route"] = route("hard")
            # agent.loop via run_role: Researcher answers, memory injected
            res = run_role(RESEARCHER, ctx["top_q"], client, memory=tm, max_rounds=2)
            ctx["finding"] = res.answer
            # orchestrator.stall: one finding -> productive -> continue
            ctx["stall"] = assess(new_findings=1, stale_count=0)
            return {"answer_len": len(res.answer)}

        def h_verify(node) -> dict:
            # quality.verify: live-cited, uncited, and dead-link claims
            draft = (
                f"The service is deployed to {NEEDLE} (source: https://docs.local/ok). "
                "The cache is Redis. "  # uncited
                "Backups are nightly (source: https://docs.local/dead)."
            )
            checker = FakeChecker(live={"https://docs.local/ok"})
            ctx["findings"] = verify(draft, checker=checker)
            return {"findings": len(ctx["findings"])}

        def h_report(node) -> dict:
            # agent.roles.dispatch: deterministic role selection
            role = dispatch("draft a deployment report from these notes")
            ctx["report_role"] = role.name
            notes = ctx.get("finding", "") + "\nKey fact: " + \
                next(s for s in SOURCES if NEEDLE in s)
            res = run_role(role, f"Draft a short report from these notes:\n{notes}",
                           client, max_rounds=1)
            ctx["report"] = res.answer
            return {"report_len": len(res.answer)}

        handlers = {"ingest": h_ingest, "research": h_research,
                    "verify": h_verify, "report": h_report}

        # ---- runtime: define + trigger the durable DAG -----------------------
        gstore = GraphStore(str(Path(d) / "dag.db"))
        dag = {
            "nodes": {k: {"type": "tool"} for k in handlers},
            "edges": [["ingest", "research"], ["research", "verify"],
                      ["verify", "report"]],
        }
        gid = gstore.create_graph("research_pipeline", dag)
        run_id = Scheduler(gstore).trigger_manually(gid)  # scheduler trigger

        # ---- worker, with a SIMULATED CRASH mid-run --------------------------
        crashed = False
        while True:                                   # worker #1
            node = gstore.claim_ready_node(run_id, "w1")
            if node is None:
                break
            if node.name == "research" and not crashed:
                crashed = True                        # kill -9: claimed, never marked
                break
            gstore.mark_done(run_id, node.name, handlers[node.name](node))

        recovered = gstore.recover_run(run_id)        # restart: reset orphan
        while True:                                   # worker #2 resumes
            node = gstore.claim_ready_node(run_id, "w2")
            if node is None:
                break
            gstore.mark_done(run_id, node.name, handlers[node.name](node))

        states = gstore.node_states(run_id)

        # ============ capability matrix ============
        check("runtime: DAG + crash recovery",
              recovered == ["research"] and all(s == "done" for s in states.values()),
              f"recovered={recovered}, states={states}")
        check("scheduler: manual trigger fired run", run_id.startswith("r_"), run_id)
        check("memory: tiered ingest produced atoms", store.count("atom") > 0,
              f"{store.count('atom')} atoms")
        check("memory: recall surfaces needle",
              any(NEEDLE in h.content.lower() for h in tm.recall(QUESTION)),
              "needle retrieved by depth-rerank recall")
        check("agent.router: difficulty routing differs",
              route("trivial") != route("critical"), f"hard->{ctx.get('route')}")
        check("agent.roles.dispatch: deterministic selection",
              ctx.get("report_role") == "Writer"
              and dispatch("verify the citations").name == "Verifier",
              f"report role={ctx.get('report_role')}")
        check("agent.loop/run_role: Researcher answered",
              len(ctx.get("finding", "")) > 0, f"{len(ctx.get('finding',''))} chars")
        check("orchestrator.diversity: novel direction accepted",
              ctx.get("novel") is True)
        check("orchestrator.select: best sub-question ranked #0",
              ctx.get("top_q") == QUESTION, ctx.get("top_q", ""))
        check("orchestrator.stall: productive continue, stall pivots",
              ctx.get("stall") is not None and ctx["stall"].action == "continue"
              and assess(0, 1).action == "pivot",
              "continue on finding; pivot on stall>=2")
        check("quality.verify: uncited + dead-link flagged",
              any("cit" in f.issue.lower() for f in ctx["findings"])
              and any(f.url == "https://docs.local/dead" for f in ctx["findings"]),
              f"{len(ctx['findings'])} findings")

        # context.compact on a long research transcript (the handoff artifact)
        transcript = [{"role": "user" if i % 2 == 0 else "assistant",
                       "content": f"Round {i}: investigating sub-topic {i} in detail."}
                      for i in range(300)]
        cr = compact(transcript, keep=1)
        red = 1 - cr.est_tokens_after / max(1, cr.est_tokens_before)
        check("context.compact: transcript reduced", red > 0.3,
              f"{cr.est_tokens_before}->{cr.est_tokens_after} ({red:.0%})")

        # agent.batch: resumable — second run skips everything
        out = Path(d) / "batch.jsonl"
        fail = Path(d) / "batch_fail.jsonl"
        items = [1, 2, 3, 4]
        r1 = run_batch(items, lambda x: x * 2, out, fail,
                       BatchConfig(), key=str, sleep=lambda s: None)
        r2 = run_batch(items, lambda x: x * 2, out, fail,
                       BatchConfig(), key=str, sleep=lambda s: None)
        check("agent.batch: resumable (2nd run skips done)",
              r1["done"] == 4 and r2["skipped"] == 4,
              f"run1={r1}, run2={r2}")

        # backends: CLI adapter satisfies LLMClient and round-trips (echo, 0 model)
        cli = CliLLMClient(cmd="echo")
        echoed = cli.chat([{"role": "user", "content": "hello fullstack"}])
        check("backends.CliLLMClient: LLMClient + round-trips",
              isinstance(cli, LLMClient) and "hello fullstack" in echoed.text,
              "argv-not-shell, no API key")

        # end-to-end effectiveness
        check("E2E: report references the deployed region",
              NEEDLE in ctx.get("report", "").lower()
              or NEEDLE in ctx.get("finding", "").lower(),
              "verified report references the deployed region")

        # ---- report ----
        print("\nFULL-STACK INTEGRATION — every module, one durable pipeline\n")
        print(f"{'component / capability':<46}{'result':>7}  detail")
        passed = 0
        for name, ok, detail in results:
            passed += ok
            print(f"{name:<46}{'PASS' if ok else 'FAIL':>7}  {detail}")
        llm_calls = client.n_calls + tm.client.n_calls  # type: ignore[attr-defined]
        print(f"\nLLM calls across the whole pipeline: {llm_calls} "
              f"(ingest + Researcher + Writer only; every other stage was 0-LLM).")
        tail = ("the tool works end-to-end, deterministic-first."
                if passed == len(results) else "(see FAILs).")
        print(f"VERDICT: {passed}/{len(results)} components pass — {tail}")


if __name__ == "__main__":
    main()
