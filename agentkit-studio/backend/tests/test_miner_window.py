"""Moving-window miner (DESIGN §11.6): the miner must sweep the WHOLE document, not just
head+tail, so a section in the MIDDLE (e.g. Methodology/Conclusion at char ~55K of a 64K
report) is seen and not falsely reported missing. Guards the fix for the V36 false
"Required section 'Methodology' is missing" weakness on a report that contained it.
"""
from studio.task_runs import mine_weaknesses_from_outputs


class _RecordingClient:
    """Captures each prompt the miner sends and replies with an empty weakness list."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def chat(self, msgs):  # noqa: ANN001 - test double
        self.prompts.append(msgs[0]["content"])

        class _R:
            text = "[]"

        return _R()


def test_moving_window_covers_the_middle_of_a_long_document() -> None:
    # A long doc whose marker section sits in the OLD blind spot (past the 8K head,
    # before the 4K tail). Head+tail never saw it; the moving window must.
    body = "\n\n".join(f"## Section {i}\n" + ("filler " * 200) for i in range(40))
    mid = body.find("## Section 30")
    assert 12_000 < mid < len(body) - 12_000, mid  # genuinely mid-document

    client = _RecordingClient()
    mine_weaknesses_from_outputs({"reducer_response": body[:400]}, body, "task", client)

    assert len(client.prompts) > 1, "long doc must be swept in multiple windows"
    assert any("## Section 30" in p for p in client.prompts), "middle section never seen"


def test_short_document_is_a_single_window() -> None:
    client = _RecordingClient()
    mine_weaknesses_from_outputs({"r": "x"}, "a short report", "task", client)
    assert len(client.prompts) == 1  # no extra LLM cost for small reports


def test_weaknesses_dedupe_across_overlapping_windows() -> None:
    # Every window emits the SAME weakness; the union must collapse it to one.
    body = ("## A\n" + "z " * 6000) * 2  # exceeds one window → multiple calls

    class _DupClient:
        def chat(self, msgs):  # noqa: ANN001
            class _R:
                text = '["[document] No conclusion section"]'

            return _R()

    out = mine_weaknesses_from_outputs({"r": body[:400]}, body, "task", _DupClient())
    assert out == ["[document] No conclusion section"], out
