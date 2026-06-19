"""Measured: does the tiered (LLM-on-the-cold-path) design fix the three pains?

Compares two read pipelines on the long cross-session needle workload:

  heavy  (Argus/mem0-style "careful"): LLM triage + HyDE expand + answer = 3 calls/q
  tiered (proposed)                  : pure gate + depth recall + commit  = 1 call/q

Held constant: reader model, embedder, the 116-ish-turn history, the needle set.
Varied: how many LLM calls each pipeline spends, and the reader prompt
(hedge vs commit). Needle check is a deterministic substring match (no judge).

Three pains, three measured columns:
  too many LLM calls  -> read_calls/q + ingest_calls (lower = better)
  recall/accuracy     -> accuracy on needles (higher = better)
  slow                -> wall latency/q (lower = better)

Stage A (free, no LLM) reports R@1/R@5 for flat-cosine vs depth-rerank.

Run:  .venv/bin/python examples/eval_tiered_memory.py   (needs oMLX on :8000)
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentkit.memory.store import MemoryStore  # noqa: E402
from agentkit.memory.tiered import TieredMemory  # noqa: E402
from eval_long_memory import FACTS, _est_tokens  # noqa: E402  (reuse needle set)
from run_measured import OMLXClient, OMLXEmbedder  # noqa: E402

# Smaller than eval_long_memory to cap LLM spend; same shape (needles early).
N_SESSIONS = 12
NEEDLE_ZONE = 6
TURNS_PER_SESSION = 3
NEEDLES = FACTS[:NEEDLE_ZONE]
TOP_K = 5
CHAT_MODEL = "gemma-4-26B-A4B-it-heretic-4bit"


def build_sessions() -> list[list[str]]:
    """Return per-session turn lists. Needle statements land in early sessions."""
    topics = ["the weather", "a movie", "weekend plans", "a book", "lunch",
              "a bug at work", "a trail", "music"]
    sessions: list[list[str]] = []
    for s in range(N_SESSIONS):
        turns: list[str] = []
        if s < len(NEEDLES):
            turns.append(NEEDLES[s][0])
        for t in range(TURNS_PER_SESSION):
            turns.append(f"In session {s}, let's chat about {topics[(s + t) % len(topics)]}.")
        sessions.append(turns)
    return sessions


def stage_a(flat: MemoryStore, tiered: TieredMemory) -> None:
    """FREE: R@1/R@5, flat cosine vs depth rerank. Proves depth doesn't hurt
    needle recall (the design tension) and is a safe tiebreak."""
    def recall_at(getter, n: int) -> float:
        # needle-VALUE substring (works across raw turns AND atomised facts;
        # exact statement-match would false-negative on the tiered store).
        hits = sum(
            any(needle in r.content.lower() for r in getter(q, n))
            for _stmt, q, needle in NEEDLES
        )
        return hits / len(NEEDLES)

    f1 = recall_at(lambda q, n: flat.search(q, top_k=n), 1)
    f5 = recall_at(lambda q, n: flat.search(q, top_k=n), 5)
    t1 = recall_at(lambda q, n: tiered.recall(q, k=n), 1)
    t5 = recall_at(lambda q, n: tiered.recall(q, k=n), 5)
    print("[Stage A] retrieval (free, 0 LLM)")
    print(f"  flat   R@1={f1:.2f}  R@5={f5:.2f}")
    print(f"  depth  R@1={t1:.2f}  R@5={t5:.2f}\n")


def _answer(client: OMLXClient, msgs: list[dict]) -> str:
    return client.chat(msgs).text.lower()


def heavy_pipeline(client: OMLXClient, flat: MemoryStore, question: str) -> tuple[str, int]:
    """Argus/mem0-style: triage LLM + HyDE LLM + answer LLM = 3 calls."""
    # 1. LLM triage
    _answer(client, [
        {"role": "system", "content": "Reply only YES or NO."},
        {"role": "user", "content": f"Does answering '{question}' need memory lookup?"},
    ])
    # 2. HyDE expansion → use to retrieve
    hyde = _answer(client, [
        {"role": "system", "content": "Write a one-sentence hypothetical answer."},
        {"role": "user", "content": question},
    ])
    hits = flat.search(question + " " + hyde, top_k=TOP_K)
    ctx = "\n".join(f"- {h.content}" for h in hits)
    # 3. answer (hedge prompt)
    ans = _answer(client, [
        {"role": "system", "content":
         "Answer using ONLY the context; if absent say 'I don't know'. One short phrase."},
        {"role": "user", "content": f"Context:\n{ctx}\n\nQuestion: {question}"},
    ])
    return ans, 3


def tiered_pipeline(client: OMLXClient, tm: TieredMemory, question: str) -> tuple[str, int]:
    """Proposed: pure gate (0) + depth recall (0) + commit answer (1) = 1 call."""
    tm.gate(question, recent_context="")          # deterministic, 0 LLM
    msgs = tm.build_messages(question, k=TOP_K, commit=True)  # depth recall, 0 LLM
    return _answer(client, msgs), 1               # 1 LLM call total


def stage_b(flat: MemoryStore, tm: TieredMemory) -> None:
    client = OMLXClient(model=CHAT_MODEL, max_tokens=64)
    rows = {}
    for name, run in (("heavy", lambda q: heavy_pipeline(client, flat, q)),
                      ("tiered", lambda q: tiered_pipeline(client, tm, q))):
        hits = calls = 0
        t0 = time.time()
        tok0 = client.total_tokens
        for _stmt, question, needle in NEEDLES:
            ans, c = run(question)
            calls += c
            if needle in ans:
                hits += 1
        rows[name] = {
            "acc": hits / len(NEEDLES),
            "calls_q": calls / len(NEEDLES),
            "lat_q": (time.time() - t0) / len(NEEDLES),
            "tok": client.total_tokens - tok0,
        }

    print(f"[Stage B] reader={CHAT_MODEL}, {len(NEEDLES)} needles, "
          f"tiered ingest_calls={tm.ingest_llm_calls}")
    print(f"{'pipeline':<8}{'accuracy':>10}{'read_calls/q':>14}{'latency/q':>12}{'tokens':>9}")
    for name in ("heavy", "tiered"):
        r = rows[name]
        print(f"{name:<8}{r['acc']:>9.0%}{r['calls_q']:>14.0f}{r['lat_q']:>10.1f}s{r['tok']:>9}")

    h, t = rows["heavy"], rows["tiered"]
    print(f"\nVERDICT: tiered {t['calls_q']:.0f} vs heavy {h['calls_q']:.0f} read-calls/q "
          f"({(1 - t['calls_q']/h['calls_q']):.0%} fewer), "
          f"accuracy {t['acc']:.0%} vs {h['acc']:.0%}, "
          f"latency {(1 - t['lat_q']/h['lat_q']):.0%} faster.")


def stage_c(tm: TieredMemory) -> None:
    """L2/L3 layering: persona standing-context cost vs dumping all atoms, and
    whether distillation retains the needle facts. Free (no LLM beyond the
    consolidate that already built the layers)."""
    atoms = tm.store.get_recent("atom", limit=10_000)
    all_atoms_ctx = "\n".join(a.content for a in atoms)
    persona = tm.inject_profile()

    atoms_tok = _est_tokens(all_atoms_ctx)
    persona_tok = _est_tokens(persona)
    covered = sum(needle in persona.lower() for _s, _q, needle in NEEDLES)

    print("\n[Stage C] L2/L3 layering — persona vs all-atoms (free)")
    print(f"  all-atoms standing context : {atoms_tok:>5} tok ({len(atoms)} atoms)")
    print(f"  L3 persona standing context: {persona_tok:>5} tok")
    print(f"  reduction: {(1 - persona_tok / max(1, atoms_tok)):.0%}  |  "
          f"needle retention in persona: {covered}/{len(NEEDLES)}")


def main() -> None:
    sessions = build_sessions()
    with tempfile.TemporaryDirectory() as d:
        emb = OMLXEmbedder()
        # baseline store: raw turns, no atomise (heavy pipeline reads this)
        flat = MemoryStore(Path(d) / "flat.db", embedder=emb)
        for turns in sessions:
            for turn in turns:
                flat.add("raw", turn, {})
        # tiered store: write-time atomise (1 LLM call / session, cold path)
        tier_store = MemoryStore(Path(d) / "tier.db", embedder=emb)
        tm = TieredMemory(tier_store, client=OMLXClient(model=CHAT_MODEL, max_tokens=256))
        for i, turns in enumerate(sessions):
            tm.ingest_session(turns, session=i, atomise=True)
        counts = tm.consolidate()  # build L2 scenarios + L3 persona (cold path)
        print(f"flat={flat.count()} raw turns, tiered={tier_store.count('atom')} atoms, "
              f"{counts['scenarios']} scenarios + {counts['persona']} persona "
              f"({tm.ingest_llm_calls} ingest LLM calls)\n")

        stage_a(flat, tm)
        stage_b(flat, tm)
        stage_c(tm)


if __name__ == "__main__":
    main()
