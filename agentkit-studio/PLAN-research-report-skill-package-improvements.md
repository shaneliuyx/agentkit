# Plan: Improve AgentKit Studio With Research Report Skill Package Lessons

Date: 2026-06-30

## Scope

This plan compares the current `agentkit-studio` codebase with the curated local references in `ref/research_report_agent_skill_package/` and `ref/enhanced_report_bundle/`, then turns the comparison into implementation work. The goal is not to copy the package. Studio is already a richer FastAPI/React multi-agent runner with SSE, hill-climb persistence, tool execution, section reducers, deterministic scoring, and export. The useful package lessons are the missing product contracts around evidence state, hard-fail governance, final packaging, and human review.

Product target: a generic research report generator. The Studio source code and built-in mechanisms must work for technical, market, policy, academic, product, competitive, literature-review, and general explanatory reports. Generated reports themselves must be specific to the user's task, topic, audience, and evidence; "generic" is a source-code/product constraint, not a desired report style. Agent-framework/Pi/Craft examples are validation fixtures and optional presets, not the default domain. Topic-specific behavior should come from intake, report profile, template preset, evidence policy, user constraints, fetched evidence, and reviewed catalog/template data, not hardcoded prompts or code paths.

Standing guardrail: production changes must remain task-neutral while generated reports remain task-specific. Do not add fixed prose, fixed conclusions, topic-specific recovery drafts, model-id branches such as "weak model report mode," or one-off templates for a single example. Any reusable improvement should be expressed as generic report-state, evidence, template/catalog, prompt, validation, or UI behavior; domain examples belong in tests, fixtures, references, user input, fetched evidence, or reviewed catalog entries.

## Evidence Base

Current Studio capabilities:

- FastAPI/React studio over `agentkit`, with session/run endpoints, SSE events, topology graph, panels, budgets, and loop export documented in `README.md` and `SPEC.md`.
- Research-report scoring is already deterministic and GUI-configurable: `backend/studio/rubric.py` has `sourcing`, `verification`, `evidence_depth`, `analysis`, `structure`, and `methodology` weights, verified URL integration, template coverage, and adjusted score penalties for open weaknesses.
- The reducer path already avoids whole-document re-emission. `backend/studio/findings.py` converts `RESEARCH_FINDING` blocks into additive patches, dedupes findings, preserves URLs, applies ranking tables, and grounds findings against fetched URLs or verified quotes.
- The runner already carries forward prior artifacts and weaknesses, seeds missing template sections, applies section-aware reducers, emits verification and loop-doctor events, and records finished run snapshots for export.
- `backend/studio/artifact_lint.py` adds deterministic malformed-mermaid and unbalanced-code-fence weaknesses.
- `backend/studio/export.py` serializes a finished run to a loop-library shape, not to a full research-report package.

Reference package lessons from `ref/research_report_agent_skill_package/`:

- `01_skill/research-report-agent.skill.md` defines a clean public contract: plan, retrieve, verify, synthesize, review, revise, package; it also requires audience/purpose intake, source metadata, evidence matrix, reflection, quality scoring, and final packaging.
- `02_methodology/evidence_matrix_template.md` defines explicit evidence fields: claim, source, source type, date, reliability, relevance, status, used-in section, and caveats/conflicts.
- `02_methodology/quality_rubric.md` defines a 100-point rubric and hard-fail criteria: missing citations, major unverified claims, source misrepresentation, invalid profile-specific assets, missing limitations, unsafe tool action, no answer, and excessive copied text.
- `03_code/research_report_agent.py` is a minimal mock loop. It is useful as a state-shape sketch (`SourceNote`, `ReportState`) but weaker than Studio: retrieval is mocked, scoring is coarse, no real tool grounding, no reducer, no SSE, no persistence.

Enhanced report lessons from `ref/enhanced_report_bundle/enhanced_report.md`:

- Generalize the Pi/Craft lesson into a domain-neutral layered architecture: report goal layer, reasoning/planning layer, orchestration/validation layer, tool/source layer.
- Use a validation-first loop: plan, validate tool request, execute, observe, validate result, then synthesize or re-plan.
- Make tool contracts explicit with allowlists, parameter validation, permission gates, sandboxing, error boundaries, and structured observations.
- Treat memory as structured state: short-term context, working notes, checkpoints, summaries, and preferences, not just chat history.
- Make observability and recovery visible: log plan, tool calls, outputs, validation decisions, checkpoints, retries, and final synthesis decisions.
- Export a complete bundle, not only Markdown: PDF, HTML, static diagrams, runnable practice code, pseudocode, source notes, sample output, and requirements.
- Include evaluation metrics: task success, tool-call accuracy, validation catch rate, citation accuracy, cost per task, human intervention rate, and recovery rate.
- Use explicit stop conditions: validation pass, max tool calls, repeated tool failure, approval needed, scope/cost/time limits.
- Prefer fewer, stronger sources and separate verified evidence from interpretation.

## Main Diagnosis

Studio has stronger orchestration than the package, but weaker explicit research state and productized artifact packaging. The package treats evidence as a first-class artifact; Studio treats findings mostly as reducer input and final markdown text. The enhanced report adds another gap: Studio has many internal safety and tracing mechanisms, but they are not yet exposed as a clear validation-first contract with durable checkpoints, observable tool observations, stop-condition metrics, and shareable report bundles. This makes the system good at producing and improving a document, but harder to audit as a research workflow: there is no durable evidence matrix, no typed source status ledger, no 100-point/hard-fail publish decision, no package export containing matrix/scorecard/checklist/PDF/HTML/code assets, and limited human-review surface.

## Decision 1: Evidence Matrix Should Be Derived From Existing Findings, Not A Separate Agent Loop

Option A: Add a new top-level `ResearchReportAgent` loop modeled after the package.

- Pros: clean conceptual match to the package, obvious `ReportState` object, easy to explain.
- Cons: duplicates `Runner`, `ToolAugmentedClient`, `TaskRunStore`, reducer logic, SSE events, token accounting, and hill-climb behavior. It would regress current strengths like grounded patching, dedupe, prior-run weaknesses, topology selection, and GUI controls.

Option B: Add a first-class `EvidenceItem`/matrix layer on top of current `RESEARCH_FINDING` parsing and reducer flow.

- Pros: reuses existing fetched-URL/quote grounding, dedupe, patch reducer, task persistence, SSE transport, and tests. The matrix becomes an audit product generated from the same facts that update the document.
- Cons: requires threading evidence items through reducer/post-run state and adding persistence/export/UI.

Chosen: Option B. The package's state model should inform Studio's data contract, not replace Studio's runner.

## Workstream A: Typed Evidence Matrix

Goal: make every accepted source-backed claim auditable outside the final prose.

Implementation:

1. Add `backend/studio/evidence.py`.
   - Define `EvidenceStatus = Literal["verified", "partially_supported", "disputed", "outdated", "unverified", "weak"]`.
   - Define `EvidenceItem` with fields matching the package template: `id`, `claim`, `source`, `source_type`, `url`, `date`, `reliability`, `relevance`, `status`, `used_in`, `quote`, `notes`.
   - Add `from_finding(finding, section_hint)` to convert existing `agentkit.artifacts.types.Finding` objects into `EvidenceItem`.
   - Add `render_evidence_matrix(items) -> str` for markdown export.

2. Extend `backend/studio/findings.py`.
   - In `_parse_findings`, capture `PUBLICATION`/`date` and source type when present. Current parsing keeps URL/title/quote/why/popularity/patch target; add metadata extraction without breaking old finding formats.
   - In `_make_section_reducer`, return or emit the accepted findings/evidence items in addition to merged text. If changing the reducer signature is too invasive, use a side-channel callback passed by `Runner` when constructing `_make_section_reducer`.

3. Persist evidence.
   - Extend `TaskRunStore.record` to store `evidence_json` or create a separate `task_run_evidence` table keyed by `task_hash`, `session_id`, `version`.
   - Prefer a separate table if evidence grows large or needs querying by URL/status. Prefer `evidence_json` if the first implementation only needs export and panel display.

Tradeoff: `evidence_json` is faster and lower risk, but querying "which URL keeps appearing as weak?" will be awkward. A table is better long-term, but touches migrations and test setup. Recommended first step: `evidence_json` on `task_runs`, with a helper that can later migrate to a table.

Acceptance tests:

- Parsing plain and JSON `RESEARCH_FINDING` with `PUBLICATION`, `SOURCE_TYPE`, `RELIABILITY`, `RELEVANCE`.
- Matrix rows preserve URL, quote, claim, and used-in section.
- Grounded duplicate findings produce one evidence item after dedupe.
- Offline tests pass without network.

## Workstream B: Hard-Fail Publish Gate

Goal: separate "quality score" from "publish decision." A report can score well structurally but still be blocked by hard defects.

Implementation:

1. Add `backend/studio/publish_gate.py`.
   - Input: final markdown, `VerifyEvent`, `GateEvent`s, evidence items, lint weaknesses, rubric score.
   - Output: `PublishGateResult` with `score_100`, `decision`, `hard_fails`, `category_scores`, `required_fixes`.
   - Map Studio signals to package hard fails:
     - Missing citations: report has factual sections but no verified URLs/evidence.
     - Major unverified claim: evidence status is `unverified`/`weak` for a claim used in `Key Findings`, `Recommendations`, or equivalent high-weight sections.
     - Source misrepresentation: later phase can use quote mismatch or verify-panel findings; initially flag when a cited URL is not in verified URL cache and no quote verifies.
     - Broken code not disclosed: deterministic lint for unbalanced code fences plus future code-block execution checks.
     - Missing limitations: rubric/template section absence for limitations/open questions.
     - Unsafe tool action: any `GateEvent` outcome `fail`/`reject`/`escalate`.
     - No answer: low structure plus missing conclusion/recommendation.
     - Excessive copied text: quote density threshold from `score_breakdown`.

2. Add an SSE event, e.g. `publish_gate`.
   - Backend: `backend/studio/events.py`.
   - Frontend types/store: `frontend/src/api/types.ts`, `frontend/src/store/runStore.ts`.
   - UI: show decision and hard fails near Loop Doctor or Result.

3. Integrate after verify/loopdoctor and before final `done`.
   - Runner currently emits verify and loopdoctor around `backend/studio/runner.py` run-finalization. Add publish gate there so it uses final markdown, gate events, and evidence.

Tradeoff: extend existing Loop Doctor vs add a separate Publish Gate.

- Loop Doctor already audits boundedness, verification, gates, and stopping. Extending it avoids another panel.
- But Loop Doctor is loop-quality oriented, while the package hard fails are report-publication oriented. Mixing them will blur "is the loop safe?" with "is this report publish-ready?"

Chosen: add a separate publish gate event and panel block, while letting Loop Doctor remain loop/system audit.

Acceptance tests:

- Report with no citations is blocked even if rubric structure is high.
- Report with lint weakness is blocked or downgraded.
- Report with `GateEvent(outcome="fail")` is blocked.
- Report with verified evidence, limitations, and no gate failures can be `PUBLISH_READY` at score >= 90.

## Workstream C: Expand Rubric To 100-Point Category View Without Losing Determinism

Goal: keep Studio's deterministic scorer but present package-aligned categories and thresholds.

Implementation:

1. Keep `rubric_score()` as the machine optimization signal.
2. Add `rubric_scorecard_100()` that projects current deterministic parts plus publish gate data into the package's 12 categories:
   - Scope/framing: intake metadata present and final answer addresses requirement.
   - ToC completeness: `sections_present`.
   - Source quality: evidence reliability/source type mix.
   - Citation integrity: verified URL ratio and uncited claims.
   - Evidence synthesis: current `analysis` signal.
   - Analytical depth: analysis signal plus report length/section coverage.
   - Practical usefulness: presence of recommendations/implementation/checklists.
   - Code quality: code lint/execution status, initially unknown unless code blocks exist.
   - Diagrams/tables: markdown table count and mermaid/code fence validity.
   - Readability: structure, paragraph length heuristics, no reducer preamble.
   - Reflection/limitations: section presence and uncertainty language.
   - Governance/safety: gates, budget, human-review flag.

Tradeoff: replace `_WEIGHTS` with the 12 package weights vs add a projection layer.

- Replacing `_WEIGHTS` gives conceptual alignment but risks destabilizing the hill-climb metric already calibrated in tests.
- A projection layer gives user-facing package compatibility while preserving the optimized internal signal.

Chosen: projection layer. Use `rubric_score()` for epoch keep/discard; use `rubric_scorecard_100()` for publish/readiness reporting.

Acceptance tests:

- Existing `test_rubric.py` keeps passing.
- A good fixture maps above 80/90 depending on hard fails.
- A thin fixture maps below 70 or is blocked by missing evidence.

