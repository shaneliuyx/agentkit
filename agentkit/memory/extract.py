"""agentkit.memory.extract — the cheap deterministic memory tier.

Pure functions that pull structured facts out of a conversation WITHOUT
embeddings or an LLM. This is the *deterministic-first* tier of the memory
system: before paying for an embedding call (memory/store.py) or an LLM
summarization pass, extract what regex/heuristics can extract for free.

Same input MUST give the same output — there is no time, no randomness, no
network. The compactor (agentkit.context.compactor) imports these helpers so
the two share one definition of "what is a file op / a commit / a preference".

A "message" here is an OpenAI-style dict:
    {"role": "user"|"assistant"|"tool"|"system",
     "content": str,
     "tool_calls": [{"function": {"name": str, "arguments": str|dict}}, ...],  # optional
     "name": str}                                                              # optional (tool)
"""

from __future__ import annotations

import json
import re
from typing import Any

from agentkit.types import Message

# ── shared regex / keyword vocab ────────────────────────────────────────────
# Preference markers: lines/sentences expressing a standing instruction.
_PREF_RE = re.compile(r"\b(always|never|prefer|don't|do not)\b", re.IGNORECASE)
# Scope-change openers: a later user turn that redirects the task.
_SCOPE_RE = re.compile(r"^\s*(also|actually|instead|now|wait|change)\b", re.IGNORECASE)
# Error markers in tool results / assistant text.
_ERROR_RE = re.compile(
    r"\b(Error|Traceback|failed|FAILED|still failing|exception)\b"
)
# A git commit hash: 7–40 hex chars.
_HASH_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
# Keys in tool-call args that denote a file path.
_PATH_KEYS = ("path", "file_path", "file", "filename", "filepath")
# Tool-name substrings that denote a file operation.
_CREATE_NAMES = ("write", "create")
_MODIFY_NAMES = ("edit",)
_FILE_NAMES = _CREATE_NAMES + _MODIFY_NAMES + ("file",)

# Caps (documented limits — keep extraction bounded).
MAX_FILES = 30
MAX_COMMITS = 8
MAX_OUTSTANDING = 5
MAX_PREFS = 15
_OUTSTANDING_TRUNC = 200


# ── low-level access helpers ────────────────────────────────────────────────
def _content(msg: Message) -> str:
    """Return a message's text content as a string (never None)."""
    c = msg.get("content")
    return c if isinstance(c, str) else ""


def _tool_calls(msg: Message) -> list[dict[str, Any]]:
    """Return a message's tool_calls list (empty if absent/malformed)."""
    tc = msg.get("tool_calls")
    return tc if isinstance(tc, list) else []


