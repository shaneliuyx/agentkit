#!/usr/bin/env python3
"""
research_agent_demo.py

A small, runnable teaching example for the enhanced report.
It models a Pi-like planner and a Craft-like orchestrator without requiring
external APIs or third-party packages.

Run:
    python3 research_agent_demo.py

Outputs:
    demo_report.md
    checkpoint.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable


@dataclass
class Observation:
    step: int
    tool: str
    status: str
    message: str
    data: dict[str, Any]


class CraftLikeOrchestrator:
    """Execution layer: validates tool names, runs tools, and records results."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.registry: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "load_notes": self.load_notes,
            "draft_summary": self.draft_summary,
            "validate_summary": self.validate_summary,
            "write_report": self.write_report,
        }

    def execute(self, step: int, tool: str, params: dict[str, Any]) -> Observation:
        if tool not in self.registry:
            return Observation(step, tool, "error", f"Unknown tool: {tool}", {})
        try:
            result = self.registry[tool](params)
            return Observation(step, tool, "ok", result.get("message", "ok"), result)
        except Exception as exc:  # safe boundary: do not crash the agent loop
            return Observation(step, tool, "error", str(exc), {})

    def load_notes(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params.get("path", "source_notes.json"))
        notes = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(notes, list) or not notes:
            raise ValueError("source_notes.json must contain a non-empty list")
        return {"message": f"Loaded {len(notes)} notes", "notes": notes}

    def draft_summary(self, params: dict[str, Any]) -> dict[str, Any]:
        notes = params["notes"]
        bullets = []
        for item in notes:
            bullets.append(f"- **{item['title']}**: {item['summary']} Source: {item['source']}")
        summary = "\n".join(bullets)
        return {"message": "Summary drafted", "summary": summary}

    def validate_summary(self, params: dict[str, Any]) -> dict[str, Any]:
        summary = params["summary"]
        checks = {
            "has_bullets": "- **" in summary,
            "has_sources": "https://" in summary,
            "reasonable_length": 100 <= len(summary) <= 5000,
        }
        passed = all(checks.values())
        if not passed:
            raise ValueError(f"Validation failed: {checks}")
        return {"message": "Summary passed validation", "checks": checks}

    def write_report(self, params: dict[str, Any]) -> dict[str, Any]:
        output = self.workspace / params.get("filename", "demo_report.md")
        content = params["content"]
        output.write_text(content, encoding="utf-8")
        return {"message": f"Report written to {output}", "path": str(output)}


class PiLikePlanner:
    """Reasoning layer: builds a plan and decides how observations feed the next step."""

    def __init__(self, goal: str) -> None:
        self.goal = goal
        self.memory: list[Observation] = []

    def plan(self) -> list[dict[str, Any]]:
        return [
            {"tool": "load_notes", "params": {"path": "source_notes.json"}},
            {"tool": "draft_summary", "params": {}},
            {"tool": "validate_summary", "params": {}},
            {"tool": "write_report", "params": {"filename": "demo_report.md"}},
        ]

    def enrich_params(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(params)
        if tool == "draft_summary":
            notes_obs = self.find_observation("load_notes")
            enriched["notes"] = notes_obs.data["notes"]
        elif tool == "validate_summary":
            summary_obs = self.find_observation("draft_summary")
            enriched["summary"] = summary_obs.data["summary"]
        elif tool == "write_report":
            summary_obs = self.find_observation("draft_summary")
            checks_obs = self.find_observation("validate_summary")
            enriched["content"] = self.compose_report(summary_obs.data["summary"], checks_obs.data["checks"])
        return enriched

    def find_observation(self, tool: str) -> Observation:
        for obs in reversed(self.memory):
            if obs.tool == tool and obs.status == "ok":
                return obs
        raise RuntimeError(f"Required observation not found: {tool}")

    def compose_report(self, summary: str, checks: dict[str, bool]) -> str:
        return (
            f"# Demo Research Agent Report\n\n"
            f"## Goal\n\n{self.goal}\n\n"
            f"## Synthesized Notes\n\n{summary}\n\n"
            f"## Validation Checks\n\n"
            + "\n".join(f"- {name}: {value}" for name, value in checks.items())
            + "\n\n## Reflection\n\n"
            "The important lesson is not that this simple script is autonomous. "
            "The lesson is that autonomy should be built from small, inspectable parts: "
            "planning, execution, validation, memory, and final synthesis.\n"
        )

    def checkpoint(self, path: Path) -> None:
        path.write_text(json.dumps([asdict(obs) for obs in self.memory], indent=2), encoding="utf-8")


def main() -> None:
    workspace = Path(".")
    orchestrator = CraftLikeOrchestrator(workspace)
    planner = PiLikePlanner("Create a concise, source-grounded report about agent architecture.")

    for step_number, task in enumerate(planner.plan(), start=1):
        params = planner.enrich_params(task["tool"], task["params"])
        obs = orchestrator.execute(step_number, task["tool"], params)
        planner.memory.append(obs)
        print(f"Step {step_number}: {task['tool']} -> {obs.status}")
        if obs.status != "ok":
            print(f"Stopped: {obs.message}")
            break

    planner.checkpoint(Path("checkpoint.json"))
    print("Done. Created demo_report.md and checkpoint.json")


if __name__ == "__main__":
    main()
