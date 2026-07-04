/** Security spine panel (SPEC §5.5.4) — run_gate Outcomes + sandbox/net_guard. */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

export function outcomeState(outcome: string): string {
  const o = outcome.toLowerCase();
  if (o.includes("allow") || o.includes("pass") || o.includes("ok") || o.includes("accept"))
    return "done";
  if (o.includes("deny") || o.includes("block") || o.includes("fail") || o.includes("reject"))
    return "error";
  return "running";
}

/** Presentation/depth actions (table/list/diagram/format/depth), distinct from scoring gates. */
const QUALITY_GATES = new Set([
  "content_presentation",
  "section_presentation",
  "depth-expansion",
]);

export function isQualityGate(name: string): boolean {
  return QUALITY_GATES.has(name);
}

export function SecurityPanel() {
  const gates = useRunStore((s) => s.gates);
  return (
    <PanelShell empty={gates.length === 0} emptyHint="No gate evaluations yet.">
      {gates.map((g, i) => {
        const quality = isQualityGate(g.name);
        return (
          <article key={i} className="card panel-row" data-kind={quality ? "quality" : undefined}>
            <div className="panel-row-head">
              <span className="mono tag">{g.name}</span>
              <span className="mono" data-state={outcomeState(g.outcome)}>
                {g.outcome}
              </span>
            </div>
            {quality ? <span className="tag">Quality action</span> : null}
            <p className="panel-row-text">{g.detail}</p>
            {g.sandboxed ? <span className="mono faint">sandboxed</span> : null}
          </article>
        );
      })}
    </PanelShell>
  );
}