## Workstream D: Intake, Research Brief, And Generic Report Profile

Goal: make topic, audience, purpose, report type, constraints, expected depth, and deliverable format explicit instead of buried in a free-form requirement. This is the main mechanism that keeps the system generic.

Implementation:

1. Extend `LoopConfig` or add `ResearchConfig`.
   - `topic`
   - `audience`
   - `purpose`
   - `report_type`: `"technical" | "market" | "policy" | "academic" | "competitive" | "literature_review" | "product" | "general"`
   - `depth`: `"brief" | "standard" | "deep"`
   - `format`: `"markdown" | "html" | "bundle"`
   - `source_policy`: source count, recency requirement, authority preference, allowed/disallowed domains.
   - `output_policy`: diagrams/code/tables/checklists allowed or required.
   - `constraints`, `human_review_required`.
   - Frontend: add compact controls in `LoopConfigPanel`.

2. Inject the research brief into planning and worker prompts.
   - The package requires intake before ToC/research questions. In Studio, use the brief as a structured prompt prefix for `_plan_from_epics`, `_build_skeleton`, and `_build_executor_prompt`.

3. Add report-profile routing.
   - Map `report_type` to a template preset and quality checks.
   - Technical reports may enable code/diagram sections.
   - Market/competitive reports emphasize market sizing, competitors, pricing, risks, and source recency.
   - Policy reports emphasize stakeholders, legal/regulatory context, impacts, tradeoffs, and uncertainty.
   - Academic/literature reviews emphasize methodology, prior work, evidence quality, disagreements, and references.
   - General reports use a compact default outline.

4. Keep code examples and diagrams optional.
   - Do not require runnable code, pseudocode, architecture diagrams, or implementation blueprints unless the selected report profile or user request calls for them.
   - If the model generates code for a non-code report, lint should mark it as off-scope unless explicitly requested.

Tradeoff: separate `ResearchConfig` vs fold fields into `LoopConfig`.

- Separate `ResearchConfig` is cleaner because not every loop is a research report.
- Folding into `LoopConfig` is faster but makes generic loop config more report-specific.

Chosen: separate optional `ResearchConfig` attached to `Session`. Default empty so non-research tasks keep current behavior.

Acceptance tests:

- Empty config preserves existing session creation behavior.
- Audience/purpose appear in generated skeleton or planning prompt.
- Research config persists into `RunSnapshot` for export.
- Different report profiles select different default sections without code changes.
- Code/diagram requirements are absent for non-technical profiles unless requested.

## Workstream E: Research Package Export

Goal: export not only a loop definition but a complete research-report bundle like the downloaded package.

Scope note: this workstream is the minimal export spine. Workstream L extends the same `report_export.py` module into the full enhanced bundle. Do not create a second exporter for Workstream L.

Implementation:

1. Add `backend/studio/report_export.py`.
   - Input: `RunSnapshot`, final markdown, evidence matrix, scorecard, publish gate result, loopdoctor checks.
   - Output files:
     - `research_report.md`
     - `evidence_matrix.md`
     - `scorecard.json`
     - `human_review_checklist.md`
     - `run_manifest.json`
     - optional `loop.json` from existing `run_to_loop()`

2. Add `GET /export/{session_id}/research-package` or extend `/export` with `?format=research_package`.

Tradeoff: ZIP export vs JSON bundle response.

- ZIP is closer to the package and easier for users to download/share.
- JSON is simpler in FastAPI tests and frontend rendering.

Chosen: implement a JSON manifest plus file contents first; add ZIP once the bundle schema is stable. This keeps tests simple and avoids binary response edge cases during early iteration.

Acceptance tests:

- Fresh session returns 409.
- Finished run with evidence returns all required files.
- Bundle manifest includes package version, run metadata, scorecard, and evidence counts.

## Workstream F: Human Review Workflow

Goal: expose the package's "do not publish high-impact reports without review" rule.

Implementation:

1. Add `human_review_required` and `review_status` fields.
2. Generate a checklist from hard fails, weak evidence, high-impact categories, and package checklist items.
3. UI: show a review checklist block with explicit "not reviewed" state; do not call it publish-ready until reviewed when required.

Tradeoff: enforce a blocking manual approval state vs just export the checklist.

- Blocking approval is safer for high-impact domains but requires user interaction/state.
- Export-only is easier but does not enforce the package's policy.

Chosen: start export-only plus visible `REVIEW_REQUIRED` decision for high-impact or unsafe domains. Add a manual approval action later if users need workflow enforcement.

Acceptance tests:

- Legal/medical/security/investment keywords or explicit config set `REVIEW_REQUIRED`.
- Review-required result cannot be `PUBLISH_READY` even with score >= 90.

## Workstream G: Diagram And Code Quality Checks

Goal: cover the package rubric categories for diagrams/tables/code without overbuilding.

Implementation:

1. Extend `artifact_lint.py`.
   - Add table sanity checks: malformed markdown table separators, empty comparison tables.
   - Add diagram checks: require explanatory prose near mermaid blocks.
   - Add code-block checks: detect language, dependency hints, obvious syntax for Python snippets.

2. Optional later: execute fenced Python blocks in a temp sandbox only when explicitly safe.

Tradeoff: deterministic static checks vs executing code examples.

- Static checks are fast, safe, and fit current offline tests.
- Execution catches real breakage but raises sandboxing, dependency, and runtime-cost issues.

Chosen: static checks first. Add execution only behind an explicit code-review gate.

Acceptance tests:

- Broken mermaid and unbalanced fences still flagged.
- Empty tables and unexplained diagrams flagged.
- Pseudocode-labelled blocks are not treated as broken runnable code.

## Workstream H: Source Quality And Corroboration

Goal: implement "never trust one source" without forcing every claim into costly duplicate retrieval.

Implementation:

1. Add source-type inference:
   - official docs, academic papers, standards, vendor docs, blog/article, forum/social, unknown.
   - Use URL host and optional metadata from findings.

2. Add claim-level corroboration:
   - Normalize claim text from evidence items.
   - For claims used in major sections, require either two independent URLs or one high-reliability primary source.

Tradeoff: require two sources for every important claim vs graded reliability.

- Two-source rule is simple but can punish primary-source facts where one official source is enough.
- Graded reliability matches package language ("when possible") and avoids unnecessary searches.

Chosen: graded reliability: primary/official/acad source can verify alone; blog/social/unknown needs corroboration for major claims.

Acceptance tests:

- One official source can be `verified`.
- One blog source for a major recommendation is `partially_supported` or hard-fail candidate.
- Two independent non-primary sources can corroborate.

## Workstream I: Add Research Report Loop And Skill Through Existing Loop Search/Seed

Goal: make the downloaded research-report workflow discoverable and seedable through the current Loops panel and `/loops` + `/session/{id}/seed` path.

Current code:

- `backend/studio/loops.py` loads the Forward Future catalog, matches a requirement by token overlap against each loop's title/description/useWhen/keywords, and adapts a loop's flat `steps[]` into `session.seed_steps` as a linear DAG.
- `backend/studio/app.py` exposes `GET /loops?requirement=...`, `POST /session/{session_id}/seed`, and also accepts `loop_id` during `POST /session`.
- `backend/studio/runner.py` consumes `session.seed_steps` before LLM planning: seeded sessions call `plan(_plan_requirement, decomposer=make_seeded_decomposer(seed_steps))` and emit `LoopSeedEvent`.
- `frontend/src/components/panels/LoopsPanel.tsx` already searches loops and calls `seedLoop(sessionId, loopId)`.
- `backend/studio/skills_paths.py` currently defines only five loop-library path skills (`discover`, `find`, `loop-doctor`, `adapt`, `design`). `/skills` lists these for display; it does not yet expose domain-specific skills like research-report generation.

Decision: add a local overlay catalog and local domain skills, not a separate "research package" seeding endpoint.

Option A: Add a new endpoint such as `/research-report/seed`.

- Pros: fast to implement and can use the downloaded package directly.
- Cons: duplicates the existing `/loops` search and seed UX, bypasses `LoopSeedEvent`, and creates a second seeding path that the runner does not need.

Option B: Merge local loops into `CatalogClient` as an overlay before matching.

- Pros: reuses `/loops`, `/session/{id}/seed`, `make_seeded_decomposer`, LoopsPanel, store state, tests, and seeded runner path. The research report loop becomes just another loop, which is exactly how the current UI is designed.
- Cons: `CatalogClient` must distinguish remote catalog loops from local loops and avoid slug collisions.

Chosen: Option B.

Implementation:

1. Add a local loop definition file, for example `backend/studio/local_catalog/loops/research-report-agent.json`.
   - Shape should match the real catalog loop schema enough for `CatalogClient`: `slug`, `title`, `description`, `useWhen`, `category`, `steps`, `keywords`, `verification`, `why`.
   - Suggested slug: `research-report-agent`.
   - Category: `{"slug": "research", "label": "Research"}`.
   - Steps should encode the package loop:
     1. Intake and scope the topic, audience, purpose, constraints, and format.
     2. Draft ToC, research questions, source requirements, and deliverables.
     3. Retrieve authoritative sources and record source notes.
     4. Verify claims and build an evidence matrix.
     5. Draft the report with analysis, tables, diagrams/examples where useful.
     6. Apply quality scoring and hard-fail checks.
     7. Revise until score/publish gate passes or blockers are explicit.
     8. Package report, evidence matrix, scorecard, limitations, references, and review checklist.

2. Extend `CatalogClient.load` or `CatalogClient.__init__` to merge local loops with remote/stale loops.
   - Add `LOCAL_LOOP_DIR = Path(__file__).parent / "local_catalog" / "loops"`.
   - Add `_load_local_loops(path) -> list[dict[str, Any]]`.
   - Merge local loops after remote loops so local definitions can override by slug if needed.

3. Update matching and lookup.
   - Ensure `CatalogClient.get("research-report-agent")` returns the local loop.
   - Ensure `CatalogClient.find("write a cited research report about ...")` ranks it highly by keywords such as `research`, `report`, `evidence`, `citations`, `sources`, `analysis`, `limitations`, `scorecard`, `package`.

4. Update tests.
   - Add fixture-independent test that `CatalogClient(data={"loops": []})` includes or can include the local research loop depending on constructor flag.
   - Add `test_find_research_report_local_loop`.
   - Add `test_seed_research_report_loop_builds_valid_plan`.

Tradeoff: auto-load local loops in every `CatalogClient(data=...)` vs only in `CatalogClient.load()`.

- Auto-loading in `__init__` makes production and tests consistent, but existing tests that expect fixture-only counts/rankings may shift.
- Loading only in `load()` avoids test churn, but a test-created client would miss the local loop unless it opts in.

Recommended: add an `include_local: bool = True` constructor flag, and set tests that require fixture-only behavior to `False` if needed. This keeps production behavior simple while preserving precise offline tests.

Skill integration:

1. Add a domain-specific research report skill alongside path skills.
   - Either create `backend/studio/skills_research.py`, or extend `skills_paths.py` with a separate `build_domain_skills()`.
   - Use the package's `01_skill/research-report-agent.skill.md` as source material, but condense it into an `agentkit.skills.core.Skill` body that tells the agent to use Studio's loop seed, evidence matrix, rubric, publish gate, and package export.

2. Change `/skills` to return both path skills and domain skills.
   - Keep the existing five path skills unchanged.
   - Add `research-report-agent` with trigger text for "research report", "technical deep dive", "literature review", "comparison report", "evidence-backed report".

3. Optionally register domain skills into `SkillLibrary`.
   - Current `/skills` only displays skill names/descriptions from `build_path_skills()`. It does not retrieve or invoke skills.
   - If later agents need semantic skill retrieval, add `register_all_skills(library)` that saves both path and domain skills.

Tradeoff: add the research report workflow as a loop, a skill, or both.

- Loop only: directly solves search/seed/run. Best for execution.
- Skill only: useful instruction, but the current UI does not seed a run from skills.
- Both: loop handles execution; skill documents/routs the workflow and can be retrieved by agents.

Chosen: both, but loop first. The loop is what makes "Seed this run" work today; the skill is discoverability and future retrieval.

## Workstream J: Catalog Management For Local And Remote Loops/Skills

Goal: turn loops and skills from mostly read-only discovery aids into manageable catalog assets. Users should be able to inspect, add, edit, enable/disable, import/export, and seed from both local and remote loops, and should be able to manage local/domain skills alongside remote/path skills.

Current state:

- Remote loop catalog is hardcoded to `CATALOG_URL` in `backend/studio/loops.py`.
- Remote loops are cached at `~/.cache/agentkit-studio/loop_catalog.json` with a 24-hour TTL.
- `CatalogClient.find()` searches remote catalog data by token overlap.
- `CatalogClient.adapt()` converts a flat loop's `steps[]` into seed steps.
- Local loops do not yet exist as a first-class concept.
- `/skills` lists hardcoded path skills from `build_path_skills()` only; skills are not editable, searchable, source-tagged, or persisted through a Studio catalog API.
- Frontend Loops panel supports search and seed, but not catalog CRUD, source selection, import, validation, or skill management.

