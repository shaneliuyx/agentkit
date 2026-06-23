/**
 * App shell (SPEC §3 layout): BackendPanel + RunBar top, TopologyGraph center,
 * TokenMeter + StreamPane side, tabbed PanelDrawer bottom.
 *
 * `?demo=1` replays the canned fixture (SPEC §8 milestone 2 verification) so the
 * full UI can be exercised without a backend.
 */
import { useEffect, useState } from "react";
import { BackendPanel } from "./components/config/BackendPanel";
import { RunBar } from "./components/config/RunBar";
import { TopologyGraph } from "./components/graph/TopologyGraph";
import { TokenMeter } from "./components/hud/TokenMeter";
import { StreamPane } from "./components/hud/StreamPane";
import { PanelDrawer } from "./components/panels/PanelDrawer";
import { useRunStore } from "./store/runStore";
import { replayFixture } from "./dev/fixtures";
import type { RunMode } from "./api/types";

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [mode, setMode] = useState<RunMode>("auto");
  const session = useRunStore((s) => s.session);
  const errorMessage = useRunStore((s) => s.errorMessage);
  const cancelled = useRunStore((s) => s.cancelled);
  const apply = useRunStore((s) => s.apply);
  const beginRun = useRunStore((s) => s.beginRun);

  // Demo replay: ?demo=1
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("demo") !== "1") {
      return;
    }
    beginRun("demo-session", "auto");
    return replayFixture(apply);
  }, [apply, beginRun]);

  const handleSession = (id: string, m: RunMode) => {
    setSessionId(id);
    setMode(m);
  };

  return (
    <div className="studio-shell">
      <header className="studio-top">
        <div className="studio-brand">
          <span className="eyebrow">AgentKit</span>
          <span className="brand-mark">
            <span className="accent">Studio</span>
          </span>
          {session ? (
            <span className="pill" data-state="done" title={session.llm.model}>
              <span className="dot" />
              {session.llm.label}
            </span>
          ) : null}
        </div>
        <BackendPanel onSession={handleSession} mode={mode} disabled={false} />
        <RunBar sessionId={sessionId} mode={mode} onModeChange={setMode} />
      </header>

      <main className="studio-main">
        <section className="studio-canvas">
          <TopologyGraph />
          {cancelled ? (
            <div className="studio-toast studio-toast-cancelled" role="status">
              Run cancelled — showing partial results.
            </div>
          ) : null}
          {errorMessage ? (
            <div className="studio-toast" role="alert">
              {errorMessage}
            </div>
          ) : null}
        </section>
        <aside className="studio-side">
          <TokenMeter />
          <StreamPane />
        </aside>
      </main>

      <section className="studio-drawer">
        <PanelDrawer />
      </section>
    </div>
  );
}
