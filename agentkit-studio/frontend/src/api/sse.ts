/**
 * SSE client + REST helpers (SPEC §5.4).
 *
 * `openRunStream` connects an EventSource to `/api/run/{session_id}?requirement=...`,
 * parses each frame into a typed `StudioEvent`, and hands it to `onEvent`. The
 * Vite dev proxy maps `/api` → the FastAPI backend, so all paths stay same-origin.
 */
import type {
  BackendsResponse,
  SessionRequest,
  SessionResponse,
  StudioEvent,
} from "./types";

const API_BASE = "/api";

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unexpected error";
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`POST ${path} failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

/** GET /api/backends → PROFILES menu for the BackendPanel dropdown. */
export async function fetchBackends(): Promise<BackendsResponse> {
  const res = await fetch(`${API_BASE}/backends`);
  if (!res.ok) {
    throw new Error(`GET /backends failed: ${res.status}`);
  }
  return (await res.json()) as BackendsResponse;
}

/** POST /api/session → builds the StudioChatClient + embedder, returns session_id. */
export function createSession(req: SessionRequest): Promise<SessionResponse> {
  return postJson<SessionResponse>("/session", req);
}

/** POST /api/cancel/{session_id} → cooperative cancel via interrupt_state. */
export function cancelRun(sessionId: string): Promise<{ cancelled: boolean }> {
  return postJson<{ cancelled: boolean }>(`/cancel/${sessionId}`, {});
}

export interface RunStreamHandle {
  /** Tear down the EventSource. Idempotent. */
  close: () => void;
}

export interface RunStreamCallbacks {
  onEvent: (event: StudioEvent) => void;
  onError?: (message: string) => void;
  onOpen?: () => void;
}

/**
 * Open the run stream. The backend emits one JSON frame per `message` event.
 * Returns a handle whose `close()` detaches the source (does NOT cancel the
 * server run — use `cancelRun` for cooperative cancel).
 */
export function openRunStream(
  sessionId: string,
  requirement: string,
  callbacks: RunStreamCallbacks,
): RunStreamHandle {
  const url = `${API_BASE}/run/${sessionId}?requirement=${encodeURIComponent(
    requirement,
  )}`;
  const source = new EventSource(url);

  source.onopen = () => callbacks.onOpen?.();

  source.onmessage = (msg: MessageEvent<string>) => {
    try {
      const event = JSON.parse(msg.data) as StudioEvent;
      callbacks.onEvent(event);
    } catch (error: unknown) {
      callbacks.onError?.(`Bad SSE frame: ${getErrorMessage(error)}`);
    }
  };

  source.onerror = () => {
    // EventSource fires onerror on normal stream close too; surface it but the
    // caller decides whether the run actually finished (via a `done` event).
    callbacks.onError?.("SSE connection error or closed");
  };

  return {
    close: () => source.close(),
  };
}
