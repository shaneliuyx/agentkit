"""Memory-recall QUALITY eval — do memory's extra tokens buy BETTER answers?

The measured run showed memory ADDS ~3800 tokens to inject recall (8 hits) but
did not show whether those recalls improve the answer. This closes that gap.

Fair design (a convenient eval is worse than none):
- Paired: a thread of related sub-questions where later ones should benefit from
  earlier answers. Each answered WITH memory (recall prior answers) and WITHOUT.
- Blind + position-randomized: the judge sees "Answer 1/2"; order alternates by
  question index (deterministic — cancels position bias without `random`).
- DIFFERENT judge model than the generator (reduces self-judging bias).
- Rubric-scored via agentkit.Rubric: groundedness, builds-on-prior, non-redundancy.
- Honest: if with-memory does not win, that is a valid negative result.

Run: .venv/bin/python examples/eval_memory_quality.py   (needs oMLX :8000)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_measured import OMLXClient, OMLXEmbedder  # noqa: E402

from agentkit import Dimension, Rubric  # noqa: E402
from agentkit.memory import MemoryStore  # noqa: E402

GEN_MODEL = "gemma-4-26B-A4B-it-heretic-4bit"
JUDGE_MODEL = "Qwen2.5-Coder-14B-Instruct-MLX-4bit"  # distinct family, instruct, JSON-reliable

# A thread where each question builds on the prior answers — the regime where
# recalling earlier findings should help. Q0 has no prior, so it is not judged.
THREAD = [
    "Define context loss in long-horizon LLM agents.",
    "Given that, what external-memory architectures mitigate it?",
    "For those architectures, how does retrieval select what to recall?",
    "What are the main failure modes of that retrieval step?",
    "Summarize the design guidance implied by the above for a practitioner.",
]

RUBRIC = Rubric((
    Dimension("groundedness", "Grounded in concrete specifics", 2.0),
    Dimension("builds_on_prior", "Builds on / coheres with earlier thread context", 1.5),
    Dimension("non_redundancy", "Adds new information, not repetition", 1.0),
))

SYS = "You are a precise research assistant. Answer in 3-5 sentences."


def _num(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _extract_json(raw: str) -> dict | None:
    """Pull the last balanced JSON object carrying answer1/answer2 out of the
    judge's reply. Reasoning-distilled judges emit prose before the JSON and the
    object is nested, so a non-greedy regex won't do — scan every '{', try a
    full decode, keep the last that parses into the expected shape."""
    dec = json.JSONDecoder()
    found: dict | None = None
    for idx, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(raw[idx:])
        except ValueError:
            continue
        if isinstance(obj, dict) and "answer1" in obj and "answer2" in obj:
            found = obj
    return found


def _answer(client: OMLXClient, question: str, recall: str) -> str:
    msgs = [{"role": "system", "content": SYS}]
    if recall:
        msgs.append({"role": "system", "content": recall})
    msgs.append({"role": "user", "content": question})
    return client.chat(msgs).text.strip()


def _judge(judge: OMLXClient, question: str, ans1: str, ans2: str) -> dict | None:
    dims = ", ".join(d.key for d in RUBRIC.dimensions)
    prompt = (
        f"Question: {question}\n\n"
        f"Answer 1:\n{ans1}\n\nAnswer 2:\n{ans2}\n\n"
        f"Score each answer 1-5 on these dimensions: {dims}. "
        "Reason briefly if you must, then END your reply with ONLY a JSON object "
        "as the final content, exactly this shape: "
        '{"answer1":{"groundedness":N,"builds_on_prior":N,"non_redundancy":N},'
        '"answer2":{"groundedness":N,"builds_on_prior":N,"non_redundancy":N}}'
    )
    for _ in range(2):  # one retry: reasoning judges sometimes omit the JSON
        raw = judge.chat([{"role": "user", "content": prompt}]).text
        obj = _extract_json(raw)
        if obj is not None:
            return obj
    return None


def _score(side: dict) -> float:
    return RUBRIC.aggregate({d.key: _num(side.get(d.key)) for d in RUBRIC.dimensions})


def main() -> None:
    gen = OMLXClient(model=GEN_MODEL)
    judge = OMLXClient(model=JUDGE_MODEL, max_tokens=1024)
    embedder = OMLXEmbedder()
    mem = MemoryStore(tempfile.mktemp(suffix=".db", prefix="evalmem_"), embedder)

    print(f"memory-recall quality eval — gen={GEN_MODEL}")
    print(f"judge={JUDGE_MODEL} (distinct, blind, position-randomized)\n", flush=True)

    rows = []
    mem_score_sum = nomem_score_sum = mem_wins = judged = skipped = 0
    for i, q in enumerate(THREAD):
        recall = mem.inject_context(q, k=3)            # WITH memory: recall prior
        a_mem = _answer(gen, q, recall)
        a_nomem = _answer(gen, q, "")                   # WITHOUT: isolated
        mem.add("episodic", a_mem, {"q": q})            # accumulate for next round

        if i == 0:
            continue                                    # no prior to recall yet
        # Blind order: even i -> memory is Answer 1; odd i -> Answer 2.
        mem_is_a1 = (i % 2 == 0)
        a1, a2 = (a_mem, a_nomem) if mem_is_a1 else (a_nomem, a_mem)
        v = _judge(judge, q, a1, a2)
        if v is None:
            print(f"  (judge gave no parseable JSON for Q{i}; skipped)", flush=True)
            skipped += 1
            continue
        judged += 1
        s1, s2 = _score(v["answer1"]), _score(v["answer2"])
        mem_s, nomem_s = (s1, s2) if mem_is_a1 else (s2, s1)
        mem_score_sum += mem_s
        nomem_score_sum += nomem_s
        if mem_s > nomem_s:
            mem_wins += 1
        rows.append((i, mem_s, nomem_s, "mem" if mem_s > nomem_s else
                     ("tie" if mem_s == nomem_s else "no-mem")))

    print("-" * 60)
    print(f"{'Q':>2}  {'mem':>5}  {'no-mem':>6}  winner")
    for i, ms, ns, w in rows:
        print(f"{i:>2}  {ms:>5.2f}  {ns:>6.2f}  {w}")
    print("-" * 60)
    if judged == 0:
        print("no questions judged (judge produced no parseable JSON). "
              f"skipped={skipped}")
        return
    print(f"with-memory win rate     : {mem_wins}/{judged}  (skipped={skipped})")
    print(f"mean rubric score mem/no : {mem_score_sum / judged:.2f} / "
          f"{nomem_score_sum / judged:.2f}")
    print(f"mean score delta (mem-no): {(mem_score_sum - nomem_score_sum) / judged:+.2f}")
    print("\nNOTE: single thread, N small, one local judge model. A signal, not "
          "proof. Negative/flat result = memory's extra tokens did NOT buy quality here.")


if __name__ == "__main__":
    main()
