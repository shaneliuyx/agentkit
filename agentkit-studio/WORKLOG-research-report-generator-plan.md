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
62. Re-ran the current code in the same normal E2E mode as the last successful run, not hill-climb:
   - Session: `s_089e897f1651`.
   - Request: catalog management for local/remote agent loops and skills in a generic research report generator, with citations and implementation risks.
   - Runtime: ~301 seconds.
   - Events reached `done`; no auto-improve hill-climb loop was enabled.
   - Output file: `backend/tmp/studio-workspaces/s_089e897f1651/result.md` (5,635 chars).
   - Comparison to `s_617c7f5b7aa0`: content is longer and more on-topic, but still lacks real source URLs, so publish readiness remains a failure.
63. Investigated missing `agent_io.jsonl` during normal E2E:
   - Prior successful sessions `s_617c7f5b7aa0` and `s_fa0fecbb18a6` had `agent_io.jsonl`, but only a run-summary row.
   - Current normal run also created `agent_io.jsonl`, but only after completion and only with the run-summary row; no per-phase `io/*.md` records were written.
   - Root cause: per-phase logging was gated on `_eff_ws2`, which is normally set by hill-climb carry-forward. In non-hill-climb normal runs `_eff_ws2` can stay `None`, so the per-phase logging block is skipped while final summary logging uses a separate workspace-root path.
64. Fixed normal-run agent I/O diagnostics without changing artifact execution semantics:
   - `backend/studio/runner.py` now uses a diagnostics-only workspace root fallback for the per-phase `agent_io.jsonl` writer.
   - It does not set `_eff_ws2` globally, avoiding unintended activation of artifact patch/reduce behavior in normal runs.
   - Added `test_normal_run_writes_agent_io_log`.
   - Validation: `pytest tests/test_runner.py::test_normal_run_writes_agent_io_log tests/test_runner.py::test_done_writes_result_file tests/test_runner.py::test_gemma_report_request_keeps_llm_epic_planning_by_default tests/test_runner.py::test_gemma_planning_and_topology_selection_are_capped tests/test_client_max_tokens.py tests/test_model_profiles.py -q` → passed.
65. Re-ran patched normal E2E to validate diagnostics:
   - Session: `s_990e9422708c`.
   - Runtime: ~329 seconds; reached `done` with no auto-improve hill-climb loop.
   - `agent_io.jsonl` now contains per-phase agent/reducer rows plus `io/*.in.md` and `io/*.out.md` files for phases.
   - Output stayed at 5,635 chars and still failed citation quality because the model wrote citation-looking references without real URLs.
66. Found and fixed a runner topology bridge bug exposed by the new per-phase logs:
   - `DESIGN-v2.md` and `agentkit.topology.core` define selector concepts such as `durable_board`, `gateway`, and `tree`; `durable_board`/`gateway` are trigger/state-level concepts, not Studio runtime fan-out strategies.
   - Before the fix, `run_plan()` silently executed state-level concepts with the SINGLE strategy while the UI/logs still showed the state-level topology as if it were the execution shape.
   - `backend/studio/runner.py` now maps selector concepts to executable runtime shapes (`durable_board`/`gateway` → `single`, `tree` → `star`) and records the original selector concept in the topology rationale.
   - Added `test_runner_maps_state_level_selector_topology_to_runtime_shape`.
67. Verification after the runner fixes:
   - command: `pytest tests/test_runner.py::test_runner_maps_state_level_selector_topology_to_runtime_shape tests/test_runner.py::test_normal_run_writes_agent_io_log tests/test_runner.py::test_gemma_planning_and_topology_selection_are_capped tests/test_client_max_tokens.py tests/test_model_profiles.py -q`
   - result: 7 passed.
   - genericity audit on `studio/runner.py`, `studio/model_profiles.py`, and `studio/client.py`: no genericity issues.
68. User clarified topology selection direction: the LLM should decide why a topology is selected, with code explaining the principles/steps rather than applying the rule tree as the decision-maker.
   - Added `select_topologies_by_llm()` in `backend/studio/planning.py`.
   - LLM mode now asks the model to return JSON `{topology, rationale, questions_fired}` for each phase.
   - The prompt gives the documented principles from `DESIGN-v2.md`: prefer single until coordination is needed; gateway for routing/permissions; durable_board for restart/cross-session/human-in-loop; mesh for debate; star/map for independent/per-item parallelism; pipeline for ordered stages; tree for hierarchy.
   - Runner code now only validates the LLM answer and maps state-level selector concepts to executable Studio shapes (`durable_board`/`gateway` → `single`, `tree` → `star`), preserving the LLM rationale in `TopologyEvent`.
   - Verification: `pytest tests/test_runner.py::test_gemma_planning_and_topology_selection_are_capped tests/test_runner.py::test_runner_maps_state_level_selector_topology_to_runtime_shape tests/test_runner.py::test_normal_run_writes_agent_io_log tests/test_client_max_tokens.py tests/test_model_profiles.py -q` → 7 passed.
   - Genericity audit on `studio/runner.py`, `studio/planning.py`, `studio/model_profiles.py`, and `studio/client.py`: no genericity issues.
69. Aligned the epic-planner prompt with the LLM-led topology selector:
   - `backend/studio/prompts.py` now lists `single`, `star`, `map`, `mesh`, `pipeline`, `gateway`, `durable_board`, and `tree` as topology intents.
   - Removed the old wording that the system reconciles topology with its own selector; it now says a later LLM topology selector makes the final choice and explains the rationale.
   - The topology selector prompt example uses neutral principle labels rather than hardcoded `Q` labels.
   - Added prompt assertions in `test_build_planner_cot_prompt_guides_report_plan_depth`.
   - Verification: `pytest tests/test_m8_m9_helpers.py::test_build_planner_cot_prompt_guides_report_plan_depth tests/test_runner.py::test_gemma_planning_and_topology_selection_are_capped tests/test_runner.py::test_runner_maps_state_level_selector_topology_to_runtime_shape tests/test_runner.py::test_normal_run_writes_agent_io_log tests/test_client_max_tokens.py tests/test_model_profiles.py -q` → 8 passed.
   - Genericity audit on `studio/runner.py`, `studio/planning.py`, `studio/prompts.py`, `studio/model_profiles.py`, and `studio/client.py`: no genericity issues.
70. Re-ran normal E2E with LLM-led topology selection:
   - Session: `s_6b4cf7edbc93`.
   - Runtime: ~468 seconds; reached `done`; no hill-climb loop.
   - `agent_io.jsonl` now has per-phase rows and `io/*.in/out.md`.
   - Output included some URLs but remained low quality (score ~0.29) and token use rose sharply (~222K total tokens) because LLM topology selection over-fanned out.
   - The per-spoke prompts showed a second issue: workers were focused by generic prompt-derived facets instead of concrete section assignments.
