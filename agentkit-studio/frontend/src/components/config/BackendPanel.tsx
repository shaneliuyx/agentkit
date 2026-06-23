/**
 * Backend selector (SPEC §5.4 / §6). Pulls PROFILES from GET /backends, lets the
 * user pick an LLM + embedder profile or supply a raw OpenAI-compatible override,
 * then POST /session to build the StudioChatClient. Exposes the created session
 * id + chosen mode/budget to the parent via `onSession`.
 */
import { useEffect, useState } from "react";
import { createSession, fetchBackends } from "../../api/sse";
import type {
  BackendProfile,
  BackendSelection,
  RunMode,
} from "../../api/types";
import "./config.css";

interface BackendPanelProps {
  onSession: (sessionId: string, mode: RunMode) => void;
  mode: RunMode;
  disabled: boolean;
}

const RAW = "__raw__";

function selectionFor(profileName: string, raw: RawOverride): BackendSelection {
  if (profileName === RAW) {
    return { raw: { base_url: raw.baseUrl, model: raw.model, api_key: raw.apiKey } };
  }
  return { profile: profileName };
}

interface RawOverride {
  baseUrl: string;
  model: string;
  apiKey: string;
}

export function BackendPanel({ onSession, mode, disabled }: BackendPanelProps) {
  const [profiles, setProfiles] = useState<BackendProfile[]>([]);
  const [embedders, setEmbedders] = useState<BackendProfile[]>([]);
  const [llmProfile, setLlmProfile] = useState<string>("");
  const [embedProfile, setEmbedProfile] = useState<string>("");
  const [ceiling, setCeiling] = useState<string>("");
  const [raw, setRaw] = useState<RawOverride>({ baseUrl: "", model: "", apiKey: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchBackends()
      .then((res) => {
        setProfiles(res.profiles);
        setEmbedders(res.embedders);
        setLlmProfile(res.profiles[0]?.name ?? RAW);
        setEmbedProfile(res.embedders[0]?.name ?? RAW);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load backends");
      });
  }, []);

  const handleConnect = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await createSession({
        llm: selectionFor(llmProfile, raw),
        embed: selectionFor(embedProfile, raw),
        mode,
        budget: { ceiling: ceiling ? Number(ceiling) : null },
      });
      onSession(res.session_id, mode);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Session creation failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="backend-panel">
      <div className="field">
        <label htmlFor="llm-profile">LLM backend</label>
        <select
          id="llm-profile"
          value={llmProfile}
          onChange={(e) => setLlmProfile(e.target.value)}
          disabled={disabled}
        >
          {profiles.map((p) => (
            <option key={p.name} value={p.name}>
              {p.label} — {p.model}
            </option>
          ))}
          <option value={RAW}>Raw override…</option>
        </select>
      </div>

      <div className="field">
        <label htmlFor="embed-profile">Embedder</label>
        <select
          id="embed-profile"
          value={embedProfile}
          onChange={(e) => setEmbedProfile(e.target.value)}
          disabled={disabled}
        >
          {embedders.map((p) => (
            <option key={p.name} value={p.name}>
              {p.label}
            </option>
          ))}
          <option value={RAW}>Raw override…</option>
        </select>
      </div>

      {llmProfile === RAW ? (
        <div className="backend-raw">
          <input
            placeholder="base_url"
            value={raw.baseUrl}
            onChange={(e) => setRaw({ ...raw, baseUrl: e.target.value })}
            disabled={disabled}
          />
          <input
            placeholder="model"
            value={raw.model}
            onChange={(e) => setRaw({ ...raw, model: e.target.value })}
            disabled={disabled}
          />
          <input
            placeholder="api_key"
            type="password"
            value={raw.apiKey}
            onChange={(e) => setRaw({ ...raw, apiKey: e.target.value })}
            disabled={disabled}
          />
        </div>
      ) : null}

      <div className="field backend-ceiling">
        <label htmlFor="ceiling">Budget ceiling ($)</label>
        <input
          id="ceiling"
          inputMode="decimal"
          placeholder="∞"
          value={ceiling}
          onChange={(e) => setCeiling(e.target.value)}
          disabled={disabled}
        />
      </div>

      <button
        className="btn"
        onClick={handleConnect}
        disabled={disabled || busy}
      >
        {busy ? "Connecting…" : "Connect session"}
      </button>

      {error ? <span className="backend-error">{error}</span> : null}
    </section>
  );
}
