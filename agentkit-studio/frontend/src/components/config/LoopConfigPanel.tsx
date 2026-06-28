/**
 * Loop Config Panel — header button → <dialog> modal.
 *
 * Three tabs:
 *   Goal      — configure LoopGoal, POST /session/{id}/goal
 *   Scheduler — view triggers, add a cron entry
 *   Chain     — shortcut note pointing to the Chain panel tab
 */
import { useEffect, useRef, useState } from "react";
import { useRunStore } from "../../store/runStore";
import type { RubricConfig } from "../../api/types";
import "./config.css";

interface LoopConfigPanelProps {
  sessionId: string | null;
  currentTask?: string;
}

type Tab = "goal" | "scheduler" | "chain" | "hill_climb" | "rubric";

const TAB_IDS: Tab[] = ["goal", "scheduler", "chain", "hill_climb", "rubric"];

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
  const setConfiguredGoal = useRunStore((s) => s.setConfiguredGoal);
  const setConfiguredHillClimb = useRunStore((s) => s.setConfiguredHillClimb);
  const setConfiguredRubric = useRunStore((s) => s.setConfiguredRubric);
  const setSchedulerTriggers = useRunStore((s) => s.setSchedulerTriggers);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [tab, setTab] = useState<Tab>("goal");

  // Roving-tabindex arrow-key navigation for the tablist (WAI-ARIA tabs pattern).
  const handleTabKey = (e: React.KeyboardEvent<HTMLButtonElement>, idx: number) => {
    const last = TAB_IDS.length - 1;
    let next = idx;
    if (e.key === "ArrowRight") next = idx === last ? 0 : idx + 1;
    else if (e.key === "ArrowLeft") next = idx === 0 ? last : idx - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    else return;
    e.preventDefault();
    setTab(TAB_IDS[next]);
    tabRefs.current[next]?.focus();
  };

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

  // ── Hill Climb state ───────────────────────────────────────────────────
  const [hcMetric, setHcMetric] = useState("score");
  const [hcMinDelta, setHcMinDelta] = useState("0.01");
  const [hcMaxEpochs, setHcMaxEpochs] = useState("10");
  const [hcAutoImprove, setHcAutoImprove] = useState(false);
  const [deliverablePath, setDeliverablePath] = useState("");
  const [useLatestPrior, setUseLatestPrior] = useState(true);
  const [minTasksPerAgent, setMinTasksPerAgent] = useState(3);
  const [maxTasksPerAgent, setMaxTasksPerAgent] = useState(5);
  const [maxAgents, setMaxAgents] = useState(5);
  const [hcStatus, setHcStatus] = useState<string | null>(null);
  const [schedStatus, setSchedStatus] = useState<string | null>(null);

  // ── Rubric state (criterion weights + deliverable template) ──────────────
  const [rubricWeights, setRubricWeights] = useState<Record<string, number>>({});
  const [rubricTemplate, setRubricTemplate] = useState<string[]>([]);
  const [rubricLoaded, setRubricLoaded] = useState(false);
  const [rubricStatus, setRubricStatus] = useState<string | null>(null);

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

  // Seed the rubric panel from the same defaults the backend scorer uses, once.
  useEffect(() => {
    if (tab !== "rubric" || rubricLoaded) return;
    fetch("/api/rubric/defaults")
      .then((r) => r.json())
      .then((d) => {
        setRubricWeights(d.weights ?? {});
        setRubricTemplate(d.template ?? []);
        setRubricLoaded(true);
      })
      .catch(() => setRubricStatus("✗ Could not load rubric defaults"));
  }, [tab, rubricLoaded]);

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
    setConfiguredGoal({
      end_state: goal.end_state.trim(),
      evidence_cmd: goal.evidence_cmd.trim(),
      success_pattern: goal.success_pattern.trim(),
      constraints: goal.constraints.split("\n").map((s) => s.trim()).filter(Boolean),
      max_turns: Number(goal.max_turns) || 25,
      max_tokens: Number(goal.max_tokens) || 100_000,
      timeout_s: Number(goal.timeout_s) || 1800,
    });
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
        .then((d) => { setTriggers(d.triggers ?? []); setSchedulerTriggers({ triggers: d.triggers ?? [] }); })
        .catch(() => null);
    } else {
      setSchedStatus("✗ Registration failed (backend stub)");
    }
  };

  return (
    <>
      <button
        className="btn btn-sm loop-config-btn"
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
        aria-labelledby="loop-config-title"
      >
        <div className="loop-config-inner">
          <header className="loop-config-header">
            <h2 id="loop-config-title" className="loop-config-title mono">Loop Config</h2>
            <button className="btn btn-icon btn-ghost loop-config-close" onClick={close} aria-label="Close"><span aria-hidden="true">✕</span></button>
          </header>

          {/* Tab bar */}
          <div className="loop-config-tabs" role="tablist" aria-label="Loop configuration sections">
            {TAB_IDS.map((t, i) => (
              <button
                key={t}
                ref={(el) => { tabRefs.current[i] = el; }}
                id={`lc-tab-${t}`}
                role="tab"
                aria-selected={tab === t}
                aria-controls={`lc-tabpanel-${t}`}
                tabIndex={tab === t ? 0 : -1}
                className="loop-tab-btn"
                data-active={tab === t}
                onClick={() => setTab(t)}
                onKeyDown={(e) => handleTabKey(e, i)}
              >
                {t}
              </button>
            ))}
          </div>

          {/* ── Goal tab ── */}
          {tab === "goal" && (
            <div className="loop-config-body" role="tabpanel" id="lc-tabpanel-goal" aria-labelledby="lc-tab-goal">
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
                    role="status"
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
            <div className="loop-config-body" role="tabpanel" id="lc-tabpanel-scheduler" aria-labelledby="lc-tab-scheduler">
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
                  <span className="lc-status mono" role="status" data-ok={schedStatus.startsWith("✓")}>
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
            <div className="loop-config-body" role="tabpanel" id="lc-tabpanel-chain" aria-labelledby="lc-tab-chain">
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

          {/* ── Hill Climb tab ── */}
          {tab === "hill_climb" && (
            <div className="loop-config-body" role="tabpanel" id="lc-tabpanel-hill_climb" aria-labelledby="lc-tab-hill_climb">
              <p className="loop-config-hint">
                Configure DGM hill-climbing: score metric, acceptance threshold, and epoch cap.
              </p>
              <div className="lc-field">
                <label htmlFor="lc-hc-metric">Score metric</label>
                <input
                  id="lc-hc-metric"
                  className="mono"
                  placeholder="score"
                  value={hcMetric}
                  onChange={(e) => setHcMetric(e.target.value)}
                />
              </div>
              <div className="lc-row">
                <div className="lc-field">
                  <label htmlFor="lc-hc-delta">Min improvement</label>
                  <input
                    id="lc-hc-delta"
                    className="mono"
                    type="number"
                    step="0.001"
                    value={hcMinDelta}
                    onChange={(e) => setHcMinDelta(e.target.value)}
                  />
                </div>
                <div className="lc-field">
                  <label htmlFor="lc-hc-epochs">Max epochs</label>
                  <input
                    id="lc-hc-epochs"
                    className="mono"
                    type="number"
                    value={hcMaxEpochs}
                    onChange={(e) => setHcMaxEpochs(e.target.value)}
                  />
                </div>
              </div>
              <div className="lc-field lc-checkbox">
                <label>
                  <input
                    type="checkbox"
                    checked={hcAutoImprove}
                    onChange={(e) => setHcAutoImprove(e.target.checked)}
                  />
                  Auto-improve (seed next run from prior artifact + weaknesses)
                </label>
              </div>
              <hr className="lc-divider" />
              <p className="lc-section-label">Deliverable</p>
              <div className="lc-field">
                <label htmlFor="lc-hc-deliverable">Path (leave empty for auto)</label>
                <input
                  id="lc-hc-deliverable"
                  className="mono"
                  placeholder="/path/to/report.md"
                  value={deliverablePath}
                  onChange={(e) => setDeliverablePath(e.target.value)}
                />
              </div>
              <div className="lc-field lc-checkbox">
                <label>
                  <input type="radio" name="lc-deliverable-mode" checked={useLatestPrior} onChange={() => setUseLatestPrior(true)} />
                  Use latest prior artifact (hill-climb)
                </label>
              </div>
              <div className="lc-field lc-checkbox">
                <label>
                  <input type="radio" name="lc-deliverable-mode" checked={!useLatestPrior} onChange={() => setUseLatestPrior(false)} />
                  Create new artifact each run
                </label>
              </div>
              <hr className="lc-divider" />
              <p className="lc-section-label">Agent Sizing</p>
              <div className="lc-row">
                <div className="lc-field">
                  <label>Min tasks per agent: <strong>{minTasksPerAgent}</strong></label>
                  <input
                    type="range" min={1} max={10}
                    aria-label="Min tasks per agent"
                    value={minTasksPerAgent}
                    onChange={(e) => setMinTasksPerAgent(Number(e.target.value))}
                  />
                </div>
                <div className="lc-field">
                  <label>Max tasks per agent: <strong>{maxTasksPerAgent}</strong></label>
                  <input
                    type="range" min={1} max={10}
                    aria-label="Max tasks per agent"
                    value={maxTasksPerAgent}
                    onChange={(e) => setMaxTasksPerAgent(Number(e.target.value))}
                  />
                </div>
                <div className="lc-field">
                  <label>Max agents (cap): <strong>{maxAgents}</strong></label>
                  <input
                    type="range" min={1} max={10}
                    aria-label="Max agents (cap)"
                    value={maxAgents}
                    onChange={(e) => setMaxAgents(Number(e.target.value))}
                  />
                </div>
              </div>
              {hcStatus && <p className="loop-config-hint">{hcStatus}</p>}
              {!sessionId && (
                <p className="loop-config-hint">
                  Connect a session first — hill-climb config is sent to the live
                  session (applying before connect would silently not persist).
                </p>
              )}
              <div className="lc-actions">
                <button
                  className="btn btn-primary"
                  disabled={!sessionId}
                  onClick={async () => {
                    const cfg = {
                      score_metric: hcMetric.trim() || "score",
                      min_improvement: parseFloat(hcMinDelta) || 0.01,
                      max_epochs: parseInt(hcMaxEpochs) || 10,
                      auto_improve: hcAutoImprove,
                      deliverable_path: deliverablePath.trim() || null,
                      use_latest_prior: useLatestPrior,
                      min_tasks_per_agent: minTasksPerAgent,
                      max_tasks_per_agent: maxTasksPerAgent,
                      max_agents: maxAgents,
                    };
                    setConfiguredHillClimb(cfg);
                    if (sessionId) {
                      try {
                        const res = await fetch(
                          `/api/session/${sessionId}/hill-climb`,
                          {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify(cfg),
                          }
                        );
                        setHcStatus(res.ok ? "✓ Hill climb config applied" : "✗ Failed to apply");
                      } catch {
                        setHcStatus("✗ Network error");
                      }
                    }
                  }}
                >
                  Apply hill climb config
                </button>
              </div>
            </div>
          )}

          {/* ── Rubric tab ── */}
          {tab === "rubric" && (
            <div className="loop-config-body" role="tabpanel" id="lc-tabpanel-rubric" aria-labelledby="lc-tab-rubric">
              <p className="loop-config-hint">
                The <strong>rubric</strong> is the epoch keep/discard gate's scoring
                standard (DESIGN §14.2) — deterministic and model-free. Tune the
                per-criterion weights and edit the deliverable <strong>template</strong>;
                the template both steers report generation and drives the{" "}
                <code>structure</code> score.
              </p>

              <p className="lc-section-label">Criterion weights</p>
              {Object.keys(rubricWeights).length === 0 ? (
                <p className="loop-config-hint muted">Loading defaults…</p>
              ) : (
                Object.entries(rubricWeights).map(([k, v]) => (
                  <div className="lc-field" key={k}>
                    <label htmlFor={`lc-rw-${k}`}>
                      {k.replace(/_/g, " ")}: <strong>{v.toFixed(2)}</strong>
                    </label>
                    <input
                      id={`lc-rw-${k}`}
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={v}
                      onChange={(e) =>
                        setRubricWeights({ ...rubricWeights, [k]: Number(e.target.value) })
                      }
                    />
                  </div>
                ))
              )}
              <span className="lc-hint">
                Weights are L1-normalized on apply — only their relative size matters.
              </span>

              <hr className="lc-divider" />
              <p className="lc-section-label">Deliverable template (required sections)</p>
              {rubricTemplate.map((section, i) => (
                <div className="lc-row" key={i}>
                  <div className="lc-field" style={{ flex: 1 }}>
                    <input
                      className="mono"
                      aria-label={`Deliverable section ${i + 1}`}
                      value={section}
                      placeholder="Section heading"
                      onChange={(e) =>
                        setRubricTemplate(
                          rubricTemplate.map((s, j) => (j === i ? e.target.value : s)),
                        )
                      }
                    />
                  </div>
                  <button
                    className="btn"
                    aria-label={`Remove section ${section}`}
                    onClick={() =>
                      setRubricTemplate(rubricTemplate.filter((_, j) => j !== i))
                    }
                  >
                    ✕
                  </button>
                </div>
              ))}
              <button
                className="btn"
                onClick={() => setRubricTemplate([...rubricTemplate, ""])}
              >
                + Add section
              </button>

              {rubricStatus && (
                <p className="lc-status mono" role="status" data-ok={rubricStatus.startsWith("✓")}>
                  {rubricStatus}
                </p>
              )}
              {!sessionId && (
                <p className="loop-config-hint">
                  Saved locally now — connect a session, then re-apply so the rubric
                  reaches the live run.
                </p>
              )}
              <div className="lc-actions">
                <button
                  className="btn btn-primary"
                  onClick={async () => {
                    const cfg: RubricConfig = {
                      weights: rubricWeights,
                      template: rubricTemplate.map((s) => s.trim()).filter(Boolean),
                    };
                    setConfiguredRubric(cfg);
                    setRubricStatus("✓ Rubric saved — will apply on next run");
                    if (sessionId) {
                      try {
                        const res = await fetch(`/api/session/${sessionId}/rubric`, {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify(cfg),
                        });
                        setRubricStatus(
                          res.ok ? "✓ Rubric applied to session" : "✗ Failed to apply",
                        );
                      } catch {
                        setRubricStatus("✗ Network error");
                      }
                    }
                  }}
                >
                  Apply rubric
                </button>
              </div>
            </div>
          )}
        </div>
      </dialog>
    </>
  );
}
