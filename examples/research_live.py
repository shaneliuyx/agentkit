"""Live full-stack research — real internet, real citations, fed back.

Unlike `eval_fullstack.py` (offline, deterministic 15/15 matrix), this run goes
to the *real web*. The Researcher role does genuine ReAct: it searches SearXNG,
reads pages over HTTP, and answers with source URLs; `quality.verify` then checks
those citations are actually LIVE (real HTTP HEAD), the answer is stored in and
recalled from memory, the trajectory is compacted, and a Writer drafts the final
report. The researched result is printed back at the end.

Stack exercised end-to-end with a live network:
  web tools (SearXNG + urllib) → agent.run_role (real ReAct + router + memory)
  → memory.TieredMemory (ingest + recall) → quality.verify (REAL link liveness)
  → context.compact (trajectory handoff) → agent Writer (final report).

Backends: search = SearXNG at $SEARXNG_URL (default localhost:8080); page fetch =
stdlib urllib (no scrapling needed); link-check = HttpUrlChecker (real HEAD).

Run:  .venv/bin/python examples/research_live.py
Needs: oMLX on :8000, and SearXNG reachable (OrbStack `searxng-lab` on :8080).
"""

from __future__ import annotations

import html
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

# Make web_toolkit use the local SearXNG (it gates on this env var being set).
os.environ.setdefault("SEARXNG_URL", "http://localhost:8080")
sys.path.insert(0, "/Users/yuxinliu/code/agent-prep/shared")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from web_toolkit import web_search  # noqa: E402

from agentkit.agent.roles import RESEARCHER, WRITER, run_role  # noqa: E402
from agentkit.agent.router import route  # noqa: E402
from agentkit.context.compactor import compact  # noqa: E402
from agentkit.memory.store import MemoryStore  # noqa: E402
from agentkit.memory.tiered import TieredMemory  # noqa: E402
from agentkit.quality.verify import (  # noqa: E402
    HttpUrlChecker,
    extract_claims,
    verify,
)
from run_measured import OMLXClient, OMLXEmbedder  # noqa: E402

CHAT_MODEL = "gemma-4-26B-A4B-it-heretic-4bit"
QUESTION = (
    "What is SearXNG and what are two of its main privacy features? "
    "Attach the source URL for each claim."
)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


# --------------------------- real web tools --------------------------------

def tool_web_search(args: dict) -> dict:
    """Search the web (SearXNG) and return structured hits."""
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "missing 'query'"}
    hits = web_search(query, results=5)
    return {"results": [{"title": h.title, "url": h.url, "snippet": h.snippet}
                        for h in hits]}


def tool_read_url(args: dict) -> dict:
    """Fetch a URL over HTTP and return its visible text (stdlib only)."""
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return {"error": f"bad url: {url}"}
    req = urllib.request.Request(url, headers={"User-Agent": "agentkit-research/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read(200_000).decode("utf-8", "replace")
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    text = _WS.sub(" ", html.unescape(_TAG.sub(" ", raw))).strip()
    return {"url": url, "text": text[:2000]}


def banner(title: str) -> None:
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def main() -> None:
    client = OMLXClient(model=CHAT_MODEL, max_tokens=512)
    emb = OMLXEmbedder()
    tools = {"web_search": tool_web_search, "read_url": tool_read_url}

    # connectivity preflight — fail loud if the web backend is unreachable.
    probe = web_search("test", results=1)
    print(f"[preflight] SearXNG via {os.environ['SEARXNG_URL']}: "
          f"{'OK' if probe else 'NO RESULTS'} ({len(probe)} hit)")

    # router picks the tier for a 'hard' research step (deterministic).
    print(f"[router] research step -> {route('hard')}")

    # ---- 1. REAL research: Researcher does ReAct over the live web ----
    banner(f"RESEARCH QUESTION\n{QUESTION}")
    res = run_role(RESEARCHER, QUESTION, client, tools=tools, max_rounds=5)
    answer = res.answer
    tool_calls = [s.tool_name for s in res.trajectory if s.tool_name]
    print(f"\n[agent] rounds={res.rounds_used} stop={res.stop_reason} "
          f"tool_calls={tool_calls} tokens={res.total_tokens}")
    banner("RESEARCHER ANSWER (from the live web)")
    print(answer.strip() or "(empty)")

    # ---- 2. memory: store the finding, then recall it ----
    with tempfile.TemporaryDirectory() as d:
        tm = TieredMemory(MemoryStore(Path(d) / "m.db", embedder=emb),
                          client=OMLXClient(model=CHAT_MODEL, max_tokens=256))
        tm.ingest_session([answer], session=0, atomise=True)
        recalled = tm.recall("SearXNG privacy features", k=3)
        banner("MEMORY RECALL (atomised + depth-reranked)")
        for h in recalled:
            print(" •", h.content[:100])

        # ---- 3. quality.verify: are the cited URLs REALLY live? ----
        claims = extract_claims(answer)
        cited = [c for c in claims if c.citation]
        findings = verify(answer, checker=HttpUrlChecker(timeout=8))
        dead = [f for f in findings if f.issue == "dead link"]
        banner("VERIFICATION (real HTTP link-liveness on cited URLs)")
        print(f" claims={len(claims)} cited={len(cited)} "
              f"uncited={len(claims) - len(cited)}; "
              f"cited URLs live-checked, dead={len(dead)}")
        for c in cited[:5]:
            print(f"   ✓ cited: {c.text[:50]!r} -> {c.citation}")
        for f in dead:
            print(f"   ✗ DEAD: {f.url}")

        # ---- 4. context.compact: the trajectory handoff artifact ----
        msgs = [{"role": s.role, "content": s.content} for s in res.trajectory]
        cr = compact(msgs, keep=1) if msgs else None
        if cr:
            print(f"\n[compact] trajectory {cr.est_tokens_before} -> "
                  f"{cr.est_tokens_after} tok")

        # ---- 5. Writer drafts the final report from the verified notes ----
        report = run_role(WRITER, f"Draft a 4-sentence briefing from these "
                          f"notes, keep the source URLs:\n{answer}", client,
                          max_rounds=1).answer
        banner("FINAL REPORT (Writer)")
        print(report.strip() or "(empty)")

    # ---- feedback summary ----
    live = sum(1 for f in findings if f.severity in ("critical", "high"))
    banner("FEEDBACK")
    print(f"researched {len(tool_calls)} tool call(s); answer {len(answer)} chars; "
          f"{len(findings)} verification finding(s) ({live} link/critical); "
          f"memory recalled {len(recalled)} atom(s).")
    print("Real internet research completed end-to-end through the agentkit stack.")


if __name__ == "__main__":
    main()
