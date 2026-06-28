"""F1: semantic finding dedup."""
from agentkit.artifacts.dedup import dedupe_findings
from agentkit.artifacts.types import Finding


class _FakeEmb:
    """Cluster embedder: findings sharing a keyword get identical vectors (= duplicates)."""

    def embed(self, texts):
        out = []
        for t in texts:
            tl = t.lower()
            if "ralph" in tl:
                out.append([1.0, 0.0, 0.0])
            elif "verifier" in tl:
                out.append([0.0, 1.0, 0.0])
            else:
                out.append([0.0, 0.0, 1.0])
        return out


def _f(url, quote="", why="", verified=False):
    return Finding(url=url, quote=quote, why=why, quote_verified=verified)


def test_dedupe_collapses_semantic_near_duplicates():
    findings = [
        _f("https://a", quote="Ralph is a bash loop technique"),
        _f("https://b", quote="Ralph, in its purest form, is a bash loop"),  # near-dup
        _f("https://c", quote="The verifier is the bottleneck"),
    ]
    kept, dropped = dedupe_findings(findings, _FakeEmb(), threshold=0.85)
    assert dropped == 1
    assert len(kept) == 2  # the two ralph findings collapse; verifier stays


def test_dedupe_keeps_verified_quote_as_center():
    findings = [
        _f("https://a", quote="Ralph loop", verified=False),
        _f("https://b", quote="Ralph loop technique", verified=True),  # stronger
    ]
    kept, dropped = dedupe_findings(findings, _FakeEmb())
    assert dropped == 1 and len(kept) == 1
    assert kept[0].quote_verified  # the stronger (verified) finding is kept as the center


def test_dedupe_lexical_fallback_no_embedder():
    findings = [
        _f("https://a", quote="same text"),
        _f("https://a2", quote="same text"),   # exact-key duplicate
        _f("https://b", quote="other text"),
    ]
    kept, dropped = dedupe_findings(findings, None)
    assert dropped == 1 and len(kept) == 2


def test_dedupe_empty():
    assert dedupe_findings([], _FakeEmb()) == ([], 0)
