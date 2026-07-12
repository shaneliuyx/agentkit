# AgentKit Studio — Design (research-report generation)

**Status:** current design authority (supersedes `DESIGN-v2.md`, which documents the
retired hub/spoke "Section-Ownership" architecture).
**Last rewritten:** 2026-07-12, against the code as it stands after the `research_first`
rebuild + the `runner.py` decomposition.
**Companion:** `SPEC.md` owns milestone/route contracts; `PLAN-CONSOLIDATED.md` owns the
backlog + decision history. (The former `ARCHITECTURE-doc-generation-pipeline.md` process map
is superseded — its per-stage detail lives in §3 below; recover the old mermaid deep-dive from
git history at commit `4de0e9c` if ever needed.)

Refs below are by **module::function** (durable across refactors), not line numbers.

---

## 1. Objective & success metric

**What it is.** AgentKit Studio is a local, single-user platform that turns a plain-language
requirement into an **evidence-backed research report** — sections, code, diagrams, a
comparison/integration artifact, and a References list built from real fetched sources — and
**improves it across runs** (hill-climb) without human editing.

**"Good", measured.** A run is judged by a rubric-scored self-eval (0–1) plus a deterministic
**editorial gate** (E1–E11). The canonical acceptance bar (task_hash `492bae60177b`, the
Pi/Craft report): every requested subject resolved + cited from a distinct real source, an
integration diagram or comparison table that names every subject, References built from the
cited claim set (last, both-directions consistent), and no fabricated/ungrounded prose. Live
runs reach 0.8–1.0 on that lineage.

**Non-negotiables (the whole system exists to enforce these).**
- **No fabrication.** Content enters the artifact only if it is grounded in a fetched source's
  verbatim quote. Absence of evidence becomes an honest *Limitations* line, never invented prose.
- **No grader-gaming.** Guards are structural / distance-based; there are no hardcoded
  task/subject phrase lists in production code (test-enforced).
- **Honest failure.** A check that cannot verify records `could_not_verify`, never a false
  pass; a crashed run records `unverified`, never a `0.0` that would poison the lineage.

---

## 2. System architecture — module map

Studio is a FastAPI backend (`studio/`) + a Vite/React frontend (`frontend/`), riding a shared
engine (`agentkit/`, §12). One SQLite file (`tmp/task_runs.db`) is the entire durable state.

| Module | Role | Path in the live path? |
|---|---|---|
| `research_first.py` | **The generation core** — the linear FRAME→RESEARCH→CLAIMS→WRITE→ASSEMBLE pipeline. | ✅ default |
| `runner.py` | The run shell: session dispatch, the epoch loop, SSE `emit`, scoring/recording. | ✅ shared |
| `finalize.py` | The epoch-end **pass pipeline** (materialize, score, editorial gate, record). | ✅ shared |
| `task_runs.py` | `TaskRunStore` — lineage, seeding, weakness mining, cross-task similarity (SQLite + numpy). | ✅ shared |
| `rubric.py` | The research-report evaluation rubric + adjusted scoring. | ✅ shared |
| `tools.py` | `ToolAugmentedClient` — the web_search/web_fetch tool loop over any LLM client. | ✅ shared |
| `triggers.py` | Scheduler trigger registry + chain registry + cron firing (§10). | ✅ (loop automation) |
| `app.py` | FastAPI routes + the SSE run stream. | ✅ shared |
| `backends.py` / `client.py` | `PROFILES` menu → `StudioChatClient`; usage capture. | ✅ shared |
| `legacy_loop.py` | The retired hub/spoke seed-and-patch loop — **rollback fallback only**. | ⛔ fallback |
| `seed_carry.py` | Hill-climb artifact carry-forward — **skipped by research_first** (cold-start). | ⛔ fallback |
| `editor_pass.py` | The editor/structural-retry subsystem — a **content-mutating finalize pass, skipped on research_first** (§7). | ⛔ fallback |

