/**
 * Stream pane (SPEC §6). Appends `text` deltas as they arrive; auto-scrolls to the
 * tail. Per-phase granularity when the backend doesn't stream (SPEC §9).
 */
import { useEffect, useRef } from "react";
import { useRunStore } from "../../store/runStore";
import "./hud.css";

export function StreamPane() {
  const streamText = useRunStore((s) => s.streamText);
  const status = useRunStore((s) => s.status);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [streamText]);

  return (
    <section className="hud-stream panel">
      <header className="hud-head">
        <span className="eyebrow">Stream</span>
        <span className="pill" data-state={status}>
          <span className="dot" />
          {status}
        </span>
      </header>
      <div className="hud-stream-body mono" ref={ref}>
        {streamText ? (
          streamText
        ) : (
          <span className="faint">Streamed model output appears here…</span>
        )}
      </div>
    </section>
  );
}