71. User corrected the reducer interpretation:
   - Reducer is not merely a doc pass-through. Per `DESIGN-v2.md`, every phase should follow hub → worker → reducer; the reducer consolidates artifacts and verifies assignment coverage.
   - Reverted the mistaken idea of adding a fake identity reducer record as a fix.
   - Root-cause finding: the code still had section-assignment validation and coverage checks, but active reducer-backed fanout did not isolate worker prompts to assigned sections.
72. Restored section-owned worker foci through the shared topology path:
   - Added optional `worker_foci` to `agentkit.planner.core.PlanStep`; empty preserves old behavior.
   - Updated `agentkit.topology.dynamic` STAR/MESH strategies to use explicit `worker_foci` before falling back to `_facets()`.
   - Added `build_section_worker_foci()` in `backend/studio/planning.py`. It builds per-worker assignment text from deliverable sections plus relevant section/document weaknesses, and tells the worker to create/populate assigned sections that are absent or pending.
   - Wired `backend/studio/runner.py` to set `worker_foci` for reducer-backed fanout phases from existing artifact headings plus missing rubric-template sections.
   - This keeps the generic source-code rule: assignments come from configured deliverable structure and weaknesses, not hardcoded report content.
73. Verification for section-owned worker foci:
   - `pytest backend/tests/test_section_ownership.py::test_worker_foci_include_assigned_sections_weaknesses_and_create_guidance backend/tests/test_section_ownership.py::test_star_workers_use_explicit_section_foci_not_generic_facets -q` → 2 passed.
   - `pytest backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_runner.py::test_normal_run_writes_agent_io_log backend/tests/test_section_ownership.py::test_worker_foci_include_assigned_sections_weaknesses_and_create_guidance backend/tests/test_section_ownership.py::test_star_workers_use_explicit_section_foci_not_generic_facets -q` → 4 passed.
   - `pytest backend/tests/test_runner.py::test_gemma_planning_and_topology_selection_are_capped backend/tests/test_runner.py::test_runner_maps_state_level_selector_topology_to_runtime_shape backend/tests/test_runner.py::test_hill_climb_honors_selected_topology_no_force_star backend/tests/test_m8_m9_helpers.py::test_build_planner_cot_prompt_guides_report_plan_depth backend/tests/test_m8_m9_helpers.py::test_build_worker_cot_prompt_has_verbatim_anchor_rule backend/tests/test_m8_m9_helpers.py::test_build_hub_cot_prompt_step5_is_section_based backend/tests/test_client_max_tokens.py backend/tests/test_model_profiles.py -q` → 10 passed.
   - Import check with `PYTHONDONTWRITEBYTECODE=1 python -B`: passed.
   - Genericity audit on `studio/runner.py`, `studio/planning.py`, `studio/prompts.py`, `studio/model_profiles.py`, and `studio/client.py`: no genericity issues.
74. Implemented profile-based report templates:
   - Added `deep_technical` and `agent_system_technical` presets in `backend/studio/report_profiles.py`.
   - `/rubric/defaults` now returns `report_type` and `template_presets`; `/session/{id}/rubric` can resolve a built-in template from `report_type` when no explicit template is provided.
   - The rubric tab UI now shows a report profile selector and keeps the selected sections editable.
   - Validation: `pytest backend/tests/test_report_profiles.py backend/tests/test_m8_m9_helpers.py::test_rubric_defaults_expose_profile_templates backend/tests/test_m8_m9_helpers.py::test_set_rubric_can_select_profile_template backend/tests/test_runner.py::test_llm_template_run_bootstraps_artifact_for_reducer -q` → 10 passed; `npm run build` passed.
75. Audited `backend/tmp/studio-workspaces/s_53e37002cad3/io` after user reported input/output looked wrong:
   - `s3/s5/s6/s8` STAR inputs now carry section-scoped assignments, but one worker was assigned `## (intro)` because the runner treated the document title/preamble as a section.
   - Several worker outputs emitted invalid placeholder findings (`URL: n/a`, `QUOTE: n/a`), which the reducer cannot turn into grounded content.
   - Reducer/phase outputs retained skeleton placeholders such as `_(pending - needs sourced content)_` after adding real content, causing false quality failures.
   - The final artifact duplicated full report sections/titles and retained placeholders; run-summary score was 0.6052 with duplicate heading and placeholder weaknesses.
76. Fixed two deterministic causes from the `s_53e37002cad3/io` audit:
   - Runner now filters worker assignment headings to real `##` sections only, excluding `(intro)`/title pseudo-sections.
   - Added `strip_satisfied_placeholders()` and run it after artifact normalization so pending markers are removed only from sections that now contain real content.
   - Validation: `pytest backend/tests/test_section_ownership.py::test_strip_satisfied_placeholders_keeps_empty_sections_only backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_runner.py::test_llm_template_run_bootstraps_artifact_for_reducer -q` → 3 passed; import check passed.
77. Added active reducer patch-shaping and enforcement after reviewing the bad `s_53e37002cad3/io` outputs:
   - Root cause: the reducer prompt asked for PATCHES but did not sufficiently shape patch content, so weak model outputs could put a full document, repeated H1/title blocks, or skeleton placeholders inside a JSON patch. A writeback-only guard would be passive because malformed content can already stack during `reduce_patches`.
   - `backend/studio/findings.py` now tells the reducer the explicit patch content contract first: short sourced paragraph/sentence only, copied http(s) URL required, no markdown headings, no report title/full document, no template placeholder/empty-section marker, and `[]` when the contract cannot be met.
   - Added `_sanitize_llm_patches()` as the enforcement layer after shaping. It drops LLM patches with invalid op, missing/nonexistent anchor, non-section insert target, no URL, heading/full-document shape, placeholder markers, or oversized content. Deterministic `RESEARCH_FINDING` floor patches still run, so rejection does not make the phase passive.
   - `backend/studio/runner.py` now normalizes and strips satisfied placeholders from the writeback candidate before `accept_rewrite()` and persistence, so raw reducer echo text is not promoted to the next phase baseline.
   - Added regression tests for bad full-document LLM patches, invalid LLM patch plus valid deterministic floor, and prompt contract shaping.
   - Validation: `pytest backend/tests/test_runner.py::test_section_reducer_emits_patches_no_full_regen backend/tests/test_runner.py::test_section_reducer_drops_full_document_llm_patch backend/tests/test_runner.py::test_section_reducer_uses_floor_when_llm_patch_is_invalid backend/tests/test_runner.py::test_section_reducer_deterministic_floor_from_findings backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker -q` → 5 passed.
   - Genericity audit on `studio/findings.py` and `studio/runner.py`: no genericity issues.
78. Ran normal E2E probes against the catalog-management report task:
   - Bare API session `s_e89d87dc40dd` did not set a rubric/template, so `target_doc` stayed `None`, no `artifact.md` was created, and final score was 0.24. This is not comparable to the UI path but proves headless tests must apply `/rubric/defaults` + `/session/{id}/rubric` when validating artifact reducer behavior.
   - Rubric-configured pre-patch run `s_f448d4163eed` linked every phase to `artifact.md`, but worker prompts searched ambiguous fragments like `remote agent loops`, producing off-topic sources (`remote.com`, Chrome Remote Desktop, resume/pandas examples) and leaving the artifact skeleton unchanged.
   - Patched executor prompt in `backend/studio/prompts.py`: workers are now goal-bounded rather than goal-blind. The prompt includes `TASK GOAL` for search relevance/WHY, tells workers to build searches from `TASK GOAL + assigned focus + target section`, and says empty weaknesses mean pending assigned sections must be filled with sourced evidence.
   - Rubric-configured post-prompt run `s_c603986f6076` verified the new prompt appears in real worker input and improved source relevance: outputs cited agent-skills and Microsoft multi-agent architecture sources instead of ambiguous "remote"/"skills" web results.
