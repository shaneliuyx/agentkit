/** Memory panel (SPEC §5.5.1) — MemoryStore entries with tier + recall score. */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

export function MemoryPanel() {
  const memory = useRunStore((s) => s.memory);
  const notice = useRunStore((s) => s.memoryNotice);

  // A degradation notice (SPEC §9) is content too — only empty when there is
  // neither an entry nor a notice.
  const empty = memory.length === 0 && notice.length === 0;

  return (
    <PanelShell empty={empty} emptyHint="No memory writes yet.">
      {notice ? <p className="panel-notice">{notice}</p> : null}
      {memory.map((m) => (
        <article key={m.id} className="card panel-row">
          <div className="panel-row-head">
            <span className="mono tag">{m.tier}</span>
            <span className="mono dim">score {m.score.toFixed(2)}</span>
          </div>
          <p className="panel-row-text">{m.text}</p>
        </article>
      ))}
    </PanelShell>
  );
}