**The decomposition (2026-07-12).** `runner.py` was a 4,443-line god module; it was carved to
2,187 lines by extracting three cohesive, behavior-preserving units — `legacy_loop.py`
(the legacy `_run_phase_loop`), `seed_carry.py` (`_seed_carry_forward`), and `editor_pass.py`
(the 16-function editor subsystem, re-exported from `runner` so consumers are unchanged). Each
is a verbatim move (AST-identical bodies) behind a late-import / re-export seam so nothing that
imports from `studio.runner` breaks. Rationale: the god module taxed every session with
navigation + merge-conflict cost; isolating "the legacy loop", "seed carry-forward", and "the
editor" into findable files is the win — the line count is a proxy.

**Two generation modes, one shell.** The runner dispatches to `research_first` when
`_use_research_first(session)` is true (registry default is **off**; `POST /session` sets it,
and the env kill-switch `STUDIO_DISABLE_RESEARCH_FIRST` forces the legacy path for instant
rollback). The legacy hub/spoke loop (`legacy_loop.py`) exists only as that fallback and is not
documented here. **Fail-visible:** an opted-in research_first run that errors surfaces the error;
it does not silently fall back.

---

## 3. The generation core — `research_first`

`generate_research_first` (`research_first.py`) runs **linearly**: five stages, each feeding the
next. No topology, no hub/spoke fan-out, no per-phase LLM orchestration. The unit of research is
the **subject** (the real referent, e.g. `Pi`, `Craft`), not the section.

```
FRAME → RESEARCH → CLAIMS → WRITE → ASSEMBLE
```

| Stage | Entry | One line |
|---|---|---|
| **FRAME** | inline in `generate_research_first` | Extract subjects + sections + code/diagram needs; disambiguate each subject; classify the inter-subject relationship. |
| **RESEARCH** | `_research` | Per-subject fetch loop (offtopic/anchor-gated), a 0-source subject triggers a question-critic reformulation; returns a per-subject source ledger + coverage. |
| **CLAIMS** | `_build_claims` | Extract `{claim, quote, url, subjects, relations}` from each source over a moving window; coverage-gate + recover. |
| **WRITE** | `_write_section` per section | Write each skeleton heading from its own claims; splice grounded code / diagrams / a relations or comparison table / the summary (last). |
| **ASSEMBLE** | `_assemble` | Stitch title + summary + sections; build References **from claims** (last, both directions); write the coverage sink. |

SSE surfaces each stage as a `phase_start` (`research_first_frame/research/claims/write/assemble`)
plus data events (`frame`, `research`, `claims`, `write`, `coverage`), then `hill_climb`, then a
single terminal `done`.

### 3.1 FRAME — subjects, sections, relationship
- **Subjects are the unit of research.** `_extract_subjects` pulls the real referents; the LLM
  fallback is deterministically validated (verbatim substring, ≤4 words, ≠ whole-task,
  compound-split *after* the whole-task check) so a whole-task string never becomes a subject.
- **Disambiguation** (`_disambiguate_subject`) resolves each subject to a concrete referent +
  search anchors (fail-open). Descriptor + anchors persist into the coverage ledger and the Scope
  assumption line — this is what keeps "Pi" resolving to `earendil-works/pi`, not the π constant.
- **Relationship is classified, not presumed** (`_classify_relationship`): a `kind` ∈
  {cooperates, competes, extends, alternative, independent, unknown}, grounded in joint evidence,
  fail-open `unknown`. Task framing outweighs subject similarity ("use X and Y together" →
  cooperates/extends). Downstream: cooperate/extends → integration section + diagram;
  competes/alternative → comparison table; independent → no forced relationship artifact. (The old
  hardcoded interface enum `api|cli|mcp|sdk` was removed — D-level MVP-8.)

### 3.2 RESEARCH — per-subject fetch loop (`_research`)
- **Coverage by construction.** Every subject is searched unconditionally; a joint loop searches
  the classified relationship mechanism. No subject is silently skipped.
- **Offtopic gate** (`_is_offtopic`) = a hard keyword band + a gray-zone LLM judge (on the strong
  judge client). Wrong-referent sources (π-Wikipedia, same-name products) are dropped at selection,
  before they pollute claims.
