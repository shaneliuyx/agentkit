"""F4b — honest ranking synthesizer."""
from agentkit.artifacts.metrics import Metric
from agentkit.artifacts.ranking import parse_stated, synthesize_ranking_table
from agentkit.artifacts.types import Finding


def test_parse_stated():
    assert parse_stated("6.5M views") == (6_500_000, "views")
    assert parse_stated("1,200 stars") == (1200, "stars")
    assert parse_stated("n/a") is None
    assert parse_stated("") is None


def test_ranking_split_measured_vs_reported_no_fabrication():
    findings = [
        Finding(url="https://arxiv.org/abs/2210.03629", title="ReAct"),
        Finding(url="https://tosea.ai/x", title="Tosea", popularity="6.5M views"),
        Finding(url="https://blog.example/a", title="Blog A"),
        Finding(url="https://blog.example/a", title="Blog A"),   # cited twice → salience 2
    ]
    metrics = {"https://arxiv.org/abs/2210.03629": Metric(7094, "citations", "semantic-scholar")}
    t = synthesize_ranking_table(findings, metrics)
    # methodology note: 1 of 3 sources measurable
    assert "1 of 3 cited sources have an independently verifiable metric" in t
    # Measured table ranks ONLY the source with a real metric
    assert "**Measured popularity**" in t and "7,094 citations" in t and "ReAct" in t
    # Reported/unranked holds the stated claim + the no-metric blog, honestly labelled
    assert "**Reported / unranked**" in t
    assert "6,500,000 views" in t and "reported, not independently verified" in t
    assert "Blog A" in t and "no public engagement metric" in t   # NO invented number
    # the stated/blog sources are NOT mixed into the ranked Measured table
    measured_block = t.split("**Reported")[0]
    assert "Blog A" not in measured_block and "6,500,000" not in measured_block
