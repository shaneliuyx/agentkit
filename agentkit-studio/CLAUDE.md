# AgentKit Studio — operating notes for Claude

Design authority is `SPEC.md`. This file records **environment setup that is
easy to forget and silently degrades quality when missing** — discovered the
hard way during the hill-climb E2E test (2026-06-25).

## Web search wiring (REQUIRED for research loops to cite real sources)

The research loops (`research-to-artifact-loop` etc.) only do *real* web
research when the `web_search` / `web_fetch` tools are live. The gate is
`studio.tools.web_toolkit_available()` **AND** `session.tools_enabled=True`.
When the toolkit is missing, the loop silently runs with **no web search** and
the model **fabricates / cannot ground its citations** — and the (full-window)
scorer correctly caps the score (~0.30). This is the #1 cause of a research
report that "looks fine" but can't reach a high score.

### Prerequisites (all three needed)

1. **`web_toolkit` on the backend venv path.** It lives at
   `/Users/yuxinliu/code/agent-prep/shared/web_toolkit` (a path-package, *not*
   pip-installable — no pyproject). Wire it with a `.pth`:
   ```bash
   SP=$(backend/.venv/bin/python -c "import site; print(site.getsitepackages()[0])")
   echo "/Users/yuxinliu/code/agent-prep/shared" > "$SP/web_toolkit_path.pth"
   ```
   Verify: `backend/.venv/bin/python -c "from studio.tools import web_toolkit_available; print(web_toolkit_available())"` → `True`.

2. **A search backend.** Precedence is **SearXNG → Tavily → DDG**:
   - SearXNG: `SEARXNG_URL` (defaults to `http://localhost:8080`). Confirm up:
     `curl -s -o /dev/null -w "%{http_code}" http://localhost:8080` → `200`.
     NOTE: a reachable SearXNG can still return an **empty** pool for some
     queries; `web_toolkit` falls through to Tavily/DDG on empty (fix 2026-06-25).
     NOTE: if SearXNG is **down** (timeout/connection refused), it now also falls
     through to Tavily/DDG — `_live_structured` wraps the SearXNG call in
     `except SearchError: pass` (fix in `agent-prep/shared/web_toolkit/search.py`,
     2026-06-25). Verify both cases with the health check below.
   - Tavily fallback needs the client installed (venv is **uv**-managed, no pip):
     ```bash
     cd backend && uv pip install tavily-python
     ```
     and `TAVILY_API_KEY` set in the env.
   - DDG fallback needs `ddgs` (`uv pip install ddgs`).

3. **`tools_enabled: True`** in the `POST /session` body (the GUI sends this
   when the tools toggle is on).

### Quick health check (run before any research-quality test)

```bash
cd backend
.venv/bin/python -c "
from studio.tools import web_toolkit_available
from web_toolkit import web_search
print('toolkit:', web_toolkit_available())
print('hits:', len(web_search('agent development loop articles', results=3, use_cache=False)))
"
```
Both should be truthy/non-zero. If `hits=0` on a query SearXNG can't answer,
Tavily/DDG must be installed+configured or the agent will fabricate.

## Local services the backend assumes

- **oMLX `:8000`** — local chat + BGE-M3 embeddings (the `local` embed profile).
- **SearXNG `:8080`** — primary search backend.
- LLM profiles resolve in `studio/backends.py::PROFILES` (`haiku` → Anthropic
  proxy / VibeProxy per env).

## Hill-climb / cross-session improvement

- Run history is SQLite at `backend/tmp/task_runs.db`
  (`task_runs(task_hash, session_id, version, score, weaknesses_json, ...)`).
- `task_hash = sha256(requirement.strip().lower())[:12]` — same requirement
  across sessions shares a hash, so `auto_improve` picks the prior **best**
  (`TaskRunStore.best()`, not `latest()`) and carries its `artifact.md` forward.
- Scorer (`studio/task_runs.py::score_result`) shows the LLM the **full** output
  up to 20K chars (was 3K — a small window hid tail citations and gave falsely
  high scores). `mine_weaknesses_from_outputs` uses a 6K window.

### Testing the SSE run path from a script (gotcha)

The runner records the score in its completion block **as the SSE stream is
drained**. A client that disconnects early (e.g. `urllib` raising
`IncompleteRead`) cancels the server-side StreamingResponse generator → the run
may not record → `task_runs.db` never gets the new version. A browser
`EventSource` (the GUI) holds the connection open and does **not** hit this.
For script-driven E2E, fully drain the SSE stream until the server closes it,
or drive the run through the real GUI.
