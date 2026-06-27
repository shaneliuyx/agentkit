"""Tests for agentkit.artifacts.patcher."""

from pathlib import Path

from agentkit.artifacts.patcher import (
    DocPatch,
    cleanup_orphaned_tmp,
    reduce_patches,
    write_artifact,
)


def test_append_op():
    result = reduce_patches("hello", [[DocPatch(op="append", anchor=None, content=" world")]])
    assert result.text == "hello\n world"


def test_prepend_op():
    result = reduce_patches("world", [[DocPatch(op="prepend", anchor=None, content="hello\n")]])
    assert result.text.startswith("hello")


def test_replace_op():
    result = reduce_patches("foo bar", [[DocPatch(op="replace", anchor="foo", content="baz")]])
    assert result.text == "baz bar"


def test_delete_op():
    result = reduce_patches("foo bar", [[DocPatch(op="delete", anchor=" bar", content="")]])
    assert result.text == "foo"


def test_insert_after_op():
    result = reduce_patches(
        "line1\nline2",
        [[DocPatch(op="insert_after", anchor="line1", content="\nINSERTED")]],
    )
    assert "INSERTED" in result.text
    assert result.text.index("line1") < result.text.index("INSERTED")


def test_insert_before_op():
    result = reduce_patches(
        "line1\nline2",
        [[DocPatch(op="insert_before", anchor="line2", content="BEFORE\n")]],
    )
    assert "BEFORE" in result.text
    assert result.text.index("BEFORE") < result.text.index("line2")


def test_anchor_missing_produces_conflict():
    result = reduce_patches(
        "hello world",
        [[DocPatch(op="replace", anchor="NOTFOUND", content="x")]],
    )
    assert len(result.conflicts) == 1
    assert result.conflicts[0].reason == "anchor_destroyed"


def test_duplicate_insert_skipped():
    text = "hello EXISTING world"
    result = reduce_patches(
        text,
        [[DocPatch(op="insert_after", anchor="hello", content=" EXISTING")]],
    )
    assert len(result.conflicts) == 1
    assert result.conflicts[0].reason == "duplicate"


def test_multi_worker_patches():
    result = reduce_patches(
        "A B C",
        [
            [DocPatch(op="replace", anchor="A", content="X")],
            [DocPatch(op="replace", anchor="B", content="Y")],
        ],
    )
    assert "X" in result.text
    assert "Y" in result.text


def test_empty_patch_groups():
    result = reduce_patches("unchanged", [])
    assert result.text == "unchanged"
    assert result.conflicts == []


def test_refine_fn_output_replaces_merged_text():
    """Phase 2: when llm_refine_fn returns polished text, it becomes the result."""
    called = {}

    def refine(text: str) -> str:
        called["input"] = text
        return "POLISHED: " + text

    result = reduce_patches(
        "foo bar",
        [[DocPatch(op="replace", anchor="foo", content="baz")]],
        llm_refine_fn=refine,
    )
    assert called["input"] == "baz bar"  # refine sees the Phase-1 merged text
    assert result.text == "POLISHED: baz bar"


def test_refine_fn_exception_falls_back_to_merged():
    """A flaky refine LLM must never corrupt a clean structural merge."""
    def refine(text: str) -> str:
        raise RuntimeError("LLM down")

    result = reduce_patches(
        "foo bar",
        [[DocPatch(op="replace", anchor="foo", content="baz")]],
        llm_refine_fn=refine,
    )
    assert result.text == "baz bar"  # merged text preserved


def test_refine_fn_empty_output_falls_back_to_merged():
    """An empty/whitespace refine result is ignored — keep the merge."""
    result = reduce_patches(
        "foo bar",
        [[DocPatch(op="replace", anchor="foo", content="baz")]],
        llm_refine_fn=lambda _t: "   ",
    )
    assert result.text == "baz bar"


def test_refine_fn_none_is_noop():
    """Default (no refine_fn) leaves Phase-1 behaviour identical."""
    result = reduce_patches(
        "foo bar",
        [[DocPatch(op="replace", anchor="foo", content="baz")]],
    )
    assert result.text == "baz bar"


def test_write_artifact_atomic(tmp_path: Path):
    dest = tmp_path / "artifact.md"
    write_artifact(dest, "hello world")
    assert dest.exists()
    assert dest.read_text() == "hello world"
    assert not (tmp_path / "artifact.tmp").exists()


def test_write_artifact_creates_parents(tmp_path: Path):
    dest = tmp_path / "sub" / "dir" / "artifact.md"
    write_artifact(dest, "content")
    assert dest.exists()


def test_cleanup_removes_tmp_files(tmp_path: Path):
    orphan = tmp_path / "artifact.tmp"
    orphan.write_text("leftover")
    cleanup_orphaned_tmp(tmp_path)
    assert not orphan.exists()


def test_cleanup_leaves_md_files(tmp_path: Path):
    keeper = tmp_path / "artifact.md"
    keeper.write_text("keep me")
    cleanup_orphaned_tmp(tmp_path)
    assert keeper.exists()


def test_cleanup_recursive(tmp_path: Path):
    sub = tmp_path / "session123"
    sub.mkdir()
    orphan = sub / "artifact.tmp"
    orphan.write_text("crash remnant")
    cleanup_orphaned_tmp(tmp_path)
    assert not orphan.exists()
