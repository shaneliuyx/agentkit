/** Hill Climbing Dashboard — DGM epoch-by-epoch score timeline. */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

export function HillClimbPanel() {
  const hillClimb = useRunStore((s) => s.hillClimb);

  return (
    <PanelShell
      empty={hillClimb.length === 0}
      emptyHint="No hill-climbing rounds yet. Prompt evolution runs when hill_climb_from_traces() is called."
    >
      {hillClimb.map((h, i) => (
        <article key={i} className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">epoch {h.epoch}</span>
            <span
              className="mono"
              data-state={
                h.status === "accept" ? "done" : h.status === "escalate" ? "warn" : "error"
              }
            >
              {h.status}
            </span>
          </div>
          <div className="panel-metric">
            <span className="panel-metric-val mono">{h.score.toFixed(3)}</span>
            <span className="mono" data-state={h.delta > 0 ? "done" : h.delta < 0 ? "error" : undefined}>
              {h.delta >= 0 ? "+" : ""}{h.delta.toFixed(3)}
            </span>
          </div>
          {h.note && <p className="panel-text muted">{h.note}</p>}
          {h.weaknesses.length > 0 && (
            <div className="panel-tags">
              {h.weaknesses.slice(0, 3).map((w, wi) => (
                <span key={wi} className="mono tag muted">{w.slice(0, 60)}</span>
              ))}
            </div>
          )}
        </article>
      ))}
    </PanelShell>
  );
}
