/** Security spine panel (SPEC §5.5.4) — run_gate Outcomes + sandbox/net_guard. */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

function outcomeState(outcome: string): string {
  const o = outcome.toLowerCase();
  if (o.includes("allow") || o.includes("pass") || o.includes("ok")) return "done";
  if (o.includes("deny") || o.includes("block") || o.includes("fail")) return "error";
  return "running";
}

export function SecurityPanel() {
  const gates = useRunStore((s) => s.gates);
  return (
    <PanelShell empty={gates.length === 0} emptyHint="No gate evaluations yet.">
      {gates.map((g, i) => (
        <article key={i} className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">{g.name}</span>
            <span className="mono" data-state={outcomeState(g.outcome)}>
              {g.outcome}
            </span>
          </div>
          <p className="panel-row-text">{g.detail}</p>
          {g.sandboxed ? <span className="mono faint">sandboxed</span> : null}
        </article>
      ))}
    </PanelShell>
  );
}
