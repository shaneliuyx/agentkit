"""Section-file workspace for report artifacts.

``artifact.md`` remains the assembled report. Section files are the future edit
targets for workers/reducers, with ``active_outline.json`` recording the live
section order.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agentkit.artifacts.sections import split_sections

SECTIONS_DIR = "sections"
OUTLINE_FILE = "active_outline.json"
ASSIGNMENT_QUEUE_FILE = "assignment_queue.json"
PLACEHOLDER = "_(pending - needs sourced content)_"


def slugify_section(title: str) -> str:
    """Filesystem-safe section slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return slug or "section"


def _section_title(heading: str) -> str:
    return re.sub(r"^#{1,6}\s*", "", heading or "").strip()


def _section_body(title: str, body: str | None = None) -> str:
    text = (body or "").strip()
    if text:
        lines = text.splitlines()
        if lines and re.match(r"^##\s+", lines[0]):
            lines = [lines[0], *[line for line in lines[1:] if not re.match(r"^#\s+", line)]]
            text = "\n".join(lines).strip()
        else:
            text = "\n".join(line for line in lines if not re.match(r"^#\s+", line)).strip()
        if not text:
            return f"## {title}\n\n{PLACEHOLDER}\n"
        if lines and re.match(r"^##\s+", lines[0]):
            return text.rstrip() + "\n"
        return f"## {title}\n\n{text}\n"
    return f"## {title}\n\n{PLACEHOLDER}\n"


def _unique_file(index: int, title: str, used: set[str]) -> str:
    base = f"{index:03d}-{slugify_section(title)}"
    name = f"{base}.md"
    n = 2
    while name in used:
        name = f"{base}-{n}.md"
        n += 1
    used.add(name)
    return name


def split_artifact_to_sections(
    text: str, initial_outline: list[str] | tuple[str, ...] = ()
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return ``(active_outline, section_file_contents)`` for an artifact."""
    pairs = split_sections(text or "")
    preamble = ""
    section_bodies: dict[str, str] = {}
    order: list[str] = []
    for heading, body in pairs:
        if heading == "(intro)":
            preamble = body.rstrip() + ("\n\n" if body.strip() else "")
            continue
        title = _section_title(heading)
        if not title:
            continue
        key = title.lower()
        if key not in section_bodies:
            order.append(title)
            section_bodies[key] = body

    outline_titles: list[str] = []
    seen: set[str] = set()
    for title in [str(s).strip() for s in initial_outline if str(s).strip()] + order:
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        outline_titles.append(title)

    files: dict[str, str] = {}
    used_files: set[str] = set()
    sections: list[dict[str, str]] = []
    for index, title in enumerate(outline_titles, start=1):
        file_name = _unique_file(index, title, used_files)
        sections.append({"title": title, "file": file_name})
        files[file_name] = _section_body(title, section_bodies.get(title.lower()))

    return {"preamble": preamble, "sections": sections}, files


def write_section_workspace(
    root: Path, artifact_text: str, initial_outline: list[str] | tuple[str, ...] = ()
) -> dict[str, Any]:
    """Write ``sections/`` files and ``active_outline.json`` under *root*."""
    outline, files = split_artifact_to_sections(artifact_text, initial_outline)
    section_dir = Path(root) / SECTIONS_DIR
    section_dir.mkdir(parents=True, exist_ok=True)
    for file_name, content in files.items():
        (section_dir / file_name).write_text(content, encoding="utf-8")
    (section_dir / OUTLINE_FILE).write_text(
        json.dumps(outline, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assemble_artifact_from_sections(root)
    return outline


def load_active_outline(root: Path) -> dict[str, Any] | None:
    """Read ``active_outline.json`` if a section workspace exists."""
    path = Path(root) / SECTIONS_DIR / OUTLINE_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def active_outline_titles(root: Path) -> list[str]:
    """Return ordered section titles from ``active_outline.json``."""
    outline = load_active_outline(root)
    if not outline:
        return []
    return [
        str(section.get("title", "")).strip()
        for section in outline.get("sections", [])
        if str(section.get("title", "")).strip()
    ]


def section_file_map(root: Path) -> dict[str, str]:
    """Return title -> section-relative file path from ``active_outline.json``."""
    outline = load_active_outline(root)
    if not outline:
        return {}
    files: dict[str, str] = {}
    for section in outline.get("sections", []):
        title = str(section.get("title", "")).strip()
        file_name = str(section.get("file", "")).strip()
        if title and file_name:
            files[title] = f"{SECTIONS_DIR}/{file_name}"
    return files


def write_assignment_queue(root: Path, rows: tuple[dict[str, str], ...]) -> None:
    """Persist queued one-file section assignments."""
    section_dir = Path(root) / SECTIONS_DIR
    section_dir.mkdir(parents=True, exist_ok=True)
    (section_dir / ASSIGNMENT_QUEUE_FILE).write_text(
        json.dumps(list(rows), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_assignment_queue(root: Path) -> list[dict[str, str]]:
    """Read queued section assignments."""
    path = Path(root) / SECTIONS_DIR / ASSIGNMENT_QUEUE_FILE
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def clear_completed_assignments(root: Path, completed_count: int) -> None:
    """Delete queue rows only after worker calls completed."""
    rows = load_assignment_queue(root)
    remaining = rows[max(0, int(completed_count or 0)):]
    write_assignment_queue(root, tuple(remaining))


def assemble_artifact_from_sections(root: Path) -> str:
    """Assemble ``artifact.md`` from ``sections/active_outline.json``."""
    root = Path(root)
    section_dir = root / SECTIONS_DIR
    outline = json.loads((section_dir / OUTLINE_FILE).read_text(encoding="utf-8"))
    parts: list[str] = []
    preamble = outline.get("preamble") or ""
    if preamble.strip():
        parts.append(preamble.rstrip())
    for section in outline.get("sections", []):
        file_name = section.get("file")
        if not file_name:
            continue
        path = section_dir / file_name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8").strip())
    assembled = "\n\n".join(p for p in parts if p).rstrip() + "\n"
    (root / "artifact.md").write_text(assembled, encoding="utf-8")
    return assembled
