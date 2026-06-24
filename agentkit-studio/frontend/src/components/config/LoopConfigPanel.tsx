/**
 * Loop Config Panel — header button → <dialog> modal.
 *
 * Three tabs:
 *   Goal      — configure LoopGoal, POST /session/{id}/goal
 *   Scheduler — view triggers, add a cron entry
 *   Chain     — shortcut note pointing to the Chain panel tab
 */
import { useEffect, useRef, useState } from "react";
import "./config.css";

interface LoopConfigPanelProps {
  sessionId: string | null;
  currentTask?: string;
}

type Tab = "goal" | "scheduler" | "chain";

interface GoalForm {
  end_state: string;
  evidence_cmd: string;
  success_pattern: string;
  constraints: string;
  max_turns: string;
  max_tokens: string;
  timeout_s: string;
}

const GOAL_DEFAULTS: GoalForm = {
  end_state: "",
  evidence_cmd: "",
  success_pattern: "",
  constraints: "",
  max_turns: "25",
  max_tokens: "100000",
  timeout_s: "1800",
};

interface SchedulerTrigger {
  id: string;
  type: string;
  spec: string;
  last_fired: string | null;
  next_fire: string | null;
}

async function _postGoal(
  sessionId: string,
  goal: GoalForm,
  setBusy: (b: boolean) => void,
  setStatus: (s: string | null) => void,
  onSessionNotFound?: () => void,
) {
  setBusy(true);
  setStatus(null);
  try {
    const payload: Record<string, unknown> = { end_state: goal.end_state.trim() };
    if (goal.evidence_cmd.trim()) payload.evidence_cmd = goal.evidence_cmd.trim();
    if (goal.success_pattern.trim()) payload.success_pattern = goal.success_pattern.trim();
    if (goal.constraints.trim())
      payload.constraints = goal.constraints.split("\n").map((s) => s.trim()).filter(Boolean);
    if (goal.max_turns) payload.max_turns = Number(goal.max_turns);
    if (goal.max_tokens) payload.max_tokens = Number(goal.max_tokens);
    if (goal.timeout_s) payload.timeout_s = Number(goal.timeout_s);
    const res = await fetch(`/api/session/${sessionId}/goal`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      setStatus("✓ Goal applied");
    } else {
      const d = await res.json().catch(() => ({}));
      const detail = (d as { detail?: string }).detail ?? "Failed";
      if (res.status === 404 && onSessionNotFound) {
        onSessionNotFound();
        setStatus("✓ Goal saved — session expired, will apply on next connect");
      } else {
        setStatus(`✗ ${detail}`);
      }
    }
  } catch (e: unknown) {
    setStatus(`✗ ${e instanceof Error ? e.message : "Network error"}`);
  } finally {
    setBusy(false);
  }
}