def _call_name_args(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Normalize one tool-call dict to (name, args_dict).

    Accepts both the OpenAI nested shape ({"function": {"name", "arguments"}})
    and a flat shape ({"name", "arguments"}). Arguments may be a JSON string.
    """
    maybe_fn = call.get("function")
    fn = maybe_fn if isinstance(maybe_fn, dict) else call
    name = fn.get("name", "")
    raw = fn.get("arguments", fn.get("args", {}))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raw = {}
    args = raw if isinstance(raw, dict) else {}
    return (name if isinstance(name, str) else ""), args


def _first_path(args: dict[str, Any]) -> str | None:
    """Return the first file-path-like value among known path keys."""
    for key in _PATH_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _trim_common_prefix(paths: list[str]) -> list[str]:
    """Strip the longest shared directory prefix so paths read cleanly."""
    if len(paths) < 2:
        return paths
    import os.path

    prefix = os.path.commonpath([p for p in paths]) if all("/" in p for p in paths) else ""
    if not prefix:
        return paths
    out = []
    for p in paths:
        trimmed = p[len(prefix):].lstrip("/")
        out.append(trimmed or p)
    return out


# ── public extractors ───────────────────────────────────────────────────────
def extract_files(messages: list[Message]) -> list[str]:
    """Extract file create/modify operations from assistant tool_calls.

    A tool call counts as a file op when the tool name contains write/edit/
    create/file OR the args carry a path-like key. ``create``/``write`` →
    "Created:", ``edit`` → "Modified:". Deduped (first-seen order), capped at
    ``MAX_FILES``, with a common directory prefix trimmed for readability.
    """
    raw: list[tuple[str, str]] = []  # (verb, path) before dedup
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for call in _tool_calls(msg):
            name, args = _call_name_args(call)
            lname = name.lower()
            path = _first_path(args)
            is_file_op = any(s in lname for s in _FILE_NAMES) or path is not None
            if not is_file_op or path is None:
                continue
            if any(s in lname for s in _MODIFY_NAMES):
                verb = "Modified"
            else:
                verb = "Created"
            raw.append((verb, path))

    # Trim a shared prefix across the raw paths, then format + dedup.
    trimmed = _trim_common_prefix([p for _, p in raw])
    seen: set[str] = set()
    out: list[str] = []
    for (verb, _), shown in zip(raw, trimmed):
        line = f"{verb}: {shown}"
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= MAX_FILES:
            break
    return out


def extract_commits(messages: list[Message]) -> list[str]:
    """Extract git commits referenced in tool-call args or tool-result content.

    Looks for ``git commit`` mentions and a 7–40 hex hash; when a hash is
    present it is paired with the first message line found nearby. Keeps the
    last ``MAX_COMMITS`` (most recent), deduped.
    """
    found: list[str] = []
    for msg in messages:
        texts: list[str] = []
        role = msg.get("role")
        if role == "assistant":
            for call in _tool_calls(msg):
                _, args = _call_name_args(call)
                texts.append(json.dumps(args))
        if role in ("tool", "assistant"):
            texts.append(_content(msg))

        for text in texts:
            # Require an explicit "git commit" mention to treat this as a commit.
            if "git commit" not in text:
                continue
            # Pull a hash if present.
            m = _HASH_RE.search(text)
            hash_part = m.group(1)[:10] if m else ""
            # Pull a first message line: look for -m "..." or the first line.
            msg_match = re.search(r'-m\s+["\']([^"\']+)["\']', text)
            msg_part = msg_match.group(1).strip() if msg_match else ""
            if not msg_part:
                # First non-empty line of the text as a weak fallback.
                for ln in text.splitlines():
                    ln = ln.strip()
                    if ln and "commit" not in ln.lower():
                        msg_part = ln[:80]
                        break
            entry = " ".join(p for p in (hash_part, msg_part) if p).strip()
            if entry and entry not in found:
                found.append(entry)
    return found[-MAX_COMMITS:]


def extract_preferences(messages: list[Message]) -> list[str]:
    """Extract standing user preferences (always/never/prefer/don't/do not).

    Scans user messages line-by-line; keeps lines that match a preference
    marker. Unique (first-seen), capped at ``MAX_PREFS``.
    """
    seen: set[str] = set()
    out: list[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        for line in _content(msg).splitlines():
            # Split each line into sentences so we keep only the preference
            # sentence, not a whole goal line that merely contains "always".
            for sentence in re.split(r"(?<=[.!?])\s+", line):
                sentence = sentence.strip()
                if not sentence or not _PREF_RE.search(sentence):
                    continue
                if sentence in seen:
                    continue
                seen.add(sentence)
                out.append(sentence)
                if len(out) >= MAX_PREFS:
                    return out
    return out


def extract_outstanding(messages: list[Message]) -> list[str]:
    """Extract recent error/blocker context from tool results + assistant text.

    Keeps the last ``MAX_OUTSTANDING`` error-bearing snippets, each truncated to
    200 chars. Not part of the (files/commits/prefs) trio but shares the same
    deterministic heuristics, so it lives here too.
    """
    found: list[str] = []
    for msg in messages:
        if msg.get("role") not in ("tool", "assistant"):
            continue
        text = _content(msg)
        if not text or not _ERROR_RE.search(text):
            continue
        snippet = text.strip().replace("\n", " ")[:_OUTSTANDING_TRUNC]
        if snippet not in found:
            found.append(snippet)
    return found[-MAX_OUTSTANDING:]


if __name__ == "__main__":
    convo: list[Message] = [
        {"role": "user", "content": "Build a parser. Always use type hints."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "write_file", "arguments": '{"path": "src/parse.py"}'}},
        ]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "edit_file", "arguments": {"file_path": "src/parse.py"}}},
        ]},
        {"role": "tool", "name": "shell",
         "content": "git commit -m \"add parser\"\n[main a1b2c3d] add parser"},
        {"role": "tool", "name": "pytest", "content": "Traceback: ValueError in parse()"},
        {"role": "user", "content": "Actually, prefer dataclasses here."},
    ]

    files = extract_files(convo)
    assert any("Created" in f and "parse.py" in f for f in files), files
    assert any("Modified" in f for f in files), files

    commits = extract_commits(convo)
    assert any("add parser" in c for c in commits), commits

    prefs = extract_preferences(convo)
    assert any("type hints" in p for p in prefs), prefs
    assert any("dataclasses" in p for p in prefs), prefs

    outstanding = extract_outstanding(convo)
    assert any("Traceback" in o for o in outstanding), outstanding

    # Determinism: same input → same output.
    assert extract_files(convo) == files
    assert extract_preferences(convo) == prefs
    print("extract self-check OK")
