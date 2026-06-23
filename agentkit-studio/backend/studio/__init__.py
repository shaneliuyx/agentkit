"""AgentKit Studio backend — FastAPI glue over agentkit + agent-prep/shared.

The package is the GUI layer of SPEC.md: it plans a requirement into phases,
assigns a per-phase topology, runs each phase under agentkit's real fan-out
primitives, and streams a typed SSE event sequence to the React frontend.

Nothing here re-implements agentkit; it composes it. The one bespoke piece is
``StudioChatClient`` (client.py), which captures the prompt/completion token
split that agentkit's own ``OpenAIChatClient`` discards — the in/out HUD needs
it.
"""

__version__ = "0.1.0"
