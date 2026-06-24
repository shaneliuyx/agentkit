/**
 * Loop Chain Composer — task description → LLM-suggested chain spec → run.
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
  const [task, setTask] = useState("");
  const [spec, setSpec] = useState(EXAMPLE);
  const [running, setRunning] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [suggestStatus, setSuggestStatus] = useState<string | null>(null);

  const handleSuggest = async () => {
    if (!task.trim()) return;
    setSuggesting(true);
    setSuggestStatus(null);
    setError(null);
    try {
      const res = await fetch("/api/chain/suggest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: task.trim() }),
      });
      if (res.ok) {
        const data = await res.json() as { specs: unknown[]; initial_ctx: unknown };
        setSpec(JSON.stringify(data, null, 2));
        setSuggestStatus("✓ Chain spec generated");
      } else {
        const d = await res.json().catch(() => ({})) as { detail?: string };
        setSuggestStatus(`✗ ${d.detail ?? "Suggest failed"}`);
      }
    } catch (e: unknown) {
      setSuggestStatus(`✗ ${e instanceof Error ? e.message : "Network error"}`);
    } finally {
      setSuggesting(false);
    }
  };

  const handleRun = async () => {
    setError(null);
    setRunning(true);
    try {
      const parsed = JSON.parse(spec);
      const res = await fetch("/api/chain/run", {
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
        <label className="panel-label mono">Task description</label>
        <input
          className="chain-task-input mono"
          placeholder="Describe the multi-step task…"
          value={task}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void handleSuggest(); }}
        />
        <div className="chain-actions">
          <button
            className="btn btn-primary"
            onClick={handleSuggest}
            disabled={suggesting || !task.trim()}
            title={!task.trim() ? "Enter a task description first" : "Let the LLM generate a chain spec"}
          >
            {suggesting ? "Generating…" : "✦ Suggest chain"}
          </button>
          {suggestStatus && (
            <span className="lc-status mono" data-ok={suggestStatus.startsWith("✓")}>
              {suggestStatus}
            </span>
          )}
        </div>

        <label className="panel-label mono" style={{ marginTop: "12px" }}>Chain Spec (JSON)</label>
        <textarea
          className="chain-editor mono"
          value={spec}
          onChange={(e) => setSpec(e.target.value)}
          rows={12}
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
