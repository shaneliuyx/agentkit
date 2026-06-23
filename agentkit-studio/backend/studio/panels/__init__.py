"""studio.panels — comprehensive panel data sources (SPEC §5.5).

Each module turns agentkit machinery into one (or a few) SSE event(s). Panels
degrade gracefully: when a local service (oMLX :8000, Qdrant :6333, Phoenix
:6006) is down, the panel emits an empty result + a notice rather than crashing
the run.
"""