- **Question-critic** (`_reformulate_queries`, D2): a 0-source subject triggers ≤3 model-proposed
  reformulated queries (fed the descriptor/anchors so a retry never regresses to the bare ambiguous
  name), sharing the same gates. Fail-open — a 0-source subject never stalls; it becomes a
  Limitations line.

### 3.3 CLAIMS — extract, coverage-gate, recover (`_build_claims`)
- **Full-file moving window.** The old `content[:8000]` head-cut saw only nav chrome (the
  architecture sentence was at char ~93k). `_content_windows` reads the whole boilerplate-stripped
  file in overlapping bounded windows — this is what made the real Pi↔Craft joint claim
  extractable (joint 0→7).
- **Claim schema carries `relations:[{head,rel,tail}]`.** The LLM emits triples; code keeps only
  those whose *both* endpoints are whole-token-grounded in the verbatim quote. Feeds the relations
  fan-out table and the integration diagram edge.
- **Subject-tag clamp.** A single-subject fetch loop vetted sources against only its own anchors,
  so any claim tagged with something other than that subject is unvetted → reset to the loop's
  subject. Kills the wrong-referent leak class.
- **Coverage gate + recovery** (`_coverage_gate`, `_recover_underevidenced`): an under-evidenced
  contract fires one targeted recovery search *now*; a still-short contract emits a fail-visible
  `coverage_failed_partial` event and (via P2.5 recovery) becomes a declared question-limitation —
  never weak fallback prose. `_classify_answerability` routes structurally-unanswerable asks to
  reasoned Limitations instead of burning searches.

### 3.4 WRITE — one section per heading (`_write_section`)
- **Sections are written once, from their own claims.** `_claims_for_section` round-robins subjects
  into the per-section cap so subject-2 is never starved.
- **Grounding is enforced at write time.** `_drop_ungrounded_sentences` (fence-aware) keeps a
  sentence only if it cites a URL whose **domain** matches the section's claims (domain-level
  because weak models truncate real URL paths; cross-domain fabrication still dies).
- **Structural artifacts are grounded, not invented.** Code is compile-gated and its interface
  vocabulary must appear in claims; a diagram needs ≥4 claim-grounded nodes or none is drawn; the
  relationship table vs comparison table branch is chosen by the classified `kind`.
- **Exec Summary is written LAST** (`_write_summary`, D4) from the final body — the overclaiming-
  summary class is prevented by construction (E8 remains as the detector).

### 3.5 ASSEMBLE — stitch + References-from-claims + coverage (`_assemble`)
- **References are BUILT from claims, never scraped from body** (`_rebuild_references_from_claims`):
  References = the deduped `claims[].url` set, both directions, always last. A real repo lands in
  References once it is a claim; LaTeX/SVG link-texts from a rendered page never do.
- **Inline `[N]` markers render a declared citation** (`_apply_citation_markers`): the writer emits
  explicit URLs; code maps each to its reference number. Zero false attribution.
- **Coverage ledger (P1)** (`_coverage_cited`): per subject `{queries_issued, sources_fetched,
  cited_in_artifact}`; `cited_in_artifact` counts a URL only if it (or its `[N]`) appears in the
  **body** (References-only presence does not count). Written to `coverage.json` + an SSE event.
- **Not-found contract (P4)** (`_limitations_note`): a subject that yielded no claims auto-emits a
  Limitations line (match-only home so it never splices into a wrong section); silent when covered.

---

## 4. Core invariants

1. **One deliverable per task.** A run writes a single `artifact.md`; research_first writes each
   section once from fresh claims (no reconstruction-from-scratch of a prior version).
2. **Grounded-or-absent.** Every sentence, code block, diagram node, and citation traces to a
   fetched source's verbatim quote, or it does not ship. Absence → a tracked Limitations line.
3. **References ⊆ cited claims, both directions, last.** The References list is exactly the cited
   claim-URL set; body markers and the list are consistent both ways.
