"""agentkit.memory.store — SQLite + numpy cosine memory store.

Three memory types (episodic/semantic/procedural), retrieved by cosine
similarity over embeddings. Generalized from the self-improving-agent-lab
store: the hardcoded ``openai.OpenAI`` / oMLX embedding call is replaced by an
injected ``Embedder`` (agentkit.types.Embedder), and the ``from config import
settings`` dependency is dropped — the store takes ``db_path`` + ``embedder``
as constructor args.

Failure tolerance is preserved exactly: an embedding failure on ``add`` stores
the entry WITHOUT a vector (it simply won't appear in similarity search), and
``inject_context`` degrades to an empty string rather than breaking the loop.

Schema:
  memories(id, memory_type, content, embedding_blob, metadata_json, created_at)

Usage:
    store = MemoryStore("memory.db", embedder=my_embedder)
    store.add("episodic", "Solved arithmetic task", {"task": "2+2", "answer": 4})
    results = store.search("arithmetic problem", top_k=3)
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from agentkit.types import Embedder


# ---------------------------------------------------------------------------
# Vector (de)serialization + similarity
# ---------------------------------------------------------------------------

def _vec_to_blob(vec: list[float]) -> bytes:
    """Pack a float list into a compact binary blob for SQLite storage."""
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> np.ndarray:
    """Unpack a binary blob back into a numpy array."""
    n = len(blob) // 4  # 4 bytes per float32
    return np.array(struct.unpack(f"{n}f", blob), dtype=np.float32)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors. Returns 0 if either is zero."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# Cheap keyword rung of the retrieval ladder (P23) + topic-presence tokens (P35)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_TOKENS = frozenset(
    "the a an is are was were do does did what which who whom whose when where "
    "why how of to in on at for and or with i me you we they it this that".split()
)


def _content_tokens(text: str) -> set[str]:
    """Lowercase content tokens (stopwords dropped). Shared by the keyword rung
    and the topic-presence check — both are pure, model-free string work."""
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP_TOKENS}


def _keyword_prefilter(query: str, rows: list[tuple]) -> list[tuple]:
    """The cheap rung BELOW the vector tier (P23): keep only rows whose content
    or source shares at least one content token with the query. Deterministic,
    zero-embedding — it runs first and the vector tier ranks within the result.
    Returns ``[]`` when nothing matches (caller falls back to the full set)."""
    q = _content_tokens(query)
    if not q:
        return []
    kept: list[tuple] = []
    for row in rows:
        hay = _content_tokens(f"{row[2]} {row[6] or ''}")  # content + source
        if q & hay:
            kept.append(row)
    return kept


# ---------------------------------------------------------------------------
# Memory entry dataclass
# ---------------------------------------------------------------------------

@dataclass
class MemoryEntry:
    """A single memory retrieved from the store.

    ``source`` is a write-time provenance tag (P34 evidence-before-belief):
    where this memory came from (e.g. ``"user"``, ``"assistant"``, a tool name,
    a doc id). It is persisted and survives search; defaults to ``None`` for
    back-compat with rows written before provenance existed.

    ``access_count`` / ``last_used`` are the read/retention-loop signal (P36):
    a successful ``search`` increments ``access_count`` and stamps ``last_used``
    on the rows it returned (opt-out via ``search(track=False)``), so a decay /
    eviction policy can act on *real* usage rather than a synthetic proxy.
    """
    id: int
    memory_type: str        # "episodic" | "semantic" | "procedural"
    content: str
    metadata: dict[str, Any]
    similarity: float       # cosine similarity to the query (0-1)
    created_at: float       # unix timestamp
    source: str | None = None       # P34: write-time provenance tag
    access_count: int = 0           # P36: times returned by a tracked search
    last_used: float | None = None  # P36: last tracked-search unix timestamp


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------

class MemoryStore:
    """SQLite + numpy cosine-similarity vector store.

    Embeddings are produced by an injected ``Embedder`` — never a hardcoded
    vendor. The store is append-only by design (immutable data makes the
    history fully auditable)."""

    def __init__(self, db_path: str | Path, embedder: Embedder) -> None:
        self.db_path = Path(db_path)
        self.embedder = embedder
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_type    TEXT    NOT NULL,
                content        TEXT    NOT NULL,
                embedding_blob BLOB,
                metadata_json  TEXT    NOT NULL DEFAULT '{}',
                created_at     REAL    NOT NULL,
                source         TEXT,
                access_count   INTEGER NOT NULL DEFAULT 0,
                last_used      REAL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_type ON memories(memory_type)"
        )
        self._migrate_columns()
        self._conn.commit()

    def _migrate_columns(self) -> None:
        """Add the P34 (``source``) and P36 (``access_count``/``last_used``)
        columns to a pre-existing DB. Idempotent: a fresh DB already has them
        from ``CREATE TABLE``; an old DB gets them via ALTER. Back-compat so a
        store written before provenance/usage-tracking keeps opening."""
        have = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "source" not in have:
            self._conn.execute("ALTER TABLE memories ADD COLUMN source TEXT")
        if "access_count" not in have:
            self._conn.execute(
                "ALTER TABLE memories ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0"
            )
        if "last_used" not in have:
            self._conn.execute("ALTER TABLE memories ADD COLUMN last_used REAL")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(
        self,
        memory_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        embed: bool = True,
        source: str | None = None,
    ) -> int:
        """Add a new memory entry. Returns the new row's integer id.

        Args:
            memory_type: "episodic" | "semantic" | "procedural"
            content:     The text content to store and embed.
            metadata:    Arbitrary JSON-serialisable metadata dict.
            embed:       Whether to compute and store an embedding.
                         Set False for bulk imports where you add embeddings later.
            source:      Optional write-time provenance tag (P34): where this
                         memory came from (role, tool, doc id). Persisted and
                         surfaced on every ``MemoryEntry``; defaults to None.
        """
        blob: bytes | None = None
        if embed:
            try:
                vectors = self.embedder.embed([content])
                blob = _vec_to_blob(vectors[0])
            except Exception as exc:
                # Embedding failures are non-fatal; store without a vector.
                # The entry will not appear in similarity search results.
                warnings.warn(
                    f"Embedding failed (will store without vector): {exc}",
                    stacklevel=2,
                )

        cursor = self._conn.execute(
            """
            INSERT INTO memories (memory_type, content, embedding_blob, metadata_json, created_at, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                memory_type,
                content,
                blob,
                json.dumps(metadata or {}),
                time.time(),
                source,
            ),
        )
        self._conn.commit()
        # lastrowid is Optional in the type stubs but always set after an INSERT.
        return cursor.lastrowid if cursor.lastrowid is not None else -1

    def record_trajectory(self, trajectory: dict, salience: float = 0.5) -> int:
        """Store one agent trajectory as an episodic memory.

        `salience` (0-1) is kept in metadata for later recency/salience ranking.
        Thin sugar over add() so callers have a single verb for the RECORD step.
        """
        content = trajectory.get("summary") or json.dumps(trajectory)[:2000]
        return self.add(
            "episodic",
            content,
            metadata={"salience": float(salience), **trajectory.get("metadata", {})},
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    _SELECT_COLS = (
        "id, memory_type, content, embedding_blob, metadata_json, created_at, "
        "source, access_count, last_used"
    )

    def _row_to_entry(self, row: tuple, similarity: float) -> MemoryEntry:
        """Build a ``MemoryEntry`` from a ``_SELECT_COLS`` row."""
        return MemoryEntry(
            id=row[0],
            memory_type=row[1],
            content=row[2],
            metadata=json.loads(row[4]),
            similarity=similarity,
            created_at=row[5],
            source=row[6],
            access_count=row[7] if row[7] is not None else 0,
            last_used=row[8],
        )

    def search(
        self,
        query: str,
        memory_type: str | None = None,
        top_k: int = 5,
        track: bool = True,
        prefilter: bool = False,
    ) -> list[MemoryEntry]:
        """Retrieve the top_k most similar memories for a query string.

        Args:
            query:       The search query (embedded via the injected embedder).
            memory_type: Optional filter ("episodic", "semantic", "procedural").
            top_k:       Number of results to return.
            track:       When True (default), record usage on the returned rows
                         (P36): bump ``access_count`` and stamp ``last_used`` so a
                         retention/eviction policy acts on real reads. Pass False
                         for side-effect-free probes / ablation harnesses.
            prefilter:   When True, run the cheap keyword rung of the retrieval
                         ladder FIRST (P23): a deterministic substring/token
                         prefilter narrows the candidate set, then the vector
                         tier ranks within it. Default False preserves the
                         vector-only behavior every existing caller relies on.

        Returns:
            List of MemoryEntry sorted by descending cosine similarity.

        Raises:
            RuntimeError: if the query cannot be embedded (search needs a vector).
        """
        try:
            query_vec = np.array(self.embedder.embed([query])[0], dtype=np.float32)
        except Exception as exc:
            raise RuntimeError(f"Failed to embed query: {exc}") from exc

        # Fetch all rows that have embeddings
        if memory_type:
            rows = self._conn.execute(
                f"SELECT {self._SELECT_COLS} "
                "FROM memories WHERE memory_type=? AND embedding_blob IS NOT NULL",
                (memory_type,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT {self._SELECT_COLS} "
                "FROM memories WHERE embedding_blob IS NOT NULL"
            ).fetchall()

        # P23 — cheap-first retrieval ladder: the keyword rung runs BELOW the
        # vector tier. A deterministic token-overlap prefilter narrows the rows;
        # only the survivors pay the cosine cost. If nothing matches the cheap
        # rung, fall through to the full vector tier (never zero out a query).
        if prefilter:
            narrowed = _keyword_prefilter(query, rows)
            if narrowed:
                rows = narrowed

        scored: list[tuple[float, MemoryEntry]] = []
        for row in rows:
            sim = _cosine_similarity(query_vec, _blob_to_vec(row[3]))
            scored.append((sim, self._row_to_entry(row, sim)))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [entry for _, entry in scored[:top_k]]
        if track and results:
            self._track_usage([e.id for e in results])
            # Reflect the just-recorded usage on the returned entries too, so a
            # caller reading access_count right after search sees the bump.
            for e in results:
                e.access_count += 1
        return results

    def _track_usage(self, ids: list[int]) -> None:
        """P36 read/retention loop: increment ``access_count`` and stamp
        ``last_used`` on the given rows. This is the read path RECORDING what it
        used so retention/eviction (``evict_coldest``) consumes real signal."""
        if not ids:
            return
        now = time.time()
        self._conn.executemany(
            "UPDATE memories SET access_count = access_count + 1, last_used = ? WHERE id = ?",
            [(now, i) for i in ids],
        )
        self._conn.commit()

    def inject_context(self, query: str, k: int = 4) -> str:
        """Retrieve the top-k relevant memories and format them as a compact
        ``<memory_context>`` block for injection into the agent's system prompt
        BEFORE it acts. This is the read side of the experience layer.

        Returns "" when there is nothing useful to inject (no memories yet, or
        embeddings unavailable) so the caller can concatenate unconditionally.
        Never raises: a memory miss must not break the ACT loop.
        """
        try:
            hits = self.search(query, top_k=k)
        except Exception:
            # Embedder down / no embeddings: degrade to no context, never break
            # the loop. The agent simply runs without prior lessons.
            return ""
        # Only inject reasonably-relevant hits; a 0.0-similarity row is noise.
        useful = [h for h in hits if h.similarity > 0.0]
        if not useful:
            return ""
        lines = [
            f"- ({h.memory_type}, sim={h.similarity:.2f}) {h.content}"
            for h in useful
        ]
        return (
            "<memory_context>\n"
            "Relevant lessons from your past experience (use them; do not repeat past mistakes):\n"
            + "\n".join(lines)
            + "\n</memory_context>"
        )

    def get_recent(
        self,
        memory_type: str | None = None,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Return the most recent memories (by insertion time), optionally filtered by type."""
        if memory_type:
            rows = self._conn.execute(
                f"SELECT {self._SELECT_COLS} "
                "FROM memories WHERE memory_type=? ORDER BY created_at DESC LIMIT ?",
                (memory_type, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT {self._SELECT_COLS} "
                "FROM memories ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        return [self._row_to_entry(r, 0.0) for r in rows]

    def topic_present(
        self, query: str, memory_type: str | None = None, min_overlap: int = 1
    ) -> bool:
        """P35 abstention = topic-presence: cheap, deterministic "is the
        question's SUBJECT in the records at all?" — distinct from answer
        groundedness. A reader can call this BEFORE answering and abstain when it
        returns False, WITHOUT over-refusing answerable questions (the failure
        mode of a strict grounding gate).

        Pure token-overlap over stored content + source tags (no embedder, no
        LLM): True iff at least ``min_overlap`` content tokens of ``query`` appear
        in any stored memory. Biased toward PRESENT (any overlap ⇒ present), so it
        abstains only when the subject is genuinely absent.
        """
        q = _content_tokens(query)
        if not q:
            return False
        if memory_type:
            rows = self._conn.execute(
                "SELECT content, source FROM memories WHERE memory_type=?",
                (memory_type,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT content, source FROM memories"
            ).fetchall()
        for content, source in rows:
            hay = _content_tokens(f"{content} {source or ''}")
            if len(q & hay) >= min_overlap:
                return True
        return False

    def evict_coldest(self, keep: int) -> int:
        """P36 retention consumer: bound the store to ``keep`` rows, evicting the
        COLDEST first — fewest accesses, then least-recently used, then oldest.
        Consumes the ``access_count`` / ``last_used`` signal the read path
        records, so eviction is *earned* (survivors are what recall kept using)
        rather than arbitrary. Returns the number of rows evicted."""
        total = self.count()
        if total <= keep:
            return 0
        n_evict = total - keep
        victims = self._conn.execute(
            "SELECT id FROM memories "
            "ORDER BY access_count ASC, "
            "COALESCE(last_used, 0) ASC, created_at ASC "
            "LIMIT ?",
            (n_evict,),
        ).fetchall()
        ids = [v[0] for v in victims]
        self._conn.executemany("DELETE FROM memories WHERE id = ?", [(i,) for i in ids])
        self._conn.commit()
        return len(ids)

    def count(self, memory_type: str | None = None) -> int:
        """Return the total number of stored memories."""
        if memory_type:
            return self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE memory_type=?", (memory_type,)
            ).fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


if __name__ == "__main__":
    import hashlib
    import tempfile

    class _HashEmbedder:
        """Deterministic bag-of-words hashing embedder: shared tokens → higher
        cosine similarity. Good enough to exercise search/ranking offline."""

        def __init__(self, dim: int = 64) -> None:
            self.dim = dim

        def embed(self, texts: list[str]) -> list[list[float]]:
            out = []
            for t in texts:
                vec = [0.0] * self.dim
                for tok in t.lower().split():
                    h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
                    vec[h % self.dim] += 1.0
                out.append(vec)
            return out

    store = MemoryStore(tempfile.mktemp(suffix=".db"), embedder=_HashEmbedder())
    store.add("episodic", "solved 2+2 = 4")
    store.add("semantic", "addition combines two numbers")
    store.add("procedural", "to add, use the calc tool")
    assert store.count() == 3
    assert store.count("episodic") == 1

    hits = store.search("addition arithmetic", top_k=2)
    assert len(hits) == 2
    assert hits[0].similarity >= hits[1].similarity  # sorted descending

    sem = store.search("addition", memory_type="semantic", top_k=5)
    assert all(h.memory_type == "semantic" for h in sem)

    ctx = store.inject_context("addition")
    assert ctx.startswith("<memory_context>")

    recent = store.get_recent(limit=2)
    assert len(recent) == 2

    # P34 provenance: source survives the write and the search round-trip.
    sid = store.add("episodic", "deploy ran at noon", source="assistant")
    assert sid > 0
    src_hits = store.search("deploy noon", top_k=3)
    assert any(h.content == "deploy ran at noon" and h.source == "assistant"
               for h in src_hits), src_hits

    # P36 read/retention loop: a tracked search bumps access_count + last_used.
    fresh = MemoryStore(tempfile.mktemp(suffix=".db"), embedder=_HashEmbedder())
    fresh.add("semantic", "alpha beta gamma")
    fresh.add("semantic", "delta epsilon zeta")
    before = fresh.get_recent(memory_type="semantic", limit=10)
    assert all(e.access_count == 0 for e in before)
    fresh.search("alpha beta", top_k=1)  # track defaults True
    after = {e.content: e for e in fresh.get_recent(memory_type="semantic", limit=10)}
    assert after["alpha beta gamma"].access_count == 1
    assert after["alpha beta gamma"].last_used is not None
    assert after["delta epsilon zeta"].access_count == 0  # untouched
    fresh.search("alpha beta", top_k=1, track=False)       # opt-out: no bump
    again = {e.content: e for e in fresh.get_recent(memory_type="semantic", limit=10)}
    assert again["alpha beta gamma"].access_count == 1

    # P36 retention consumer: evict the coldest, keep the hot one.
    fresh.search("alpha beta", top_k=1)  # alpha now hotter than delta
    evicted = fresh.evict_coldest(keep=1)
    assert evicted == 1
    survivors = [e.content for e in fresh.get_recent(memory_type="semantic", limit=10)]
    assert survivors == ["alpha beta gamma"], survivors

    # P35 topic-presence: present subject → True, absent → abstain (False).
    assert fresh.topic_present("alpha gamma") is True
    assert fresh.topic_present("quantum chromodynamics") is False

    # P23 cheap-first ladder: keyword prefilter narrows before the vector tier.
    ladder = MemoryStore(tempfile.mktemp(suffix=".db"), embedder=_HashEmbedder())
    ladder.add("semantic", "redis cache eviction policy")
    ladder.add("semantic", "postgres index tuning")
    pre = ladder.search("redis eviction", top_k=5, prefilter=True, track=False)
    assert all("redis" in h.content or "eviction" in h.content for h in pre), pre

    print("store self-check OK")
