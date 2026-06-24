/** Goal Panel — shows current LoopGoal and goal_met verdict. */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

export function GoalPanel() {
  const goalMet = useRunStore((s) => s.goalMet);

  return (
    <PanelShell
      empty={goalMet === null}
      emptyHint="No goal set. Use POST /session/{id}/goal to configure a verifiable stop condition."
    >
      {goalMet !== null && (
        <article className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">goal</span>
            <span className="mono" data-state={goalMet.met ? "done" : undefined}>
              {goalMet.met ? "✓ met" : "not yet met"}
            </span>
          </div>
          <p className="panel-text">{goalMet.end_state}</p>
          <p className="panel-text muted">{goalMet.reason}</p>
          {goalMet.evidence && (
            <pre className="panel-code">{goalMet.evidence.slice(0, 500)}</pre>
          )}
          <div className="panel-meta mono muted">phase: {goalMet.step_id}</div>
        </article>
      )}
    </PanelShell>
  );
}
