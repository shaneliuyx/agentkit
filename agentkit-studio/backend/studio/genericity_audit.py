"""Conservative checks for hardcoded task-specific report content in source."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


_ALLOWED_PATH_PARTS = {"tests", "fixtures", "ref", "tmp", "__pycache__"}
_GENERIC_TERMS = {
    "artifact",
    "catalog",
    "citation",
    "evidence",
    "finding",
    "loop",
    "publish",
    "report",
    "section",
    "skill",
    "source",
    "template",
    "url",
}
_KNOWN_EXAMPLE_PHRASES = (
    "catalog management for agent loops",
    "local catalogs are best suited",
    "remote catalogs are useful",
    "workflow-vs-agent auditability",
)


@dataclass(frozen=True)
class GenericityIssue:
    path: str
    line: int
    phrase: str
    reason: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}: {self.phrase}"


def _is_allowed_path(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & _ALLOWED_PATH_PARTS)


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower())


def _looks_like_fixed_report(text: str) -> bool:
    if len(text) < 240:
        return False
    heading_count = len(re.findall(r"(?m)^#{1,3}\s+\w+", text))
    if heading_count < 2:
        return False
    word_count = len(_words(text))
    if word_count < 55:
        return False
    unique = {w.rstrip("s") for w in _words(text)} - _GENERIC_TERMS
    return len(unique) >= 12


def _literal_issues(path: Path, text: str, line: int) -> list[GenericityIssue]:
    low = text.lower()
    issues: list[GenericityIssue] = []
    for phrase in _KNOWN_EXAMPLE_PHRASES:
        if phrase in low:
            issues.append(
                GenericityIssue(
                    str(path),
                    line,
                    phrase,
                    "topic-specific example prose in production string",
                )
            )
    if _looks_like_fixed_report(text):
        snippet = " ".join(text.strip().split())[:100]
        issues.append(
            GenericityIssue(
                str(path),
                line,
                snippet,
                "string literal looks like a fixed report draft",
            )
        )
    return issues


def audit_python_file(path: Path) -> list[GenericityIssue]:
    if _is_allowed_path(path) or path.suffix != ".py":
        return []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    issues: list[GenericityIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            issues.extend(_literal_issues(path, node.value, getattr(node, "lineno", 1)))
    return issues


def audit_genericity(paths: list[str | Path]) -> list[GenericityIssue]:
    issues: list[GenericityIssue] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in path.rglob("*.py"):
                issues.extend(audit_python_file(child))
        else:
            issues.extend(audit_python_file(path))
    return issues
