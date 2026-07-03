import studio.markdown_format as mf
from studio.markdown_format import beautify_markdown


def test_normalizes_messy_markdown():
    messy = "#  Heading\n\n\n* item one\n*  item two\n\n| a | bb |\n|--|--|\n| 1 | 2 |\n"
    out = beautify_markdown(messy)
    assert "# Heading\n" in out          # collapsed heading spacing
    assert "- item one\n- item two" in out  # normalized list markers
    assert "| a   | bb  |" in out         # aligned table columns
    assert "*  item two" not in out


def test_empty_passthrough():
    assert beautify_markdown("") == ""


def test_fails_open_on_formatter_error(monkeypatch):
    """Any mdformat exception → original text returned unchanged, no raise."""
    import mdformat

    def _boom(*a, **k):
        raise ValueError("simulated formatter crash")

    monkeypatch.setattr(mdformat, "text", _boom)
    original = "# Title\n\nsome *body* text\n"
    assert beautify_markdown(original) == original
