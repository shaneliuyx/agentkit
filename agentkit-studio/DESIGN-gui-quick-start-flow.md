# DESIGN — GUI quick-start flow (connect → type → send)

**Status:** proposed, not implemented. Codex-reviewed twice (2026-07-03):
round 1 `.omc/artifacts/ask/codex-review-these-5-gui-ux-optimization-proposals-*.md`,
round 2 (this doc) `.omc/artifacts/ask/codex-review-this-design-doc-for-agentkit-studio-frontend-gui-quic-*.md`.
Round 2 verdict: **#4 is structurally sound as designed. #5 is only half-fixed**
(see its section below) — do not ship #5 as originally shaped.

## Problem

Driving a live test through the AgentKit Studio GUI via browser automation
surfaced real friction in the connect → configure → run flow. The current
sequence to run one task:

1. Select LLM backend from the combobox.
2. Click **Connect session** (fires `POST /api/session`, no visible
   confirmation — the button text never changes).
3. Click the **llm** mode toggle (default is `auto`, the numbered-list
   decomposer — wrong for free-form prose tasks, which is the common case).
4. Type the requirement into the chat textarea.
5. Click **Send**.
6. Separately, open **⚙ Loop → hill_climb** tab, toggle auto-improve, click
   **Apply hill climb config** — a fully disconnected flow from 1–5.

