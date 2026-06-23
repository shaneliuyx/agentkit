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
  return (
    <PanelShell empty={dag === null} emptyHint="No durable DAG materialized yet.">
      {dag ? (
        <>
          <div className="panel-row-head">
            <span className="eyebrow">graph {dag.graph_id}</span>
            <span className="mono dim">{dag.edges.length} edges</span>
          </div>
          <div className="dag-nodes">
            {dag.nodes.map((n) => (
              <span key={n.id} className="pill" data-state={dagState(n.status)}>
                <span className="dot" />
                {n.id}
              </span>
            ))}
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