4. **No whole-doc LLM echo.** No call takes a large document and expects it echoed back whole (a
   model truncates a long echo). Processing windows → processes → splices a localized block.
5. **Cold-start by design.** research_first never seeds from a prior artifact (§7); each epoch is a
   fresh linear build. Improvement rides the scoring/editorial tail, not artifact carry-forward.
6. **Honest verification.** could_not_verify ≠ pass; crashed ≠ scored. Fail-open guards always log.

---

## 5. Claims, contracts, coverage & recovery (design workstreams D1–D5)

The research→write boundary is a **first-class evidence layer**, not transient patch material —
the single largest design change from the legacy pipeline.

- **D1 — claims-with-provenance (~realized).** `claims.jsonl` is a grow-only, deduped substrate of
  `{claim, quote, url, subjects, relations}`. WRITE compiles sections FROM claims; coverage (E3),
  conflicts, and exec-summary consistency (E8) are **queries over the claims table**, not
  whole-text LLM judgments. Residual: no per-claim `confidence` field (its only consumer would be
  claim-ranking, already handled by subject-grounded fan-out ranking).
- **Answer contracts (P2, done-in-research_first).** FRAME emits a per-question
  `{question, subject, answer_form, min_evidence}` contract *before* research; resolution is
  per-question (`_resolve_question_contracts`). A joint comparison/design question resolves by
  DISTINCT cited URLs per side **and** an artifact (table/diagram) that strictly names every
  subject — not by a single both-subject page (a web-availability ceiling). A semantic
  joint-claim guard (`_verify_joint_claim` / `_filter_joint_noise`) drops bare-name co-mentions
  ("Pi is one of the compatible models") that are lexically clean but a different referent.
- **D2 — search-step critic (done).** `_reformulate_queries` (§3.2).
- **D3 — disambiguation channel (done).** Descriptor/anchors persist structurally in
  `coverage.json` + the SSE `coverage` event, on top of the Scope assumption line.
- **D4 — write-order (realized-by-construction).** Exec Summary written last from the body (§3.4).
- **D5 — belief/uncertainty (closed).** The confidence-ledger / next-epoch-budget core is
  **dead-by-cold-start** (there is no next epoch to consume a confidence signal — the same
  MEASURED-NULL class as legacy repeat-weakness escalation). The one live part — a failed
  structural producer raising a research signal, not empty content — is realized by
  `_recover_underevidenced`'s in-run closed-loop recovery → declared question-limitation.

---

## 6. Finalize pipeline & the editorial gate

After generation, `run_passes(state)` (`finalize.py`) walks an ordered `PASSES` list, fail-open
per pass. A research_first run carries `rebuild_generated=True`, which **skips every
content-mutating pass** (`_CONTENT_MUTATING_PASSES`: normalize_dedupe, synthesize_readability,
repair_lints, neutralize_urls, structural_producer_l0, rebuild_references, expand_underdeveloped,
publish_gate, **editor**) and runs only the recording/scoring tail.

**Why trust inverts on rebuild.** For a legacy run those passes repair/dedupe/rebuild the document;
for a research_first run the ASSEMBLE stage already owns dedupe, references, fence/citation repair,
and structural production — so re-running them can only corrupt. This is why `editor_pass.py`
(the editor subsystem) does **not** run on a research_first report (verified live: a research_first
E2E emits zero editor gate events, by design — regression-locked in `test_finalize.py`).

**Write-early + materialize overwrite.** The generated text is written to `artifact.md` right after
generate returns; `_pass_materialize_artifact` then OVERWRITES `artifact.md` on rebuild so a stale
seed can never win the post-gen preference. Verified byte-exact (recorded `result_text` ==
workspace `artifact.md`).

### 6.1 The editorial gate — E1–E11 (`compute_editorial_rows`)

Eleven deterministic checks over the assembled artifact; each returns
`{row, verdict ∈ pass|fail|could_not_verify, evidence, required}`. A raised check degrades to
`could_not_verify` — **never a false pass**.

