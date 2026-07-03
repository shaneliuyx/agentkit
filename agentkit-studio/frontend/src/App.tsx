/**
 * App shell (SPEC §3 layout): BackendPanel + RunBar top, TopologyGraph center,
 * TokenMeter + StreamPane side, tabbed PanelDrawer bottom.
 *
 * `?demo=1` replays the canned fixture (SPEC §8 milestone 2 verification) so the
 * full UI can be exercised without a backend.
 */
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { BackendPanel, type BackendPanelHandle } from "./components/config/BackendPanel";
import { ChatPanel } from "./components/hud/ChatPanel";
import { RunActions } from "./components/config/RunActions";
import { LoopConfigPanel } from "./components/config/LoopConfigPanel";
import { TopologyGraph } from "./components/graph/TopologyGraph";
import { TokenMeter } from "./components/hud/TokenMeter";
import { PanelDrawer } from "./components/panels/PanelDrawer";
import { useRunStore } from "./store/runStore";

// Lazy-loaded: pulls react-markdown into its own chunk (only fetched when a run
// finishes and the result window first renders), keeping the initial bundle lean.
const ResultWindow = lazy(() =>
  import("./components/result/ResultWindow").then((m) => ({ default: m.ResultWindow })),
);
import { replayFixture } from "./dev/fixtures";
import type { RunMode } from "./api/types";

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [mode, setMode] = useState<RunMode>("llm");
  const [stale, setStale] = useState(false);
  const backendRef = useRef<BackendPanelHandle>(null);
  const connectingRef = useRef<Promise<string> | null>(null);
  const session = useRunStore((s) => s.session);
  const errorMessage = useRunStore((s) => s.errorMessage);
  const cancelled = useRunStore((s) => s.cancelled);
  const loopSeed = useRunStore((s) => s.loopSeed);
  const loops = useRunStore((s) => s.loops);
  const apply = useRunStore((s) => s.apply);
  const beginRun = useRunStore((s) => s.beginRun);

  // Resolve the seeded loop's human title from the catalog matches; fall back to id.
  const seededTitle = loopSeed
    ? (loops.find((l) => l.id === loopSeed.loop_id)?.title ?? loopSeed.loop_id)
    : null;

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
    setStale(false);
  };

  const handleDirty = () => {
    if (sessionId) setStale(true);
  };

  const connectIfNeeded = useCallback((): Promise<string> => {
    if (sessionId && !stale) return Promise.resolve(sessionId);
    if (connectingRef.current) return connectingRef.current;
    if (!backendRef.current) return Promise.reject(new Error("Backend panel not ready"));
    const p = backendRef.current.connect().finally(() => {
      connectingRef.current = null;
    });
    connectingRef.current = p;
    return p;
  }, [sessionId, stale]);

  return (
    <div className="studio-shell">
      <header className="studio-top">
        <div className="studio-brand">
          <span className="eyebrow">AgentKit</span>
          <h1 className="brand-mark" aria-label="AgentKit Studio">
            <span className="accent">Studio</span>
          </h1>
          {session ? (
            <span className="pill" data-state="done" title={session.llm.model}>
              <span className="dot" />
              {session.llm.label}
            </span>
          ) : null}
        </div>
        <BackendPanel
          ref={backendRef}
          onSession={handleSession}
          onDirty={handleDirty}
          mode={mode}
          disabled={false}
          connected={!!sessionId}
          stale={stale}
        />
        <RunActions sessionId={sessionId} />
        <LoopConfigPanel sessionId={sessionId} />
      </header>

      <main className="studio-main">
        <section className="studio-canvas">
          {seededTitle ? (
            <div className="studio-seed-banner" role="status">
              <span className="mono tag">seeded</span>
              from loop <strong>{seededTitle}</strong>
            </div>
          ) : null}
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
          <ChatPanel
            sessionId={sessionId}
            mode={mode}
            onModeChange={setMode}
            connectIfNeeded={connectIfNeeded}
          />
        </aside>
      </main>

      <section className="studio-drawer">
        <PanelDrawer sessionId={sessionId} />
      </section>

      <Suspense fallback={null}>
        <ResultWindow />
      </Suspense>
    </div>
  );
}
