from agentkit.artifacts.types import Finding

from studio.evidence import evidence_from_findings, render_evidence_matrix, source_type_for_url


def test_evidence_from_findings_keeps_only_final_citations() -> None:
    findings = [
        Finding(
            url="https://example.com/a",
            title="A",
            quote="quoted words",
            why="supports the claim",
            patch_target="## Findings",
            quote_verified=True,
            grounded=True,
        ),
        Finding(
            url="https://example.com/dropped",
            title="Dropped",
            why="not used",
            grounded=True,
        ),
    ]

    rows = evidence_from_findings(findings, "Final cites https://example.com/a.")

    assert len(rows) == 1
    assert rows[0].status == "verified"
    assert rows[0].used_in == "## Findings"
    assert rows[0].url == "https://example.com/a"
    matrix = render_evidence_matrix(rows)
    assert "supports the claim" in matrix
    assert "https://example.com/a" in matrix


def test_source_type_inference_covers_primary_and_weak_sources() -> None:
    assert source_type_for_url("https://docs.python.org/3/library/ast.html") == "official_docs"
    assert source_type_for_url("https://www.nist.gov/example") == "standard"
    assert source_type_for_url("https://pubmed.ncbi.nlm.nih.gov/123") == "preprint"
    assert source_type_for_url("https://medium.com/example/post") == "blog"
    assert source_type_for_url("https://reddit.com/r/example") == "forum_social"


def test_major_claim_from_single_non_primary_source_is_weak() -> None:
    findings = [Finding(
        url="https://single-source.example/post",
        title="Blog",
        quote="A single blog says the market doubled.",
        why="market doubled",
        patch_target="## Key Findings",
        grounded=True,
    )]

    rows = evidence_from_findings(findings, "Cites https://single-source.example/post.")

    assert rows[0].source_type == "web"
    assert rows[0].status == "weak"
    assert "corroborate" in rows[0].notes


def test_major_claim_with_independent_hosts_is_corroborated() -> None:
    findings = [
        Finding(
            url="https://source-a.example/report",
            title="A",
            why="market doubled",
            patch_target="## Key Findings",
            grounded=True,
        ),
        Finding(
            url="https://source-b.example/report",
            title="B",
            why="market doubled",
            patch_target="## Key Findings",
            grounded=True,
        ),
    ]
    final = "Cites https://source-a.example/report and https://source-b.example/report."

    rows = evidence_from_findings(findings, final)

    assert [r.status for r in rows] == ["partially_supported", "partially_supported"]
    assert all("corroborated" in r.notes for r in rows)
