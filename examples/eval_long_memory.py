"""LongMemEval-style test: does memory pay off on a LONG cross-session workload?

The short-thread eval (examples/eval_memory_quality.py) found a NULL result:
memory added ~3800 tokens with no quality gain, because a 5-question thread
fits entirely in context — retrieval is redundant. This script tests the
regime where memory is *supposed* to win: a history far larger than the reader
context budget, with the answer-bearing fact planted in an EARLY session.

Method (mirrors LongMemEval arXiv:2410.10813 = index -> retrieve -> read):
  - Synthesize S sessions of multi-turn chat. Plant K "needle" facts in the
    first third; flood the rest with plausible filler.
  - Hold the reader model CONSTANT, vary ONLY what enters its context:
      oracle   : needle handed in directly        (upper bound)
      memory   : MemoryStore.search decides         (the thing under test)
      truncate : most-recent turns up to a budget   (no-memory baseline)
  - Needle check is a deterministic substring match (no LLM judge).
  - Stage A (recall@k) is free: it asks only whether search() surfaces the
    needle turn, with zero reader calls. Run it first to prove the mechanism.

Run:  .venv/bin/python examples/eval_long_memory.py
Needs: oMLX serving chat + embeddings on :8000 (same stack as run_measured.py).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentkit.memory.store import MemoryStore  # noqa: E402
from run_measured import OMLXClient, OMLXEmbedder  # noqa: E402

# --- knobs ---------------------------------------------------------------
N_SESSIONS = 36          # total chat sessions in the history
NEEDLE_ZONE = 12         # needles planted within the first N sessions (early)
TURNS_PER_SESSION = 3    # filler turns per session
TOP_K = 5                # retrieval depth
BUDGET_TOKENS = 350      # reader context budget (truncate keeps recent <= this)
CHAT_MODEL = "gemma-4-26B-A4B-it-heretic-4bit"


# Each fact: (statement planted in history, question asked later, needle value).
# Questions are worded DIFFERENTLY from statements so retrieval must be
# semantic, not lexical — keeps the test honest (no lexical gaming).
FACTS = [
    ("By the way, my dog's name is Mochi.",
     "What is the name of my pet dog?", "mochi"),
    ("I ended up deploying the service to the ap-southeast-1 region.",
     "Which cloud region did I deploy my service to?", "ap-southeast-1"),
    ("My favorite color has always been teal.",
     "What color do I like most?", "teal"),
    ("I switched my morning drink to oolong tea last month.",
     "What do I drink in the mornings now?", "oolong"),
    ("Our team standup moved to 9:45 in the morning.",
     "What time is our daily standup?", "9:45"),
    ("I keep my backups on a drive I labelled Falcon.",
     "What did I name my backup drive?", "falcon"),
    ("The license key for the analyzer starts with the prefix ZX9.",
     "What is the prefix of my analyzer license key?", "zx9"),
    ("I'm allergic to shellfish, so I avoid it entirely.",
     "What food am I allergic to?", "shellfish"),
]


def _est_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (~4 chars/token)."""
    return max(1, len(text) // 4)


def build_history() -> list[tuple[int, str]]:
    """Return chronological [(session_idx, turn_text)]. Needle statements land
    in early sessions; everything else is plausible filler."""
    turns: list[tuple[int, str]] = []
    topics = ["the weather", "a movie I saw", "weekend plans", "a book chapter",
              "lunch options", "a bug at work", "a hiking trail", "music"]
    for s in range(N_SESSIONS):
        # plant one needle per early session, in order
        if s < len(FACTS) and s < NEEDLE_ZONE:
            turns.append((s, FACTS[s][0]))
        for t in range(TURNS_PER_SESSION):
            topic = topics[(s + t) % len(topics)]
            turns.append((s, f"In session {s}, let's chat about {topic} for a bit."))
    return turns


def truncate_recent(turns: list[tuple[int, str]], budget: int) -> str:
    """No-memory baseline: keep most-recent turns until the budget is hit."""
    kept: list[str] = []
    used = 0
    for _, text in reversed(turns):
        cost = _est_tokens(text)
        if used + cost > budget:
            break
        kept.append(text)
        used += cost
    kept.reverse()
    return "\n".join(kept)


def stage_a_recall(store: MemoryStore) -> float:
    """FREE: does search() surface the needle turn in top-k? No reader calls."""
    hits = 0
    for statement, question, _ in FACTS:
        results = store.search(question, top_k=TOP_K)
        if any(r.content.strip() == statement.strip() for r in results):
            hits += 1
    rate = hits / len(FACTS)
    print(f"[Stage A] recall@{TOP_K} = {hits}/{len(FACTS)} = {rate:.2f}")
    return rate


def _ask(client: OMLXClient, context: str, question: str) -> str:
    msgs = [
        {"role": "system", "content":
         "Answer the user's question using ONLY the context. If the answer is "
         "not in the context, say 'I don't know'. Answer in one short phrase."},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]
    return client.chat(msgs).text.lower()


def stage_b_answers(store: MemoryStore, turns: list[tuple[int, str]]) -> None:
    """Hold reader constant; vary only the context. Deterministic needle check."""
    client = OMLXClient(model=CHAT_MODEL, max_tokens=64)
    score = {"oracle": 0, "memory": 0, "truncate": 0}
    tok = {"oracle": 0, "memory": 0, "truncate": 0}

    for statement, question, needle in FACTS:
        contexts = {
            "oracle": statement,
            "memory": store.inject_context(question, k=TOP_K),
            "truncate": truncate_recent(turns, BUDGET_TOKENS),
        }
        for cond, ctx in contexts.items():
            before = client.total_tokens
            answer = _ask(client, ctx, question)
            tok[cond] += client.total_tokens - before
            if needle in answer:
                score[cond] += 1

    n = len(FACTS)
    print(f"\n[Stage B] reader = {CHAT_MODEL}, budget = {BUDGET_TOKENS} tok, "
          f"history = {len(turns)} turns / {N_SESSIONS} sessions")
    print(f"{'condition':<10} {'accuracy':>10} {'tokens':>10}")
    for cond in ("oracle", "memory", "truncate"):
        print(f"{cond:<10} {score[cond]}/{n} ({score[cond]/n:.0%})  {tok[cond]:>8}")

    verdict = ("MEMORY PAYS OFF" if score["memory"] > score["truncate"]
               else "no clear memory gain")
    print(f"\nVERDICT: {verdict} "
          f"(memory {score['memory']}/{n} vs truncate {score['truncate']}/{n})")


def main() -> None:
    turns = build_history()
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(Path(d) / "long.db", embedder=OMLXEmbedder())
        # index: store every turn as an episodic memory
        for sess, text in turns:
            store.add("episodic", text, {"session": sess})
        print(f"indexed {store.count('episodic')} turns\n")

        if stage_a_recall(store) == 0.0:
            print("retrieval surfaced nothing — skipping reader stage.")
            return
        stage_b_answers(store, turns)


if __name__ == "__main__":
    main()
