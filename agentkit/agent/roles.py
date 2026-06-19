"""agentkit.agent.roles — role specialization as configuration over run_agent.

A *role* is not a new agent runtime; it is a frozen bundle of configuration
(system prompt, tools, default difficulty, optional output schema) applied to
the EXISTING ``run_agent`` loop. This captures the feynman-style ensemble
(Researcher / Reviewer / Writer / Verifier) without forking the loop.

Dispatch is deterministic-first (the library thesis): the default ``dispatch``
is a pure keyword heuristic that needs no model. An LLM classifier is optional
and injected — never required.

Difficulty labels match ``agentkit.agent.router`` exactly:
``trivial | easy | medium | hard | critical``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agentkit.agent.loop import AgentResult, ToolFn, ToolRegistry, run_agent
from agentkit.types import LLMClient


@dataclass(frozen=True)
class AgentRole:
    """An immutable role: configuration applied over ``run_agent``.

    Attributes:
        name:          Human-readable role name (e.g. "Researcher").
        system_prompt: The system prompt that specializes the agent's behavior.
        tools:         A tuple of tool names this role is expected to use
                       (advisory metadata; the actual registry is injected at
                       run time via ``run_role``).
        difficulty:    Router difficulty label
                       (trivial|easy|medium|hard|critical) — the role's default
                       reasoning tier.
        output_schema: Optional JSON-schema-like dict describing the role's
                       expected structured output (None if free-form).
    """

    name: str
    system_prompt: str
    tools: tuple[str, ...] = ()
    difficulty: str = "medium"
    output_schema: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Presets — the feynman ensemble.
# ---------------------------------------------------------------------------

RESEARCHER = AgentRole(
    name="Researcher",
    system_prompt=(
        "You are a Researcher. Gather evidence across papers, the web, code "
        "repositories, and documentation to answer the task. Prefer primary "
        "sources. For every non-trivial claim, attach the source it came from "
        "(a URL or a citation marker). Distinguish what the sources actually "
        "say from your own inference. Output your findings with their citations."
    ),
    tools=("web_search", "read_url", "search_code", "read_docs"),
    difficulty="medium",
)

REVIEWER = AgentRole(
    name="Reviewer",
    system_prompt=(
        "You are a Reviewer running a simulated peer review. Critique the "
        "provided work for correctness, soundness, clarity, and completeness. "
        "Output SEVERITY-GRADED findings, each labeled one of "
        "critical / high / medium / low, with a short justification. After the "
        "findings, produce a concise revision plan: the ordered set of changes "
        "that would address the findings, highest severity first."
    ),
    tools=(),
    difficulty="hard",
)

WRITER = AgentRole(
    name="Writer",
    system_prompt=(
        "You are a Writer. Turn the provided research notes into a structured, "
        "well-organized draft. Use clear sections and headings, lead with the "
        "key point, and preserve any citations present in the notes. Do not "
        "invent facts that are not supported by the notes."
    ),
    tools=(),
    difficulty="medium",
)

VERIFIER = AgentRole(
    name="Verifier",
    system_prompt=(
        "You are a Verifier. Check the inline citations in the provided text: "
        "confirm each claim is backed by a citation, verify that cited source "
        "URLs are live, and flag or clean dead links. Report uncited claims, "
        "dead links, and any claim a cited source does not actually support, "
        "graded by severity (critical / high / medium / low)."
    ),
    tools=("check_url", "read_url"),
    difficulty="hard",
)

DEFAULT_ROLES: tuple[AgentRole, ...] = (RESEARCHER, REVIEWER, WRITER, VERIFIER)


# ---------------------------------------------------------------------------
# run_role — thin wrapper over the existing run_agent.
# ---------------------------------------------------------------------------

def run_role(
    role: AgentRole,
    task: str,
    client: LLMClient,
    tools: ToolRegistry | dict[str, ToolFn] | None = None,
    memory: Any | None = None,
    max_rounds: int | None = None,
) -> AgentResult:
    """Run ``task`` under ``role`` against an injected LLM client.

    This is configuration over ``run_agent``: the role supplies the system
    prompt (and advisory tool list / difficulty); the caller injects the actual
    ``client``, ``tools`` registry, and optional ``memory``.

    Args:
        role:       The AgentRole specializing the run.
        task:       The task string.
        client:     An injected LLMClient.
        tools:      A ToolRegistry or {name: handler} dict (or None). The
                    role's ``tools`` tuple is advisory metadata; the concrete
                    registry is what actually gets dispatched.
        memory:     Optional memory with ``.inject_context(task) -> str``.
        max_rounds: Optional override for the loop's max rounds.

    Returns:
        The AgentResult produced by ``run_agent``.
    """
    kwargs: dict[str, Any] = {
        "client": client,
        "tools": tools,
        "system_prompt": role.system_prompt,
        "memory": memory,
    }
    if max_rounds is not None:
        kwargs["max_rounds"] = max_rounds
    return run_agent(task, **kwargs)


# ---------------------------------------------------------------------------
# dispatch — deterministic-first role selection.
# ---------------------------------------------------------------------------

# Keyword -> role-name mapping for the deterministic heuristic.
_REVIEW_KEYWORDS = ("review", "audit", "critique")
_WRITE_KEYWORDS = ("draft", "write", "compose")
_VERIFY_KEYWORDS = ("verify", "cite", "citation", "check links", "check link")


def _by_name(roles: tuple[AgentRole, ...], name: str) -> AgentRole | None:
    for r in roles:
        if r.name.lower() == name.lower():
            return r
    return None


def dispatch(
    task: str,
    roles: tuple[AgentRole, ...] = DEFAULT_ROLES,
    classifier: Callable[[str, tuple[AgentRole, ...]], AgentRole] | None = None,
) -> AgentRole:
    """Select a role for ``task``.

    Deterministic-first: if no ``classifier`` is injected, a pure keyword
    heuristic decides:
      - review / audit / critique         -> Reviewer
      - draft / write / compose           -> Writer
      - verify / cite / citation / links  -> Verifier
      - otherwise                         -> Researcher

    If a ``classifier`` is injected, it is used instead (the optional LLM tier).

    Args:
        task:       The task string.
        roles:      Candidate roles (defaults to the four presets).
        classifier: Optional callable ``(task, roles) -> AgentRole``.

    Returns:
        The selected AgentRole.
    """
    if classifier is not None:
        return classifier(task, roles)

    text = task.lower()

    def matches(keywords: tuple[str, ...]) -> bool:
        return any(kw in text for kw in keywords)

    if matches(_VERIFY_KEYWORDS):
        return _by_name(roles, "Verifier") or roles[0]
    if matches(_REVIEW_KEYWORDS):
        return _by_name(roles, "Reviewer") or roles[0]
    if matches(_WRITE_KEYWORDS):
        return _by_name(roles, "Writer") or roles[0]
    return _by_name(roles, "Researcher") or roles[0]


if __name__ == "__main__":
    from agentkit.types import ChatResult, Message

    # dispatch routes known phrases to the right role (no network).
    assert dispatch("Please review and audit this module").name == "Reviewer"
    assert dispatch("Draft a blog post about agents").name == "Writer"
    assert dispatch("Verify the citations and check links").name == "Verifier"
    assert dispatch("Find the latest papers on RAG").name == "Researcher"

    # All four presets present and frozen.
    assert len(DEFAULT_ROLES) == 4
    for _role in DEFAULT_ROLES:
        try:
            object.__setattr__  # noqa: B018 - referenced to keep linters quiet
            _role.name  # readable
        except Exception:  # pragma: no cover
            raise
    try:
        RESEARCHER.name = "x"  # type: ignore[misc]
        raise AssertionError("AgentRole must be frozen")
    except AttributeError:
        pass  # dataclasses.FrozenInstanceError is an AttributeError subclass

    # An injected classifier overrides the heuristic.
    forced = dispatch("anything", classifier=lambda t, rs: WRITER)
    assert forced.name == "Writer"

    # run_role builds a prompt containing the role's system_prompt (FAKE client,
    # no network). We capture the system message the loop sends.
    class _CapturingClient:
        def __init__(self) -> None:
            self.system_seen = ""

        def chat(self, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> ChatResult:
            for m in messages:
                if m.get("role") == "system":
                    self.system_seen = m.get("content", "")
            return ChatResult(text="final answer", total_tokens=3)

    client = _CapturingClient()
    result = run_role(RESEARCHER, "Find sources on X", client=client)
    assert isinstance(result, AgentResult)
    assert result.answer == "final answer"
    assert RESEARCHER.system_prompt in client.system_seen, client.system_seen

    print("roles self-check OK")
