/** Router panel (SPEC §5.5.7) — per-step difficulty → tier routing decisions. */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

export function RouterPanel() {
  const router = useRunStore((s) => s.router);
  return (
    <PanelShell empty={router.length === 0} emptyHint="No routing decisions yet.">
      {router.map((r, i) => (
        <article key={i} className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">{r.step_id}</span>
            <span className="mono dim">{r.difficulty}</span>
          </div>
          <div className="panel-metric">
            <span className="mono faint">tier</span>
            <span className="panel-metric-val mono">{r.tier}</span>
          </div>
        </article>
      ))}
    </PanelShell>
  );
}