| Row | Checks | Note |
|---|---|---|
| E1 | required sections present | deterministic |
| E2 | no stub sections (thin non-structural bodies) | deterministic |
| E3 | every ledger subject cited-in-artifact or declared not-found | **hard** (fail → rejected); reads coverage.json |
| E4 | citations resolve (grounding) | **hard**; grounded-by-construction on rebuild |
| E5 | References ⊇ and ⊆ body citations (both directions) | deterministic |
| E6 | structural validity (mermaid/table/fence) | deterministic |
| E7 | dynamic sections carry their promised block (a "Code…" heading needs a fence, etc.) | advisory |
| E8 | exec-summary consistency: no summary-only citation the body doesn't support | advisory |
| E9 | internal number consistency (same noun phrase, no conflicting counts) | advisory |
| E10 | honest limitations: a recorded coverage gap is NAMED in Limitations, not boilerplate | advisory |
| E11 | lint clean (content-validity list) | deterministic |

**Verdict router** (`_editorial_run_status`): only **E3/E4 fail → `rejected`** (a subject
researched-but-uncited, or grounding residue — a content defect, not a nit). Every other fail
records `completed` (the fix is a next-epoch weakness seed, never a faked pass). Empty rows →
`unverified` (a crashed gate must never read as a fully-scored `completed`). **Doctrine:** the
SPEC's LLM-judge half of each row is deliberately skipped for the cold-start pipeline — an
advisory weakness is never consumed (no next epoch), and a weak-model judge would false-reject
prevention-hardened output. Only the cheap deterministic *regression invariant* is built.

---

## 7. Hill-climb, cold-start & persistence

**Hill-climb** is cross-run improvement keyed by `task_hash = sha256(requirement.strip().lower())[:12]`.
The SAME requirement (case/space-normalized) shares a hash across sessions; a later run finds the
prior lineage and its accumulated weaknesses. Each run records a monotone `version` per hash. The
SSE `hill_climb` event reports `{epoch, score, status}` (e.g. `v99 score=0.81 status=improving`).

**Cold-start by design.** research_first never carries a prior artifact forward — the
`_seed_carry_forward` block is skipped when `_use_research_first(session)`. Rationale: the legacy
loop merged the carried seed into the rebuild output, accumulating stale sections across a lineage
and pushing References off the end; a linear pipeline that writes each section once from fresh
claims is only correct if nothing pre-seeds it. Improvement therefore comes from the
scoring/editorial tail feeding weaknesses forward, not from artifact carry-forward.

> **Lineage trap.** Attaching a goal or a loop-seed changes the requirement text → rotates
> `task_hash` → a fresh cold-start v1. Run the bare end-state requirement to resume a lineage.

**Persistence — `task_runs.db`** (one SQLite file; `TaskRunStore`, no ORM):
```sql
CREATE TABLE task_runs (
  id INTEGER PRIMARY KEY, task_hash TEXT, session_id TEXT,
  version INTEGER, score REAL, weaknesses_json TEXT,
  requirement TEXT, result_text TEXT,            -- deliverable (preamble-stripped)
  requirement_embedding BLOB,                    -- R10 cross-task cosine similarity
  config_json TEXT, created_at TEXT);            -- hill-climb config snapshot (survives restart)
```
- **Seed = latest-with-content, not best-score** (`latest_with_content`): self-eval scores are
  noisy; the latest run carries the most accumulated work. The seed is sanitized on read.
- **Servable-ancestor invariant.** `latest_with_content` carries NO status filter — the user is
  always served the latest content row even when `rejected`; only the hill-climb *seed* path
  excludes `rejected`, and research_first is cold-start (never seeds), so an all-rejected lineage
  degrades to cold-start, never to "nothing servable".
- **Acceptance economics (L5).** `build_pass_economics` records per-pass attempted/accepted counts
  + tokens-per-accepted into the run metrics.

---

## 8. Evaluation — rubric & scoring