79. Fixed the next artifact writeback blocker found in `s_c603986f6076`:
   - Root cause: valid reducer patches could insert content immediately after a heading anchor, producing glued headings like `## Executive SummaryAgent...`. `accept_rewrite()` then could not match old section identity (`## Executive Summary`) and rejected the populated artifact, leaving the skeleton at 555 chars.
   - `_sanitize_llm_patches()` now block-separates every accepted LLM patch as `\n\n... \n` before `reduce_patches()`, so valid content cannot corrupt heading identity.
   - Added `test_section_reducer_block_separates_llm_patch_content`.
   - Validation: `pytest backend/tests/test_runner.py::test_executor_prompt_frames_research_not_planning backend/tests/test_runner.py::test_section_reducer_emits_patches_no_full_regen backend/tests/test_runner.py::test_section_reducer_drops_full_document_llm_patch backend/tests/test_runner.py::test_section_reducer_block_separates_llm_patch_content backend/tests/test_runner.py::test_section_reducer_uses_floor_when_llm_patch_is_invalid backend/tests/test_runner.py::test_section_reducer_deterministic_floor_from_findings -q` → 6 passed.
   - Genericity audit on `studio/prompts.py`, `studio/findings.py`, and `studio/runner.py`: no genericity issues.
80. Live-verified the combined prompt + reducer patch fixes:
   - Restarted backend and ran rubric-configured normal session `s_18274bd06638` with Gemma, max_agents=1.
   - Evidence: `artifact.md` grew from the 556-byte skeleton to 2,621 bytes by step `s5`, proving writeback now accepts populated sections.
   - Formatting check: the artifact has clean block-separated headings (`## Executive Summary`, `## Scope and Research Questions`, `## Background and Context`) rather than glued headings like `## Executive SummaryAgent`.
   - Relevance check: sources in `io/*.out.md` and artifact are on-topic (`Agent Skills for Large Language Models...`, Microsoft multi-agent reference architecture); no `remote.com`, Chrome Remote Desktop, resume, or pandas drift appeared in the checked outputs.
   - Stopped the SSE verification client after collecting early proof to avoid consuming more model time; backend remains running on port 8770.
   - Remaining open issue: later sections still show `_(pending - needs sourced content)_`; next work should improve coverage/assignment so every required section gets evidence, not just the first few sections.
81. Fixed one coverage cause for pending later sections:
   - Root cause: `agentkit.topology.dynamic.MapStrategy` ignored `PlanStep.worker_foci` when upstream had no extracted item list. LLM-selected MAP phases therefore fell back to one generic worker instead of section-owned workers, so later template sections could remain pending.
   - Updated MAP no-items fallback to use explicit `worker_foci` before falling back to a single generic call. This mirrors STAR/MESH behavior and keeps the shared topology layer domain-free.
   - Added `test_map_workers_use_section_foci_when_no_upstream_items`.
   - Validation: `pytest backend/tests/test_section_ownership.py::test_star_workers_use_explicit_section_foci_not_generic_facets backend/tests/test_section_ownership.py::test_map_workers_use_section_foci_when_no_upstream_items backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance -q` → 3 passed.
   - Genericity audit on `studio/prompts.py`, `studio/findings.py`, `studio/runner.py`, and `studio/planning.py`: no genericity issues.
82. Live-verified MAP section-focus behavior after restarting backend:
   - Session: `s_e4df76cf9882`, rubric-configured normal run with Gemma and `max_agents=3`.
   - Evidence: step `s2` selected `map` and spawned three section-focused workers instead of one generic fallback:
     - spoke0: Executive Summary, Scope and Research Questions, Background and Context
     - spoke1: Key Findings, Evidence and Analysis, Implications or Recommendations
     - spoke2: Limitations and Uncertainty, References
   - `artifact.md` grew to 4,929 bytes during the MAP phase. Stopped the SSE client after collecting proof to avoid extra model spend.
83. Fixed the next quality failure found in `s_e4df76cf9882/artifact.md`:
   - Symptom: the template was present, but section bodies repeated the same source-backed claims multiple times, producing a citation dump rather than a coherent report.
   - Root cause: `_sanitize_llm_patches()` enforced patch shape but did not dedupe accepted LLM patches against URLs already cited in the target section or against other accepted LLM patches in the same reducer pass. The deterministic floor had document-level URL dedupe, but LLM patches could still reinsert the same source into the same section.
   - Fix: `backend/studio/findings.py` now computes cited URLs per markdown section and drops LLM patches whose source URL already appears in that target section, while still allowing the same source to support a different section. It also drops same-source duplicate LLM patches within one reducer batch.
   - Added regression tests:
     - `test_section_reducer_drops_duplicate_llm_source_in_target_section`
     - `test_section_reducer_allows_same_llm_source_in_different_section`
   - Validation: focused reducer/section tests passed (`9 passed`), genericity audit on `studio/prompts.py`, `studio/findings.py`, `studio/runner.py`, and `studio/planning.py` returned `no genericity issues`.
   - Operational step: restarted backend on port `8770` so new sessions use the patched reducer.
84. User added a standing validation requirement:
   - Every completed test or E2E verification must be compared with the previous comparable run before it counts as success.
   - Drift in output quality, artifact structure, coverage, topology behavior, logs, scores, or failure mode is not allowed without root-cause explanation.
   - If the previous comparable run failed, the next run must show measurable improvement against that failure before it counts as success.
   - Added these rules to `AGENTS.md` under `Project Guardrail: Validation Drift`.
85. Applied the validation-drift rule to the fresh E2E `s_40c4382877d0`:
   - Previous failed baseline: `s_e4df76cf9882` had 8 H2 sections, 0 placeholders, 32 extra duplicate long sentences, and repeated source claims in sections.
   - Current raw E2E after the reducer URL-dedupe fix: duplicate long-sentence extras improved from 32 to 3, but structure regressed to 51 H2 headings because later finalization/publish-revision output re-expanded duplicated template sections. Per the new rule, this was a failure, not a success.
   - Root cause: `normalize_artifact()` could collapse the artifact to 8 H2 sections, but LLM readability/publish-revision output could still overwrite `artifact.md` after earlier normalization.
   - Fixes:
     - `backend/studio/runner.py` now applies `strip_satisfied_placeholders(normalize_artifact(...))` to readability output and publish-revision output before either can replace the artifact.
     - `backend/studio/report_quality.py` now rejects duplicate `##` section headings as a generic publish-readiness failure.
     - Added `test_duplicate_report_sections_fail_publish_gate`.
   - Validation:
     - `pytest backend/tests/test_report_quality.py::test_duplicate_report_sections_fail_publish_gate backend/tests/test_report_quality.py::test_cited_on_topic_report_passes_publish_gate backend/tests/test_runner.py::test_section_reducer_drops_duplicate_llm_source_in_target_section backend/tests/test_runner.py::test_section_reducer_allows_same_llm_source_in_different_section -q` → 4 passed.
     - Genericity audit on `studio/report_quality.py`, `studio/runner.py`, and `studio/findings.py`: no genericity issues.
     - Diagnostic comparison: normalized `s_40c4382877d0` would be 10,346 chars, 8 H2 headings, 0 placeholders, 0 duplicate long-sentence extras, and publish-ready under the deterministic gate.
   - Restarted backend on port `8770` to use the new code.
