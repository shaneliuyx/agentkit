/**
 * Loop Doctor panel (M8). Renders the four health checks emitted by the backend's
 * `loopdoctor` frame — bounded, material_checks, safe_actions, clear_stopping —
 * each with a status pill and, when not passing, the advisory `fix` text.
 *
 * Repairs are SUGGESTIONS: the `fix` is rendered as a `.panel-notice` advisory
 * (amber), never as an action button.
 */
import type { LoopDoctorStatus } from "../../api/types";
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

/** Map a check status to the shared `data-state` token (green / amber / red). */
export function statusState(status: LoopDoctorStatus): string {
  switch (status) {
    case "pass":
      return "done";
    case "warn":
      return "warn";
    case "fail":
      return "error";
  }
}

export function LoopDoctorPanel() {
  const checks = useRunStore((s) => s.loopDoctor);
  return (
    <PanelShell empty={checks.length === 0} emptyHint="No Loop Doctor checks yet.">
      {checks.map((c) => (
        <article key={c.name} className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">{c.name}</span>
            <span className="mono" data-state={statusState(c.status)}>
              {c.status}
            </span>
          </div>
          {c.status !== "pass" ? (
            <p className="panel-notice">{c.fix}</p>
          ) : null}
        </article>
      ))}
    </PanelShell>
  );
}
