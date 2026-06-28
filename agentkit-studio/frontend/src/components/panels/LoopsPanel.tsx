/**
 * Loops panel (M7 Wave 1 — the 8th panel). Search the loop-library catalog by
 * requirement, render matches as cards, and seed the active session's next run
 * from a chosen loop. Once a `loop_seed` event arrives, the store's `loopSeed`
 * drives the "seeded from <title>" banner on the graph (see App).
 */
import { useEffect, useState } from "react";
import { fetchLoops, fetchSkills, seedLoop } from "../../api/sse";
import { useRunStore } from "../../store/runStore";
import { PanelShell } from "./PanelShell";

interface LoopsPanelProps {
  sessionId: string | null;
}

interface PathSkill {
  name: string;
  description: string;
}

export function LoopsPanel({ sessionId }: LoopsPanelProps) {
  const loops = useRunStore((s) => s.loops);
  const loopSeed = useRunStore((s) => s.loopSeed);
  const applyLoops = useRunStore((s) => s.apply);

  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [seeding, setSeeding] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [skills, setSkills] = useState<PathSkill[]>([]);

  // M9: surface the 5 path skills so the available loop paths are visible. Best
  // effort — failure leaves the section hidden rather than disrupting the panel.
  useEffect(() => {
    let cancelled = false;
    fetchSkills()
      .then((res) => {
        if (!cancelled) {
          setSkills(res.skills);
        }
      })
      .catch(() => {
        /* skills are an optional affordance; ignore fetch failure */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleFind = async () => {
    if (query.trim().length === 0) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetchLoops(query.trim());
      // Reuse the reducer so a GET and a live `loops` SSE frame land identically.
      applyLoops({
        type: "loops",
        session_id: sessionId ?? "",
        ts: Date.now() / 1000,
        payload: res,
      });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Loop search failed");
    } finally {
      setBusy(false);
    }
  };

  const handleSeed = async (loopId: string) => {
    if (!sessionId) {
      setError("Connect a session before seeding a loop.");
      return;
    }
    setSeeding(loopId);
    setError(null);
    try {
      await seedLoop(sessionId, loopId);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Seed failed");
    } finally {
      setSeeding(null);
    }
  };

  const empty = loops.length === 0;

  return (
    <PanelShell empty={false} emptyHint="">
      <div className="loops-search">
        <input
          className="loops-input"
          placeholder="Describe the task to find a matching loop…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              handleFind();
            }
          }}
          aria-label="Loop search requirement"
        />
        <button className="btn" onClick={handleFind} disabled={busy}>
          {busy ? "Finding…" : "Find loops"}
        </button>
      </div>

      {error ? <p className="panel-notice" role="alert">{error}</p> : null}

      {loopSeed ? (
        <p className="loops-seeded mono">
          Run seeded from loop <strong>{loopSeed.loop_id}</strong> ·{" "}
          {loopSeed.steps.length} steps
        </p>
      ) : null}

      {empty ? (
        <p className="panel-empty">No loop matches yet — search above.</p>
      ) : (
        loops.map((m) => (
          <article key={m.id} className="card panel-row">
            <div className="panel-row-head">
              <span className="mono tag">{m.trigger}</span>
              <span className="mono dim">match {m.score.toFixed(2)}</span>
            </div>
            <h2 className="loops-title">{m.title}</h2>
            <p className="panel-row-text">{m.summary}</p>
            {m.keywords.length > 0 ? (
              <p className="mono faint loops-keywords">{m.keywords.join(" · ")}</p>
            ) : null}
            <a className="mono dim loops-link" href={m.url} target="_blank" rel="noreferrer">
              catalog ↗
            </a>
            <button
              type="button"
              className="btn btn-sm loops-seed-btn"
              onClick={() => handleSeed(m.id)}
              disabled={seeding !== null || !sessionId}
            >
              {seeding === m.id ? "Seeding…" : "Seed this run"}
            </button>
          </article>
        ))
      )}

      {skills.length > 0 ? (
        <section className="loops-skills">
          <p className="mono tag">path skills</p>
          {skills.map((s) => (
            <p key={s.name} className="loops-skill" title={s.description}>
              <span className="mono dim">{s.name}</span>
              <span className="panel-row-text"> — {s.description}</span>
            </p>
          ))}
        </section>
      ) : null}
    </PanelShell>
  );
}
