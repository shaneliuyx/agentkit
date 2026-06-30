# Worklog: Build Research Report Generator Plan

Branch: `build-research-report-generator-plan`

Objective: implement the research report generator improvement plan and validate it end-to-end with `gemma-4-26B-A4B-it-heretic-4bit` where the local environment allows.

## Steps Completed

1. Created feature branch `build-research-report-generator-plan`.
2. Inspected current dirty worktree and confirmed many unrelated modified/deleted files existed before this implementation slice. These are intentionally left untouched unless needed.
3. Used TokenSave to locate relevant implementation surfaces:
   - `backend/studio/artifact_lint.py`
   - `backend/studio/rubric.py`
   - `backend/studio/runner.py`
   - `backend/studio/tools.py`
   - `backend/tests/test_artifact_lint.py`
   - existing task-run database and prior workspace outputs.
4. Queried `backend/tmp/task_runs.db` and found repeated historical quality failures for the Pi/Craft report lineage:
   - duplicate outlines,
   - placeholder source references,
   - malformed Mermaid,
   - fragmented/non-cohesive output,
   - source-reference placeholder in session `s_9ef0b2a7bf46`.
5. Started first implementation slice around deterministic quality controls and profile metadata.
6. Extended `backend/studio/artifact_lint.py` with helper checks for:
   - duplicate headings,
   - placeholder text,
   - malformed or unverified links,
   - citation walls,
   - orphaned code fragments outside fences,
   - long citation-free evidence-bearing sections.
7. Updated `lint_artifact()` to call the new deterministic checks while preserving existing Mermaid and unbalanced-fence checks.
8. Added `backend/studio/model_profiles.py`:
   - default model profile,
   - weak Gemma profile for `gemma-4-26B-A4B-it-heretic-4bit`,
   - `resolve_model_profile()`.
9. Added `backend/studio/report_profiles.py`:
   - generic, technical, market, policy, literature review, academic, competitive, and product profiles,
   - `resolve_report_profile()`.
10. Added minimized bad-report fixture:
   - `backend/tests/fixtures/bad_report_duplicate_sections.md`.
11. Added profile tests:
   - `backend/tests/test_model_profiles.py`,
   - `backend/tests/test_report_profiles.py`.
12. Extended `backend/tests/test_artifact_lint.py` with:
   - a bad-report fixture regression test covering duplicate headings, placeholders, unverified links, citation walls, orphaned code, and citation-free long sections;
   - a clean market-report guard to avoid over-aggressive linting.
13. Ran focused tests:
   - command: `pytest tests/test_artifact_lint.py tests/test_model_profiles.py tests/test_report_profiles.py -q`
   - result: 11 passed, 1 failed.
   - failure: `bad_report_duplicate_sections.md` did not trigger the citation-free-section check because the intended long section was only 130 words, below the 150-word production threshold.
14. Updated `backend/tests/fixtures/bad_report_duplicate_sections.md` to keep the lint threshold conservative and make the fixture represent a genuinely long unsupported report section.
15. Re-ran focused tests:
   - command: `pytest tests/test_artifact_lint.py tests/test_model_profiles.py tests/test_report_profiles.py -q`
   - result: 12 passed.
16. Implemented model-profile runtime wiring:
   - `ToolAugmentedClient` now accepts `max_searches` and `max_successful_fetches` in addition to the existing `max_iters`.
   - Budgets are tracked per `chat()` call so one phase cannot exhaust another phase's local action allowance.
   - Budget exhaustion returns a rejected tool result telling the model to synthesize from gathered evidence instead of continuing tool use.
   - `Runner._maybe_tool_augment()` resolves the session model through `resolve_model_profile()` and passes Gemma/default loop budgets into `ToolAugmentedClient`.
17. Added focused runtime tests:
   - `test_search_budget_rejects_extra_searches`
   - `test_successful_fetch_budget_rejects_after_success`
   - `test_gemma_profile_limits_searches_in_runner_tool_loop`
18. Ran expanded focused tests:
   - command: `pytest tests/test_tools.py tests/test_artifact_lint.py tests/test_model_profiles.py tests/test_report_profiles.py tests/test_runner.py::test_gemma_profile_limits_searches_in_runner_tool_loop -q`
   - result: 38 passed.
