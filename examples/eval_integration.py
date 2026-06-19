"""End-to-end integration test — every memory component, one hard scenario.

A multi-session personal-assistant history where facts EVOLVE, so a no-memory
(truncation) agent structurally cannot keep up. Each capability exercises a
different component and is the kind of case where memory pays off:

  capability             component(s)                       check
  ---------------------  ---------------------------------  -------------------
  cross-session recall   ingest_session + _atomise + recall  old fact retrieved;
                                                             truncation loses it
  contradiction          depth recency rerank               latest value wins
  right-to-be-forgotten  surrender                          secret gone from recall
  pinned safety fact     pin                                buried fact lifted
  distractor rejection   recall + cosine                    right fact ranks #1
  persona token win      consolidate (L2/L3) + inject_profile cheap profile
  gate skip (0 LLM)      gate                               skips when in-context
  working memory         context.compact                    transcript shrinks
  end-to-end             build_messages (commit prompt)     memory agent answers,
                                                             truncation agent fails

Mostly deterministic/free checks (substring + rank + token counts); a few real
reader calls for the headline memory-vs-truncation comparison.

Run:  .venv/bin/python examples/eval_integration.py   (needs oMLX on :8000)
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentkit.context.compactor import compact  # noqa: E402
from agentkit.memory.store import MemoryStore  # noqa: E402
from agentkit.memory.tiered import (  # noqa: E402
    HEDGE_SYSTEM,
    TieredConfig,
    TieredMemory,
)
from eval_long_memory import _est_tokens  # noqa: E402
from run_measured import OMLXClient, OMLXEmbedder  # noqa: E402

CHAT_MODEL = "gemma-4-26B-A4B-it-heretic-4bit"
N_SESSIONS = 18
GAP = 1800.0  # one depth half-life per session, so old facts decay below new
BUDGET = 300  # truncation agent's recent-window budget (tokens)

# (session, statement, value-substring, capability, slot). Filler fills the rest.
# slot != None  -> structured fact written via remember() with SCD-2 supersession
# slot == None  -> conversational statement, atomised into the session.
FACTS = [
    (0, "I deploy the production service to the us-east-1 region.", "us-east-1", "stale", "deploy_region"),
    (3, "Update: I have migrated the deployment to ap-southeast-1 now.", "ap-southeast-1", "fresh", "deploy_region"),
    (1, "My dog's name is Mochi.", "mochi", "recall", None),
    (2, "My favorite color is teal.", "teal", "target", None),
    (4, "My one-time login code is OTP-7741.", "7741", "secret", None),
    (5, "Important: I am severely allergic to shellfish.", "shellfish", "pinned", None),
    (6, "A colleague painted their office wall maroon last week.", "maroon", "distractor", None),
]
TOPICS = ["the weather", "a film", "weekend plans", "a novel", "lunch spots",
          "a flaky test", "a hiking trail", "new music", "a podcast"]


def build_sessions() -> tuple[list[tuple[int, list[str], float]], list[str]]:
    """Return (per-session [(idx, turns, ts)], flat chronological raw turns).
    Older sessions get older timestamps so depth can tell stale from fresh."""
    base = time.time()
    # only conversational facts (slot is None) become session statements;
    # slotted facts are written separately via remember() for supersession.
    by_session = {s: stmt for s, stmt, _v, _k, slot in FACTS if slot is None}
    sessions: list[tuple[int, list[str], float]] = []
    flat: list[str] = []
    for i in range(N_SESSIONS):
        turns: list[str] = []
        if i in by_session:
            turns.append(by_session[i])
        for t in range(3):
            turns.append(f"Session {i}: chatting about {TOPICS[(i + t) % len(TOPICS)]}.")
        ts = base - (N_SESSIONS - 1 - i) * GAP
        sessions.append((i, turns, ts))
        flat.extend(turns)
    return sessions, flat


def _ids_with(store: MemoryStore, value: str) -> list[int]:
    return [m.id for m in store.get_recent("atom", limit=10_000)
            if value in m.content.lower()]


def _recall_texts(tm: TieredMemory, query: str, k: int = 5) -> list[str]:
    return [h.content.lower() for h in tm.recall(query, k=k)]


def _rank_of(texts: list[str], value: str) -> int:
    for i, t in enumerate(texts):
        if value in t:
            return i
    return -1


def truncate_recent(flat: list[str], budget: int) -> str:
    kept, used = [], 0
    for text in reversed(flat):
        c = _est_tokens(text)
        if used + c > budget:
            break
        kept.append(text)
        used += c
    return "\n".join(reversed(kept))


def run_checks(tm: TieredMemory, flat: list[str], client: OMLXClient) -> None:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    # 1. cross-session recall: old fact retrieved; truncation window loses it.
    got = _recall_texts(tm, "what is my dog called?")
    trunc = truncate_recent(flat, BUDGET).lower()
    check("cross-session recall", any("mochi" in t for t in got) and "mochi" not in trunc,
          "memory finds Mochi; truncation window does not")

    # 2. contradiction / latest-wins: fresh region ranks above stale.
    reg = _recall_texts(tm, "which region is my service deployed to?")
    r_fresh, r_stale = _rank_of(reg, "ap-southeast-1"), _rank_of(reg, "us-east-1")
    check("contradiction latest-wins", r_fresh != -1 and (r_stale == -1 or r_fresh < r_stale),
          f"fresh rank={r_fresh}, stale rank={r_stale}")

    # 3. right-to-be-forgotten: surrender the secret, then it must vanish.
    for sid in _ids_with(tm.store, "7741"):
        tm.surrender(sid)
    sec = _recall_texts(tm, "what is my one-time login code?")
    check("forgetting (surrender)", all("7741" not in t for t in sec),
          "OTP absent from recall after surrender")

    # 4. pinned safety fact: pin lifts the (old, decayed) allergy fact.
    before = _rank_of(_recall_texts(tm, "do I have any allergies?"), "shellfish")
    for aid in _ids_with(tm.store, "shellfish"):
        tm.pin(aid)
    after = _rank_of(_recall_texts(tm, "do I have any allergies?"), "shellfish")
    check("pin lifts buried fact", after != -1 and (before == -1 or after <= before),
          f"allergy rank {before} -> {after} after pin")

    # 5. distractor rejection: right colour ranks #1, not the distractor.
    col = _recall_texts(tm, "what is my favorite color?")
    check("distractor rejection", _rank_of(col, "teal") == 0,
          f"teal rank={_rank_of(col, 'teal')}, maroon rank={_rank_of(col, 'maroon')}")

    # 6. persona token win: profile far cheaper than all atoms.
    atoms = tm.store.get_recent("atom", limit=10_000)
    all_tok = _est_tokens("\n".join(a.content for a in atoms))
    persona_tok = _est_tokens(tm.inject_profile())
    check("persona token win", persona_tok < all_tok * 0.5,
          f"{persona_tok} vs {all_tok} tok ({(1 - persona_tok/max(1, all_tok)):.0%} less)")

    # 7. gate skip (0 LLM): skip retrieval on a topical follow-up (query terms
    # already in the recent window); retrieve when the turn is off-topic.
    skip = tm.gate("which region do I deploy to",
                   "we were just discussing which region I deploy to: ap-southeast-1")
    do = tm.gate("which region do I deploy to", "we were chatting about the weather and a film")
    check("gate skip (pure, 0 LLM)", skip is False and do is True,
          f"follow-up->retrieve={skip}, off-topic->retrieve={do}")

    # 8. working memory: compact a long (200-turn) transcript.
    msgs = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"Turn {i} about {TOPICS[i % len(TOPICS)]} with some detail."}
            for i in range(200)]
    cr = compact(msgs, keep=1)
    reduction = 1 - cr.est_tokens_after / max(1, cr.est_tokens_before)
    check("working-memory compaction", reduction > 0.3,
          f"{cr.est_tokens_before} -> {cr.est_tokens_after} tok ({reduction:.0%} less)")

    # --- headline end-to-end (reader): memory agent vs truncation agent ---
    q = "Which region is my service deployed to right now?"
    mem_ans = client.chat(tm.build_messages(q, commit=True)).text.lower()
    trunc_ans = client.chat([
        {"role": "system", "content": HEDGE_SYSTEM},
        {"role": "user", "content": f"Context:\n{truncate_recent(flat, BUDGET)}\n\nQuestion: {q}"},
    ]).text.lower()
    check("E2E: memory agent answers", "ap-southeast-1" in mem_ans, f"answer={mem_ans.strip()[:60]}")
    check("E2E: truncation agent fails", "ap-southeast-1" not in trunc_ans,
          f"answer={trunc_ans.strip()[:60]}")

    # report
    print(f"\n{'capability':<32}{'result':>8}  detail")
    passed = 0
    for name, ok, detail in results:
        passed += ok
        print(f"{name:<32}{'PASS' if ok else 'FAIL':>8}  {detail}")
    tail = ("memory pays off where truncation cannot." if passed == len(results)
            else "(see FAILs).")
    print(f"\nVERDICT: {passed}/{len(results)} capabilities pass — {tail}")


def main() -> None:
    sessions, flat = build_sessions()
    with tempfile.TemporaryDirectory() as d:
        emb = OMLXEmbedder()
        store = MemoryStore(Path(d) / "mem.db", embedder=emb)
        tm = TieredMemory(store, client=OMLXClient(model=CHAT_MODEL, max_tokens=256),
                          config=TieredConfig(half_life_seconds=GAP))
        # slotted structured facts, keyed by the session they occur in.
        slotted = {s: (stmt, slot) for s, stmt, _v, _k, slot in FACTS if slot}
        for idx, turns, ts in sessions:
            tm.ingest_session(turns, session=idx, ts=ts, atomise=True)
            if idx in slotted:  # chronological → fresh supersedes stale (SCD-2)
                stmt, slot = slotted[idx]
                tm.remember(stmt, slot=slot, ts=ts)
        counts = tm.consolidate()
        print(f"ingested {store.count('atom')} atoms over {N_SESSIONS} sessions, "
              f"{counts['scenarios']} scenarios + {counts['persona']} persona "
              f"({tm.ingest_llm_calls} ingest LLM calls)")
        run_checks(tm, flat, OMLXClient(model=CHAT_MODEL, max_tokens=64))


if __name__ == "__main__":
    main()
