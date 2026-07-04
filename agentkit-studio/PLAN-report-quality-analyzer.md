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

### D. Shallowness diagnosis (user directive — the ROOT) — NOT STARTED
- READ the report + its EVIDENCE files (`backend/tmp/studio-workspaces/s_2b186fac503d/evidence/*` or
  the fetch cache), form MY ground truth of what depth/content the report SHOULD have given the
  evidence, then diagnose the code that makes it shallow. Known suspects (from PLAN-generic §P5/Bug C):
  per-target dedup cap 3/section (`dedup.py:92`), cited-URL drop pre-consolidation (`findings.py:327`),
  one-short-paragraph reducer contract (`findings.py:268`), same-URL merge (`dedup.py:77`). Also the
  orphaned/repetitive scaffolding this artifact shows (Exec Summary + Background have near-duplicate
  citation fragments; References has misplaced analysis) — a de-dup/cleanup gap.
- Make the code output a satisfying (deep, clean) result vs MY ground truth.

## UNCOMMITTED / PARKED
The earlier diagram-GENERATION MVP (`studio/section_presentation.py` + the `_run_editor_pass` wiring +
`tests/test_section_presentation.py` [8 unit green] + 3 `test_runner.py` integration tests [2 FAIL on a
fake-client quirk, NOT production code — production `plan_one` renders correctly standalone]). This
thread was overtaken by the detection focus; decide whether to finish or drop it.