86. Ran post-fix E2E `s_5c9736ef5aa1` and applied the drift rule:
   - Result: completed in 690.9s, 266,841 tokens, publish gate passed.
   - Comparison:
     - `s_e4df76cf9882`: 8 H2, 0 duplicate heading extras, 32 duplicate long-sentence extras.
     - `s_40c4382877d0`: 51 H2, 43 duplicate heading extras, 3 duplicate long-sentence extras.
     - `s_5c9736ef5aa1`: 7 H2, 0 duplicate heading extras, 0 duplicate long-sentence extras.
   - Verdict: improved the prior duplicate-content and duplicate-heading failures, but not fully correct because the `References` section heading was lost and references became a trailing link list under the previous section.
   - User correctly rejected hard final template enforcement: the hub/reducer must be allowed to add or change sections during the process. Do not force the original template as a final schema.
   - Reverted the attempted rigid `enforce_template_outline()` helper. Kept generic safeguards only: LLM readability/publish-revision outputs are normalized before writeback, duplicate headings fail publish gate, and unfinished placeholders fail publish gate.
   - Updated `PLAN-research-report-skill-package-improvements.md` Workstream O with the preferred section-file architecture: hub sees whole artifact and assigns; workers edit bounded section files; reducer sees whole + edited files, merges and verifies coverage, writes section files, then assembles the next whole artifact for the next hub.
   - Added the active-outline rule: the initial template is only the starting outline. When hub/reducer accepts a new/renamed/reordered/removed section, the run's active template/outline must be updated and later assignment coverage, publish checks, scoring, and export use that active outline instead of the original static template.
87. Implemented the minimal active-outline tracking slice:
   - `backend/studio/runner.py` now tracks `rubric_config["active_template"]` as a conservative union of the starting template plus accepted `##` artifact headings. It updates after accepted artifact writes, normalization writes, finalization writes, publish-revision writes, and epoch reverts. It does not remove original template sections automatically.
   - Section assignment, synthetic coverage assignment, false-weakness filtering, epoch preference, rubric scoring, and publish readiness now use the active outline.
   - `backend/studio/report_quality.py` now accepts `required_sections` and fails publish readiness when the final report misses active-outline sections; this catches the `s_5c9736ef5aa1` failure where `References` became a trailing link list while the gate still passed.
   - Added tests:
     - `test_active_template_tracks_added_sections_without_removing_original`
     - `test_missing_active_outline_section_fails_publish_gate`
   - Validation/drift comparison: previous comparable focused suite was 6 passed; updated suite is 7 passed. Genericity audit on `studio/runner.py`, `studio/report_quality.py`, and `studio/findings.py`: no genericity issues.
88. Updated the plan to start building section-file ownership:
   - Moved section-file artifact ownership and active-outline persistence to priority 1.
   - Added `Build Start Plan: Section Files And Active Outline` with four slices:
     1. Add `backend/studio/section_workspace.py` primitives and tests.
     2. Sync runner assignment context from `active_outline.json`.
     3. Make reducer merge section files and reassemble `artifact.md`.
     4. Run E2E drift validation against `s_e4df76cf9882`, `s_40c4382877d0`, and `s_5c9736ef5aa1`.
   - Key rule preserved: active outline can evolve when hub/reducer accepts structural changes; original template is only the starting outline, not a forced final schema.
89. Implemented Build Start Plan Slice 1:
   - Added `backend/studio/section_workspace.py`.
   - Added `backend/tests/test_section_workspace.py`.
   - Implemented deterministic section workspace primitives:
     - `slugify_section(title)`.
     - `split_artifact_to_sections(text, initial_outline)`.
     - `write_section_workspace(root, artifact_text, initial_outline)`.
     - `assemble_artifact_from_sections(root)`.
   - Workspace shape:
     - `artifact.md` remains the assembled report.
     - `sections/active_outline.json` stores preamble plus ordered section `{title, file}` entries.
     - `sections/*.md` store one section each, including its `##` heading.
   - Behavior:
     - Initial outline sections missing from the artifact remain in `active_outline.json` and get placeholder section files.
     - New artifact `##` sections append to the active outline.
     - Assembly is deterministic and uses no LLM call.
   - Validation/drift comparison:
     - New Slice 1 suite plus active-outline/publish checks: `pytest backend/tests/test_section_workspace.py backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_report_quality.py::test_missing_active_outline_section_fails_publish_gate -q` → 5 passed.
     - Genericity audit on `studio/section_workspace.py`, `studio/runner.py`, and `studio/report_quality.py`: no genericity issues.
90. Implemented Build Start Plan Slice 2 assignment context:
   - Runner syncs accepted/bootstrapped artifacts into `sections/active_outline.json` and ordered section files.
   - Worker section lists now come from `active_outline.json` when available, with fallback to the assembled artifact/template.
   - Added `section_file_map(root)` so each worker focus can name the concrete section file path for its assigned heading.
   - Worker focus text now includes `ASSIGNED SECTION FILES` and forbids combining multiple assigned sections into one file.
   - Production runner policy is one section file per worker focus until all active section files are assigned; multi-section focus support remains only as a compatibility fallback.
   - Updated plan Slice 2 with the one-file-per-agent safety rule and fallback rationale.
   - Validation:
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py::test_worker_foci_include_assigned_sections_weaknesses_and_create_guidance backend/tests/test_section_ownership.py::test_multi_section_worker_focus_keeps_distinct_file_targets backend/tests/test_section_ownership.py::test_one_section_file_per_worker_focus_assigns_all_files backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_report_quality.py::test_missing_active_outline_section_fails_publish_gate backend/tests/test_genericity_audit.py -q` → 11 passed.
91. Tightened section assignment into an explicit one-file fetch queue:
   - Added `build_section_assignment_queue()` in `backend/studio/planning.py`; it builds one section-file focus per queue item.
   - Worker prompts now include `ASSIGNMENT QUEUE FETCH` and say one worker call may fetch exactly one queued section file.
   - Runner uses the assignment queue for section workers.
   - Runner now prevents `max_agents` from truncating the section queue: queue length controls total section foci, while `max_workers` remains the concurrency throttle.
   - Added repo guardrail: section workers fetch one section file per worker call; do not batch multiple section files into one worker output.
   - Validation:
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py::test_worker_foci_include_assigned_sections_weaknesses_and_create_guidance backend/tests/test_section_ownership.py::test_multi_section_worker_focus_keeps_distinct_file_targets backend/tests/test_section_ownership.py::test_one_section_file_per_worker_focus_assigns_all_files backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_report_quality.py::test_missing_active_outline_section_fails_publish_gate backend/tests/test_genericity_audit.py -q` → 13 passed.
   - Attempted to inscribe the durable rule in guild lore, but `lore_inscribe` failed with a project foreign-key constraint; the rule is preserved in `AGENTS.md` and this worklog.
