"""studio.backends — the PROFILES menu → StudioChatClient / Embedder factory.

SPEC §2 note: there are two ``make_client``s. ``shared/llm.py`` owns the curated
``PROFILES`` menu (haiku/opus via VibeProxy :8317, 14b/qwen via oMLX :8000);
agentkit's adapters turn a resolved ``(base_url, model, key)`` into a Protocol
client. Studio uses ``PROFILES`` as the GUI dropdown source, then builds a
``StudioChatClient`` (usage-capturing) from the resolved triple.

A backend spec is either ``{"profile": "<name>"}`` (look up in PROFILES) or a
raw ``{"base_url", "model", "api_key"}`` override.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentkit.backends.openai_compat import OpenAIEmbedder

from studio.client import OnUsage, StudioChatClient
from studio.shared_bridge import PROFILES

#: oMLX endpoints (the local profiles) are treated as "local" — the embedder
#: factory targets the same host by default.
_LOCAL_ENDPOINT = "http://localhost:8000/v1"

#: Default local embedding model (oMLX BGE-M3), used when no embedder profile is
#: chosen. Matches the agent-prep smoke-test stack.
DEFAULT_EMBED_MODEL = "bge-m3-mlx-fp16"


@dataclass(frozen=True)
class ResolvedBackend:
    """A resolved LLM backend ready to build a client from."""

    label: str
    model: str
    base_url: str
    api_key: str
    kind: str  # "local" | "cloud" | "raw"


def _profile_kind(base_url: str) -> str:
    """Classify a profile endpoint for the GUI badge."""
    return "local" if base_url.startswith(_LOCAL_ENDPOINT[:21]) else "cloud"


def list_profiles() -> list[dict[str, Any]]:
    """The ``GET /backends`` menu: one entry per ``PROFILES`` name."""
    out: list[dict[str, Any]] = []
    for name, (base_url, _key, model) in PROFILES.items():
        out.append(
            {
                "name": name,
                "label": name,
                "kind": _profile_kind(base_url),
                "model": model,
                "endpoint": base_url,
            }
        )
    return out


def list_embedders() -> list[dict[str, Any]]:
    """The embedder menu — currently the single local oMLX embedder default.

    Kept as a list so the GUI dropdown shape matches ``list_profiles``.
    """
    return [
        {
            "name": "local",
            "label": "oMLX BGE-M3 (local)",
            "kind": "local",
            "model": DEFAULT_EMBED_MODEL,
            "endpoint": _LOCAL_ENDPOINT,
        }
    ]


def resolve_backend(spec: dict[str, Any]) -> ResolvedBackend:
    """Resolve a session ``llm`` spec into a ``ResolvedBackend``.

    ``spec`` is ``{"profile": "<name>"}`` or ``{"raw": {base_url, model, api_key}}``.
    Raises ``ValueError`` on an unknown profile or a malformed raw spec.
    """
    if "profile" in spec and spec["profile"]:
        name = spec["profile"]
        if name not in PROFILES:
            raise ValueError(
                f"unknown profile {name!r}; choose from {sorted(PROFILES)}"
            )
        base_url, api_key, model = PROFILES[name]
        return ResolvedBackend(
            label=name,
            model=model,
            base_url=base_url,
            api_key=api_key,
            kind=_profile_kind(base_url),
        )

    raw = spec.get("raw") or {}
    model = raw.get("model")
    if not model:
        raise ValueError("raw backend spec requires a 'model'")
    base_url = raw.get("base_url") or _LOCAL_ENDPOINT
    api_key = raw.get("api_key") or "EMPTY"
    return ResolvedBackend(
        label=raw.get("label") or model,
        model=model,
        base_url=base_url,
        api_key=api_key,
        kind="raw",
    )


def build_chat_client(
    backend: ResolvedBackend, on_usage: OnUsage, *, temperature: float = 0.0
) -> StudioChatClient:
    """Build the usage-capturing ``StudioChatClient`` for a resolved backend."""
    return StudioChatClient(
        backend.model,
        base_url=backend.base_url,
        api_key=backend.api_key,
        on_usage=on_usage,
        temperature=temperature,
    )


def build_embedder(spec: dict[str, Any] | None) -> tuple[OpenAIEmbedder | None, dict[str, str]]:
    """Build a local OpenAI-compatible embedder, returning ``(embedder, info)``.

    ``info`` is the ``{label, model}`` dict for the ``session`` event. Returns
    ``(None, {...})`` if no embedder is requested. Construction never makes a
    network call (oMLX defaults + sentinel key), so a down service degrades only
    when a panel actually embeds (handled in the memory panel).
    """
    spec = spec or {}
    if "raw" in spec and spec["raw"]:
        raw = spec["raw"]
        model = raw.get("model") or DEFAULT_EMBED_MODEL
        embedder = OpenAIEmbedder(
            model, base_url=raw.get("base_url"), api_key=raw.get("api_key")
        )
        return embedder, {"label": raw.get("label") or model, "model": model}

    # Default local embedder (or explicit "local" profile).
    model = DEFAULT_EMBED_MODEL
    embedder = OpenAIEmbedder(model, base_url=_LOCAL_ENDPOINT)
    return embedder, {"label": "oMLX BGE-M3 (local)", "model": model}
