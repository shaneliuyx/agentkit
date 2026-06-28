"""Shared types for the document-restructuring primitives (dedup, ranking, repair).

Kept dependency-free so ``agentkit.artifacts`` stays importable without numpy/embedders;
the concrete embedder is injected by the caller (studio wires its BGE-M3 client)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Finding:
    """One grounded research finding extracted from a worker draft.

    ``quote`` is verbatim copy-paste evidence; ``why`` frames its relevance; ``popularity``
    is the raw stated metric string (e.g. "6.5M views" / "n/a"). ``grounded`` is the dual
    oracle (URL fetched OR quote verbatim-verified) — only grounded findings reach a patch.
    """

    url: str
    title: str = ""
    quote: str = ""
    why: str = ""
    popularity: str = ""
    patch_target: str = ""
    quote_verified: bool = False
    grounded: bool = False


class Embedder(Protocol):
    """Duck-typed embedder: ``embed(list[str]) -> list[list[float]]`` (e.g. BGE-M3)."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...