### Data Model

Add a catalog asset model shared by loops and skills.

Loop fields:

- `id`: stable slug.
- `source`: `"remote" | "local"`.
- `origin`: URL, local file path, or `"builtin"`.
- `title`, `description`, `useWhen`, `prompt`, `category`, `steps`, `keywords`, `verification`, `why`.
- `enabled`: local setting; disabled assets do not appear in search.
- `editable`: true for local assets, false for remote cache unless copied locally.
- `updated_at`, `created_at`.
- `version`: optional, for local authoring.
- `validation`: last validation result.

Skill fields:

- `name`: stable skill id.
- `source`: `"builtin" | "local" | "remote"`.
- `origin`: file path, remote URL, or bundled module.
- `description`, `trigger`, `body`, `source_task`.
- `enabled`, `editable`, `updated_at`, `validation`.

Storage:

- Local loops: `backend/studio/local_catalog/loops/*.json`.
- Local skills: `backend/studio/local_catalog/skills/*.json` using `agentkit.skills.core.SkillLibrary`'s JSON shape where possible. If a user imports `.skill.md`, parse and normalize it into `Skill(name, description, trigger, body, source_task)` rather than treating Markdown as a separate primary store.
- Remote catalog metadata/config: `backend/studio/local_catalog/sources.json`, with entries like:
  - `id`
  - `kind`: `"loop_catalog" | "skill_catalog"`
  - `url`
  - `enabled`
  - `ttl_s`
  - `last_fetch_at`
  - `last_status`

Tradeoff: store local catalog under repo vs user cache.

- Repo storage makes assets versionable and reviewable, good for checked-in curated loops like `research-report-agent`.
- User cache/config avoids dirtying the repo for personal experiments.
- Recommended: support both. Repo-bundled assets live in `backend/studio/local_catalog`; user-created assets default to `~/.config/agentkit-studio/catalog` unless the app is in developer mode. For this repo, first implementation can use repo storage because the user explicitly wants a plan for improving the current project.

### Backend API

Add `backend/studio/catalog_mgmt.py` and route handlers in `backend/studio/app.py`.

Read APIs:

- `GET /catalog/sources`
  - Returns configured remote/local sources with status and counts.
- `GET /catalog/loops?source=all|local|remote&enabled=true|false|all&q=...`
  - Returns normalized loop assets, not only match results.
- `GET /catalog/loops/{loop_id}`
  - Returns full loop JSON plus source/editability/validation.
- `GET /catalog/skills?source=all|builtin|local|remote&enabled=true|false|all&q=...`
  - Returns normalized skill assets.
- `GET /catalog/skills/{skill_name}`
  - Returns full skill body plus metadata.

Loop write APIs:

- `POST /catalog/loops`
  - Create a local loop. Body uses catalog schema fields.
  - Validate before saving.
- `PUT /catalog/loops/{loop_id}`
  - Update a local loop.
  - Reject remote loop edits with 409 and suggest copy-local.
- `POST /catalog/loops/{loop_id}/copy-local`
  - Copy a remote loop into local editable storage.
- `POST /catalog/loops/{loop_id}/enable`
  - Enable/disable local or remote assets by writing an overlay setting.
- `DELETE /catalog/loops/{loop_id}`
  - Delete local loop or hide remote loop via disabled overlay.
- `POST /catalog/loops/import`
  - Import loop JSON, validate, save local.
- `GET /catalog/loops/{loop_id}/export`
  - Export loop JSON.
- `POST /catalog/loops/{loop_id}/validate`
  - Return validation details without saving.

Skill write APIs:

- `POST /catalog/skills`
  - Create a local skill.
- `PUT /catalog/skills/{skill_name}`
  - Update a local skill.
- `POST /catalog/skills/{skill_name}/copy-local`
  - Copy builtin/remote skill into local editable storage.
- `POST /catalog/skills/{skill_name}/enable`
  - Enable/disable by overlay.
- `DELETE /catalog/skills/{skill_name}`
  - Delete local skill or hide builtin/remote skill via overlay.
- `POST /catalog/skills/import`
  - Import `.skill.md` or JSON skill form.
- `GET /catalog/skills/{skill_name}/export`
  - Export skill as `.skill.md` and/or JSON.
- `POST /catalog/skills/{skill_name}/validate`
  - Validate required fields: name, description, trigger, body.

Remote source APIs:

- `POST /catalog/sources`
  - Add remote loop/skill catalog URL.
- `PUT /catalog/sources/{source_id}`
  - Enable/disable/update TTL.
- `POST /catalog/sources/{source_id}/refresh`
  - Force fetch now; return count/status.
- `DELETE /catalog/sources/{source_id}`
  - Remove source and its cached assets.

Tradeoff: reuse `/loops` and `/skills` vs introduce `/catalog/*`.

- Reusing `/loops` keeps API small but overloads search with management and makes UI state messy.
- `/catalog/*` cleanly separates catalog inventory/CRUD from run-time search/seed.
- Recommended: keep `/loops` and `/session/{id}/seed` as runtime APIs; add `/catalog/*` for management. `/loops` should search the enabled normalized catalog assembled by catalog management.

### Catalog Resolver

Refactor `CatalogClient` into two layers:

1. `CatalogRepository`
   - Loads remote cache, bundled local loops, user local loops, and enabled/disabled overlays.
   - Normalizes schemas.
   - Resolves conflicts.

2. `CatalogClient`
   - Uses repository data for `find`, `get`, and `adapt`.
   - Remains deterministic and testable.

Conflict resolution:

- Same slug local and remote:
  - local wins in search/get.
  - response includes `overrides_remote: true`.
  - UI shows "local override".
- Same slug from two remote sources:
  - prefer highest-priority source from `sources.json`.
  - UI shows conflict warning.

Validation:

- Loop must have non-empty `slug/title/description/useWhen/steps`.
- Steps must be non-empty strings.
- Optional `verification.title/detail` should exist before publishing/export.
- Skill must have non-empty `name/description/trigger/body` and load/save cleanly through `SkillLibrary`.
- Slugs/names must match safe filename regex: lowercase alnum plus hyphen for loops; skill names lowercase alnum plus hyphen/underscore if existing `SkillLibrary` accepts it.

### Frontend UI

Add catalog management inside the existing Loops panel or as a new panel tab.

Recommended UI structure:

- Keep current `LoopsPanel` search/seed as the default "Find" view.
- Add a segmented control or tabs inside the panel:
  - `Find`
  - `Loops`
  - `Skills`
  - `Sources`

Find view:

- Existing search box and results.
- Add source filters: `All`, `Local`, `Remote`.
- Add badges on results: `local`, `remote`, `disabled`, `override`.
- Existing "Seed this run" remains the primary action.
- Add secondary actions:
  - `View`
  - `Copy local`
  - `Export`

Loops management view:

- Dense list/table:
  - title
  - source
  - category
  - enabled
  - validation status
  - updated
  - actions
- Actions:
  - `New loop`
  - `Import`
  - `Refresh remote`
  - row: `Edit`, `Duplicate`, `Enable/Disable`, `Validate`, `Seed`, `Delete/Hide`, `Export`
- Editor drawer/modal:
  - slug
  - title
  - description
  - useWhen
  - prompt
  - category slug/label
  - keywords editor
  - steps editor with add/remove/reorder
  - verification title/detail
  - why
  - validate/save buttons

Skills management view:

- Dense list/table:
  - name
  - source
  - trigger
  - enabled
  - validation
  - actions
- Actions:
  - `New skill`
  - `Import .skill.md`
  - `Edit local`
  - `Copy local`
  - `Enable/Disable`
  - `Validate`
  - `Export`
- Editor:
  - name
  - description
  - trigger
  - body markdown textarea
  - source_task
  - validation preview

Sources view:

- Remote source list:
  - source id
  - kind
  - URL
  - enabled
  - TTL
  - last refresh
  - status
  - asset count
- Actions:
  - `Add source`
  - `Refresh`
  - `Enable/Disable`
  - `Remove`
- Show fetch errors explicitly, but keep stale cache usable.

Design details:

- Use existing panel styling (`PanelShell`, `panel-row`, `card`) but keep management tables dense.
- Avoid nested cards. Use rows and drawers/modals.
- Use existing button classes. Add icons only if the project already uses an icon package; otherwise keep text buttons for now.
- Do not block current search/seed flow on management API failures.

### Remote Skills

There is no current remote skill catalog schema in the code. Add support in two stages:

Stage 1:

- Manage builtin and local skills only.
- `/catalog/skills` returns path skills plus local/domain skills.
- Import `.skill.md` by parsing YAML front matter plus markdown body.
- Save imported skills through `SkillLibrary` so Studio stores the normalized JSON/Markdown pair, not a separate Markdown-only format.

Stage 2:

- Add remote skill catalog source support once a schema is chosen.
- Recommended remote skill catalog shape:
  - `schemaVersion`
  - `name`
  - `updated`
  - `skills[]`
  - each skill: `name`, `description`, `trigger`, `body`, `source_task`, `version`, `keywords`.

Tradeoff: invent remote skill schema now vs wait.

- Inventing now enables symmetrical management but risks locking into an unproven format.
- Waiting lets local skill management ship sooner and avoids fake compatibility.
- Recommended: implement local/builtin skills now; design remote skill source interface but mark it experimental until a real remote schema exists.

### How Research Report Assets Fit

Add research-report assets as managed local catalog entries:

- Local loop:
  - `backend/studio/local_catalog/loops/research-report-agent.json`
  - visible in `Find` results for research/report/evidence/citation queries.
  - editable in Loops management view.
  - seedable through existing `Seed this run`.

- Local skill:
  - `backend/studio/local_catalog/skills/research-report-agent.json`, loaded/saved through `SkillLibrary`.
  - visible in Skills management view.
  - can be copied/exported.
  - body should reference Studio-specific mechanisms: local loop seed, evidence matrix, rubric, publish gate, report package export.

### Tests

Backend tests:

- `test_catalog_repository_loads_remote_and_local_loops`.
- `test_local_loop_overrides_remote_slug`.
- `test_disabled_loop_excluded_from_find`.
- `test_create_update_delete_local_loop`.
- `test_copy_remote_loop_to_local`.
- `test_import_export_loop_roundtrip`.
- `test_validate_loop_rejects_empty_steps`.
- `test_catalog_skills_lists_builtin_and_local`.
- `test_import_skill_markdown_roundtrip`.
- `test_disable_builtin_skill_hides_from_catalog_but_does_not_delete_source`.
- `test_remote_source_refresh_uses_stale_cache_on_failure`.

Frontend tests:

- Store accepts catalog inventory payloads.
- Find view filters local/remote.
- Seed button still calls existing `seedLoop`.
- Loop editor validates required fields before save.
- Skill editor imports/exports body without truncation.
- Disabled asset badge renders and seed is disabled for disabled loops.

Migration plan:

1. Add repository/normalizer read path with no UI changes.
2. Point existing `/loops` search to normalized enabled catalog.
3. Add local research-report loop and prove it can seed.
4. Add `/catalog/loops` read-only list and UI inventory.
5. Add loop CRUD/import/export.
6. Add local/builtin skill inventory.
7. Add skill CRUD/import/export.
8. Add source management and forced refresh.

## Workstream K: Validation-First Tool Contracts, Observations, And Checkpoints

Goal: implement the enhanced report's validation-first recommendation as an inspectable, domain-neutral runtime contract: reasoning proposes work, orchestration validates and executes allowed tools, every result becomes a structured observation, and checkpoints make the run recoverable and auditable.

Current code already has pieces:

- `ToolAugmentedClient` owns tool schemas and dispatch for web search/fetch/read/write/read_artifact/patch_artifact.
- The runner emits `tool_call`, `tool_result`, `gate`, `token`, `phase_done`, `verify`, and loop-doctor style events.
- Workspace artifacts and `TaskRunStore` persist final run results and weaknesses.

Missing product contract:

- There is no single `Observation` object that records pre-validation, execution result, post-validation, retry/escalation decision, and checkpoint id.
- Checkpoints are not exported as a user-readable trace.
- Stop conditions are distributed across budget, cancel, hill-climb, and phase loop logic instead of being summarized in one run-level stop report.

Implementation:

