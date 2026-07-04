# HANDOFF — report-quality analyzer: presentation classification + shallowness

**Date:** 2026-07-04
**Branch:** `build-research-report-generator-plan` (single checkout `/Users/yuxinliu/code/agentkit`,
studio backend at `agentkit-studio/backend`). Pushed through **`ccabed3`**.
**Design authority:** `PLAN-report-quality-analyzer.md` (this session's thread) +
`PLAN-per-section-presentation.md` (§10/§11, the diagram-generation MVP thread) +
`PLAN-generic-component-assignment.md` (§P5/Bug C shallowness).

Read `PLAN-report-quality-analyzer.md` first — it has the verified findings, my ground truth, and the
remaining-work list. This doc is "what happened / state / how to resume."

---

## TL;DR

Built and proved the **per-location presentation detection core** (which of 5 forms each report section
wants + format-error lint), settled the adjudicator technique by web-research + A/B (**few-shot on a
strong model**, not the weak local gemma), and **diagnosed the report-shallowness** to 3 exact code
roots. All committed + pushed. The shallowness FIX and the multi-model frontend wiring are NOT built —
they need a fresh session. One earlier thread (diagram-GENERATION MVP) is parked with 2 failing
integration tests (fake-client quirk; production code works standalone).

| Commit | What | State |
|---|---|---|
| `7a7f049` | Follow-up #1: literal-token diagram grounding (replaces inert embedding-cosine) | ✅ 710 green, pushed |
| `843fa44` | C2: reject edgeless diagram (real committed-renderer defect, codex-found) | ✅ 711 green, pushed |
| `fe8298e`,`1d9d940` | #2 per-section-presentation plan, codex-reviewed ×2 (9 corrections + §11) | ✅ pushed |
| `6212f11` | §7 live prompt-test result (detector fixes old whole-doc FP; C3 trivial-pass confirmed) | ✅ pushed |
| `ef66178` | **presentation_classifier.py + format lint (detection core)** | ✅ 12+14 tests green, pushed |
| `16dd8ac` | **few-shot adjudicator** (research-backed cascade replacement) | ✅ 12 green, pushed |
| `ccabed3` | **shallowness diagnosis** (3 reducer-pipeline roots) | ✅ pushed |

---

## What's DONE and trustworthy

### 1. Presentation detection core — `studio/presentation_classifier.py` (+ `tests/test_presentation_classifier.py`, 12 green)
For each H2 location: `current_form` (markdown surface read) + `recommended_form` (the §10 ladder) →
`Improvement` where the current form is UNDER the recommended (escalation only — never downgrade
well-formed structure). Tiered per PLAN §10:
- **Deterministic ladder** = high-recall PRE-FILTER over measured features (item/component counts,
  sequence/comparison cues, sequence-marked-sentence counting).
- **LLM adjudicator** (`_adjudicate_prompt`, single-shot 5-way + few-shot) OVERRIDES the deterministic
  tier's over-triggering. `analyze_report(text, client)` — pass a STRONG-model client (haiku).
- **`find_format_errors`** — deterministic empty/unclosed code-fence lint. Caught the real line-265
  unclosed ```python fence in `s_2b186fac503d`.
- `diagram_render._ground` also drops generic-only labels (System/Data/Process — codex C3).

### 2. Adjudicator technique — SETTLED by research + A/B
- Web research (EMNLP-2025 hierarchical classification; Wei-2022 CoT): top-down decision **cascades
  error-accumulate** (a wrong parent gate propagates — exactly gemma's YES-bias); **CoT hurts small
  models**; chain/single-prompt beats tree on cost.
- A/B on the real artifact: single-shot == multi-round (4/6 haiku) at 4× the cost; **few-shot** lifted
  **gemma 1→4/6 and haiku 4→5/6** and flipped gemma from over-affirming to conservative. Few-shot
  examples are GENERIC (API gateway / study findings / Redis-Postgres-S3 — no report hardcoding).
- **DECISION: single-shot few-shot on a CAPABLE model.** The weak local gemma is the ceiling — verified
  across 5 techniques (5-way ×2, edge-extraction, binary cascade, evidence-grounded gate); it
  over-affirms "structure" on any section that names components. Consistent with the project's own
  lesson (W3.5.9: reader/judge model was the binding constraint → moved to Haiku).

### 3. Shallowness — DIAGNOSED (not fixed)
375KB evidence (9 sources) → 22.9KB report ≈ **6% utilization**. The `io/*.reducer.in/out` pairs show a
worker's ~34KB of findings reduced to ~6 ONE-SENTENCE patches. Three confirmed code roots:
- **`findings.py:268`** — reducer contract "content is one short paragraph or SENTENCE" → forbids
  multi-sentence synthesis by design; depth plateaus. PRIMARY cap.
- **`findings.py:333`** — per-section density cap ("thins the wall") over-thins real depth.
- **`findings.py:327`** — drops a finding whose URL is already cited → a section can't deepen from a
  source it cited once.
Plus additive-only reducer + weak dedup → the "multiple redundant/duplicate sections" the run's own
weakness log flags (visible as near-duplicate scaffolding fragments in Exec Summary / Background, and
orphaned analysis paragraphs in References).

---

## REMAINING work (fresh session)

1. **Shallowness FIX (highest leverage, the ROOT).** Build the **expand-underdeveloped-sections** stage
   (PLAN-generic §P5 / codex): consumes UNUSED grounded findings into sections below an evidence/word
   floor; hard-guarded (only `_parse_findings` survivors, URL retention, ≤1 synthesized paragraph per
   source-cluster, reject if citation density rises without synthesis density). Plus relax the cited-URL
   drop (`findings.py:327`) + make the density cap (`findings.py:333`) floor-aware for under-developed
   sections. **Touches the core reducer/findings pipeline — verify with real gemma, do NOT rush.**
2. **Multi-model wiring (backend).** Build a JUDGE client from a strong profile (default `haiku`)
   SEPARATE from the generation client (gemma). `analyze_report(text, client)` already takes the client;
   the caller supplies haiku. Add session config `judge_profile`. Wire an endpoint (or the editor pass)
   that runs `analyze_report` + `find_format_errors` → the action list.
3. **Frontend.** Judge-model selector (mirrors generation dropdown, source `list_profiles()`); surface
   the action list (heading, line, current→recommended, source) + format defects; `POST /session`
   carries `judge_llm` alongside `llm`.
4. **Parked: diagram-GENERATION MVP** — `studio/section_presentation.py` (untracked) + the
   `_run_editor_pass` wiring (uncommitted in `runner.py`) + `tests/test_section_presentation.py` (8 unit
   green, untracked) + 3 `test_runner.py` integration tests (**2 FAIL** on a fake-client quirk — `_PSClient`
   detector returns wrong verdict in-test; production `plan_one` renders correctly standalone). Decide:
   finish the fake-client fix, or drop this thread (it was overtaken by the detection focus).

---

## UNCOMMITTED / UNTRACKED STATE (decide before next commit)

- **Uncommitted tracked:** `backend/studio/runner.py` (the section_presentation editor-pass wiring),
  `backend/tests/test_runner.py` (3 section_presentation integration tests, 2 failing). These are the
  parked MVP thread — either finish or `git checkout` them.
- **Untracked:** `backend/studio/section_presentation.py`, `backend/tests/test_section_presentation.py`
  (the MVP module + its 8 passing unit tests).
- No scratch files left from this session's live probes (all were inline `python -c`, nothing written).

## How to resume (environment)

- **Services:** oMLX `:8000` (serves many models — target `gemma-4-26B-A4B-it-heretic-4bit` for
  generation; DO NOT grab `/v1/models` `data[0]` — it's a heavier 31B that 507s on load). Strong models
  via VibeProxy profiles: `haiku`/`sonnet`/`opus` (`studio.shared_bridge.PROFILES`). SearXNG `:8080`,
  backend `:8770`, frontend `:5173`.
- **Build a client in a script:** `from studio.backends import resolve_backend, build_chat_client;
  build_chat_client(resolve_backend({"profile": "haiku"}), on_usage=lambda *a,**k: None, temperature=0.0)`.
- **Tests:** `cd agentkit-studio/backend && .venv/bin/python -m pytest tests/test_presentation_classifier.py
  tests/test_diagram_render.py -q` (26 green). Full suite was 711 green at `843fa44`; the parked
  section_presentation integration tests fail (excluded from the committed core).
- **Reference artifact:** `backend/tmp/studio-workspaces/s_2b186fac503d/` — `artifact.md` (report),
  `evidence/source-00N.md` (9 sources / 375KB), `io/*.reducer.in|out.md` (the thinning trace).
  My ground-truth labels for its 8 sections are in `PLAN-report-quality-analyzer.md`.
- **gotcha:** `cd` in a compound bash command drifts cwd for later calls; always `cd` to an absolute path.

## Method note (why the findings are trustworthy)

Every conclusion was verified against reality, not theory: the embedding-grounding guard was proven
inert by measurement; the weak-model ceiling was established across 5 techniques; the presentation
labels were set by READING the report and its 9 evidence sources (not trusting the code); the
shallowness roots were traced through the real `io/` reducer artifacts to exact `findings.py` lines.
Chasing an exact label match by prompt-tuning was explicitly rejected as overfitting/hardcoding.