19. Verified local live endpoints with unsandboxed localhost access:
   - Studio backend `http://localhost:8770/backends`: reachable.
   - oMLX `http://localhost:8000/v1/models`: reachable.
   - exact model `gemma-4-26B-A4B-it-heretic-4bit`: present.
20. Started a bounded real SSE run through Studio:
   - session: `s_6840cffcb216`
   - llm profile: `gemma`
   - model: `gemma-4-26B-A4B-it-heretic-4bit`
   - mode: `llm`
   - tools_enabled: `true`
   - loop sizing: one agent, one task per agent, auto_improve disabled.
21. The first bounded SSE run (`s_6840cffcb216`) exited after ~143 seconds without useful streamed output. No workspace artifact/result existed for that session when checked.
22. User ran `omx setup --scope project --merge-agents` after installing `oh-my-codex`, then `omx doctor`. New OMX/Codex project files appeared (`.codex/`, `.agents/`, `AGENTS.md`, `skills-lock.json`, etc.). Treat these as environment setup and do not include them in the scoped implementation commit unless explicitly requested.
23. Re-ran focused tests after setup:
   - command: `pytest tests/test_tools.py tests/test_artifact_lint.py tests/test_model_profiles.py tests/test_report_profiles.py tests/test_runner.py::test_gemma_profile_limits_searches_in_runner_tool_loop -q`
   - result: 38 passed.
24. Restarted Studio backend on port 8770 so E2E exercised the current branch code. The detached restart process passed its own health check but exited afterward without traceback; ran uvicorn in the foreground with escalation for reliable observation.
25. Completed bounded real Gemma E2E session `s_a98d2c787af6`.
   - Runtime: ~114 seconds.
   - Events: session, plan, topology, phase/tool/gate, token/budget/verify/loopdoctor/hill_climb/done.
   - Output file: `backend/tmp/studio-workspaces/s_a98d2c787af6/result.md` (1,029 chars).
   - No `artifact.md` was produced.
   - Output quality was bad: zero URLs, drifted into generic risk taxonomy rather than agent-loop/skill catalog management, and did not satisfy the requested evidence/citation behavior.
   - `lint_artifact()` did not flag issues because the result was short and had no malformed structural markers; this shows a semantic/request-alignment gate is still needed.
26. Added weak-model report routing:
   - `report_profiles.py` now detects report-like requests and can build a weak-model report plan/prompt.
   - `Runner._run_inner()` resolves the active model profile and routes weak Gemma report requests away from broad LLM epic planning.
   - Important: this does not remove the existing epic planner or automatic topology selector; the weak report plan still passes through topology assignment and emits selector rationale.
27. User pointed to `ref/research_report_agent_skill_package/02_methodology/agent_loop.md` for stage planning. Read it and confirmed the canonical flow:
   - Intake ResearchConfig,
   - Select Report Profile,
   - Section + Source Plan,
   - Retrieve Sources,
   - Validate Evidence,
   - Evidence Matrix,
   - Deterministic Section Assembly,
   - Section Rewrite,
   - Report Lints + Publish Gate,
   - Revision/Packaging.
28. Replaced the weak-model one-step report plan with a fixed methodology-derived stage plan:
   - `intake-profile`,
   - `source-plan`,
   - `retrieve-verify`,
   - `assemble-rewrite`,
   - `lint-publish`.
   Each stage is narrow, schema/action bounded, and uses `SINGLE` intent; automatic topology selection still validates/logs the topology event.
29. User asked about original DB template management. Validated current code:
   - `backend/studio/templates.py` defines `TemplateStore` backed by SQLite table `report_templates`, with `save_template()` and `find_template()`.
   - `backend/studio/runner.py` currently saves good final skeletons after a run when `_score >= 0.6` and an embedder exists.
   - No active runner call to `find_template()` was found.
   - `_build_skeleton()` deliberately stopped semantic template reuse because prior topic-specific headings leaked into unrelated reports.
30. Updated `PLAN-research-report-skill-package-improvements.md` Workstream N:
   - built-in generic/profile presets should live in code (`report_profiles.py`);
   - DB templates are a managed learned/approved catalog, not the source of truth for defaults;
   - add metadata/status/quality fields for DB templates;
   - audit/quarantine/replace stale or bad original DB skeletons instead of blindly reusing them;
   - auto-select DB templates only when report type, status, approval/score, semantic similarity, and lints all pass.
