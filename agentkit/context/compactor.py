"""agentkit.context.compactor — deterministic, zero-LLM conversation compaction.

A Python port of the pi-vcc (https://github.com/sting8k/pi-vcc) algorithmic
conversation-compaction pattern. The thesis: most of what a long agent
conversation needs to *carry forward* — the goal, the files touched, commits,
open errors, standing preferences, and a skimmable transcript — can be
extracted with regex + heuristics, deterministically and instantly, WITHOUT an
LLM summarization call. That is the deterministic-first performance tier for
context.

Determinism is a hard contract: the SAME input MUST produce the SAME output.
There is therefore NO time, NO randomness, and NO network in this module. Token
counts are a *heuristic estimate* (``len(text) // 4``), not a real tokenizer.

Pipeline:
  1. normalize  — messages → internal Block dataclasses
  2. filter     — drop system + empty blocks
  3. cut        — split at the last `keep` user turns; head is summarized, the
                  kept tail is returned verbatim
  4. sections   — regex/heuristic extraction (Goal/Files/Commits/Outstanding/
                  Preferences); shared with agentkit.memory.extract
  5. transcript — chronological one-line-per-block brief with a rolling window
  6. format     — sections then a blank line then the brief transcript
  7. merge      — combine two CompactResults (sticky vs volatile sections)

Public API: ``Block``, ``CompactResult``, ``compact``, ``merge``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from agentkit.memory import extract as ex
from agentkit.types import Message

# Section keys, in render order.
GOAL = "Session Goal"
FILES = "Files And Changes"
COMMITS = "Commits"
OUTSTANDING = "Outstanding Context"
PREFS = "User Preferences"
_SECTION_ORDER = [GOAL, FILES, COMMITS, OUTSTANDING, PREFS]
# Sticky sections accumulate across merges; volatile ones are replaced.
_STICKY = {GOAL, PREFS}
_VOLATILE = {FILES, OUTSTANDING}

_TEXT_TRUNC = 300
_ARG_TRUNC = 60
DEFAULT_KEEP = 1
DEFAULT_MAX_LINES = 120


def _est_tokens(text: str) -> int:
    """Heuristic token estimate: ~4 chars/token. NOT a real tokenizer — this is
    a cheap proxy used only to report relative reduction, never for billing."""
    return len(text) // 4


# ---------------------------------------------------------------------------
# 1. normalize → Block
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Block:
    """An internal, immutable view of one conversational unit.

    kind ∈ {user, assistant, tool_call, tool_result, system}. A single assistant
    message with tool_calls expands into one assistant text block (if it has
    text) plus one tool_call block per call.
    """
    kind: str
    text: str = ""
    tool: str = ""              # tool name for tool_call / tool_result
    arg: str = ""               # first-arg value preview for tool_call
    index: int = 0              # source message index (stable ordering)


def _normalize(messages: list[Message]) -> list[Block]:
    """Expand OpenAI-style messages into ordered Block units."""
    blocks: list[Block] = []
    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        content = msg.get("content")
        text = content if isinstance(content, str) else ""

        if role == "system":
            blocks.append(Block(kind="system", text=text, index=i))
            continue
        if role == "user":
            blocks.append(Block(kind="user", text=text, index=i))
            continue
        if role == "tool":
            blocks.append(Block(kind="tool_result", text=text,
                                tool=str(msg.get("name", "")), index=i))
            continue
        if role == "assistant":
            if text:
                blocks.append(Block(kind="assistant", text=text, index=i))
            for call in ex._tool_calls(msg):
                name, args = ex._call_name_args(call)
                first_arg = next((str(v) for v in args.values() if v not in (None, "")), "")
                blocks.append(Block(kind="tool_call", tool=name,
                                    arg=first_arg, index=i))
            continue
        # Unknown role: keep its text as a generic user-like block so nothing
        # is silently dropped.
        if text:
            blocks.append(Block(kind="user", text=text, index=i))
    return blocks


# ---------------------------------------------------------------------------
# 2. filter
# ---------------------------------------------------------------------------

def _filter(blocks: list[Block]) -> list[Block]:
    """Drop system blocks and content-empty blocks (tool_call keeps even when
    it has no text, because its tool/arg carry signal)."""
    out: list[Block] = []
    for b in blocks:
        if b.kind == "system":
            continue
        if b.kind == "tool_call":
            out.append(b)
            continue
        if not b.text.strip():
            continue
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# 3. cut at the last `keep` user turns
# ---------------------------------------------------------------------------

def _cut_index(messages: list[Message], keep: int) -> int:
    """Return the message index at which the kept tail begins.

    The tail is the last `keep` user turns plus everything after the earliest of
    them. `keep=0` means compact everything (tail is empty → index == len).
    """
    if keep <= 0:
        return len(messages)
    user_positions = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if len(user_positions) <= keep:
        # Fewer user turns than we keep → everything is tail; nothing to compact.
        return 0
    return user_positions[-keep]


# ---------------------------------------------------------------------------
# 4. build sections
# ---------------------------------------------------------------------------

def _build_goal(head_msgs: list[Message]) -> list[str]:
    """First user message is the goal; later user turns that open with a
    scope-change word are appended as `- [Scope change]` lines."""
    user_msgs = [m for m in head_msgs if m.get("role") == "user"]
    if not user_msgs:
        return []
    lines = [ex._content(user_msgs[0]).strip()]
    for m in user_msgs[1:]:
        text = ex._content(m).strip()
        if text and ex._SCOPE_RE.match(text):
            first_line = text.splitlines()[0][:_TEXT_TRUNC]
            lines.append(f"- [Scope change] {first_line}")
    return [ln for ln in lines if ln]


def _build_sections(head_msgs: list[Message]) -> dict[str, list[str]]:
    """Run all deterministic extractors over the head (to-be-summarized) span.
    Empty sections are omitted."""
    sections: dict[str, list[str]] = {}
    goal = _build_goal(head_msgs)
    if goal:
        sections[GOAL] = goal
    files = ex.extract_files(head_msgs)
    if files:
        sections[FILES] = files
    commits = ex.extract_commits(head_msgs)
    if commits:
        sections[COMMITS] = commits
    outstanding = ex.extract_outstanding(head_msgs)
    if outstanding:
        sections[OUTSTANDING] = outstanding
    prefs = ex.extract_preferences(head_msgs)
    if prefs:
        sections[PREFS] = prefs
    return sections


# ---------------------------------------------------------------------------
# 5. brief transcript
# ---------------------------------------------------------------------------

def _brief_transcript(head_blocks: list[Block], max_lines: int) -> list[str]:
    """Chronological one-line-per-block transcript with a rolling window."""
    lines: list[str] = []
    idx = 0
    for b in head_blocks:
        if b.kind == "user":
            lines.append(f"[user] {b.text.strip()[:_TEXT_TRUNC]}")
        elif b.kind == "assistant":
            lines.append(f"[assistant] {b.text.strip()[:_TEXT_TRUNC]}")
        elif b.kind == "tool_call":
            idx += 1
            arg = b.arg.strip()[:_ARG_TRUNC]
            lines.append(f'* {b.tool} "{arg}" (#{idx})')
        # tool_result is folded/omitted from the brief (signal lives in sections).
    if len(lines) > max_lines:
        omitted = len(lines) - max_lines
        lines = [f"...({omitted} earlier lines omitted)"] + lines[-max_lines:]
    return lines


# ---------------------------------------------------------------------------
# 6. format
# ---------------------------------------------------------------------------

def _format(sections: dict[str, list[str]], transcript: list[str]) -> str:
    """Render sections (bracketed, in canonical order) then a blank line then
    the brief transcript."""
    parts: list[str] = []
    for key in _SECTION_ORDER:
        items = sections.get(key)
        if not items:
            continue
        parts.append(f"[{key}]")
        parts.extend(items)
        parts.append("")  # blank line between sections
    body = "\n".join(parts).rstrip()
    transcript_text = "\n".join(transcript)
    if body and transcript_text:
        return f"{body}\n\n{transcript_text}"
    return body or transcript_text


# ---------------------------------------------------------------------------
# CompactResult + public API
# ---------------------------------------------------------------------------

@dataclass
class CompactResult:
    """The output of a compaction pass."""
    text: str
    sections: dict[str, list[str]]
    kept_tail: list[Message]
    est_tokens_before: int
    est_tokens_after: int


def _messages_text(messages: list[Message]) -> str:
    """Serialize messages for the before-size estimate.

    This must reflect what the messages actually cost in the model's context
    window, so it includes the role label, the content, and the full tool-call
    name + argument JSON (the structured tool_calls payload is real context,
    not free). Using only the bare ``content`` would understate the baseline
    and make the reduction look smaller than it is.
    """
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "")
        parts.append(f"{role}: {ex._content(m)}")
        for call in ex._tool_calls(m):
            name, args = ex._call_name_args(call)
            parts.append(f"tool_call {name} {json.dumps(args)}")
    return "\n".join(parts)


def compact(messages: list[Message], keep: int = DEFAULT_KEEP,
            max_lines: int = DEFAULT_MAX_LINES) -> CompactResult:
    """Compact a conversation into a deterministic summary + brief transcript.

    Args:
        messages:  OpenAI-style message dicts.
        keep:      Number of trailing user turns to return verbatim (the tail).
                   ``keep=0`` compacts everything.
        max_lines: Rolling-window cap on the brief transcript.

    Returns:
        A CompactResult. ``.text`` is deterministic for a given input.
    """
    before_text = _messages_text(messages)
    est_before = _est_tokens(before_text)

    cut = _cut_index(messages, keep)
    head_msgs = messages[:cut]
    kept_tail = messages[cut:]

    head_blocks = _filter(_normalize(head_msgs))
    sections = _build_sections(head_msgs)
    transcript = _brief_transcript(head_blocks, max_lines)
    text = _format(sections, transcript)

    return CompactResult(
        text=text,
        sections=sections,
        kept_tail=kept_tail,
        est_tokens_before=est_before,
        est_tokens_after=_est_tokens(text),
    )


def _dedup_keep_order(items: list[str]) -> list[str]:
    """Stable dedup preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def merge(prev: CompactResult, new: CompactResult,
          max_lines: int = DEFAULT_MAX_LINES) -> CompactResult:
    """Merge two compaction results.

    - Sticky sections (Goal, Preferences) accumulate + dedup.
    - Volatile sections (Files, Outstanding) are replaced by ``new``.
    - Commits accumulate, capped at the last ``MAX_COMMITS``.
    - The transcript rolls: prev + new, trimmed to ``max_lines``.
    """
    merged: dict[str, list[str]] = {}
    for key in _SECTION_ORDER:
        p = prev.sections.get(key, [])
        n = new.sections.get(key, [])
        if key in _STICKY:
            combined = _dedup_keep_order(p + n)
        elif key in _VOLATILE:
            combined = n if n else p
        elif key == COMMITS:
            combined = _dedup_keep_order(p + n)[-ex.MAX_COMMITS:]
        else:
            combined = _dedup_keep_order(p + n)
        if combined:
            merged[key] = combined

    # Roll the transcript: take each result's transcript body (drop sections)
    # and concatenate, then trim to max_lines.
    prev_lines = _transcript_lines(prev.text)
    new_lines = _transcript_lines(new.text)
    rolled = prev_lines + new_lines
    if len(rolled) > max_lines:
        omitted = len(rolled) - max_lines
        rolled = [f"...({omitted} earlier lines omitted)"] + rolled[-max_lines:]

    text = _format(merged, rolled)
    return CompactResult(
        text=text,
        sections=merged,
        kept_tail=new.kept_tail,
        est_tokens_before=prev.est_tokens_before + new.est_tokens_before,
        est_tokens_after=_est_tokens(text),
    )


