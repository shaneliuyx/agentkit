"""Offline MVP checks for research-first report quality gates."""

from __future__ import annotations

import argparse
import itertools
import json
import re
from pathlib import Path
from typing import Any, Callable


_URL_RE = re.compile(r"https?://\S+")
_MERMAID_LABEL_RE = re.compile(r'\["([^"]+)"\]')
_MERMAID_EDGE_LABEL_RE = re.compile(r"-->\|([^|]+)\||--\|([^|]+)\|")
_INTERNAL_MARKER_RE = re.compile(
    r"(?i)\b(?:RESEARCH_FINDING|SEARCH|PATCH_TARGET|ARTICLE_TITLE|KEY_INSIGHT)\b"
)
_WORD_RE = re.compile(r"[a-z0-9]{3,}")
_SUMMARY_RE = re.compile(r"(?ims)^##+\s+executive\s+summary\s*$\n(?P<body>.*?)(?=^##+\s+|\Z)")
_FILLER_LABELS = {
    "a",
    "an",
    "and",
    "are",
    "contains",
    "examples",
    "files",
    "including",
    "over",
    "repository",
    "repositories",
    "source",
    "sources",
    "the",
    "various",
}
_RELATION_GENERIC_WORDS = {
    "connected",
    "discussed",
    "documented",
    "integrate",
    "integrates",
    "integration",
    "operate",
    "related",
    "relationship",
    "share",
    "shared",
    "through",
    "together",
}

Evaluator = Callable[..., dict[str, Any]]


