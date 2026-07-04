# PLAN — Follow-up #2: per-section presentation loop (diagram, then table/list)

**Date:** 2026-07-04
**Branch:** `build-research-report-generator-plan` (HEAD `7a7f049` = Follow-up #1 literal-token grounding, committed + 710 green)
**Prereq context:** `HANDOFF-report-quality-fixes.md` (Follow-ups §, §10 unifying design) + `PLAN-generic-component-assignment.md` §10.
**Status:** OFFLINE DESIGN (no code yet). The §7 live prompt-test is MANDATORY before wiring — do NOT skip to implementation.

---

## 0. Objective & success metric

- **Objective:** per H2 section, pick the RIGHT presentation for the content (paragraph / list /
  table / diagram) and generate it — instead of today's ONE whole-doc diagram keyed off the
  requirement-opportunity list.
- **Primary metric (MVP = diagram only):** on the real v9-shaped artifact, a diagram lands in a
  section whose content genuinely warrants one (≥3 named components with real relationships) and
  does NOT land in narrative/meta sections (Executive Summary, Scope). Measured live on gemma.
- **Parity (must NOT regress):** literal-token grounding (#1) unchanged; every revert invariant in
  `_editor_structural_retry` intact; whole-doc rubric/lint non-regression; `pytest tests` 710 green.

## 1. Verified seams (traced 2026-07-04, runner.py)

- **Detection today** (`_editor_structural_retry`, runner.py:1204): `_DIAGRAM_SHAPED_RE.search(" ".join(
  structural_opportunities))` — keyed off the requirement-compliance OPPORTUNITY list, run over the
  WHOLE `scored_text`, produces ONE diagram. `structural_opportunities` are filtered upstream in
  `_run_editor_pass` via `_is_structural_opportunity` (runner.py:821 = `_STRUCTURAL_OPP_RE` regex +
  LLM fallback `_classify_structural_opportunity`).
- **Generation** (runner.py:1205-1223): `diagram_render.build_components_prompt(scored_text)` → bare
  `base_client` → `render_grounded_diagram(reply, scored_text)` [now literal-token, #1] →
  `insert_diagram_block` → `_write_artifact_through_sections`.
- **Accept gate** (`_accept_candidate`, runner.py:1178): keep iff (a) `cand_score >= base_score`, (b)
  no net-new normalized weakness, (c) `opportunity_recount(candidate)` STRICTLY `< base_opp_count`.
  Reused verbatim by BOTH the A2 path and the tool loop.
- **Section model** (`_write_artifact_through_sections`, runner.py:683 → `write_section_workspace`):
  the artifact is split on `## ` H2 headings into section files, then reassembled. A per-section
  block written into the artifact body survives this round-trip (same property #1's diagram relies on).

## 2. The crux — accept gate vs content-shape (decision required)

`_accept_candidate`'s condition (c) requires a strict drop in the requirement-`opportunity_recount`.
A per-section presentation block chosen by CONTENT SHAPE (not by an outstanding requirement) causes
NO recount drop → the current gate REJECTS it. Options:

- **(A) MVP — relax (c) for shape-driven blocks.** For a section-local block with no backing
  requirement opportunity, accept on: score non-regression + no net-new weakness + lint clean +
  block is grounded (literal-token, #1) + non-duplicative (idempotency) + passes the text-first
  floor. Keep (c)'s strict-drop path for blocks that DO satisfy a requirement opportunity.
  - *Pro:* smallest diff, ships the per-section loop now. *Con:* loses the strict monotone signal
    for shape-driven blocks; over-formatting is guarded only by score-non-regression + text-first
    floor + idempotency (must verify these actually hold the line on gemma).
- **(B) Principled — a "presentation-debt" recount.** Count sections whose content-shape warrants a
  block but lacks one; a block that resolves a warranted section drops this count → the SAME strict-
  drop gate works uniformly (mirrors codex §9 `structural_requirement_recount`).
  - *Pro:* one uniform monotone gate, no relaxation. *Con:* heavier; the debt-counter is itself a
    per-section detector that must be deterministic enough to not thrash the gate.

**Recommendation:** ship **(A)** as MVP (guarded), design **(B)** as the follow-on once the live
prompt-test proves the detector is stable. Flag for codex review — this is the highest-risk change.

## 3. Section-iteration algorithm (MVP, diagram only)

1. Split `scored_text` into `(heading, body)` H2 chunks (reuse the `## ` split; helper already implied
   by `write_section_workspace`). Skip H1 title + a leading preamble with no `##`.
2. **Idempotency:** skip a section whose body already contains a ` ```mermaid ` (MVP) — later also
   ` ```` `-table / markdown table (for #3).
3. **Detect** (per section, tiered — §4): deterministic pre-filter → LLM classifier over the SECTION
   body → presentation type. MVP acts only on `DIAGRAM`.
4. **Generate** for a qualifying section: `build_components_prompt(section_body)` (grounding source =
   the SECTION, not whole doc) → `render_grounded_diagram(reply, section_body)` → insert at the TOP
   of THAT section's body (not the global `insert_diagram_block` target-heading scan) → caption.
5. **Gate** the whole candidate artifact through the §2(A) accept path; restore-on-fail (reuse
   `_editor_snapshot`/`_editor_restore`). Idempotent + bounded (one pass over sections; a section is
   attempted at most `_EDITOR_STRUCTURAL_RETRY_ATTEMPTS` times, fresh snapshot each).

## 4. Detection prompt (fix the measured looseness)

Handoff measured the whole-doc detector false-positiving `YES|architecture` on Executive Summary +
Scope and echoing the literal `<architecture|process|dataflow>` placeholder. Per-section detector:
- **Deterministic pre-filter (cheap gate):** ≥3 candidate named components (proper-noun-ish tokens)
  AND ≥1 relational verb/among ("calls", "feeds", "sends to", "depends on", "->"); else stay prose.
- **LLM floor (text-first):** "Would a diagram MATERIALLY beat prose for THIS section — ≥3 distinct
  components with REAL relationships/flow, NOT a narrative or meta summary? Answer exactly one of:
  DIAGRAM / PROSE." No `<placeholder>` tokens in the prompt (they got echoed). Reuse the
  `_classify_structural_opportunity` few-shot pattern (runner.py:782) but over CONTENT.

## 5. Gates (all four, per handoff §10)

text-first threshold (§4) · no-duplication (idempotency §3.2 + don't restate prose already present) ·
highlight-main-findings (place the block in the section it summarizes, captioned) · caption every block.

## 6. Tests

- Unit: section splitter; idempotency skip; detector pre-filter (narrative section → PROSE, arch
  section → DIAGRAM) with a faked classifier; per-section insertion placement; §2(A) accept path
  accepts a grounded non-regressing block with flat opportunity_recount (the case the old gate
  rejected) and rejects a score-regressing one.
- Integration (mirror `test_a2_*`): drive real `_run_editor_pass` on a 2-section artifact (one arch,
  one narrative) → diagram lands in arch section only, survives round-trip, gate keeps it.
- Full `pytest tests` green.

## 7. MANDATORY live prompt-test FIRST (before any wiring)

Per PLAN-generic §10 sequencing + handoff. Services already up (oMLX gemma :8000, SearXNG :8080,
backend :8770). On the real v9 artifact (`backend/tmp/studio-workspaces/s_791e2db70e88/artifact.md`):
1. For EACH H2 section, run the §4 detector on gemma → record DIAGRAM/PROSE. Assert: narrative/meta
   sections (Exec Summary, Scope, Key Findings prose) → PROSE; the architecture/background section →
   DIAGRAM. If the detector still false-positives, tighten §4 BEFORE wiring.
2. For the DIAGRAM section, run `build_components_prompt(section_body)` on gemma → confirm the
   component list is grounded (literal-token) in that section. Only then implement §3.
Scratch harness in `backend/` (delete after, like the #1 `a2_*` scratch — do NOT commit).

## 8. Sequencing

MVP diagram (§3, gate §2(A)) → live-validate a diagram LANDS per-section via the production path →
**#4 list/paragraph reformatting** (user's next pick) → **#3 table path** (same render pattern,
simpler). Coverage/debt-recount (§2(B)) only if (A) over-formats in live testing.

## 9. Reuse constraint (PLAN-generic §7)

Extend existing seams: `_STRUCTURAL_OPP_RE`/`_classify_structural_opportunity`, `diagram_render`,
`_editor_scored_issues`, `_editor_snapshot`/`_editor_restore`, `_write_artifact_through_sections`.
Never fork a parallel scorer/detector/renderer.

---

## 10. CODEX REVIEW (2026-07-04, high-effort) — SOUND-WITH-CORRECTIONS, folded

Codex reviewed §0–§9 against the real code. Verdict SOUND-WITH-CORRECTIONS. These supersede the
relevant parts above; implement to §10, not the original §2/§3.

**C1 (supersedes §2). Accept gate = deterministic LOCAL presentation-debt (option C, not A or B).**
§2(A)'s relax-to-score-non-regression is REJECTED — it removes the only monotone proof; the rubric
won't reliably penalize a useless diagram, lint checks syntax not value, grounding checks noun-
presence not whether the block should exist. Instead: keep the strict-drop gate, but count debt
LOCALLY. Before insertion compute `warrants(section_body) and not _has_mermaid(section_body)`; after,
require that EXACT section's debt goes 1→0. Preserves `_accept_candidate`'s strict-drop semantics with
no new whole-doc compliance recount (this is §2(B) made deterministic + local — the winner). Resolves
C4 too (the renderer's fall-open backstop stays intact).

**C2 (renderer defect — ALSO affects committed #1). `render_grounded_diagram` requires no edge.**
Four grounded nouns with ZERO valid edges still render a "diagram." A diagram's whole value is
RELATIONSHIPS. Fix in `diagram_render.py`: after dropping edges to unknown endpoints, require ≥1 (ideally
≥2) rendered edges; else return None. This is a real defect in `7a7f049`, not just a #2 concern — fix it
as a small standalone hardening before/with #2, with a test (edgeless component list → None).

**C3 (supersedes §4 grounding claim). Per-section grounding is a TRIVIAL-PASS for value.**
`render_grounded_diagram(raw, section_body)` only proves labels share tokens with the SAME section —
gemma echoing section nouns always passes. Literal-token grounding is a fabrication guard, NOT a
"should-this-be-a-diagram" signal. The warrant decision (C1 debt + §4 text-first floor + C2 edge
requirement) is what gates value; do not lean on grounding for it. Also reject generic labels
("System", "Process", "Data", "Component") as non-discriminating.

**C5 (supersedes §3.1/§8). Cap MVP to ONE diagram per editor pass.** Metric is one RIGHT diagram, not
coverage. Rank eligible sections by detector confidence, attempt the single highest with bounded
retries (`_EDITOR_STRUCTURAL_RETRY_ATTEMPTS`), stop after accept. Defer the full multi-section loop.

**C6 (only if/when §3 loops multiple sections). Advance the snapshot baseline after each accept** —
compare each candidate against the CURRENT accepted artifact, not the original pre-pass snapshot, or a
later restore undoes prior accepted work and the gate lies. Moot for the C5 one-diagram MVP; required
before lifting the cap.

**C7 (sharpens §1/§6). Round-trip is `write_section_workspace` + active-outline ordering, not a raw
`##` split.** Preserve exact H2 headings; add NO new heading inside the block path. Add an integration
test with active-outline REORDERING to prove the inserted block survives reassembly.

**C8 (sharpens §5). Caption is a gate → the per-section inserter must OWN it.** `insert_diagram_block`
emits only the fenced block today. Make the per-section path add the caption and test it.

**C9 (sharpens §7). Live prompt-test must record raw reply, parsed components, grounded-kept nodes, and
RENDERED EDGE COUNT per section** — not just DIAGRAM/PROSE. gemma failure modes to expect: false-
positive DIAGRAM on summaries, endpoint-alias COMPONENT lines that drop all edges (→ C2 catches),
generic labels (→ C3), short-acronym fall-open, whole-doc score flatlining on visual clutter.

**Revised build order:** C2 renderer edge-requirement (small, standalone, hardens #1) → §7 live prompt-
test recording C9 fields → C1 local-debt gate + C5 one-diagram wiring + C8 caption → tests (§6 + C7
reorder test) → live-validate → then #4 list/paragraph, #3 table.
