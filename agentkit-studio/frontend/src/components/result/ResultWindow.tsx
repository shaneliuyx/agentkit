/**
 * Result window — a dismissable modal that appears when a run finishes,
 * rendering the final result.md as FORMATTED markdown (react-markdown + GFM) and
 * showing where the result was saved (the backend's `done.result_path`, the file
 * in the session workspace). Re-opens for each new finished run.
 */
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useRunStore } from "../../store/runStore";
import "./result.css";

export function ResultWindow() {
  const status = useRunStore((s) => s.status);
  const result = useRunStore((s) => s.result);
  const [dismissed, setDismissed] = useState(false);

  // A new finished run (new `result` object) re-opens the window.
  useEffect(() => {
    if (result) {
      setDismissed(false);
    }
  }, [result]);

  if (status !== "done" || !result || !result.result.trim() || dismissed) {
    return null;
  }

  return (
    <div
      className="result-window"
      role="dialog"
      aria-modal="true"
      aria-label="Run result"
    >
      <div className="result-window-card">
        <header className="result-window-head">
          <span className="mono tag">RESULT</span>
          <button
            type="button"
            className="result-window-close"
            onClick={() => setDismissed(true)}
            aria-label="Close result"
          >
            ×
          </button>
        </header>

        <div className="result-window-body markdown-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {result.result}
          </ReactMarkdown>
        </div>

        {result.result_path ? (
          <footer className="result-window-foot mono">
            <span className="result-window-foot-label">Saved to</span>
            <code className="result-window-path">{result.result_path}</code>
          </footer>
        ) : null}
      </div>
    </div>
  );
}
