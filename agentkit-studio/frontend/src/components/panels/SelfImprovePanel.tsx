/**
 * Self-improve / re-plan panel (SPEC §5.5.2) — assess/StallAssessment rounds plus
 * forwarded log_event agent events.
 */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

export function SelfImprovePanel() {
  const rounds = useRunStore((s) => s.selfimprove);
  const agentEvents = useRunStore((s) => s.agentEvents);
  const empty = rounds.length === 0 && agentEvents.length === 0;

  return (
    <PanelShell empty={empty} emptyHint="No self-improvement rounds yet.">
      {rounds.map((r, i) => (
        <article key={`r${i}`} className="card panel-row" data-stalled={r.stalled}>
          <div className="panel-row-head">
            <span className="mono tag">round {r.round}</span>
            <span className="mono" data-state={r.stalled ? "error" : "done"}>
              {r.stalled ? "stalled" : "progressing"}
            </span>
          </div>
          <p className="panel-row-text">{r.assessment}</p>
          <p className="mono dim">→ {r.action}</p>
        </article>
      ))}
      {agentEvents.map((e, i) => (
        <div key={`e${i}`} className="panel-event mono">
          <span className="dim">[{e.step_id}]</span> {e.name}
        </div>
      ))}
    </PanelShell>
  );
}