92. Added assignment lifecycle persistence:
   - Added atomic assignment rows: `{agent_id, section, file, status}`.
   - Persisted rows to `sections/assignment_queue.json` before worker dispatch.
   - Rows are deleted only after `run_plan()` returns worker records for those calls.
   - Normal runs now use the same workspace-root fallback as diagnostics so the queue exists even without `_eff_ws2`.
   - Validation:
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py::test_worker_foci_include_assigned_sections_weaknesses_and_create_guidance backend/tests/test_section_ownership.py::test_multi_section_worker_focus_keeps_distinct_file_targets backend/tests/test_section_ownership.py::test_one_section_file_per_worker_focus_assigns_all_files backend/tests/test_section_ownership.py::test_section_assignment_rows_are_atomic_agent_file_pairs backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_report_quality.py::test_missing_active_outline_section_fails_publish_gate backend/tests/test_genericity_audit.py -q` → 15 passed.
     - `python -m py_compile backend/studio/planning.py backend/studio/runner.py backend/studio/section_workspace.py` → passed.
93. Made queue rows self-contained:
   - Each assignment row now includes the full worker assignment text in `assignment`, in addition to `agent_id`, `section`, `file`, and `status`.
   - Runner persists the row list first, then derives worker foci from the same row `assignment` values. This avoids drift between queued work and dispatched work.
   - Updated guardrail and plan: an agent can fetch one queue row and receive the complete file target, guidance, and section-specific weaknesses.
   - Validation:
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py::test_worker_foci_include_assigned_sections_weaknesses_and_create_guidance backend/tests/test_section_ownership.py::test_multi_section_worker_focus_keeps_distinct_file_targets backend/tests/test_section_ownership.py::test_one_section_file_per_worker_focus_assigns_all_files backend/tests/test_section_ownership.py::test_section_assignment_rows_are_atomic_agent_file_pairs backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_report_quality.py::test_missing_active_outline_section_fails_publish_gate backend/tests/test_genericity_audit.py -q` → 15 passed.
     - `python -m py_compile backend/studio/planning.py backend/studio/runner.py backend/studio/section_workspace.py` → passed.
94. Started Slice 3 reducer section-file writeback:
   - Added `_write_artifact_through_sections()` in `backend/studio/runner.py`.
   - Accepted reducer output now writes section files first through `write_section_workspace()`, then reads the assembled `artifact.md`.
   - Per-phase normalization also writes through section files before updating active outline.
   - Added `test_section_writeback_assembles_artifact_from_section_files`.
   - Validation:
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_section_ownership.py::test_section_assignment_rows_are_atomic_agent_file_pairs backend/tests/test_report_quality.py::test_missing_active_outline_section_fails_publish_gate backend/tests/test_genericity_audit.py -q` → 12 passed.
     - `python -m py_compile backend/studio/planning.py backend/studio/runner.py backend/studio/section_workspace.py` → passed.
95. Routed final artifact writebacks through section files:
   - Final normalization, lint repair, URL neutralization, epoch revert, post-gate finalize, and publish revision writebacks now use `_write_artifact_through_sections()` instead of direct `artifact.md` replacement.
   - `result_output` is refreshed from the assembled artifact when section-first writing changes exact text/newlines.
   - `result.md` grounded-full archive remains a direct separate snapshot by design.
   - Validation:
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_done_writes_result_file backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_section_ownership.py::test_section_assignment_rows_are_atomic_agent_file_pairs backend/tests/test_report_quality.py::test_missing_active_outline_section_fails_publish_gate backend/tests/test_genericity_audit.py -q` → 13 passed.
     - `python -m py_compile backend/studio/planning.py backend/studio/runner.py backend/studio/section_workspace.py` → passed.
96. Made reducer-created sections explicit:
   - Missing reducer `PATCH_TARGET` anchors that name a `##` heading now append that heading plus the grounded content, instead of appending orphan content.
   - Section-first assembly then creates the matching section file and active-outline entry.
   - Updated `test_section_reducer_demotes_missing_anchor_no_conflict_marker` and `test_section_writeback_assembles_artifact_from_section_files`.
   - Validation:
     - `pytest backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py::test_section_assignment_rows_are_atomic_agent_file_pairs backend/tests/test_report_quality.py::test_missing_active_outline_section_fails_publish_gate backend/tests/test_genericity_audit.py -q` → 11 passed.
     - `python -m py_compile backend/studio/findings.py backend/studio/planning.py backend/studio/runner.py backend/studio/section_workspace.py` → passed.
97. Full focused section/reducer validation exposed and fixed an older outline-merge bug:
   - Failed run: broader focused suite had 1 failure in `test_n1_no_double_outline_when_concepts_present_under_other_names`; `_merge_missing_sections()` appended `Background and Context` to a full agent-authored outline because one long template concept was only partially token-covered.
   - Fix: when a document already has at least a full template-sized outline, tolerate a small residual unmatched template set instead of appending a parallel skeleton.
   - This is generic source-code behavior: no domain-specific report prose or fixed generated content added.
   - Validation improved from `1 failed, 66 passed` to:
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_section_reducer_emits_patches_no_full_regen backend/tests/test_runner.py::test_section_reducer_drops_full_document_llm_patch backend/tests/test_runner.py::test_section_reducer_block_separates_llm_patch_content backend/tests/test_runner.py::test_section_reducer_drops_duplicate_llm_source_in_target_section backend/tests/test_runner.py::test_section_reducer_allows_same_llm_source_in_different_section backend/tests/test_runner.py::test_section_reducer_uses_floor_when_llm_patch_is_invalid backend/tests/test_runner.py::test_section_reducer_deterministic_floor_from_findings backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_report_quality.py::test_missing_active_outline_section_fails_publish_gate backend/tests/test_genericity_audit.py -q` → 67 passed.
     - `python -m py_compile backend/studio/artifact_text.py backend/studio/findings.py backend/studio/planning.py backend/studio/runner.py backend/studio/section_workspace.py` → passed.
98. Pinned merged document section ordering:
   - Added `test_write_section_workspace_reorders_to_active_outline`.
   - The section workspace already assembled from `active_outline.json`; the regression now proves out-of-order reducer output is reassembled as active-outline sections first, with newly accepted sections appended after the existing outline.
   - Updated plan Slice 3 with the ordering acceptance rule.
   - Validation:
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_section_ownership.py backend/tests/test_genericity_audit.py -q` → 57 passed.
     - `python -m py_compile backend/studio/section_workspace.py backend/tests/test_section_workspace.py` → passed.
