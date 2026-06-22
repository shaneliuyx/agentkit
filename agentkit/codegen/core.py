"""agentkit.codegen.core — agent-authored, sandbox-validated tools (REPLAN §4 Ph6).

The youtu EDP-46 auto-tool-gen pipeline, gated by the existing security spine:

    query -> schema -> code -> sandbox-validate -> debugger-repair -> gate -> register

Two things are the injected LLM (``types.LLMClient``): the **code-proposer**
(drafts a tool SCHEMA first, then an implementation against it — schema before
code) and the **debugger** (patches a failing draft from its traceback).
Everything else is deterministic and model-free:

  - the validate+repair LOOP control (run in ``SubprocessSandbox`` -> inspect
    ``exit_code`` -> decide repair-or-stop, bounded by ``max_repairs``);
  - admission via ``gates.run_gate``. The gate's containment stage ESCALATES any
    proposal carrying side-effecting tokens (subprocess/network/fs-mutation), so
    an I/O tool stops for a human while a pure/read-only tool can ACCEPT.

``register`` honors that verdict: it auto-registers ONLY an ACCEPTed tool;
ESCALATE/REJECT are returned to the caller, never silently registered.

Pure stdlib + agentkit seams. No vendor import; the LLM is injected.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agentkit.gates.core import Gate, Outcome, Verdict
from agentkit.sandbox.core import Sandbox
from agentkit.types import LLMClient, Message

# Default ceiling on debugger repair attempts. The loop tries the proposer once,
# then asks the debugger at most this many times before giving up — it can never
# spin forever.
DEFAULT_MAX_REPAIRS: int = 3

# A fenced ```python ... ``` (or bare ``` ... ```) code block in an LLM reply.
_CODE_BLOCK_RE = re.compile(
    r"```(?:python)?\s*\n(?P<code>.*?)```", re.DOTALL | re.IGNORECASE
)
# A JSON object following a ``SCHEMA:`` marker, or the first standalone object.
_SCHEMA_RE = re.compile(r"SCHEMA:\s*(?P<json>\{.*?\})\s*(?:CODE:|```|$)", re.DOTALL)

_PROPOSE_SYSTEM = (
    "You are a tool author for an agent. Given a capability request, FIRST emit "
    "a JSON tool schema (name, description, parameters), THEN a Python "
    "implementation that matches it. Respond exactly as:\n"
    "SCHEMA:\n<json>\n\nCODE:\n```python\n<code>\n```\n"
    "Keep the code pure and read-only unless the task truly needs side effects."
)

_DEBUG_SYSTEM = (
    "You are a debugger. A generated tool failed when run in a sandbox. Given "
    "its code and the traceback, return ONLY the corrected Python code in a "
    "```python ...``` block. Do not explain."
)


@dataclass(frozen=True)
class GeneratedTool:
    """An agent-authored tool and the verdict the gate reached on it.

    Attributes:
        name:     the tool's name (from its schema).
        schema:   the typed parameter schema (name, description, parameters).
        code:     the (possibly debugger-repaired) implementation source.
        manifest: an MCP-style dict {name, schema, code} for registration.
        verdict:  the gate ``Verdict`` (None only before admission is run).
    """

    name: str
    schema: dict[str, Any]
    code: str
    manifest: dict[str, Any]
    verdict: Verdict | None = field(default=None)


def _extract_code(text: str) -> str:
    """Pull the first fenced code block from an LLM reply, else the raw text."""
    m = _CODE_BLOCK_RE.search(text or "")
    if m:
        return m.group("code").strip() + "\n"
    return (text or "").strip() + "\n"


def _extract_schema(text: str, *, fallback_name: str) -> dict[str, Any]:
    """Parse the ``SCHEMA:`` JSON from an LLM reply; degrade to a minimal one."""
    m = _SCHEMA_RE.search(text or "")
    raw = m.group("json") if m else ""
    if not raw:
        # No SCHEMA marker: try the first standalone JSON object in the reply.
        brace = re.search(r"\{.*\}", text or "", re.DOTALL)
        raw = brace.group(0) if brace else ""
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("name"):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return {"name": fallback_name, "description": "", "parameters": {}}


def parse_proposal(text: str, *, fallback_name: str) -> tuple[dict[str, Any], str]:
    """Deterministically split an LLM reply into ``(schema, code)``.

    Schema-before-code: the schema is read first, then the implementation. This
    is pure parsing — no model call — so the proposer's structure is enforced
    by the caller, not trusted blindly.
    """
    schema = _extract_schema(text, fallback_name=fallback_name)
    code = _extract_code(text)
    return schema, code


def _slug(query: str) -> str:
    """A safe fallback tool name derived from the query."""
    slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
    return slug[:40] or "generated_tool"


def propose_tool(query: str, *, client: LLMClient) -> GeneratedTool:
    """Draft a tool: the injected LLM emits a SCHEMA first, then CODE for it.

    Returns an unvalidated ``GeneratedTool`` (``verdict`` is None). The forge's
    validate/repair/gate steps decide whether it is admissible.
    """
    messages: list[Message] = [
        {"role": "system", "content": _PROPOSE_SYSTEM},
        {"role": "user", "content": f"Capability requested: {query}"},
    ]
    response = client.chat(messages)
    text = getattr(response, "text", "") or ""
    schema, code = parse_proposal(text, fallback_name=_slug(query))
    name = str(schema.get("name") or _slug(query))
    return GeneratedTool(
        name=name,
        schema=schema,
        code=code,
        manifest=_manifest(name, schema, code),
        verdict=None,
    )


def _manifest(name: str, schema: dict[str, Any], code: str) -> dict[str, Any]:
    """An MCP-style manifest dict for a generated tool."""
    return {"name": name, "schema": schema, "code": code}


@dataclass(frozen=True)
class ToolForge:
    """Forge agent-authored tools end-to-end behind the security spine.

    Injected dependencies (never vendors):
        client:  the LLMClient for the proposer AND the debugger.
        sandbox: the ``Sandbox`` the validate/repair loop runs candidates in.
        gate:    the LEARN ``Gate`` that admits (or ESCALATEs/REJECTs) a tool.
    """

    client: LLMClient
    sandbox: Sandbox
    gate: Gate

    def forge(self, query: str, *, max_repairs: int = DEFAULT_MAX_REPAIRS) -> GeneratedTool:
        """Run the full pipeline; return the tool with its gate ``Verdict``.

        Steps: propose (schema->code) -> deterministic validate+repair loop in
        the sandbox -> admit via the gate. The returned tool always carries a
        verdict; only an ACCEPT one is auto-registrable.
        """
        tool = propose_tool(query, client=self.client)
        code = self._validate_and_repair(tool.code, max_repairs=max_repairs)
        if code != tool.code:
            tool = GeneratedTool(
                name=tool.name,
                schema=tool.schema,
                code=code,
                manifest=_manifest(tool.name, tool.schema, code),
                verdict=None,
            )
        verdict = self.gate.run_gate(self._proposal(tool), baseline_score=0.0)
        return GeneratedTool(
            name=tool.name,
            schema=tool.schema,
            code=tool.code,
            manifest=tool.manifest,
            verdict=verdict,
        )

    def _validate_and_repair(self, code: str, *, max_repairs: int) -> str:
        """The load-bearing LOOP: run -> inspect exit_code -> repair, bounded.

        Deterministic control flow. Only the patch generation (when a run
        fails) calls the injected LLM debugger. Stops on the first clean run or
        after ``max_repairs`` debugger attempts — never spins forever.
        """
        for attempt in range(max_repairs + 1):
            result = self.sandbox.run(code, timeout=self.gate.timeout, cwd=self.gate.cwd)
            if result.exit_code == 0:
                return code  # clean run — done
            if attempt == max_repairs:
                break  # repair budget spent — stop without a further debug call
            patched = self._debug(code, result.stderr)
            if not patched:
                return code  # debugger returned nothing — give up to the gate
            code = patched
        return code  # exhausted repairs; the gate will REJECT a still-broken tool

    def _debug(self, code: str, traceback: str) -> str:
        """Ask the injected LLM debugger to patch a failing draft."""
        messages: list[Message] = [
            {"role": "system", "content": _DEBUG_SYSTEM},
            {
                "role": "user",
                "content": f"Code:\n```python\n{code}\n```\nTraceback:\n{traceback[:1500]}",
            },
        ]
        response = self.client.chat(messages)
        return _extract_code(getattr(response, "text", "") or "")

    @staticmethod
    def _proposal(tool: GeneratedTool) -> dict[str, Any]:
        """The gate-shaped dict for a generated tool (code field is scanned)."""
        return {"type": "tool", "name": tool.name, "code": tool.code, "schema": tool.schema}

    def register(self, tool: GeneratedTool, registry: dict[str, GeneratedTool]) -> bool:
        """Auto-register ONLY an ACCEPTed tool; return whether it was admitted.

        ESCALATE/REJECT are intentionally NOT registered — they are returned to
        the caller for human review. Mutates ``registry`` in place by binding
        the tool's name (the only intended side effect of admission).
        """
        if tool.verdict is None or tool.verdict.status is not Outcome.ACCEPT:
            return False
        registry[tool.name] = tool
        return True


if __name__ == "__main__":
    import tempfile

    from agentkit.sandbox.core import SubprocessSandbox
    from agentkit.types import ChatResult

    class _Scripted:
        def __init__(self, *texts: str) -> None:
            self._texts, self.idx = list(texts), 0

        def chat(self, messages, tools=None):  # type: ignore[no-untyped-def]
            text = self._texts[min(self.idx, len(self._texts) - 1)]
            self.idx += 1
            return ChatResult(text=text)

    schema = json.dumps({"name": "add", "description": "add", "parameters": {}})
    clean = f"SCHEMA:\n{schema}\n\nCODE:\n```python\nprint(2 + 3)\n```\n"
    broken = f"SCHEMA:\n{schema}\n\nCODE:\n```python\ndef f(:\n```\n"
    repaired = "```python\nprint(2 + 3)\n```"
    side = f"SCHEMA:\n{schema}\n\nCODE:\n```python\nimport subprocess\n```\n"

    with tempfile.TemporaryDirectory() as d:
        def mk(*texts: str) -> ToolForge:
            return ToolForge(
                client=_Scripted(*texts),
                sandbox=SubprocessSandbox(),
                gate=Gate(sandbox=SubprocessSandbox(), evaluator=lambda p: 0.9, cwd=d),
            )

        # schema-before-code parse.
        t = propose_tool("add two numbers", client=_Scripted(clean))
        assert t.name == "add" and "print" in t.code, t

        # clean read-only -> ACCEPT + registers.
        t = mk(clean).forge("add")
        assert t.verdict.status is Outcome.ACCEPT, t.verdict
        reg: dict[str, GeneratedTool] = {}
        assert mk(clean).register(t, reg) is True and "add" in reg

        # broken -> repaired -> ACCEPT.
        t = mk(broken, repaired).forge("add", max_repairs=3)
        assert t.verdict.status is Outcome.ACCEPT, t.verdict

        # side-effecting -> ESCALATE, not registered.
        t = mk(side).forge("run")
        assert t.verdict.status is Outcome.ESCALATE, t.verdict
        reg2: dict[str, GeneratedTool] = {}
        assert mk(side).register(t, reg2) is False and reg2 == {}

        # loop stops after max_repairs.
        f = mk(broken, broken)
        t = f.forge("add", max_repairs=2)
        assert f.client.idx == 3, f.client.idx  # 1 propose + 2 debug
        assert t.verdict.status is Outcome.REJECT, t.verdict

    print("codegen.core self-check OK")