def _transcript_lines(text: str) -> list[str]:
    """Recover the brief-transcript lines from a rendered CompactResult.text.

    The transcript begins after the last blank line that follows a bracketed
    section. We detect transcript lines by their prefixes ([user], [assistant],
    *, ...) which never collide with section headers ([Section Goal] etc.)."""
    lines = text.splitlines()
    out: list[str] = []
    for ln in lines:
        if ln.startswith("[user]") or ln.startswith("[assistant]") \
                or ln.startswith("* ") or ln.startswith("...("):
            out.append(ln)
    return out


if __name__ == "__main__":
    # Synthetic 50-message coding session.
    def _build_session() -> list[Message]:
        msgs: list[Message] = [
            {"role": "system", "content": "You are a coding agent."},
            {"role": "user",
             "content": "Build a JSON config loader. Always validate inputs."},
        ]
        for i in range(24):
            msgs.append({"role": "assistant", "content": f"Working on step {i}.",
                         "tool_calls": [
                             {"function": {"name": "write_file",
                                           "arguments": f'{{"path": "src/loader_{i}.py"}}'}}]})
            msgs.append({"role": "tool", "name": "shell",
                         "content": (f"git commit -m \"step {i}\"\n[main a1b2c3{i:x}] step {i}"
                                     if i % 4 == 0 else "ok")})
        msgs.append({"role": "tool", "name": "pytest",
                     "content": "Traceback: KeyError 'timeout' in loader.py"})
        msgs.append({"role": "user", "content": "Actually, prefer tomllib over json."})
        msgs.append({"role": "assistant", "content": "Switching to tomllib now."})
        return msgs

    session = _build_session()
    assert len(session) >= 50, len(session)

    result = compact(session, keep=1)

    # Token reduction.
    assert result.est_tokens_after < result.est_tokens_before, (
        result.est_tokens_after, result.est_tokens_before)

    # Determinism: compact twice → identical text.
    assert compact(session, keep=1).text == result.text

    # Known goal / preference / file appear.
    assert GOAL in result.sections and "config loader" in result.sections[GOAL][0]
    assert PREFS in result.sections
    assert any("validate" in p for p in result.sections[PREFS])
    assert FILES in result.sections
    assert any("loader_0.py" in f for f in result.sections[FILES])
    assert COMMITS in result.sections
    assert OUTSTANDING in result.sections

    # keep=0 compacts everything (empty tail).
    assert compact(session, keep=0).kept_tail == []
    # keep=1 keeps the last user turn onward.
    assert len(result.kept_tail) >= 1
    assert result.kept_tail[0]["role"] == "user"

    # merge: sticky accumulates, volatile replaces.
    r2 = compact(session, keep=1)
    m = merge(result, r2)
    assert GOAL in m.sections
    print(f"compactor self-check OK "
          f"(before={result.est_tokens_before} after={result.est_tokens_after} "
          f"reduction={100 * (1 - result.est_tokens_after / max(1, result.est_tokens_before)):.0f}%)")
