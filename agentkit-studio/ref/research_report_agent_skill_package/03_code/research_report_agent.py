from dataclasses import dataclass, field
from typing import List, Dict, Any
import json
import time


@dataclass
class SourceNote:
    title: str
    source_type: str
    claim: str
    url: str = ""
    date: str = ""
    reliability: int = 3
    relevance: int = 3
    status: str = "unverified"


@dataclass
class ReportState:
    topic: str
    audience: str
    purpose: str
    toc: List[str] = field(default_factory=list)
    research_questions: List[str] = field(default_factory=list)
    source_notes: List[SourceNote] = field(default_factory=list)
    draft: str = ""
    scorecard: Dict[str, Any] = field(default_factory=dict)
    iteration: int = 0
    max_iterations: int = 3


class ResearchReportAgent:
    def __init__(self):
        self.required_sections = [
            "Executive Summary",
            "Background and Context",
            "Research Questions",
            "Methodology",
            "Current State",
            "Deep Analysis",
            "Practical Implementation",
            "Quality and Governance",
            "Reflection and Limitations",
            "Recommendations",
            "Conclusion",
            "References",
        ]

    def run(self, topic: str, audience: str, purpose: str) -> ReportState:
        state = ReportState(topic=topic, audience=audience, purpose=purpose)

        self.intake(state)
        self.plan_toc(state)
        self.plan_research_questions(state)

        while state.iteration < state.max_iterations:
            state.iteration += 1
            print(f"\n--- Iteration {state.iteration} ---")

            self.retrieve_sources(state)
            self.verify_sources(state)
            self.draft_report(state)
            self.evaluate_report(state)

            if self.is_publish_ready(state):
                print("Report is publish-ready.")
                break

            self.revise_plan(state)

        return state

    def intake(self, state: ReportState) -> None:
        if not state.topic.strip():
            raise ValueError("Topic is required.")
        if not state.audience.strip():
            state.audience = "technical and business readers"
        if not state.purpose.strip():
            state.purpose = "support decision-making"

    def plan_toc(self, state: ReportState) -> None:
        state.toc = self.required_sections.copy()

    def plan_research_questions(self, state: ReportState) -> None:
        state.research_questions = [
            f"What is the current state of {state.topic}?",
            f"Why does {state.topic} matter to {state.audience}?",
            f"What evidence supports the main claims about {state.topic}?",
            f"What are the practical implementation steps for {state.topic}?",
            f"What risks, limitations, and open questions remain?",
        ]

    def retrieve_sources(self, state: ReportState) -> None:
        # Replace this mock implementation with web search / document search.
        mock_results = [
            SourceNote(
                title="Foundational paper or official documentation",
                source_type="primary",
                claim=f"{state.topic} requires structured planning, evidence collection, and evaluation.",
                url="https://example.com/source-1",
                date="2026",
                reliability=5,
                relevance=5,
                status="candidate",
            ),
            SourceNote(
                title="Implementation guide",
                source_type="technical documentation",
                claim=f"Practical {state.topic} systems need checkpoints, guardrails, and traceability.",
                url="https://example.com/source-2",
                date="2026",
                reliability=4,
                relevance=5,
                status="candidate",
            ),
        ]

        state.source_notes.extend(mock_results)

    def verify_sources(self, state: ReportState) -> None:
        for note in state.source_notes:
            if note.reliability >= 4 and note.relevance >= 4:
                note.status = "verified"
            elif note.reliability >= 3:
                note.status = "partially_supported"
            else:
                note.status = "weak"

    def draft_report(self, state: ReportState) -> None:
        verified_claims = [
            note.claim for note in state.source_notes
            if note.status in {"verified", "partially_supported"}
        ]

        claims_text = "\n".join(f"- {claim}" for claim in verified_claims)

        state.draft = f"""# Research Report: {state.topic}

## 1. Executive Summary
This report explains {state.topic} for {state.audience}. Its purpose is to {state.purpose}.

## 2. Background and Context
{state.topic} should be understood as a system-level topic requiring both conceptual clarity and practical implementation guidance.

## 3. Research Questions
{chr(10).join(f"- {q}" for q in state.research_questions)}

## 4. Methodology
The agent collected candidate sources, filtered them by reliability and relevance, and converted verified claims into an evidence-backed report.

## 5. Evidence Summary
{claims_text}

## 6. Practical Implementation
A production-ready system should include:
- planning
- retrieval
- source verification
- drafting
- evaluator review
- revision loop
- final packaging

## 7. Reflection and Limitations
This draft is generated from a simplified mock source system. In production, the agent must use real source retrieval, citation management, and human review.

## 8. Conclusion
A strong research-report agent should combine structured workflow with controlled agentic iteration.
"""

    def evaluate_report(self, state: ReportState) -> None:
        section_coverage = sum(1 for section in state.toc if section.split()[0] in state.draft)
        has_sources = len(state.source_notes) >= 2
        has_verified = any(note.status == "verified" for note in state.source_notes)
        has_reflection = "Reflection" in state.draft or "Limitations" in state.draft
        has_practical = "Practical Implementation" in state.draft

        score = 0
        score += 25 if section_coverage >= 5 else 10
        score += 25 if has_sources and has_verified else 10
        score += 20 if has_practical else 0
        score += 15 if has_reflection else 0
        score += 15 if len(state.draft) > 1000 else 5

        hard_fails = []
        if not has_sources:
            hard_fails.append("missing_sources")
        if not has_reflection:
            hard_fails.append("missing_reflection_or_limitations")

        state.scorecard = {
            "score": score,
            "hard_fails": hard_fails,
            "decision": "publish_ready" if score >= 90 and not hard_fails else "revise",
        }

    def is_publish_ready(self, state: ReportState) -> bool:
        return (
            state.scorecard.get("score", 0) >= 90
            and not state.scorecard.get("hard_fails")
        )

    def revise_plan(self, state: ReportState) -> None:
        print("Revision required. Improving evidence depth and report completeness.")
        # In a real agent, this would generate new searches and targeted fixes.
        time.sleep(0.2)

    def save(self, state: ReportState, path: str = "research_report.md") -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(state.draft)

        with open("research_state.json", "w", encoding="utf-8") as f:
            json.dump({
                "topic": state.topic,
                "audience": state.audience,
                "purpose": state.purpose,
                "toc": state.toc,
                "research_questions": state.research_questions,
                "scorecard": state.scorecard,
                "sources": [note.__dict__ for note in state.source_notes],
            }, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    agent = ResearchReportAgent()
    final_state = agent.run(
        topic="Agent system design for research report generation",
        audience="cloud architects and AI solution builders",
        purpose="guide implementation of a reliable research-report agent",
    )
    agent.save(final_state)

    print("\nFinal scorecard:")
    print(json.dumps(final_state.scorecard, indent=2))