def _claims(claims_jsonl: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in claims_jsonl.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _claim_subjects(claim: dict[str, Any]) -> set[str]:
    return {str(s).casefold() for s in claim.get("subjects") or [] if str(s).strip()}


def _has_url(claim: dict[str, Any]) -> bool:
    return bool(_URL_RE.fullmatch(str(claim.get("url") or "").strip()))


def _evidence_words(claims: list[dict[str, Any]], subjects: list[str]) -> set[str]:
    subject_words = {w for subject in subjects for w in _WORD_RE.findall(subject.casefold())}
    text = " ".join(
        str(claim.get(key) or "") for claim in claims if _has_url(claim) for key in ("claim", "quote")
    )
    return {w for w in _WORD_RE.findall(text.casefold()) if w not in subject_words}


def _claim_words(claims: list[dict[str, Any]], subjects: list[str]) -> set[str]:
    subject_words = {w for subject in subjects for w in _WORD_RE.findall(subject.casefold())}
    text = " ".join(str(claim.get(key) or "") for claim in claims for key in ("claim", "quote"))
    return {
        word
        for word in _WORD_RE.findall(text.casefold())
        if word not in subject_words and word not in _FILLER_LABELS
    }


def _joint_claims(claims: list[dict[str, Any]], subjects: list[str]) -> list[dict[str, Any]]:
    wanted = {subject.casefold() for subject in subjects}
    return [claim for claim in claims if wanted <= _claim_subjects(claim) and _has_url(claim)]


def _summary_mechanism_words(artifact: str, subjects: list[str]) -> set[str]:
    match = _SUMMARY_RE.search(artifact)
    if not match:
        return set()
    body = match.group("body").split("```", 1)[0]
    subject_keys = {subject.casefold() for subject in subjects}
    subject_words = {word for subject in subjects for word in _WORD_RE.findall(subject.casefold())}
    words: set[str] = set()
    for sentence in re.split(r"(?<=[.!?])\s+", body):
        low = sentence.casefold()
        # Whole-word subject match, not substring: 'pi' is a substring of 'apis',
        # which wrongly counted a single-subject sentence as a 2-subject
        # relationship sentence and harvested its words as ungrounded mechanism.
        if sum(1 for subject in subject_keys if re.search(r"\b" + re.escape(subject) + r"\b", low)) < 2:
            continue
        words.update(
            word
            for word in _WORD_RE.findall(low)
            if word not in subject_words and word not in _FILLER_LABELS and word not in _RELATION_GENERIC_WORDS
        )
    return words


def _mermaid_labels(artifact: str) -> list[str]:
    labels = list(_MERMAID_LABEL_RE.findall(artifact))
    for groups in _MERMAID_EDGE_LABEL_RE.findall(artifact):
        labels.extend(label for label in groups if label)
    return labels


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def evaluate(*, artifact: str, claims_jsonl: str, subjects: list[str]) -> dict[str, Any]:
    """Return generic gate results for one rendered report artifact."""
    claims = _claims(claims_jsonl)
    folded = {s: s.casefold() for s in subjects}

    missing = [
        subject
        for subject, key in folded.items()
        if not any(key in _claim_subjects(claim) and _has_url(claim) for claim in claims)
    ]
    checks = [
        _check(
            "subject_evidence",
            not missing,
            "all subjects have claim URLs" if not missing else f"missing subject evidence: {', '.join(missing)}",
        )
    ]

    missing_pairs: list[str] = []
    for left, right in itertools.combinations(subjects, 2):
        pair = {left.casefold(), right.casefold()}
        if not any(pair <= _claim_subjects(claim) and _has_url(claim) for claim in claims):
            missing_pairs.append(f"{left} + {right}")
    checks.append(
        _check(
            "relationship_evidence",
            not missing_pairs,
            "all subject pairs have joint claim URLs"
            if not missing_pairs
            else f"missing relationship evidence: {', '.join(missing_pairs)}",
        )
    )

    all_subject_claims = _joint_claims(claims, subjects)
    checks.append(
        _check(
            "n_subject_relationship_evidence",
            len(subjects) < 3 or bool(all_subject_claims),
            "not applicable for fewer than three subjects"
            if len(subjects) < 3
            else "all-subject relationship evidence present"
            if all_subject_claims
            else "missing all-subject relationship evidence",
        )
    )

    subject_keys = set(folded.values())
    evidence_words = _evidence_words(claims, subjects)
    weak = []
    for label in _mermaid_labels(artifact):
        low = label.strip().casefold()
        if low in subject_keys:
            continue
        words = {word for word in _WORD_RE.findall(low) if word not in _FILLER_LABELS}
        if low in _FILLER_LABELS or len(low) < 3 or not words or not words <= evidence_words:
            weak.append(label)
    checks.append(
        _check(
            "diagram_label_quality",
            not weak,
            "diagram labels are specific" if not weak else f"weak diagram labels: {', '.join(weak)}",
        )
    )

    mechanism_words = _summary_mechanism_words(artifact, subjects)
    # Ground summary mechanism vocabulary against the whole cited corpus, not only
    # joint-subject claims: relationship EXISTENCE is already enforced by
    # relationship_evidence above, so a mechanism word attested by a single-subject
    # cited claim (woven into a 2-subject summary sentence) is grounded, not a leak.
    grounded_words = _claim_words([claim for claim in claims if _has_url(claim)], subjects)
    checks.append(
        _check(
            "summary_mechanism_grounding",
            not mechanism_words or mechanism_words <= grounded_words,
            "summary mechanisms are grounded"
            if not mechanism_words or mechanism_words <= grounded_words
            else f"ungrounded summary mechanism words: {', '.join(sorted(mechanism_words))}",
        )
    )

    markers = sorted({m.group(0).upper() for m in _INTERNAL_MARKER_RE.finditer(artifact)})
    checks.append(
        _check(
            "internal_marker_leaks",
            not markers,
            "no internal markers found" if not markers else f"internal markers found: {', '.join(markers)}",
        )
    )

    return {"passed": all(check["passed"] for check in checks), "checks": checks}


def _jsonl(records: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(record) for record in records)


def _canaries() -> list[dict[str, Any]]:
    valid_claims = [
        {"claim": "Alpha exposes an API.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta exposes a CLI.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {
            "claim": "Alpha and Beta integrate through an API bridge.",
            "subjects": ["Alpha", "Beta"],
            "url": "https://example.com/ab",
        },
    ]
    three_subject_claims = [
        {"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"},
        {"claim": "Beta has evidence.", "subjects": ["Beta"], "url": "https://example.com/b"},
        {"claim": "Gamma has evidence.", "subjects": ["Gamma"], "url": "https://example.com/c"},
        {"claim": "Alpha and Beta share an API.", "subjects": ["Alpha", "Beta"], "url": "https://example.com/ab"},
        {"claim": "Alpha and Gamma share a queue.", "subjects": ["Alpha", "Gamma"], "url": "https://example.com/ac"},
        {"claim": "Beta and Gamma share events.", "subjects": ["Beta", "Gamma"], "url": "https://example.com/bc"},
    ]
    return [
        {
            "name": "valid_report",
            "artifact": (
                "# Report\n\n"
                "```mermaid\n"
                "flowchart TD\n"
                '  A["Alpha"] --> X["API Bridge"] --> B["Beta"]\n'
                "```\n"
            ),
            "claims_jsonl": _jsonl(valid_claims),
            "subjects": ["Alpha", "Beta"],
            "expect_passed": True,
        },
        {
            "name": "missing_subject_evidence",
            "artifact": "Alpha and Beta report.",
            "claims_jsonl": _jsonl(
                [{"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"}]
            ),
            "subjects": ["Alpha", "Beta"],
            "expect_failed": "subject_evidence",
        },
        {
            "name": "missing_relationship_evidence",
            "artifact": "Alpha and Beta are related.",
            "claims_jsonl": _jsonl(
                [
                    {"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"},
                    {"claim": "Beta has evidence.", "subjects": ["Beta"], "url": "https://example.com/b"},
                ]
            ),
            "subjects": ["Alpha", "Beta"],
            "expect_failed": "relationship_evidence",
        },
        {
            "name": "uncited_diagram_label",
            "artifact": '```mermaid\nflowchart TD\n  A["Alpha"] --> X["Uncited Layer"] --> B["Beta"]\n```',
            "claims_jsonl": _jsonl(valid_claims),
            "subjects": ["Alpha", "Beta"],
            "expect_failed": "diagram_label_quality",
        },
        {
            "name": "missing_all_subject_relationship",
            "artifact": "Alpha, Beta, and Gamma operate together.",
            "claims_jsonl": _jsonl(three_subject_claims),
            "subjects": ["Alpha", "Beta", "Gamma"],
            "expect_failed": "n_subject_relationship_evidence",
        },
        {
            "name": "uncorroborated_summary_mechanism",
            "artifact": "## Executive Summary\n\nAlpha and Beta integrate through a hidden adapter.",
            "claims_jsonl": _jsonl(
                [
                    {"claim": "Alpha exposes an API.", "subjects": ["Alpha"], "url": "https://example.com/a"},
                    {"claim": "Beta exposes a CLI.", "subjects": ["Beta"], "url": "https://example.com/b"},
                    {
                        "claim": "Alpha and Beta are discussed together in deployment notes.",
                        "subjects": ["Alpha", "Beta"],
                        "url": "https://example.com/ab",
                    },
                ]
            ),
            "subjects": ["Alpha", "Beta"],
            "expect_failed": "summary_mechanism_grounding",
        },
        {
            # Guard against the too-strict flip: a 2-subject summary sentence uses a
            # mechanism word ("sidecar") attested only by a single-subject cited claim.
            # Fails under the old joint-claims-only grounding, passes now.
            "name": "mechanism_grounded_in_single_subject_claim",
            "artifact": "## Executive Summary\n\nAlpha and Beta integrate through a sidecar.",
            "claims_jsonl": _jsonl(
                [
                    {"claim": "Alpha runs a sidecar proxy.", "subjects": ["Alpha"], "url": "https://example.com/a"},
                    {"claim": "Beta accepts requests.", "subjects": ["Beta"], "url": "https://example.com/b"},
                    {"claim": "Alpha and Beta integrate.", "subjects": ["Alpha", "Beta"], "url": "https://example.com/ab"},
                ]
            ),
            "subjects": ["Alpha", "Beta"],
            "expect_passed": True,
        },
        {
            "name": "uncited_diagram_edge_label",
            "artifact": '```mermaid\nflowchart TD\n  A["Alpha"] -->|Hidden Adapter| B["Beta"]\n```',
            "claims_jsonl": _jsonl(valid_claims),
            "subjects": ["Alpha", "Beta"],
            "expect_failed": "diagram_label_quality",
        },
        {
            "name": "internal_marker_leak",
            "artifact": "Alpha evidence. SEARCH leaked inline.",
            "claims_jsonl": _jsonl(
                [{"claim": "Alpha has evidence.", "subjects": ["Alpha"], "url": "https://example.com/a"}]
            ),
            "subjects": ["Alpha"],
            "expect_failed": "internal_marker_leaks",
        },
    ]


def evaluate_gate_health(evaluator: Evaluator = evaluate) -> dict[str, Any]:
    """Run generic canaries that detect too-loose and too-strict gate behavior."""
    checks: list[dict[str, Any]] = []
    for canary in _canaries():
        result = evaluator(
            artifact=canary["artifact"],
            claims_jsonl=canary["claims_jsonl"],
            subjects=canary["subjects"],
        )
        by_name = {check["name"]: bool(check["passed"]) for check in result.get("checks", [])}
        if canary.get("expect_passed"):
            passed = bool(result.get("passed"))
            detail = "expected pass" if passed else "unexpected fail"
        else:
            gate = str(canary["expect_failed"])
            passed = by_name.get(gate) is False
            detail = f"expected {gate} failure" if passed else f"unexpected pass for {gate}"
        checks.append(_check(str(canary["name"]), passed, detail))
    return {"passed": all(check["passed"] for check in checks), "checks": checks}


def evaluate_with_gate_health(*, artifact: str, claims_jsonl: str, subjects: list[str]) -> dict[str, Any]:
    """Return artifact verdict plus canary-based confidence in the gate itself."""
    return {
        "artifact": evaluate(artifact=artifact, claims_jsonl=claims_jsonl, subjects=subjects),
        "gate_health": evaluate_gate_health(),
    }


def evaluate_workspace(path: Path, subjects: list[str]) -> dict[str, Any]:
    artifact = next((path / name for name in ("artifact.md", "result.md") if (path / name).exists()), None)
    claims = path / "claims.jsonl"
    if artifact is None:
        raise FileNotFoundError(f"no artifact.md or result.md in {path}")
    if not claims.exists():
        raise FileNotFoundError(f"no claims.jsonl in {path}")
    return evaluate_with_gate_health(
        artifact=artifact.read_text(encoding="utf-8"),
        claims_jsonl=claims.read_text(encoding="utf-8"),
        subjects=subjects,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--subject", action="append", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(evaluate_workspace(args.workspace, args.subject), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
