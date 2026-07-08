"""Phase 0 (PLAN Part 1.4): run the bare Pi/Craft acceptance N times on current
code, record a-e + score + generation metrics per run, print a distribution.

Structure defects are fixed (hardcoding gone, ordering fixed, seed-merge fixed);
this isolates the remaining GENERATION-QUALITY variance (word/diagram/relationship
-section landing) as the true blocker to consistent a-e.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
DB = BACKEND / "tmp/task_runs.db"
WS = BACKEND / "tmp/studio-workspaces"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
LINEAGE = "492bae60177b"


def _run_probe() -> str | None:
    out = subprocess.run(
        [str(BACKEND / ".venv/bin/python"), str(HERE / "probe_run_sse.py")],
        capture_output=True, text=True,
    ).stdout
    m = re.search(r"PROBE_SESSION=(\S+)", out)
    if not m:
        print("  probe produced no session:", out[-300:])
    return m.group(1) if m else None


def _score(session_id: str) -> str:
    q = (f"SELECT substr(cast(score as text),1,6) FROM task_runs WHERE "
         f"task_hash='{LINEAGE}' AND session_id='{session_id}'")
    return subprocess.run(["sqlite3", str(DB), q], capture_output=True, text=True).stdout.strip()


def _metrics(session_id: str) -> dict:
    md = WS / session_id / "result.md"
    if not md.exists():
        return {"error": "no result.md"}
    t = md.read_text()
    heads = re.findall(r"(?m)^## (.+)$", t)
    rel = [h for h in heads if re.search(r"(?i)integrat|comparison|relationship|alternativ|extension", h)]
    return {
        "words": len(t.split()),
        "mermaid": t.count("```mermaid"),
        "code": len(re.findall(r"```(?:python|typescript|javascript|js|ts)\b", t)),
        "refs_last": heads[-1] == "References" if heads else False,
        "rel_section": rel[0][:40] if rel else "",
        "craft_oss": "craft-agents-oss" in t.split("## References")[-1] if "## References" in t else False,
    }


def _ae(m: dict) -> dict:
    """Part 1.3 a-e as booleans from metrics."""
    return {
        "a_craft": bool(m.get("craft_oss")),
        "b_words>1468": m.get("words", 0) > 1468,
        "c_3diagrams": m.get("mermaid", 0) >= 3,
        "d_2code": m.get("code", 0) >= 2,
        "e_relsection": bool(m.get("rel_section")),
        "fmt_refslast": bool(m.get("refs_last")),
    }


def main() -> int:
    rows = []
    for i in range(1, N + 1):
        print(f"[run {i}/{N}] generating...", flush=True)
        sid = _run_probe()
        if not sid:
            rows.append({"run": i, "error": "no session"})
            continue
        m = _metrics(sid)
        ae = _ae(m)
        rows.append({"run": i, "session": sid, "score": _score(sid), **m, "ae": ae})
        print(f"  {sid} score={rows[-1]['score']} words={m.get('words')} "
              f"mermaid={m.get('mermaid')} code={m.get('code')} refs_last={m.get('refs_last')} "
              f"rel={m.get('rel_section')!r} a-e={sum(ae.values())}/6", flush=True)

    print("\n===== PHASE 0 DISTRIBUTION =====")
    ok = [r for r in rows if "ae" in r]
    crit = ["a_craft", "b_words>1468", "c_3diagrams", "d_2code", "e_relsection", "fmt_refslast"]
    for c in crit:
        passes = sum(1 for r in ok if r["ae"][c])
        print(f"  {c:16} {passes}/{len(ok)} pass")
    full = [r for r in ok if all(r["ae"].values())]
    print(f"  FULL a-e+fmt PASS: {len(full)}/{len(ok)}")
    if ok:
        ws = [r["words"] for r in ok]
        me = [r["mermaid"] for r in ok]
        print(f"  words: min={min(ws)} max={max(ws)} | mermaid: min={min(me)} max={max(me)}")
    print("\nJSON:", json.dumps(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
