"""Tests for agentkit.tools.fs — root-jailed file tools (deterministic, no network).

Security is the design point, so the tests are adversarial: a relative ``..``
path and an absolute path are refused for BOTH read and write, a file outside
the root is never created or read, oversize reads are capped, and the
tool-handler surface returns escapes as structured data rather than crashing.
The happy path (read/write within root, parent-dir creation) is covered too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentkit.tools.fs import (
    DEFAULT_MAX_BYTES,
    FileToolError,
    make_fs_tools,
    read_file,
    write_file,
)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_write_then_read_within_root(tmp_path: Path):
    n = write_file("hello.txt", "hi there", root=tmp_path)
    assert n == len("hi there".encode("utf-8"))
    assert read_file("hello.txt", root=tmp_path) == "hi there"


@pytest.mark.unit
def test_write_creates_parent_dirs_inside_root(tmp_path: Path):
    write_file("a/b/c/deep.txt", "nested", root=tmp_path)
    assert (tmp_path / "a" / "b" / "c" / "deep.txt").is_file()
    assert read_file("a/b/c/deep.txt", root=tmp_path) == "nested"


# ---------------------------------------------------------------------------
# Escape rejection — the key security test.
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "../../outside.txt"])
def test_read_rejects_escape(tmp_path: Path, bad: str):
    with pytest.raises(FileToolError):
        read_file(bad, root=tmp_path)


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "../../outside.txt"])
def test_write_rejects_escape(tmp_path: Path, bad: str):
    with pytest.raises(FileToolError):
        write_file(bad, "payload", root=tmp_path)


@pytest.mark.unit
def test_escaping_write_never_creates_file_outside_root(tmp_path: Path):
    # A sibling dir outside root; the rejected write must not touch it.
    outside = tmp_path.parent / "should_not_exist.txt"
    assert not outside.exists()
    with pytest.raises(FileToolError):
        write_file("../should_not_exist.txt", "x", root=tmp_path)
    assert not outside.exists()


@pytest.mark.unit
def test_read_outside_root_never_reads_real_file(tmp_path: Path):
    # A real file lives just outside root; the jailed read must refuse it,
    # rather than returning its contents.
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("TOP SECRET")
    try:
        with pytest.raises(FileToolError):
            read_file("../secret.txt", root=tmp_path)
        with pytest.raises(FileToolError):
            read_file(str(secret), root=tmp_path)
    finally:
        secret.unlink()


@pytest.mark.unit
def test_symlink_escape_is_refused(tmp_path: Path):
    # A symlink inside root pointing outside must resolve outside and be refused.
    outside_dir = tmp_path.parent / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "target.txt").write_text("leak")
    link = tmp_path / "link"
    try:
        link.symlink_to(outside_dir)
        with pytest.raises(FileToolError):
            read_file("link/target.txt", root=tmp_path)
    finally:
        link.unlink(missing_ok=True)
        (outside_dir / "target.txt").unlink()
        outside_dir.rmdir()


# ---------------------------------------------------------------------------
# Oversize cap + missing file
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_oversize_read_is_capped(tmp_path: Path):
    write_file("big.txt", "A" * 100, root=tmp_path)
    with pytest.raises(FileToolError):
        read_file("big.txt", root=tmp_path, max_bytes=10)
    # Under the cap it reads fine.
    assert read_file("big.txt", root=tmp_path, max_bytes=1000) == "A" * 100


@pytest.mark.unit
def test_read_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileToolError):
        read_file("nope.txt", root=tmp_path)


@pytest.mark.unit
def test_default_max_bytes_is_sane():
    assert DEFAULT_MAX_BYTES >= 1024


# ---------------------------------------------------------------------------
# Tool-handler surface — wire-compatible with run_agent(tools=...).
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_make_fs_tools_returns_dict_registry(tmp_path: Path):
    tools = make_fs_tools(tmp_path)
    assert set(tools) == {"read_file", "write_file"}
    assert all(callable(fn) for fn in tools.values())


@pytest.mark.unit
def test_handlers_happy_path(tmp_path: Path):
    tools = make_fs_tools(tmp_path)
    assert tools["write_file"]({"path": "x.txt", "content": "data"}) == {
        "bytes_written": 4
    }
    out = tools["read_file"]({"path": "x.txt"})
    assert out["content"] == "data"
    assert out["bytes"] == 4


@pytest.mark.unit
def test_handlers_return_escape_as_data_not_exception(tmp_path: Path):
    tools = make_fs_tools(tmp_path)
    assert "error" in tools["read_file"]({"path": "../escape.txt"})
    assert "error" in tools["write_file"]({"path": "/etc/passwd", "content": "x"})


@pytest.mark.unit
def test_make_fs_tools_root_is_isolated(tmp_path: Path):
    # Each registry is bound to its own root — no global/shared state.
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    tools_a = make_fs_tools(root_a)
    tools_a["write_file"]({"path": "only_in_a.txt", "content": "z"})
    tools_b = make_fs_tools(root_b)
    # b's registry cannot see a's file.
    assert "error" in tools_b["read_file"]({"path": "only_in_a.txt"})