1. Add `backend/studio/observations.py`.
   - Define `ToolObservation`:
     - `id`, `session_id`, `step_id`, `tool`, `args_redacted`, `allowed`, `requires_approval`, `status`, `message`, `result_summary`, `validation_status`, `validation_issues`, `retry_count`, `ts`.
   - Define `Checkpoint`:
     - `id`, `session_id`, `phase_id`, `artifact_path`, `evidence_count`, `score`, `weakness_count`, `observation_ids`, `ts`.
   - Add `summarize_tool_result()` to avoid dumping large web pages or secrets.

2. Instrument `ToolAugmentedClient`.
   - Before dispatch: validate tool name, required args, URL/file/path constraints, and whether action requires approval.
   - After dispatch: classify result as `ok`, `empty`, `error`, `unsafe`, `unverified`, or `retryable`.
   - Emit/store a `ToolObservation` for each call.
   - Preserve existing `tool_call`/`tool_result` SSE events for UI compatibility.

3. Add checkpoint creation after each major stage:
   - after plan creation,
   - after each phase,
   - after reducer writeback,
   - after verify/publish gate,
   - after package export.

4. Add a `stop_report`.
   - Track why the loop stopped:
     - `validation_passed`,
     - `max_epochs_reached`,
     - `budget_exceeded`,
     - `cancel_requested`,
     - `repeated_tool_failure`,
     - `approval_required`,
     - `no_meaningful_progress`,
     - `error`.
   - Include counts: tool calls, retries, failed validations, checkpoints, elapsed time, token cost.

Tradeoff: persist observations in SQLite vs JSONL files in the workspace.

- SQLite makes querying and UI panels easier.
- JSONL is simple, append-only, easy to export, and robust while the schema evolves.
- Recommended first step: write `agent_trace.jsonl` and `checkpoints.jsonl` in the session workspace, then add SQLite indexing only if the UI needs cross-run analytics.

Acceptance tests:

- Unknown tool produces an observation with `allowed=false` and does not execute.
- Tool error becomes `status="error"` and run can continue or stop according to policy.
- A phase checkpoint records artifact path, observation ids, and evidence count.
- Stop report is emitted/exported for budget, cancel, and validation-pass paths.

## Workstream L: Full Enhanced Report Bundle Export

Goal: match the enhanced report's bundle standard: a finished research run should be exportable as a complete shareable package, not only a Markdown report or loop JSON.

Required bundle files:

- `research_report.md`: editable source report.
- `research_report.html`: browser-friendly rendered report.
- `research_report.pdf`: polished PDF when local renderer is available.
- `evidence_matrix.md` and `evidence_matrix.json`: structured evidence.
- `scorecard.json`: 100-point score, hard fails, category scores, publish decision.
- `human_review_checklist.md`: review tasks and unresolved risks.
- `run_manifest.json`: session id, requirement, model/backend, costs, timestamps, source counts, exported files.
- `agent_trace.jsonl`: compact observations/tool calls/validation decisions.
- `checkpoints.jsonl`: phase-level recovery/audit checkpoints.
- `source_notes.json`: normalized source notes used by the report.
- `requirements.txt`: runtime dependency note for any generated demo code.
- Optional diagrams:
  - `agent_architecture.png`
  - `enhancement_timeline.png`
  - plus original Mermaid blocks in Markdown.
- Optional practice code:
  - `research_agent_demo.py`
  - `research_agent_demo.pseudo`
  - `research_agent_demo_output.txt`

Implementation:

1. Extend `backend/studio/report_export.py`.
   - Add `build_research_bundle(snapshot, evidence, scorecard, publish_gate, observations, checkpoints)`.
   - Return a manifest plus file-content mapping initially.

2. Add renderers.
   - Markdown to HTML: use a Python markdown library if already available, otherwise a minimal safe renderer with fenced-code/table support.
   - Markdown to PDF: prefer optional Pandoc if installed; otherwise return `pdf_status="unavailable"` and keep HTML/Markdown complete.
   - Diagrams: keep Mermaid source in Markdown; optionally render static PNG through a later renderer. Do not block export if PNG rendering is unavailable.

3. Add practice-code bundle generation.
   - Generate a small deterministic `research_agent_demo.py` that mirrors Studio concepts:
     - planner,
     - orchestrator/tool registry,
     - observation,
     - validation,
     - checkpoint write,
     - final report write.
   - Generate matching `.pseudo`, sample `source_notes.json`, and sample output.
   - Keep this opt-in or include it only for research/education exports so ordinary task exports remain concise.

Tradeoff: always produce PDF/PNG vs best-effort optional renderers.

- Always requiring PDF/PNG would make exports brittle on machines without Pandoc/diagram tooling.
- Best-effort renderers keep the core artifact reliable and report missing optional formats clearly.
- Recommended: core files must always export; PDF/PNG are optional with status in `run_manifest.json`.

Acceptance tests:

- Bundle includes all required core files.
- HTML export contains report headings and tables.
- PDF unavailable path is explicit and non-fatal.
- Demo code runs offline and writes expected sample files.
- Manifest records renderer status and every file name.

## Workstream M: Evaluation Metrics And Stop-Condition Dashboard

Goal: make agent behavior measurable, following the enhanced report's metrics section.

Metrics to compute per run:

- Task success rate: publish gate pass or goal met.
- Tool-call accuracy: successful validated tool calls / total tool calls.
- Validation catch rate: invalid/empty/unsafe outputs caught / invalid outputs observed.
- Citation accuracy: verified evidence items / cited evidence items.
- Cost per task: tokens, model calls, tool calls, wall time.
- Human intervention rate: approval requests, review-required decisions, user cancellations.
- Recovery rate: successful retries / failed first attempts.
- Stop-condition reason: from the stop report in Workstream K.

Implementation:

1. Add `backend/studio/run_metrics.py`.
   - Input: observations, evidence, publish gate result, token accounting, checkpoints, stop report.
   - Output: `RunMetrics`.

2. Emit `metrics` SSE event after publish gate and before `done`.
   - Frontend stores metrics and displays compact cards in Result/Loop Doctor area.

3. Persist metrics in `TaskRunStore`.
   - Store `metrics_json` for each run.
   - Future: aggregate by task hash to show improvement over epochs.

Tradeoff: calculate metrics live in runner vs post-process from trace.

- Live calculation is simple but can drift from exported trace if events change.
- Post-processing from observations/checkpoints guarantees reproducibility.
- Recommended: derive metrics from stored observations/checkpoints at post-run time.

Acceptance tests:

- Metrics are deterministic for a synthetic trace.
- Tool-call accuracy and citation accuracy handle zero denominators.
- Stop reason appears in metrics and export manifest.

## Workstream N: Generic Template Presets And Editorial Cleanup Gates

Goal: make report structure profile-driven. The enhanced report's technical-agent structure should become one selectable preset, while the generic research-report preset remains compact and topic-neutral.

Generic default template:

1. Executive Summary
2. Scope and Research Questions
3. Background and Context
4. Key Findings
5. Evidence and Analysis
6. Implications or Recommendations
7. Limitations and Uncertainty
8. References

Enhanced technical-report preset:

1. Executive Summary
2. Background and Scope
3. Key Concepts
4. Architecture Overview or Current State
5. Methodology
6. Implementation Blueprint
7. Practical Code or Example Walkthrough
8. Governance and Security
9. Evaluation and Metrics
10. Limitations and Open Questions
11. Reflection and Lessons Learned
12. Appendix: Code, Diagrams, Glossary, References

Implementation:

1. Do not blindly replace `DEFAULT_TEMPLATE`.
   - The current template is short and generic, which helps avoid topic leakage.
   - Add preset registry, for example `GENERIC_RESEARCH_TEMPLATE`, `ENHANCED_TECHNICAL_REPORT_TEMPLATE`, `MARKET_RESEARCH_TEMPLATE`, `POLICY_RESEARCH_TEMPLATE`, `LITERATURE_REVIEW_TEMPLATE`, and `COMPETITIVE_ANALYSIS_TEMPLATE`.
   - Expose template presets in the rubric/config UI.
   - Route automatically from `ResearchConfig.report_type`, with user override.

2. Keep static generic/profile presets in code, and use the SQLite template DB only
   for learned or explicitly approved reusable skeletons.
   - Current code validation: `backend/studio/templates.py` defines
     `TemplateStore(report_templates)` with `save_template()` and
     `find_template()`, and `backend/studio/runner.py` saves decent report
     skeletons after a run. However, the run path does not currently call
     `find_template()` for generation, and `_build_skeleton()` deliberately
     stopped semantic template reuse because prior topic-specific headings could
     leak into unrelated reports.
   - Therefore, `backend/studio/report_profiles.py` should own built-in default
     templates; `TemplateStore` should be treated as a managed template catalog,
     not the source of truth for generic defaults.
   - Add metadata columns or a sidecar table for DB templates:
     `template_id`, `name`, `report_type`, `source` (`learned|approved|imported`),
     `status` (`active|stale|disabled`), `quality_score`, `created_from_session`,
     `approved_by`, `last_used_at`, and `failure_reason`.
   - Only auto-select a DB template when all are true:
     report type matches, status is `active`, source is `approved` or quality
     score clears threshold, semantic similarity clears a high threshold, and
     deterministic lints find no duplicate headings/placeholders/off-profile
     sections.
   - If no DB template passes, fall back to the code profile preset.

3. Replace or quarantine original bad DB templates.
   - Add a maintenance command or API such as
     `POST /catalog/templates/audit` and `POST /catalog/templates/{id}/replace`.
   - Audit every row in `report_templates` with the same structural lints used
     for final reports:
     duplicate headings, placeholders, topic-specific leakage, unsolicited code
     sections, missing references, and broken/unverified links.
   - Mark bad rows `stale` or `disabled`; do not delete by default, because old
     run provenance may still need to explain where a skeleton came from.
   - Seed approved replacements for the generic and profile templates from
     `report_profiles.py`, or let the user promote a clean generated skeleton.
   - Rationale: replacing the DB contents directly is necessary if earlier
     experiments saved Pi/Craft or agent-framework-specific skeletons. Without
     quarantine, a future semantic match can reintroduce the exact topic drift
     `_build_skeleton()` was changed to avoid.

4. Add editorial lint checks:
   - repeated headings or near-duplicate sections,
   - scratchpad markers,
   - unverified popularity numbers,
   - vague unsupported phrases like "production-grade" without evidence,
   - missing glossary for acronym-heavy reports,
   - missing explanation near diagrams/code examples when diagrams/code are present or requested,
   - off-profile content, such as unsolicited code blocks in a market/policy/general report.

5. Add cleanup reducers.
   - Use existing citation-preserving rewrite functions, but gate them through citation/URL retention checks.
   - Never remove sourced facts or evidence items while de-duplicating prose.

Tradeoff: one universal template vs profile-specific presets.

- One universal template is easier to implement but over-structures some reports and under-structures others.
- Profile-specific presets require more routing but keep the generator generic across domains.
- Recommended: generic default plus profile presets. The local `research-report-agent` loop should choose the preset from intake, not always the enhanced technical preset.

Acceptance tests:

- Generic default remains compact and topic-neutral.
- Enhanced technical preset includes all 12 technical sections.
- Market/policy/literature-review presets do not include code sections unless requested.
- DB template audit disables skeletons with placeholders, duplicate headings, or off-profile sections.
- DB template auto-selection prefers active approved templates and falls back to code presets when no safe match exists.
- Replacing a stale DB template preserves provenance while preventing future automatic reuse.
- Duplicate heading lint catches repeated sections.
- Editorial cleanup does not drop citations.

## Workstream O: Weak-Model Report Quality Hardening

Goal: fix the concrete failure seen in `backend/tmp/studio-workspaces/s_9ef0b2a7bf46/artifact.md` and make the generic report pipeline robust when the active local model is weak at long-context instruction following, specifically `gemma-4-26B-A4B-it-heretic-4bit`.

Genericity guard:

- The bad artifact is a regression fixture, not a product template.
- The fix must work for any report type selected by `ResearchConfig`: technical, market, policy, academic, competitive, product, literature review, or general.
- Do not hardcode Pi/Craft, agent frameworks, code examples, or architecture sections into weak-model mode.
- Weak-model mode should constrain task size and output schema while preserving the selected report profile.

Observed failure in the generated artifact:

- Duplicate top-level sections: `Executive Summary`, `Design Architecture`, `Implementation Methodology`, and `Example Code Implementation` appear more than once.
- A dangling code fence/code fragment appears near the second `Example Code Implementation`; the original class definition is missing while trailing example lines remain.
- The first half of the report is generic, citation-free prose; the second half is a loose wall of appended evidence sentences.
- Some citations are malformed or explicitly unverified, for example `(unverified)` links and broken markdown link syntax.
- A placeholder reference section remains: "no specific URLs were provided".
- Evidence paragraphs repeat the same sources and phrases many times instead of being synthesized into sections.
- The final document violates the intended skeleton and reads like multiple partial outputs concatenated together.

Root causes in current code:

