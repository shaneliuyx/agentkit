/**
 * The SSE event contract — TS side of SPEC §4.
 *
 * This file mirrors the backend `events.py`. It is the single source of truth
 * the whole frontend builds on: the store reducer switches on `StudioEvent.type`,
 * and every component reads typed payload fields off the union. Field names are
 * byte-exact against the SPEC §4 table — do not rename without changing both halves.
 *
 * Every frame on the wire is `{ type, session_id, ts, payload }`. We model that as
 * a discriminated union keyed on `type` so the reducer is an exhaustive `switch`.
 */

// ── Shared sub-shapes referenced by multiple payloads ──────────────────────

export type TopologyKind = "SINGLE" | "STAR" | "MESH" | "PIPELINE";

/** A planned phase (SPEC §4 `plan`). */
export interface PlanStep {
  id: string;
  description: string;
  depends_on: string[];
  role: string;
  difficulty: string;
}

/** Per-step topology assignment (SPEC §4 `topology`). */
export interface TopologyStep {
  id: string;
  topology: TopologyKind;
}

/** A render-graph node as emitted by the backend's derived `graph` frame. */
export interface GraphNodePayload {
  id: string;
  kind: string;
  phase: string;
  label: string;
  state: string;
}

/** A render-graph edge as emitted by the backend's derived `graph` frame. */
export interface GraphEdgePayload {
  from: string;
  to: string;
  kind: string;
}

/** Cumulative token totals carried alongside each per-step `token` frame. */
export interface CumulativeTokens {
  input: number;
  output: number;
  total: number;
  estimated: boolean;
}

export interface MemoryEntry {
  id: string;
  text: string;
  tier: string;
  score: number;
}

export interface VerifyFinding {
  claim: string;
  supported: boolean;
  sources: string[];
}

export interface DagNode {
  id: string;
  status: string;
}

// ── Per-event payloads (one per SPEC §4 row) ───────────────────────────────

export interface SessionPayload {
  llm: { label: string; model: string };
  embed: { label: string; model: string };
  mode: string;
}

export interface PlanPayload {
  task: string;
  steps: PlanStep[];
}

export interface TopologyPayload {
  steps: TopologyStep[];
}

export interface GraphPayload {
  nodes: GraphNodePayload[];
  edges: GraphEdgePayload[];
}

export interface PhaseStartPayload {
  step_id: string;
}

export interface AgentEventPayload {
  step_id: string;
  name: string;
  data: Record<string, unknown>;
}

export interface TokenPayload {
  step_id: string;
  input: number;
  output: number;
  total: number;
  estimated: boolean;
  cumulative: CumulativeTokens;
}

export interface TextPayload {
  step_id: string;
  delta: string;
}

export interface PhaseDonePayload {
  step_id: string;
  topology: TopologyKind;
  n_agents: number;
  tokens: number;
  wall_s: number;
  output: string;
}

export interface BudgetPayload {
  spent: number;
  ceiling: number | null;
  exceeded: boolean;
}

export interface RouterPayload {
  step_id: string;
  difficulty: string;
  tier: string;
}

export interface MemoryPayload {
  entries: MemoryEntry[];
  /**
   * Graceful-degradation message (SPEC §9). Non-empty when the memory subsystem is
   * disabled/unhealthy (e.g. "no embedder configured — memory panel disabled" when
   * oMLX/Qdrant are down); `""` when healthy.
   */
  notice: string;
}

export interface SelfImprovePayload {
  round: number;
  stalled: boolean;
  assessment: string;
  action: string;
}

export interface EvolvePayload {
  round: number;
  score: number;
  delta: number;
  variant: string;
}

export interface GatePayload {
  name: string;
  outcome: string;
  detail: string;
  sandboxed: boolean;
}

export interface DagPayload {
  graph_id: string;
  nodes: DagNode[];
  edges: [string, string][];
}

export interface VerifyPayload {
  findings: VerifyFinding[];
  uncited: string[];
}

export interface DonePayload {
  total_tokens: number;
  input: number;
  output: number;
  estimated: boolean;
  wall_s: number;
  result: string;
  /** True when the run was stopped via /cancel (partial result) rather than finishing. */
  cancelled: boolean;
}

export interface ErrorPayload {
  message: string;
  where: string;
}

// ── The discriminated union ────────────────────────────────────────────────

/** Base envelope every frame shares. */
interface Frame<T extends string, P> {
  type: T;
  session_id: string;
  ts: number;
  payload: P;
}

export type StudioEvent =
  | Frame<"session", SessionPayload>
  | Frame<"plan", PlanPayload>
  | Frame<"topology", TopologyPayload>
  | Frame<"graph", GraphPayload>
  | Frame<"phase_start", PhaseStartPayload>
  | Frame<"agent_event", AgentEventPayload>
  | Frame<"token", TokenPayload>
  | Frame<"text", TextPayload>
  | Frame<"phase_done", PhaseDonePayload>
  | Frame<"budget", BudgetPayload>
  | Frame<"router", RouterPayload>
  | Frame<"memory", MemoryPayload>
  | Frame<"selfimprove", SelfImprovePayload>
  | Frame<"evolve", EvolvePayload>
  | Frame<"gate", GatePayload>
  | Frame<"dag", DagPayload>
  | Frame<"verify", VerifyPayload>
  | Frame<"done", DonePayload>
  | Frame<"error", ErrorPayload>;

export type StudioEventType = StudioEvent["type"];

// ── REST shapes (SPEC §5.4) ────────────────────────────────────────────────

export interface BackendProfile {
  name: string;
  label: string;
  kind: string;
  model: string;
  endpoint: string;
}

export interface BackendsResponse {
  profiles: BackendProfile[];
  embedders: BackendProfile[];
}

export type RunMode = "auto" | "llm";

/** Either a named profile or a raw endpoint override. */
export type BackendSelection =
  | { profile: string }
  | { raw: { base_url: string; model: string; api_key: string } };

export interface SessionRequest {
  llm: BackendSelection;
  embed: BackendSelection;
  mode: RunMode;
  budget: { ceiling: number | null };
}

export interface SessionResponse {
  session_id: string;
}
