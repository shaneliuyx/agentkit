/**
 * Backend selector (SPEC §5.4 / §6). Pulls PROFILES from GET /backends, lets the
 * user pick an LLM + embedder profile or supply a raw OpenAI-compatible override,
 * then POST /session to build the StudioChatClient. Exposes the created session
 * id + chosen mode/budget to the parent via `onSession`.
 */
import { forwardRef, useEffect, useImperativeHandle, useState } from "react";
import { createSession, fetchBackends } from "../../api/sse";
import type {
  BackendProfile,
  BackendSelection,
  RunMode,
} from "../../api/types";
import "./config.css";

export interface BackendPanelHandle {
  connect: () => Promise<string>;
}

interface BackendPanelProps {
  onSession: (sessionId: string, mode: RunMode) => void;
  onDirty: () => void;
  mode: RunMode;
  disabled: boolean;
  connected: boolean;
  stale: boolean;
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

export const BackendPanel = forwardRef<BackendPanelHandle, BackendPanelProps>(
  function BackendPanel(
    { onSession, onDirty, mode, disabled, connected, stale },
    ref,
  ) {
  const [profiles, setProfiles] = useState<BackendProfile[]>([]);
  const [embedders, setEmbedders] = useState<BackendProfile[]>([]);
  const [llmProfile, setLlmProfile] = useState<string>("");
  const [embedProfile, setEmbedProfile] = useState<string>("");
  const [ceiling, setCeiling] = useState<string>("");
  const [raw, setRaw] = useState<RawOverride>({ baseUrl: "", model: "", apiKey: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Captured at the moment a connect actually succeeds — must NOT be derived
  // live from the current dropdown selection, or changing the LLM profile
  // post-connect (which flips the button to "Reconnect to apply changes")
  // would make the status pill falsely claim the new profile is already live.
  const [connectedLabel, setConnectedLabel] = useState<string>("");

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

  const doConnect = async (): Promise<string> => {
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
      setConnectedLabel(
        llmProfile === RAW
          ? raw.model || "raw override"
          : (profiles.find((p) => p.name === llmProfile)?.label ?? llmProfile),
      );
      return res.session_id;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Session creation failed");
      throw e;
    } finally {
      setBusy(false);
    }
  };

  useImperativeHandle(ref, () => ({ connect: doConnect }), [
    llmProfile,
    embedProfile,
    ceiling,
    raw,
    mode,
  ]);

  return (
    <section className="backend-panel">
      <div className="field">
        <label htmlFor="llm-profile">LLM backend</label>
        <select
          id="llm-profile"
          value={llmProfile}
          onChange={(e) => {
            setLlmProfile(e.target.value);
            onDirty();
          }}
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
          onChange={(e) => {
            setEmbedProfile(e.target.value);
            onDirty();
          }}
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
            aria-label="Base URL"
            placeholder="base_url"
            value={raw.baseUrl}
            onChange={(e) => {
              setRaw({ ...raw, baseUrl: e.target.value });
              onDirty();
            }}
            disabled={disabled}
          />
          <input
            aria-label="Model"
            placeholder="model"
            value={raw.model}
            onChange={(e) => {
              setRaw({ ...raw, model: e.target.value });
              onDirty();
            }}
            disabled={disabled}
          />
          <input
            aria-label="API key"
            placeholder="api_key"
            type="password"
            value={raw.apiKey}
            onChange={(e) => {
              setRaw({ ...raw, apiKey: e.target.value });
              onDirty();
            }}
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
          onChange={(e) => {
            setCeiling(e.target.value);
            onDirty();
          }}
          disabled={disabled}
        />
      </div>

      <div className="backend-connect-row">
        <button
          className="btn"
          onClick={() => {
            doConnect().catch(() => {});
          }}
          disabled={disabled || busy}
        >
          {busy
            ? "Connecting…"
            : stale
              ? "Reconnect to apply changes"
              : connected
                ? "Reconnect"
                : "Connect session"}
        </button>
        {connected && !busy ? (
          <span className="backend-status">Connected — {connectedLabel}</span>
        ) : null}
      </div>

      {error ? (
        <span className="backend-error" role="alert">
          {error}
        </span>
      ) : null}
    </section>
  );
  },
);