- `backend/studio/prompts.py` gives good instructions, but the system still trusts the model to obey strict output formats such as `RESEARCH_FINDING` and `PATCHES`. Gemma-class models often narrate, echo, or partially comply.
- `backend/studio/tools.py` allows up to `_MAX_TOOL_ITERS = 8`, which is useful for strong tool-using models but gives weaker models enough turns to drift, over-fetch, or forget the required output schema.
- `backend/studio/findings.py` can parse and ground findings, but prose that is not parseable as a finding can still leak into `outputs`, post-loop patch paths, or final selection logic.
- `backend/studio/artifact_text.py` has moving-window synthesis/readability, but those are late-stage passes. The bad artifact shows corruption happens before final readability: duplicated outlines, evidence append walls, and dangling code fragments.
- `backend/studio/artifact_lint.py` only checks malformed Mermaid edges and unbalanced code fences. It does not catch placeholder references, unverified links, repeated section blocks after normalization, citation walls, broken markdown links, or orphaned code fragments outside fences.
- `Runner._run_phase_loop` writes back phase output when `accept_rewrite` accepts it, but weak-model prose can be structurally accepted even when it is semantically not the assigned section work.
- The seeded loop path in `backend/studio/loops.py` injects abstract steps. For weak models, abstract steps are not enough; each step needs a small, explicit contract and bounded action count.

Design principle:

- Preserve the original architecture: LLM epic planning and automatic topology
  selection remain the default. Model profiles provide budgets, prompt hints,
  and validation thresholds; they should not silently replace the planner or
  hardcode product workflow branches.
- Make the planner smarter instead of replacing it: the planner prompt should
  ask the LLM to choose compact, standard, or staged report planning based on
  requested report size, evidence burden, risk, report profile, and user
  constraints.
- Treat weaker local models as needing stronger guardrails around action count,
  context size, and publish gates, while still letting the LLM reason about
  stage planning unless the user explicitly seeds a fixed loop/template.
- Move quality control earlier through typed evidence, moving windows, and
  deterministic publish gates; avoid hardcoded topic logic such as "simple
  report" or "weak LLM means use this exact plan."

### O1. Add Model Profiles For Budget And Prompt Hints

Add model profiles derived from model id and user/config overrides. These are
capability hints, not workflow overrides.

Implementation:

1. Add `backend/studio/model_profiles.py`.
   - `ModelProfile(name, weak_instruction_following, context_chars, max_tool_iters, max_actions_per_phase, section_window_chars, finding_batch_size, allowed_sections_from_profile=True)`.
   - Default profile for `gemma-4-26B-A4B-it-heretic-4bit`:
     - `weak_instruction_following = true`
     - `context_chars = 12000`
     - `section_window_chars = 6000`
     - `max_tool_iters = 4`
     - `max_actions_per_phase = 3`
     - `finding_batch_size = 3`
   - Strong/default profile can keep current values.

2. Resolve the profile in `Runner.__init__` or `_run_inner` from `session.llm_info` / backend model id.
3. Pass the profile into:
   - `ToolAugmentedClient`,
   - planner prompt construction as optional budget/schema guidance only,
   - worker/executor prompt construction,
   - section reducer,
   - post-run finalization.
4. Do not bypass `_plan_from_epics()` or automatic topology selection solely
   because the profile says weak instruction following. If a fixed report loop
   is desired, use the existing seed-loop/catalog path so the choice is explicit
   and observable via `LoopSeedEvent`.
5. Enhance `_build_planner_cot_prompt()` with report-depth heuristics:
   - compact/brief report: 1-2 epics;
   - standard sourced report: 3-4 epics, borrowing the methodology stages as
     guidance;
   - large/high-impact/disputed report: 4-5 epics with explicit verification,
     limitations, revision, and publish/human-review gates.
   The prompt must state these are heuristics, not hardcoded stages.

Tradeoff: hardcode Gemma-specific checks vs user-configurable profile.

- Hardcoding is fast but brittle across renamed local models.
- A model profile table plus override is slightly more work but reusable.
- Recommended: ship a small table keyed by substring match and allow config override.

Acceptance tests:

- Gemma model id resolves to weak profile.
- Unknown model id resolves to default profile.
- Weak profile lowers tool loop and action budgets without changing strong profile defaults.
- Gemma/profile resolution does not automatically bypass LLM epic planning.
- Planner prompt contains report-depth heuristics and still emits `EPIC_PLAN`.

### O2. Bound Actions Per Phase And Per Worker

For weak models, make the action budget explicit and enforce it in code.

Implementation:

1. Change `ToolAugmentedClient` to accept `max_tool_iters` instead of using only module-level `_MAX_TOOL_ITERS`.
2. Add `max_successful_fetches` and `max_searches` counters to the tool loop.
   - Weak profile: at most 1 search and 2 fetches per worker.
   - If exceeded, return a structured tool error like `ACTION_BUDGET_EXCEEDED`.
3. Add a short system/tool reminder after each tool result:
   - "You have N actions left. Final answer must be only RESEARCH_FINDING JSON lines."
4. If the model emits narration matching `_PLANNING_RE` twice in one phase, stop tool iteration and force a tools-disabled extraction prompt over already fetched pages.

Rationale:

- Weak models can ignore long instructions after several turns. A small action budget keeps the conversation within the window and forces synthesis while fetched evidence is still salient.

Acceptance tests:

- Weak profile stops after configured search/fetch budget.
- Strong profile preserves current behavior.
- Repeated narration triggers forced extraction instead of more tool turns.

### O3. Replace Free-Form Finding Blocks With JSONL For Weak Models

Keep current `RESEARCH_FINDING` parsing for compatibility, but use a stricter JSONL contract in weak-model mode.

Implementation:

1. Add a weak-model executor prompt variant in `backend/studio/prompts.py`.
   - Output contract:
     - exactly one JSON object per line,
     - keys: `url`, `title`, `quote`, `why`, `patch_target`,
     - no markdown,
     - no headings,
     - no prose outside JSON.
   - Include one valid example and one invalid example.
   - Keep the prompt under 1200 words.

2. Add `_parse_findings_jsonl(text)` in `backend/studio/findings.py`.
   - Parse only line-level JSON objects.
   - Reuse the same grounding oracle as `_parse_findings`.
   - Drop any line that lacks `url` or has no grounded URL/quote.

3. In weak-model mode, parse JSONL first, then fallback to current block parser.
4. Add a structured `format_violation_count` to observations/metrics.

Rationale:

- The bad artifact shows the model mixed prose, code, citations, and findings. JSONL is easier to validate and repair than loose markdown blocks.

Acceptance tests:

- Valid JSONL finding parses and grounds.
- Markdown prose outside JSONL is ignored in weak mode.
- Invalid JSON lines are counted but do not enter the report.

### O4. Use Moving Windows Before Assembly, Not Only During Final Polish

Apply moving-window discipline to worker inputs and reducer inputs.

Implementation:

1. In `Runner._run_phase_loop`, when `_hub_art_text` is built, replace `read_text()[:3000]` with a section-aware context selector.
   - Include only assigned/target sections plus a compact document outline.
   - Use `agentkit.artifacts.sections.split_sections`.
   - Cap at `profile.section_window_chars`.

2. In `_make_section_reducer`, do not inject full `CURRENT ARTIFACT` for weak models.
   - Pass only:
     - heading list,
     - target section bodies,
     - worker findings,
     - current weaknesses for those sections.
   - Let deterministic patching reassemble into the full artifact.

3. Add a helper `select_report_window(text, target_sections, max_chars)`.
   - Always include title and section headings.
   - Include full body only for target sections.
   - Include 1-2 neighboring sections if budget remains.

Rationale:

- Current final synthesis has moving-window protections, but the reducer prompt can still show too much current artifact and too many worker outputs. Weak models lose the schema when overloaded.

Acceptance tests:

- Window selector includes target sections and excludes unrelated long sections.
- Reducer prompt for weak profile stays under configured char budget.
- Full artifact is still assembled through deterministic patching.

### O5. Deterministic Section Assembly Before Any LLM Rewrite

For weak models, never let an LLM produce the full report body directly.

Implementation:

1. Add `backend/studio/report_assembler.py`.
   - Input: selected report profile/template, evidence items/findings grouped by `patch_target`.
   - Output: clean markdown sections with:
     - section heading,
     - 2-4 synthesized bullet facts or paragraphs,
     - citations attached to claims,
     - empty sections kept as explicit placeholders only before publish gate, not in final publish-ready output.

2. In weak-model mode:
   - worker -> JSONL findings,
   - parser -> grounded findings,
   - assembler -> section draft,
   - optional section-sized LLM rewrite,
   - deterministic final merge.

3. Keep the existing reducer for strong/default models, but allow the research-report local loop to select deterministic assembly.

Rationale:

- The bad artifact is a concatenation failure. Deterministic assembly prevents repeated outlines and evidence append walls.

Acceptance tests:

- Same evidence list always produces same section order.
- Duplicate URLs are merged before section output.
   - No prose outside the selected/approved outline appears in assembled report.

### O6. Add Report-Quality Lints For The Exact Failure Modes

Extend `backend/studio/artifact_lint.py`.

New deterministic checks:

1. Duplicate heading blocks after normalization.
   - Detect if `dedupe_sections` would materially change the report.

2. Placeholder references.
   - Flag phrases:
     - "no specific URLs were provided",
     - "placeholder for citations",
     - "to be completed",
     - "pending — needs sourced content".

3. Broken/unverified links.
   - Flag markdown links with `((unverified)`, empty URLs, malformed parentheses, or URL text containing `unverified`.

4. Citation wall.
   - Flag 3+ consecutive paragraphs starting with phrases like:
     - "This validates",
     - "This provides",
     - "This demonstrates",
     - "Establishes",
     - "Illustrates".

5. Orphaned code fragment.
   - Flag code-looking lines outside a fenced block when nearby code fence balance is broken or a section has `# Example usage` without a preceding fenced code start.

6. Citation-free core sections.
   - For research reports, flag evidence-bearing sections with >150 words and no URL/citation.
   - Do not require citations in purely navigational sections such as title, table of contents, glossary, or appendix labels.

Actionable behavior:

- Add these lints to post-run weaknesses.
- Publish gate must fail on placeholder references, malformed links, unbalanced fences, and unverified links in final report.
- In weak-model mode, if these lints remain after finalization, serve the grounded draft with a visible "not publish-ready" state rather than pretending the report is complete.

Acceptance tests:

- The known bad artifact path triggers duplicate-section, placeholder-reference, broken-link/unverified-link, citation-wall, and code-fragment lints.
- A clean cited report does not trigger these lints.

### O7. Add A Final Publish-Ready Assembler Gate

Before `DoneEvent`, run a deterministic final report gate.

Implementation:

1. Add `finalize_report_quality(text, profile, template, verified_urls)`.
2. Run in `_postrun_score_and_record` after `normalize_artifact`, `_repair_lints`, URL neutralization, and readability.
3. Gate rules:
   - no duplicate headings,
   - no placeholder reference text,
   - no unbalanced fences,
   - no malformed markdown links,
   - no `(unverified)` links,
   - all required template headings present or explicitly marked missing in publish gate,
   - citations retained after readability rewrite.
4. If the gate fails:
   - persist the report,
   - mark `publish_ready = false`,
   - add failure details to `PublishGateEvent`,
   - do not call the result "final publish-ready report".

Rationale:

- The existing `verify` and `loopdoctor` panels do not stop a visibly bad report from being presented as completed.

Acceptance tests:

- Bad artifact fails final gate.
- Clean artifact passes.
- Gate failure is persisted and export manifest includes the failure list.

### O8. Make The Generic Research-Report Loop Explicit As A Seedable Methodology

The local `research-report-agent` loop should not seed broad abstract steps only,
and it should not assume a technical-agent topic. It should be available through
catalog/seed-loop management as an explicit methodology choice, while ordinary
unseeded runs continue to use LLM epic planning and automatic topology selection.

Replace or augment the seeded loop steps with methodology-friendly steps:

1. `Scope`: produce JSON only: topic, report_type, audience, purpose, required sections, source policy, output policy, max sources per section.
2. `Plan sources`: produce a section-to-query table; max 2 queries per section.
3. `Fetch evidence batch`: for each assigned section, run at most 1 search and 2 fetches, output JSONL findings only.
4. `Validate evidence`: drop ungrounded findings; produce evidence matrix.
5. `Assemble sections`: deterministic assembler groups evidence by section.
6. `Section rewrite`: rewrite one section at a time, max 6000 chars, citations sacred.
7. `Lint and repair`: deterministic report lints plus block-level repair only.
8. `Publish gate and export`: fail closed if placeholders, broken links, or missing citations remain.

Profile-specific behavior:

