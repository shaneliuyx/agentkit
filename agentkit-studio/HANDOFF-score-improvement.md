# HANDOFF — break the hill-climb score past ~0.67 (substantiation depth)

_Written 2026-06-27. All prior work is on `main` (pushed, `f1dc170`). This doc is
a scratch handoff for the NEXT session; delete when the four levers land._

## TL;DR

The hill-climb loop is now **correct, bounded, clean, and honestly-scored**. The
score plateaus at **~0.67** for ONE reason: **substantiation depth** — workers
*fetch and cite* real sources (v29 had 34 URLs) but the artifact *lists* them
without *integrating* what each says. The score is `solved / total` over the
weakness set, so resolving the substantiation weaknesses is the whole game.

Implement four levers, in order. Lever 1 is the biggest win at ~zero cost/risk.

## Current state (verified live, v29)

- Latest report: `backend/tmp/studio-workspaces/s_45642fe313ba/artifact.md` (38KB,
  score 0.67, clean — no preamble, no future-date, 34 URLs, ends cleanly).
- `task_hash = 4ca9b03811b7` (= `sha256("find the most popular loop engineer
  articles for agent development, and create a research report".lower())[:12]`).
- Score history (DB `task_runs`): v26 0.10 (old noisy scorer) → v27 0.67 → v28
  0.64 → v29 0.67 (all-fixes run).

### v29 residual weaknesses (THE targets — each maps to a lever)
```
[document] Output truncated mid-sentence in final section          -> Lever 3
[## Core Finding...] Section incomplete                             -> Lever 3
[## Sources] Missing substantiation for Kief Morris / Martin Fowler -> Lever 1 + 2
[## Sources] Missing substantiation for Tessl.io                    -> Lever 1 + 2
[## Sources] Missing substantiation for academic citations (Yao...) -> Lever 1 + 2
```
4 of 5 are "cited but thin" -> Lever 1 is the dominant lever.

---

## Lever 1 — workers SUMMARIZE, not just cite  (biggest gain, ~0 cost/risk)

**Where:** `studio/runner.py` -> `_build_executor_prompt(goal, artifact_text,
weaknesses_block)` (added §11.10; it builds the STAR-spoke prompt). The spokes
already fetch (cache-as-oracle wired), but `RESEARCH_FINDING.CONTENT` asks only
for "2-4 sentences" -> the model emits a generic line.

**Do:**
1. Enrich the `RESEARCH_FINDING` schema in the prompt to require, per fetched
   source: (a) one **verbatim QUOTE** (<=25 words) copied from the fetched page,
   (b) the article's **central CLAIM** (1 sentence), (c) **WHY it matters** to the
   report's question. Keep `URL` / `ARTICLE_TITLE` / `POPULARITY` / `PATCH_TARGET`.
2. **Quote-in-cache guard (deterministic):** after the phase, validate each
   finding's QUOTE is an actual substring of the fetched page in the cache. The
   cache is `studio/tools.py::_fetch_cache` (module dict, key `"url|selector"`,
   value `(content, n_bytes)`). A finding whose quote is NOT in any cached page is
   fabricated -> drop it (or flag). Ties to the existing `_verified_urls` logic
   (`runner.py` ~1696, `_cached_set`).

**Impact:** resolves the 3 substantiation weaknesses -> expect ~0.67 -> ~0.85.
**Test:** extend `tests/test_runner.py::test_executor_prompt_*` to assert the QUOTE
requirement is present; add a small `_quote_in_cache` unit test.

## Lever 3 — patch-based reducer  (kills truncation + tokens; structural)

**Where:** `studio/runner.py` -> `_make_section_reducer(client, artifact_text,
weaknesses)` (the injected `run_plan` reducer, §4.5). Today it **regenerates the
full ~38K artifact** -> output caps truncate a section even with `max_tokens=8192`
(see `client.py` default). That is weaknesses #1, #2.

**Do:** switch the reducer to emit **section PATCHES** (`insert_after` / `replace`
on ONE `## heading`), applied mechanically by `reduce_patches`
(`agentkit.artifacts.reducer` / the OCC reducer — ALREADY in the codebase; see
DESIGN §2.2 "Reducer algorithm" + §11.3). No full-doc regeneration -> truncation
impossible, output tokens drop ~10x. The grow-only ratchet + `_strip_preamble`
stay as guards.

**Watch:** the per-phase write-back (`runner.py` ~1354, `_clean_out =
_strip_preamble(sr.output)`) currently expects the full doc back. With patches,
the artifact is mutated in place by `reduce_patches` — re-read the file for the
new `_seed_len`. Keep `_split_sections` (`studio/tools.py`) as the section oracle.

**Impact:** resolves the 2 truncation/incomplete weaknesses; re-cuts tokens.
**Effort:** medium. **Test:** reducer emits valid patches; truncation gone on a
>32K artifact.

## Lever 2 — reducer WEAVES the finding into prose  (do after Lever 3)

