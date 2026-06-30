# Curated Reference Materials For Research Report Generator

This directory stores curated reference material used by
`PLAN-research-report-skill-package-improvements.md`.

The initial files came from:

- `/Users/yuxinliu/Downloads/research_report_agent_skill_package`
- `/Users/yuxinliu/Downloads/enhanced_report_bundle`

They have now been modified for AgentKit Studio's target product: a generic,
profile-driven research report generator.

## Directories

- `research_report_agent_skill_package/`
  - Curated for: generic skill contract, report profiles, evidence matrix,
    quality rubric, publish policy, and human review checklist.
  - The mock runner remains only as a state-shape sketch. Do not port it over
    Studio's existing runner.

- `enhanced_report_bundle/`
  - Curated for: bundle shape, source-note schema, checkpoint schema, and
    optional demo-code packaging examples.
  - Generated binary/sample outputs from the original bundle were removed when
    they were not useful as implementation references.

## Rules

1. Studio's target is a generic research report generator.
2. Topic-specific behavior must come from `ResearchConfig`, report profile,
   template preset, evidence policy, and user constraints.
3. Code, diagrams, implementation blueprints, and architecture sections are
   optional profile-specific assets, not default requirements.
4. Use these files as implementation-ready reference requirements; update them
   when the plan changes.
5. Do not treat any single copied report topic as the default product domain.
