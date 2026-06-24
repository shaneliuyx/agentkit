/**
 * Loop Chain Composer — JSON editor for LoopChain specs + live result view.
 *
 * Phase 1: JSON textarea + Run button + ChainPayload event list.
 * Phase 2 (future): drag-and-drop node graph.
 */
import { useState } from "react";
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

const EXAMPLE = JSON.stringify(
  {
    specs: [
      { name: "research", description: "Research the topic", depends_on: [] },
      { name: "synthesize", description: "Synthesize into a report", depends_on: ["research"] },
    ],
    initial_ctx: { task: "Analyze loop engineering patterns" },
  },
  null,
  2
);

export function ChainComposerPanel() {
  const chainResults = useRunStore((s) => s.chainResults);
  const [spec, setSpec] = useState(EXAMPLE);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleRun = async () => {
    setError(null);
    setRunning(true);
    try {
      const parsed = JSON.parse(spec);
      const res = await fetch("http://localhost:8000/chain/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(parsed),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError((data as { detail?: string }).detail ?? "Chain run failed");
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setRunning(false);
    }
  };

  return (
    <PanelShell empty={false} emptyHint="">
      <div className="chain-composer">
        <label className="panel-label mono">Chain Spec (JSON)</label>
        <textarea
          className="chain-editor mono"
          value={spec}
          onChange={(e) => setSpec(e.target.value)}
          rows={10}
          spellCheck={false}
        />
        {error && (
          <div className="panel-error mono" role="alert">{error}</div>
        )}
        <button className="run-btn" onClick={handleRun} disabled={running}>
          {running ? "Running…" : "Run Chain"}
        </button>
      </div>
      {chainResults.length > 0 && (
        <section>
          <div className="panel-label mono muted" style={{ padding: "8px 12px" }}>
            Chain Results
          </div>
          {chainResults.map((r, i) => (
            <article key={i} className="card panel-row">
              <div className="panel-row-head">
                <span className="mono tag">{r.spec_name}</span>
                <span
                  className="mono"
                  data-state={r.status === "done" ? "done" : r.skipped ? "warn" : "error"}
                >
                  {r.skipped ? "skipped" : r.status}
                </span>
              </div>
              {r.output_summary && (
                <p className="panel-text muted">{r.output_summary.slice(0, 200)}</p>
              )}
            </article>
          ))}
        </section>
      )}
    </PanelShell>
  );
}