99. Ran E2E drift validation after section-file queue/writeback changes:
   - Session: `s_bf6d00a39d43`.
   - Runtime: 1045.8s, normal API run with Gemma, rubric profile, `max_agents=3`, `auto_improve=false`.
   - Structural comparison:
     - `s_e4df76cf9882`: 8 H2, 0 duplicate heading extras, 32 duplicate long-sentence extras, References present.
     - `s_40c4382877d0`: 51 H2, 43 duplicate heading extras, 1 duplicate long-sentence extra, References present.
     - `s_5c9736ef5aa1`: 7 H2, 0 duplicate heading extras, 0 duplicate long-sentence extras, References missing.
     - `s_bf6d00a39d43`: 8 H2, 0 duplicate heading extras, 0 duplicate long-sentence extras, References present, active outline has 8 sections, section files=8, assignment queue empty after completion.
   - Verdict: improved the prior duplicate and missing-References failures, but not counted as success because two stray H1 report titles remained after the References section.
   - Fixes:
     - `section_workspace._section_body()` now drops stray H1 lines inside section bodies during section-file assembly.
     - `report_quality.evaluate_publish_readiness()` now fails outputs containing extra H1 report titles after the first document title.
   - Validation:
     - `pytest backend/tests/test_report_quality.py::test_extra_report_titles_fail_publish_gate backend/tests/test_report_quality.py::test_duplicate_report_sections_fail_publish_gate backend/tests/test_report_quality.py::test_missing_active_outline_section_fails_publish_gate backend/tests/test_section_workspace.py::test_write_section_workspace_drops_stray_h1_inside_sections backend/tests/test_genericity_audit.py -q` → 7 passed.
     - `python -m py_compile backend/studio/report_quality.py backend/studio/section_workspace.py backend/tests/test_report_quality.py backend/tests/test_section_workspace.py` → passed.
     - Re-evaluating `s_bf6d00a39d43/artifact.md` with the new publish gate fails on extra report titles, so the failure is now actively blocked.
100. Added generic dynamic title resolution after observing the template H1 survived into merged reports:
   - Cause: `_build_skeleton()` correctly seeds a topic-agnostic placeholder H1, but final writeback did not reliably resolve it before publish.
   - Fix:
     - `artifact_text.resolve_report_title()` preserves a real model-authored H1, replaces skeleton placeholder H1s from the task requirement, and prepends a derived title when a report has sections but no H1.
     - `runner._write_artifact_through_sections()` applies the resolver before section-file assembly so `artifact.md` and section workspace state stay consistent.
     - `report_quality.evaluate_publish_readiness()` now fails unresolved placeholder report titles.
   - This remains generic source-code behavior: it derives a run-specific title from the request and does not encode domain report prose.
   - Validation:
     - `pytest backend/tests/test_section_ownership.py::test_resolve_report_title_replaces_template_placeholder backend/tests/test_section_ownership.py::test_resolve_report_title_preserves_model_title backend/tests/test_report_quality.py::test_placeholder_report_title_fails_publish_gate backend/tests/test_report_quality.py::test_extra_report_titles_fail_publish_gate backend/tests/test_section_workspace.py::test_write_section_workspace_reorders_to_active_outline backend/tests/test_section_workspace.py::test_write_section_workspace_drops_stray_h1_inside_sections -q` → 6 passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py -q` → 71 passed.
     - `python -m py_compile backend/studio/artifact_text.py backend/studio/report_quality.py backend/studio/runner.py backend/tests/test_section_ownership.py backend/tests/test_report_quality.py` → passed.
101. Interrupted invalid old-code confirmation run:
   - Session `s_20a3c32e187b` was launched before the title resolver was loaded.
   - During edits, the backend process serving that run stopped; the client hung waiting on the stream and was interrupted manually.
   - Partial artifact still showed the old failure (`# _(deliverable title - generated from the findings below)_`) while keeping the eight H2 sections in active-outline order, so it is recorded as an interrupted failed comparison, not a successful E2E.
102. Restarted backend on port `8770` with current code and launched corrected comparable E2E:
   - Session: `s_fefebe57f0b4`.
   - Same path as the previous comparable run: Gemma profile, `mode=llm`, tools enabled, `/session/{id}/rubric` with `report_type=general`, `auto_improve=false`, `max_agents=3`, `max_tasks_per_agent=5`.
   - Requirement unchanged: catalog management for local and remote agent loops and skills in a generic research report generator, with citations, risks, and actionable recommendations.
   - Interrupted intentionally during `s3`: early phase output replaced the skeleton title with exact generic `# Research Report`, which is still not a dynamic task title. Not counted as success.
103. Tightened dynamic-title handling for exact generic model titles:
   - `artifact_text._is_placeholder_title()` now treats exact `Research Report`, `Technical Report`, `Final Report`, `Report`, and `Deliverable` H1s as unresolved.
   - The publish gate now also fails exact generic H1 titles.
   - Added tests proving generic H1s are replaced while specific model-authored H1s are preserved.
   - Validation:
     - `pytest backend/tests/test_section_ownership.py::test_resolve_report_title_replaces_template_placeholder backend/tests/test_section_ownership.py::test_resolve_report_title_preserves_model_title backend/tests/test_section_ownership.py::test_resolve_report_title_replaces_generic_model_title backend/tests/test_report_quality.py::test_placeholder_report_title_fails_publish_gate backend/tests/test_report_quality.py::test_generic_report_title_fails_publish_gate backend/tests/test_report_quality.py::test_extra_report_titles_fail_publish_gate backend/tests/test_section_workspace.py::test_write_section_workspace_reorders_to_active_outline backend/tests/test_section_workspace.py::test_write_section_workspace_drops_stray_h1_inside_sections -q` → 8 passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py -q` → 73 passed.
     - `python -m py_compile backend/studio/artifact_text.py backend/studio/report_quality.py backend/studio/runner.py backend/tests/test_section_ownership.py backend/tests/test_report_quality.py` → passed.
104. Fixed dynamic-title resolver input:
   - Session `s_a1c0a44d5a95` was interrupted during `s3` after disk inspection showed `artifact.md` still had `# Research Report`.
   - Root cause: `_write_artifact_through_sections()` called `resolve_report_title()` with `session.requirement`, but the runtime requirement lives in `_original_requirement`; the helper therefore derived the fallback title.
   - Fix: `_write_artifact_through_sections()` now accepts an explicit `requirement`, and runtime call sites pass `_original_requirement`.
   - Validation:
     - `pytest backend/tests/test_section_ownership.py::test_resolve_report_title_replaces_generic_model_title backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_report_quality.py::test_generic_report_title_fails_publish_gate -q` → 3 passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py -q` → 73 passed.
     - `python -m py_compile backend/studio/runner.py backend/studio/artifact_text.py` → passed.
105. Reworked H1 title handling as live template state after user correction:
   - User correction: report title is assigned based on task by hub/reducer, written back to template, agents are assigned H1/H2 work, and later title edits must update the template.
   - Failure observed: session `s_a9449e6fdc64` crashed in `s1` with `name '_original_requirement' is not defined` after early writeback tried to use a variable outside scope.
   - Fixes:
     - Early per-phase writebacks now pass the in-scope `requirement` to `_write_artifact_through_sections()`.
     - Accepted artifact H1 is now stored in `rubric_config["active_title"]` via `_update_active_template_from_artifact()`.
     - Template steering now includes the current H1 title when one exists, so subsequent hub/worker phases see the active title alongside H2 sections.
     - `_write_artifact_through_sections()` prefers `active_title` when replacing placeholder/generic H1s, while still preserving a specific model-authored H1 and deriving a task title only as fallback.
   - Remaining design note: current section files isolate H2 body edits; H1 is now tracked as template state, but a dedicated `000-title.md`/H1 queue row can be added if strict one-file-per-H1 editing is required.
   - Validation:
     - `pytest backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py::test_generic_report_title_fails_publish_gate backend/tests/test_section_ownership.py::test_resolve_report_title_replaces_generic_model_title -q` → 6 passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py -q` → 75 passed.
     - `python -m py_compile backend/studio/runner.py backend/studio/artifact_text.py backend/tests/test_runner.py` → passed.
