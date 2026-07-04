# PLAN — report-quality analyzer (per-location presentation + format + depth)

**Date:** 2026-07-04
**Branch:** `build-research-report-generator-plan` (detection core committed `ef66178`)
**Method (user):** for each capability — READ the report/evidence, form MY OWN ground truth first,
then make the code output match it, via GENERIC rules (no hardcoding), verified on a real artifact.

Test artifact: `backend/tmp/studio-workspaces/s_2b186fac503d/artifact.md` (23KB, degraded — repetitive
scaffolding fragments + orphaned analysis in References + an unclosed code fence).

---

## DONE (committed `ef66178`)

`studio/presentation_classifier.py` + `tests/test_presentation_classifier.py` (12 green):
- **Detection ladder** (deterministic, high-recall pre-filter): features (item/component/edge counts,
  sequence/comparison cues) → recommended form; `current_form` from markdown surface; improvement =
  ESCALATION up the structure-rank (never downgrade well-formed structure).
- **Tier-2 LLM adjudicator** (`_adjudicate_prompt`, single-shot 5-way) OVERRIDES the deterministic
  tier's over-triggering. DIAGRAM criterion = connected-structure (components connect to EACH OTHER).
- **`find_format_errors`** — deterministic empty/unclosed-fence lint; caught the real line-265 fence.
- `diagram_render._ground` drops generic-only labels (System/Data/Process, codex C3).

## VERIFIED FINDINGS (the hard-won evidence)

1. **Deterministic tier over-triggers** on entity-rich narrative (`_COMPONENT_RE` counts every
   capitalized word: Exec Summary scored 24 "components" → DIAGRAM false-positive). → pre-filter only.
2. **The weak local gemma CANNOT do 5-way presentation classification** — verified across 5 techniques
   (5-way ×2 prompts, edge-extraction, binary cascade, evidence-grounded gate). It over-affirms
   "structure" on any section naming components (says DIAGRAM to everything; hallucinates 4 edges for a
   pure narrative). **The model is the ceiling, not the prompt.**
3. **A/B (haiku): single-shot 4/6 == multi-round cascade 4/6, single-shot 4x cheaper.** Multi-round
   (user's "one by one" idea) only helps a WEAK model that can't hold 5 options; a capable model doesn't
   need it. → adopt single-shot on a strong model.
3b. **FEW-SHOT is the settled adjudicator (committed `16dd8ac`).** Web research (EMNLP-2025 hierarchical
   classification: top-down cascades error-accumulate; Wei-2022: CoT hurts small models) pointed to
   in-context examples over cascades/CoT. Measured: few-shot lifted **gemma 1→4/6, haiku 4→5/6**, and
   flipped gemma from over-affirming to conservative. Baked into `_adjudicate_prompt` (generic examples,
   no hardcoding). haiku few-shot 5/6 is the ceiling — the one miss (Key Findings) is a soft/borderline case.
4. **Classification accuracy is capped by INPUT QUALITY.** Residual haiku misses are (a) genuinely
   borderline sections (Key Findings straddles — it says "packages that layer on top of each other") and
   (b) report DEFECTS (References→DIAGRAM because it's polluted with orphaned analysis paragraphs). So
   **content-quality/shallowness is the ROOT**, upstream of presentation.

### MY GROUND TRUTH for s_2b186fac503d (from reading, for regression):
Exec Summary=PARAGRAPH · Scope=NUMBERED(ok) · **Background=DIAGRAM** · Key Findings=BULLETED(soft;
DIAGRAM defensible) · Evidence=PARAGRAPH(borderline) · **Implications=BULLETED** · Limitations=needs
finer walk · References=BULLETED(currently polluted). Format: line-265 unclosed fence. Best code result
(haiku single-shot): Background✓ Implications✓ ExecSummary✓ References✓, misses Evidence+KeyFindings.

## REMAINING WORK

### A. Multi-model wiring (user directive) — backend
- Build a JUDGE client from a strong profile (default `haiku`) SEPARATE from the generation client
  (gemma). `presentation_classifier.analyze_report(text, client)` already takes the client — the
  caller supplies a haiku client. Add session config `judge_profile` (default "haiku"); build it in
  `studio/backends.py` / the runner/API alongside the generation client.
- Expose an endpoint (or wire into the editor pass) that runs `analyze_report` + `find_format_errors`
  and returns the action list. Generation stays on gemma; detection on haiku (cost/fidelity tiering).

### B. Frontend (user directive)
- Add a judge-model selector (dropdown, mirrors the generation-model dropdown; source `list_profiles()`).
- Surface the action list (per-location: heading, line, current→recommended, source) + format defects.
- Session `POST /session` body carries `judge_llm` alongside `llm`.

### C. Test + A/B (user directive)
- End-to-end: run analyzer via the wired path, confirm haiku adjudication, compare to MY GROUND TRUTH.
- A/B single-shot vs multi-round is DONE (single-shot won); re-confirm after wiring.

### D. Shallowness diagnosis (user directive — the ROOT) — DIAGNOSED (2026-07-04), fix NOT yet built

**Evidence (measured on s_2b186fac503d):** 9 sources / ~375KB (source-003 = 119KB) → 22.9KB report
= **~6% utilization**. The `io/*.reducer.in/out` pairs localize the collapse: worker extracts ~34KB of
findings → reducer emits **~6 ONE-SENTENCE patches (~3.5KB)**. The run's own weakness log admits it:
"Evidence synthesis 7.4/14.7, Analytical depth 5.3/10.5, multiple redundant/duplicate sections."

**Three confirmed code roots (the depth ceiling is architectural, not a knob):**
1. **`findings.py:268` — reducer contract "content is one short paragraph or SENTENCE, not a markdown
   section."** Forbids multi-sentence synthesis BY DESIGN; depth accretes one sentence/round and
   plateaus. PRIMARY cap.
2. **`findings.py:333` — per-section density cap** ("thins the wall") — anti-citation-wall, over-thins
   real depth.
3. **`findings.py:327` — drops a finding whose URL is ALREADY cited** — a section can't be DEEPENED from
   a source it cited once; each source ≈ 1 sentence/section then locked out.
Plus: additive-only reducer + weak dedup → near-duplicate sentences across rounds (the "redundant
sections" weakness) — wastes the depth budget on repetition.

**MY GROUND TRUTH of target depth:** the 9 sources are genuinely rich (Pi architecture, lifecycle
handlers, context-window internals; GH issues #350/#807; package.json; 2 Substack deep-dives). A
satisfying report carries MULTI-SENTENCE synthesized analysis per section, draws >1 claim per rich
source, and does not repeat itself. Current: ~6% + visible repetition.

**Fix direction (PLAN-generic §P5 / codex, NOT yet implemented):** an **expand-underdeveloped-sections**
stage fed by UNUSED grounded findings — hard-guarded (only `_parse_findings` survivors; URL retention;
expand only sections below an evidence/word floor; ≤1 synthesized paragraph per source-cluster; reject
if citation density rises without synthesis density) — PLUS relax the cited-URL drop (327) and make the
density cap (333) floor-aware for under-developed sections. This is the hardest deferred bug (touches the
core reducer/findings pipeline); implement + real-gemma verify in a FRESH session.

## UNCOMMITTED / PARKED
The earlier diagram-GENERATION MVP (`studio/section_presentation.py` + the `_run_editor_pass` wiring +
`tests/test_section_presentation.py` [8 unit green] + 3 `test_runner.py` integration tests [2 FAIL on a
fake-client quirk, NOT production code — production `plan_one` renders correctly standalone]). This
thread was overtaken by the detection focus; decide whether to finish or drop it.