31. Started second bounded real Gemma E2E session `s_558d8fb9b6c6` after adding the initial one-step weak report route. It correctly emitted a single `report-draft` plan and tool calls, but hung after the phase gate in post-phase finalization. Cancelled via `/cancel/s_558d8fb9b6c6` and interrupted the client script. This session ran before the methodology-stage replacement and should not be treated as final validation.
32. User corrected the design direction: new changes must stay compatible with the original architecture. The system should encourage LLM automatic processing, not hardcode workflow branches based on conditions like "simple report" or "weak LLM".
33. Reverted the automatic runner override that routed Gemma report requests away from `_plan_from_epics()`. Current compatible design:
   - unseeded LLM mode keeps original epic-based planning;
   - automatic topology selection still runs and emits rationale;
   - model profiles only provide action budgets / prompt hints / gates;
   - methodology stage plans from `agent_loop.md` are reusable as explicit seeded/catalog loops, not silent model-id routing.
34. Updated `PLAN-research-report-skill-package-improvements.md` accordingly:
   - "weak-model mode" reframed as model profiles for budgets and prompt hints;
   - fixed research-report stages reframed as a seedable methodology;
   - acceptance test added that Gemma/profile resolution does not bypass LLM epic planning by default.
35. User suggested a better compromise: if stage planning is too complex, adjust
   the planner prompt so the LLM can choose smarter planning based on report size,
   risk, evidence burden, and requested depth.
36. Updated `_build_planner_cot_prompt()` in `backend/studio/prompts.py`:
   - keeps existing EPIC_PLAN contract;
   - keeps LLM epic planning as default;
   - adds report-specific heuristics for compact, standard, and large/high-impact
     reports;
   - explicitly says the methodology stages are planning heuristics, not hardcoded
     stages.
37. Added `test_build_planner_cot_prompt_guides_report_plan_depth` to lock this
   prompt behavior.
38. Ran compatibility/prompt focused tests:
   - command: `pytest tests/test_tools.py tests/test_artifact_lint.py tests/test_model_profiles.py tests/test_report_profiles.py tests/test_templates.py tests/test_m8_m9_helpers.py::test_build_planner_cot_prompt_guides_report_plan_depth tests/test_runner.py::test_gemma_profile_limits_searches_in_runner_tool_loop tests/test_runner.py::test_gemma_report_request_keeps_llm_epic_planning_by_default -q`
   - result: 46 passed.
39. Ran bounded real Gemma E2E session `s_fa0fecbb18a6` through the compatible original planner path.
   - Runtime: 77.2 seconds.
   - Planner behavior: kept `_plan_from_epics()` and emitted three epics; topology selection emitted rationale for `star`, `star`, `pipeline`.
   - Tool behavior: used web_search/web_fetch within budgeted phases.
   - Output file: `backend/tmp/studio-workspaces/s_fa0fecbb18a6/result.md` (642 chars).
   - Output quality: still bad for the requested generic catalog-management report; zero URLs, no placeholder text, but semantically drifted to workflow-vs-agent auditability and did not cover local/remote catalogs or implementation steps.
   - `lint_artifact()` reported no issues because the output was structurally clean but semantically incomplete. This validates the plan need for evidence matrix, semantic/request-alignment gate, deterministic report assembly, and publish gate; prompt heuristics alone are insufficient.
40. Stopped the foreground uvicorn backend after E2E; no long-running exec sessions remain from the live test.
41. Continued implementation because `s_fa0fecbb18a6` proved the real path runs but produces a structurally clean, semantically bad report.
42. Added `backend/studio/report_quality.py`:
   - deterministic publish-readiness checks for report-like requests;
   - non-report tasks pass through;
   - report tasks fail when requested evidence/citations are absent, the report is too short, or important request terms are missing.
43. Wired `evaluate_publish_readiness()` into `Runner._postrun_score_and_record()` after final cleanup and before adjusted scoring:
   - emits `GateEvent(name="publish-ready")`;
   - prepends publish-gate issues into `_weaknesses`, so adjusted score and hill-climb feedback reflect request-alignment failures.
44. Added tests:
   - `backend/tests/test_report_quality.py`;
   - `test_publish_gate_emits_failure_for_report_without_sources`.