`score_result` (`task_runs.py`) shows the judge the **full** artifact (up to ~20K chars — a small
window hid tail citations and gave falsely high scores) and returns a rubric-weighted 0–1 score.
`rubric.py` owns the criteria (summary, findings, evidence synthesis, analytical depth, sourcing,
methodology, conclusion) and the topic-agnostic default template (`GENERIC_RESEARCH_PROFILE`).
Weaknesses are mined section-tagged (`mine_weaknesses_from_outputs`, ~6K window) and refuted
against the real artifact (`refute_false_weaknesses`) so a stale placeholder claim can't seed a
phantom next-epoch fix. Cross-task lessons (R10) come from `similar_runs`/`accumulated_weaknesses`
(cosine over `requirement_embedding`).

---

## 9. Loop automation — scheduler & chain

Two Studio-native features let a report (or any task) run on a schedule or as a dependency DAG.

**Chain** (`POST /chain/run`, `_execute_chain`). A `LoopChain` (agentkit) runs named steps in
topological order, each step's merged input = the initial context + upstream step outputs. Each
step is a **real bounded LLM task** (`_build_chain_runner_factory`, max 1024 tokens, fed the step
description + upstream outputs); no backend configured → an honest `{error: "no LLM backend"}`,
never a silent stub. `/chain/suggest` decomposes a task into a DAG spec via the LLM.

**Scheduler** (`triggers.py`; `POST /scheduler/cron`, `GET /scheduler`, `DELETE …`). A cron
trigger registers a `(cron_expr, chain_id)` pair and, when armed, fires the **saved** chain of that
id on a self-rescheduling daemon `threading.Timer`. The tick period is derived from the cron
expression (`*/N * * * *` honored; unsupported forms flagged `cron_parsed=False` rather than
silently misread), clamped to a floor so a real-LLM loop cannot run away. A failing tick records
`failures`/`last_error` on the trigger (surfaced in `GET /scheduler`) instead of spamming a thread
traceback; the schedule self-heals (re-arm in `finally`). In-memory + process-local — a durable
store is a future enhancement; register/list/delete/fire are fully real.

---

## 10. SSE run API

The GUI is one client; the same calls drive a run from any script (backend on `:8770`).

