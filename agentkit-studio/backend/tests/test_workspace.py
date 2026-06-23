"""Tests for the per-session file jail (workspace.py) — the containment proof.

The important assertions: a path-escape (``../etc/passwd``, an absolute path, a
symlink out of the jail) is REJECTED for both read and write, and the file
outside the workspace is left untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentkit.sandbox.core import MAX_OUTPUT_BYTES
from studio.workspace import Workspace, WorkspaceError


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace("sess-1", root=tmp_path / "workspaces")


def test_write_then_read_inside_workspace(ws: Workspace) -> None:
    n, shown = ws.write("notes/todo.txt", "hello world")
    assert n == 11 and shown == "notes/todo.txt"
    text, read_bytes = ws.read("notes/todo.txt")
    assert text == "hello world" and read_bytes == 11


def test_write_creates_parent_dirs_inside_jail(ws: Workspace) -> None:
    ws.write("a/b/c/deep.txt", "x")
    assert (ws.root / "a" / "b" / "c" / "deep.txt").is_file()


def test_read_relative_escape_rejected(ws: Workspace) -> None:
    with pytest.raises(WorkspaceError):
        ws.read("../../../../etc/passwd")


def test_write_relative_escape_rejected_and_no_file_created(
    ws: Workspace, tmp_path: Path
) -> None:
    """A ../ escape is rejected AND nothing is written outside the workspace."""
    target_outside = tmp_path / "pwned.txt"
    assert not target_outside.exists()
    with pytest.raises(WorkspaceError):
        ws.write(f"../../{target_outside.name}", "owned")
    # The escaping write touched nothing on disk outside the jail.
    assert not target_outside.exists()
    assert not (ws.root.parent / "pwned.txt").exists()


def test_absolute_path_escape_rejected(ws: Workspace, tmp_path: Path) -> None:
    """An absolute path is treated as workspace-relative and contained, OR
    rejected — never written to the absolute location outside the jail."""
    outside = tmp_path / "abs_pwned.txt"
    with pytest.raises(WorkspaceError):
        ws.write(str(outside), "owned")
    assert not outside.exists()
    with pytest.raises(WorkspaceError):
        ws.read("/etc/hosts")


def test_symlink_escape_rejected(ws: Workspace, tmp_path: Path) -> None:
    """A symlink inside the workspace pointing outside is rejected on read
    (realpath follows the link before the containment check)."""
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    link = ws.root / "link_out"
    link.symlink_to(secret)
    with pytest.raises(WorkspaceError):
        ws.read("link_out")


def test_read_missing_file_errors(ws: Workspace) -> None:
    with pytest.raises(WorkspaceError):
        ws.read("does_not_exist.txt")


def test_read_caps_oversized_file(ws: Workspace) -> None:
    """A file larger than MAX_OUTPUT_BYTES is truncated on read."""
    big = "A" * (MAX_OUTPUT_BYTES + 5000)
    ws.write("big.txt", big)
    text, n = ws.read("big.txt")
    assert n == MAX_OUTPUT_BYTES
    assert len(text.encode("utf-8")) == MAX_OUTPUT_BYTES


def test_empty_path_rejected(ws: Workspace) -> None:
    with pytest.raises(WorkspaceError):
        ws.read("")
    with pytest.raises(WorkspaceError):
        ws.write("   ", "x")