- Technical profile may include code, diagrams, architecture, and implementation sections.
- Market/competitive profiles should use market size, segments, competitors, pricing, customer needs, risks, and recommendations.
- Policy profile should use stakeholders, legal/regulatory context, options, impacts, tradeoffs, and uncertainty.
- Academic/literature-review profile should use research questions, methodology, prior work, evidence quality, disagreements, and bibliography.
- General profile should keep a compact explanatory structure.

Rationale:

- A model should not be forced into this plan just because of its model id. The
  stage loop is useful when the user chooses the research-report methodology or
  when an intake/config explicitly requests fixed-stage generation. Keeping it
  in the catalog preserves compatibility with the original LLM-first planner.

Acceptance tests:

- Seeded research-report loop produces these steps.
- Each step includes an action budget and output schema.
- Final loop has no step that asks the model to echo a full long document.
- Same weak-model controls apply across at least two non-agent topics, for example market research and policy analysis.
- Unseeded LLM-mode report requests still go through `_plan_from_epics()` and automatic topology selection.

### O9. Add Regression Test Using The Bad Artifact

Add the generated bad artifact as a fixture or minimized fixture.

Implementation:

1. Add `backend/tests/fixtures/bad_report_duplicate_sections.md`, either copied from the observed artifact or minimized to preserve every failure class.
2. Add tests:
   - `test_bad_report_lints_detect_known_failures`.
   - `test_normalize_artifact_removes_duplicate_outline`.
   - `test_publish_gate_rejects_bad_report`.
   - `test_weak_model_profile_uses_small_windows_and_action_caps`.
3. Add one golden clean report fixture to prevent over-aggressive linting.

Rationale:

- The current system had many individual guards but still shipped this artifact. A regression fixture makes the real failure non-negotiable.

### O10. Audit Report-Generator Code For Genericity Before Landing Changes

Goal: make the standing generic-report rule enforceable. Each report-generator
implementation slice should prove that Studio source-code behavior remains
task-neutral before it is committed or promoted, while preserving the requirement
that generated reports be specific to the user's task and evidence.

Implementation:

1. Add a genericity audit checklist to every report-generator slice.
   - Check changed production files under `backend/studio`, `frontend/src`, and
     any reused shared helpers.
   - Look for fixed report prose in source code, fixed conclusions, topic-specific recovery
     drafts, one-off template sections, model-id branches that change report
     semantics, and hardcoded example-domain assumptions.
   - Allowed locations for topic-specific examples: `tests/`, fixtures, `ref/`,
     user input, and reviewed catalog/template data.

2. Add a small scanner or pytest helper.
   - Start with changed production files only to avoid noisy historical debt.
   - Flag suspicious string literals that look like complete report paragraphs,
     fixed example answers, or domain-specific fallback drafts.
   - Report file/line and phrase; do not auto-fix.
   - Maintain a short allowlist for generic product terms such as `template`,
     `citation`, `source`, `evidence`, `section`, `loop`, `skill`, and
     `catalog`.

3. Apply the audit to shared-library reuse.
   - If `/Users/yuxinliu/code/agentkit` or
     `/Users/yuxinliu/code/agent-prep/shared` code is reused or modified for
     this project, audit those helper changes too.
   - Shared helpers should expose generic primitives, policies, and adapters,
     not Studio's current example topics or validation fixtures.

4. Record the audit result in the worklog for each slice.
   - `pass`: no production genericity issues found.
   - `warn`: domain term remains with rationale because it is a generic product
     term or catalog/template data.
   - `fail`: required code or prompt change before the slice can land.

Tradeoff: manual checklist vs automated scanner.

- Manual review catches semantic lock-in and avoids false positives, but it is
  easy to skip during fast iterations.
- A scanner is incomplete and may need allowlists, but it catches obvious
  hardcoded fallback prose and makes the rule visible in local/CI checks.

Recommended: use both. Start with this checklist plus a conservative scanner
over changed production strings, then tighten the scanner as concrete failures
are discovered.

Acceptance tests:

- Scanner flags a production string that contains a fixed example report answer
  or topic-specific recovery draft in source code.
- Scanner ignores the same text in `tests/`, fixtures, and `ref/`.
- Scanner does not flag generic report-generator terms such as template,
  citation, source, evidence, section, loop, skill, and catalog by default.
- Every report-generator slice records whether the genericity audit passed,
  warned with rationale, or found required changes.

## Enhanced Report Coverage Checklist

The enhanced report's suggestions are addressed as follows:

- Complete downloadable bundle: Workstream L.
- PDF/HTML/static diagram support: Workstream L.
- Runnable practice code, pseudocode, source notes, sample output, requirements: Workstream L.
- Validation-first generic research loop: Workstream K.
- Tool allowlists, permissions, sandboxing, pre/post validation: Workstreams B, K, G.
- Structured memory, checkpoints, summaries, preferences: Workstreams A, K, J.
- Observability and audit trace: Workstreams K, M, L.
- Explicit stop conditions: Workstreams K and M.
- Evaluation metrics: Workstream M.
- Stronger source discipline and uncertainty: Workstreams A, B, H.
- Fewer unsupported claims and editorial cleanup: Workstreams N and O.
- Clear final report structure with glossary/appendix/references: Workstreams N and L.
- Human approval/review for high-impact actions: Workstreams F, B, K.
- Weak local model reliability, moving-window execution, action caps, and schema-first outputs: Workstream O.
- Regression prevention for the known bad generated report: Workstream O.
- Genericity audit for report-generator production code: Workstream O10.

## Feasibility And Shared-Library Reuse Audit

This pass double-checks the plan against the current Studio code plus reusable components in `/Users/yuxinliu/code/agentkit` and `/Users/yuxinliu/code/agent-prep/shared`.

Verdict: the plan is feasible, but implementation should be reuse-first. Studio already has the correct extension points for loops, seed steps, tools, findings, rubric scoring, persistence, and frontend panels. The main correction is to avoid parallel implementations of primitives that already exist in shared libraries.

### Reuse Requirements

1. Evidence model.
   - Existing component: `agentkit.artifacts.types.Finding`.
   - Use it as the base research-finding shape and add Studio-side metadata only for report-specific evidence fields such as `source_type`, `publication_date`, `reliability`, `used_in_sections`, and `publish_status`.
   - Rationale: `backend/studio/findings.py` already extracts and grounds `Finding` objects before patching artifacts. A separate evidence parser would duplicate the most reliable code path.

2. Artifact patching and section ownership.
   - Existing components: `agentkit.artifacts.patcher.DocPatch`, `reduce_patches`, `write_artifact`; `agentkit.artifacts.sections.split_sections`, `accept_rewrite`.
   - Use these for report/evidence edits and template-section reconciliation.
   - Rationale: current Studio already patches artifacts through `findings.py` and `artifact_text.py`. Reusing shared patch/section primitives keeps reducer behavior consistent.

3. Source popularity and source type.
   - Existing component: `agentkit.artifacts.metrics.fetch_metrics` and `source_kind`.
   - Use it for optional source enrichment before adding any new popularity fetcher.
   - Limitation: it currently covers arXiv citation count and GitHub stars only.
   - Shared-lib improvement: add a small extension interface, for example `MetricProvider`, and providers for generic web pages, PDFs, docs, and news pages when metadata is available. Keep failures as `None`.
   - Rationale: report evidence scoring needs source-quality hints, but it should degrade gracefully and not block publish gates when popularity is unavailable.

4. Skill catalog.
   - Existing component: `agentkit.skills.core.Skill` and `SkillLibrary`.
   - Catalog management should wrap `SkillLibrary` for local skill save/load/list/retrieve. It should not create an unrelated skill storage engine.
   - Limitation: `SkillLibrary` stores skills but has no enable/disable overlay, source/origin metadata, remote-source status, or `.skill.md` import parser.
   - Shared-lib or Studio improvement: keep enable/disable/source overlays in Studio catalog metadata; add a parser utility if `.skill.md` import becomes common.
   - Rationale: `SkillLibrary` already provides the validated skill object and paired JSON/Markdown persistence. Studio only needs management metadata and UI.

5. Gates and validation.
   - Existing component: `agentkit.gates.core.Gate`, `Outcome`, `Verdict`, and `run_gate`.
   - Use gates for validating local loop/skill changes and human-review escalation where a deterministic or sandboxed check is possible.
   - Limitation: report publish readiness is domain-specific and should not be folded into the generic LEARN gate.
   - Recommended split: generic gate validates assets and code/proposal safety; Studio `PublishGateEvent` validates report-specific criteria such as citations, evidence status, scorecard thresholds, unsafe actions, and human-review requirements.

6. Sandbox and execution safety.
   - Existing component: `agentkit.sandbox.core.SubprocessSandbox`, `Sandbox`, `ExecResult`, `is_within`, `MAX_OUTPUT_BYTES`.
   - Use `SubprocessSandbox` for optional demo-code checks, local validator commands, and catalog validation that executes anything. Do not use raw `subprocess` in new Studio validators.
   - Limitation: `DockerSandbox` is only a seam and raises `NotImplementedError`.
   - Shared-lib improvement: if stronger isolation is required, implement `DockerSandbox.run()` behind the existing protocol instead of adding a separate Studio sandbox.
   - Rationale: the shared sandbox already enforces argv-not-shell, cwd jail, timeout, and output caps.

7. Egress policy.
   - Existing component: `agentkit.sandbox.net_guard`.
   - Use it for user-configured remote catalog sources and any future backend/tool URL policy checks.
   - Limitation: `agent-prep/shared/web_toolkit/search.py` and `fetch.py` do not currently call `net_guard.assert_allowed`.
   - Shared-lib improvement: add optional egress enforcement to shared web toolkit, controlled by a parameter or environment flag, so Studio can block unapproved hosts without forking search/fetch.
   - Rationale: catalog management introduces remote URLs; egress checks should be centralized and deterministic.

8. Loop stop conditions and chain planning.
   - Existing components: `agentkit.loop.goal.LoopGoal`, `StopVerdict`, `check_goal`; `agentkit.loop.chain.LoopSpec`, `LoopChain`; `agentkit.loop.suggest.suggest_goal_params`, `suggest_chain_spec`.
   - Reuse these for explicit stop-condition UI, seed-loop generation, and multi-phase research-report loop composition where possible.
   - Limitations:
     - `check_goal()` uses raw `subprocess.run`, not `SubprocessSandbox`.
     - `LoopChain` accepts Python callables and is not directly serializable as a catalog JSON asset.
   - Shared-lib improvements:
     - Add a sandbox-backed option to `check_goal(goal, sandbox=SubprocessSandbox())`.
     - Add a serializable chain-spec schema or adapter so Studio can store loop DAGs as catalog assets.
   - Rationale: the enhanced report asks for explicit stop conditions and multi-step loops; these primitives already encode that concept and only need Studio adapters.

9. Hill-climb and weakness mining.
   - Existing component: `agentkit.loop.hill_climb.mine_weaknesses` and `hill_climb_from_traces`.
   - Reuse as a later improvement source after Studio persists compact trajectories/observations.
   - Limitation: it expects `AgentResult` trajectories, while Studio currently persists run rows and events, not full `AgentResult` objects.
   - Actionable step: add a Studio adapter from `agent_trace.jsonl` or `TaskRun` history into the expected trajectory summary shape before using hill-climb weakness mining.

10. Token accounting and interrupt state.
    - Existing components: `agent-prep/shared/agent_loop_tools/token_accounting.py` and `interrupt_state.py`.
    - Use `TokenAccounting` for stop reports and metrics instead of a new token accumulator.
    - Use `InterruptStateSnapshot` helpers if Studio adds graceful stop/cancel semantics beyond the current runner controls.
    - Rationale: the shared token accumulator already handles exact-vs-estimated honesty through a sticky estimated flag.

11. Search/fetch.
    - Existing components: `agent-prep/shared/web_toolkit/search.py`, `fetch.py`, `_types.py`, and `_cache.py`.
    - Continue using structured `SearchResult`, `FetchResult`, `BatchFetchResult`, cache semantics, and backend fallback behavior.
    - Limitation: result objects do not include credibility fields, retrieval intent, claim linkage, or verification status.
    - Studio improvement: wrap search/fetch results into `ToolObservation` and `EvidenceItem` records without modifying the low-level fetch contract.
    - Shared-lib improvement: optionally add response metadata fields such as final URL, content type, fetched_at, and cache_hit if evidence auditing needs them.

12. Phoenix tracing.
    - Existing component: `agent-prep/shared/phoenix_tracing/tracing.py`.
    - Use it as an optional trace exporter/link, not as the only run trace store.
    - Limitation: Phoenix dependencies are lazy optional imports and may not be installed; initialization is process-global and raises on conflicting project/server.
    - Actionable step: write local `agent_trace.jsonl` unconditionally, then optionally wrap a run with `trace_run` when Phoenix is configured.

