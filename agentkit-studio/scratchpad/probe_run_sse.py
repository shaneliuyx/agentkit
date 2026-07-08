"""SSE probe driver: one live research_first run on the Pi/Craft lineage.

Drains the stream fully (CLAUDE.md gotcha: an early disconnect cancels the
server generator and the run never records). Bare requirement only — any
attachment rotates task_hash off lineage 492bae60177b.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import quote

BASE = "http://localhost:8770"
STUDIO_DIR = Path(__file__).resolve().parents[1] / "backend" / "studio"


def _server_is_stale() -> str | None:
    """uvicorn runs with no --reload; a `pkill` that fails to kill leaves the OLD
    process bound to :8770 and a fresh uvicorn can't rebind, so stale code serves
    silently and a "passing" run proves nothing (cost two runs, 2026-07-08).
    Returns a reason if the server started before the newest studio/*.py, else None.
    Best-effort: any lookup failure → None (don't block)."""
    from datetime import datetime

    try:
        pid = int(subprocess.check_output(["pgrep", "-f", "uvicorn studio.app"]).split()[0])
        # macOS ps: absolute start time, e.g. "Wed Jul  8 21:13:34 2026".
        lstart = subprocess.check_output(["ps", "-o", "lstart=", "-p", str(pid)]).decode().strip()
        started = datetime.strptime(lstart, "%a %b %d %H:%M:%S %Y").timestamp()
    except Exception:
        return None
    newest_src = max((p.stat().st_mtime for p in STUDIO_DIR.glob("*.py")), default=0.0)
    if started < newest_src:
        return f"server is {int(newest_src - started)}s older than newest studio/*.py — restart it"
    return None
REQUIREMENT = (
    "Study how to use Pi and Craft to develop agents and create a research "
    "report, need to include example code and design architecture."
)
EXPECTED_HASH = "492bae60177b"


def main() -> int:
    task_hash = hashlib.sha256(REQUIREMENT.strip().lower().encode()).hexdigest()[:12]
    if task_hash != EXPECTED_HASH:
        print(f"ABORT: task_hash {task_hash} != {EXPECTED_HASH} — off-lineage requirement")
        return 1
    stale = _server_is_stale()
    if stale:
        print(f"ABORT: {stale}")
        return 2

    body = json.dumps(
        {"llm": {"profile": "gemma"}, "embed": {}, "tools_enabled": True}
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/session", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        session_id = json.loads(resp.read())["session_id"]
    print(f"session_id={session_id}", flush=True)

    url = f"{BASE}/run/{session_id}?requirement={quote(REQUIREMENT)}"
    events = 0
    last_lines: list[str] = []
    # No read timeout: WRITE-stage LLM calls stall the stream for minutes.
    with urllib.request.urlopen(urllib.request.Request(url)) as stream:
        for raw in stream:  # drain until the server closes
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                events += 1
                print(line, flush=True)
            elif line.startswith("data:") and len(line) < 400:
                last_lines.append(line)
                if len(last_lines) > 20:
                    last_lines.pop(0)
    print(f"stream closed after {events} events")
    print("tail data frames:")
    for line in last_lines:
        print(" ", line[:200])
    print(f"PROBE_SESSION={session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
