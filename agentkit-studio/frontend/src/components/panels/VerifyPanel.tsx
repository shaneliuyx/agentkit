/** Verification panel (SPEC §5.5.6) — verify() findings + uncited claims. */
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

export function VerifyPanel() {
  const verify = useRunStore((s) => s.verify);
  const empty = verify === null;

  return (
    <PanelShell empty={empty} emptyHint="No verification run yet.">
      {verify ? (
        <>
          {verify.findings.map((f, i) => (
            <article key={i} className="card panel-row">
              <div className="panel-row-head">
                <span className="mono" data-state={f.supported ? "done" : "error"}>
                  {f.supported ? "supported" : "unsupported"}
                </span>
                <span className="mono faint">
                  {f.sources.length} src
                </span>
              </div>
              <p className="panel-row-text">{f.claim}</p>
            </article>
          ))}
          {verify.uncited.length > 0 ? (
            <div className="card panel-row" data-warn="true">
              <h2 className="eyebrow">Uncited</h2>
              {verify.uncited.map((c, i) => (
                <p key={i} className="panel-row-text">
                  {c}
                </p>
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </PanelShell>
  );
}
