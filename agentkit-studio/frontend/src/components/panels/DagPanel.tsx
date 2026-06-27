/** DAG panel (SPEC §5.5.5) — GraphStore node statuses + edge list. */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

function dagState(status: string): string {
  const s = status.toLowerCase();
  if (s.includes("done") || s.includes("complete") || s.includes("success")) return "done";
  if (s.includes("fail") || s.includes("error")) return "error";
  if (s.includes("run") || s.includes("active")) return "running";
  return "pending";
}

export function DagPanel() {
  const dag = useRunStore((s) => s.dag);
  // The pill status must come from the SAME live source the diagram uses
  // (s.phases), not the durable GraphStore's own node.status — those update on
  // different events and drift out of sync ("HUB status ≠ diagram"). Fall back to
  // the durable status only for nodes with no matching live phase.
  const phases = useRunStore((s) => s.phases);
  return (
    <PanelShell empty={dag === null} emptyHint="No durable DAG materialized yet.">
      {dag ? (
        <>
          <div className="panel-row-head">
            <span className="eyebrow">graph {dag.graph_id}</span>
            <span className="mono dim">{dag.edges.length} edges</span>
          </div>
          <div className="dag-nodes">
            {dag.nodes.map((n) => {
              const phase = phases.find((p) => p.id === n.id);
              const state = phase ? phase.state : dagState(n.status);
              return (
                <span key={n.id} className="pill" data-state={state}>
                  <span className="dot" />
                  {n.id}
                </span>
              );
            })}
          </div>
          <div className="dag-edges mono faint">
            {dag.edges.map(([from, to], i) => (
              <span key={i}>
                {from} → {to}
              </span>
            ))}
          </div>
        </>
      ) : null}
    </PanelShell>
  );
}