106. Tightened fallback H1 derivation after live run produced a clipped title:
   - Session `s_084c167e026d` passed the previous crash and produced a dynamic H1, but it was malformed: `Catalog management for local and remote agent loops and skills in a generic research`.
   - Root cause: the fallback title derivation capped the whole topic at 14 words before removing long contextual clauses.
   - Fix: `_derive_title_from_requirement()` now removes long `in/within ...` context clauses before word-capping, preserving subject clauses like `for local and remote agent loops and skills`.
   - Validation:
     - `pytest backend/tests/test_section_ownership.py::test_resolve_report_title_drops_long_context_clause backend/tests/test_section_ownership.py::test_resolve_report_title_replaces_generic_model_title backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title -q` → 4 passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py -q` → 76 passed.
     - `python -m py_compile backend/studio/artifact_text.py backend/tests/test_section_ownership.py` → passed.
107. Completed comparable E2E with live-H1 and title-clause fixes:
   - Session: `s_d80886665d0e`.
   - Runtime: 880.9s, Gemma profile, rubric profile, `max_agents=3`, `auto_improve=false`.
   - Publish gate passed.
   - Structural metrics: one H1 (`Catalog management for local and remote agent loops and skills`), 8 H2 headings in active-outline order, 0 duplicate H2 extras, References present, 8 section files, assignment queue empty.
   - Drift check:
     - Improved prior title failures (`s_bf6d00a39d43` had 2 extra H1s; `s_20a3c32e187b` had placeholder H1; `s_fefebe57f0b4`/`s_a1c0a44d5a95` had generic H1; `s_084c167e026d` had clipped H1).
     - Found one new long duplicate sentence, so not accepted as final success yet.
108. Added generic long-sentence duplicate cleanup:
   - The duplicate was a repeated source-navigation sentence with the same URL. It was not source-specific logic; it exposed that paragraph dedup missed exact long sentence repeats across sections.
   - `normalize_artifact()` now drops exact repeated long sentences only when every URL in the duplicate sentence has already appeared earlier, preserving unique citations.
   - Validation:
     - `pytest backend/tests/test_section_ownership.py::test_normalize_artifact_dedupes_repeated_long_sentences_when_url_seen backend/tests/test_section_ownership.py::test_normalize_artifact_unglues_headings_and_dedupes backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files -q` → 3 passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py -q` → 77 passed.
     - `python -m py_compile backend/studio/artifact_text.py backend/tests/test_section_ownership.py` → passed.
     - Applying the new normalizer to `s_d80886665d0e/artifact.md` changed duplicate long-sentence extras 1→0, preserved all 8 unique URLs, kept the same H1/H2 order, and still passed publish readiness.
109. Completed final comparable E2E after restarting backend with the latest code:
   - Session: `s_a5a2f9f64f7f`.
   - Runtime: 848.1s, Gemma profile, rubric profile, `max_agents=3`, `auto_improve=false`.
   - Publish gate passed.
   - Structural metrics:
     - one task-specific H1: `Catalog management for local and remote agent loops and skills`;
     - 8 H2 sections: `Executive Summary`, `Scope and Research Questions`, `Background and Context`, `Key Findings`, `Evidence and Analysis`, `Implications or Recommendations`, `Limitations and Uncertainty`, `References`;
     - `active_outline.json` exactly matches the H2 order;
     - 0 duplicate H2 extras;
     - 0 duplicate long-sentence extras;
     - 0 placeholder matches;
     - 8 unique URLs preserved;
     - `References` present;
     - 8 section files;
     - assignment queue empty.
   - Drift comparison:
     - Improves `s_e4df76cf9882`: placeholder H1 fixed and duplicate long-sentence extras 32→0.
     - Improves `s_40c4382877d0`: duplicate H2 extras 43→0 and extra H1 fixed.
     - Improves `s_5c9736ef5aa1`: missing References fixed and extra H1 fixed.
     - Improves `s_bf6d00a39d43`: extra H1s 2→0 while preserving ordered H2s, queue completion, and references.
     - Improves `s_d80886665d0e`: duplicate long-sentence extras 1→0 with no structural regression.
   - Updated `PLAN-research-report-skill-package-improvements.md` so the H1/title is explicitly live template state: hub proposes it from the task, reducer accepts or improves it, writeback stores it in active template metadata, and later agent title changes must update the active template.
110. Committed the accepted section/title slice:
   - Commit message: `Stabilize report section ownership`.
   - Included files: `AGENTS.md`, plan/worklog, `artifact_text.py`, `findings.py`, `planning.py`, `report_quality.py`, `runner.py`, new `section_workspace.py`, and focused section/title/report-quality tests.
   - Left unrelated pre-existing dirty files unstaged, including frontend graph/config edits, shared `agentkit` edits, deleted design/handoff docs, and model/profile/task-run changes outside this slice.
111. Added and committed profile-based report templates:
   - Commit message: `Add profile-based report templates`.
   - Added `deep_technical` as a generic profile preset using the enhanced report structure while removing the domain-specific `agent_system_technical` preset before commit.
   - `/rubric/defaults` now returns built-in template presets, and `/session/{id}/rubric` can select a preset by `report_type` unless an explicit template is provided.
   - The rubric UI exposes a native select for report profile templates and copies the selected preset sections into the editable template list.
   - Added focused backend tests in `test_report_profiles.py` and `test_rubric_profile_api.py`.
   - Validation:
     - `pytest backend/tests/test_report_profiles.py backend/tests/test_rubric_profile_api.py -q` → 9 passed.
     - `python -m py_compile backend/studio/report_profiles.py backend/studio/app.py backend/tests/test_rubric_profile_api.py` → passed.
     - `npm run build` in `frontend/` → passed.
