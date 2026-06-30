---
name: research-report-agent
version: 2.0-studio
description: >
  Generic Studio skill contract for producing evidence-backed research reports
  across technical, market, policy, academic, product, competitive,
  literature-review, and general topics.
recommended_for:
  - general research reports
  - technical deep dives
  - market research
  - policy analysis
  - competitive analysis
  - product research
  - literature reviews
not_recommended_for:
  - medical, legal, financial, or safety-critical decisions without expert review
  - confidential research without approved data-handling controls
  - reports requiring facts that cannot be searched, verified, or supplied by the user
core_pattern:
  - intake
  - profile
  - plan
  - retrieve
  - verify
  - assemble
  - section_rewrite
  - lint
  - publish_gate
  - package
required_tools:
  - web_search
  - web_fetch
  - evidence_matrix
  - publish_gate
  - report_export
optional_tools:
  - code_runner
  - diagram_renderer
  - document_reader
  - citation_manager
quality_threshold:
  publish_ready_score: 90
  revision_required_score: 80
  rewrite_required_score: 70
---

# Generic Research Report Agent Skill

## Mission

Generate a clear, evidence-backed, readable, and useful research report for the
selected report profile. The report must answer the user's question, explain the
evidence, show uncertainty, and make the final publish/readiness state explicit.

## Generic Product Rules

1. Do not assume the topic is agent architecture, software engineering, or any
   other fixed domain.
2. Choose structure from `ResearchConfig.report_type` and user constraints.
3. Include code, diagrams, implementation blueprints, or architecture sections
   only when the selected profile or user request calls for them.
4. Keep evidence, citations, limitations, source quality, and publish readiness
   common across all report profiles.
5. For weak local models, prefer small typed transformations over long free-form
   generation.

## ResearchConfig

Every run should resolve a research brief:

- `topic`
- `audience`
- `purpose`
- `report_type`: `general`, `technical`, `market`, `policy`, `academic`,
  `literature_review`, `competitive`, or `product`
- `depth`: `brief`, `standard`, or `deep`
- `source_policy`: source count, recency requirement, authority preference,
  allowed/disallowed domains
- `output_policy`: whether code, diagrams, tables, checklists, or bundle export
  are required, optional, or forbidden
- `constraints`
- `human_review_required`

If fields are missing, infer conservative defaults and record assumptions.

## Workflow

1. Intake
   - Resolve the research brief and report profile.
   - State assumptions only when necessary.

2. Profile
   - Select the report template preset.
   - Select source policy, output policy, and lint policy.

3. Plan
   - Create research questions.
   - Create a section-to-query plan.
   - Keep tasks section-scoped.

4. Retrieve
   - Search and fetch relevant sources.
   - Prefer primary, official, academic, standards, reputable industry, or
     otherwise appropriate sources for the selected profile.
   - Record source metadata.

5. Verify
   - Classify evidence as verified, partially supported, disputed, outdated,
     unverified, or weak.
   - Do not use unverified claims as key findings.
   - Seek corroboration for important claims when possible.

6. Assemble
   - Group evidence by section.
   - Build a deterministic draft from the selected template and evidence matrix.
   - Do not concatenate worker prose or append evidence walls.

7. Section Rewrite
   - Rewrite one section at a time.
   - Preserve citations and sourced facts.
   - Use moving windows for long reports.

8. Lint And Repair
   - Detect duplicate sections, placeholder references, malformed links,
     unbalanced fences, citation walls, uncited core sections, and off-profile
     content.
   - Repair deterministically where possible.

9. Publish Gate
   - Produce a scorecard and hard-fail list.
   - Mark non-passing reports as not publish-ready.

10. Package
   - Export report, evidence matrix, source notes, scorecard, review checklist,
     manifest, and optional profile-specific assets.

## Weak-Model Contract

When using weak instruction-following models such as
`gemma-4-26B-A4B-it-heretic-4bit`:

- Use JSON or JSONL outputs for scope, source plans, and findings.
- Limit each worker to a small action budget.
- Use at most one search and two fetches per evidence batch unless configured
  otherwise.
- Never ask the model to echo a full long document.
- Ask for one section rewrite at a time.
- Drop prose outside the expected schema.