export function LoopConfigPanel({ sessionId, currentTask = "" }: LoopConfigPanelProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<Tab>("goal");

  // ── Goal state ──────────────────────────────────────────────────────────
  const [goal, setGoal] = useState<GoalForm>(GOAL_DEFAULTS);
  const [goalStatus, setGoalStatus] = useState<string | null>(null);
  const [goalBusy, setGoalBusy] = useState(false);
  const [suggestBusy, setSuggestBusy] = useState(false);
  const pendingApply = useRef(false);

  // ── Scheduler state ─────────────────────────────────────────────────────
  const [triggers, setTriggers] = useState<SchedulerTrigger[]>([]);
  const [cronSpec, setCronSpec] = useState("");
  const [cronChain, setCronChain] = useState("");
  const [schedStatus, setSchedStatus] = useState<string | null>(null);

  const open = () => {
    // Pre-fill end_state from the RunBar task if it's a sensible non-error string
    if (currentTask && !goal.end_state && !currentTask.startsWith("✗")) {
      setGoal((g) => ({ ...g, end_state: currentTask }));
    }
    dialogRef.current?.showModal();
  };
  const close = () => dialogRef.current?.close();

  // Close on backdrop click
  const handleDialogClick = (e: React.MouseEvent<HTMLDialogElement>) => {
    if (e.target === dialogRef.current) close();
  };

  // Load scheduler triggers when tab opened
  useEffect(() => {
    if (tab !== "scheduler") return;
    fetch("/api/scheduler")
      .then((r) => r.json())
      .then((d) => setTriggers(d.triggers ?? []))
      .catch(() => setTriggers([]));
  }, [tab]);

  // Auto-apply goal when a session connects if the user had saved one locally
  useEffect(() => {
    if (!sessionId || !pendingApply.current) return;
    pendingApply.current = false;
    void _postGoal(sessionId, goal, setGoalBusy, setGoalStatus);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const handleApplyGoal = () => {
    if (!goal.end_state.trim()) return;
    // Always save locally — user gets instant feedback regardless of session state
    pendingApply.current = true;
    setGoalStatus("✓ Goal saved — will apply on next run");
    // Best-effort push to backend if session exists
    if (sessionId) {
      void _postGoal(sessionId, goal, () => {}, (s) => {
        if (s?.startsWith("✓")) setGoalStatus("✓ Goal applied to session");
      });
    }
  };

  const handleClearGoal = async () => {
    if (!sessionId) return;
    const res = await fetch(`/api/session/${sessionId}/goal`, {
      method: "DELETE",
    }).catch(() => null);
    setGoalStatus(res?.ok ? "✓ Goal cleared" : "✗ Clear failed");
    if (res?.ok) setGoal(GOAL_DEFAULTS);
  };

  const handleSuggest = async () => {
    if (!sessionId || !goal.end_state.trim()) return;
    setSuggestBusy(true);
    setGoalStatus(null);
    try {
      const res = await fetch(
        `/api/session/${sessionId}/goal/suggest`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ end_state: goal.end_state, task: currentTask }),
        }
      );
      if (res.ok) {
        const s = await res.json();
        setGoal((g) => ({
          ...g,
          evidence_cmd:    s.evidence_cmd    ?? g.evidence_cmd,
          success_pattern: s.success_pattern ?? g.success_pattern,
          constraints:     Array.isArray(s.constraints)
                             ? s.constraints.join("\n")
                             : g.constraints,
          max_turns:       s.max_turns    != null ? String(s.max_turns)    : g.max_turns,
          max_tokens:      s.max_tokens   != null ? String(s.max_tokens)   : g.max_tokens,
          timeout_s:       s.timeout_s    != null ? String(s.timeout_s)    : g.timeout_s,
        }));
        setGoalStatus("✓ Parameters suggested — review before applying");
      } else {
        const d = await res.json().catch(() => ({}));
        const detail = (d as { detail?: string }).detail ?? "Suggest failed";
        if (res.status === 404) {
          setGoalStatus("✗ Session expired — click Connect session, then retry Suggest");
        } else {
          setGoalStatus(`✗ ${detail}`);
        }
      }
    } catch (e: unknown) {
      setGoalStatus(`✗ ${e instanceof Error ? e.message : "Network error"}`);
    } finally {
      setSuggestBusy(false);
    }
  };

  const handleAddCron = async () => {
    if (!cronSpec.trim()) return;
    setSchedStatus(null);
    const res = await fetch("/api/scheduler/cron", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec: cronSpec.trim(), chain_id: cronChain.trim() || null }),
    }).catch(() => null);
    if (res?.ok) {
      setSchedStatus("✓ Trigger registered");
      setCronSpec("");
      setCronChain("");
      // Refresh list
      fetch("/api/scheduler")
        .then((r) => r.json())
        .then((d) => setTriggers(d.triggers ?? []))
        .catch(() => null);
    } else {
      setSchedStatus("✗ Registration failed (backend stub)");
    }
  };

  return (
    <>
      <button
        className="btn loop-config-btn"
        onClick={open}
        title="Loop configuration — Goal, Scheduler, Chain"
        aria-label="Open loop configuration"
      >
        ⚙ Loop
      </button>

      <dialog
        ref={dialogRef}
        className="loop-config-dialog"
        onClick={handleDialogClick}
        aria-label="Loop configuration"
      >
        <div className="loop-config-inner">
          <header className="loop-config-header">
            <span className="loop-config-title mono">Loop Config</span>
            <button className="loop-config-close" onClick={close} aria-label="Close">✕</button>
          </header>

          {/* Tab bar */}
          <div className="loop-config-tabs" role="tablist">
            {(["goal", "scheduler", "chain"] as Tab[]).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                className="loop-tab-btn"
                data-active={tab === t}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
          </div>

          {/* ── Goal tab ── */}
          {tab === "goal" && (
            <div className="loop-config-body">
              <p className="loop-config-hint">
                A <strong>LoopGoal</strong> is a verifiable stop condition: the runner calls
                <code> check_goal()</code> after each phase — a pure subprocess, no LLM.
              </p>

              <div className="lc-field">
                <label htmlFor="lc-end-state">End state <span className="req">*</span></label>
                <input
                  id="lc-end-state"
                  placeholder="All billing tests pass"
                  value={goal.end_state}
                  onChange={(e) => setGoal({ ...goal, end_state: e.target.value })}
                />
              </div>

              <div className="lc-field">
                <label htmlFor="lc-evidence-cmd">Evidence command</label>
                <input
                  id="lc-evidence-cmd"
                  className="mono"
                  placeholder="pytest tests/ -q"
                  value={goal.evidence_cmd}
                  onChange={(e) => setGoal({ ...goal, evidence_cmd: e.target.value })}
                />
                <span className="lc-hint">Shell command. Exit 0 = met (if no pattern set).</span>
              </div>

              <div className="lc-field">
                <label htmlFor="lc-pattern">Success pattern (regex)</label>
                <input
                  id="lc-pattern"
                  className="mono"
                  placeholder="\d+ passed"
                  value={goal.success_pattern}
                  onChange={(e) => setGoal({ ...goal, success_pattern: e.target.value })}
                />
                <span className="lc-hint">If set, stdout must match this regex.</span>
              </div>

              <div className="lc-field">
                <label htmlFor="lc-constraints">Constraints (one per line)</label>
                <textarea
                  id="lc-constraints"
                  className="mono"
                  placeholder={"no mutation\nmax 3 files changed"}
                  rows={3}
                  value={goal.constraints}
                  onChange={(e) => setGoal({ ...goal, constraints: e.target.value })}
                />
              </div>

              <div className="lc-row">
                <div className="lc-field lc-field-sm">
                  <label htmlFor="lc-max-turns">Max turns</label>
                  <input
                    id="lc-max-turns"
                    type="number"
                    min={1}
                    value={goal.max_turns}
                    onChange={(e) => setGoal({ ...goal, max_turns: e.target.value })}
                  />
                </div>
                <div className="lc-field lc-field-sm">
                  <label htmlFor="lc-max-tokens">Max tokens</label>
                  <input
                    id="lc-max-tokens"
                    type="number"
                    min={1000}
                    value={goal.max_tokens}
                    onChange={(e) => setGoal({ ...goal, max_tokens: e.target.value })}
                  />
                </div>
                <div className="lc-field lc-field-sm">
                  <label htmlFor="lc-timeout">Timeout (s)</label>
                  <input
                    id="lc-timeout"
                    type="number"
                    min={10}
                    value={goal.timeout_s}
                    onChange={(e) => setGoal({ ...goal, timeout_s: e.target.value })}
                  />
                </div>
              </div>

              <div className="lc-actions">
                <button
                  className="btn"
                  onClick={handleSuggest}
                  disabled={suggestBusy || !sessionId || !goal.end_state.trim()}
                  title={
                    !sessionId
                      ? "Connect a session first — Suggest calls the LLM"
                      : !goal.end_state.trim()
                      ? "Type an end state above first"
                      : "Ask the LLM to suggest evidence_cmd, success_pattern and limits"
                  }
                >
                  {suggestBusy ? "Thinking…" : "✨ Suggest"}
                </button>
                <button
                  className="btn btn-primary"
                  onClick={handleApplyGoal}
                  disabled={goalBusy || !goal.end_state.trim()}
                >
                  {goalBusy ? "Applying…" : "Apply goal"}
                </button>
                <button
                  className="btn"
                  onClick={handleClearGoal}
                  disabled={!sessionId}
                >
                  Clear
                </button>
                {goalStatus && (
                  <span
                    className="lc-status mono"
                    data-ok={goalStatus.startsWith("✓")}
                  >
                    {goalStatus}
                  </span>
                )}
              </div>
              {!sessionId && (
                <p className="lc-warn">Connect a session first.</p>
              )}
            </div>
          )}

          {/* ── Scheduler tab ── */}
          {tab === "scheduler" && (
            <div className="loop-config-body">
              <p className="loop-config-hint">
                Register cron or webhook triggers that fire a <strong>LoopChain</strong> automatically.
              </p>

              <div className="lc-field">
                <label htmlFor="lc-cron-spec">Cron expression</label>
                <input
                  id="lc-cron-spec"
                  className="mono"
                  placeholder="0 * * * *  (hourly)"
                  value={cronSpec}
                  onChange={(e) => setCronSpec(e.target.value)}
                />
              </div>
              <div className="lc-field">
                <label htmlFor="lc-cron-chain">Chain ID (optional)</label>
                <input
                  id="lc-cron-chain"
                  className="mono"
                  placeholder="my-chain"
                  value={cronChain}
                  onChange={(e) => setCronChain(e.target.value)}
                />
              </div>
              <div className="lc-actions">
                <button className="btn btn-primary" onClick={handleAddCron} disabled={!cronSpec.trim()}>
                  Register trigger
                </button>
                {schedStatus && (
                  <span className="lc-status mono" data-ok={schedStatus.startsWith("✓")}>
                    {schedStatus}
                  </span>
                )}
              </div>

              {triggers.length > 0 ? (
                <table className="lc-table mono">
                  <thead>
                    <tr><th>id</th><th>type</th><th>spec</th><th>next fire</th></tr>
                  </thead>
                  <tbody>
                    {triggers.map((t) => (
                      <tr key={t.id}>
                        <td>{t.id}</td>
                        <td>{t.type}</td>
                        <td>{t.spec}</td>
                        <td>{t.next_fire ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="loop-config-hint muted">No triggers registered yet.</p>
              )}
            </div>
          )}

          {/* ── Chain tab ── */}
          {tab === "chain" && (
            <div className="loop-config-body">
              <p className="loop-config-hint">
                A <strong>LoopChain</strong> is a DAG of loops. Each spec declares its{" "}
                <code>depends_on</code> predecessors; outputs flow downstream automatically.
              </p>
              <p className="loop-config-hint">
                Use the <strong>Chain</strong> tab in the panel drawer below to compose and run
                chains interactively via JSON editor.
              </p>
              <div className="lc-chain-example">
                <pre className="panel-code">{`{
  "specs": [
    { "name": "research",    "depends_on": [] },
    { "name": "synthesize",  "depends_on": ["research"] },
    { "name": "deploy",      "depends_on": ["synthesize"] }
  ],
  "initial_ctx": { "task": "ship billing v2" }
}`}</pre>
              </div>
              <button className="btn" onClick={close}>
                Open Chain tab ↓
              </button>
            </div>
          )}
        </div>
      </dialog>
    </>
  );
}
