"""L6 offline probe for the P-ledger cited-in-artifact join.

Reads a SAVED research_first workspace and asserts the ASSEMBLE-side dimension
(``cited_in_artifact``) that the new ``_coverage_cited`` join computes, using the
SAME function production calls — proving the join before any live run.
``queries_issued`` / ``sources_fetched`` are live-only (the ledger is not
persisted in a saved ws), so the probe targets the one dimension that is both
reconstructable offline AND genuinely new/error-prone.

    usage: python -m tools.probe_p_ledger tmp/studio-workspaces/s_0adf2d8a453c
"""

import json
import sys
from pathlib import Path

from studio.research_first import _body_cited_urls, _coverage_cited


def main(ws_arg: str) -> None:
    ws = Path(ws_arg)
    claims = [json.loads(ln) for ln in (ws / "claims.jsonl").read_text().splitlines() if ln.strip()]
    text = (ws / "result.md").read_text()
    cited = _coverage_cited(claims, text)

    subjects = {s for c in claims for s in (c.get("subjects") or [])}
    assert cited, "join returned nothing — cited-URL/subject wiring is broken"
    assert set(cited) <= subjects | {"__joint__"}, "cited a subject not present in claims"

    cited_urls = _body_cited_urls({c["url"] for c in claims if c.get("url")}, text)
    for s, n in cited.items():
        if s == "__joint__":
            continue
        backing = [
            c["url"]
            for c in claims
            if s in (c.get("subjects") or []) and c["url"] in cited_urls
        ]
        assert len(set(backing)) == n, f"{s}: counted {n} but {len(set(backing))} URLs back it"
    print("PASS", ws.name, cited)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m tools.probe_p_ledger <workspace_dir>", file=sys.stderr)
        raise SystemExit(2)
    main(sys.argv[1])
