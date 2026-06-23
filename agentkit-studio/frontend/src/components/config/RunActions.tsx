/**
 * Post-run actions (M9). Once a run finishes (`status === "done"`), exposes
 * "Export as loop": fetches the serialized loop from the backend and triggers a
 * browser download (Blob → <a download="loop-{sessionId}.json">).
 *
 * Lives in the top bar beside the RunBar so it surfaces exactly when the run is
 * complete and the session id is known.
 */
import { useState } from "react";
import { exportLoop } from "../../api/sse";
import { useRunStore } from "../../store/runStore";
import "./config.css";

interface RunActionsProps {
  sessionId: string | null;
}

/** Download arbitrary JSON as a named file via a transient object URL. */
function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function RunActions({ sessionId }: RunActionsProps) {
  const status = useRunStore((s) => s.status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The export endpoint serializes a finished run; only offer it once done.
  if (status !== "done" || !sessionId) {
    return null;
  }

  const handleExport = async () => {
    setBusy(true);
    setError(null);
    try {
      const { loop } = await exportLoop(sessionId);
      downloadJson(`loop-${sessionId}.json`, loop);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="run-actions">
      <button className="btn" onClick={handleExport} disabled={busy}>
        {busy ? "Exporting…" : "Export as loop"}
      </button>
      {error ? (
        <span className="run-actions-error mono" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}
