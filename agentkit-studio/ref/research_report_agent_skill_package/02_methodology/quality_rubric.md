# Generic Research Report Quality Rubric

Use a 100-point score. A report should not be considered publish-ready unless it
scores 90+ and has no hard-fail issues.

| Category | Weight | What to check |
|---|---:|---|
| Scope and research framing | 8 | Clear topic, audience, purpose, assumptions, exclusions |
| Profile fit and structure | 8 | Sections match selected report profile and user constraints |
| Source quality | 12 | Sources are authoritative, current when needed, relevant, and diverse enough |
| Citation integrity | 12 | Major factual claims are cited; links are valid; quotes are accurate |
| Evidence synthesis | 12 | Report compares and connects evidence instead of listing source summaries |
| Analytical depth | 10 | Includes tradeoffs, implications, alternatives, and decision criteria |
| Practical usefulness | 9 | Provides useful recommendations, checklists, next steps, or decision support |
| Profile-specific assets | 6 | Code, diagrams, tables, or matrices are present only when useful/requested and are valid |
| Readability and formatting | 7 | Clear headings, concise paragraphs, plain language, no duplicate sections |
| Limitations and uncertainty | 6 | Weak evidence, conflicts, caveats, and open questions are explicit |
| Governance and safety | 5 | Human review, privacy/security/cost/tool risks handled when relevant |
| Packaging completeness | 5 | Includes required report, evidence, source notes, scorecard, manifest/checklist |

## Score Interpretation

| Score | Decision |
|---:|---|
| 90-100 | Publish-ready |
| 80-89 | Good draft, minor revision required |
| 70-79 | Major revision required |
| 60-69 | Weak report; restructure and re-research |
| <60 | Not acceptable; restart research loop |

## Hard-Fail Criteria

Even if the weighted score is high, the report must fail if any of these are
true:

| Hard fail | Why it blocks release |
|---|---|
| No citations for major factual claims | Cannot verify accuracy |
| Major conclusion based on unverified evidence | High hallucination risk |
| Source says something different from the report | Misrepresentation |
| Placeholder references remain | Report is unfinished |
| Malformed or explicitly unverified links remain | Reader cannot audit sources |
| Duplicate outline or repeated section blocks remain | Report assembly failed |
| Unbalanced code fence or broken code marked runnable | Misleads the reader |
| Report lacks limitations or uncertainty | Overconfident analysis |
| Unsafe tool action or data exposure | Security/compliance risk |
| No clear answer to the original question | Fails the task |
| Excessive copied text from sources | Copyright and quality risk |

## Profile-Specific Scoring Notes

- Technical reports may score code/diagrams when requested; non-technical reports
  should not be penalized for omitting code.
- Market reports should emphasize source recency, market signals, comparisons,
  risks, and recommendations.
- Policy reports should emphasize stakeholders, impacts, options, tradeoffs,
  legal/regulatory context, and uncertainty.
- Literature reviews should emphasize search method, evidence quality,
  disagreement, gaps, and bibliography.
- General reports should remain concise and should not be over-structured.
