"""studio.export — serialize a finished Studio run to a loop-library loop (M9).

Closes the loop: Studio both *consumes* published loops (M7 seeding) and
*produces* them. A finished run carries everything a publishable loop needs —
the plan steps (→ the loop's flat ``steps``), per-step topology (→ step
annotations), the requirement (→ ``description``/``useWhen``), the Loop Doctor
checks (→ ``verification``), and the budget (→ a bounded-spend note in ``why``).

The output is the REAL catalog loop shape (verified against catalog.json
schemaVersion 2): ``slug, title, category{slug,label}, description, useWhen,
prompt, verification{title,detail}, steps[], why, keywords[]``. ``number``,
``author``, ``published`` etc. are catalog-assigned at publish time and omitted
here — the exported loop is an unpublished draft a human edits before
contributing, so it round-trips conceptually rather than claiming authorship.

This module is PURE: it serializes a ``RunSnapshot`` value and touches nothing.
"""

from __future__ import annotations

import html
import re
from typing import Any

from studio.session import RunSnapshot

#: Tokens too common to carry signal as loop keywords.
_STOP = frozenset(
    "the a an is are to of in on for and or with this that build make use using "
    "do does run loop agent workflow when how into from your you it a an".split()
)


def _slugify(text: str, *, fallback: str = "studio-run") -> str:
    """Lowercase, hyphenated slug from free text (catalog slug shape)."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or fallback


def _title(requirement: str) -> str:
    """A short human title from the requirement (first clause, capped)."""
    head = requirement.strip().splitlines()[0].strip() if requirement.strip() else ""
    head = head[:80].rstrip()
    return head[:1].upper() + head[1:] if head else "Studio run"


def _keywords(requirement: str, steps: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    """Content keywords from the requirement + step descriptions (deduped)."""
    text = requirement + " " + " ".join(str(s.get("description", "")) for s in steps)
    raw = "".join(c if c.isalnum() else " " for c in text.lower()).split()
    seen: list[str] = []
    for tok in raw:
        if tok not in _STOP and len(tok) > 2 and tok not in seen:
            seen.append(tok)
        if len(seen) >= limit:
            break
    return seen


def _verification(checks: list[dict[str, Any]]) -> dict[str, str]:
    """Build the loop's ``verification`` block from the Loop Doctor checks.

    ``title`` states the success gate; ``detail`` lists each audit dimension's
    status (and any suggested repair) so the published loop carries the same
    bounded/checked/safe/terminating contract the run was audited against.
    """
    passed = [c for c in checks if c.get("status") == "pass"]
    title = (
        f"All {len(checks)} loop-doctor checks pass."
        if checks and len(passed) == len(checks)
        else f"{len(passed)} of {len(checks)} loop-doctor checks pass."
    )
    lines = []
    for c in checks:
        line = f"{c.get('name')}: {c.get('status')}"
        if c.get("fix"):
            line += f" — {c['fix']}"
        lines.append(line)
    detail = "; ".join(lines) if lines else "No audit recorded."
    return {"title": title, "detail": detail}


def _steps_with_topology(steps: list[dict[str, Any]], topology: dict[str, str]) -> list[str]:
    """Flatten plan steps to the loop's ``steps`` list, annotating fan-out.

    A finite DAG IS the stop condition, so each step keeps its dependency +
    topology shape inline (e.g. "(mesh; after s1)") — the annotation is how the
    flat catalog ``steps`` field preserves the run's structure for a re-import.
    """
    out: list[str] = []
    for s in steps:
        desc = str(s.get("description", "")).strip()
        topo = topology.get(str(s.get("id")), "")
        deps = [str(d) for d in (s.get("depends_on") or [])]
        notes = []
        if topo and topo != "single":
            notes.append(topo)
        if deps:
            notes.append("after " + ", ".join(deps))
        if notes:
            desc = f"{desc} ({'; '.join(notes)})"
        out.append(desc)
    return out


def run_to_loop(snapshot: RunSnapshot) -> dict[str, Any]:
    """Serialize a finished ``RunSnapshot`` into a loop-library loop dict.

    PURE. The returned dict is the catalog loop shape (an unpublished draft):
    ``{slug, title, category{slug,label}, description, useWhen, prompt,
    verification{title,detail}, steps[], why, keywords[]}``.
    """
    requirement = snapshot.requirement
    steps = snapshot.plan_steps
    title = _title(requirement)
    bounded = snapshot.budget_ceiling is not None
    budget_note = (
        f" Bounded by a {snapshot.budget_ceiling:g}-token fan-out ceiling."
        if bounded
        else " Set a token ceiling before publishing to bound fan-out spend."
    )
    loop = {
        "slug": _slugify(title),
        "title": title,
        "category": {"slug": "engineering", "label": "Engineering"},
        "description": (
            f"A Studio-exported agent loop that decomposes '{requirement.strip()}' "
            f"into {len(steps)} bounded, verified phases."
        ),
        "useWhen": (
            f"Use this whenever you need to: {requirement.strip()}"
            if requirement.strip()
            else "Use this for a repeatable multi-phase agent run."
        ),
        "prompt": requirement.strip(),
        "verification": _verification(snapshot.loopdoctor_checks),
        "steps": _steps_with_topology(steps, snapshot.topology),
        "why": (
            "Exported from a finished AgentKit Studio run: each phase is gate-checked "
            "and the final output is verified, so the loop ties its outcome to "
            "observable checks rather than memory." + budget_note
        ),
        "keywords": _keywords(requirement, steps),
    }
    if snapshot.evidence_matrix:
        loop["evidenceMatrix"] = snapshot.evidence_matrix
    return loop


def run_to_research_package(snapshot: RunSnapshot) -> dict[str, Any]:
    """Serialize a finished run into the minimal research-report package.

    JSON manifest + file contents first; ZIP can wait until the schema proves useful.
    """
    loop = run_to_loop(snapshot)
    scorecard = snapshot.scorecard_100 or {}
    evidence = snapshot.evidence_matrix or ""
    evidence_rows = _evidence_rows_from_matrix(evidence)
    files = {
        "research_report.md": snapshot.result or "",
        "research_report.html": _markdown_to_html(snapshot.result or ""),
        "evidence_matrix.md": evidence,
        "evidence_matrix.json": evidence_rows,
        "scorecard.json": scorecard,
        "metrics.json": snapshot.metrics or {},
        "human_review_checklist.md": _human_review_checklist(snapshot, scorecard, evidence),
        "run_manifest.json": {
            "packageVersion": 1,
            "requirement": snapshot.requirement,
            "cancelled": snapshot.cancelled,
            "budgetCeiling": snapshot.budget_ceiling,
            "steps": snapshot.plan_steps,
            "topology": snapshot.topology,
            "loopdoctorChecks": snapshot.loopdoctor_checks,
            "hasEvidenceMatrix": bool(evidence.strip()),
            "evidenceCount": len(evidence_rows),
            "hasScorecard": bool(scorecard),
            "review": _review(snapshot),
            "metrics": snapshot.metrics or {},
            "stopReport": (snapshot.metrics or {}).get("stop_report", {}),
            "rendererStatus": {
                "html": "rendered",
                "pdf": "unavailable",
                "diagrams": "unavailable",
            },
            "exportedFiles": [],
        },
        "agent_trace.jsonl": snapshot.agent_trace_jsonl or "",
        "checkpoints.jsonl": snapshot.checkpoints_jsonl or "",
        "source_notes.json": _source_notes_from_evidence(evidence_rows),
        "requirements.txt": "# No package-specific runtime dependencies are required.\n",
        "research_agent_demo.py": _research_agent_demo_py(),
        "research_agent_demo.pseudo": _research_agent_demo_pseudo(),
        "research_agent_demo_output.txt": _research_agent_demo_output(snapshot, evidence_rows),
        "loop.json": loop,
    }
    files["run_manifest.json"]["exportedFiles"] = list(files)
    return {
        "manifest": {
            "title": loop["title"],
            "packageVersion": 1,
            "format": "research_package_json",
            "review": _review(snapshot),
            "rendererStatus": files["run_manifest.json"]["rendererStatus"],
            "files": list(files),
        },
        "files": files,
    }


def _human_review_checklist(
    snapshot: RunSnapshot, scorecard: dict[str, Any], evidence: str
) -> str:
    review = _review(snapshot)
    checks = [
        f"- [ ] Review final report against task: {snapshot.requirement.strip()}",
        "- [ ] Confirm cited evidence supports every major claim.",
    ]
    if review.get("required"):
        checks.append("- [ ] Complete required human review before publishing.")
    if not evidence.strip():
        checks.append("- [ ] Add or verify the evidence matrix before publishing.")
    if scorecard:
        checks.append("- [ ] Review low-scoring scorecard categories and unresolved weaknesses.")
    if any(c.get("status") != "pass" for c in snapshot.loopdoctor_checks):
        checks.append("- [ ] Resolve non-passing Loop Doctor checks.")
    return "# Human Review Checklist\n\n" + "\n".join(checks) + "\n"


def _review(snapshot: RunSnapshot) -> dict[str, Any]:
    if snapshot.review:
        return snapshot.review
    from studio.report_quality import build_review_status

    return build_review_status(
        snapshot.requirement,
        evidence_count=_markdown_table_row_count(snapshot.evidence_matrix),
        scorecard=snapshot.scorecard_100,
        loopdoctor_checks=snapshot.loopdoctor_checks,
    )


def _markdown_table_row_count(markdown: str) -> int:
    rows = [
        line for line in (markdown or "").splitlines()
        if line.strip().startswith("|") and "---" not in line
    ]
    return max(0, len(rows) - 1)


def _markdown_to_html(markdown: str) -> str:
    """Render enough Markdown for an offline report bundle."""
    lines = (markdown or "").splitlines()
    out: list[str] = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Research Report</title>",
        "<style>body{font-family:system-ui,-apple-system,sans-serif;line-height:1.5;max-width:960px;margin:32px auto;padding:0 20px}table{border-collapse:collapse;width:100%;margin:16px 0}th,td{border:1px solid #d0d7de;padding:6px 8px;text-align:left;vertical-align:top}pre{background:#f6f8fa;padding:12px;overflow:auto}code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}</style>",
        "</head>",
        "<body>",
    ]
    paragraph: list[str] = []
    list_open = False
    fence_open = False

    def flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{html.escape(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            out.append("</ul>")
            list_open = False

    table_lines: list[str] = []

    def flush_table() -> None:
        if not table_lines:
            return
        rows = [_split_table_row(line) for line in table_lines if "---" not in line]
        if rows:
            out.append("<table>")
            header, *body = rows
            out.append("<thead><tr>" + "".join(f"<th>{html.escape(cell)}</th>" for cell in header) + "</tr></thead>")
            if body:
                out.append("<tbody>")
                for row in body:
                    out.append("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>")
                out.append("</tbody>")
            out.append("</table>")
        table_lines.clear()

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            flush_paragraph()
            close_list()
            flush_table()
            if fence_open:
                out.append("</code></pre>")
                fence_open = False
            else:
                out.append("<pre><code>")
                fence_open = True
            continue
        if fence_open:
            out.append(html.escape(line))
            continue
        if line.strip().startswith("|"):
            flush_paragraph()
            close_list()
            table_lines.append(line)
            continue
        flush_table()
        if not line.strip():
            flush_paragraph()
            close_list()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{html.escape(heading.group(2).strip())}</h{level}>")
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        if bullet:
            flush_paragraph()
            if not list_open:
                out.append("<ul>")
                list_open = True
            out.append(f"<li>{html.escape(bullet.group(1).strip())}</li>")
            continue
        paragraph.append(line.strip())
    flush_paragraph()
    close_list()
    flush_table()
    if fence_open:
        out.append("</code></pre>")
    out.extend(["</body>", "</html>"])
    return "\n".join(out)


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _evidence_rows_from_matrix(markdown: str) -> list[dict[str, str]]:
    lines = [
        line for line in (markdown or "").splitlines()
        if line.strip().startswith("|") and "---" not in line
    ]
    if len(lines) < 2:
        return []
    headers = [re.sub(r"[^a-z0-9]+", "_", h.lower()).strip("_") for h in _split_table_row(lines[0])]
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        cells = _split_table_row(line)
        row = {headers[i]: cells[i] if i < len(cells) else "" for i in range(len(headers))}
        rows.append(row)
    return rows


def _source_notes_from_evidence(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    for row in rows:
        source = row.get("source", "")
        url_match = re.search(r"\((https?://[^)]+)\)", source)
        notes.append({
            "claim": row.get("claim", ""),
            "source": re.sub(r"^\[|\]\(https?://[^)]+\)$", "", source),
            "url": url_match.group(1) if url_match else source if source.startswith("http") else "",
            "status": row.get("status", ""),
            "used_in": row.get("used_in", ""),
            "caveats": row.get("caveats", ""),
        })
    return notes


def _research_agent_demo_py() -> str:
    return '''"""Offline miniature of the Studio research-report loop."""

from __future__ import annotations

import json
from pathlib import Path


def plan(topic: str) -> list[str]:
    return ["scope", "collect evidence", "draft report", "validate", "package"]


def observe(tool: str, summary: str) -> dict[str, str]:
    return {"tool": tool, "status": "ok", "summary": summary}


def validate(report: str, evidence: list[dict[str, str]]) -> list[str]:
    issues = []
    if not report.strip():
        issues.append("empty report")
    if not evidence:
        issues.append("missing evidence")
    return issues


def run(topic: str, out_dir: str = "demo_output") -> dict[str, object]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    steps = plan(topic)
    evidence = [{"claim": topic, "source": "provided input", "status": "demo"}]
    report = "# Demo Research Report\\n\\n" + topic + "\\n"
    trace = [observe("demo_source", "used provided topic as sample evidence")]
    issues = validate(report, evidence)
    (out / "research_report.md").write_text(report, encoding="utf-8")
    (out / "source_notes.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    (out / "agent_trace.jsonl").write_text(
        "".join(json.dumps(row) + "\\n" for row in trace),
        encoding="utf-8",
    )
    return {"steps": steps, "issues": issues, "files": sorted(p.name for p in out.iterdir())}


if __name__ == "__main__":
    print(json.dumps(run("sample research topic"), indent=2))
'''


def _research_agent_demo_pseudo() -> str:
    return """PLAN topic into bounded steps.
OBSERVE each allowed tool result as compact trace data.
VALIDATE report text against available source notes.
CHECKPOINT report, source notes, and trace files.
PACKAGE the final markdown, evidence, metrics, and review checklist.
"""


def _research_agent_demo_output(snapshot: RunSnapshot, rows: list[dict[str, str]]) -> str:
    title = _title(snapshot.requirement)
    return (
        "Demo run summary\n"
        f"- topic: {title}\n"
        "- steps: scope, collect evidence, draft report, validate, package\n"
        f"- evidence_rows: {len(rows)}\n"
        "- files: research_report.md, source_notes.json, agent_trace.jsonl\n"
    )
