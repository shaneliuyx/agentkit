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
118. Implemented Workstream C scoring-standard slice from `PLAN-research-report-skill-package-improvements.md`:
   - Added the plan's shared 12-category allowed vocabulary for 100-point scorecards and profile/template-derived `scoring_matrix` presets while keeping `rubric_score()` as the existing deterministic optimizer signal.
   - User correction: scoring must be associated with the selected profile/template; if a category is not in, implied by, or related to the frozen template, it must not appear in the scoring system. Updated code/tests and the plan to omit unrelated categories rather than carrying inactive/zero rows.
   - User clarification: this is still one unified scoring system, based on the original deterministic rubric plus the scoring matrix. `rubric_scorecard_100()` now returns the original base score and signal breakdown alongside the matrix-projected 100-point category scores; runner run summaries include that unified payload.
   - `/rubric/defaults` and `/session/{id}/rubric` now expose/store frozen `scoring_template` and `scoring_matrix`; explicit scoring templates are preserved separately from mutable live templates.
   - Runner scoring now checks ToC completeness against frozen `scoring_template`, while active outline/template changes still drive section writeback and publish readiness.
   - Runner run-summary `agent_io.jsonl` now includes `scorecard_100` as a reporting projection without replacing the stored adjusted rubric score.
   - Added frontend API typing for optional `scoring_template` and `scoring_matrix`.
   - Updated `PLAN-research-report-skill-package-improvements.md` to remove contradictory wording about universal active rows/applicability and to specify template-bound scoring rows.
   - Validation:
     - `pytest backend/tests/test_rubric.py backend/tests/test_rubric_profile_api.py backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original -q` → 15 passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py backend/tests/test_report_profiles.py backend/tests/test_rubric_profile_api.py backend/tests/test_client_max_tokens.py backend/tests/test_model_profiles.py backend/tests/test_rubric.py -q` → 100 passed.
     - `python -m py_compile backend/studio/rubric.py backend/studio/app.py backend/studio/runner.py backend/studio/epoch_gate.py backend/studio/session.py backend/tests/test_rubric.py backend/tests/test_rubric_profile_api.py backend/tests/test_runner.py` → passed.
     - `npm run build` in `frontend/` → passed.
     - Genericity audit for `studio/runner.py`, `studio/rubric.py`, and `studio/app.py` → no genericity issues.
   - Follow-up validation after template-bound scoring correction:
     - `pytest backend/tests/test_rubric.py backend/tests/test_rubric_profile_api.py backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original -q` → 15 passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py backend/tests/test_report_profiles.py backend/tests/test_rubric_profile_api.py backend/tests/test_client_max_tokens.py backend/tests/test_model_profiles.py backend/tests/test_rubric.py -q` → 102 passed.
     - `python -m py_compile backend/studio/rubric.py backend/studio/app.py backend/studio/runner.py backend/studio/session.py backend/tests/test_rubric.py backend/tests/test_rubric_profile_api.py` → passed.
     - `npm run build` in `frontend/` → passed.
     - Genericity audit for `studio/runner.py`, `studio/rubric.py`, and `studio/app.py` → no genericity issues.
   - Follow-up validation after unified original-plus-matrix clarification:
     - `pytest backend/tests/test_rubric.py backend/tests/test_rubric_profile_api.py backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original -q` → 15 passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py backend/tests/test_report_profiles.py backend/tests/test_rubric_profile_api.py backend/tests/test_client_max_tokens.py backend/tests/test_model_profiles.py backend/tests/test_rubric.py -q` → 102 passed.
     - `python -m py_compile backend/studio/rubric.py backend/studio/runner.py backend/studio/app.py backend/tests/test_rubric.py` → passed.
     - `npm run build` in `frontend/` → passed.
     - Genericity audit for `studio/runner.py`, `studio/rubric.py`, and `studio/app.py` → no genericity issues.
   - Validation drift: comparable backend regression checkpoint expanded from 90 passed (step 115) to 102 passed because scoring-standard/template-bound tests were added; no new failure mode appeared. No new E2E was run for this backend/API scoring slice.
119. Routed scoring rules and scorecard weaknesses through task/agent/reducer flow:
   - Added prompt-format helpers in `rubric.py` so the full frozen scoring matrix can be rendered for planner/reducer use and cropped by section relatedness for workers.
   - Task creation/planning now receives the full unified scoring block: original deterministic signals are the base measurements, the frozen scoring matrix defines the 100-point profile/template scorecard, and reducers measure the whole artifact.
   - Section worker assignment queue rows now include `SCORING REQUIREMENTS FOR THIS SCOPE`, filtered to rows related to the assigned section plus universal rows; unrelated section rules are not shown to that worker.
   - Section reducers now receive `FULL SCORING RULES` for whole-artifact review after merging worker findings.
   - Added `scorecard_weaknesses()` so low scorecard categories become section-routable weaknesses. These are prepended into the existing weakness list before adjusted scoring/recording, then flow through `TaskRunStore.record` → `accumulated_weaknesses` → `session.weaknesses` → section worker assignment in the next run.
   - Documented the weakness lifecycle and scoring-rule routing in both `PLAN-research-report-skill-package-improvements.md` and `DESIGN-v2.md`.
   - User correction: for a cold run, scoring rubrics are split across workers; after each reducer/writeback, achieved scoring rows are removed from the next phase's worker prompts and unachieved rows become weaknesses. Reducers, however, always receive the full frozen requirements. Implemented `remaining_scoring_matrix` as run-local worker prompt state only; final scoring/reducers still use the full `scoring_matrix`.
   - Validation:
     - `pytest backend/tests/test_rubric.py backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_section_ownership.py::test_section_reducer_receives_full_scoring_rules -q` → 14 passed.
     - `python -m py_compile backend/studio/rubric.py backend/studio/planning.py backend/studio/findings.py backend/studio/runner.py backend/tests/test_rubric.py backend/tests/test_runner.py backend/tests/test_section_ownership.py` → passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py backend/tests/test_report_profiles.py backend/tests/test_rubric_profile_api.py backend/tests/test_client_max_tokens.py backend/tests/test_model_profiles.py backend/tests/test_rubric.py -q` → 105 passed.
     - `npm run build` in `frontend/` → passed.
     - Genericity audit for `studio/runner.py`, `studio/rubric.py`, `studio/planning.py`, `studio/findings.py`, and `studio/app.py` → no genericity issues.
   - Follow-up validation after reducer-full/worker-shrinking correction:
     - `pytest backend/tests/test_rubric.py backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_section_ownership.py::test_section_reducer_receives_full_scoring_rules -q` → 15 passed.
     - `python -m py_compile backend/studio/rubric.py backend/studio/runner.py backend/tests/test_rubric.py` → passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py backend/tests/test_report_profiles.py backend/tests/test_rubric_profile_api.py backend/tests/test_client_max_tokens.py backend/tests/test_model_profiles.py backend/tests/test_rubric.py -q` → 106 passed.
     - `npm run build` in `frontend/` → passed.
     - Genericity audit for `studio/runner.py`, `studio/rubric.py`, `studio/planning.py`, `studio/findings.py`, and `studio/app.py` → no genericity issues.
   - Validation drift: comparable backend regression checkpoint expanded from 102 passed to 106 passed because routing/weakness/shrinking tests were added; no new failure mode appeared. No new E2E was run for this prompt/routing/weakness slice.
120. Continued after the plan-only decision update:
   - Found and fixed a reducer/full-requirements contradiction in `runner.py`: phase scorecard computation now uses the full frozen `scoring_matrix`, while `_prompt_scoring_matrix()` remains the shrinking helper for worker prompts only.
   - Added `_full_scoring_matrix()` and a focused regression test proving worker prompts can use `remaining_scoring_matrix` without changing reducer/scoring source of truth.
   - Surfaced the existing `scorecard_100` payload in the terminal `done` event and ResultWindow instead of adding a new event or panel. The UI displays total score, base score, and emitted profile/template category rows; unrelated categories remain absent because the backend matrix omits them.
   - Validation:
     - `pytest backend/tests/test_runner.py::test_done_writes_result_file backend/tests/test_runner.py::test_scoring_matrix_helpers_keep_reducers_full backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_section_ownership.py::test_section_reducer_receives_full_scoring_rules backend/tests/test_rubric.py -q` → 17 passed.
     - `python -m py_compile backend/studio/events.py backend/studio/runner.py backend/tests/test_runner.py` → passed.
     - `npm run build` in `frontend/` → passed.
   - No new E2E was run for this UI/contract slice.
121. Fixed the section-worker prompt regression found by E2E `s_4e70dc3a135a`:
   - Root cause: queued section assignments carried scoring rules, but did not restate the executor-only output contract. Weak local workers could answer with plain section markdown or fake bibliography entries, which the reducer correctly discarded.
   - Added a worker-output contract to the shared section assignment text in `planning.py`: workers must return grounded `RESEARCH_FINDING` blocks or reducer-applicable `PATCHES`; `RESEARCH_FINDING` must include `ARTICLE_TITLE`, exact fetched `URL`, `PATCH_TARGET`, `QUOTE`, and `WHY`; no plain markdown report prose or invented References sections.
   - Added `test_section_worker_focus_requires_grounded_reducer_inputs` to lock the contract.
   - Validation:
     - `pytest backend/tests/test_section_ownership.py::test_section_worker_focus_requires_grounded_reducer_inputs backend/tests/test_section_ownership.py::test_one_section_file_per_worker_focus_assigns_all_files backend/tests/test_section_ownership.py::test_section_reducer_receives_full_scoring_rules -q` → 3 passed.
     - `python -m py_compile backend/studio/planning.py backend/tests/test_section_ownership.py` → passed.
     - `pytest backend/tests/test_section_workspace.py backend/tests/test_section_ownership.py backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_runner.py::test_active_template_tracks_added_sections_without_removing_original backend/tests/test_runner.py::test_section_writeback_assembles_artifact_from_section_files backend/tests/test_runner.py::test_section_writeback_prefers_active_report_title backend/tests/test_runner.py::test_section_reducer_demotes_missing_anchor_no_conflict_marker backend/tests/test_runner.py::test_section_assignment_queue_fetches_all_files_despite_agent_cap backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py backend/tests/test_report_profiles.py backend/tests/test_rubric_profile_api.py backend/tests/test_client_max_tokens.py backend/tests/test_model_profiles.py backend/tests/test_rubric.py -q` → 107 passed.
     - Genericity audit for `studio/planning.py`, `studio/runner.py`, `studio/rubric.py`, `studio/findings.py`, and `studio/app.py` → no genericity issues.
   - E2E retry note: corrected the driver to use `auto_improve=false` with `max_epochs=1` because `max_epochs=5` is ignored without auto-improve. Session `s_353133d9a180` was started against foreground uvicorn but produced no SSE data or workspace before manual cancellation after several minutes; no comparable drift judgment was possible.
122. Analyzed why previous workspaces looked better and wired the missing synthesis step:
   - `s_2dafb96d6fb6` looked better because it accumulated many sourced passages before final steps; `s_3bdf12f07b9e` looked better mainly because late single-agent steps produced full prose that writeback accepted. `s_4e70dc3a135a` stayed as placeholders because strict current worker/reducer contracts left no valid findings/patches to merge.
   - Updated `PLAN-research-report-skill-package-improvements.md`: section workers stay strict (`RESEARCH_FINDING` or scoped `PATCHES` only), but once grounded evidence exists a sanctioned final synthesis/refine pass may turn it into coherent report prose while preserving verified URLs. No grounded evidence means keep placeholders and surface weaknesses, not invented prose.
   - Reused existing `_synthesize_analysis()` in `runner.py` before readability refinement and again in post-gate finalization, so reverted/kept artifacts get the same grounded synthesis path without adding a new prompt or subsystem.
   - Backend log check: no per-session backend log existed beyond each workspace's `agent_io.jsonl`; generic `backend/tmp/*.log` did not contain these session IDs.
   - Validation:
     - `python -m py_compile backend/studio/runner.py` → passed.
     - `pytest backend/tests/test_plan_backlog.py::test_synthesis_accepts_when_citations_preserved backend/tests/test_plan_backlog.py::test_synthesis_rejects_when_a_url_is_dropped backend/tests/test_plan_backlog.py::test_synthesis_rejects_when_materially_shorter backend/tests/test_section_ownership.py::test_s4d_windowed_synthesis_large_doc_preserves_urls_and_changes backend/tests/test_section_ownership.py::test_refine_readability_keeps_urls_and_rewrites backend/tests/test_section_ownership.py::test_refine_readability_rejects_url_drop -q` → 6 passed.
     - `pytest backend/tests/test_runner.py -q` → 76 passed.
     - `pytest backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py -q` → 70 passed.
   - Validation drift: no new E2E was run; targeted backend checks expanded from the previous 3-test prompt contract slice to runner/finalization coverage, with no regression observed.
123. Fixed the worker patch-schema mismatch and validated with a comparable E2E:
   - Root cause from live run `s_c333e5fc8596`: the section worker prompt said workers could emit reducer-applicable `PATCHES`, but task guidance still emphasized `PATCH_TARGET`; Gemma therefore returned legacy `PATCH_TARGET`/`CONTENT` full-section prose patches. The strict reducer correctly rejected most of those ungrounded patches.
   - Updated `planning._section_focus_text()` to state the exact accepted `PATCHES` shape: `{"op":"insert_after","anchor":"## Exact Assigned Heading","content":"One short grounded sentence with the exact source URL."}`. `PATCH_TARGET` remains valid only for `RESEARCH_FINDING`; legacy `PATCH_TARGET`/`CONTENT` patch objects are explicitly forbidden.
   - Added deterministic final cleanup: References placeholder notes are replaced by the artifact's collected URLs when URLs exist elsewhere; stale document/section placeholder weaknesses are refuted against the final post-synthesis artifact before adjusted scoring.
   - Updated `PLAN-research-report-skill-package-improvements.md` with the exact patch schema requirement, placeholder cleanup requirement, and current E2E comparison set.
   - Validation:
     - `pytest backend/tests/test_section_ownership.py::test_section_worker_focus_requires_grounded_reducer_inputs backend/tests/test_section_ownership.py::test_one_section_file_per_worker_focus_assigns_all_files backend/tests/test_section_ownership.py::test_section_reducer_receives_full_scoring_rules -q` -> 3 passed.
     - `python -m py_compile backend/studio/planning.py backend/studio/artifact_text.py backend/studio/task_runs.py backend/tests/test_section_ownership.py` -> passed.
     - `pytest backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_report_quality.py backend/tests/test_genericity_audit.py -q` -> 162 passed.
     - Comparable E2E `s_831049d2caee` with Gemma, `embed:{}`, tools enabled, `auto_improve=false`, `max_epochs=1`, `max_agents=3`: completed in ~929s; artifact/result 9,346 bytes; 8 H2 sections; 21 unique URLs; 0 placeholder hits; adjusted score 0.6721; scorecard 91.74.
   - Validation drift:
     - Improved over failed `s_4e70dc3a135a`: 561 bytes / 0 URLs / 8 placeholders / score 0.0894 -> 9,346 bytes / 21 URLs / 0 placeholders / score 0.6721.
     - Improved over pre-schema `s_c333e5fc8596`: 7,272 bytes / 3 URLs / placeholder note / score 0.2602 -> 9,346 bytes / 21 URLs / 0 placeholders / score 0.6721.
     - Improved over older `s_3bdf12f07b9e` on size and citations: 3,825 bytes / 5 URLs -> 9,346 bytes / 21 URLs.
     - Still trails best prior `s_2dafb96d6fb6` on depth/length: 14,135 bytes / 19 URLs, but current run has higher scorecard and no placeholder/reference drift. Remaining weaknesses: publish-gate term coverage, one uncited Evidence section, and practical implementation-risk specificity.
124. Removed publish-gate term noise from internal structure/assignment text:
   - Root cause from `s_831049d2caee`: the publish gate's lexical topic check saw internal prompt text (`Structure the deliverable... top-level headings, in order` and `Focus specifically on... ASSIGNED SECTIONS`) as user-request terms, producing false missing terms like `deliverable`, `toplevel`, `heading`, `order`, `focu`, `assigned`, plus concept-only `citation`/`actionable`.
   - Fixed `report_quality._important_terms()` to strip the internal structure/focus blocks before extracting important terms. Added a tiny normalization so `implementation` matches report prose that says `implement...`; treated `citation`/`actionable` as gate concepts rather than topic terms because URL and recommendation checks cover them.
   - Validation:
     - `pytest backend/tests/test_report_quality.py -q` -> 12 passed.
     - `python -m py_compile backend/studio/report_quality.py backend/tests/test_report_quality.py` -> passed.
     - `pytest backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py -q` -> 160 passed.
     - Re-evaluated `s_831049d2caee/artifact.md` with the same internal structure/focus text plus required sections and verified URLs: `publish_ready True`.
125. Made final publish gating lint-aware for the remaining uncited-section weakness:
   - Root cause from `s_831049d2caee`: `lint_artifact()` correctly reported `[Evidence and Analysis] Long evidence-bearing section has no citation URL.`, but the final publish revision path only passed `evaluate_publish_readiness().issues` into the revision prompt and final `publish-ready` gate. The lint weakness was recorded for future weakness handling but was not eligible for same-run publish revision.
   - Added `report_quality.combined_publish_issues()` to dedupe publish-gate issues with deterministic artifact lints. `runner.py` now uses it for the original final artifact, the revision candidate, and final residual publish-ready issues; a revision is accepted only when both publish readiness and artifact lints are clean.
   - Added a focused regression proving a report can pass topic/evidence publish readiness while still producing the citation-free-section lint through the combined issue helper.
   - Validation:
     - `python -m py_compile backend/studio/runner.py backend/studio/report_quality.py backend/tests/test_report_quality.py backend/tests/test_runner.py` -> passed.
     - `pytest backend/tests/test_report_quality.py -q` -> 13 passed.
     - `pytest backend/tests/test_report_quality.py backend/tests/test_runner.py -q` -> 89 passed.
     - `pytest backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py -q` -> 161 passed.
   - Validation drift: comparable backend regression checkpoint expanded from 160 passed to 161 passed because the uncited-section combined-issue regression was added; no new backend failure mode appeared. No new live E2E was run for this small final-gate plumbing slice, so `s_831049d2caee` remains the latest full-run comparison baseline.
126. Tightened practical-usefulness scoring for implementation-risk specificity:
   - Root cause from the remaining `s_831049d2caee` quality gap: the `Practical usefulness` scorecard row used the generic `analysis` signal, so analysis-heavy prose could satisfy the category without concrete implementation risks, mitigations, owners, metrics, rollout/rollback, or next actions.
   - Added a category-local practical marker score for `Practical usefulness` while leaving the original optimizer/base `rubric_score()` unchanged. The category weakness hint now tells agents to add concrete implementation risks, mitigations, sequencing, and next actions instead of the generic "improve this rule" message.
   - Added a focused rubric regression proving analysis markers alone do not satisfy Practical usefulness, while a concrete implementation-risk/action paragraph does.
   - Validation:
     - `python -m py_compile backend/studio/rubric.py backend/tests/test_rubric.py` -> passed.
     - `pytest backend/tests/test_rubric.py -q` -> 14 passed.
     - `pytest backend/tests/test_rubric.py backend/tests/test_rubric_profile_api.py backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_runner.py::test_scoring_matrix_helpers_keep_reducers_full backend/tests/test_section_ownership.py::test_section_reducer_receives_full_scoring_rules -q` -> 20 passed.
     - `pytest backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 175 passed.
   - Validation drift: the comparable backend checkpoint expanded from 161 to 175 because `test_rubric.py` is now included and gained the practical-usefulness regression; no new backend failure mode appeared. No new live E2E was run for this deterministic scorecard slice.
127. Ran and analyzed a live post-scorecard E2E, then fixed a stale publish-gate term source:
   - Comparable E2E `s_6c2f8f47151e` with Gemma, `embed:{}`, tools enabled, `auto_improve=false`, `max_epochs=1`, `max_agents=3`: completed in ~753s; artifact 7,945 bytes; 8 H2 sections; 13 unique URLs; 0 placeholder hits; adjusted score 0.4374; scorecard 78.84.
   - Validation drift against `s_831049d2caee`: regressed on artifact length (9,346 -> 7,945 bytes), citation count (21 -> 13 URLs), adjusted score (0.6721 -> 0.4374), and scorecard (91.74 -> 78.84). It still kept the core section count and avoided placeholders.
   - Root cause for part of the score regression: publish readiness still treated the injected `Unified scoring requirements for this task:` block as user topic text, producing false missing terms such as `scoring`, `original`, `deterministic`, `rubric`, and `matrix`.
   - Fixed `report_quality._important_terms()` with `_SCORING_INSTRUCTION_RE` so the scoring block is stripped before topic-term extraction, matching the existing stripping for internal structure and section-focus assignment text.
   - Extended `test_publish_gate_ignores_internal_structure_instruction_terms()` to include the scoring block.
   - Re-evaluated `backend/tmp/studio-workspaces/s_6c2f8f47151e/artifact.md` with the patched gate: `publish_ready True`, `issues ()`, `combined ()`, with 4 verified URLs supplied out of 13 total artifact URLs.
   - Validation:
     - `python -m py_compile backend/studio/report_quality.py backend/tests/test_report_quality.py` -> passed.
     - `pytest backend/tests/test_report_quality.py -q` -> 13 passed.
     - `pytest backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 175 passed.
   - Validation drift: backend regression coverage remained at 175 passed; the live artifact itself still regressed versus `s_831049d2caee`, so a fresh E2E after this gate fix is still needed for clean end-to-end proof.
128. Ran a clean E2E after the scoring-instruction publish-gate fix and patched final verified-URL recomputation:
   - Restarted the backend with `./restart.sh backend`; the detached uvicorn process exited after health without traceback, so restarted uvicorn in a foreground tool session for observable logs.
   - Corrected the E2E driver to call `/run/{session_id}?requirement=...`; a body-less `/run/{session_id}` GET returns 422 by design.
   - Comparable E2E `s_6eb0a036aabc` with Gemma, `embed:{}`, tools enabled, `auto_improve=false`, `max_epochs=1`, `max_agents=3`: completed in 706.6s; artifact 6,881 bytes; 8 H2 sections; 6 unique URLs; 0 placeholder hits; publish-ready passed; adjusted score 0.3862; scorecard 69.04.
   - Validation drift:
     - Regressed versus `s_831049d2caee`: 9,346 -> 6,881 bytes, 21 -> 6 URLs, 0.6721 -> 0.3862 adjusted score, 91.74 -> 69.04 scorecard.
     - Regressed versus `s_6c2f8f47151e`: 7,945 -> 6,881 bytes, 13 -> 6 URLs, 0.4374 -> 0.3862 adjusted score, 78.84 -> 69.04 scorecard.
     - Improved only on false publish-term behavior: publish-ready no longer fails on injected scoring terms.
   - Root cause found from `s_6eb0a036aabc`: final publish readiness can pass while the recorded scorecard reports Citation integrity as 0.0/14.7 because final synthesis/readability or epoch-gate writeback can change the cited URL set after `_verified_urls` is first computed. Scorecard weaknesses then measure stale verification state rather than the exact served artifact.
   - Added `runner._verified_urls_from_cache()` and replaced duplicated inline cache reads. The runner now recomputes verified URLs after epoch-gate revert, post-gate finalization, publish-revision writeback, and final section writeback before publish/scoring/scorecard weakness generation.
   - Added `test_runner_verified_urls_from_cache_rechecks_final_text`.
   - Validation:
     - `python -m py_compile backend/studio/runner.py backend/tests/test_runner.py backend/studio/report_quality.py backend/tests/test_report_quality.py` -> passed.
     - `pytest backend/tests/test_runner.py::test_runner_verified_urls_from_cache_rechecks_final_text backend/tests/test_report_quality.py -q` -> 14 passed.
     - `pytest backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 176 passed.
   - Remaining issue: the rerun still produced weak evidence volume and broad standards-style citations (`w3.org`, `iso.org`, `nist.gov`, etc.) rather than enough fetched, task-specific sources. Next slice should improve worker evidence yield and/or final synthesis acceptance so the live artifact returns to at least the `s_831049d2caee` quality band.
129. Reran after verified-URL recomputation and pruned stale final weaknesses:
   - Comparable E2E `s_9d7a9d9f16fc` with Gemma, `embed:{}`, tools enabled, `auto_improve=false`, `max_epochs=1`, `max_agents=3`: completed in 756.0s; artifact 7,961 bytes; 8 H2 sections; 13 unique URLs; 0 placeholder hits; publish-ready passed; adjusted score 0.4686; scorecard 78.84.
   - Validation drift:
     - Improved over `s_6eb0a036aabc`: 6,881 -> 7,961 bytes, 6 -> 13 URLs, 0.3862 -> 0.4686 adjusted score, 69.04 -> 78.84 scorecard.
     - Matched `s_6c2f8f47151e` on scorecard/shape and improved adjusted score: 0.4374 -> 0.4686.
     - Still trails `s_831049d2caee`: 9,346 bytes / 21 URLs / 0.6721 score / 91.74 scorecard remains the best corrected baseline.
   - Root cause found after inspecting `s_9d7a9d9f16fc`: current artifact has no deterministic lint or publish issues, but recorded weaknesses still included stale pre-finalization lints such as `[document] Placeholder text remains...`, `[Evidence and Analysis] Long evidence-bearing section has no citation URL.`, and an LLM-mined claim that the final output had no citations or populated References.
   - Added `runner._prune_resolved_weaknesses()` and call it after final publish/revision, before adjusted scoring and scorecard weaknesses. It removes resolved deterministic lint strings against the exact final artifact, then reuses `refute_false_weaknesses()`.
   - Extended `refute_false_weaknesses()` so absence/failure-to-integrate claims about citations/URLs/sources/references are refuted when the final artifact demonstrably contains URLs and a References section.
   - Added `test_prune_resolved_weaknesses_drops_stale_final_lints`.
   - Validation:
     - `python -m py_compile backend/studio/task_runs.py backend/studio/runner.py backend/tests/test_runner.py` -> passed.
     - `pytest backend/tests/test_runner.py::test_prune_resolved_weaknesses_drops_stale_final_lints backend/tests/test_runner.py::test_runner_verified_urls_from_cache_rechecks_final_text -q` -> 2 passed.
     - `pytest backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 177 passed.
   - Remaining issue: evidence quality is now the primary gap. Runtime verified URLs improved from zero but are still too few; final synthesis still includes broad or synthesized references that were not backed by fetched cache entries, so Citation integrity remains below the `s_831049d2caee` band.
130. Fixed scoring-block prompt truncation that suppressed worker search/fetch:
   - Completed interrupted E2E analysis for `s_c4c84e3bf3ff`: artifact 6,462 bytes, 8 H2 sections, 17 unique URLs, 0 placeholders, adjusted score 0.5309, scorecard 77.79, 7 weaknesses. This improved over `s_9d7a9d9f16fc` on adjusted score (0.4686 -> 0.5309) and URL count (13 -> 17), but still regressed versus corrected baseline `s_831049d2caee` (9,346 bytes / 21 URLs / 0.6721 adjusted / 91.74 scorecard).
   - Root cause from `s_c4c84e3bf3ff/io/s2.spoke0.in.md`: section worker prompts kept section assignments and cropped scoring rows, but lost the `_build_executor_prompt()` execution steps, including `Run web_search THEN web_fetch`. `_strip_task_scoring_block()` saw `Unified scoring requirements for this task:` inside `plan_obj.task` and truncated everything after it, deleting the search/fetch mandate before worker focus text was appended. Workers then emitted weak or fabricated scoped patches such as `example.com` instead of grounded `RESEARCH_FINDING` blocks.
   - Patched `runner.py` so the executor goal is stripped of the global scoring block before `_build_executor_prompt()` is built. Worker prompts still crop unrelated scoring rows, but the executor contract now survives.
   - Extended `test_loop_workers_receive_section_assignments_and_weakness_guidance` to assert worker prompts include `Run web_search THEN web_fetch` and `OUTPUT FORMAT (critical)` while omitting the global scoring block.
   - Validation:
     - `python -m py_compile backend/studio/artifact_text.py backend/studio/runner.py backend/studio/task_runs.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py` -> passed.
     - `pytest backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance backend/tests/test_runner.py::test_prune_resolved_weaknesses_drops_stale_final_lints backend/tests/test_runner.py::test_runner_verified_urls_from_cache_rechecks_final_text backend/tests/test_plan_backlog.py::test_synthesis_rejects_when_a_url_is_added -q` -> 4 passed.
     - `pytest backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 178 passed.
     - Genericity audit for touched production files found no hardcoded catalog-management/example strings.
   - E2E note: restarted backend and attempted a fresh `auto_improve=false`, `max_epochs=1` run `s_c163173909d2` after the patch. It stalled before writing phase I/O beyond the initial skeleton and was cancelled; it produced no comparable artifact, so the reliable validation for this slice is the prompt-regression test plus prior completed-run drift above.
131. Added the first typed evidence-matrix surface without a new agent loop:
   - Implemented `backend/studio/evidence.py` with `EvidenceItem`, `evidence_from_findings()`, and `render_evidence_matrix()`. The converter filters to findings whose URLs survive into the final served artifact, so dropped worker findings do not become claimed evidence.
   - Added `EvidenceEvent` to the backend SSE contract and emit it from `runner.py` after final cleanup/publish weakness pruning. The event carries evidence rows plus a markdown matrix.
   - Mirrored the `evidence` event in `frontend/src/api/types.ts` and `frontend/src/store/runStore.ts`. No dedicated panel or DB migration yet; this is the minimal Workstream A surface for later UI/export persistence.
   - Added `backend/tests/test_evidence.py`.
   - Validation:
     - `python -m py_compile backend/studio/evidence.py backend/studio/events.py backend/studio/runner.py backend/tests/test_evidence.py backend/tests/test_runner.py` -> passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance -q` -> 2 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 179 passed.
     - `npm run build` in `frontend/` -> passed.
     - Genericity audit for touched production files found no hardcoded catalog-management/example strings.
   - Validation drift: comparable backend regression checkpoint expanded from 178 to 179 because the evidence converter test was added; no new backend failure mode appeared. No live E2E was run for this event/store slice.
132. Made the evidence matrix visible/exportable:
   - Added `evidence_matrix` to `RunSnapshot` and included it as `evidenceMatrix` in `/export/{session_id}` loop drafts when present.
   - Updated `runner.py` to overwrite the earlier run snapshot after post-run finalization, so export uses the final result plus the evidence matrix emitted after cleanup.
   - Rendered the evidence matrix in `ResultWindow` below the report/scorecard using the existing markdown/table rendering path; no new panel or store abstraction.
   - Extended `test_export_on_finished_run_returns_a_loop` to prove evidence matrix export.
   - Validation:
     - `python -m py_compile backend/studio/evidence.py backend/studio/events.py backend/studio/export.py backend/studio/runner.py backend/studio/session.py backend/tests/test_evidence.py backend/tests/test_export.py` -> passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_runner.py::test_loop_workers_receive_section_assignments_and_weakness_guidance -q` -> 7 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 184 passed.
     - `npm run build` in `frontend/` -> passed.
     - Genericity audit for touched production files found no hardcoded catalog-management/example strings.
   - Validation drift: comparable backend checkpoint expanded from 179 to 184 because `test_export.py` was added to this run and now covers evidence export; no new backend failure mode appeared. Port 8770 was clear.
133. Persisted accepted evidence rows in task-run history:
   - Added `TaskRun.evidence` and an additive `task_runs.evidence_json` SQLite migration in `TaskRunStore`.
   - `record()` now stores evidence rows, and `latest`, `best`, `all_runs`, `latest_with_content`, and `similar_runs` preserve the row shape without shifting the embedding vector into `_row_to_run()`.
   - Wired `runner.py` so the same evidence rows emitted in the `evidence` SSE event are passed into the `TaskRun` record.
   - Added `test_task_run_store_persists_evidence_rows` covering `latest`, `best`, `all_runs`, and `latest_with_content`.
   - Validation:
     - `python -m py_compile backend/studio/task_runs.py backend/studio/runner.py backend/tests/test_runner.py backend/tests/test_similar_runs.py` -> passed.
     - `pytest backend/tests/test_runner.py::test_task_run_store_persists_evidence_rows backend/tests/test_runner.py::test_latest_with_content_falls_back_to_result_text backend/tests/test_similar_runs.py -q` -> 15 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_similar_runs.py -q` -> 198 passed.
     - Comparable backend checkpoint from the previous slice rerun exactly without `test_similar_runs.py`: 184 -> 185 passed, explained by the new persistence test; no unexpected drift.
134. Added the minimal research-package export spine:
   - Reused `backend/studio/export.py` rather than adding a second exporter module.
   - Added `run_to_research_package(snapshot)` returning a JSON manifest and file-content map for `research_report.md`, `evidence_matrix.md`, `scorecard.json`, `human_review_checklist.md`, `run_manifest.json`, and `loop.json`.
   - Added `GET /export/{session_id}/research-package` with the same 404/409 semantics as the existing loop export endpoint.
   - Extended `RunSnapshot` with `scorecard_100` and wired the runner's final scorecard into the recorded snapshot so package export includes the actual run scorecard.
   - Manifest now includes `packageVersion`, run metadata, `hasEvidenceMatrix`, `evidenceCount`, and `hasScorecard`.
   - Validation:
     - `python -m py_compile backend/studio/export.py backend/studio/app.py backend/studio/session.py backend/studio/runner.py backend/tests/test_export.py` -> passed.
     - `pytest backend/tests/test_export.py -q` -> 8 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 188 passed.
   - Validation drift: comparable backend checkpoint expanded from 185 to 188 because `test_export.py` gained three research-package export tests; no unexpected failure mode appeared.
135. Added export-only human review-required state:
   - Added `build_review_status()` in `report_quality.py`. It marks `REVIEW_REQUIRED` for high-impact topics, publish/readiness issues, missing accepted evidence on report tasks, non-passing Loop Doctor checks, low report scorecard totals, or weak source/citation/evidence scorecard rows.
   - Extended `DoneEvent` and `RunSnapshot` with a `review` payload containing `required`, `status`, `publish_decision`, `reviewed`, and `reasons`. Review-required runs report `publish_decision: REVIEW_REQUIRED`, not `PUBLISH_READY`.
   - Wired the runner to compute review status after post-run scoring/evidence finalization and before the final snapshot/done event.
   - Research-package manifest and `run_manifest.json` now include the review payload; `human_review_checklist.md` adds a required-review item when applicable.
   - ResultWindow renders a visible `REVIEW REQUIRED — not reviewed` block with reasons. Manual approval remains deferred.
   - Validation:
     - `python -m py_compile backend/studio/report_quality.py backend/studio/events.py backend/studio/export.py backend/studio/session.py backend/studio/runner.py backend/tests/test_report_quality.py backend/tests/test_export.py backend/tests/test_runner.py` -> passed.
     - Focused pytest for review/export/done coverage -> 5 passed.
     - `npm run build` in `frontend/` -> passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 191 passed.
   - Validation drift: comparable backend checkpoint expanded from 188 to 191 because review/export tests were added; no unexpected backend failure mode appeared.
136. Added static table/diagram/code artifact lints:
   - Extended `artifact_lint.py` with conservative static checks for malformed markdown table separators, empty comparison tables, mermaid diagrams without nearby explanatory prose, and syntax errors in fenced `python`/`py` blocks.
   - Pseudocode and other non-Python fences are ignored by the runnable-code check; no execution sandbox was added.
   - Added the new lint markers to the runner's resolved-weakness pruning list so fixed table/diagram/code issues do not carry forward.
   - Added/updated `test_artifact_lint.py` coverage for valid explained mermaid, unexplained mermaid, malformed/empty tables, Python syntax, and pseudocode.
   - Validation:
     - `python -m py_compile backend/studio/artifact_lint.py backend/studio/runner.py backend/tests/test_artifact_lint.py` -> passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_report_quality.py::test_combined_publish_issues_include_artifact_lints backend/tests/test_runner.py::test_prune_resolved_weaknesses_drops_stale_final_lints -q` -> 12 passed.
     - Exact comparable backend checkpoint remained `191 passed`.
     - Comparable backend checkpoint plus `test_artifact_lint.py` -> 201 passed.
   - Validation drift: exact prior comparable set did not drift; expanded lint-inclusive set adds the artifact lint coverage.
137. Added source-type inference and major-claim corroboration:
   - Extended `evidence.py` source typing for official docs, standards, academic/preprint sources, repositories, blogs, forum/social sources, and generic web sources.
   - Added evidence-layer corroboration: major-section claims from single non-primary sources are downgraded to `weak`; primary/high-reliability sources can stand alone; two independent hosts for the same normalized claim are marked as corroborated.
   - Wired weak evidence count through `runner.py` into `build_review_status()`, so report-like runs with weak major evidence require human review with reason `weak or uncorroborated evidence`.
   - Updated `PLAN-research-report-skill-package-improvements.md` Workstream H with the shipped source-quality behavior and its scope boundary.
   - Validation:
     - `python -m py_compile backend/studio/evidence.py backend/studio/report_quality.py backend/studio/runner.py backend/tests/test_evidence.py backend/tests/test_report_quality.py` -> passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_report_quality.py::test_review_status_requires_review_for_high_impact_or_weak_evidence -q` -> 5 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 194 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 204 passed.
   - Validation drift: exact comparable backend checkpoint expanded from 191 to 194 because `test_evidence.py` gained three source-quality tests; lint-inclusive comparable set expanded from 201 to 204 for the same reason. No unexpected failure mode appeared.
138. Expanded the research-package bundle core files:
   - Extended `run_to_research_package()` to export `research_report.html`, `evidence_matrix.json`, `agent_trace.jsonl`, `checkpoints.jsonl`, `source_notes.json`, and `requirements.txt` alongside the existing markdown report, evidence matrix, scorecard, review checklist, manifest, and loop JSON.
   - Added a dependency-free Markdown-to-HTML renderer covering headings, paragraphs, lists, fenced code, and markdown tables.
   - Derived structured evidence JSON and source notes from the current evidence-matrix snapshot; this keeps the slice export-only until `RunSnapshot` carries first-class evidence rows.
   - Added `rendererStatus` and `exportedFiles` to `run_manifest.json`; PDF and diagram rendering are explicitly `unavailable`, not implicit failures.
   - Updated Workstream L in `PLAN-research-report-skill-package-improvements.md` with shipped files and deferred PDF/PNG/demo/trace-state work.
   - Validation:
     - `python -m py_compile backend/studio/export.py backend/tests/test_export.py` -> passed.
     - `pytest backend/tests/test_export.py -q` -> 10 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 195 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 205 passed.
   - Validation drift: comparable backend checkpoint expanded from 194 to 195 because `test_export.py` gained one HTML/table bundle test; lint-inclusive set expanded from 204 to 205. No unexpected failure mode appeared.
139. Added minimal stop-report and run-metrics path:
   - Added `backend/studio/run_metrics.py` with deterministic `build_stop_report()` and `build_run_metrics()` helpers, including stable `None` values for zero-denominator tool/citation accuracy.
   - Added `MetricsEvent`, `DoneEvent.metrics`, `RunSnapshot.metrics`, frontend `metrics` event typing, and run-store state.
   - Wired `runner.py` to distinguish `validation_passed`, `budget_exceeded`, and `cancel_requested`, count tool-result failures at the shared tool-result emit path, emit `metrics` before `done`, and record the same payload into the run snapshot.
   - Research-package export now includes `metrics.json`; `run_manifest.json` includes `metrics` and `stopReport`.
   - Kept full `ToolObservation`/checkpoint JSONL instrumentation deferred; this slice uses existing signals and does not invent fake trace rows.
   - Validation:
     - `python -m py_compile backend/studio/run_metrics.py backend/studio/events.py backend/studio/session.py backend/studio/export.py backend/studio/runner.py backend/tests/test_run_metrics.py backend/tests/test_export.py backend/tests/test_runner.py` -> passed.
     - `pytest backend/tests/test_run_metrics.py backend/tests/test_export.py backend/tests/test_runner.py::test_metrics_event_emits_before_done -q` -> 13 passed.
     - `npm run build` in `frontend/` -> passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 198 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 208 passed.
   - Validation drift: comparable backend checkpoint expanded from 195 to 198 because `test_run_metrics.py` added two tests and `test_runner.py` added one metrics ordering test; lint-inclusive set expanded from 205 to 208. No unexpected failure mode appeared.
140. Replaced placeholder trace/checkpoint bundle files with real JSONL state:
   - Extended `RunSnapshot` with `agent_trace_jsonl` and `checkpoints_jsonl`.
   - `runner.py` now appends compact tool observations from the shared tool-result emit path, appends phase checkpoints from real `StepRun` completions, and appends a final checkpoint with evidence count, score, failed validation count, observation ids, and stop reason.
   - `run_to_research_package()` now exports `agent_trace.jsonl` and `checkpoints.jsonl` from the snapshot instead of empty placeholder strings.
   - Extended the existing tool-loop runner test to prove a real `web_search` tool result becomes trace JSONL and is referenced by the final checkpoint; extended export tests to prove supplied trace/checkpoint JSONL is packaged.
   - Validation:
     - `python -m py_compile backend/studio/session.py backend/studio/export.py backend/studio/runner.py backend/tests/test_export.py backend/tests/test_runner.py` -> passed.
     - `pytest backend/tests/test_export.py backend/tests/test_runner.py::test_tool_loop_emits_tool_events -q` -> 11 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 198 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 208 passed.
   - Validation drift: no count drift; existing tests were strengthened. Remaining observation work is richer pre-validation/argument-redaction detail and optional SQLite indexing if cross-run UI queries need it.
141. Added offline practice-code files to the research package:
   - `run_to_research_package()` now includes `research_agent_demo.py`, `research_agent_demo.pseudo`, and `research_agent_demo_output.txt`.
   - The demo script is deterministic stdlib Python and mirrors the Studio loop at toy scale: plan, observe, validate, checkpoint/write files, and return a package summary.
   - Export tests now assert the files are included and execute the packaged demo offline in a temp directory.
   - Validation:
     - `python -m py_compile backend/studio/export.py backend/tests/test_export.py` -> passed.
     - `pytest backend/tests/test_export.py -q` -> 11 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 199 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 209 passed.
   - Validation drift: comparable backend checkpoint expanded from 198 to 199 because `test_export.py` gained one offline demo execution test; lint-inclusive set expanded from 208 to 209. No unexpected failure mode appeared.
142. Added redacted tool arguments to trace observations:
   - Runner now pairs each `tool_call` with its `tool_result` and writes `args_redacted` plus `ts` into `agent_trace_jsonl`.
   - Secret-like keys (`api_key`, `token`, `password`, `secret`) are replaced with `[redacted]`; long strings are clipped at the trace boundary.
   - Extended the real tool-loop runner test to prove `web_search` query arguments appear in trace JSONL; added a small redaction unit test.
   - Validation:
     - `python -m py_compile backend/studio/runner.py backend/tests/test_runner.py` -> passed.
     - `pytest backend/tests/test_runner.py::test_tool_loop_emits_tool_events backend/tests/test_runner.py::test_tool_arg_redaction_clips_secrets -q` -> 2 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 200 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py -q` -> 210 passed.
   - Validation drift: comparable backend checkpoint expanded from 199 to 200 because `test_runner.py` gained one redaction test; lint-inclusive set expanded from 209 to 210. No unexpected failure mode appeared.
143. Added DB template audit/quarantine support:
   - `TemplateStore` now migrates metadata columns onto `report_templates`: `report_type`, `source`, `status`, and `failure_reason`.
   - Added `TemplateStore.audit_templates(report_type)` to reuse deterministic artifact lints and disable unsafe skeletons while preserving rows for provenance.
   - General/non-code profiles disable code or implementation sections as off-profile content.
   - `find_template()` now searches only `status='active'` templates, preventing disabled stale skeletons from automatic reuse.
   - Added `test_templates.py` coverage for placeholder quarantine, off-profile code quarantine, and active-only template lookup.
   - Validation:
     - `python -m py_compile backend/studio/templates.py backend/tests/test_templates.py` -> passed.
     - `pytest backend/tests/test_templates.py -q` -> 3 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 203 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 213 passed.
   - Validation drift: comparable backend checkpoint expanded from 200 to 203 because `test_templates.py` added three tests; lint-inclusive set expanded from 210 to 213. No unexpected failure mode appeared.
144. Added public template audit endpoint:
   - Added `POST /catalog/templates/audit` to run `TemplateStore.audit_templates(report_type)` and return audited rows plus a disabled count.
   - Endpoint keeps the previous quarantine behavior: unsafe rows are disabled, not deleted.
   - Added an endpoint test that monkeypatches `TemplateStore` to a temp DB and verifies an unsafe skeleton is disabled through the HTTP route.
   - Validation:
     - `python -m py_compile backend/studio/app.py backend/studio/templates.py backend/tests/test_templates.py` -> passed.
     - `pytest backend/tests/test_templates.py -q` -> 4 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 204 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 214 passed.
   - Validation drift: comparable backend checkpoint expanded from 203 to 204 because `test_templates.py` gained one endpoint test; lint-inclusive set expanded from 213 to 214. No unexpected failure mode appeared.
145. Added read-only template inventory endpoint:
   - Added `TemplateStore.list_templates(status=None)` returning id, name, requirement, report type, source, status, failure reason, created time, heading count, and a bounded preview without returning full skeleton bodies.
   - Added `GET /catalog/templates?status=...` for read-only template catalog inventory.
   - Added store and endpoint tests with temp DB monkeypatching.
   - Validation:
     - `python -m py_compile backend/studio/app.py backend/studio/templates.py backend/tests/test_templates.py` -> passed.
     - `pytest backend/tests/test_templates.py -q` -> 6 passed.
     - `pytest backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 206 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 216 passed.
   - Validation drift: comparable backend checkpoint expanded from 204 to 206 because `test_templates.py` gained two inventory tests; lint-inclusive set expanded from 214 to 216. No unexpected failure mode appeared.
146. Added in-place template replacement endpoint:
   - Added `TemplateStore.replace_template(template_id, skeleton, report_type, source)` to replace one stored skeleton while preserving the existing row id, name, requirement, embedding/provenance fields, and created time.
   - Replacement immediately runs the same deterministic template safety checks as audit/quarantine; valid replacements become `active`, invalid replacements remain stored but become `disabled` with `failure_reason`.
   - Added `POST /catalog/templates/{template_id}/replace`, returning 404 for missing rows and 422 for an empty skeleton.
   - Added store and endpoint tests for preserved row identity, invalid replacement quarantine, successful HTTP replacement, missing-row rejection, and empty-skeleton rejection.
   - Validation:
     - `python -m py_compile backend/studio/templates.py backend/studio/app.py backend/tests/test_templates.py` -> passed.
     - `pytest backend/tests/test_templates.py -q` -> 11 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 221 passed.
   - Validation drift: lint-inclusive comparable set expanded from 216 to 221 because `test_templates.py` gained five replacement tests. No unexpected failure mode appeared.
147. Added template catalog import/export endpoints:
   - Added `TemplateStore.export_templates(status=None)` for explicit catalog export with full skeleton bodies; regular `list_templates()` remains preview-only for inventory.
   - Added `TemplateStore.import_templates(templates)` to import skeletons with name, requirement, report type, and source metadata. Imported skeletons are audited before activation; unsafe rows are stored as `disabled`, and exact duplicate skeletons are skipped.
   - Added `GET /catalog/templates/export` and `POST /catalog/templates/import`.
   - Added store and endpoint tests for full-body export, audited import, duplicate skipping, and HTTP import/export paths.
   - Validation:
     - `python -m py_compile backend/studio/templates.py backend/studio/app.py backend/tests/test_templates.py` -> passed.
     - `pytest backend/tests/test_templates.py -q` -> 15 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 225 passed.
   - Validation drift: lint-inclusive comparable set expanded from 221 to 225 because `test_templates.py` gained four import/export tests. No unexpected failure mode appeared.
148. Added template approval metadata/action:
   - `report_templates` now migrates `quality_score`, `created_from_session`, `approved_by`, and `last_used_at`.
   - `TemplateStore.approve_template()` approves only clean audited rows. Unsafe rows remain `disabled` with `failure_reason`; approval does not bypass structural lint.
   - `find_template()` stamps `last_used_at` when an active template is actually selected for reuse.
   - Added `POST /catalog/templates/{template_id}/approve`.
   - Added tests for usage stamping, clean approval metadata, unsafe approval quarantine, and HTTP approval.
   - Validation:
     - `python -m py_compile backend/studio/templates.py backend/studio/app.py backend/tests/test_templates.py` -> passed.
     - `pytest backend/tests/test_templates.py -q` -> 18 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 228 passed.
   - Validation drift: lint-inclusive comparable set expanded from 225 to 228 because `test_templates.py` gained three approval/usage tests. No unexpected failure mode appeared.
149. Added pre-validation checkpoint detail:
   - Runner now appends a `pre_validation` checkpoint before final stop accounting.
   - The checkpoint records artifact path, evidence count, weak evidence count, score, publish issue count, Loop Doctor failure count, review-required state, and observation ids.
   - Strengthened the existing tool-loop trace test to prove `pre_validation` appears before `final` and carries validation counters.
   - Validation:
     - `python -m py_compile backend/studio/runner.py backend/tests/test_runner.py` -> passed.
     - `pytest backend/tests/test_runner.py::test_tool_loop_emits_tool_events -q` -> 1 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 228 passed.
   - Validation drift: no pass-count drift; an existing trace/checkpoint test was strengthened. No unexpected failure mode appeared.
150. Deduped repeated research findings before patching:
   - Compared the user-provided prior workspaces. `s_2dafb96d6fb6` preserved many more sourced findings than `s_3bdf12f07b9e`, but it also shows repeated claim/quote prose; the current patch path should keep yield without duplicating identical worker findings.
   - `_parse_findings()` now dedupes parsed `RESEARCH_FINDING` blocks by normalized URL, patch target, and claim/quote before `_findings_to_patches()` creates additive patches.
   - Added a helper test proving repeated findings create one patch while distinct findings still create multiple patches.
   - Validation:
     - `python -m py_compile backend/studio/findings.py backend/tests/test_m8_m9_helpers.py` -> passed.
     - `pytest backend/tests/test_m8_m9_helpers.py::test_findings_to_patches_dedupes_repeated_blocks backend/tests/test_m8_m9_helpers.py::test_findings_to_patches_multiple_blocks -q` -> 2 passed.
     - Expanded helper-inclusive comparable set (`... test_m8_m9_helpers.py ...`) -> 292 passed.
     - Prior lint-inclusive comparable set without helper file -> 228 passed.
   - Validation drift: no drift in the prior 228-test baseline; expanded comparable coverage includes the new helper test and passed at 292.
151. Passed final-synthesis fetched evidence by workspace file path:
   - `_final_evidence_dossier()` now writes cited fetched sources into session-local `evidence/source-NNN.md` files plus `evidence/fetched-sources.json` when a workspace is available.
   - Final synthesis now receives these jailed `read_file` paths instead of inlined raw cache content, while keeping excerpt fallback for non-workspace helper use.
   - Seeded-artifact final steps now also receive `FULL SCORING STANDARD`, `UNRESOLVED WEAKNESSES`, and fetched evidence file paths while preserving the no-whole-document-echo guard.
   - The final report prompt explicitly tells synthesis to use `read_file` on listed evidence paths when upstream outputs are too thin for analysis.
   - Updated `PLAN-research-report-skill-package-improvements.md` to require per-run evidence manifest/source paths, not wholesale `.web_cache.json` exposure.
   - Validation:
     - `python -m py_compile backend/studio/runner.py backend/tests/test_runner.py` -> passed.
     - `pytest backend/tests/test_runner.py::test_final_report_step_requires_synthesis_and_reflection backend/tests/test_runner.py::test_final_evidence_dossier_writes_workspace_paths backend/tests/test_runner.py::test_final_non_report_step_keeps_generic_artifact_contract -q` -> 3 passed.
     - `pytest backend/tests/test_runner.py::test_seeded_final_step_gets_scoring_weaknesses_and_evidence_paths -q` -> 1 passed.
     - `pytest backend/tests/test_runner.py -q` -> 85 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 237 passed.
     - `pytest backend/tests -q` -> 496 passed.
   - Validation drift: `test_runner.py` expanded by two evidence-path regression tests and passed at 85. The current lint-inclusive comparable set passes at 237 and the full backend suite passes at 496 in this dirty tree; no failure-mode drift observed.
152. Fixed empty final-synthesis scoring block fallback:
   - Verified the user-reported historical prompt `backend/tmp/studio-workspaces/s_c3a0b7e0805b/io/epic-2.in.md` still contains the bad block: `FULL SCORING STANDARD: - (none for this assigned section)`. This file is an old artifact and is not rewritten by code changes.
   - `_full_scoring_matrix()` now falls back to the selected/default profile-template matrix when `session.rubric_config.scoring_matrix` is missing, so final synthesis does not receive an empty full-standard block.
   - Cold final synthesis now uses the required `FETCHED EVIDENCE FILES` heading, matching the seeded final-step contract.
   - Added a cold-run regression proving the generated workspace prompt file under `io/*.in.md` receives `FULL SCORING STANDARD`, `UNRESOLVED WEAKNESSES`, `FETCHED EVIDENCE FILES`, `evidence/fetched-sources.json`, and `evidence/source-001.md`, with source content kept in files rather than prompt-inlined.
   - Added a default-matrix fallback test and made the seeded prompt test task hash unique so local persisted prior runs cannot mask the intended seed.
   - Validation:
     - `python -m py_compile backend/studio/runner.py backend/tests/test_runner.py` -> passed.
     - `pytest backend/tests/test_runner.py -q -k "scoring_matrix_falls_back or final_report_step_requires_synthesis or final_evidence_dossier or cold_final_step_gets_scoring_weaknesses_and_evidence_paths or seeded_final_step_gets_scoring_weaknesses_and_evidence_paths"` -> 5 passed.
     - `pytest backend/tests/test_runner.py -q` -> 87 passed.
     - `pytest backend/tests/test_artifact_lint.py backend/tests/test_evidence.py backend/tests/test_export.py backend/tests/test_report_quality.py backend/tests/test_run_metrics.py backend/tests/test_section_ownership.py backend/tests/test_plan_backlog.py backend/tests/test_runner.py backend/tests/test_rubric.py backend/tests/test_templates.py -q` -> 239 passed.
     - `pytest backend/tests -q` -> 498 passed.
   - Validation drift: `test_runner.py` expanded from 85 to 87 tests, the comparable backend slice from 237 to 239, and the full backend suite from 496 to 498 because cold-run final synthesis and empty-rubric fallback regressions were added. The old workspace still demonstrates the pre-fix failure and should not be used as a post-fix artifact; new regression coverage inspects the generated `io/*.in.md` prompt file directly.

153. Reducer now evaluates against FULL SCORING STANDARD + UNRESOLVED WEAKNESSES + FETCHED EVIDENCE FILES; reducer stage made inspectable; local research-report loop/skill added:
   - Fixed the reducer's empty-scoring bug: the section reducer passed `rubric_config.scoring_matrix` directly, so when scoring lives in the requirement (empty `rubric_config`) it saw `- (no scoring matrix provided)`. Now `runner.py` passes `format_scoring_rules(_full_scoring_matrix(session))` — the same profile/template fallback entry 152 added for final synthesis. Every reducer measures the whole artifact against the full frozen matrix.
   - `findings.py::_make_section_reducer` gains `evidence_fn`; the reducer prompt now injects a `FETCHED EVIDENCE FILES` block (reuses `_final_evidence_dossier` on the phase's worker drafts + current artifact) so reducers can verify claims against fetched sources, not worker prose alone. SECTION WEAKNESSES + FULL SCORING RULES were already injected.
   - The reducer stage was invisible in `io/` (it runs inside `run_plan`, not as a recorded spoke). The reducer prompt+output are now captured (`reduce._io_capture`) and written to `io/<step>.reducer.in.md`/`.out.md`, and a `reducer` role row is added to `agent_io.jsonl`.
   - Reducer-side finding consolidation (`agentkit/artifacts/dedup.py::consolidate_findings`): same-URL merge (keep richest), per-`patch_target` density cap, scaffolding-lead strip; against-doc dedup wired in `findings.py` reduce(). Live cold reduce logged `url_merged=7` (13 raw findings -> 6 distinct patches).
   - artifact/result split (`runner.py` post-gate finalize): `artifact.md` = readable+deduped seed (scored + carried forward), `result.md` = grounded full archive, so per-round cleanup compounds instead of being re-paid.
   - Per-section readability refine (`artifact_text.py::_refine_readability` + `_split_top_level`): windows by top-level `##` section so the model synthesizes the whole section instead of per-paragraph fragments that fail the citation guard.
   - Workstream I (loop first + skill): `backend/studio/local_catalog/loops/research-report-agent.json` merged into `CatalogClient` via `include_local` (local overrides remote by slug, survives remote-fetch failure). Domain skill `research-report-agent` via `skills_paths.py::build_domain_skills`, exposed through `/skills`. Live: `/loops` ranks it #1, `/skills` lists it, `/session/{id}/seed` emits `loop_seed` with 8 steps.
   - Validation:
     - Live proof (new-code run): `io/e1:s1.reducer.in.md` has FULL SCORING RULES + SECTION WEAKNESSES + FETCHED EVIDENCE FILES (18 fetched source files). `io/e1:s16.in.md` (final synthesis) has non-empty FULL SCORING STANDARD (9 rows via fallback) + UNRESOLVED WEAKNESSES (15 section-prefixed rows).
     - Full backend suite -> 504 passed (was 498), no regression. New tests: reducer injection + io_capture and `consolidate_findings` (`test_section_ownership.py`), local loop find/seed/override (`test_loops.py`), domain skill (`test_skills_paths.py`), `/skills` domain listing (`test_export.py`).
   - Validation drift: full suite 498 -> 504 (six new tests); one existing test updated for the intended `/skills` and remote-fetch-degrade behavior changes (local loops now survive a remote catalog failure).

154. Loop-seed carry-forward via semantic search (local AND remote loops):
   - Problem: a loop-seeded hill-climb run could cold-start (fresh ~8K artifact) instead of improving the closest prior report, because a seeded/reworded run can rotate the task_hash so the exact-key `latest_with_content` lookup misses.
   - Fix (`runner.py`, in the `auto_improve` carry-forward block, shared by local + remote loop seeds): when the exact-hash prior is missing and an embedder is present, fall back to SEMANTIC search — `TaskRunStore.similar_runs(requirement, embedder, min_similarity=0.6, exclude_hash=_thash)` — and seed from a similar prior's artifact. Genuinely novel tasks (nothing clears the threshold) still cold-start.
   - Selection rules the user specified: (a) if nothing found in DB above threshold -> cold start; (b) skip empty/placeholder priors (high embedding similarity but 0-score, <500 chars) since seeding garbage is worse than cold; (c) when multiple content-bearing priors qualify, choose the LATEST (`TaskRunStore.session_recency(session_id)` = max DB row id). Extracted `runner._pick_seed_with_content(sims, ws_root, min_chars, recency_fn)` (pure, tested).
   - Live proof: a local `research-report-agent` loop-seeded run (`s_25b939d9e354`) now seeds `artifact.md` at 21919 bytes = the prior `s_0511138358f3` report (score 0.92), not the ~8K cold artifact. Semantic ranking correctly skipped two 0.98-similarity empty placeholder runs and picked the latest content-bearing prior.
   - Validation: `test_runner.py` gains `test_pick_seed_with_content_skips_empty_and_prefers_latest` and `test_session_recency_returns_max_row_id`; full backend suite -> 506 passed (was 504), no regression.

155. Explicit seed-file parameter (`hill_climb_config.seed_path`) + GUI menu:
   - Problem the user hit: a run for the catalog-management requirement (`5cbd0aba6a3b` lineage) seeded from its own weak same-task prior (~8K, score ~0.23) via the exact-hash path, so the semantic fallback never fired and a much stronger cross-task prior (a 22K/0.92 artifact) was never reachable. Diagnosis: the exact-hash lineage, once it exists (even weak), outranks any semantic seed; and `_pick_seed_with_content` picks by *closest similarity*, not size/score, so even deleting the lineage would not deterministically route to a chosen 22K seed.
   - Fix (`runner.py` + `app.py`): new `hill_climb_config.seed_path`. When set, `runner._seed_prior_from_path(seed_path, thash, requirement)` reads that artifact file and builds a synthetic prior whose `session_id` (`__seedfile__<name>`) has no on-disk `artifact.md`, so seed application falls through to `result_text` (the file body). It runs BEFORE the exact-hash + semantic DB lookups, so it overrides both. Blank/missing/unreadable/empty -> None -> normal DB seeding (no behavior change when unset). `/session/{id}/hill-climb` accepts `seed_path`.
   - GUI: `LoopConfigPanel` (⚙ Loop -> Hill Climb tab, Deliverable section) gains a "Seed file (overrides DB seed)" input; posts `seed_path` in the hill-climb config body.
   - Live proof (gemma, local `research-report-agent` loop, max_epochs=2, DB restored so the weak 8K lineage is present): throughput log shows `seed via explicit seed_path (21899 chars)` — the override seeds the 22K artifact, beating the 7988-char DB prior `latest_with_content` would otherwise return.
   - Validation: `test_runner.py::test_seed_prior_from_path_reads_file_and_falls_back` (file read, synthetic-prior shape, blank/missing/empty fallbacks); `test_runner.py` 90 passed; frontend `tsc --noEmit` clean.

156. Seed re-seed-every-epoch guard, goal-aware editor phase + `search_evidence` tool, and this session's tokenized-match/URL-attach fix (retested against real gemma):
   - **Seed re-seed guard** (`runner.py` ~1657-1666, `auto_improve` carry-forward block): `_seed_prior_from_path` (entry 155's explicit `seed_path` override) is now gated to `self._epoch <= 1`. Previously a multi-epoch hill-climb re-applied the static seed file on every epoch, discarding each epoch's own progress — a document-shrink symptom distinct from the round-1 collapse `HANDOFF-seed-shrink-debug.md` diagnoses (that file's stub-reset hypothesis was not separately retested this session; its status is left unchanged below). Epoch 2+ now falls through to `latest_with_content` (the prior epoch's own persisted result). Test: `test_seed_path_only_seeds_first_epoch_not_every_epoch`.
   - **Editor phase** (`runner.py::_run_editor_pass` + `_editor_*` helpers ~732-863, call site ~3086-3105): an optional goal-aware final pass, gated on `tools_enabled` + a configured scoring matrix + a non-empty artifact. Up to `_EDITOR_MAX_ROUNDS` (2) rounds; each round re-scores via `_editor_scored_issues` (rubric + lint) and does a FULL revert (`artifact.md` + `sections/*.md` + `active_outline.json`) on any regression (`new_score <= cur_score or len(new_issues) >= len(cur_issues)`), feeding the failed round's issues back into round 2 as feedback so it doesn't blindly repeat. Only `read_file`, `search_evidence`, `read_artifact`, `patch_artifact` are offered (no web/write) — the editor grounds and patches, it does not re-research. Weaknesses returned to the caller are always the fresh post-editor list, never a stale pre-editor one.
   - **`search_evidence` tool** (`studio/tools.py`): greps the session's fetched `evidence/*.md` (never the `.json` manifest, never a whole file — capped at `_MAX_EVIDENCE_MATCHES`=10 matches / `_EVIDENCE_CONTEXT_LINES`=3 lines / `_MAX_EVIDENCE_CONTEXT_CHARS`=800 chars per hit) so the editor (and, experimentally, the worker) can find grounding quotes/stats instead of fabricating citations.
   - **Real-gemma verified ACCEPT case** (earlier this session, before today's bug fixes): against `s_db8f2ec3b920`, one round fixed an orphaned-code-block lint defect, score 0.920 -> 1.000. Two separate real-gemma REVERTs on citation-integrity weaknesses on the same fixture were also observed (flat 0.718->0.718 both rounds) — the editor's revert-on-regression safety working as designed, not a bug.
   - **Worker-side `search_evidence` — tested, NOT wired in.** A follow-up experiment gave the WORKER (not just the editor) `search_evidence` access. Measured no improvement, and surfaced two real tool bugs (below) plus a separate, disambiguated model-behavior ceiling: even with both bugs patched in a throwaway counterfactual, gemma-4-26B-4bit still ignored real `search_evidence` hits and re-fetched from the web when writing NEW content as a worker. This ceiling is independent of the tool bugs (controlled counterfactual) and is not worth re-litigating — worker-side wiring stays unshipped for this reason, not because the tool was broken.
   - **This session's fix — Bug A (contiguous-substring match) + Bug B (no source URL on hits):** `_run_search_evidence` did pure `ql not in line.lower()` matching, so multi-word queries (`"methodology and structure"`) returned 0 hits whenever the phrase was not one contiguous substring on a single raw line — the common case. Fixed with tokenized matching: a single-keyword query keeps the original per-line substring check; a multi-word query lowercases+splits into tokens and matches a line/window when a majority (`ceil(0.6*n)` tokens, tolerant of one absent/misspelled token) of distinct tokens co-occur within that hit's context window. Separately, hits carried `{file, line, context}` with no source URL even though every `evidence/source-NNN.md` is written as `f"URL: {url}\n\n{content}"` (`runner.py::_final_evidence_dossier`); each match now also carries a `url` field parsed from its file's first line, so a citation can be built from a hit without fabricating a link.
   - **Retest against real gemma with the fix live** (`s_db8f2ec3b920`, same fixture as the earlier REVERTs): `search_evidence` sanity confirmed working (`agent`/`loop`/`memory` -> 10 matches each, correct file/line). The editor pass still reverted BOTH rounds on the same citation-integrity weaknesses: `round 1 regressed: score 0.718->0.718, weaknesses 2->2; reverted`, `round 2 regressed: score 0.718->0.718, weaknesses 2->2; reverted`; artifact unchanged (27694 chars before/after); on-disk matches returned text. **This is a genuine still-negative result, not a partial win** — it confirms this model's citation-integrity ceiling on this fixture is real and independent of the tool bugs just fixed, since the tool itself now works correctly and the editor still could not use it to produce an improvement.
   - Validation: 2 new tokenizer/URL tests + 1 multi-word regression test added to `test_tools.py` (exact previously-failing queries `"methodology and structure"` / `"results and discussion"` now match, each with a correct `url`); full backend suite -> 520 passed.

157. Editor tool allowlist widened to 6 tools + `edit_file` cap raised to 100 KiB (documenting an undocumented earlier pass); cross-task R10 seeding contamination root-caused; binary-LLM relevance detector built and calibrated on the real contaminated fixtures; synthesis/contradiction prompt guidance added and calibrated against real gemma:
   - **Editor allowlist / `edit_file` cap (carried over from an earlier, undocumented pass this session)**: `_run_editor_pass`'s `ToolAugmentedClient(offer_tools=...)` now offers 6 tools — `read_file`, `search_evidence`, `read_artifact`, `patch_artifact`, `edit_file`, `glob` — up from the 4 entry 156 documented; `edit_file`/`glob` let the editor discover section files structurally and make simple unique-string substitutions without `patch_artifact`'s doc-hash contract. `studio/tools.py::_EDIT_FILE_MAX_BYTES = 100*1024` (up from the shared `agentkit.sandbox.core.MAX_OUTPUT_BYTES` 64 KiB default) is threaded through BOTH the read side (`Workspace.read(path, max_bytes=_EDIT_FILE_MAX_BYTES)`) and the write side (edited-content length check against the same constant), so a section file between 64-100 KiB is neither silently truncated on read nor rejected on write. Verified present via `grep` this session (not built by me — documenting the leftover).
   - **Cross-task-seeding contamination — confirmed root cause.** A live e2e run (`s_98742f3026ee`, "benchmarking autonomous coding agents") seeded via R10 (`TaskRunStore.similar_runs`, sim=0.957) from a DIFFERENT-topic prior task (`s_f2c641b4a7d2`, task_hash `1697bb0a06df`, "catalog management for local/remote agent loops"). The seeded Executive Summary/Background/Key Findings/Implications kept citations genuinely about catalog curation. Root cause: nothing in the pipeline forced seeded-but-off-topic content to be adapted. Honest nuance (not overclaimed as a systemic threshold bug): the two requirement texts are genuinely close in embedding space (full-requirement cosine 0.948) *partly* because both were generated by the same test-driver template ("Write a concise generic research report about X. Include citations. Structure the deliverable with these sections...") — some of the 0.957 similarity is self-inflicted by shared scaffolding language, not purely topical coincidence. This does NOT mean R10's 0.6 min_similarity seeding threshold is broadly miscalibrated — the fix is adaptation of seeded content, not threshold-tuning.
   - **Cosine-based relevance detector — calibrated and REJECTED before shipping**, per real evidence. Section-body-vs-requirement cosine on the real contaminated fixture scored the catalog-management content HIGHER against the WRONG (coding-agent) requirement (0.887) than against its OWN (0.869) — the wrong direction, at every granularity tried (section, sentence, topic-phrase-only). A synthetic control confirmed cosine only separates FAR-domain content (cookie recipe 0.609, Roman history 0.605) from on-topic (0.799); same-domain drift (catalog mgmt, 0.729) sits inside that band — exactly where R10 seeding lives by construction (R10 only fires on already-similar tasks). Building the cosine detector anyway would have shipped an inert feature on the exact case it exists for; escalated instead of building it, per this session's cost-aware / no-demo-gaming discipline.
   - **Binary LLM relevance check (`studio/relevance.py`, new)** — `relevance_issues(client, sections, requirement) -> (penalty, issues)`. Narrow, single-input classification (task requirement + one section's content only — no other sections, no whole document, no prior seed's own task, no comparison artifact), matching the design guidance that a constrained binary classification is more reliable than the open-ended pairwise judge `epoch_gate.py` already documents as tie-prone. Runs ONCE per section per epoch, gated on a new `_seed_cross_task` flag (`_seed_carry_forward` now returns `_seed_via_similarity` as a 7th tuple element — R10-sourced seed only; a same-task continuation epoch pays zero extra LLM calls) — NEVER inside `rubric_score`/`rubric_scorecard_100` themselves (those are called ~10+ times per epoch across `_editor_scored_issues`/`epoch_gate`/the final scoring block).
     - **Prompt calibration against the real fixture** (local `qwen`/14B-coder-4bit via oMLX; VibeProxy/haiku was down): a naive "is this relevant" phrasing flagged 0/8 real contaminated sections — the model defaults YES whenever content shares surface vocabulary ("agentic AI", "agents", "autonomous") with the task, the SAME failure mode cosine had (confirmed via a synthetic control: cookie-recipe/Roman-history correctly rejected, but same-domain catalog-mgmt drift accepted). Explicitly asking for the section's SPECIFIC topic ("not a broadly similar or adjacent one") recovered signal: 1/8 real sections flagged — non-zero, a genuine improvement over cosine's zero/wrong-direction result, but still a real recall ceiling with this model (verified robust to excerpt length: 400/600/800/1500-char caps all gave 1/8). A "extract the subject, then compare" CoT-lite scaffold was also tried and made it WORSE (0/8) — the model's own subject-extraction anchored on the task's vocabulary rather than the paragraph's actual content, reverted. Shipped: the "specific topic, not adjacent" phrasing.
     - Honest ceiling, not overclaimed: flagged in the module docstring with an upgrade path (retest against a stronger model — haiku/sonnet via VibeProxy — when available; no code change needed, just swap the client the caller passes in).
   - **`rubric_score`/`rubric_scorecard_100` gain an optional `relevance_penalty: float = 0.0` kwarg** (`studio/rubric.py`) — a plain precomputed [0,1] fraction, subtracted from the weighted base score / scorecard total, clamped. Both functions stay pure/deterministic (no I/O); the LLM call happens once, upstream, in `_run_phase_loop` (gated on `_seed_cross_task`), stashed on `self._epoch_relevance_penalty`/`self._epoch_relevance_issues` (mirrors the existing `self._last_scorecard_100` cross-method bridge pattern between `_run_phase_loop` and the sibling `_postrun_score_and_record`) and threaded into every scoring call for that epoch.
   - **Editor 4th action** (`_editor_scored_issues` gains `extra_issues`/`relevance_penalty` params, threaded through all 4 call sites in `_run_editor_pass`; `_EDITOR_PERSONA` updated): relevance issues are a pure ADDITION to the existing rubric+lint weakness list feeding the editor's ≤2-round revert-on-regression loop — loop mechanics (round cap, full revert, fresh-recompute) unchanged.
   - **Worker-side relevance repair-clause (Part A)** — mirrors the existing lint `_repair_clause` pattern exactly (`_run_phase_loop`, the `_artifact_copied` seed block): when `_seed_cross_task` and a `base_client` are available, `relevance_issues` runs against the SEED's sections and, on any flag, injects a narrow "EXCEPTION (relevance-repair-in-place): ... REPLACE it ... do not just append ... everything else stays verbatim" clause into the final-step worker prompt.
   - **Synthesis / contradiction-reconciliation prompt guidance (prompt-only, no new detector)**: `studio/artifact_lint.py::_citation_wall_issues` (quote-stacking detector) was ALREADY flowing into both consumer prompts via `lint_artifact` — confirmed by inspection before touching anything; the gap was that the fix-instruction, once the weakness fired, was generic. Bounded real-model calibration (`gemma-4-26B-A4B-it-heretic-4bit` via oMLX, 2 scenarios x 3 candidate instruction variants — terse-imperative, explanatory-with-example, checklist-style):
     - Scenario A (quote-wall: 3 verbatim quotes, no synthesis) and Scenario B (2 sourced claims giving different numbers for the same benchmark — a genuine contradiction).
     - Terse-imperative ("never restate a quote — interpret it") was DISQUALIFIED on real output: gemma DROPPED the verbatim quotes entirely in both scenarios while paraphrasing — a real grounding-loss regression risk for this codebase's citation-verification pipeline.
     - Explanatory-with-example and checklist-style BOTH preserved every verbatim quote intact AND added genuine synthesis sentences after each, AND correctly identified + reconciled the Scenario B contradiction ("this discrepancy... suggests the two sources are referencing different versions/subsets... necessitating verification"). Checklist-style's explicit "synthesis sentence AFTER it" framing was marginally the clearest about keep-then-add (vs. replace); its semantics were adopted, in prose form matching each target prompt's existing style (not literal markdown bullets).
     - The decomposed-turns contingency ("if combined variants disappoint, split into separate narrow turns, same batching philosophy as the editor's round loop") was NOT triggered — the combined single-turn instruction handled both jobs correctly on real output, so decomposition was not additionally tested (avoiding unnecessary live-model calls).
     - Shipped: `studio/prompts.py::_build_executor_prompt`'s WHY: field guidance (worker's citation-guidance section — QUOTE stays verbatim/mandatory in its own field; WHY now explicitly asks for synthesis beyond restating the quote + flagging any claim that contradicts one already in the document) and `studio/runner.py::_EDITOR_PERSONA` (the editor sees the whole assembled document, so is best positioned to catch cross-section contradictions — same semantics, prose-adapted to the persona's existing style).
   - Testing: `tests/test_relevance.py` (new — 5 synthetic unit tests + 1 real-data `@pytest.mark.integration` calibration test, deselected by default per the repo's `-m 'not integration'` addopts, confirmed passing when run explicitly against the real fixture + a live local backend); `tests/test_rubric.py` (+4, pure-arithmetic `relevance_penalty` tests); `tests/test_runner.py` (+3: editor `extra_issues` union unit test, editor-pass call-site threading test, cross-task worker-repair-clause E2E test mirroring the existing mermaid-repair test's structure).
   - Validation: `pytest backend/tests -q` -> 547 passed, 2 deselected (was 535 before this entry); `pytest backend/tests -m integration -q` -> 1 passed (the new real-data relevance calibration test), 1 skipped (the pre-existing `test_skills_paths.py` live test currently always skips — its `build_embedder({})` call resolves to `None` unconditionally per current code, an existing/unrelated quirk not touched here).

158. Two hill-climb anti-regression correctness fixes in `runner.py` (gate baseline + post-loop guard), each with a genuine regression test:
   - **Fix 1 — keep/discard gate compared against the wrong baseline.** In `_run_phase_loop`, the last-phase seed block (`if is_last and upstream:` / `if _artifact_copied:`) read the current on-disk `artifact.md` into `_seed_text` — the same variable that is returned from `_run_phase_loop` and fed as the `prior_text` baseline to `studio.epoch_gate.accept_epoch` in the sibling `_postrun_score_and_record`. By the last phase that on-disk artifact already holds THIS epoch's own folded RESEARCH_FINDING content, so the gate compared the epoch's output against itself — the "prior best to beat" was silently reset to the epoch's own just-generated work, defeating the keep/discard gate's anti-regression purpose. Fix: the on-disk read is now a LOCAL `_seed_on_disk` variable used only by the seed-lint and cross-task relevance repair clauses (its only real consumers); the epoch-seed parameter `_seed_text` (the actual carried-forward seed from the seed-resolution block, or `_seed_prior_from_path` for a manual `seed_path`, or empty on genuine cold start) now flows through to the gate untouched. Cold-start behavior is unchanged — an empty seed still hits `accept_epoch`'s documented "No prior -> accept" branch.
   - **Fix 2 — two competing anti-regression mechanisms unified into one.** The post-loop patch-apply path (after the phase loop, `reduce_patches` writeback) guarded its write with a crude `if _rr.text and len(_rr.text) >= _seed_len:` whole-doc length floor — inconsistent with the smarter section-granular `accept_rewrite` guard the per-phase writeback path already uses. Per direction, deleted the length floor and replaced it with the SAME `accept_rewrite(_cur_text, _rr.text)` guard (imported from `agentkit.artifacts.sections`), plus matching `writeback ACCEPT/REJECT ...` `_dbg` logging mirroring the per-phase call site. `accept_rewrite` already permits legitimate shortening (dedup, synthesis replacing verbose quote-dumping) while rejecting any merge that guts/deletes a content-bearing section and rejecting blank output — so a shorter-but-better merge is now kept where the length floor would have discarded it purely for being shorter. Quality (synthesis/relevance/verified sources) over raw length.
   - **Tests (`tests/test_runner.py`, +2 -> suite 549):**
     - `test_epoch_gate_baseline_is_the_seed_not_this_epochs_own_output`: seeds a distinctive prior (`SEED_SENTINEL_PRIOR_BASELINE`) via `TaskRunStore`, grounds a fresh finding (`FRESH_EPOCH_ONLY`) in the fetch cache so it is folded into `artifact.md` DURING the run, monkeypatches `studio.epoch_gate.accept_epoch` to capture the `prior_text` it actually receives, and asserts that baseline contains the seed sentinel and NOT the fresh-epoch content. Confirmed to genuinely catch the bug: reintroducing `_seed_text = _seed_on_disk` makes the captured baseline contain `FRESH_EPOCH_ONLY` and the test fails.
     - `test_postloop_guard_accepts_shorter_but_improved_rewrite`: a dedup/synthesis rewrite that is strictly shorter than the original but keeps every content-bearing section is ACCEPTED by `accept_rewrite` (the guard now wired into the post-loop path) while the old `len(new) >= len(old)` floor would REJECT it; a section gutted to its bare heading is still rejected — proving the swap changes behavior in the intended direction only.
   - Validation: `pytest tests -q` -> 549 passed, 2 deselected (was 547). No production behavior touched beyond the two guards; editor round-loop, relevance/rubric-penalty wiring, `epoch_gate.py` logic, and seed-shrink fixes left intact.

159. Wired the calibration-winning relevance-check prompt into `studio/relevance.py`, corrected a model-conflation error in the prior calibration record, and threaded the seed's real origin-topic through the runner:
   - **Model-conflation correction (the record was wrong).** Entry 157's `relevance.py` docstring claimed a "1/8 recall ceiling on gemma". That figure was actually measured against `qwen` (Qwen2.5-Coder-14B, a VibeProxy/oMLX fallback), NOT the deployed model. Re-calibrated against the ACTUAL deployed model (`gemma-4-26B-A4B-it-heretic-4bit`, the `gemma` profile): even the naive YES/NO baseline already scores **7/8 recall** on the same real fixture — the "1/8 on gemma" claim was false. Docstring rewritten to state the correction explicitly (calib harness + `res_gemma.json`/`res_qwen.json` in scratchpad; not re-run — the result is verified).
   - **New winning prompt (verbatim, tested).** Swapped the old naive "Answer YES or NO" phrasing for a DYNAMIC-EXEMPLAR few-shot + evidence-extraction prompt (`_build_prompt`): two labeled examples (EXAMPLE A = the seed's real origin subject → IRRELEVANT, EXAMPLE B = the report topic itself → RELEVANT), then a "quote the ONE on-topic sentence or NONE" evidence step ending in `QUOTE:` / `VERDICT: RELEVANT|IRRELEVANT`. Parsing is now `re.search(r"VERDICT:\s*(RELEVANT|IRRELEVANT)")` (case-tolerant); flag on IRRELEVANT; unparseable → do NOT flag (fail-open, matches `epoch_gate.py`). Real gemma calibration numbers: **7/8 recall, 1/8 false-flag** (the dynamic candidate `1d`; few-shot-alone regressed precision to 2/8, evidence-alone dropped recall to 3/8, static generic exemplar dropped recall to 5/8).
   - **References-skip (removes the one residual false-flag).** The single false-flag was always the "References"/bibliography section — a link list has no topical prose sentence to quote, a structural artifact not a discrimination failure. `relevance_issues` now skips sections whose heading is in `_REFERENCE_HEADINGS` (references/sources/bibliography/works cited/citations, case-insensitive) — not counted, not flagged. Simple heading check, not a general classifier.
   - **`seed_topic` threading (real R10 provenance).** Added a `seed_topic: str | None = None` param to `relevance_issues`; when a cross-task R10 seed fires, `runner._seed_carry_forward` now captures the picked prior's OWN `requirement` (`_seed_topic = _prior.requirement`) and threads it out (return tuple extended 7→8) through `_run_phase_loop` into the `relevance_issues(...)` call, so EXAMPLE A names the seed's real origin subject. Falls back to the generic phrase (`a different specific subject in the same broad field`) when no seed provenance is available.
   - **Tests (`tests/test_relevance.py`, +3 unit -> synthetic suite 8; `tests/test_runner.py` mock updated).** New unit tests: References-skip (bibliography section not counted/flagged), dynamic-exemplar `seed_topic` threading + generic fallback (asserts the exemplar text in the prompt), and unparseable-reply fail-open. The `@pytest.mark.integration` real-data calibration test now uses the `gemma` profile (not `qwen`), threads the seed's real requirement as `seed_topic`, and asserts the MAJORITY of contaminated sections are flagged (`penalty >= 0.5`, `>= 4` issues) — reflecting the corrected 7/8 recall instead of the old 1/8. `test_runner.py::test_cross_task_seed_reaches_worker_with_relevance_repair_instruction`'s fake client was updated from the old `YES or NO` contract to the new `VERDICT:` format.
   - Validation: `pytest tests -q` -> **552 passed**, 2 deselected (was 549). `pytest tests -m integration -q` -> **1 passed** against live gemma. Live sanity run of the wired production path on the real fixture: **penalty=0.8571 (6/7 flagged, References skipped)** — the one miss is "Limitations and Uncertainty"; strong recall confirms the wired prompt works, not just compiles.

160. Live-E2E self-propagating-contamination finding + two defense-in-depth fixes (the relevance classifier is correctly wired but has a known recall ceiling; these fixes prevent propagation, they do not claim detection is now perfect):
   - **The finding (root-caused precisely).** Live run `s_38360e413cdc` (a cold-start-intent run whose `auto_improve` triggered R10 cross-task semantic seeding anyway) seeded from `s_98742f3026ee` — which is ITSELF the original contamination case from earlier this session (catalog-management content bled into an unrelated report). Critically, `s_98742f3026ee` ran and was recorded to `task_runs.db` BEFORE the relevance-check feature (entries 157/159) existed, so its recorded score never had a relevance penalty applied — it looks clean in the DB and got picked as a "similar enough" R10 seed, propagating its own contamination into a new, unrelated run. The LLM classifier missed this specific case, which is the already-documented ~85% recall ceiling (entry 159's 7/8), **not a new bug** — classifier accuracy is out of scope and was not touched.
   - **Fix 1 — unconditional cross-task seed-adaptation instruction (detection-independent backup).** In `runner.py::_run_phase_loop`'s final-step seed block, whenever this epoch's content came from a cross-task R10 seed (`_seed_cross_task` is True — the same flag the relevance repair-clause already gates on), the worker/reducer prompt now gets an UNCONDITIONAL `CROSS-TASK SEED` notice, INDEPENDENT of whether `relevance_issues(...)` flagged any specific section. Pure prompt text gated on the known boolean — no new LLM call, no new detection. Stacks WITH (does not replace) the existing conditional repair-clause. Wording is an explicit two-step: "DROP that content entirely (delete it, do not keep or reword it) and WRITE NEW content addressing the CURRENT task" — the softer "replace/adapt" phrasing risked the model merely rewording the same off-topic substance; the existing conditional relevance repair-clause was tightened to the same DROP-then-WRITE-NEW wording while in that code region.
   - **Fix 2 — deprioritize pre-relevance-check entries as R10 seeds.** Added a `relevance_checked: bool` field to `TaskRun` + a `relevance_checked INTEGER NOT NULL DEFAULT 0` column to `task_runs` (`task_runs.py`), with a migration/backfill so existing rows (incl. `s_98742f3026ee`) default to `0` = "not checked / unknown". Set `True` on the recorded run only when `relevance_issues(...)` actually ran that epoch (threaded via `runner.Runner._epoch_relevance_checked`). `similar_runs()` now applies a rank penalty (`_RELEVANCE_UNCHECKED_PENALTY = 0.15`, subtracted from similarity for ranking only — raw similarity returned unchanged) to unchecked candidates: **deprioritize, not exclude** (most legitimate same-task runs are also unchecked, since the relevance-check only runs on cross-task-seeded epochs — excluding all of them would gut the seed pool). A relevance-checked seed of comparable similarity now outranks a pre-feature one, but a pre-feature seed is still available when it is the only close match. Additive schema + ranking only; no existing DB rows modified, no retroactive re-scoring.
   - **Tests.** `tests/test_runner.py` (+1): `test_cross_task_seed_injects_unconditional_adaptation_notice` — asserts the `CROSS-TASK SEED` notice reaches the worker when a cross-task seed fired even though the classifier voted RELEVANT for every section (conditional clause absent), AND is absent on a genuine cold-start run (empty store). The existing `test_cross_task_seed_reaches_worker_with_relevance_repair_instruction` was updated to the tightened DROP-then-WRITE-NEW wording. `tests/test_similar_runs.py` (+4): flag round-trips through the DB; unchecked candidate deprioritized below a tied checked one but still present (not excluded); checked candidate selected normally with no penalty; legacy pre-column rows backfill to `relevance_checked=False`.
   - **Honest limitation note.** The LLM relevance classifier's ~85% recall ceiling (entry 159) is a known, accepted limitation. These two fixes are defense-in-depth (Fix 1: always-on adaptation instruction) and propagation-prevention (Fix 2: stop pre-feature contaminated runs from being reselected as seeds) — NOT a claim that per-section detection is now perfect.
   - Validation: `pytest tests -q` -> **557 passed**, 2 deselected (was 552). No production behavior changed for non-cross-task-seeded runs (both fixes gate on the cross-task-seed boolean / the new flag).

161. Coarse whole-document seed-relevance gate — a NEW, earlier, cheaper defense that DROPS a cross-field seed before it ever enters generation (wired, not yet e2e-validated):
   - **What it is.** A single whole-doc summarize-then-compare LLM call (`studio/relevance.py::seed_doc_relevance(client, seed_text, requirement) -> bool`) that runs ONCE at seed-resolution time, BEFORE epoch 1's worker/orchestrator, in `runner.py::_seed_carry_forward`. It summarizes the whole seed doc's actual subject, summarizes the task's actual subject, and returns a RELATED/NOT_RELATED verdict on whether they are the SAME SPECIFIC subject (same broad field is explicitly NOT enough). On NOT_RELATED it drops the seed entirely (`_raw_seed=None`, `_seed_via_similarity=False`, `_seed_topic=""`) and the run falls back to the existing cold-start blank template skeleton (the §14.1 `_artifact_copied==False` path). This is the coarse counterpart to the per-section `relevance_issues()` check: one call vs. N-per-epoch, catches wholesale cross-field mismatch that the section check's known ~85% recall ceiling can miss.
   - **Where.** `studio/relevance.py`: new `seed_doc_relevance()` + `_seed_gate_prompt()` (prompt reused VERBATIM from the calibrated `tmp/probe_coarse_seed_gate.py::strict_prompt`), `_SEED_DOC_CAP=30000`, `_SEED_VERDICT_RE` (takes the LAST verdict token, as the model states summaries first). `studio/runner.py`: `base_client` now built before `_seed_carry_forward` and threaded in; the gate fires inside the seed-resolution block, gated on `_seed_via_similarity` (the existing cross-task R10 flag) so same-task-lineage/epoch-carry-forward/explicit-`seed_path` seeds NEVER pay for it. `_dbg("seed dropped by coarse relevance gate ...")` on a drop.
   - **Design boundaries (deliberate).** ADDITIVE to — not a replacement for — `relevance_issues()`: calibration proved a whole-doc RELATED verdict can coexist with real section-level contamination, so the per-section check keeps running every epoch regardless of this gate's verdict. Fail-open on every error path (client down, timeout, unparseable reply → keep the seed): a gate failure must never silently blank a document. Only fires on cross-task seeds; same-`task_hash` lineage is skipped entirely (no LLM call). One call per seed-resolution (epochs 2+ reuse epoch 1's own output via the existing `_epoch <= 1` guard). `epoch_gate.py` and the per-section `relevance_issues()` call site were NOT touched.
   - **Calibration evidence backing it** (from `tmp/probe_coarse_seed_gate.py`, real gemma-4-26B via oMLX, 10 fixtures × 3 samples = 30 real samples): the STRICT "name-the-specific-subject" prompt (the one reused) scored **100% recall on cross-field mismatch** (NEG-C catalog-agent report → redis/postgres task) and **0/6 false-reject** across the genuine-reuse positives (same-subject-different-hash rehashes, reworded requirements, same-task lineage). Known non-goal: same-broad-field-but-different-specific-subject (NEG-D Pi/Craft-build vs catalog-management) is the hard case the coarse gate is NOT guaranteed to catch — that is exactly what the per-section `relevance_issues()` check still covers. The naive "just ask if related" prompt variant failed calibration at 1/6 recall and was rejected.
   - **Tests.** `tests/test_relevance.py` (+5, all mock-client unit tests): RELATED verdict keeps the seed (returns True, 1 LLM call); NOT_RELATED drops it (returns False, 1 call); empty inputs (None client / empty seed / empty requirement) skip the LLM call entirely (0 calls — mirrors the "non-cross-task skips the gate" mechanic); client-raises → fail-open (keep seed); unparseable reply (no verdict token) → fail-open (keep seed).
   - Validation: `pytest tests -q` -> **562 passed**, 2 deselected (was 557). Wired and unit-tested; **NOT yet e2e-validated** against a live cross-field-seed run.

162. Duplicate-title-at-two-heading-levels — heading-level standardization at the section fold boundary (root-caused on real live evidence, fixed + regression-tested):
   - **Finding (real live evidence, session `s_089481ef5161`).** Every top-level section title appeared TWICE in the assembled `artifact.md` — once at `#` (H1, lines 1-134) and once at `##` (H2, the canonical scaffold, lines 135+): `# Executive Summary` … `## Executive Summary`, same for Scope/Background/Key Findings. Traced via the phase-step `io/` scratch files: `e1:s1.spoke8` is the section-aware reducer (its `.in.md` is the 18-byte marker `<injected reducer>`); its `.out.md` (10KB) is NOT a `RESEARCH_FINDING`/`PATCH_TARGET` block but a complete standalone report written with `#` H1 headings — the reducer synthesized from scratch at the wrong heading level instead of patching the H2-keyed sections.
   - **Root cause.** `agentkit.artifacts.sections.split_sections` keys sections ONLY on `##`. At the fold boundary (`studio/section_workspace.py::split_artifact_to_sections`) the reducer's rich H1 blocks were therefore parsed as `(intro)` **preamble**, while the outline still generated a `##` section of the same title from `section_bodies.get(...) == None` → a `_(pending)_` **placeholder**. Result: rich H1 content orphaned in preamble + thin H2 placeholder of the same name = two copies at two levels. No heading-level normalization existed anywhere on the fold path (`normalize_artifact`'s `dedupe_sections` is level-agnostic, but the assembled artifact is built from section files whose preamble/section split had already mis-routed the H1 block).
   - **Fix (heading standardization at the fold boundary, `studio/section_workspace.py`).** New `_normalize_heading_levels(text, known_titles)` runs INSIDE `split_artifact_to_sections`, BEFORE `split_sections`: any heading whose title (level- and enumerator-agnostic via new `_match_key`, matching `artifact_text._heading_key`) matches a KNOWN outline title is normalized to `##`; every other heading is left untouched (legit `###` subsections like `### 1. The Power of the Agent Loop` and a genuine document title match no section title, so keep their level). Scoped precisely — NOT a blanket `#`→`##` demote. On the now-recognized duplicate, the section-body dedup keeps the **richer** body (merge/supersede, consistent with `dedupe_sections`' "richest body kept" and `accept_rewrite`'s no-section-gutted principle) rather than first-wins — so the reducer's rich H1 synthesis is preserved and SUPERSEDES the thin placeholder, not discarded.
   - **The reducer's H1 dump was genuinely useful, not pure duplication.** Its H1 Executive Summary/Scope/Background/Key Findings carried real synthesis while the H2 scaffold versions were thin placeholders; the pre-fix bug left that value orphaned in preamble. The fix ROUTES it into the correct section file. Confirmed: pre-fix the Executive Summary section is a `_(pending)_` placeholder with the rich content stranded in preamble; post-fix it is the 993-char real synthesis.
   - **Test evidence.** Real fixture `tests/fixtures/reducer_h1_dump.md` = verbatim bytes of `s_089481ef5161/io/e1:s1.spoke8.out.md`. New `tests/test_section_workspace.py::test_reducer_h1_dump_folds_into_single_sections` folds it through `write_section_workspace` + `assemble_artifact_from_sections` and asserts each of the 8 outline titles appears exactly ONCE (all at `##`, no orphan `#`), the Executive Summary section is the rich synthesis (no `PLACEHOLDER`), and the legit `### 1.` subsection keeps its level. Verified the test FAILS pre-fix (placeholder + orphaned H1 preamble). Out-of-scope untouched: the coarse `seed_doc_relevance` gate and per-section `relevance_issues()` were NOT modified.
   - Validation: `pytest tests -q` -> **563 passed**, 2 deselected (was 562, +1 for the new regression test). No regressions. NOT committed (tree left uncommitted for review).

163. Post-run markdown beautification of the final served artifact (`mdformat` + `mdformat-gfm`, pinned):
   - **What.** New `studio/markdown_format.py::beautify_markdown(text)` calls `mdformat.text(text, extensions={"gfm"})` to normalize the SERVED research report's cosmetics — heading spacing, list markers (`*`→`-`), blank-line consistency, GFM table column alignment. Pure prettification, not a content-correctness change.
   - **Wiring point (post-run, served copy only).** Applied at the single `return _outcome, result_output` at the end of `runner.py::_postrun_score_and_record`, AFTER the whole scoring/gating body has run and AFTER `_store.record(..., result_text=result_output)` has written the raw text to `task_runs.db`. So `rubric_score` / `accept_epoch` (epoch_gate) / `relevance_issues` and the hill-climb DB record all score the UNFORMATTED generated text; only the served copy (`session.record_run(result=...)` + `self._last_result`, which builds the terminal `done` event) gets beautified. Grep confirms `beautify_markdown` appears only at that return + the module itself — never in a scoring/gating path.
   - **Fail-open.** Wrapper wraps `mdformat.text` in `try/except Exception` → logs `warning` and returns the original text unchanged (matches the codebase's `relevance_issues`/`seed_doc_relevance` fail-open convention). A formatter crash can never block a run from completing or drop content. Empty input short-circuits.
   - **Pinned versions.** `mdformat==1.0.0` + `mdformat-gfm==1.0.0` added to `backend/pyproject.toml` dependencies (mdformat's docs warn formatting style can drift release-to-release, so pinned), installed into `backend/.venv` via `uv pip install`.
   - **Tests.** New `tests/test_markdown_format.py`: (1) messy sample → normalized headings/list markers/aligned table; (2) empty passthrough; (3) fail-open — `monkeypatch` forces `mdformat.text` to raise, asserts the original text is returned unchanged and nothing propagates. (mdformat is robust — it auto-closes an unclosed fence rather than raising — so the fail-open path is exercised via a simulated crash, the honest deterministic way.)
   - Validation: `pytest tests -q` -> **566 passed**, 2 deselected (was 563, +3 for the new tests). No regressions. Out-of-scope untouched: the coarse `seed_doc_relevance` gate, per-section `relevance_issues()`, and the entry-162 heading-standardization fix. NOT committed (tree left uncommitted for review).

164. `_TITLE_STOP_RE` too-greedy title truncation (real live evidence, fixed + regression-tested):
   - **Finding.** `studio/artifact_text.py::resolve_report_title`'s H1-title derivation truncated the Pi/Craft task's report title to `# Study how to` — cutting off mid-sentence. Live evidence: `tmp/studio-workspaces/s_9b7bacfdc703/artifact.md` line 1.
   - **Root cause.** `_TITLE_STOP_RE = r"(?is)\b(?:include|use|cover|provide|with|and include|also include)\b.*$"` matched the FIRST occurrence of any listed word ANYWHERE in the sentence — not just a trailing meta-instruction clause — and deleted everything from that point to end of string. Requirement's first sentence: "Study how to **use** Pi and Craft to develop agents..." — "use" is a core topic word (4th word), not a trailing instruction, but the old regex nuked everything after it regardless.
   - **Fix.** Anchored the stop-word match to a comma-based clause boundary: `r"(?is),\s*(?:\w+\s+){0,3}\b(?:include|use|cover|provide|with)\b.*$"` — now only strips a stop-word clause that follows a comma (allowing up to 3 filler words, e.g. ", need to include"), which correctly targets genuine trailing instructions ("...report, need to include example code...") while leaving core-sentence verbs alone. Dropped the redundant `and include|also include` alternatives (now subsumed by the comma-anchor + filler-word allowance).
   - **Test.** `tests/test_section_ownership.py::test_resolve_report_title_keeps_use_as_core_topic_word` — real requirement text, asserts the title keeps "use Pi and Craft..." and drops the actual trailing instruction clause. All 4 pre-existing title tests still pass unchanged.
   - Validation: `pytest tests -q` -> **567 passed**, 2 deselected (was 566, +1). No regressions. NOT committed.

165. Independent Codex adversarial review — 6 production-risk findings, all fixed with real code + regression tests (2026-07-02). Each finding cited below with file/line evidence and the fix applied. Validation: studio `pytest tests -q` -> **575 passed**, 2 deselected (was 567, +8 new); agentkit lib `pytest tests` -> **433 passed** (+1 new). NOT committed (tree left uncommitted for review).
   - **Finding 1 (CRITICAL) — catalog routes unauthenticated + `/replace` left a stale embedding.** `studio/app.py`'s `/catalog/templates/{audit,replace,approve,export,import}` operated on the shared `TemplateStore()` (task_runs.db) with no auth; and `studio/templates.py::replace_template` updated only skeleton/type/source/status/reason, leaving the OLD `requirement_embedding` so `find_template()` would serve the REPLACED skeleton on a stale semantic match. **Fix (auth):** added `studio/app.py::require_catalog_admin` FastAPI `Depends` on the 5 mutation/export routes — an OPT-IN `X-Studio-Admin-Key` header compared (`hmac.compare_digest`) against `STUDIO_ADMIN_KEY`; 403 on missing/mismatch when the env var is set, keyless localhost-dev default preserved when unset (the app has no other auth and the GUI sends no key, so fail-closed-by-default would break the GUI + tests — flagged as opt-in, a one-line flip to fail-closed if desired). `GET /catalog/templates` (inventory previews only, no skeleton bodies) left open. **Fix (embedding):** `replace_template` now recomputes `requirement_embedding` when an embedder is wired, else NULLs it, so a replaced skeleton can't be auto-reused on outdated semantics until re-learned. **Tests:** `test_codex_findings.py::{test_catalog_mutation_requires_admin_key_when_configured, test_catalog_open_when_no_admin_key, test_replace_template_clears_stale_embedding}`.
   - **Finding 2 (HIGH) — module globals in `agentkit/topology/dynamic.py` corrupt concurrent sessions.** `run_plan()` wrote `_POOL_WORKERS`/`_MAX_SPOKES`/`_REDUCER` as module globals every call; two runs on different threads (Studio runs each session on its own `threading.Thread`) could overwrite each other's reducer/sizing mid-run. **Fix:** converted all three to `contextvars.ContextVar` (isolated per call/thread — a fresh thread starts with the default); `run_plan` `.set()`s them at entry and `.reset()`s in `finally` (also makes nested calls safe); readers (`_spoke_cap`, `_parallel_map`, and each strategy `.run()` via a local shadow) read from the vars. No external importers of the globals. **Test:** `tests/test_dynamic_topology_concurrency.py` — two concurrent `run_plan` calls with distinct reducers + `max_agents`, barrier-forced overlap, asserts no reducer-tag or breadth-cap bleed.
   - **Finding 3 (HIGH) — race-prone version allocation in `studio/task_runs.py`.** The runner called `next_version()` then `record()` as two steps (`runner.py:3384`); two concurrent auto-improve runs of the same task could compute + insert the SAME version, making `latest()` (ORDER BY version DESC) nondeterministic. **Fix:** added `UNIQUE(task_hash, version)` via `CREATE UNIQUE INDEX IF NOT EXISTS idx_task_runs_hash_version` (best-effort on legacy dup DBs) + new `TaskRunStore.record_versioned()` that allocates+inserts and retries on the resulting `IntegrityError` (retry-on-conflict = the sanctioned atomic-allocation equivalent). Runner call site now uses `record_versioned`. Test-fixture fallout: `test_similar_runs.py::_run` pinned `version=1` for repeated task_hashes (exactly the dup the index blocks) → switched to an `itertools.count` so each record gets a distinct, realistic version. **Test:** `test_codex_findings.py::test_record_versioned_no_duplicate_under_concurrency` — 8 threads, separate stores/connections to one db file, barrier overlap, asserts 8 distinct versions 1..8 and a deterministic `latest()`.
   - **Finding 4 (HIGH) — process-global fetch cache let one session verify citations via another's fetches.** `studio/tools.py::_fetch_cache` was a single process-global dict; `findings.py` grounding accepts a citation if its URL matches ANYTHING in that cache, so session B could "verify" against session A's fetch (plus a bare iterate-vs-write race). **Fix:** replaced the dict with `_ContextFetchCache` — a `contextvars`-backed dict proxy (each run-thread gets its own backing store; reads snapshot before iterating). It preserves the full dict interface, so all ~50 internal/`findings.py`/test call sites (incl. `test_runner.py`'s 40 refs and `conftest.py`) are untouched. **Test:** `test_codex_findings.py::test_fetch_cache_isolated_across_sessions` — thread A caches a URL and grounds it; thread B (barrier-overlapped) must NOT see A's fetch.
   - **Finding 5 (HIGH) — `last_run` published (raw) before postrun neutralization/editing finished.** `runner.py:1555` called `session.record_run()` with the RAW result BEFORE `_postrun_score_and_record()` (which still mutates the served artifact — URL neutralization, editor pass); `/chat` and `/export` read `session.last_run` and would serve stale/unneutralized output in that window. **Fix:** removed the premature `record_run` (its only rationale, "so /export can serialize", is served by the final `record_run` at the end which now solely sets `last_run` from the post-processed text). Confirmed nothing between the two records reads `last_run` (only `app.py`'s `/chat` + `/export` do). **Test:** `test_codex_findings.py::test_last_run_published_only_after_postrun` — wraps `_postrun_score_and_record` + `session.record_run` to record call order, asserts exactly ONE record, after postrun.
   - **Finding 6 (MEDIUM) — fail-open URL verification masked fabrication during outages.** `_verified_urls_from_cache()` (`runner.py:91`) returns `[]` both when verification ran with no matches AND when the `.web_cache.json` is missing/unreadable; `neutralize_unverified_urls()` fail-opens on empty (deliberately, so an outage never blanks real citations), so fabricated URLs pass through silently in the couldn't-check case. **Fix:** added `runner.py::_web_cache_available()` distinguishing "verification could run" (cache present + non-empty) from "couldn't run"; at the publish/neutralize site it now emits a distinguishable warning when the set is empty AND the cache is unavailable AND the text cites URLs — closing the *silent* part of the risk without regressing the deliberate fail-open (blanking real citations on a transient outage is worse). **Tests:** `test_codex_findings.py::{test_web_cache_available_distinguishes_missing, test_neutralize_fail_open_preserved_but_verified_set_still_strips}`.
   - **Out of scope (untouched, per instruction):** the coarse whole-doc seed-relevance gate, per-section relevance check, entry-162 heading-standardization, entry-163 mdformat wiring, entry-164 `_TITLE_STOP_RE` fix.

166. [LOGGED, NOT FIXED — deferred per user] Run-death-loses-all-progress on sustained LLM backend outage:
   - **Finding.** Two live runs (`s_9b7bacfdc703`, `s_8a6fa3c6ba8f` — both auto-improve attempts seeding from the clean `s_7be8460e09e7` cold-start) died with an `error` SSE event, message `"Connection error."`, `where: "runner"`. `s_9b7bacfdc703` got to step e1:s2 (406,814 tokens) before dying; `s_8a6fa3c6ba8f` died at the very start (0 tokens) — consistent with a genuine multi-minute oMLX outage window, not a single-call blip.
   - **Root cause (traced, not guessed).** `studio/client.py::StudioChatClient.chat` already wraps every call in `agentkit/backends/openai_compat.py::_resilient()` (7 retries, backoff `2.0 * (attempt+1)`, covers `APIConnectionError`/`APIError` — genuinely resilient design, comment at `client.py:49-54` documents this was deliberately widened from 4 retries/~30s to 7/~4min for exactly this kind of transient-outage case). Once retries exhaust, `_resilient_with` raises `LLMUnavailable(str(exc))` — and `str(APIConnectionError(...))` is literally `"Connection error."`, which is exactly the message that surfaced. This propagates up to ONE top-level catch-all in `runner.py:1098-1099` (`except Exception as exc: self._emit(ErrorEvent(message=str(exc), where="runner"))`), which ends the run entirely. Nothing is recorded to `task_runs.db` for that attempt — no partial artifact, no `relevance_checked` flag, no version row — so a subsequent run has zero carry-forward from the dead attempt's real research work (406K tokens' worth, in `s_9b7bacfdc703`'s case) and must restart from the last successfully-recorded run instead.
   - **Proposed fix (not implemented — user chose to log and defer given session cost).** On `LLMUnavailable` reaching the top-level catch, before emitting the terminal error event, persist whatever partial artifact/section-workspace state exists for that attempt to `task_runs.db` with an explicit incomplete/failed status (distinct from a normal completed version), so `TaskRunStore.latest_with_content()`/`similar_runs()` can still surface it as a legitimate (if partial) seed for the next attempt on that task, rather than the work being silently lost. Needs: (a) a status column or reuse of an existing flag to mark "died mid-run, partial only" rows so they're not treated as competitive with genuinely-completed high-score runs, (b) confirm section-workspace state is actually intact/consistent at the point of a mid-phase connection death before persisting it as a seed candidate.
   - Not investigated: whether this is the SAME oMLX outage window that also affected the concurrent Codex adversarial-review invocation (unrelated process, different port) — plausible local-machine resource contention during a period of heavy parallel background load (2 studio runs + codex challenge + earlier agent dispatches running concurrently), not necessarily an oMLX-specific defect.

167. Generic explicit-requirement compliance checker — extracts the checkable requirements literally stated in ANY task and verifies the artifact against them, feeding the same penalty/weakness/editor machinery (built + calibrated on real gemma, 2026-07-02):
   - **Motivation (real live evidence).** Across every Pi/Craft run this session ("study how to use Pi and Craft… need to include example code or design architecture") the report never reliably included an architecture diagram. `runner.py` had mermaid LINT/REPAIR but ZERO proactive guidance to GENERATE a required artifact. Design constraint (user, firm): the fix must be GENERIC — no hardcoded "diagram"/"citation" keyword lists; the LLM must extract + verify requirements dynamically from the actual task text every run, honoring the repo's task-neutral guardrail in `agentkit-studio/CLAUDE.md`.
   - **New module `studio/requirement_compliance.py`** (mirrors `studio/relevance.py` file org / fail-open / penalty-scale conventions):
     - `extract_requirements(client, task_text) -> list[str]` — ONE LLM call per RUN (cached; the task text is static for a `task_hash`). Enumerates EXPLICIT, checkable requirements ("include a diagram", "include example code", "cite ≥3 sources", "under 800 words", "cover X and Y"), excluding vague quality goals. Fail-open → `[]`.
     - `requirement_compliance_issues(client, reqs, artifact) -> (penalty, issues)` — ONE LLM call per EPOCH on the assembled artifact. Evidence-grounded per-requirement SATISFIED/NOT_SATISFIED verdicts (`REQUIREMENT n:` parse); `penalty = unsatisfied/total` in [0,1] (same scale as `relevance_issues`); human-readable issue strings for the editor weakness list. Fail-open → `(0.0, [])` on client down / unparseable / empty.
   - **Rubric wiring (`studio/rubric.py`).** Added `compliance_penalty: float = 0.0` kwarg to `rubric_score` + `rubric_scorecard_100` — pure/deterministic, mirrors `relevance_penalty` exactly; the two penalties stack (subtract-and-clamp; scorecard derates `total` by the combined fraction of `max_points`). No I/O inside.
   - **Runner wiring (`studio/runner.py`).** Run-level cache `self._task_requirements` (extract-once) + per-epoch `self._epoch_compliance_{penalty,issues}` (init in `__init__`, mirror the `_epoch_relevance_*` bridge). Verification runs in `_postrun_score_and_record` on the FINAL assembled `_scored_text`, BEFORE the editor pass. Its issues+penalty (a) union into the editor pass `extra_issues` and its precomputed-penalty arg (so the editor can REPAIR a miss in-place THIS epoch — the mechanism that can add a missing artifact), (b) thread into the recorded `rubric_score`/`rubric_scorecard_100` via `compliance_penalty=`, (c) union into the recorded `_weaknesses`. A worker repair-clause (`_compliance_repair_clause`) injects the LAST epoch's misses into the reducer prompt on hill-climb epochs (zero extra LLM call — feed-forward like the weakness list; empty on cold-start, where the same-epoch editor pass is the backstop).
   - **REAL calibration (deployed `gemma-4-26B-A4B-it-heretic-4bit` via oMLX :8000, on real `tmp/task_runs.db` fixtures). The naive first-draft verifier failed and was iterated, exactly as the coordinator anticipated:**
     - **Case A (Pi/Craft — the primary case).** Extraction correctly pulls "Include example code or design architecture" (plus the task's explicitly-listed section headings — also genuine explicit requirements). Verified against the real code-bearing artifact (len 11794, no diagram): penalty **0.0 — the OR requirement is HONESTLY satisfied by the code that is present**, so the generic checker does NOT fabricate a diagram demand. **This is the honest, correct generic behaviour, reported plainly rather than re-hardcoding diagram detection under a generic name:** "example code OR design architecture" is literally satisfied by code alone; diagram reliability only follows when a task actually MANDATES a diagram (an AND requirement). On a genuinely thinner Pi/Craft artifact the checker flagged the real missing SECTION requirements while correctly NOT flagging code/architecture.
     - **Case B (catalog task, "Include citations." — a DIFFERENT requirement, proves generality/not-diagram-specific).** Extraction pulls "Include citations."; on the real cited artifact (30 distinct URLs) → penalty **0.0, citations NOT flagged** (correct). **Naive-verifier iteration:** first-draft and several fixture attempts scored 0.0 even after stripping URLs — reading the model's QUOTED evidence each time showed it was HONEST: crude strips left markdown-link scaffolding, parenthetical source attributions, and a plain-text References section, all of which the model correctly cited as real citations. Fix = **evidence-grounding** in the verifier prompt (mirrors the winning "quote the on-topic sentence" design in `relevance.py`), which surfaced the model's reasoning and let the calibration converge. Real research reports proved to be saturated with citation structures, making a real-data citation-free fixture impractical; the decisive absent-case ("flags when genuinely missing") is therefore pinned on an unambiguous controlled plain-prose doc, where the checker flags "Include citations." NOT_SATISFIED stably across 3 runs.
   - **Tests.** `tests/test_requirement_compliance.py` — 15 unit tests (mock client: extraction line-parse/bullet-strip/NONE-sentinel/empty/fail-open; verification flag/no-flag/penalty-scale/out-of-range/fail-open×2; rubric kwarg purity + penalty stacking + scorecard derate) + 2 `@pytest.mark.integration` real-gemma calibration tests (Case A + Case B, deselected by default via `-m 'not integration'`, stable across 3 runs).
   - **Validation.** studio `pytest tests -q` → **590 passed, 4 deselected** (was 575 passed / 2 deselected — +15 unit, +2 integration; 0 regressions). NOT committed (tree left uncommitted for review).
   - **Files:** `studio/requirement_compliance.py` (new), `studio/rubric.py` (`compliance_penalty` on `rubric_score`:528 + `rubric_scorecard_100`:560), `studio/runner.py` (`__init__`:~1000 state; `_run_phase_loop`:~2131 worker repair-clause + f-string inject; `_postrun_score_and_record`:~3286 verification block, ~3312 editor pass merge, ~3372 score calls, ~3398 weakness union), `tests/test_requirement_compliance.py` (new).
   - **Honest limitation (per design constraint — stated plainly, not papered over).** For an OR requirement already satisfied by code, the checker will not force the other disjunct (a diagram). The user's original wish ("make diagrams show up reliably for the Pi/Craft task") is therefore only met when the task's stated requirement actually mandates a diagram; a purely generic, no-hardcoding checker cannot manufacture that demand from an OR the artifact already satisfies. The mechanism DOES reliably drive fixes for genuinely-missing explicit requirements (missing sections, missing citations), which is the generalizable win.
   - **Out of scope (untouched, per instruction):** coarse seed-relevance gate, per-section relevance check, heading-standardization, mdformat wiring, `_TITLE_STOP_RE`, all 6 entry-165 Codex-findings fixes.

168. Codex second-opinion follow-up to entry 167 — `quality_opportunities` lane + editor soft-accept tie-breaker (built + full suite green, 2026-07-03):
   - **Codex finding (treated as the design spec).** Entry 167's hard-compliance decision is CORRECT and must not change: "include example code OR design architecture" is honestly satisfied by the code the artifact has, so the OR is SATISFIED, `compliance_penalty=0.0`, no hard issue — turning OR into AND would make the checker dishonest. BUT calling the product gap "solved" because the OR is technically met is rationalizing: entry 167 solved "don't miss literal requirements"; it did NOT solve "architecture-heavy reports should reliably CONTAIN the architecture visual." Codex's concrete fix: add a SEPARATE generic lane — `hard_issues` (unmet requirements, drive `compliance_penalty`) vs. `quality_opportunities` (unsatisfied ALTERNATIVE branches of an OR clause ALREADY satisfied by another branch). Feed opportunities to the editor as NON-blocking "consider also" polish, never as requirement failures. And fix the tie-breaker: the editor gate rejects an equal-score round, so a round that successfully ADDS the diagram would be discarded as "no score gain" — the suggestion would be decorative. Explicitly out of scope (Codex flagged as a distinct future concern): the constrained nodes/edges→mermaid deterministic builder. No diagram keyword list; no Pi/Craft special case — stays task-neutral.
   - **Extraction now preserves OR STRUCTURE (`studio/requirement_compliance.py`).** `extract_requirements` return shape changed `list[str]` → `list[list[str]]`: each element is a requirement GROUP, each group a list of interchangeable BRANCHES (single-branch = mandatory item; multi-branch = OR clause satisfied by ANY branch). Generic prompt addition only: the model puts alternatives on ONE line separated by ` || ` (parsed by new `_ALT_SEP_RE`) — no domain vocabulary. `_normalize_groups` also accepts the legacy flat `list[str]` (each → single-branch group), so a stale cached value never breaks.
   - **Verification verifies each BRANCH, judges each GROUP (`requirement_compliance_issues`).** Return shape `(penalty, issues)` → `(penalty, hard_issues, quality_opportunities)`. It flattens branches into the numbered verifier prompt (unchanged evidence-grounded per-line SATISFIED/NOT parse), then aggregates back per group: group SATISFIED iff ANY branch is. `penalty = unsatisfied-GROUP fraction` (reduces EXACTLY to the old per-item fraction when every group is single-branch — no behaviour change for mandatory-only tasks). `hard_issues` = one string per group with NO branch met (mandatory miss, or OR-with-no-branch — the "none of the stated alternatives were met (…)" form). `quality_opportunities` = one string per unsatisfied SIBLING branch of an ALREADY-satisfied OR group ("Explicit alternative not included: <branch> …"). Opportunities are structurally incapable of touching `penalty`/`hard_issues`. Fail-open now `(0.0, [], [])`.
   - **Rubric untouched (verified by test).** `studio/rubric.py::rubric_score`/`rubric_scorecard_100` `compliance_penalty` semantics unchanged — `quality_opportunities` are NEVER passed to the rubric, so the score cannot be derated by them. Pinned by `test_quality_opportunities_never_affect_the_rubric_penalty` (an OR satisfied by one branch → opportunity present, penalty 0.0 → `rubric_score(compliance_penalty=penalty) == rubric_score()`).
   - **Runner wiring (`studio/runner.py`).** New per-epoch state `self._epoch_quality_opportunities` (init `[]` in `__init__` + reset in `_postrun_score_and_record`). The 3-tuple is unpacked in `_postrun_score_and_record`; `quality_opportunities` are threaded into `_run_editor_pass` as a SEPARATE param (NOT merged into `extra_issues`, which stays hard-only) plus an `opportunity_recount` callback (`_make_opportunity_recount`, re-verifies the cached task requirements against a candidate text, fail-open to the epoch-start count). Editor phase: `_editor_drive_round` gains one clearly-labelled OPTIONAL-polish turn (`_editor_opportunity_prompt`: "already satisfied by another branch, NOT required, add ONLY if cheap and grounded, else change NOTHING") that only fires when opportunities exist. The worker/reducer repair clause (`_epoch_compliance_issues`, hard-only) is unchanged — opportunities are editor-scope only, per dispatch instruction #5.
   - **Editor tie-breaker (the "otherwise decorative" fix).** The revert condition (`new_score <= cur_score or len(new_issues) >= len(cur_issues)`) is untouched; a NARROW additional accept-path is layered on top: a round that would revert is instead KEPT iff it (a) does not regress the score (`new_score >= cur_score`), (b) does not add weaknesses/lint (`len(new_issues) <= len(cur_issues)`), AND (c) strictly reduces the outstanding opportunity count (`opportunity_recount(new_text) < cur_opp_count`, where `cur_opp_count` is recomputed fresh at each round's top). ALL existing regression protections for hard score/lint/weaknesses are intact — this only rescues rounds that genuinely reduced opportunities without regressing anything. Gated on `quality_opportunities` being non-empty, so the normal 99% path pays ZERO extra compliance calls. The accept `GateEvent` detail distinguishes the soft path ("accepted on reduced optional opportunities").
   - **Tests (`tests/test_requirement_compliance.py` +5 unit, `tests/test_runner.py` +2).** THE Codex-specified pinning case — `test_or_group_satisfied_by_one_branch_is_opportunity_not_penalty`: a code-only artifact against one `["include example code","include design architecture"]` OR group → `penalty == 0.0`, `hard_issues == []`, `quality_opportunities` has EXACTLY one entry naming the missing architecture branch. Plus: OR-with-no-branch-met is a hard issue (`test_or_group_no_branch_satisfied_is_hard_issue`, penalty 1.0), single-mandatory unmet still hard not opportunity (`test_single_mandatory_unmet_still_hard_issue_not_opportunity` — regression-proofs real-miss detection), OR-alternatives parse into one group (`test_extract_requirements_parses_or_alternatives_into_one_group`), rubric purity (above). Editor: `test_editor_soft_opportunity_accept_keeps_flat_round_that_added_the_alternative` (a flat-score round that dropped the opportunity count is ACCEPTED, not reverted, with the soft-path detail) + negative control `test_editor_flat_round_still_reverts_when_no_opportunity_reduced` (identical flat round with no opportunity drop still reverts — proves the hard gate is intact). The 2 real-gemma integration calibration tests updated to the 3-tuple (hard-only assertions).
   - **Validation.** studio `pytest tests -q` → **597 passed, 4 deselected** (was 590/4 — +7: +5 compliance unit, +2 editor tie-breaker; 0 regressions; the existing entry-167 unit tests were updated in place to the new shapes, not net-new). NOT committed (tree left uncommitted for review).
   - **Files:** `studio/requirement_compliance.py` (extraction→groups, verifier→3-tuple, `_normalize_groups`/`_hard_issue_str`/`_opportunity_str`/`_ALT_SEP_RE` added), `studio/runner.py` (`__init__` `_epoch_quality_opportunities`; `_editor_opportunity_prompt` + `_editor_drive_round` optional turn; `_run_editor_pass` new params + fresh-per-round `cur_opp_count` + `_opp_accept` tie-breaker + soft-accept emit; `_make_opportunity_recount`; `_postrun_score_and_record` 3-tuple unpack + editor-call threading), `tests/test_requirement_compliance.py`, `tests/test_runner.py`. `studio/rubric.py` intentionally UNCHANGED.
   - **Net product effect.** Hard compliance stays honest (an OR met by code is still SATISFIED, still penalty 0.0 — entry 167's correct call preserved). The NEW behaviour: when the artifact omits an OR alternative the user could have wanted (a diagram), the editor now gets an explicit non-blocking nudge to add it AND — crucially — an editor round that DOES add it survives the gate even at a flat rubric score. That is the actionable improvement over "solved because the OR is technically satisfied" without any keyword-specific hack.

169. Codex second review-iteration on entry 168's tie-breaker — 2 correctness bugs fixed (design unchanged, 2026-07-03). Entry 168's `quality_opportunities` architecture is CORRECT and untouched; an independent Codex adversarial pass found 2 real bugs in the *implementation* of the soft-accept tie-breaker, both now fixed with real regression tests (proven to fail against the old code):
   - **Bug 1 — fail-open recount fabricated a false "success" (`studio/requirement_compliance.py`, `studio/runner.py::_make_opportunity_recount`).** `_make_opportunity_recount` returned `len(_opps)` from `requirement_compliance_issues()`, which **fail-opens to `(0.0, [], [])` on ANY error** (client down, LLM error, unparseable reply). A silently-FAILED re-check therefore returned `len([]) == 0`, and the tie-breaker read "0 opportunities remaining" as "the opportunity was fulfilled" → soft-accepted a round nothing verified. **Fix:** added a `strict: bool = False` kwarg + `ComplianceCheckUnavailable` exception to `requirement_compliance_issues` — in strict mode the 3 fail-open paths (no client/reqs/empty artifact `L221`, verifier LLM error `L234`, unparseable reply `L241`) RAISE instead of returning the ambiguous empty tuple. Default callers (rubric penalty path in `_postrun_score_and_record`, the 4 existing fail-open unit tests) are unchanged. `_make_opportunity_recount` now calls `strict=True`, catches, and returns `int | None` — `None` = UNKNOWN, never a spurious 0. (It also no longer fail-opens to `len(self._epoch_quality_opportunities)`, which could itself have masked a real reduction.)
   - **Bug 2 — weakness non-regression compared COUNTS, not identity (`studio/runner.py::_run_editor_pass`).** The soft-accept gate used `len(new_issues) <= len(cur_issues)` — a pure count. A round could REMOVE one weakness and INTRODUCE a different one (net-same count), reduce an opportunity, and be accepted — admitting a net-new distinct weakness the original hard gate would have rejected. **Fix:** the check is now an IDENTITY comparison on **normalized** weaknesses — `{_norm_weakness(w) for w in new_issues} - {_norm_weakness(w) for w in cur_issues}` must be EMPTY (no net-new distinct weakness). Reuses the canonical `_norm_weakness` from `studio/task_runs.py` (same normalization `findings.py` already uses for weakness-set identity — no new comparison invented). Strictly stronger than the old count-only check; the happy path (genuine opportunity reduction with the SAME weaknesses) still accepts.
   - **Tie-breaker rewrite.** `cur_opp_count` is now `int | None` (a `None` baseline recount → gate off). Cheap gates (`_opp_gate`: hard-regressed + active + `cur_opp_count is not None and > 0` + flat-score + `_no_new_weakness`) are evaluated FIRST so the extra candidate-recount LLM call only fires when a soft accept is otherwise plausible; `_opp_accept = _opp_gate and _new_opp_count is not None and _new_opp_count < cur_opp_count`. A `None` at either the baseline or candidate recount can never open the soft path.
   - **Tests (`tests/test_requirement_compliance.py` +1, `tests/test_runner.py` +3).** `test_strict_mode_raises_instead_of_fail_open` (all 3 fail-open paths raise under `strict=True`; default still fail-opens). `test_recount_returns_none_when_compliance_check_fail_opens` (Bug 1 wrapper: an unparseable-reply client → recount returns `None`, not `0`). `test_editor_soft_accept_does_not_fire_when_recount_fail_opens` (Bug 1 e2e: recount `None` → flat round REVERTS, no soft-accept). `test_editor_soft_accept_rejects_weakness_swap_at_equal_count` (Bug 2: `["w1"]`→`["w2"]` swap, opportunity 1→0, flat score → REVERTED despite the opportunity drop). Discrimination proven directly: for both scenarios the OLD gate computes `soft-accept=True` (buggy) and the NEW gate `False`; the existing positive path (`test_editor_soft_opportunity_accept_keeps_flat_round_that_added_the_alternative`, same weaknesses `["w1"]`→`["w1"]`) stays `True`.
   - **Validation.** studio `pytest tests -q` → **601 passed, 4 deselected** (was 597/4 — +4 net-new: +1 compliance strict, +3 runner tie-breaker; 0 regressions). NOT committed (tree left uncommitted for review).
   - **Files:** `studio/requirement_compliance.py` (`ComplianceCheckUnavailable` + `strict` kwarg on `requirement_compliance_issues`), `studio/runner.py` (`_make_opportunity_recount` → `int | None` via `strict=True`; `_run_editor_pass` `cur_opp_count` → `int | None`, `_no_new_weakness` identity check, `_opp_gate`/`_new_opp_count` restructure + docstring), `tests/test_requirement_compliance.py`, `tests/test_runner.py`. Entry 168's OR-group parsing / hard-issue / rubric-purity logic and all entry-165/167 fixes intentionally UNCHANGED.

170. Codex THIRD review-iteration on entry 169's strict-mode fix — one finer-grained fail-open variant closed (same design, 2026-07-03). Entry 169 made `strict=True` RAISE on the 3 WHOLE-response fail-open paths, but a 3rd independent Codex adversarial pass found a subtler variant of the same bug class survived: a PARTIAL parse (the verifier responds but omits/garbles the per-branch verdict line for SOME branch) was NOT caught, so a genuinely-unverified OR-sibling could still fabricate a false `0` opportunity recount. This entry extends the strict-mode raise to cover that partial case at the same granularity — no design change, just a finer boundary on the SAME fail-open edge entry 169 hardened.
   - **The gap (`studio/requirement_compliance.py`).** In `requirement_compliance_issues`, `flat` is the list of ALL branches asked (`len(flat)` = expected branch-verdict count); `verdicts` is the dict of successfully-parsed in-range verdicts. Entry 169's strict raise only fired when `not verdicts` (ZERO parseable verdicts). But the per-group aggregation loop silently SKIPS any group with no parsed branch verdict (`if not bv: continue`) and only counts PARSED branch verdicts. So for an OR group `["code" || "architecture"]` where the verifier returns `REQUIREMENT 1: SATISFIED` and DROPS branch 2, the group is judged satisfied by branch 1, branch 2 is never counted as an opportunity, and `quality_opportunities == []` → the recount returns `len([]) == 0`, a fabricated "0 opportunities remaining" success even though branch 2 was never actually verified. Pre-fix, the strict call returned the SAME non-raising `(0.0, [], [])` as the default path (pinned as the control in the new unit test).
   - **The fix (strict-mode-only, 1 added guard).** Immediately after the existing `if not verdicts:` block, added: `if strict and len(verdicts) < len(flat): raise ComplianceCheckUnavailable(...)`. This detects when FEWER branch verdicts were parsed than branches asked (a partial parse) and raises at the SAME granularity/location as the other whole-response cases. **Default (`strict=False`) behavior is byte-for-byte UNCHANGED** — the per-group fail-open skip (`if not bv: continue`) still stands, so the production rubric-penalty scoring path at `_postrun_score_and_record` keeps its best-effort semantics exactly per entry 169's design. Only the editor recount (the sole `strict=True` caller) sees the new raise, which `_make_opportunity_recount` already catches → `None` (UNKNOWN), closing the exploit path.
   - **Tests (`tests/test_requirement_compliance.py` +1, `tests/test_runner.py` +1).** `test_strict_mode_raises_on_partial_parse_of_or_branches` — one OR group with 2 branches, verifier answers branch 1 only; the DEFAULT call returns `(0.0, [], [])` WITHOUT raising (control: proves the fabricated-zero the exploit rides on, and that the pre-fix strict call would have returned the same non-raising result — i.e. the gap the test exercises), while `strict=True` RAISES `ComplianceCheckUnavailable`. `test_editor_soft_accept_does_not_fire_on_partial_parse_recount` — e2e: `_make_opportunity_recount` wrapping a partial-parse client returns `None` (not `0`), and driven through `_run_editor_pass` a flat-score round STILL reverts (no soft-accept), proving the exploit path Codex described is closed end-to-end. Distinct from entry 169's `..._when_recount_fail_opens` (a whole-response failure): here the model answered, just incompletely.
   - **Validation.** studio `pytest tests -q` → **603 passed, 4 deselected** (was 601/4 — +2 net-new: +1 compliance partial-parse, +1 runner e2e; 0 regressions; entry 169's 4 strict/tie-breaker tests still green). NOT committed (tree left uncommitted for review).
   - **Files:** `studio/requirement_compliance.py` (one strict-mode `len(verdicts) < len(flat)` guard), `tests/test_requirement_compliance.py`, `tests/test_runner.py`. Everything else — entry 169's whole-response strict raises, the tie-breaker restructure, entry 168's OR-group/opportunity architecture, all entry-165/167 fixes — intentionally UNCHANGED.

171. PER-PHASE requirement compliance wiring — requirements fulfilled DURING generation, not just verified at epoch end (additive to entries 167-170, 2026-07-03). User requirement, verbatim: *"we need identify requirement as fully as possible in 1st phase and tell agent to fufill, all following phase should verify and correct missing elements"*. Entries 167-170 verify compliance ONCE PER EPOCH at the very end (`_postrun_score_and_record`), so a stated requirement only got a repair shot on the NEXT epoch (or the same-epoch editor backstop). This entry gives requirements a much earlier, stronger shot: a proactive heads-up in the FIRST phase of every epoch and a verify-and-correct clause in every phase AFTER the first — both injected only into the goal-aware REDUCER prompt. The entry 167-170 epoch-end mechanism is UNCHANGED and remains the final backstop.
   - **Respects the goal-blind spoke-worker invariant.** Fan-out spoke workers stay goal-blind (they receive a section assignment + evidence tools, never the task/requirement text — see `runner.py` "goal-blind" comments). Every existing repair-clause (lint/relevance/cross-task) is injected into the REDUCER, and this new wiring follows the SAME rule: the requirement list/clause is threaded ONLY into the section-aware reducer prompt (`studio/findings.py::_make_section_reducer`, new `requirement_clause` param), never into `_build_executor_prompt`/spoke prompts.
   - **Extraction pulled forward to run-start (cold-start fix).** `extract_requirements` was cached lazily at epoch END, so on epoch-1 cold start `self._task_requirements` was still `None` while the phases ran. Added a run-scoped pre-extraction in `_run_inner` just before `_run_phase_loop` (guarded `if self._task_requirements is None`, uses the SAME `_original_requirement` as the epoch-end path so both share one cache; fail-open). Epoch 2+ reuses the cache — still exactly one extraction call per run.
   - **Phase 1 (proactive).** New module fn `_phase1_requirement_notice(requirements)`: renders the full cached requirement-group list (single-branch = mandatory; multi-branch = `X (or alternatively: Y)`) as a "STATED TASK REQUIREMENTS — address each … starting now" block. NOT a repair clause (nothing generated yet); empty string when the task states no explicit checkable requirement. No LLM call.
   - **Phases 2..N (verify-and-correct).** New module fn `_per_phase_compliance_repair_clause(client, requirements, partial_artifact)`: runs `requirement_compliance_issues(strict=False)` against the partial artifact assembled from the sections produced SO FAR (`assemble_artifact_from_sections`, falling back to the on-disk `_cur_art` snapshot), and injects BOTH still-outstanding `hard_issues` AND `quality_opportunities` (user wants both pursued early) as a "STATED REQUIREMENTS NOT YET ADDRESSED" block. An already-satisfied group is NOT re-mentioned. Fail-open: any verifier failure returns `""` and the phase proceeds with no clause. One extra reducer-side verification call per phase 2..N (≈7 for an 8-phase task) — acceptable on the local oMLX backend (no per-call API cost).
   - **Wiring (`runner.py::_run_phase_loop`).** The phase loop now `enumerate`s its steps; inside the existing `_artifact_copied` reducer-build block it computes `_requirement_clause` (phase-1 notice for `_phase_idx == 0`, else the per-phase verify clause) and passes it to `_make_section_reducer(..., requirement_clause=…)`. Cold-start research runs reach this because the §14.1 skeleton bootstrap sets `_artifact_copied = True` when a section template exists, so the section reducer fires every phase on both cold-start and hill-climb.
   - **Tests (`tests/test_runner.py` +4).** `test_phase1_requirement_notice_lists_all_groups_or_empty` (renders single + OR groups; empty on none); `test_phase1_notice_reaches_reducer_prompt` (phase-1 reducer prompt build contains the full list); `test_per_phase_clause_lists_only_still_outstanding` (mocked verifier marks one group satisfied → that group is NOT re-mentioned, only the genuine miss + unmet OR-sibling surface; all-satisfied → empty); `test_per_phase_clause_fails_open_on_verifier_error` (verifier raises → `""`, phase proceeds, nothing injected); `test_cold_start_phase1_gets_proactive_requirement_notice` (full cold-start run, no seed, template bootstrap → extractor runs before the phase loop AND the proactive notice reaches a reducer prompt). Two pre-existing tool-loop tests (`test_tool_loop_emits_tool_events`, `test_gemma_profile_limits_searches_in_runner_tool_loop`) used naive call-counting fake clients; taught them to answer the extractor prompt out-of-band (`"NONE"`) so the new run-start extraction call doesn't consume a scripted tool-call slot — same accommodation the entry-167+ integration tests already make for relevance/seed prompts.
   - **Validation.** studio `pytest tests -q` → **608 passed, 4 deselected** (was 603/4 — +5 net-new; 0 regressions; entries 167-170's compliance/opportunity/tie-breaker tests still green). NOT committed (tree left uncommitted for review).
   - **Files:** `studio/runner.py` (pre-extraction, `enumerate` loop, `_requirement_clause` build + pass-through, two new module fns), `studio/findings.py` (`_make_section_reducer` `requirement_clause` param + prompt injection), `tests/test_runner.py`. UNCHANGED: entries 167-170's epoch-end verification/scoring/editor-opportunity/tie-breaker logic, the seed-relevance gate, per-section relevance, heading/mdformat/`_TITLE_STOP_RE`/entry-165 fixes.

172. STRUCTURAL editor opportunity turn — the editor now genuinely ATTEMPTS diagrams/tables/code the reducer structurally can't produce (prompt-guidance only, real gemma calibration, 2026-07-03). User direction, verbatim: *"extend editor to handle structural misses too"*. **The gap:** the section-aware REDUCER (`studio/findings.py::_make_section_reducer`) is structurally forbidden from ever producing diagram/table/code content — its patch contract requires every patch to be "one short paragraph or sentence" that "MUST include at least one http(s) source URL" and "MUST NOT contain '#'". A mermaid diagram / summary table / code example is NEITHER a URL-bearing citation sentence NOR carries its own source URL, so NO requirement notice reaching the reducer (entry 171's per-phase wiring) can close this shape-of-content gap. The EDITOR phase (entry 168's `quality_opportunities`) is NOT bound by that contract — it has real file tools (`read_artifact`/`patch_artifact`/`edit_file`/`glob`/`search_evidence`, verified allowlist at `runner.py::_run_editor_pass`) and CAN write a mermaid block — but its opportunity-turn prompt (`_editor_opportunity_prompt`) was deliberately soft ("already satisfied by another branch, NOT required, add ONLY if cheap, else change NOTHING"), which the deployed model read as permission to do nothing.
   - **Fix (prompt-guidance + generic detection only).** `_editor_opportunity_prompt` now PARTITIONS the opportunity list by content SHAPE via a new module-level `_STRUCTURAL_OPP_RE` (generic structural-content vocabulary: diagram/visual/chart/flowchart/graph/mermaid/table/matrix/schematic/figure/illustration/pseudocode/code-example — word-boundaried so "paragraph" does NOT trip "graph"). **Task-neutral guardrail respected:** the regex CLASSIFIES the kind of content-gap the compliance checker already generically flagged; it injects no domain knowledge, hardcodes no task, has no "if opportunity contains 'diagram' do X for Pi/Craft" branch. Structural opportunities get GENUINE-ATTEMPT guidance — "NOT decorative, worth a genuine attempt; FIRST `read_artifact` the relevant sections and build it from the document's OWN existing research; never invent facts the artifact doesn't support; THEN add the real block (```mermaid / markdown table / fenced code) via one `patch_artifact`/`edit_file`; only if the existing content genuinely can't support it, change NOTHING". Plain/decorative opportunities KEEP the original soft "add only if cheap and grounded, else change NOTHING" qualifier verbatim.
   - **Safety mechanisms UNCHANGED.** This is prompt guidance ONLY — the entry 168-170 tie-breaker gate (revert-on-regression, strict-mode recount, identity-based net-new-weakness comparison, partial-parse detection) is byte-for-byte untouched, as is the reducer's URL/prose patch contract (a deliberate anti-hallucination guard — NOT loosened), the seed-relevance gate, and entry 171's per-phase wiring.
   - **Downstream lint/repair covers editor-added content (confirmed by reading, no new validation built).** `_editor_scored_issues` (`runner.py:733`) calls `lint_artifact(text)` on the FULL assembled artifact, source-agnostic — so a malformed mermaid the editor adds produces a lint weakness → the round REGRESSES (`len(new_issues) >= len(cur_issues)`) → the existing revert gate throws it away. A malformed diagram therefore can never ship; only a clean, lint-passing diagram survives (and is KEPT via entry 168's soft-accept path when it reduces the outstanding opportunity count without a net-new weakness). Multi-epoch runs additionally re-repair the editor-modified seed via the next epoch's deterministic `_repair_lints` (`runner.py:3181`). No new mermaid validation was built; the existing `artifact_lint.py` + `_repair_lints` machinery already covers it.
   - **REAL calibration on the deployed gemma model (`gemma-4-26B-A4B-it-heretic-4bit` via oMLX :8000).** Drove the real editor `ToolAugmentedClient` (real tools, real temp workspace, a realistic plan→act→observe→reflect agent-loops artifact, a genuine "add an architecture diagram" `quality_opportunity`) with OLD vs NEW wording and inspected whether the artifact gained a structural block + whether a mutating tool call fired. Results (honest counts): **temp 0.0 (deployment-faithful, deterministic)** — OLD `structural 0/1, mutated 0/1` (model read + searched, then changed NOTHING); NEW `structural 1/1 (mermaid), mutated 1/1` (read sections → wrote a real mermaid via patch_artifact). **temp 0.8 (robustness, 3 samples)** — OLD `structural 0/3` (1/3 mutated but produced no diagram); NEW `structural 2/3 mermaid, mutated 3/3`. The produced diagram was well-formed AND grounded in the artifact's own content (`Start → Plan → Act → Observe → Reflect --Goal Met--> End / --Goal Not Met--> Plan`) and `lint_artifact` returned CLEAN on the full artifact — i.e. it survives the revert gate rather than being discarded. **Honest limitation:** NEW is a genuine, repeatable improvement but NOT 100% reliable — at temp 0.8 one of three attempts mutated without producing a detector-recognized structural block. The wording reliably flips the model from "do nothing" to "attempt a grounded diagram"; it does not guarantee one every single sample.
   - **Tests (`tests/test_runner.py` +3, pure string construction, no LLM).** `test_editor_opportunity_prompt_encourages_attempt_for_structural_gaps` (7 structural phrasings → "STRUCTURAL CONTENT" + "genuine attempt" + "read_artifact" present, bullet NOT under "OPTIONAL POLISH"); `test_editor_opportunity_prompt_keeps_soft_qualifier_for_plain_gaps` (3 plain phrasings incl. "paragraph" → "OPTIONAL POLISH" + "ONLY if" retained, no "STRUCTURAL CONTENT"); `test_editor_opportunity_prompt_partitions_mixed_opportunities` (mixed batch → both headers, each bullet under the header matching its shape). All entry 168-170 tie-breaker tests still pass unchanged.
   - **Validation.** studio `pytest tests -q` → **611 passed, 4 deselected** (was 608/4 — +3 net-new; 0 regressions). NOT committed (tree left uncommitted for review).
   - **Files:** `studio/runner.py` (`_STRUCTURAL_OPP_RE` constant + rewritten `_editor_opportunity_prompt`), `tests/test_runner.py` (+3 tests). UNCHANGED: reducer patch contract, entries 167-171 verification/scoring/tie-breaker/per-phase wiring, `artifact_lint.py`, `_repair_lints`.

173. URL-neutralization orphaned-bracket fix — wrapped citations no longer leave garbled markdown (real live evidence `s_7da9e7c683ac`, fixed + regression-tested, 2026-07-03). **Real live evidence:** the served artifact at `tmp/studio-workspaces/s_7da9e7c683ac/artifact.md` had invalid markdown in three places — line 160 `...thin client ([(unverified))).`, line 179 `...OpenClaw. [(unverified))`, line 187 `- [(unverified))`. **Root cause:** `studio/task_runs.py::neutralize_unverified_urls` neutralized citations with `_URL_RE = re.compile(r"https?://\S+")` and an `_sub` that swapped ONLY the bare-URL substring (stripping trailing `.,)"'>` into a re-appended `trail`). When the reducer emitted a citation as a markdown link whose anchor text was itself a URL (`[https://a](https://b)`) — optionally wrapped in citation parens `([...](...))` — the greedy `\S+` matched `https://a](https://b))` as ONE token and replaced only that, leaving the leading `[` / `([` and any surviving `)` orphaned → `[(unverified))`, `([(unverified)))`, and (for a bare `(url)`) `((unverified))`. **Fix:** replaced the bare-URL-only regex with an ordered alternation `_CITATION_RE` — `([text](url))` | `[text](url)` | `(url)` | bare `https?://\S+` (inner URLs use `[^)\s]+` so the closing wrapper isn't swallowed) — and rewrote `_sub` to collapse the WHOLE construct: an unverified markdown link becomes `LABEL (unverified)` for genuine descriptive anchor text or just `(unverified)` when the anchor is empty or is itself a URL (both real cases); an unverified bare `(url)` becomes `(unverified)` (never `((unverified))`); a verified link/paren is returned untouched (brackets preserved); the bare-inline-URL branch keeps the original `trail`-preserving behavior byte-for-byte. Fail-open on empty/None verified set is unchanged. **Tests (`tests/test_url_guard.py` +6, reconstructed from the real garbled lines):** paren-wrapped link (line 160) → `(unverified).`; plain URL-label link (line 179) → `(unverified)`; list-item link (line 187) → `- (unverified)`; meaningful-label link → `LABEL (unverified)`; bare `(url)` → `(unverified).` (no double paren); verified wrapped link + paren → untouched. All pre-existing `neutralize_unverified_urls` tests (`test_url_guard.py`, `test_codex_findings.py`) pass UNMODIFIED (bare-URL trailing-punctuation + fail-open behavior proven unregressed). **Validation.** studio `pytest tests -q` → **617 passed, 4 deselected** (was 611/4 at entry 172 — +6 net-new; 0 regressions). NOT committed (tree left uncommitted for review). **Files:** `studio/task_runs.py` (`_CITATION_RE` + rewritten `neutralize_unverified_urls._sub`), `tests/test_url_guard.py` (+6 tests). UNCHANGED: everything in entries 165-172 (reducer patch contract, seed-relevance gate, per-section relevance, heading standardization, mdformat, `_TITLE_STOP_RE`, compliance/opportunity/per-phase/structural-editor logic).

174. Bounded structural-opportunity retry — the Codex-reviewed "best tradeoff" recommendation from `HANDOFF-requirement-compliance-diagram-reliability.md`, built, tested, adversarially reviewed, and real-gemma-checked (2026-07-03). **What:** entry 172's structural-editor prompt alone measured ~2/3 reliable (temp 0.8, 3 samples). `_editor_structural_retry` (new, `studio/runner.py`) gives structural opportunities (diagram/table/code — same `_STRUCTURAL_OPP_RE` classifier) up to 3 candidate attempts, each starting fresh from the SAME pre-retry snapshot (a failed attempt is restored, never built upon); keeps the FIRST candidate that does not regress score/weakness/lint (reuses `_editor_scored_issues`) AND strictly reduces the outstanding opportunity count (reuses the caller's `opportunity_recount`); restores if every attempt fails. `_run_editor_pass`'s round loop now splits `quality_opportunities` into `_structural_opps`/`_plain_opps` before `_editor_drive_round` (only plain ones go through the existing single-shot soft-accept turn) and calls the retry once per round, after the round's own accept/revert resolves, when structural opportunities are configured. No reducer-contract change, no new detector — reuses entries 167-172's existing oracles end to end.
    - **Codex adversarial review (real `codex exec` consult, ~169K tokens).** 4/5 questions PASS outright (snapshot/restore correctness, round-loop invariant consistency, cost/DoS bounds, accept/reject comparison operators). One real finding: `opportunity_recount` was called with no exception guard in both the new retry AND the pre-existing entry-168 round-level soft-accept gate (`cur_opp_count`/`_new_opp_count`) — a raising custom callback after a candidate mutation would propagate past the restore call, leaving a mutated workspace with no rollback. **Fixed:** new module-level `_safe_recount(fn, text)` wraps every `opportunity_recount` call site (all three) in a fail-open try/except, matching the file's existing fail-open philosophy; production's `_make_opportunity_recount` already fail-opened internally so this only closes the gap for a raising non-factory callback. **Test:** `test_editor_structural_retry_survives_raising_recount_after_mutation` — a recount that succeeds on the baseline call but raises on the post-candidate call must reject-and-restore, not crash or leak the mutation.
    - **Real gemma calibration (oMLX `:8000`, `gemma-4-26B-A4B-it-heretic-4bit`, direct `_editor_structural_retry` harness, no mocking of the LLM).** Compared `max_attempts=1` (== entry 172's single-shot baseline) against `max_attempts=3` (the new retry) on the SAME realistic plan-act-observe-reflect artifact and the SAME "add an architecture diagram" opportunity, at temp 0.0 (1 trial) and temp 0.8 (3 trials). **Honest result: 8/8 on BOTH conditions** — every trial produced a clean, well-formed, grounded mermaid diagram (correct 4-stage Plan→Act→Observe→Reflect flow matching the artifact's own prose) on attempt 1, so attempts 2-3 were never exercised. This is a ceiling effect from this particular artifact/opportunity being easier for gemma than whatever scenario entry 172's (now-gone, scratchpad-only) harness used — it does NOT reproduce entry 172's measured 2/3 baseline, so it cannot empirically confirm the retry's incremental reliability lift. Declined to keep iterating the eval to manufacture a failing scenario (that would be gaming the eval, not calibrating it). **What this calibration DOES confirm:** the retry mechanism is correct, safe, and produces genuinely grounded (non-fabricated) structural content when it fires — not a regression, not a false accept, not a workspace-corruption risk (post Codex fix). The specific "2/3 → ~89%/~96%" math in the design doc remains a theoretical projection, not an empirically reproduced result this session.
    - **Validation.** studio `pytest tests -q` → **622 passed, 4 deselected** (was 617/4 at entry 173 — +5 net-new; 0 regressions).  NOT committed (tree left uncommitted for review).
    - **Files:** `studio/runner.py` (`_EDITOR_STRUCTURAL_RETRY_ATTEMPTS` constant, `_structural_opportunity_block` factored out of `_editor_opportunity_prompt`, `_editor_structural_retry_prompt`, `_editor_structural_retry`, `_safe_recount`, `_run_editor_pass` round-loop wiring), `tests/test_runner.py` (+5 tests). UNCHANGED: reducer patch contract, entries 167-172 verification/scoring/per-phase/structural-prompt logic, entry 173's URL-neutralization fix.

175. PROPOSED, NOT BUILT — orchestrator-assigned structural content (supersedes the editor-retrofit approach if it works). User-proposed design correction, 2026-07-03: the goal-blind invariant (entry 171) applies to spoke WORKERS, not the phase-loop ORCHESTRATOR — the orchestrator already sees `_original_requirement`/`_task_requirements` (it's what builds each worker's section assignment in the first place, confirmed via `test_loop_workers_receive_section_assignments_and_weakness_guidance`). So routing a per-section structural hint ("this section: also draft a diagram if your evidence supports it") to the right worker is a natural extension of an assignment payload the orchestrator already constructs, NOT an invariant-breaking wire. If this works reliably, it directly SUPERSEDES today's entries 172+174 post-hoc editor-retrofit mechanism (a diagram drafted as part of original section research, in full context, should be more naturally grounded than one retrofitted cold at the very end) rather than complementing it.
    **Codex design consult (2026-07-03, `.omc/artifacts/ask/codex-design-review-of-worklog-entry-175-*.md`) — REFINES the design below, read this first.** Recommendation: a **hybrid sidecar**, not a reducer structural-passthrough contract, and NOT a replacement for entry 174. Shape: (1) orchestrator threads the structural hint to the selected section worker (as originally proposed below); (2) the worker emits a machine-readable `STRUCTURAL_CANDIDATE` sidecar — section anchor, kind, fenced block, supporting URLs/finding ids — ALONGSIDE its normal draft, never patched into the artifact by the reducer; (3) the reducer collects and validates candidates but does NOT write them in; (4) validated candidates feed into the ALREADY-BUILT, ALREADY-TESTED `_editor_structural_retry` (entry 174), which already has snapshot/restore, lint/scoring, opportunity recount, and `patch_artifact` access. **Why this beats a reducer passthrough:** `_make_section_reducer`'s prose/URL constraint is enforced by `_sanitize_llm_patches` (drops non-URL/heading/oversized/duplicate content) — carving a structural exception into that reopens exactly the class of risk entry 162 fixed. The sidecar gets the SAME benefit (content drafted in original section context, not retrofitted cold) with ZERO reducer-contract changes — the actual write still goes through today's reviewed mechanism, just fed warmer/better-grounded candidates instead of nothing. **Biggest flagged risk: grounding, not syntax** — lint proves a mermaid block is well-formed and opportunity-recount proves the gap is "resolved," but neither proves the diagram/table's FACTS are real; mitigation is requiring every candidate to carry explicit evidence URLs/finding ids and rejecting untraceable ones. **Scope verdict: hybrid quality improvement, not a replacement** — keep entry 174 as the writer/backstop; the sidecar only makes its input less cold.
    **The real remaining design work (per Codex's refined shape):**
    1. **Orchestrator → worker hint (small).** At ToC/outline construction time, map `_task_requirements`'s OR-branches (e.g. "design architecture") to the most relevant section title (an LLM call, matching this file's existing task-neutral judgment pattern — NOT a keyword map), and thread that hint into that section's worker assignment alongside the existing section-title + weakness guidance.
    2. **Worker sidecar + reducer collection (not a reducer write-path change).** A hinted worker emits a `STRUCTURAL_CANDIDATE` block (anchor/kind/content/evidence) alongside its normal prose draft; the reducer parses and forwards candidates unchanged — it never gains a new way to WRITE structural content itself.
    3. **Feed candidates into the existing `_editor_structural_retry`**, requiring evidence traceability per Codex's grounding-risk mitigation, instead of the retry always starting from a blank/cold "attempt from scratch" prompt.
    **Why not built tonight:** this is a genuine architecture project (hint-threading + a new sidecar schema + reducer-side collection + wiring into the existing retry), not a small patch, and session cost was already critical when the idea came up. Do NOT start this without first checking whether today's entry-174 LLM-classifier + bounded-retry fix (still mid-validation as of this note) already reaches acceptable reliability — if it does, this becomes a quality/naturalness improvement to schedule deliberately, not an urgent gap to close.

176. Two live-artifact bugs from `HANDOFF-structural-retry-and-diagram-reliability.md`'s open thread, fixed and Codex-reviewed (2026-07-03). Real live evidence: `tmp/studio-workspaces/s_791e2db70e88/artifact.md` (task_hash `39ee3efddbd9`, v9, score 0.7896) still shipped visible `(unverified)` markers and no diagram, despite entries 173/174. Root-caused BOTH directly against the real artifact (not synthetic), plan reviewed by Codex (`codex exec` consult, session `019f25ac-4903-7c80-8371-b1c12eda9342`) before implementation per user instruction.

    **Bug 1 — `(unverified)` ships silently.** `lint_artifact()` returned `0` issues on the live artifact even though it visibly said "(unverified)" three times. Root cause: `artifact_lint.py::_broken_link_issues` only pattern-matched the OLD garbled forms entry 173 healed (`((unverified)`, `](unverified`) — it never learned to flag the CLEAN, entry-173-healed bare `(unverified)` text, so a citation that failed verification never entered `_weaknesses`/the editor's issue list. Two sub-cases: (a) inline prose with a real sentence + unverifiable tail citation, (b) a fully bare reference-list bullet `- (unverified)` with zero other content. Codex recommended (and this implements) BOTH fixes together: (A) `_broken_link_issues` gets a new `_CLEAN_UNVERIFIED_RE` check (negative-lookbehind-excluded from the garbled patterns, so no double-count) for visibility/editor-fixability; (B) a NEW, separate `task_runs.py::strip_orphaned_unverified_bullets` deterministically removes only a list-item line whose ENTIRE content is the placeholder (never touches inline prose with real content) — kept as its OWN function rather than folded into `neutralize_unverified_urls` specifically so entry 173's own pinned tests (which assert healing stops at `"- (unverified)"`) stay unmodified; the new pass runs at the SAME call site in `runner.py`, right after neutralization. New lint message added to `runner.py::_prune_resolved_weaknesses`'s `lint_markers` tuple so it prunes correctly once resolved.
    **Bug 2 — no diagram ever generated.** Directly called `requirement_compliance_issues` with a real gemma client against the real v9 artifact + task requirement: the OR-group `[include example code, include design architecture]` came back fully SATISFIED, zero `quality_opportunities`. Raw verifier evidence: `"REQUIREMENT 2 (design architecture): SATISFIED — the document includes a 'Modular Architecture Overview' TABLE and prose... No diagram present."` — meaning entry 174's `_is_structural_opportunity`/`_editor_structural_retry` never even got a chance to fire, because the opportunity-generation layer upstream never flagged an opportunity in the first place. Confirmed all 6 editor rounds across all 3 epochs of that run REJECTED/reverted (debug log), ruling out an editor-sequencing explanation. **Fix (per Codex's design call — table satisfies table-shaped asks, diagram-shaped phrasing needs a REAL diagram):** `requirement_compliance.py` adds a deterministic, keyword-narrow `_DIAGRAM_SHAPED_RE`/`_MERMAID_BLOCK_RE` gate inside `requirement_compliance_issues`, applied to already-parsed verdicts BEFORE group aggregation (does not touch strict-mode's partial-parse count from entry 170) — a branch whose phrasing is diagram-shaped (architecture/diagram/topology/blueprint/wireframe/flow chart) and was marked SATISFIED gets deterministically downgraded to unsatisfied when no real ` ```mermaid ` block exists anywhere in the artifact. This correctly makes a mandatory diagram-shaped requirement a genuine hard issue, and an OR-sibling (this task's actual shape) a genuine `quality_opportunity` — which is exactly what threads into the already-built, already-tested entry-174 structural retry.
    **Regression risk (per Codex): low, scoped correctly.** Bug 1 is pure text/lint cleanup, does not touch reducer behavior. Bug 2 lives entirely in `requirement_compliance.py`, upstream of `_is_structural_opportunity`/`_editor_structural_retry` (untouched) and the entry-162 reducer prose-only contract (untouched); default fail-open behavior on verifier failures and entry 169/170's strict-mode raise-on-partial-parse are both unaffected (the gate only overrides already-successfully-parsed verdict booleans, never the parsed-count).
    **Tests (test-first, real captured evidence — 15 new, 0 regressions after 1 existing test updated):** `tests/test_url_guard.py` (+6: strip function, bare-bullet cases, inline-prose-untouched, multi-bullet, empty-input), `tests/test_artifact_lint.py` (+3: clean-marker flagged, garbled-form regression-unchanged, clean-report-stays-clean), `tests/test_requirement_compliance.py` (+4: OR-sibling-becomes-opportunity with real captured verifier text, mermaid-present-not-downgraded, mandatory-diagram-becomes-hard-issue, non-diagram-requirement-never-downgraded). One PRE-EXISTING test (`test_runner.py::test_per_phase_clause_lists_only_still_outstanding`) needed its "all satisfied" fixture updated to include a real mermaid block — its old assumption ("verifier says SATISFIED → truly done") is exactly what this entry corrects for diagram-shaped phrasing, so the fixture now matches genuine intent instead of a bare claim.
    **Validation.** `pytest tests -q` → **649 passed, 4 deselected** (was 622/4 at entry 174 — wait, +27 net across 173→176; this session added 649-622=27, all documented above plus the pre-existing suite growth). Additionally re-ran the full fix chain LIVE against the real `s_791e2db70e88` v9 artifact end-to-end (not just unit-scripted): `lint_artifact` now returns the new message, `strip_orphaned_unverified_bullets` removes the bare bullet while leaving the inline-prose case (by design) untouched, and a real gemma call to `requirement_compliance_issues` now returns `quality_opportunities=['...include design architecture...']` with `penalty=0.0`/`hard_issues=[]` unchanged (no scoring regression on the code-satisfied path). NOT committed (tree left uncommitted for review). **Files:** `studio/artifact_lint.py` (`_CLEAN_UNVERIFIED_RE`, `_broken_link_issues` extension), `studio/task_runs.py` (`_ORPHANED_PLACEHOLDER_BULLET_RE`, `strip_orphaned_unverified_bullets`), `studio/runner.py` (`_prune_resolved_weaknesses` marker tuple, neutralize call-site wiring), `studio/requirement_compliance.py` (`_DIAGRAM_SHAPED_RE`, `_MERMAID_BLOCK_RE`, downgrade gate in `requirement_compliance_issues`), `tests/test_url_guard.py`, `tests/test_artifact_lint.py`, `tests/test_requirement_compliance.py`, `tests/test_runner.py`. UNCHANGED: entry 162's reducer prose-only contract, entry 169/170's strict-mode fail-open hardening, entry 174's classifier/retry mechanism, entry 175 (still proposed, not built).

## Current Next Steps

1. **DONE — final validation run completed cleanly, entries 161-167 proven to interoperate.**
   The backend was restarted (to load the fixes landed after the doc note above was written) and
   the same task re-driven. Session `s_d89d406d5fbf` (task_hash `39ee3efddbd9`, auto-improve seeded
   from the clean cold-start `s_7be8460e09e7`) completed the full hill-climb with no crash: v1=0.22
   (cold start) → v2=0.5434 → v3=0.6498, `status: converged`, wall_s≈3325, 2.47M tokens. Editor
   round 2 correctly REJECTED a non-improving change (`gate outcome: reject`, "round 2 regressed:
   score 0.650->0.650") and reverted, proving the revert-on-regression safety mechanism fires on
   real output. Final artifact: title correctly derived (entry 164 confirmed), zero duplicate
   section headings (entry 162 confirmed), all sections exactly once at `##`. No mermaid diagram —
   expected per entry 167: the task's requirement is "example code **or** design architecture" and
   the artifact's code samples honestly satisfy the OR, so a genuinely generic (non-hardcoded)
   compliance checker correctly does not manufacture a demand the task didn't make. User flagged
   this as still unsatisfying and asked for a Codex second opinion — RESOLVED in entry 168
   (`quality_opportunities` lane + editor soft-accept tie-breaker; hard compliance unchanged).
2. Inspect the remaining dirty files before any future commit; do not stage them blindly (43+
   files touched this session — WORKLOG, PLAN, CURRENT, ARCHITECTURE docs plus the entry-157–165
   code/test changes, none committed per explicit instruction).
3. If the final validation run needs a re-launch, restart the backend (oMLX `:8000`,
   SearXNG `:8080`) and confirm `studio.tools.web_toolkit_available()` before driving it — see
   `CLAUDE.md`'s web-search-wiring section. A dead/unreachable LLM backend during a long
   auto-improve run is the likely cause of `s_d89d406d5fbf` stopping (entry 166's failure mode),
   not a new defect in this session's fixes.
4. Entry 166 (run-death-loses-all-progress on sustained LLM backend outage) remains **logged,
   not fixed** — deferred per explicit user choice given session cost. Proposed fix: persist
   partial state to `task_runs.db` with an incomplete/failed status marker before the top-level
   catch in `runner.py` emits the terminal error, so a future run can still seed from it. Not
   built this session; pick up here if run-death continues to lose real research work.
5. Continue Workstream K/L gaps: optional SQLite trace indexing and optional PDF/PNG renderers if
   they become necessary for UI/query/export fidelity.
6. Keep pruning resolved weaknesses at finalization; stale weaknesses should not lower adjusted
   score or seed the next run.

## Notes For Resume

- Do not revert unrelated dirty worktree files.
- Prefer implementing the plan in small testable slices.
- **Everything through entry 165 is landed, tested, and uncommitted** (tree deliberately left
  dirty for review — do not commit without explicit instruction): the coarse whole-document
  seed-relevance gate (`studio/relevance.py::seed_doc_relevance`, entry 161), the section-fold
  heading-standardization fix (`studio/section_workspace.py::_normalize_heading_levels`, entry
  162), post-run markdown beautification (`studio/markdown_format.py::beautify_markdown`, entry
  163), the `_TITLE_STOP_RE` comma-anchoring fix (`studio/artifact_text.py`, entry 164), and all 6
  Codex adversarial-review production-risk fixes (catalog-route auth + stale-embedding-on-replace,
  `agentkit/topology/dynamic.py` contextvars, `task_runs.py` versioned-insert race,
  `tools.py::_ContextFetchCache` per-thread isolation, premature `last_run` publish removal, and
  the distinguishable fail-open warning on URL verification — entry 165). Full backend suite at
  entry 165: **575 passed**, 2 deselected; agentkit lib: **433 passed**.
- **The only known open gap is entry 166** (connection-outage run death loses all progress, no
  partial-state persistence) — logged and deferred, not a regression, not silently dropped.
- The current slice keeps LLM planning as default. Methodology stages from `ref/.../agent_loop.md`
  are retained as seedable/catalog scaffolding, not automatic routing.
- Static generic/profile templates are code defaults. Learned/approved reusable skeletons belong
  in `TemplateStore`/DB after audit metadata and replacement/quarantine logic exists (this is now
  shipped — entries 143-148 — plus entry 165's stale-embedding-on-replace fix and opt-in admin-key
  auth gate on the mutation/export routes).
- Report quality/live-E2E-success is still the open question, not the code paths above: every fix
  in entries 157-165 is unit/regression-tested, but only entry 165's finding-specific
  `test_codex_findings.py` tests and the (as of this note, unresolved) `s_d89d406d5fbf` run
  constitute live-system evidence for entries 161-165 acting together. Resolve next step 1 above
  before treating this session's work as end-to-end proven on a real task.
- Standing guardrail: AgentKit Studio's source code and built-in report-generator mechanisms are generic/task-neutral, but generated reports must be task-specific and evidence-specific. Never add domain-specific production prose, fixed conclusions, model-id report branches, or example-specific templates; implement reusable evidence, prompt, catalog/template, validation, and UI mechanisms instead.

## 177. Citation-gap root cause: cross-thread fetch-cache grounding failure (2026-07-03)

**Symptom**: a real GUI run (task_hash 7f70f62b995b, session s_e05361a42d54, v1,
score 0.2857) produced a report with ZERO URL-backed citations despite workers
genuinely fetching 16 distinct real URLs across 24 `RESEARCH_FINDING` blocks
(arxiv, github, deepwiki, researchgate, etc. — verbatim quotes, real research).
Weakness gaps: Source quality 0/14.7, Citation integrity 0/14.7 (x2 sections),
Evidence synthesis 4.9/14.7 (x2), publish-gate "no source URL", plus the
`evidence/` workspace subdir was never created for the run.

**Ruled out**: the standing hypothesis from memory (`project_agentkit_studio_web_search_dep`
— missing `web_toolkit` wiring) was disproven for this run: toolkit was available
and genuinely used.

**Root cause** (confirmed via `omc ask codex` review,
`.omc/artifacts/ask/codex-read-the-root-cause-analysis-*.md`): `studio/tools.py`'s
`_fetch_cache` (`_ContextFetchCache`) is deliberately `contextvars`-scoped per run
(Finding 4, prevents cross-SESSION grounding leakage). STAR/MAP spokes run via
`ThreadPoolExecutor` (`agentkit/topology/dynamic.py:794`), whose worker threads do
NOT inherit the parent's contextvars context. `_make_section_reducer`'s `reduce()`
closure calls `client.chat(...)` on the parent run-thread — and if THAT call
triggers its own tool activity, the parent's local cache becomes non-empty with
entries unrelated to what the spokes actually fetched. `_parse_findings`'s
grounding gate (`cache_active = bool(_fetch_cache)`, drop if neither
`_url_in_cache` nor quote-in-cache) then drops every real spoke finding, because
none of the spokes' genuinely-fetched URLs are visible in the parent's cache.
Codex validated empirically: empty reducer cache keeps all 24 findings (fail-open
correctly); a parent cache with one unrelated URL drops all 24.

The `evidence/` directory gap is the SAME root cause, not separate:
`_final_evidence_dossier` (`runner.py:436`) returns `""` immediately when it finds
zero URLs in the upstream text (line 448) — before ever reaching
`evidence_dir.mkdir()` (line 479-480). Zero surviving citations → zero URLs in
upstream text → early return → dir never created.

**Fix applied** (codex's recommended lower-risk path — explicitly NOT the
riskier cross-thread cache-merge, which touches `agentkit` core thread-dispatch
semantics for no clearly-additive benefit): `studio/findings.py::_prefetch_cited`
now (a) always attempts prefetch regardless of the reducer's starting cache state
(removed the `if not _fetch_cache: return 0` guard, which assumed empty-cache
always means safe fail-open — true only if the cache STAYS empty for the rest of
the call), (b) extracts cited URLs from both plain `URL:` lines AND JSON-wrapped
`{"RESEARCH_FINDING": {"URL": ...}}` findings via new helper `_cited_urls()` (the
old regex only scanned plain lines, missing oMLX/qwen's JSON-fenced format that
`_parse_findings` itself already parses), (c) raised `_PREFETCH_LIMIT` 8→24 (a
3-phase fan-out routinely cites 8-9 URLs per phase alone).

Updated `tests/test_runner.py::test_prefetch_cited_extracts_dedups_and_caps`
(the old empty-cache-is-no-op assertion no longer holds) and added 2 new tests:
`test_prefetch_cited_runs_even_with_empty_starting_cache`,
`test_prefetch_cited_extracts_urls_from_json_shaped_findings`. Full suite:
655 passed (was 653), 4 deselected.

**Not fixed (deferred, per codex's assessment of low value / out of scope)**:
- The isolated `web_fetch{url:<|"|>...}<tool_call|>` raw-token leak seen in
  `s12.spoke4.out.md` — a narrow `_parse_inline_tool_calls` coverage gap
  affecting ~1 of ~30 tool-calling turns in the test run, unrelated to the
  citation-loss mechanism.
- A regression test asserting intra-run spoke-to-reducer cache visibility under
  an ACTIVE unrelated parent cache (codex's point 4) — the prefetch strengthening
  makes this scenario recoverable without needing the cache-merge machinery the
  test would otherwise exercise; flagged as a follow-up if a future regression
  resurfaces the grounding-drop symptom.

## 178. GUI quick-start flow shipped + entry-166 fixed + entry-177 validated live end-to-end (2026-07-03)

Four pieces of work, all landed and validated this session:

**(a) GUI quick-start flow (DESIGN-gui-quick-start-flow.md items #1/#2/#3/#6/#4) implemented
and live-verified.** Frontend only, in the doc's Codex-approved rollout order; #5 (inline
hill-climb toggle) stays excluded pending its own design pass. Key shape: `BackendPanel` becomes
a `forwardRef` exposing an imperative `connect(): Promise<string>` handle
(`BackendPanelHandle`) so the button and App's lifted `connectIfNeeded()` share ONE `doConnect`
— no state-lift refactor, no drift between the two paths. `App.tsx` tracks `stale` (any
backend-field edit post-connect → button relabels "Reconnect to apply changes"), holds an
in-flight promise ref so paste+Enter can't double-POST `/session`. `ChatPanel.handleSend`
awaits `connectIfNeeded()` UNCONDITIONALLY (the #6→#4 hard dependency: a stale session must
reconnect, not be reused) and uses the RESOLVED session id, not the prop (App's state may not
have flushed). Auto-connect triggers on first non-empty EDIT (never focus); connect failure on
Send = no run, no chat bubble, textarea keeps text, error surfaces via BackendPanel's existing
error span. Default mode `auto`→`llm` in BOTH `App.tsx` and `runStore.ts` initialState
(`?demo=1`'s explicit `"auto"` untouched). Chat textarea gets a 2px `--color-accent-dim`
border distinguishing it from the loop-search input (the real mistargeting bug from live
testing). All 4 shipped items verified through the real browser (fresh loads, network-tab
single-POST check on auto-connect). Live review also caught a real bug in the first cut:
`connectedLabel` derived live from the dropdown falsely showed the NEW profile as connected
during the stale window — fixed by capturing the label at connect-success time
(`setConnectedLabel` in `doConnect`). Frontend build + all 43 vitest tests green.

**(b) Entry 166 FIXED (was logged-deferred): mid-run death now persists partial state.**
Reproduced live twice first (sessions `s_e7f75d38b309`, `s_9ac6ec1fb066` — VibeProxy :8317
died mid-run; plan decomposed, phase 1 made ~6 LLM calls, zero tool calls ever fired, client
retries exhausted → `LLMUnavailable("Connection error.")` → top-level catch → nothing
recorded, exactly entry 166's signature). Fix (Codex-reviewed, all amendments applied —
artifact `.omc/artifacts/ask/codex-review-this-fix-plan-for-a-known-bug-worklog-entry-166-*`):
`task_runs` gains a `status` column (`'completed'` default, PRAGMA-cols migration) +
`TaskRun.status`; `runner.py::_persist_partial_run` (called from the top-level catch before
the ErrorEvent, whole body guarded so persistence can never mask the original error) records
a `status='failed_partial'` row — score 0.0, weaknesses `[]` (death reason goes to
`config={"failure": ...}` so it can NEVER enter the weakness feed-forward), `result_text` =
workspace `artifact.md` — but ONLY when `_artifact_has_real_content` clears a 20-real-word
floor (reuses `artifact_lint._PLACEHOLDER_PATTERNS`, so BOTH the hyphen and em-dash skeleton
spellings are caught; skeleton-only seed is worse than cold start). Lineage detail (Codex's
critical catch): the partial's hash is `task_hash(base_identity(requirement))` — the SAME
base-identity path `_postrun_score_and_record` uses — so the partial row lands in the lineage
`latest_with_content()` queries instead of forking it. Consumers: `latest_with_content()`
deliberately DOES return `failed_partial` rows (the carry-forward win);
`similar_runs()`/`accumulated_weaknesses()` exact-history branch/`repeat_failures()` all
filter to completed via new `completed_runs()` (failed rows carry no signal); `best()` safe
via score 0.0; `/task-runs/{hash}` API now emits `status` per row. ErrorEvent message gains
"(partial progress saved for carry-forward)" when a partial was recorded. New
`tests/test_partial_persistence.py` + runner/store additions. Suite: **668 passed, 4
deselected** (was 655 — +13, 0 regressions). The failed_partial path is unit-tested but not
yet exercised live (the validation run below completed cleanly).

**(c) Entry 177's citation-grounding fix VALIDATED LIVE end-to-end** — the loop entry 177
left open. Fresh cold-start run through the real browser GUI on the LOCAL oMLX gemma backend
(per user directive: local model, not VibeProxy/haiku), session `s_5196501f97b4`, task_hash
`9fd3126545ad`: final artifact carries real inline citations PLUS a 10-URL References section
(arxiv/NVIDIA/Google-Research/ScienceDirect/Medium — all matching genuinely-fetched sources),
`evidence/` created with `fetched-sources.json` + 9 `source-*.md` dossier files, full
`io/` spoke/reducer transcripts across 10 phases, `task_runs.db` row v1 score 1.0 status
`completed`, zero `(unverified)` markers (entry 176's strip holding). Both symptoms from the
original bug report (zero citations despite real fetches; `evidence/` never created) are
gone on a real run.

**(d) `ARCHITECTURE-doc-generation-pipeline.md` re-traced for entries 166/176/177** (643→790
lines): date/line-count header refreshed (runner.py 3642→4352), §1 mermaid gains the
top-level-catch → `_persist_partial_run` branch, §2 notes the seed-reader/completed-only
consumer split, new §5c (mid-run death + partial persistence, with consumer-filtering
matrix), §6 shows `strip_unverified_lines` at both neutralize sites + the diagram-shaped
compliance downgrade, §7 adds read-only-streak forcing, §9 risks 1/8 annotated RESOLVED
(not deleted), §10 index updated. Stale `_run_editor_pass` cites (735/777) corrected to the
real 1133.

**Ops note:** the two dead runs' missing `io/`+`evidence/` subdirs are the entry-166 failure
mode (death before any tool call), not a separate dir-creation bug. VibeProxy was restarted
for diagnosis, but the validated configuration is the local oMLX gemma profile.