1. **`POST /session`** → `{session_id}`. Body: `{llm:{profile}, judge_llm:{profile}|null,
   embed:{}, mode:"llm"|"auto", budget:{ceiling}, tools_enabled, use_research_first}`.
   `tools_enabled` gates web_search/web_fetch — **off ⇒ the pipeline fabricates and the scorer
   caps ~0.30** (the #1 "looks fine but low score" cause).
2. **`POST /session/{id}/hill-climb`** (optional) — `{auto_improve, max_epochs, min_improvement,
   score_metric, …}`.
3. **`POST /session/{id}/rubric`** (optional) — `{weights, template}`; `GET /rubric/defaults` seeds
   the panel.
4. **`GET /run/{id}?requirement=<urlencoded>`** → an **SSE stream**: `session → phase_start
   (frame/research/claims/write/assemble) + data events (frame, research, claims, write, coverage)
   → hill_climb → done`. Optional `history` = JSON `[{role,content}]` for multi-turn.

> **GOTCHA — drain the stream fully.** The run records the row *as the stream is consumed*. A client
> that disconnects early cancels the server-side generator → the run may never record. A browser
> `EventSource` is fine; a script MUST read every line until the server closes the stream.

Other routes: `/backends`, `/loops`, `/skills`, `/export/{id}`, `/artifacts/{id}`,
`/session/{id}/goal[/suggest]`, `/chain/{run,suggest}`, `/scheduler[/cron]`, `/catalog/templates/*`,
`/task-runs/{hash}`.

---

## 11. Local services & environment

- **oMLX `:8000`** — local chat + BGE-M3 embeddings (the `local` embed profile); retried 3× on
  connection errors.
- **SearXNG `:8080`** — primary search backend; precedence **SearXNG → Tavily → DDG** (falls
  through on an empty pool or a down server). `web_toolkit` must be on the venv path (a `.pth`) or
  the loop silently runs with no search and fabricates.
- LLM profiles resolve in `backends.py::PROFILES`. The **judge** client (default haiku) is a strong
  model used for the offtopic gray-zone gate, joint-claim verification, and presentation-quality
  scoring; spokes/sections stay on the session model.

---

## 12. Shared-library boundary (`agentkit`)

Studio is the orchestration shell; `agentkit` is the reusable engine (pure, no `studio` import;
client/embedder injected via Protocol). Reusable mechanisms live under `agentkit/`:
`topology.{sizing,dynamic,core}`, `orchestrator.ledger`, `artifacts.{patcher,store,sections,dedup,
metrics,ranking,types}`, `improvement.*` (`TaskRunStore`/`score_result`/`mine_weaknesses`),
`loop.{chain,suggest}` (the LoopChain the scheduler fires), `runtime.scheduler` (cron/webhook trigger
model), `evolve` (`check_goal`/`self_preference`), `tools.fetch_cache`, `context`, `types`
(`ChatResult`/`Message`/`Embedder` Protocol).

> The `runner.py` decomposition follow-up noted in `DESIGN-v2.md` §11 is **done** (§2): the
> record/scoring, seed, and editor units are extracted (into `finalize.py` / `seed_carry.py` /
> `editor_pass.py`).

---

## 13. UI / GUI (`frontend/`)

A single-page React app: a header (LLM/judge/embedder profile menus, budget, Connect session, ⚙
Loop configuration), a main topology/conversation pane with a `<ChatPanel>` multi-turn thread
(prior turns forwarded as `history` on the SSE URL), a token meter, and **14 tabbed panels**
(LOOPS, TOOLS, ROUTER, MEMORY, SELF-IMPROVE, EVOLVE, SECURITY, LOOP DOCTOR, DAG, VERIFY, GOAL,
HILL CLIMB, SCHEDULER, CHAIN) fed by the live SSE events. The ⚙ Loop Config dialog has goal /
scheduler / chain / hill_climb / rubric tabs; the rubric panel iterates whatever
`GET /rubric/defaults` returns (**no hardcoded criterion keys** — a new backend criterion needs
zero frontend change). Scheduler + chain panels are wired to the real endpoints (§9).

---

## 14. Decision log & rationale

- **research_first replaced hub/spoke (2026-07-05, user ruling).** The legacy phase-loop oscillated
  between defect and guard-patch across six runs and never produced a Craft citation in seven runs;
  the linear claims-grounded pipeline reached deterministic acceptance (0.92–1.0). Guards detect but
  never converge — the construction fix (rebuild the producer) was taken after 2 failed patch rounds
  on one defect family. That "construction over guard after ~2 rounds" is now a standing rule.
- **Cold-start over carry-forward (D4).** Merging a carried seed into a fresh linear build
  accumulates stale sections and buries References; cold-start makes "write each section once" sound.
- **Editorial rows deterministic-only.** The SPEC specced LLM judges per row; for a cold-start
  pipeline an advisory weakness is never consumed and a weak-model judge false-rejects
  prevention-hardened output — so only the cheap deterministic regression invariant is built.
- **runner.py decomposition (2026-07-12).** Behavior-preserving extraction (AST-identical moves +
  late-import/re-export seams) of the legacy loop, seed carry-forward, and editor subsystem; the
  remaining orchestration spine (`_run_inner`, 90 `self` refs) is intentionally kept whole (splitting
  it scatters one control-flow). Enforcement policy going forward: hard-gate new code (<50-line
  func / <300 class / <500 file), refactor-on-touch, no repo-wide sweep.
- **Scheduler/chain: real, not stub.** The GUI's chain step-runner and scheduler register were
  stubs; both are now real (bounded-LLM steps; register/list/fire with cron-derived interval,
  interval floor, and recorded tick failures). A latent `/chain/suggest` bug (a dead `studio.catalog`
  import that always fell back to a single step) was fixed in the same pass.
- **Verify-first / no-fake-completion (standing).** Outcomes are confirmed by live runs, not green
  suites; a stub, a `test.skip`, or a TODO branch is a blocker, not evidence. Stale-server discipline:
  a no-reload dev server can serve old code — confirm `server-start-time > newest-source-mtime`
  before trusting any live run.
