# EXECUTION PLAN — finish content presentation + shallowness (incl. frontend)

**Started:** 2026-07-04. **Branch:** `build-research-report-generator-plan`.
**Directive:** fully finish (1) content presentation — format-style, diagram, table, list —
and (2) shallowness. Both including frontend. Not "MVP" — complete.

Design authority: `PLAN-per-section-presentation.md` (§10 codex corrections), `PLAN-report-quality-analyzer.md`,
`PLAN-generic-component-assignment.md` (§P5 shallowness). Ground truth read 2026-07-04.

## State at start (verified)
- `presentation_classifier.py` — full 5-form ladder + LLM adjudicator + `find_format_errors`. BUILT + tested. **NOT wired.**
- `section_presentation.py` — DIAGRAM only. Wired into `_run_editor_pass` (commits 2117299 + 8f919f4, gate + rollback).
- Table / list generators: **do not exist.** Format-fixer: **does not exist.**
- Frontend: no presentation/format surfacing, no judge-model selector.
- Shallowness: diagnosed only — `findings.py:268` (sentence-cap, PRIMARY), `:333` (density cap), `:327` (cited-URL drop).

## Gate/seam invariants (reuse, never fork)
- Accept gate: score non-regression + no net-new normalized weakness + local improvement realized + rollback on fail/exception (the 8f919f4 pattern).
- Detector = value gate; grounding = fabrication guard only (C3). Never downgrade well-formed structure to prose.
- Adjudicator runs on a CAPABLE model (haiku), generation on gemma — multi-model split. `analyze_report(text, client)` already takes the judge client.
- Presentation pass runs AFTER the round loop, independent of `cur_issues` (M2).

---

## PHASE 1 — backend content generation (table + list + format-fix), generalized pass
Generalize `section_presentation` from diagram-only to the full 5-form + format-defect pass, driven by `presentation_classifier.analyze_report` + `find_format_errors`.
1. **Table generator** — LLM prompt: convert a comparison section's prose to a grounded markdown table (entities × attributes), literal-token grounded to the section. Reject if <2 rows or <2 cols or ungrounded.
2. **List generator** — bulleted/numbered from parallel items / sequence. Prefer deterministic restructure where the items are already enumerable (comma-series / sentence-sequence); LLM only when needed. Numbered iff sequence cues.
3. **Format-fixer** — deterministic repair for `find_format_errors`: close an unclosed fence, drop an empty fence. No LLM.
4. **Generalized `plan_presentation`** — one pass: `analyze_report(text, judge_client)` → per-improvement dispatch to the right generator → insert at `char_offset`/section → same accept gate + rollback. Keep one-change-per-pass ranking (highest-confidence improvement) OR bounded multi (decide: content coverage is a stated goal → allow up to N per pass with C6 baseline-advance). Format-fixes are always-apply (defects, not choices) — separate from the gated form-improvements.
5. Tests: unit per generator (grounded/rejected), integration through `_run_editor_pass` (table lands in comparison section, list in enumerable section, format defect fixed, rollback on mid-sync failure). Full suite green.
6. codex review → fix → commit.

## PHASE 2 — content frontend
- Surface the presentation action list (heading, current→recommended, source det/llm, reason) + format defects in the run UI.
- Judge-model selector (mirrors generation dropdown, source `list_profiles()`); `POST /session` carries `judge_llm`.
- Wire the backend to build a judge client from `judge_profile` (default `haiku`) SEPARATE from the gemma generation client, and pass it to `analyze_report`.
- Visual verify (screenshots), test.

## PHASE 3 — shallowness backend (the depth lever)
- **`expand-underdeveloped-sections` stage (§P5):** consume UNUSED grounded findings into sections below an evidence/word floor. Hard-guarded: only `_parse_findings` survivors, URL retention, ≤1 synthesized paragraph per source-cluster, reject if citation density rises without synthesis density.
- Relax `findings.py:327` cited-URL drop (allow a section to deepen from a re-cited source under guard).
- Make `findings.py:333` density cap floor-aware (don't thin under-developed sections).
- Reconsider `findings.py:268` sentence-cap contract (the PRIMARY cap) — allow bounded multi-sentence synthesis where evidence supports it.
- **Verify with real gemma** on the reference artifact (`s_2b186fac503d` / `s_791e2db70e88`): utilization ↑ from ~6%, no fabrication, citation-density guard holds. Do NOT rush — core reducer.
- Tests + codex → commit.

## PHASE 4 — shallowness frontend
- Surface utilization / depth telemetry (evidence bytes → report bytes, expand-stage actions) in the run UI.
- Any session config the expand stage needs (floor thresholds) exposed.
- Visual verify, test.

## Done = both features complete, wired, frontend live, full suite green, codex-reviewed, live-verified on gemma. Push on user say-so.

## Progress log
- (pending) Phase 1 …