**Where:** same `_make_section_reducer` (now patch-based). Today additive =
appends a `Source: URL` line. **Do:** let a patch ADD a substantiating SENTENCE
*inside* the target section (summary of the source), not just a citation line.
Preserve-verbatim still holds for EXISTING text; new substance is woven via an
`insert_after` patch anchored on a sentence in the section. Natural once patches
exist (Lever 3). **Impact:** citations -> grounded prose (the scorer's "evidence"
criterion).

## Lever 4 — sonnet for the reducer only  (optional, push past ~0.9)

**Where:** the reducer's `client` is the per-phase tool client (`runner.py` ~1280,
`_reducer = _make_section_reducer(client, ...)`). **Do:** build a SEPARATE
`StudioChatClient` on the `sonnet` profile (`studio/backends.py::PROFILES` ->
`resolve_backend({"profile":"sonnet"})` -> `build_chat_client`) and pass IT to the
reducer; keep haiku for the parallel fetch workers. **Cost:** ~3x tokens on the
reduce calls only (small share). **Impact:** deepest synthesis. **Trade-off:**
money for depth.

---

## Reference — how to build/verify (do this each iteration)

### Files touched (map)
| Concern | File . symbol |
|---|---|
| Worker/spoke prompt | `studio/runner.py::_build_executor_prompt` |
| Section reducer | `studio/runner.py::_make_section_reducer` |
| Reducer injection | `studio/runner.py` (~1280, `_reducer = ...`) |
| Per-phase writeback | `studio/runner.py` (~1354, grow-only + `_strip_preamble`) |
| Fetch cache (oracle) | `studio/tools.py::_fetch_cache`, `_split_sections`, `_section_hash` |
| Verified URLs | `studio/runner.py` (~1696, `_verified_urls`/`_cached_set`) |
| Weakness mining | `studio/task_runs.py::mine_weaknesses_from_outputs` (takes `verified_urls`) |
| Score (semantic) | `studio/runner.py::_weakness_score` (embedder cosine) |
| Patch reducer (reuse) | `agentkit.artifacts.reducer` / OCC `reduce_patches` |
| Model profiles | `studio/backends.py::PROFILES` (haiku/opus/sonnet/qwen) |

### Tests (all must stay green)
```bash
cd agentkit-studio/backend && .venv/bin/python -m pytest tests/ -q   # 239 studio
cd agentkit && agentkit-studio/backend/.venv/bin/python -m pytest tests/ -q  # core
cd agentkit-studio/frontend && npx vitest run                        # 43 frontend
```

### Live verification (the score delta is the proof)
1. **Restart the backend after any backend edit** (it loads code at startup):
   ```bash
   cd agentkit-studio/backend
   pkill -9 -f "uvicorn studio.app"; sleep 1
   lsof -nP -iTCP:8770 -sTCP:LISTEN   # must be FREE
   env -u TAVILY_API_KEY SEARXNG_URL="http://localhost:8080" \
     .venv/bin/python -m uvicorn studio.app:app --host 127.0.0.1 --port 8770 --log-level warning &
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8770/backends   # 200
   ```
2. **Drive a run** via the SSE driver (DESIGN §13 / README "Driving a run"): POST
   /session (haiku, mode=llm, tools_enabled, loop_config max_agents=5) -> POST
   /session/{id}/hill-climb (auto_improve, max_epochs=2) -> GET /run?requirement=...
   and **drain the SSE fully or it won't record**.
3. **Measure:** poll `tmp/task_runs.db` for the new `version`; compare its
   `score` and **diff its `weaknesses_json` against v29** — each resolved row is a
   predicted score gain. Target: substantiation weaknesses gone after Lever 1.

### Gotchas (cost real runs last session)
- **Restart or you test stale code** — the running uvicorn holds `:8770`; a
  "reload" can silently fail to bind. pkill, confirm FREE, start ONE, confirm pid.
- **Drain the SSE** — early disconnect cancels the server generator -> no DB row.
- **tools_enabled + SearXNG `:8080`** must be live or the loop FABRICATES (no real
  sources, score caps ~0.3). `web_toolkit` is already on the venv via `.pth`.
- **haiku proxy (`:8317`) auth can drop** (503 `auth_unavailable`) — transient;
  retry, or a sibling Claude model (sonnet) on the same proxy may serve.
- **Score is noisy at the input** (LLM mining re-words weaknesses) — `_weakness_score`
  matches semantically (cosine >=0.85) to compensate; don't regress to string match.
- **A run costs real tokens** (~250-350K input after the read_artifact section
  fix). Prefer offline probes (build the prompt, call one haiku completion) over a
  full GUI run when validating a prompt change.

## First action next session
1. Read this doc + DESIGN §4.5 / §11.10 / §11.3.
2. Implement **Lever 1** (executor QUOTE+CLAIM+WHY schema + `_quote_in_cache`
   guard). TDD: prompt-assertion test + the substring guard unit test.
3. Restart backend, one SSE run, diff the weakness set vs v29, report the score
   delta. Then Lever 3.
