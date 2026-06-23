/**
 * Token HUD (SPEC §6 + §7). Renders "{input} in / {output} out · {total} total",
 * prefixed `~` when estimated. The whole meter switches to the amber estimated
 * hue the moment the run becomes estimated — and never reverts (sticky in store).
 * Budget gauge shows spent/ceiling, red on exceeded.
 */
import { useEffect, useRef, useState } from "react";
import { useRunStore } from "../../store/runStore";
import { countUp } from "../graph/nodeAnim";
import "./hud.css";

function useCountUp(value: number): number {
  const [display, setDisplay] = useState(value);
  const prev = useRef(value);
  useEffect(() => {
    const stop = countUp(prev.current, value, setDisplay);
    prev.current = value;
    return stop;
  }, [value]);
  return display;
}

function formatCost(n: number): string {
  return `$${n.toFixed(3)}`;
}

export function TokenMeter() {
  const tokens = useRunStore((s) => s.tokens);
  const budget = useRunStore((s) => s.budget);

  const total = useCountUp(tokens.total);
  const prefix = tokens.estimated ? "~" : "";

  const gaugePct =
    budget && budget.ceiling
      ? Math.min(100, (budget.spent / budget.ceiling) * 100)
      : 0;

  return (
    <section className="hud-meter" data-estimated={tokens.estimated}>
      <header className="hud-head">
        <span className="eyebrow">Token Meter</span>
        {tokens.estimated ? (
          <span className="hud-estimated-flag" title="Backend reports no usage telemetry; counts are estimated.">
            ~ estimated
          </span>
        ) : (
          <span className="hud-exact-flag">exact</span>
        )}
      </header>

      <div className="hud-total mono">
        <span className="hud-prefix">{prefix}</span>
        {total.toLocaleString()}
        <span className="hud-total-unit"> total</span>
      </div>

      <div className="hud-split mono">
        <span>
          <span className="hud-num">{prefix}{tokens.input.toLocaleString()}</span> in
        </span>
        <span className="hud-sep">/</span>
        <span>
          <span className="hud-num">{prefix}{tokens.output.toLocaleString()}</span> out
        </span>
      </div>

      {budget ? (
        <div className="hud-budget" data-exceeded={budget.exceeded}>
          <div className="hud-budget-row">
            <span className="eyebrow">Budget</span>
            <span className="mono">
              {formatCost(budget.spent)}
              {budget.ceiling != null ? ` / ${formatCost(budget.ceiling)}` : " / ∞"}
            </span>
          </div>
          <div className="hud-gauge" aria-hidden="true">
            <div className="hud-gauge-fill" style={{ width: `${gaugePct}%` }} />
          </div>
          {budget.exceeded ? (
            <span className="hud-exceeded">budget exceeded</span>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
