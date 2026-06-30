# Quality Gate Prompts

These prompts are seed material for deterministic/LLM-assisted gates. Gates
should consume `ResearchConfig.report_type` and the selected template.

## Gate 1: Profile and Outline Review

```markdown
Evaluate the proposed outline for the selected report profile.

Check:
1. Does it answer the user's original request?
2. Does it fit report_type: <report_type>?
3. Are profile-specific sections present only when useful?
4. Is anything redundant, missing, or off-profile?
5. Are code/diagram sections present only if requested or profile-appropriate?

Return:
- pass/fail
- score 0-10
- issues
- revised outline
```

## Gate 2: Evidence Review

```markdown
Evaluate the evidence matrix.

Check:
1. Are the sources appropriate for report_type: <report_type>?
2. Are important claims supported?
3. Are there conflicting claims?
4. Are any sources outdated for this topic?
5. Are any claims too strong for the evidence?
6. Are unverified claims excluded from key findings?

Return:
- pass/fail
- unsupported claims
- weak sources
- claims needing more research
- recommended source types to add
```

## Gate 3: Draft Review

```markdown
Evaluate the draft report.

Check:
1. Is the executive summary useful?
2. Does the structure match the selected report profile?
3. Is the analysis deeper than source summarization?
4. Is the report readable for the target audience?
5. Are limitations and uncertainty included?
6. Are recommendations actionable and evidence-proportionate?
7. Are optional assets, such as code/diagrams/tables, valid and useful when present?

Return:
- weighted score
- hard fails
- top 5 improvements
- sections needing revision
```

## Gate 4: Optional Code Or Diagram Review

Run only when the selected profile or user request includes code, pseudocode, or
diagrams.

```markdown
Evaluate optional technical assets.

Check:
1. If code is claimed runnable, does it run?
2. If code is pseudocode, is it clearly labeled?
3. Are dependencies, inputs, and outputs clear?
4. Is error handling included when relevant?
5. Are diagrams syntactically valid and explained by nearby text?
6. Are assets necessary for this report profile?

Return:
- pass/fail
- bugs or syntax errors
- missing dependencies
- off-profile assets
- improvement suggestions
```