45. Ran focused tests:
   - command: `pytest tests/test_report_quality.py tests/test_tools.py tests/test_artifact_lint.py tests/test_model_profiles.py tests/test_report_profiles.py tests/test_m8_m9_helpers.py::test_build_planner_cot_prompt_guides_report_plan_depth tests/test_runner.py::test_gemma_profile_limits_searches_in_runner_tool_loop tests/test_runner.py::test_gemma_report_request_keeps_llm_epic_planning_by_default tests/test_runner.py::test_publish_gate_emits_failure_for_report_without_sources -q`
   - result: 46 passed.
46. Ran bounded real Gemma E2E session `s_617c7f5b7aa0`.
   - Runtime: 52.5 seconds.
   - Planner behavior: preserved original `_plan_from_epics()` path and emitted the same 3-epic structure as the prior compatible run.
   - Output remained bad: 642 chars, zero URLs, semantic drift to workflow-vs-agent auditability.
   - New publish gate worked: emitted `GateEvent(name="publish-ready", outcome="fail")`.
   - Gate detail:
     - requested evidence/citations but final output has no source URL;
     - final output is too short to be a complete research report;
     - final output misses important request terms including catalog, management, loop, skill, local, remote, operational, risk.
   - This proves the system now detects the bad report rather than silently presenting it as publish-ready. Next improvement should use this gate to trigger revision/evidence-matrix assembly, not just report failure.
47. Stopped foreground uvicorn backend after E2E.
48. Started implementing a cache-based recovery draft, then user correctly rejected it because it hardcoded catalog-management prose into production code and the system must serve generic cases.
49. Removed the hardcoded recovery builder and its runner call/test from the worktree before committing. Current production behavior remains generic:
   - detect publish-readiness failures deterministically;
   - do not synthesize domain-specific replacement content;
   - future recovery must use generic LLM/evidence-matrix revision prompts based on the user's request and fetched evidence.
50. Persisted the user correction as a standing local guardrail in `AGENTS.md` and the plan: the report generator is generic; production code must not hardcode topic-specific prose, conclusions, model-specific report branches, or one-off templates. Domain examples belong only in tests, fixtures, references, user input, or reviewed catalog/template data.
51. Added Workstream O10 to the plan: every report-generator slice must include a genericity audit over changed production code and any reused shared helpers. The planned scanner/checklist must flag fixed report prose, topic-specific recovery drafts, fixed conclusions, one-off template sections, and model-id semantic branches while ignoring tests, fixtures, `ref/`, and reviewed catalog/template data.
52. Genericity audit for this slice: pass with one allowed fixture. Production code contains no hardcoded catalog-management fallback prose after removal. The remaining catalog-management paragraph is only in `backend/tests/test_report_quality.py` as a unit-test fixture.
53. Re-ran focused tests after adding the generic revision prompt helper and O10 plan task:
   - command: `pytest tests/test_report_quality.py tests/test_tools.py tests/test_artifact_lint.py tests/test_model_profiles.py tests/test_report_profiles.py tests/test_m8_m9_helpers.py::test_build_planner_cot_prompt_guides_report_plan_depth tests/test_runner.py::test_gemma_profile_limits_searches_in_runner_tool_loop tests/test_runner.py::test_gemma_report_request_keeps_llm_epic_planning_by_default tests/test_runner.py::test_publish_gate_emits_failure_for_report_without_sources -q`
   - result: 47 passed.
54. User clarified the genericity rule: "generic" restricts Studio source code and built-in mechanisms, not generated report content. Generated reports must be specific to the user's task, topic, audience, and evidence. Updated the plan, prompt helper wording, local `AGENTS.md`, and genericity-audit wording to reflect that distinction.
55. Added the first genericity scanner slice:
   - `backend/studio/genericity_audit.py` scans Python production string literals for obvious fixed report drafts and known example fallback phrases.
   - `backend/tests/test_genericity_audit.py` verifies production fixed-report prose is flagged, allowed paths (`tests`, fixtures, `ref`) are ignored, and generic report-generator terms are allowed.
