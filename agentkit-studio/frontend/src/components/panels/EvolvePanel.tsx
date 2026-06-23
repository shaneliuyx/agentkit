/** Evolve panel (SPEC §5.5.3) — distill_group rounds with score + delta. */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

export function EvolvePanel() {
  const evolve = useRunStore((s) => s.evolve);
  return (
    <PanelShell empty={evolve.length === 0} emptyHint="No evolution rounds yet.">
      {evolve.map((e, i) => (
        <article key={i} className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">round {e.round}</span>
            <span className="mono">{e.variant}</span>
          </div>
          <div className="panel-metric">
            <span className="panel-metric-val mono">{e.score.toFixed(2)}</span>
            <span
              className="mono"
              data-state={e.delta > 0 ? "done" : e.delta < 0 ? "error" : undefined}
            >
              {e.delta >= 0 ? "+" : ""}
              {e.delta.toFixed(2)}
            </span>
          </div>
        </article>
      ))}
    </PanelShell>
  );
}
