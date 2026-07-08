"""Part 1.3 (a-e) + format acceptance checks against a recorded workspace.

Usage: accept_check.py <session_id>   (workspace under backend/tmp/studio-workspaces)
Deterministic only — no LLM. Complements studio.research_quality_mvp (which
this also runs) with the acceptance-bar specifics.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from studio.research_quality_mvp import evaluate_workspace  # noqa: E402

FENCE_RE = re.compile(r"^(```+)(.*)$")
BARE_LANG_RE = re.compile(r"^(?:python|typescript|javascript|mermaid|bash|json)\s*$", re.I)
PLACEHOLDER_RE = re.compile(r"_\(to be completed\)_|\(summary unavailable\)|TODO", re.I)


def fence_structure_issues(text: str) -> list[str]:
    issues: list[str] = []
    open_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        m = FENCE_RE.match(line.strip())
        if m:
            open_fence = not open_fence
            continue
        if not open_fence and BARE_LANG_RE.match(line.strip()):
            issues.append(f"line {i}: bare language line outside fence: {line.strip()!r}")
        if open_fence and line.lstrip().startswith("## "):
            issues.append(f"line {i}: heading inside fence")
    if open_fence:
        issues.append("unclosed fence at EOF")
    return issues


def main() -> int:
    sid = sys.argv[1]
    ws = BACKEND / "tmp/studio-workspaces" / sid
    text = (ws / "result.md").read_text()
    claims = [json.loads(l) for l in (ws / "claims.jsonl").read_text().splitlines() if l.strip()]
    claim_urls = {c.get("url", "") for c in claims}

    words = len(text.split())
    mermaid = text.count("```mermaid")
    # References BODY only (heading → next '## '), not everything after it — a
    # relationship/other section rendered below References would otherwise leak
    # its in-prose URLs into this check (false-positive fixed 2026-07-08).
    rm = re.search(r"(?ms)^## References\s*\n(.*?)(?=^## |\Z)", text)
    refs = rm.group(1) if rm else ""
    ref_urls = set(re.findall(r"https?://[^\s)]+", refs))
    code_blocks = len(re.findall(r"```(?:python|typescript|javascript|js|ts)\b", text))
    # References must be the LAST '## ' section (v44 ordering defect).
    heads_after_refs = re.findall(r"(?m)^## (.+)$", text.split("## References", 1)[-1])

    checks = [
        ("a. craft-agents-oss cited", "craft-agents-oss" in refs, refs.count("craft-agents-oss")),
        ("b. words > 1468", words > 1468, words),
        ("c. three diagrams", mermaid >= 3, mermaid),
        ("c2. integration cross-edge", bool(re.search(r"-->\s*\|", text)), ""),
        ("d. >=2 code blocks + integration", code_blocks >= 2, code_blocks),
        ("d2. proposed-usage caption", "Proposed usage" in text, ""),
        ("e. relationship section", bool(re.search(r"(?im)^##.*(integration|comparison|relationship|alternativ|extension)", text)), ""),
        ("fmt. references LAST section", not heads_after_refs, heads_after_refs[:3]),
        ("fmt. references subset of claims", ref_urls <= claim_urls, sorted(ref_urls - claim_urls)[:3]),
        ("fmt. no internal markers", "RESEARCH_FINDING" not in text, ""),
        ("fmt. no placeholders", not PLACEHOLDER_RE.search(text), ""),
    ]
    fails = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
        fails += 0 if ok else 1
    for issue in fence_structure_issues(text):
        print(f"FAIL  fmt. fence: {issue}")
        fails += 1
    mvp = evaluate_workspace(ws, ["Pi", "Craft"])
    print("mvp harness passed:", mvp.get("artifact", {}).get("passed"))
    for c in mvp.get("artifact", {}).get("checks", []):
        if not c["passed"]:
            print(f"FAIL  mvp.{c['name']}: {c['detail'][:160]}")
            fails += 1
    print("ACCEPTANCE:", "PASS" if fails == 0 else f"FAIL ({fails} checks)")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
