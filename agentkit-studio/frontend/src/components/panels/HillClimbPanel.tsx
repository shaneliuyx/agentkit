/** Hill Climbing Dashboard — live epoch timeline + cross-session version history. */
import { useEffect, useState } from "react";
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

interface HistoryRun {
  version: number;
  session_id: string;
  score: number;
  weaknesses: string[];
  artifact_path: string;
}

export function HillClimbPanel() {
  const hillClimb = useRunStore((s) => s.hillClimb);
  const configured = useRunStore((s) => s.configuredHillClimb);
  const currentTaskHash = useRunStore((s) => s.currentTaskHash);

  const [history, setHistory] = useState<HistoryRun[]>([]);
  const [historyHash, setHistoryHash] = useState<string | null>(null);

  useEffect(() => {
    if (!currentTaskHash || currentTaskHash === historyHash) return;
    setHistoryHash(currentTaskHash);
    fetch(`/api/task-runs/${currentTaskHash}`)
      .then((r) => r.json())
      .then((d) => setHistory(d.runs ?? []))
      .catch(() => {});
  }, [currentTaskHash, historyHash]);

  const statusColor = (s: string) =>
    s === "improving" || s === "converged" ? "done" : s === "plateau" ? "warn" : undefined;

  return (
    <PanelShell
      empty={configured === null && hillClimb.length === 0 && history.length === 0}
      emptyHint="No hill-climb config. Open ⚙ Loop → Hill Climb tab to set score metric and thresholds."
    >
      {/* Config card shown before any epochs arrive */}
      {configured !== null && hillClimb.length === 0 && (
        <article className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">hill climb config</span>
            <span className="mono muted">{configured.max_epochs} epochs max</span>
          </div>
          <div className="panel-meta mono">
            <span className="muted">metric:</span> {configured.score_metric}
            {"  "}
            <span className="muted">min Δ:</span> {configured.min_improvement}
            {"  "}
            {configured.auto_improve && (
              <span className="tag" data-state="done">auto-improve on</span>
            )}
          </div>
        </article>
      )}

      {/* Live epoch cards from the current run */}
      {hillClimb.map((h, i) => (
        <article key={i} className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">epoch {h.epoch}</span>
            <span className="mono" data-state={statusColor(h.status)}>
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

      {/* Cross-session version history — appears once ≥2 runs recorded */}
      {history.length > 1 && (
        <>
          <div className="panel-section-head mono muted" style={{ padding: "6px 0 2px" }}>
            version history ({history.length} runs)
          </div>
          {history.map((r) => {
            const isLatest = r.version === history[history.length - 1].version;
            const prev = history[r.version - 2];
            const delta = prev != null ? r.score - prev.score : null;
            return (
              <article key={r.version} className="card panel-row" data-state={isLatest ? "done" : undefined}>
                <div className="panel-row-head">
                  <span className="mono tag">v{r.version}</span>
                  {isLatest && <span className="mono tag" data-state="done">latest</span>}
                </div>
                <div className="panel-metric">
                  <span className="panel-metric-val mono">{r.score.toFixed(3)}</span>
                  {delta !== null && (
                    <span className="mono" data-state={delta > 0 ? "done" : delta < 0 ? "error" : undefined}>
                      {delta >= 0 ? "+" : ""}{delta.toFixed(3)}
                    </span>
                  )}
                </div>
                {r.weaknesses.length > 0 && (
                  <div className="panel-tags">
                    {r.weaknesses.slice(0, 3).map((w, wi) => (
                      <span key={wi} className="mono tag muted">{w.slice(0, 55)}</span>
                    ))}
                  </div>
                )}
              </article>
            );
          })}
        </>
      )}
    </PanelShell>
  );
}
