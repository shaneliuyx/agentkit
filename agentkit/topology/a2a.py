"""agentkit.topology.a2a — a minimal agent-to-agent message bus for mesh.

A mesh topology is peers *communicating + sharing context*, not just a fan-out
whose workers feed results forward. Threading DAG results downstream (peer_r2
reads peer_r1) is a one-directional approximation; genuine mesh needs a shared
channel agents POST to and READ from.

This one bus serves both needs the user named:
  - **communication (A2A):** directed messages (``recipient=...``) between peers,
    over rounds of read-and-respond — the dependency-light, in-process analog of
    the networked Agent2Agent protocol (true A2A is HTTP/JSON-RPC between agent
    servers; this is the same message shape over a shared object).
  - **shared context / output:** broadcasts + ``transcript()`` form a blackboard
    every peer reads — so peers share each other's context and outputs.

Thread-safe, so it works under the parallel worker pool.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    """One A2A message. ``recipient=None`` is a broadcast (shared context)."""

    sender: str
    content: str
    round: int = 0
    recipient: str | None = None
    ts: float = 0.0


class MessageBus:
    """A thread-safe shared channel for peer agents — message-passing AND a
    shared-context blackboard.

    Peers ``post`` (broadcast or addressed) and ``read`` what their peers said.
    ``read`` excludes the reader's OWN messages by default — so a peer sees what
    *others* contributed, which is what makes this communication, not self-echo."""

    def __init__(self) -> None:
        self._msgs: list[Message] = []
        self._lock = threading.Lock()

    def post(self, sender: str, content: str, *, round: int = 0,
             recipient: str | None = None) -> None:
        with self._lock:
            self._msgs.append(Message(sender, content, round, recipient, time.time()))

    def read(self, *, reader: str | None = None, round: int | None = None,
             ) -> list[Message]:
        """Messages visible to ``reader``: broadcasts + ones addressed to it,
        excluding its own. Optionally filtered to a ``round``."""
        with self._lock:
            out = []
            for m in self._msgs:
                if reader is not None and m.sender == reader:
                    continue                      # don't read your own posts
                if m.recipient not in (None, reader):
                    continue                      # addressed to someone else
                if round is not None and m.round != round:
                    continue
                out.append(m)
            return out

    def context(self, reader: str | None = None) -> str:
        """The shared context/output blackboard as text — every peer message
        visible to ``reader``, newest-friendly order. For prompt injection."""
        return "\n".join(f"[{m.sender}] {m.content}" for m in self.read(reader=reader))

    def transcript(self) -> list[Message]:
        with self._lock:
            return list(self._msgs)


def _demo() -> None:
    """Self-check: peers exchange messages + share context; read() excludes self."""
    bus = MessageBus()
    bus.post("peer1", "I think it's a payload schema mismatch", round=1)
    bus.post("peer2", "More likely a token-limit overflow", round=1)
    bus.post("peer3", "Could be a concurrency race", round=1)

    # peer1 reads what the OTHERS said in round 1 — not its own message.
    heard = bus.read(reader="peer1", round=1)
    assert {m.sender for m in heard} == {"peer2", "peer3"}     # genuine cross-talk
    assert all(m.sender != "peer1" for m in heard)             # no self-echo

    # shared-context view excludes the reader's own contribution.
    assert "peer1" not in bus.context(reader="peer1")
    assert "peer2" in bus.context(reader="peer1")

    # an addressed message is private to its recipient (A2A directed).
    bus.post("peer2", "peer1, can you confirm the schema?", round=2, recipient="peer1")
    assert any("confirm" in m.content for m in bus.read(reader="peer1"))
    assert not any("confirm" in m.content for m in bus.read(reader="peer3"))

    assert len(bus.transcript()) == 4
    print("topology.a2a._demo OK")


if __name__ == "__main__":
    _demo()
