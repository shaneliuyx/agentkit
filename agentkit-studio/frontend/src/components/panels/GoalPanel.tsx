/** Goal Panel — shows configured LoopGoal and goal_met verdict. */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

export function GoalPanel() {
  const goalMet = useRunStore((s) => s.goalMet);
  const configured = useRunStore((s) => s.configuredGoal);

  return (
    <PanelShell
      empty={configured === null && goalMet === null}
      emptyHint="No goal configured. Open ⚙ Loop → Goal tab to set a stop condition."
    >
      {configured !== null && (
        <article className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">configured goal</span>
            <span className="mono muted">{configured.max_turns} turns / {Math.round(configured.timeout_s / 60)}min</span>
          </div>
          <p className="panel-text">{configured.end_state}</p>
          {configured.evidence_cmd && (
            <pre className="panel-code">{configured.evidence_cmd}</pre>
          )}
          {configured.constraints.length > 0 && (
            <ul className="panel-list muted">
              {configured.constraints.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          )}
        </article>
      )}
      {goalMet !== null && (
        <article className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">goal check</span>
            <span className="mono" data-state={goalMet.met ? "done" : undefined}>
              {goalMet.met ? "✓ met" : "✗ not yet met"}
            </span>
          </div>
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