13. RAG and tree index.
    - Existing components: `agent-prep/shared/rag_hybrid` and `tree_index`.
    - Do not make them required for the first research-report upgrade because they bring Qdrant/model/index dependencies.
    - Use later if catalog assets, source notes, or large uploaded documents need semantic retrieval.
    - Rationale: current Studio report quality can improve materially through existing web search/fetch, evidence ledgers, gates, and export before adding a vector stack.

### Feasibility Corrections

1. Single local-loop path.
   - Correction: use `backend/studio/local_catalog/loops/research-report-agent.json`; do not also add `backend/studio/local_loops/research_report_agent.json`.
   - Rationale: Workstream J defines a catalog source model. A second local-loop directory would create conflict and migration work.

2. One exporter module.
   - Correction: Workstream E and Workstream L must both extend `backend/studio/report_export.py`.
   - Rationale: Workstream E is the minimal export spine; Workstream L is the full enhanced bundle. Two exporters would create inconsistent manifests and tests.

3. Skill file format.
   - Correction: local skills should normalize to `SkillLibrary` JSON/Markdown output. `.skill.md` is an import/export convenience, not the source of truth.
   - Rationale: the shared `SkillLibrary` already has a durable skill model and save/load behavior.

4. Optional renderers.
   - Correction: PDF, PNG, Mermaid rendering, and diagram exports must be best-effort optional features with explicit manifest status.
   - Rationale: the current repo does not guarantee Pandoc, browser renderers, or Mermaid CLI. Hard dependency would make export brittle.

5. Metrics source.
   - Correction: run metrics should consume `TokenAccounting`, observations, evidence, and publish-gate results. Do not maintain parallel token totals in multiple places.
   - Rationale: token exactness/estimate status is already solved in the shared helper.

6. Remote catalog policy.
   - Correction: adding remote loop/skill sources should include URL validation, source status, TTL, and optional egress allowlist enforcement.
   - Rationale: remote catalogs expand the trusted input surface; safety belongs in the catalog layer before assets appear in search or seed.

## Code-Validated Modification Matrix

This section validates each recommendation against the current source code. Items marked "modify" should be implemented; items marked "keep" already have the right foundation and should not be replaced.

### 1. Tool Contracts And Validation

Current code:

- `backend/studio/tools.py` already wraps any `LLMClient` with `ToolAugmentedClient`.
- Tool schemas exist for `web_search`, `web_fetch`, `read_file`, `write_file`, `read_artifact`, and `patch_artifact`.
- `ToolAugmentedClient._tool_names` filters model tool calls to known tool names before dispatch.
- Workspace file tools use `Workspace` containment checks; path escapes return rejected tool results instead of raw filesystem access.
- `web_fetch` and `web_search` degrade to error payloads instead of crashing the run.
- Tool loop has `_MAX_TOOL_ITERS`, rate-limit retries, context compaction, and forced synthesis when the model exhausts tool iterations.

Gap:

- Validation is implicit and event-level only. There is no durable observation record with pre-validation status, post-validation status, retry/escalation decision, or checkpoint id.
- Unknown inline tool calls are ignored if not in `_tool_names`; unknown dispatch returns a tool message but does not emit a structured rejected observation.
- Tool result validation is specific to each executor and not summarized into one contract that export/UI can audit.

Decision: keep `ToolAugmentedClient`; modify it to emit/store observations.

Actionable steps:

1. Add `backend/studio/observations.py` with `ToolObservation`, `Checkpoint`, and result summarizers.
2. Add optional callbacks to `ToolAugmentedClient`: `on_observation`, `on_checkpoint_hint`.
3. Wrap `_dispatch`:
   - create a pre-call observation with `allowed`, `requires_approval`, `validation_issues`.
   - execute only if allowed.
   - update with `status`, `result_summary`, `validation_status`, `retry_count`.
4. Keep existing `tool_call`/`tool_result` events unchanged for frontend compatibility.
5. Add tests around unknown tool, path escape, fetch failure, and successful fetch observations.

Rationale:

- The enhanced report asks for explicit tool validation and observations. The code already has the runtime hooks and safety checks, so adding an observation layer is lower risk than redesigning tool execution.

### 2. Evidence Matrix

Current code:

- `backend/studio/findings.py` parses `RESEARCH_FINDING` blocks and JSON-wrapped findings.
- Findings are grounded by fetched URL or quote verification through `studio.tools._fetch_cache`, `_url_in_cache`, and `_quote_in_cache`.
- Findings are deduped and converted into additive patches by `_findings_to_patches`.
- Current `Finding` extraction keeps URL/title/quote/why/popularity/patch target but does not persist a report-wide evidence ledger.

Gap:

- Accepted evidence disappears into prose patches. The final report can cite sources, but there is no durable evidence matrix with status, reliability, source type, date, and used-in section.

Decision: modify findings flow to produce typed evidence alongside patches.

Actionable steps:

1. Add `backend/studio/evidence.py` as described in Workstream A.
2. Extend `_parse_findings` to read `PUBLICATION`, `SOURCE_TYPE`, `RELIABILITY`, and `RELEVANCE` from plain and JSON findings.
3. Add a reducer callback or evidence side-channel in `_make_section_reducer`.
4. Persist evidence through `TaskRunStore.record` as `evidence_json` initially.
5. Add `EvidenceEvent` and frontend state only after backend evidence is stable.

Rationale:

- This builds directly on the strongest existing part of Studio: grounded findings and additive reducer patches. A separate source-note system would duplicate parsing/grounding and diverge from what actually entered the document.

### 3. Persistence, Checkpoints, And Run History

Current code:

- `backend/studio/task_runs.py` persists `task_hash`, `session_id`, `version`, `score`, `weaknesses_json`, `artifact_path`, `requirement`, `result_text`, `requirement_embedding`, and `config_json`.
- It supports latest/best/all runs, similar runs, accumulated weaknesses, repeat failures, and prior artifact carry-forward.
- It does not persist evidence, observations, metrics, publish gate results, or checkpoint references.

Gap:

- Current persistence is enough for hill-climb seeding but not enough for audit/recovery/export bundle requirements.

Decision: extend `TaskRunStore`; do not create a separate persistence stack.

Actionable steps:

1. Add nullable/default columns:
   - `evidence_json TEXT NOT NULL DEFAULT '[]'`
   - `publish_gate_json TEXT NOT NULL DEFAULT '{}'`
   - `metrics_json TEXT NOT NULL DEFAULT '{}'`
   - `trace_path TEXT NOT NULL DEFAULT ''`
   - `checkpoints_path TEXT NOT NULL DEFAULT ''`
2. Keep backward-compatible migration style used by current `PRAGMA table_info`.
3. Extend `TaskRun` dataclass and `_row_to_run`.
4. Write `agent_trace.jsonl` and `checkpoints.jsonl` in the workspace; store paths in SQLite.

Rationale:

- Existing run lineage and seeding rely on `TaskRunStore`. Extending it preserves task history behavior and lets export/metrics reuse the same run id/version model.

### 4. Publish Gate And Verification

Current code:

- `VerifyEvent` exists and frontend stores verify findings.
- `LoopDoctorEvent` exists and audits boundedness, material checks, safe actions, and clear stopping.
- `GateEvent` exists and collects safety/gate outcomes.
- `rubric.adjusted_score` penalizes open weaknesses, including hard defects like malformed Mermaid and unbalanced code fences.

Gap:

- There is no report-publication decision separate from loop-health decision.
- A structurally good report with missing citations or unsafe action can still look like a successful run unless the user reads several panels.

Decision: add `PublishGateEvent`; keep Loop Doctor separate.

Actionable steps:

1. Add `backend/studio/publish_gate.py`.
2. Call it in `Runner._run_inner` after verify/loopdoctor and before `_postrun_score_and_record`.
3. Add `PublishGateEvent` to backend `events.py`, frontend `api/types.ts`, and `runStore.ts`.
4. Show publish status in `ResultWindow` or a result-side panel.

Rationale:

- Loop Doctor answers "is this loop bounded/safe/checkable?" Publish Gate answers "is this report safe to publish?" The current code already has inputs for both, so separate event surfaces are clearer than overloading Loop Doctor.

### 5. Scoring And Rubric

Current code:

- `backend/studio/rubric.py` has deterministic weighted scoring with six criteria and GUI-configurable weights/template.
- `rubric_score` is already the hill-climb metric and keep/discard signal.
- `score_breakdown` is deterministic and tested.
- `task_runs.score_result` still exists as an LLM task-derived scorer for feedback/mining context.

Gap:

- The downloaded package and enhanced report ask for a user-facing 100-point scorecard with categories like practical usefulness, code quality, diagrams/tables, reflection, governance, and readability.
- Replacing the internal rubric would destabilize calibrated hill-climb behavior.

Decision: keep `rubric_score` for optimization; add a 100-point projection for reporting.

Actionable steps:

1. Add `rubric_scorecard_100(text, evidence, publish_gate, lint, metrics, required_sections)`.
2. Keep existing `_WEIGHTS` and tests unchanged.
3. Add the enhanced technical-report template as a preset, not the default.
4. Add frontend display for scorecard categories.

Rationale:

- Current deterministic metric is a known working control signal. The 100-point rubric should be a presentation/readiness layer, not a replacement for the optimizer.

### 6. Artifact Lint, Editorial Cleanup, And Report Structure

Current code:

- `backend/studio/artifact_lint.py` flags malformed Mermaid edges and unbalanced code fences.
- `backend/studio/artifact_text.py` already normalizes headings, dedupes repeated sections, strips reducer preamble, repairs Mermaid blocks, merges missing sections, detects gaps, synthesizes analysis, and refines readability while preserving URLs.

Gap:

- Existing lint is focused on two deterministic structural failures.
- Enhanced report also calls out repeated sections, scratchpad notes, unsupported popularity claims, vague "production-grade" language, unexplained diagrams/code, missing glossary, and source/interpretation separation.

Decision: extend current lint/cleanup modules; do not add another rewrite pipeline.

Actionable steps:

1. Extend `artifact_lint.py` with editorial checks that return weakness strings:
   - repeated headings,
   - scratchpad markers,
   - unsupported popularity/ranking claims,
   - vague unsupported production claims,
   - unexplained diagram/code blocks,
   - missing glossary when acronym density is high.
2. Keep actual rewriting in `artifact_text.py` because it already has URL-retention guards.
3. Add enhanced template preset in `rubric.py`.
4. Add tests proving cleanup never drops URLs.

Rationale:

- The code already has carefully guarded citation-preserving rewrite behavior. Adding another editorial rewrite path would risk citation loss and duplicate logic.

### 7. Export And Bundles

Current code:

- `backend/studio/export.py` only exports a finished `RunSnapshot` as a loop-library loop JSON.
- `GET /export/{session_id}` returns `{"loop": ...}` and 409s without a finished run.
- `RunSnapshot` only carries requirement, plan steps, topology, loopdoctor checks, budget ceiling, result, and cancelled.

Gap:

- Enhanced report requires a full deliverable bundle: Markdown, HTML, optional PDF, diagrams, source notes, demo code, pseudocode, sample output, requirements, manifest, evidence, scorecard, trace, and checkpoints.

Decision: keep existing loop export and add a separate research-package export.

Actionable steps:

1. Extend `RunSnapshot` with evidence, scorecard, publish gate, metrics, trace paths.
2. Add `backend/studio/report_export.py`.
3. Add `GET /export/{session_id}/research-package`.
4. Keep `/export/{session_id}` unchanged for loop JSON compatibility.
5. Make PDF/PNG renderers best-effort with explicit status in manifest.

Rationale:

- Existing export is correct for loop-library contribution but insufficient for research deliverables. A separate endpoint avoids breaking current tests and consumers.

### 8. Loop And Skill Catalog Management

Current code:

- `backend/studio/loops.py` hardcodes a remote catalog URL, caches it, searches loops, and adapts flat steps into seed steps.
- `backend/studio/app.py` exposes `/loops`, `/session/{id}/seed`, and `POST /session` can seed by `loop_id`.
- `Runner` consumes `session.seed_steps` through `make_seeded_decomposer`.
- `frontend/src/components/panels/LoopsPanel.tsx` already searches and seeds loops.
- `backend/studio/skills_paths.py` has five hardcoded path skills only.
- `/skills` returns only skill names/descriptions for display.

Gap:

- No local loops, no local/domain skills, no CRUD/import/export, no source management, no enable/disable overlay.

Decision: refactor around a catalog repository while preserving `/loops` and seed APIs.

Actionable steps:

1. Add `catalog_mgmt.py` with repository/normalizer.
2. Add local catalog storage and research-report local loop/skill.
3. Point existing `/loops` to enabled normalized catalog.
4. Add `/catalog/*` CRUD/read APIs.
5. Extend LoopsPanel into Find/Loops/Skills/Sources views.

