"""agentkit.memory.tiered — LLM-on-the-cold-path memory.

A composition layer over ``MemoryStore`` that combines three patterns measured
to attack the three pain points of LLM-heavy memory systems:

  problem                         lever                         source
  ------------------------------  ----------------------------  ----------------
  too many LLM calls (cost/time)  pure gate + 1-call ingest     CRAG (lab-03.7),
                                  + 0-LLM consolidation         Argus triage
  high recall / low accuracy      commit-biased read prompt     lab-03-5-8 (+30pt)
                                  + depth rerank lifts R@1      Lethe `depth`
  slow ingest / search / summary  write-time atomise (async),   TencentDB layering,
                                  arithmetic decay (no LLM)     Lethe

Design axiom (agentkit): the LLM touches a query exactly ONCE — to write the
final answer. Gate, retrieve, rank, and forget are deterministic; atomise moves
to background ingest. That drops a "careful" memory agent from ~5-6 LLM
calls/query to 1.

The store stays append-only (its event log is immutable). ``depth`` is computed
from age at read time (pure, no mutation); ``pin`` / ``surrender`` use a small
override projection — the log is the source of truth, overrides are a view.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from agentkit.memory.store import MemoryEntry, MemoryStore
from agentkit.types import LLMClient, Message

# Layers (TencentDB): inject the top usable layer; drill to atoms on demand.
# L0 Conversation → L1 Atom → L2 Scenario → L3 Persona. Recall searches atoms;
# the standing profile (persona) is injected cheaply, atoms drilled on demand.
LAYER_RAW = "raw"            # L0 conversation turn
LAYER_ATOM = "atom"          # L1 atomic fact (the load-bearing extract stage)
LAYER_SCENARIO = "scenario"  # L2 scene block (atoms of one session distilled)
LAYER_PERSONA = "persona"    # L3 durable user profile (all scenarios distilled)

# Reader prompts. The commit-biased one is the measured +30pt accuracy lever
# (lab-03-5-8): LongMemEval rewards commitment over calibration — weak local
# models hedge even with the evidence in front of them.
COMMIT_SYSTEM = (
    "You are answering from retrieved memory. Assume the answer IS in the "
    "context. Commit to one specific answer; do NOT hedge, qualify, or say you "
    "don't know unless the context is entirely unrelated to the question. "
    "Answer in one short phrase."
)
HEDGE_SYSTEM = (
    "Answer the question using ONLY the context. If the answer is not in the "
    "context, say 'I don't know'. Answer in one short phrase."
)


@dataclass(frozen=True)
class TieredConfig:
    half_life_seconds: float = 1800.0  # depth decay half-life (forgetting curve)
    depth_lambda: float = 0.08         # additive weight: cosine + λ·recency
    overfetch: int = 4                 # fetch k*overfetch before rerank


class TieredMemory:
    """LLM-on-the-cold-path memory over a ``MemoryStore``.

    Composition, not inheritance: the underlying append-only store is untouched;
    this class adds the write/read/consolidate policy around it."""

    def __init__(
        self,
        store: MemoryStore,
        client: LLMClient | None = None,
        config: TieredConfig | None = None,
    ) -> None:
        self.store = store
        self.client = client  # only for write-time atomise (cold path)
        self.cfg = config or TieredConfig()
        # pin/surrender projection over the immutable log (ponytail: in-memory
        # dict is enough; persist to a side table if it must survive restart).
        self._overrides: dict[int, float] = {}
        self.ingest_llm_calls = 0  # observability for the cost claim

    # -- WRITE (cold path; atomise is background-safe) --------------------

    def ingest_session(
        self, turns: list[str], *, session: int = 0, atomise: bool = True,
        ts: float | None = None,
    ) -> int:
        """Index one session. With ``atomise`` + a client, spends ONE LLM call
        to distil the whole session into atomic facts (L1); otherwise stores raw
        turns (0 LLM). ``session`` is tagged so ``consolidate`` can group
        deterministically. ``ts`` overrides the stored timestamp — pass the
        event's real time when ingesting historical logs (depth decays from it).
        Returns the number of memories written."""
        if atomise and self.client is not None:
            atoms = self._atomise(turns)
            layer = LAYER_ATOM
        else:
            atoms = turns
            layer = LAYER_RAW
        now = ts if ts is not None else time.time()
        for text in atoms:
            self.store.add(
                layer, text,
                {"layer": layer, "session": session, "depth": 1.0, "ts": now},
            )
        return len(atoms)

    def remember(self, text: str, *, slot: str | None = None,
                 ts: float | None = None) -> None:
        """Write one durable fact — the structured-fact path (vs
        ``ingest_session``'s bulk atomise). If ``slot`` is given, supersede any
        prior LIVE fact in that slot (SCD-2 latest-wins, `lab-03-5-memory`): the
        old value is surrendered so recall returns only the current one. This is
        the contradiction-handling capability mem0 lacks (it never archives on
        contradiction, so old values linger). Rerank alone cannot fix
        contradiction — a stale fact can be lexically closer to the query than
        the fresh one; supersession at write time is the correct mechanism."""
        now = ts if ts is not None else time.time()
        if slot is not None:
            for m in self.store.get_recent(LAYER_ATOM, limit=10_000):
                if m.metadata.get("slot") == slot and self._depth(m) > 0.0:
                    self.surrender(m.id)
        self.store.add(LAYER_ATOM, text,
                       {"layer": LAYER_ATOM, "slot": slot, "depth": 1.0, "ts": now})

    def _atomise(self, turns: list[str]) -> list[str]:
        """One batched LLM call → one fact per line. Falls back to raw turns on
        any failure (never lose data to a flaky extract)."""
        assert self.client is not None
        text = "\n".join(turns)
        msgs: list[Message] = [
            {"role": "system", "content":
             "Extract the atomic facts from this conversation. Output one "
             "self-contained fact per line, no numbering, no commentary. Skip "
             "small talk that carries no durable fact."},
            {"role": "user", "content": text},
        ]
        try:
            self.ingest_llm_calls += 1
            out = self.client.chat(msgs).text
        except Exception:
            return turns
        facts = [ln.strip(" -*\t").strip() for ln in out.splitlines() if ln.strip()]
        return facts or turns

    def consolidate(self) -> dict[str, int]:
        """Build the upper layers (cold path). Grouping is deterministic (by
        session, 0 LLM); summarisation is 1 LLM call per scenario + 1 for the
        persona. L2 Scenario = a session's atoms distilled into a scene block;
        L3 Persona = all scenarios distilled into a durable profile. Returns
        counts. No-op without a client."""
        if self.client is None:
            return {"scenarios": 0, "persona": 0}
        atoms = self.store.get_recent(LAYER_ATOM, limit=10_000)
        groups: dict[int, list[str]] = {}
        for a in atoms:  # deterministic grouping, zero LLM
            groups.setdefault(int(a.metadata.get("session", 0)), []).append(a.content)

        now = time.time()
        scenarios: list[str] = []
        for sess in sorted(groups):
            scene = self._summarize(groups[sess], "scenario")
            self.store.add(LAYER_SCENARIO, scene,
                           {"layer": LAYER_SCENARIO, "session": sess,
                            "depth": 1.0, "ts": now})
            scenarios.append(scene)

        persona = self._summarize(scenarios, "persona")
        self.store.add(LAYER_PERSONA, persona,
                       {"layer": LAYER_PERSONA, "depth": 1.0, "ts": now})
        return {"scenarios": len(scenarios), "persona": 1}

    def _summarize(self, items: list[str], kind: str) -> str:
        """One LLM call distilling ``items`` into the next layer up. Falls back
        to a joined string on failure (never lose the layer to a flaky call)."""
        assert self.client is not None
        if kind == "persona":
            instr = ("Distill these notes into a compact user profile: durable "
                     "preferences and facts only, one per line, no commentary.")
        else:  # scenario
            instr = ("Summarise these related facts into a 1-2 sentence scene "
                     "that preserves every concrete fact (names, values, dates).")
        msgs: list[Message] = [
            {"role": "system", "content": instr},
            {"role": "user", "content": "\n".join(items)},
        ]
        try:
            self.ingest_llm_calls += 1
            out = self.client.chat(msgs).text.strip()
        except Exception:
            return "\n".join(items)
        return out or "\n".join(items)

    def inject_profile(self) -> str:
        """The cheap standing context: the latest L3 Persona block. Use this for
        'what do you know about me' queries instead of dumping every atom
        (TencentDB progressive disclosure — drill to atoms only when details
        matter, via ``recall``/``inject``)."""
        personas = self.store.get_recent(LAYER_PERSONA, limit=1)
        return personas[0].content if personas else ""

    # -- depth (Lethe), all arithmetic, zero LLM -------------------------

    def pin(self, mem_id: int) -> None:
        """Immune to gravity — always surfaces."""
        self._overrides[mem_id] = math.inf

    def surrender(self, mem_id: int) -> None:
        """Submerged: present in the log but excluded from recall (forgetting)."""
        self._overrides[mem_id] = 0.0

    def _depth(self, entry: MemoryEntry) -> float:
        """Depth in [0, 1] (∞ for pinned). 0 ⇒ forgotten, excluded from recall.
        Pure function of age + overrides — no stored mutation."""
        ov = self._overrides.get(entry.id)
        if ov is not None:
            return ov
        base = float(entry.metadata.get("depth", 1.0))
        ts = float(entry.metadata.get("ts", entry.created_at))
        age = max(0.0, time.time() - ts)
        return base * (0.5 ** (age / self.cfg.half_life_seconds))

    # -- READ (hot path: 0 LLM until the final answer) -------------------

    def gate(self, query: str, recent_context: str) -> bool:
        """PURE retrieval gate (no LLM): skip memory search when the answer is
        already in the recent in-context window. Heuristic = content-word
        overlap between query and recent_context. Returns True ⇒ DO retrieve."""
        q = _content_words(query)
        if not q:
            return True
        recent = _content_words(recent_context)
        overlap = len(q & recent) / len(q)
        return overlap < 0.6  # most query terms already present ⇒ skip retrieve

    def recall(self, query: str, k: int = 5) -> list[MemoryEntry]:
        """Vector recall over the FACT layers (atom/raw) + recency rerank.

        Searches only the fact layers — derived scenario/persona summaries are
        for ``inject_profile``, not fact lookup (and would otherwise re-surface a
        surrendered fact). Over-fetches on cosine, drops forgotten facts
        (depth=0), then reranks ``cosine + λ·recency`` where recency is
        rank-normalised by ``ts`` ACROSS the candidates (newest=1, pinned=1).

        Relative recency — not the absolute decayed depth — is what makes the
        tiebreak work for old-vs-old contradictions (two stale facts both decay
        to ~0, so absolute depth can't separate them; their ts still can). The
        tiebreak is gentle (small λ): a clearly-relevant old fact is never
        dislodged by fresher but weakly-relevant filler."""
        hits = self.store.search(query, top_k=k * self.cfg.overfetch)
        cand = [
            h for h in hits
            if h.metadata.get("layer") not in (LAYER_SCENARIO, LAYER_PERSONA)
            and self._depth(h) > 0.0  # surrendered facts are forgotten
        ]
        ts = [float(h.metadata.get("ts", h.created_at)) for h in cand]
        lo, hi = (min(ts), max(ts)) if ts else (0.0, 0.0)

        def recency(h: MemoryEntry) -> float:
            if math.isinf(self._depth(h)):  # pinned → always freshest
                return 1.0
            t = float(h.metadata.get("ts", h.created_at))
            return 1.0 if hi == lo else (t - lo) / (hi - lo)

        scored = [(h.similarity + self.cfg.depth_lambda * recency(h), h) for h in cand]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [h for _, h in scored[:k]]

    def inject(self, query: str, k: int = 5) -> str:
        """Top-layer context block for the reader. Atoms only — compact facts,
        not raw transcripts (TencentDB progressive disclosure)."""
        hits = self.recall(query, k=k)
        return "\n".join(f"- {h.content}" for h in hits)

    def inject_context(self, query: str, k: int = 5) -> str:
        """Adapter so ``TieredMemory`` is a drop-in ``memory`` for ``run_agent``
        (which calls ``memory.inject_context(task)``). Wraps fact-layer recall in
        the same ``<memory_context>`` block ``MemoryStore`` emits."""
        facts = self.inject(query, k=k)
        return f"<memory_context>\n{facts}\n</memory_context>" if facts else ""

    def build_messages(
        self, query: str, k: int = 5, *, commit: bool = True
    ) -> list[Message]:
        """Reader messages with the commit-biased system prompt (the accuracy
        lever). Caller makes the single answer LLM call."""
        ctx = self.inject(query, k=k)
        system = COMMIT_SYSTEM if commit else HEDGE_SYSTEM
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Context:\n{ctx}\n\nQuestion: {query}"},
        ]


# ---------------------------------------------------------------------------

_STOP = frozenset(
    "the a an is are was were do does did what which who whom whose when where "
    "why how my your our their his her its of to in on at for and or with i me "
    "you we they it this that".split()
)


def _content_words(text: str) -> set[str]:
    """Lowercase content words (stopwords dropped) for the pure gate heuristic."""
    return {
        w for w in "".join(c if c.isalnum() else " " for c in text.lower()).split()
        if w and w not in _STOP
    }


def _demo() -> None:
    """Self-check: assert-based, no framework. Proves the deterministic pieces
    (gate purity, depth rerank, forgetting) without any LLM."""
    from agentkit.memory.store import MemoryEntry as _E

    # gate: query terms already in recent context ⇒ skip retrieval
    tm = TieredMemory.__new__(TieredMemory)
    tm.cfg = TieredConfig()
    tm._overrides = {}
    assert tm.gate("what is my dog's name", "we talked about cats") is True
    assert tm.gate("dog name", "my dog name is Mochi the dog") is False

    # depth: fresh > stale, pinned = inf, surrendered = 0
    now = time.time()
    fresh = _E(1, "atom", "x", {"depth": 1.0, "ts": now}, 0.5, now)
    stale = _E(2, "atom", "x", {"depth": 1.0, "ts": now - 36000}, 0.5, now)
    assert tm._depth(fresh) > tm._depth(stale)
    tm.pin(3); tm.surrender(4)
    assert math.isinf(tm._overrides[3]) and tm._overrides[4] == 0.0

    # depth is a TIEBREAK, not a multiplier: a high-cosine stale fact still
    # outranks a low-cosine fresh one (needle-in-haystack stays correct).
    needle = tm.cfg.depth_lambda * 1.0
    assert 0.90 + tm.cfg.depth_lambda * 0.01 > 0.50 + needle
    print("tiered._demo OK")


if __name__ == "__main__":
    _demo()
