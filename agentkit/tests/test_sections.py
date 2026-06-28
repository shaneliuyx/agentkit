"""F2: section split + per-section accept ratchet."""
from agentkit.artifacts.sections import accept_rewrite, split_sections


def test_split_sections_intro_and_headings():
    text = "# Title\nintro\n\n## A\nbody a\n\n## B\nbody b\n"
    secs = split_sections(text)
    assert [h for h, _ in secs] == ["(intro)", "## A", "## B"]
    assert "body a" in dict(secs)["## A"]


def test_accept_rewrite_allows_section_preserving_shrink():
    old = "## A\nlong body aaaa\n\n## B\nlong body bbbb\n"
    new = "## A\nshort\n\n## B\nlong body bbbb extended\n"   # A shrank, both still present
    assert accept_rewrite(old, new)


def test_accept_rewrite_allows_additive():
    old = "## A\nbody a\n"
    new = "## A\nbody a\n\n## C\nnew section\n"
    assert accept_rewrite(old, new)


def test_accept_rewrite_rejects_deleted_section():
    old = "## A\nbody a\n\n## B\nbody b\n"
    new = "## A\nbody a\n"   # B deleted
    assert not accept_rewrite(old, new)


def test_accept_rewrite_rejects_gutted_section():
    old = "## A\nreal content here\n\n## B\nbody b\n"
    new = "## A\n\n## B\nbody b\n"   # A gutted to its heading only
    assert not accept_rewrite(old, new)


def test_accept_rewrite_rejects_blank():
    assert not accept_rewrite("## A\nbody", "   ")