Rationale:

- Current seed path is already correct and tested. Catalog management should feed that path rather than create a parallel research-specific seeding flow.

### 9. Frontend Event And State Surface

Current code:

- `frontend/src/api/types.ts` mirrors backend events as a discriminated union.
- `runStore.ts` has exhaustive switch handling for existing events.
- Store state exists for tools, loops, loop seed, verify, gates, loopdoctor, hill climb, scheduler, chain, result.

Gap:

- No typed payloads/store state for evidence, publish gate, metrics, checkpoints/trace, research package export status, or catalog inventory/CRUD.

Decision: add event types incrementally and keep store exhaustiveness.

Actionable steps:

1. Add TS interfaces for `EvidencePayload`, `PublishGatePayload`, `MetricsPayload`.
2. Add `RunState.evidence`, `publishGate`, `metrics`, `stopReport`.
3. Add reducer cases.
4. Add REST helper methods for catalog management and research-package export.
5. Keep catalog CRUD state local to catalog components unless multiple panels need it.

Rationale:

- The current exhaustive union is useful. New backend events must be mirrored deliberately; otherwise TypeScript will catch missing reducer cases.

### 10. Stop Conditions And Metrics

Current code:

- Stop behaviors exist but are scattered: cancel flag, budget exceeded, max epochs, plateau/min improvement, final done, goal met, tool iteration cap.
- Frontend displays some pieces separately.

Gap:

- There is no single stop report or metrics summary tying stop reason, tool accuracy, validation catch rate, citation accuracy, cost, human intervention, and recovery together.

Decision: derive run metrics from observations/checkpoints at post-run time.

Actionable steps:

1. Add `run_metrics.py`.
2. Compute metrics after publish gate.
3. Emit `MetricsEvent`.
4. Persist `metrics_json`.
5. Render metrics summary in Result/Loop Doctor area.

Rationale:

- The raw ingredients already exist or are planned in observations/evidence. A post-run derived metrics layer avoids scattering metric updates throughout the runner.

### 11. Weak-Model Report Quality Hardening

Current code:

- `backend/studio/prompts.py` has explicit executor/reducer prompts, but they still depend on the model obeying long instructions and mixed markdown schemas.
- `backend/studio/tools.py` has a global `_MAX_TOOL_ITERS = 8`, `_COMPACT_CHARS = 80_000`, and a narration detector. These are not model-profile-specific.
- `backend/studio/artifact_text.py` already uses moving-window synthesis/readability after assembly, but the bad artifact shows corruption before that stage.
- `backend/studio/artifact_lint.py` checks Mermaid glued edges and unbalanced fences only.
- `backend/studio/findings.py` parses `RESEARCH_FINDING` blocks and JSON-wrapped findings, but does not enforce JSONL-only output for weak models.

Gap:

- The artifact at `backend/tmp/studio-workspaces/s_9ef0b2a7bf46/artifact.md` proves the current system can serve a report with duplicate outlines, placeholder references, malformed/unverified links, orphaned code fragments, and evidence appended as a repetitive wall. Existing normalization and final readability are insufficient for `gemma-4-26B-A4B-it-heretic-4bit`.

Decision: add a weak-model profile and fail-closed report quality gates.

Actionable steps:

1. Add `model_profiles.py` and a Gemma profile with smaller context windows and lower tool/action caps.
2. Make `ToolAugmentedClient` accept profile-driven budgets.
3. Add JSONL finding mode and parser for weak models.
4. Move section-window selection before reducer prompts.
5. Add deterministic report assembler for research-report loop output.
6. Expand `artifact_lint.py` to detect the bad artifact's exact failure classes.
7. Add final publish-ready gate before serving/exporting.
8. Add a regression fixture based on the bad artifact.

Rationale:

- Weak local models are useful when the task is decomposed into small typed transformations. They are unreliable when asked to handle long mixed-context prompts and emit full polished documents. The fix is architectural: constrain action count, constrain context, constrain output schema, assemble deterministically, and block bad final reports.

### 12. Generic Report Profiles

Current code:

- `backend/studio/rubric.py` has one default report template plus configurable GUI weights/template.
- `backend/studio/prompts.py` builds a topic-agnostic skeleton but does not know report type.
- `Runner._run_inner` injects rubric template sections when configured.
- The planned `research-report-agent` local loop can seed a research flow, but without report-profile routing it may overfit to technical reports.

Gap:

- A generic research report generator needs different outlines and checks for different report types. A technical implementation report, policy brief, market research report, competitive analysis, and literature review should not share one rigid section list.

Decision: add report profiles as configuration, not hardcoded domain branches.

Actionable steps:

1. Extend `ResearchConfig` with `report_type`, `source_policy`, and `output_policy`.
2. Add template preset registry in `rubric.py`.
3. Route `ResearchConfig.report_type` to default template, lint policy, and optional output assets.
4. Allow user override of sections.
5. Keep code/diagram generation off by default unless profile or user request needs it.

Rationale:

- This preserves generic behavior while still letting specialized reports be well-structured. It also prevents the Pi/Craft fixture and enhanced technical report from becoming accidental defaults.

## Priority Order

Before starting implementation, create a small adapter checklist for the shared-library reuse requirements above. Each new Studio module should state which existing shared primitive it wraps or why no primitive applies.

1. Generic `ResearchConfig`/report-profile routing so the generator is not hardcoded to technical or agent-framework reports.
2. Weak-model profile, action caps, moving-window reducer inputs, and report-quality lints using the bad artifact as a regression fixture.
3. Publish gate with hard-fail decisions so bad reports are marked not publish-ready.
4. Evidence matrix data model and markdown rendering.
5. Validation-first observations/checkpoints/stop report.
6. Deterministic research report assembler and JSONL finding parser for weak-model mode.
7. Catalog repository/normalizer for local + remote loops.
8. Local research-report loop overlay for `/loops` and `/session/{id}/seed`.
9. Catalog management read UI for loops/skills/sources.
10. Loop CRUD/import/export and remote source refresh.
11. Domain skill listing/registration for `research-report-agent`.
12. Skill CRUD/import/export.
13. 100-point scorecard projection.
14. Full enhanced report bundle export.
15. Run metrics and stop-condition dashboard.
16. Human review decision/checklist.
17. Expanded artifact lint for diagrams/tables/code.
18. Source-type and corroboration refinements.
19. Generic template presets and editorial cleanup gates.

This order first protects genericity, then blocks the current user-visible weak-model failure. Once profile routing exists and the pipeline can reject malformed reports while keeping weak models in small typed windows, evidence/export/catalog/UI work can build on a stable quality spine.

## Concrete File Map

Curated reference files in this repo:

- `ref/README.md`
- `ref/STUDIO_ADAPTATION.md`
- `ref/research_report_agent_skill_package/**`
- `ref/enhanced_report_bundle/**`

New backend files:

- `backend/studio/evidence.py`
- `backend/studio/publish_gate.py`
- `backend/studio/report_export.py`
- `backend/studio/catalog_mgmt.py`
- `backend/studio/observations.py`
- `backend/studio/run_metrics.py`
- `backend/studio/model_profiles.py`
- `backend/studio/report_assembler.py`
- `backend/studio/report_profiles.py` or equivalent template/profile registry in `rubric.py`
- `backend/studio/local_catalog/loops/research-report-agent.json`
- `backend/studio/local_catalog/skills/research-report-agent.json` generated/loaded through `SkillLibrary`
- `backend/studio/local_catalog/sources.json`
- `backend/studio/skills_research.py` or equivalent domain-skill module
- Optional renderer helpers under `backend/studio/exporters/` for HTML/PDF/diagram rendering.

Shared-library edits to consider only if Studio cannot adapt cleanly:

- `/Users/yuxinliu/code/agentkit/agentkit/loop/goal.py`: add optional sandbox-backed execution for `check_goal`.
- `/Users/yuxinliu/code/agentkit/agentkit/loop/chain.py`: add a serializable loop-chain schema/adapter.
- `/Users/yuxinliu/code/agentkit/agentkit/artifacts/metrics.py`: add provider extension points for source-quality metrics beyond arXiv/GitHub.
- `/Users/yuxinliu/code/agentkit/agentkit/sandbox/net_guard.py` or `/Users/yuxinliu/code/agent-prep/shared/web_toolkit/*`: add optional egress enforcement hook for remote catalog/search/fetch.

Likely backend edits:

- `backend/studio/loops.py`: refactor around catalog repository, merge local/remote loops, test research-report search/seed.
- `backend/studio/skills_paths.py` or new `skills_research.py`: expose `research-report-agent` skill in addition to path skills.
- `backend/studio/app.py`: add `/catalog/*` management endpoints while preserving `/loops`, `/skills`, and `/session/{id}/seed`.
- `backend/studio/findings.py`: extract metadata, emit evidence, and add JSONL finding parsing for weak-model mode.
- `backend/studio/tools.py`: validate tool requests/results into structured observations without breaking current tool-call behavior; make max tool iterations and search/fetch budgets model-profile-driven.
- `backend/studio/prompts.py`: add weak-model executor prompt variant with short JSONL-only contract and explicit action budget.
- `backend/studio/runner.py`: collect evidence, observations, checkpoints, stop report, metrics, call publish gate, include scorecard/export state, select section windows before reducer prompts, and apply final report-quality gate before serving/exporting.
- `backend/studio/task_runs.py`: persist evidence, publish-gate result, metrics, and trace/checkpoint paths.
- `backend/studio/rubric.py`: add 100-point scorecard projection and enhanced technical-report template preset.
- `backend/studio/rubric.py`: add generic report template presets and route them from `ResearchConfig.report_type`.
- `backend/studio/events.py`: add `PublishGateEvent`, `EvidenceEvent`, and `MetricsEvent`.
- `backend/studio/app.py`: add export endpoint and expose stored run evidence.
- `backend/studio/session.py`: extend `RunSnapshot` with evidence/scorecard/publish gate/metrics/trace paths.
- `backend/studio/artifact_lint.py`: add table/diagram/code/editorial checks plus duplicate-section, placeholder-reference, unverified-link, citation-wall, orphaned-code-fragment, and citation-free-section checks.

Likely frontend edits:

- `frontend/src/api/types.ts`: mirror new events.
- `frontend/src/api/sse.ts`: add catalog management API helpers.
- `frontend/src/store/runStore.ts`: store evidence/publish-gate/metrics state.
- `frontend/src/components/panels/LoopsPanel.tsx`: add Find/Loops/Skills/Sources catalog management tabs or split into subcomponents.
- `frontend/src/components/panels/*` or `ResultWindow.tsx`: show evidence matrix summary, hard fails, review checklist, metrics, stop reason, and export bundle status.
- `frontend/src/components/config/LoopConfigPanel.tsx`: optional research brief controls.

Tests to add:

- `backend/tests/test_evidence.py`
- `backend/tests/test_publish_gate.py`
- `backend/tests/test_report_export.py`
- `backend/tests/test_observations.py`
- `backend/tests/test_run_metrics.py`
- `backend/tests/test_catalog_mgmt.py`
- `backend/tests/test_model_profiles.py`
- `backend/tests/test_report_assembler.py`
- `backend/tests/fixtures/bad_report_duplicate_sections.md`
- `backend/tests/test_bad_report_quality_gate.py`
- Extend `backend/tests/test_rubric.py`, `backend/tests/test_artifact_lint.py`, `backend/tests/test_events.py`, and frontend store/type tests.

## Non-Goals

- Do not replace Studio's runner with `03_code/research_report_agent.py`; that file is a mock demonstration, not production architecture.
- Do not make every report research-specific by default. Research config and package export should be optional so generic loop tasks still work.
- Do not use an LLM judge as the primary epoch metric. Keep deterministic scoring for optimization and use LLM/heuristic reviewers only as auxiliary weakness sources.
- Do not treat old generated sample outputs as required implementation artifacts. The curated `ref/` files are editable requirements/reference material; application behavior still belongs in app code.

## Definition Of Done

- A finished research run can produce: final report, HTML, optional PDF, evidence matrix, source notes, scorecard, hard-fail decision, review checklist, trace/checkpoints, metrics, practice code artifacts, and loop export.
- A structurally good report with missing citations or unsafe gate failures is not marked publish-ready.
- Every run has an explicit stop reason and enough observation/checkpoint data to audit what happened.
- The research-report local loop and skill are manageable through catalog UI and seed through the existing loop mechanism.
- Existing hill-climb/rubric tests continue passing.
- New tests prove package/report-derived requirements: evidence statuses, hard-fail blocking, validation observations, stop-condition metrics, 100-point thresholds, catalog management, and enhanced bundle export contents.