112. Added and committed structured-control completion caps:
   - Commit message: `Cap structured LLM control outputs`.
   - Added `MaxTokensClient`, a minimal wrapper that passes `max_tokens` when the underlying client supports it and falls back for plain clients.
   - Updated the Gemma profile to use its real 100K context setting while capping planner and topology-selector completions to 1024 and 512 tokens.
   - This fixes the clean-checkout dependency from the runner's already-committed `MaxTokensClient` usage.
   - Validation:
     - `pytest backend/tests/test_client_max_tokens.py backend/tests/test_model_profiles.py backend/tests/test_runner.py::test_gemma_planning_and_topology_selection_are_capped -q` → 5 passed.
     - `python -m py_compile backend/studio/client.py backend/studio/model_profiles.py backend/tests/test_client_max_tokens.py backend/tests/test_model_profiles.py` → passed.
113. Added and committed evaluator cleanup helpers:
   - Commit message: `Add evaluator cleanup helpers`.
   - Added `mask_fenced_code()` so markdown heading scans ignore `#` lines inside fenced code.
   - Added deterministic false-weakness refutation for mined absence/truncation claims when the document demonstrably contains the claimed missing/complete section.
   - Added shared `consolidate_findings()` to merge same-URL findings, strip repetitive scaffold prefixes, and cap findings per target section.
   - This closes another clean-checkout dependency because committed runner/findings/tests already call these helpers.
   - Validation:
     - `pytest backend/tests/test_section_ownership.py::test_n4_mask_hides_incode_hash_lines backend/tests/test_section_ownership.py::test_n4_sections_present_ignores_incode_heading backend/tests/test_section_ownership.py::test_n4_detect_gaps_skips_code_comments backend/tests/test_section_ownership.py::test_n2_n3_refutes_false_claims_keeps_real_ones backend/tests/test_section_ownership.py::test_n3_section_ends_cleanly backend/tests/test_section_ownership.py::test_consolidate_findings_same_url_merge_keeps_richest backend/tests/test_section_ownership.py::test_consolidate_findings_density_cap_per_target backend/tests/test_rubric.py -q` → 13 passed.
     - `python -m py_compile backend/studio/rubric.py backend/studio/task_runs.py ../agentkit/artifacts/dedup.py` → passed.
114. Added and committed topology rationale surfacing:
   - Commit message: `Surface topology rationale`.
   - Added shared `assign_topologies_with_choices()` and `PlanStep.worker_foci` support required by the committed Studio runner/planning path.
   - Topology events now carry rationale/question metadata through frontend store/layout and show rationale as a topology-chip tooltip.
   - Planner prompt asks the LLM for topology intent and explains the design-principle options without hardcoding the final topology in production code.
   - Validation:
     - `pytest backend/tests/test_section_ownership.py::test_e2_assign_with_choices_returns_rationale backend/tests/test_section_ownership.py::test_e3_assign_with_choices_uses_infer_spec_under_llm backend/tests/test_section_ownership.py::test_worker_foci_include_assigned_sections_weaknesses_and_create_guidance backend/tests/test_runner.py::test_gemma_planning_and_topology_selection_are_capped backend/tests/test_runner.py::test_runner_maps_state_level_selector_topology_to_runtime_shape backend/tests/test_runner.py::test_hill_climb_honors_selected_topology_no_force_star -q` → 6 passed.
     - `python -m py_compile backend/studio/prompts.py ../agentkit/planner/core.py ../agentkit/topology/dynamic.py` → passed.
     - `npm run build` in `frontend/` → passed.
115. Ran post-commit regression checkpoint:
   - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py backend/tests/test_report_profiles.py backend/tests/test_rubric_profile_api.py backend/tests/test_client_max_tokens.py backend/tests/test_model_profiles.py -q` → 90 passed.
   - `npm run build` in `frontend/` → passed.
   - Remaining dirty files were not staged: `.gitignore`, deleted design/handoff docs, `backend/pyproject.toml`, mixed `backend/tests/test_m8_m9_helpers.py`, local `.agents/.claude/.codex`, `DESIGN-v2.md`, and `skills-lock.json`.
116. Recorded unified scoring-standard decision:
   - User agreed that introducing `scoring_template` and `scoring_matrix` requires a unified scoring standard.
   - Plan updated so score profiles share one 12-category vocabulary but can vary weights/applicability by report profile.
   - Core weighted score uses frozen run-start `scoring_template` and `scoring_matrix`; dynamic hub/reducer sections update `active_outline` for publish/export and can contribute bonus/supporting quality, but do not rewrite core weights mid-run.
   - This keeps drift comparisons stable while still allowing task-specific template evolution.
117. Fixed publish-gate pass-after-writeback drift and reran E2E:
   - Found session `s_07f06ab4ce3f` emitted `publish-ready: pass`, but rerunning the deterministic gate on saved `artifact.md` failed missing request terms. Root cause: publish revision was evaluated before section-workspace writeback, and the post-writeback saved text was not rechecked.
   - Fixed `runner.py` so accepted publish revisions are re-evaluated after `_write_artifact_through_sections()` before emitting final `publish-ready`.
   - Reran comparable normal Gemma E2E as `s_2dafb96d6fb6` with rubric `general`, tools enabled, `auto_improve=false`, `max_agents=3`, `max_tasks_per_agent=5`.
   - Storage note: because backend was started from repo root, this run is under `tmp/studio-workspaces/s_2dafb96d6fb6`, not `backend/tmp/studio-workspaces`.
   - Results: publish gate passed on stream and on saved artifact; one task-specific H1; 8 H2 sections matching `active_outline.json`; 0 duplicate H2 extras; 0 duplicate long-sentence extras; 0 placeholders; 19 unique URLs; References present; 8 section files; assignment queue empty; `agent_io.jsonl` present.

## Current Next Steps

1. Inspect the remaining dirty files before any future commit; do not stage them blindly.
2. Backend is currently responding on port `8770`; use real app endpoints such as `/backends` because `/` and `/health` are not defined.
3. Next implementation slice can build on the committed section/title workspace and profile-template selector. Continue to compare every completed E2E against previous runs.

## Notes For Resume

- Do not revert unrelated dirty worktree files.
- Prefer implementing the plan in small testable slices.
- The first completed E2E validated the real system path but proved report quality is still poor without stronger report-stage control.
- The current slice keeps LLM planning as default. Methodology stages from `ref/.../agent_loop.md` are retained as seedable/catalog scaffolding, not automatic routing.
- Static generic/profile templates are code defaults. Learned/approved reusable skeletons belong in `TemplateStore`/DB after audit metadata and replacement/quarantine logic exists.
- The latest E2E (`s_fa0fecbb18a6`) proves compatibility with the original planner/topology path, but not report-quality success.
- Standing guardrail: AgentKit Studio's source code and built-in report-generator mechanisms are generic/task-neutral, but generated reports must be task-specific and evidence-specific. Never add domain-specific production prose, fixed conclusions, model-id report branches, or example-specific templates; implement reusable evidence, prompt, catalog/template, validation, and UI mechanisms instead.
