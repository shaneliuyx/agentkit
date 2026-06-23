/**
 * Tools panel (M7 Wave 1 — web/tool activity). Lists tool_call/tool_result events:
 * tool name, a compact args summary, result summary + n_results, and any degradation
 * `notice` (e.g. "DDG fallback — SearXNG down") in the muted/amber notice style.
 */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";
import { toolIcon } from "./toolMeta";

/** Compact one-line summary of a tool's args object (e.g. {query: "x"} → query: "x"). */
function summarizeArgs(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) {
    return "—";
  }
  return entries
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}

/**
 * A result reads as a warning when the summary signals a rejected/blocked/escaped
 * operation (e.g. a write_file denied for escaping the workspace). Such summaries
 * render in the amber notice style, same as a degradation `notice`.
 */
const REJECTION_RE = /\b(reject|denied|blocked|refused|escape|forbidden|not allowed)/i;

function isRejected(summary: string | null): boolean {
  return summary !== null && REJECTION_RE.test(summary);
}

export function ToolsPanel() {
  const tools = useRunStore((s) => s.tools);
  return (
    <PanelShell empty={tools.length === 0} emptyHint="No tool calls yet.">
      {tools.map((t, i) => {
        const rejected = isRejected(t.summary);
        return (
          <article key={i} className="card panel-row" data-warn={rejected}>
            <div className="panel-row-head">
              <span className="mono tag">
                {toolIcon(t.tool)} {t.tool}
              </span>
              <span className="mono dim">
                {t.step_id}
                {t.n_results != null ? ` · ${t.n_results} results` : ""}
              </span>
            </div>
            <p className="panel-row-text faint">{summarizeArgs(t.args)}</p>
            {t.summary ? (
              rejected ? (
                <p className="panel-notice">{t.summary}</p>
              ) : (
                <p className="panel-row-text">{t.summary}</p>
              )
            ) : (
              <p className="panel-row-text faint">…awaiting result</p>
            )}
            {t.notice ? <p className="panel-notice">{t.notice}</p> : null}
          </article>
        );
      })}
    </PanelShell>
  );
}
