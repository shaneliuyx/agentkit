/** Automation Scheduler Panel — surfaces runtime/scheduler.py trigger list. */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

export function SchedulerPanel() {
  const triggers = useRunStore((s) => s.schedulerTriggers?.triggers ?? []);

  return (
    <PanelShell
      empty={triggers.length === 0}
      emptyHint="No triggers yet. Open ⚙ Loop → Scheduler tab to register a cron trigger."
    >
      {triggers.map((t, i) => (
        <article key={i} className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">{t.type}</span>
            <span className="mono muted">{t.id}</span>
          </div>
          {t.spec && (
            <div className="panel-meta mono">
              <span className="muted">spec:</span> {t.spec}
            </div>
          )}
          <div className="panel-meta mono">
            {t.last_fired && <><span className="muted">last:</span> {t.last_fired} </>}
            {t.next_fire && <><span className="muted">next:</span> {t.next_fire}</>}
          </div>
        </article>
      ))}
    </PanelShell>
  );
}