Two textareas sit in the same visual area (`LoopsPanel`'s "Describe the task
to find a matching loop…" `<input>` and the real chat `<textarea
aria-label="New message">`), which caused a real wrong-target error during
testing — not just a hypothetical confusion.

## Goals

Cut the steps above without changing what the backend actually does —
this is a frontend sequencing/affordance change, not a new capability.

## Non-goals

- No change to `POST /api/session`, `POST /api/run/{id}`, or any backend
  contract.
- No change to the hill-climb config schema or the `Apply hill climb config`
  flow's semantics — only where an equivalent quick-toggle writes to.
- Not touching `mode="auto"`'s decomposer behavior itself, only its default.

## Changes, in ship order (per Codex review)

### 1. Connected-state feedback (ship first, no side effects)

`App.tsx` already tracks a session pill via `useRunStore().session`, but the
`BackendPanel` button text stays "Connect session" regardless of state.

- While `busy` (in-flight): "Connecting…" (already implemented).
- Once `sessionId` is set: show a separate, non-clickable status label
  "Connected — {backend label}" NEXT TO the button, and relabel the button
  itself to "Reconnect" (Codex round-2: a single label that is both a status
  readout and a click target hides the action — split the two).

### 6a. `/session`'s backend `mode` fallback (decision needed before #3 ships)

`POST /session` on the backend defaults a missing `mode` to `"auto"`
(`app.py`). If the frontend is changed to always send an explicit `mode`
(current behavior — `BackendPanel` always passes `mode` from state), this
fallback is dead code for the GUI path and can be left alone. Document that
decision explicitly rather than leaving it silently inconsistent with the
new frontend default. Do not change the backend default as part of #3.

### 2. Visually distinguish the two textareas (CSS/placement only)

- Keep both `aria-label`s exactly as-is (`"Loop search requirement"` on the
  `LoopsPanel` input, `"New message"` on the `ChatPanel` textarea) — no
  accessibility regression.
- Give the chat textarea a distinct border/weight. **Dropped per Codex
  round-2:** moving loop-search into a popover overbuilds a CSS/layout-level
  confusion bug — border/placement alone is sufficient, do not add new
  interaction/accessibility surface for this.
- Do not rely on color alone.

### 3. Default planning mode to `llm`

- `auto` (numbered-list decomposer) is the narrow case; free-form prose is
  the common one.
- Both defaults must change together or they can disagree after a reset:
  - `App.tsx:28` (`useState<RunMode>("auto")` or equivalent initial value).
  - `runStore.ts:185` (store's initial `mode` state).
- Leave the `?demo=1` path's explicit `beginRun("demo-session", "auto")`
  call untouched — it's an explicit choice, not a default.
- Side effect (accepted): users who relied on the old default now see `llm`
  first. The toggle stays visible and one click away.

### 4. Auto-connect — redesigned, not "on focus"

Original proposal (auto-connect on first keystroke/focus) has a real
structural problem: `ChatPanel` only receives `sessionId` as a prop, not a
`connect()` callback — it cannot self-connect without either lifting the
connect action up or threading a callback down. Firing on bare *focus* is
also too eager (tab-through, screen-reader navigation, or automation landing
on the textarea would create sessions).

**Redesigned shape:**

- Lift a `connectIfNeeded(): Promise<string>` (returns the session id) from
  `BackendPanel`'s `handleConnect` up to `App`, passed down to `ChatPanel`
  alongside `sessionId`.
- Trigger only on the first non-empty edit of the message textarea, or on
  Send itself if no session exists yet — never on focus alone.
- Guard with an in-flight ref so a fast double-trigger (e.g. paste + Enter)
  can't fire two `POST /api/session` calls.
- `POST /api/session` does not call the model — it only builds clients and
  registers an in-memory session (`session.py:137`, `app.py:97`) — so this
  is not token-costly, but a stale/duplicate session is still a real bug to
  guard against structurally, not just by convention.
- **Failure semantics (Codex round-2, was unspecified):** if
  `connectIfNeeded()` rejects during a Send attempt, no run starts and no
  user message is appended to the thread — the textarea keeps the typed
  text and a connection-error surfaces where `BackendPanel`'s own connect
  errors already surface today, not as a new failed chat bubble.
- **Stale-session interaction (Codex round-2, was unspecified):** if item #6
  has marked the current session stale, Send must call reconnect (not reuse
  the stale `sessionId`) before running. This makes #6 a hard prerequisite
  for #4, not just an ordering preference — see Rollout order below.

### 5. Inline hill-climb toggle — only half-fixed, needs its own design pass

Original proposal (a bare checkbox next to Send) forks state: hill-climb
config today only reaches the backend when **Apply hill climb config** is
clicked (`LoopConfigPanel.tsx:134,687`). An inline checkbox that doesn't
write through the same path would show "auto-improve on" while the actual
session still runs the old config.

**Codex round-2 verdict: the redesign below fixes the inline toggle's own
fork, but not the fork that already exists inside the modal.** The modal's
real editable source today is still local component state (`hcMetric`,
`hcMinDelta`, `hcMaxEpochs`, `hcAutoImprove`), only written to the store on
"Apply" click (`LoopConfigPanel.tsx:134,687`). Making the inline control
read/write the store's `configuredHillClimb` object does not, by itself,
keep the modal in sync — the modal itself needs refactoring to read/write
that same canonical object directly, not just gain a new inline sibling.
**Do not schedule this item as ready to ship in sequence; it needs its own
design pass first** (see Rollout order below).

**Redesigned shape (still requires the modal refactor above before it's complete):**

- The inline control reads/writes the *same* `configuredHillClimb` object
  the modal uses — not a separate local boolean.
- The modal (`LoopConfigPanel.tsx`) is refactored so `hcMetric`, `hcMinDelta`,
  `hcMaxEpochs`, `hcAutoImprove` are no longer local-only state shadowing the
  store until "Apply" — both surfaces read/write the one canonical object.
- If a session already exists: flipping the inline toggle immediately POSTs
  to the existing hill-climb config endpoint with current defaults plus the
  changed `auto_improve` field.
- If no session exists yet: store locally, then **POST to
  `/session/{id}/hill-climb` immediately after `connectIfNeeded()` resolves**
  (not folded into the session's initial creation payload — that would
  contradict this doc's own non-goal of leaving `POST /api/session`'s
  contract unchanged).

## New item — surfaced by review, not in the original proposal

### 6. Stale-session-after-backend-change feedback

Today, changing the LLM/embed/budget/raw fields *after* a successful
connect does not invalidate the existing `sessionId` — the user can believe
they switched backend while every subsequent run still uses the old one.
Add: once any of those fields change post-connect, mark the session stale
and change the button to **"Reconnect to apply changes"** (distinct from
the plain "Connect session" initial state).

## Rollout order

1. #1 connected-state feedback
2. #2 visual textarea distinction
3. #3 default mode → `llm`
4. #6 stale-session feedback (small, same area as #1) — **hard dependency for #4**
5. #4 auto-connect (redesigned shape) — must ship after #6, not just conventionally later:
   Send's stale-session reconnect path (see #4 above) has nothing to check
   against until #6 exists.
6. #5 inline hill-climb toggle — **not ready to schedule.** Codex round-2:
   only half-fixed as designed (the modal itself still needs a state-source
   refactor, see #5 above). Give it its own design pass — covering the modal
   refactor's blast radius on `LoopConfigPanel.tsx` — before adding it to a
   rollout sequence.

Items 1–3 (+6) are additive UI/default changes with no state-management
rework. Item 4 requires the `connectIfNeeded` lift and depends on #6 landing
first. Item 5 is excluded from this rollout until its own design pass lands.
