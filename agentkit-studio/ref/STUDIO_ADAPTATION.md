# Studio Reference Adaptation

This directory has been curated to match AgentKit Studio's planned generic
research report generator.

## Product Constraint

Studio must support technical, market, policy, academic, product, competitive,
literature-review, and general explanatory reports. The old Pi/Craft-oriented
sample is no longer treated as the default domain.

## Reusable Content

From `research_report_agent_skill_package/`:

- `01_skill/research-report-agent.skill.md`
  - Convert into a local/domain `Skill` through `SkillLibrary`.
  - Keep the generic loop contract: intake, profile, plan, retrieve, verify,
    assemble, section rewrite, lint, publish gate, package.
- `01_skill/publish_policy.yaml`
  - Use as seed material for `publish_gate.py`.
- `02_methodology/evidence_matrix_template.md`
  - Use as seed material for `EvidenceItem` fields and matrix export.
- `02_methodology/quality_rubric.md`
  - Use as seed material for the 100-point scorecard projection and hard fails.
- `02_methodology/recommended_toc.md`
  - Use as the starting preset registry for report profiles.
- `05_evaluation/human_review_checklist.md`
  - Use as seed material for human-review workflow and bundle export.
- `03_code/research_report_agent.py`
  - Use only as a minimal state-shape sketch. Do not port its mocked execution
    loop over Studio's existing runner.

From `enhanced_report_bundle/`:

- `source_notes.json`
  - Use as a generic source-note export schema example.
- `checkpoint.json`
  - Use as a generic checkpoint manifest example.
- `research_agent_demo.py`, `.pseudo`, and output files
  - Use as optional educational/demo assets for bundle export, not mandatory
    content for every report.
- `enhanced_report.md` and `demo_report.md`
  - Use only as examples/fixtures, not default templates.

## Required Generalization

1. Add report profiles, not a single fixed report structure.
2. Keep technical sections optional.
3. Route default sections from `ResearchConfig.report_type`.
4. Keep evidence, source quality, citations, limitations, and publish readiness
   common across all profiles.
5. Use the bad generated report fixture only to test structural quality gates:
   duplicate sections, malformed links, placeholders, citation walls, and code
   fragments.

## Suggested Profile Mapping

- `general`: executive summary, scope/questions, background, key findings,
  evidence and analysis, implications/recommendations, limitations, references.
- `technical`: generic sections plus architecture/current state, methodology,
  implementation blueprint, evaluation, optional code/diagrams.
- `market`: market context, segments, competitors, customer needs, pricing,
  trends, risks, recommendations.
- `policy`: policy context, stakeholders, options, impacts, tradeoffs,
  regulatory/legal considerations, uncertainty.
- `academic` or `literature_review`: research questions, method, prior work,
  evidence quality, agreements/disagreements, gaps, bibliography.
- `competitive`: comparison criteria, competitor profiles, capability matrix,
  positioning, risks, recommendations.
