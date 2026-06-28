"""Report-template store: skeleton extraction + semantic save/find."""
from studio.templates import TemplateStore, extract_skeleton


class _Emb:
    """Cluster embedder: 'loop' requirements vs everything else."""

    def embed(self, texts):
        return [[1.0, 0.0] if "loop" in t.lower() else [0.0, 1.0] for t in texts]


def test_extract_skeleton_keeps_headings_drops_bodies_and_fences():
    md = ("# Title\n\nintro prose\n\n## A\nbody a\n\n```bash\n# PLAN.md not a heading\n```\n\n"
          "### sub\nx\n\n## B\nbody b\n")
    sk = extract_skeleton(md)
    for h in ("# Title", "## A", "### sub", "## B"):
        assert h in sk
    assert "body a" not in sk and "intro prose" not in sk
    assert "# PLAN.md not a heading" not in sk          # '#' inside a code fence is skipped
    assert "_(pending — needs sourced content)_" in sk


def test_template_save_find_semantic(tmp_path):
    s = TemplateStore(db_path=tmp_path / "t.db", embedder=_Emb())
    assert s.save_template("research loop engineering articles",
                           "# R\n## Sources\n_(pending)_\n") is True
    # a semantically-similar requirement finds the template
    assert s.find_template("find popular loop engineer posts") is not None
    # a dissimilar requirement does NOT (below threshold)
    assert s.find_template("climate change impact report") is None


def test_template_save_dedups_identical_skeleton(tmp_path):
    s = TemplateStore(db_path=tmp_path / "t.db", embedder=_Emb())
    sk = "# R\n## A\n_(pending)_\n"
    assert s.save_template("q1", sk) is True
    assert s.save_template("q2", sk) is False    # identical skeleton → not inserted again


def test_find_template_none_without_embedder(tmp_path):
    s = TemplateStore(db_path=tmp_path / "t.db", embedder=None)
    s.save_template("anything", "# R\n## A\n_(pending)_\n")   # stored, but not searchable
    assert s.find_template("anything") is None