56. Re-ran focused tests after the correction:
   - command: `pytest tests/test_genericity_audit.py tests/test_report_quality.py tests/test_tools.py tests/test_artifact_lint.py tests/test_model_profiles.py tests/test_report_profiles.py tests/test_m8_m9_helpers.py::test_build_planner_cot_prompt_guides_report_plan_depth tests/test_runner.py::test_gemma_profile_limits_searches_in_runner_tool_loop tests/test_runner.py::test_gemma_report_request_keeps_llm_epic_planning_by_default tests/test_runner.py::test_publish_gate_emits_failure_for_report_without_sources -q`
   - result: 50 passed.
57. Wired a generic publish-revision attempt into `Runner._postrun_score_and_record()`:
   - only runs in LLM mode after a publish-readiness failure;
   - only runs when prior phase outputs contain URLs/evidence context;
   - uses `build_publish_revision_prompt()` with the user's request, failed draft, gate issues, and evidence excerpts;
   - accepts the revised report only if it passes `evaluate_publish_readiness()` after URL-cache verification;
   - emits `publish-revision` before the final `publish-ready` event.
58. Genericity audit caught hardcoded example phrases inside the first scanner implementation itself. Removed that phrase list and kept the scanner generic/report-shape based.
59. Re-ran genericity audit and focused tests:
   - command: `python -c "from studio.genericity_audit import audit_genericity; issues=audit_genericity(['studio/runner.py','studio/report_quality.py','studio/genericity_audit.py']); print('\\n'.join(i.format() for i in issues) or 'no genericity issues')"`
   - result: no genericity issues.
   - command: `pytest tests/test_genericity_audit.py tests/test_report_quality.py tests/test_tools.py tests/test_artifact_lint.py tests/test_model_profiles.py tests/test_report_profiles.py tests/test_m8_m9_helpers.py::test_build_planner_cot_prompt_guides_report_plan_depth tests/test_runner.py::test_gemma_profile_limits_searches_in_runner_tool_loop tests/test_runner.py::test_gemma_report_request_keeps_llm_epic_planning_by_default tests/test_runner.py::test_publish_gate_emits_failure_for_report_without_sources -q`
   - result: 50 passed.
60. Extracted publish-revision evidence selection into `build_revision_evidence_text()` in `backend/studio/report_quality.py`:
   - includes only phase outputs that contain `http://` or `https://`;
   - labels each excerpt by phase/step id;
   - applies the same moving-window cap used by the revision prompt.
61. Added focused tests for the evidence selector and reran verification:
   - command: `pytest tests/test_genericity_audit.py tests/test_report_quality.py tests/test_tools.py tests/test_artifact_lint.py tests/test_model_profiles.py tests/test_report_profiles.py tests/test_m8_m9_helpers.py::test_build_planner_cot_prompt_guides_report_plan_depth tests/test_runner.py::test_gemma_profile_limits_searches_in_runner_tool_loop tests/test_runner.py::test_gemma_report_request_keeps_llm_epic_planning_by_default tests/test_runner.py::test_publish_gate_emits_failure_for_report_without_sources -q`
   - result: 52 passed.
   - genericity audit on `runner.py`, `report_quality.py`, and `genericity_audit.py`: no genericity issues.

## Current Next Steps

1. Stage and commit the guardrail/generic-prompt/checklist slice.
2. Next implementation: generic evidence-matrix revision loop, with no domain hardcoding.
3. Add the O10 scanner/checklist test before the next production report-generator slice lands.

## Notes For Resume

- Do not revert unrelated dirty worktree files.
- Prefer implementing the plan in small testable slices.
- The first completed E2E validated the real system path but proved report quality is still poor without stronger report-stage control.
- The current slice keeps LLM planning as default. Methodology stages from `ref/.../agent_loop.md` are retained as seedable/catalog scaffolding, not automatic routing.
- Static generic/profile templates are code defaults. Learned/approved reusable skeletons belong in `TemplateStore`/DB after audit metadata and replacement/quarantine logic exists.
- The latest E2E (`s_fa0fecbb18a6`) proves compatibility with the original planner/topology path, but not report-quality success.
- Standing guardrail: AgentKit Studio's source code and built-in report-generator mechanisms are generic/task-neutral, but generated reports must be task-specific and evidence-specific. Never add domain-specific production prose, fixed conclusions, model-id report branches, or example-specific templates; implement reusable evidence, prompt, catalog/template, validation, and UI mechanisms instead.
