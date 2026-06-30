# Curated Generic Research Report Skill References

This directory contains Studio-adapted reference material for a generic research
report generator. The original package was imported from a standalone research
report skill bundle, then modified to match the plan in this repository.

## Contents

- `01_skill/research-report-agent.skill.md` — generic, profile-driven skill contract.
- `01_skill/publish_policy.yaml` — publish thresholds, hard fails, and profile policy.
- `02_methodology/agent_loop.md` — generic research-report loop and weak-model controls.
- `02_methodology/recommended_toc.md` — report template presets by profile.
- `02_methodology/quality_rubric.md` — generic 100-point rubric and hard fails.
- `02_methodology/evidence_matrix_template.md` — evidence matrix shape.
- `02_methodology/production_best_practice_architecture.md` — reference architecture notes.
- `03_code/research_report_agent.py` — minimal state-shape sketch only.
- `03_code/score_report.py` — simple scoring example, not Studio's final scorer.
- `04_diagrams/*.mmd` — optional diagram examples.
- `05_evaluation/quality_gate_prompts.md` — profile-aware quality gate seed prompts.
- `05_evaluation/human_review_checklist.md` — profile-aware human review checklist.
- `06_roadmap/implementation_roadmap.md` — original roadmap notes.
- `07_references/references.md` — original reference notes.

Removed from the curated copy:

- combined full export;
- original manifest with hashes;
- generated artifacts that would be stale after editing.

## How Studio Should Use This

1. Convert the skill file into an `agentkit.skills.core.Skill`.
2. Use the ToC presets as seed data for `ResearchConfig.report_type`.
3. Use the evidence matrix as the schema source for `EvidenceItem`.
4. Use publish policy and quality rubric as seed data for `publish_gate.py` and
   `rubric_scorecard_100()`.
5. Treat code examples as sketches, not implementation source.

## Non-Goals

- Do not port the mock runner into Studio.
- Do not make technical/code sections mandatory for every report.
- Do not treat any original topic framing as the default product domain.
