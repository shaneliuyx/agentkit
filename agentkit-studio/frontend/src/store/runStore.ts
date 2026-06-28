/**
 * The run store (SPEC §6 "State"). A Zustand store whose `apply` reducer is a
 * flat switch on `event.type`. Every update is immutable (spread / new arrays):
 * mutation here would break React Flow + anime.js change detection downstream.
 *
 * Token honesty (SPEC §7): once any frame reports `estimated`, the run is sticky
 * `~estimated` and we never un-set it.
 */
import { create } from "zustand";
import type {
  AgentEventPayload,
  BudgetPayload,
  CumulativeTokens,
  DagPayload,
  EvolvePayload,
  GatePayload,
  GraphEdgePayload,
  GraphNodePayload,
  LoopDoctorCheck,
  MemoryEntry,
  PlanStep,
  LoopMatch,
  LoopSeedPayload,
  RouterPayload,
  RubricConfig,
  RunMode,
  SelfImprovePayload,
  SessionPayload,
  StudioEvent,
  ToolCallPayload,
  ToolResultPayload,
  TopologyKind,
  VerifyFinding,
} from "../api/types";
import { toTopologyKind } from "../api/types";
import type {
  GoalMetPayload,
  HillClimbPayload,
  SchedulerPayload,
  ChainPayload,
} from "../api/types";

/** A web/tool activity entry — a tool_call optionally paired with its tool_result. */
export interface ToolActivity {
  step_id: string;
  tool: string;
  args: Record<string, unknown>;
  summary: string | null;
  n_results: number | null;
  notice: string;
  /** True once a tool_result reports the jail refused the op (path escape). */
  rejected: boolean;
}

export type RunStatus = "idle" | "connecting" | "running" | "done" | "error";

/** A phase as the store tracks it — plan fields enriched by live run state. */
export interface PhaseState {
  id: string;
  description: string;
  depends_on: string[];
  role: string;
  difficulty: string;
  topology: TopologyKind | null;
  state: "pending" | "running" | "done";
  /** Actual runtime fan-out, from `phase_done.n_agents`; reconciles spoke count. */
  n_agents: number | null;
  tokens: number | null;
  wall_s: number | null;
  output: string | null;
}

export interface GraphState {
  nodes: GraphNodePayload[];
  edges: GraphEdgePayload[];
}

export interface TokenState {
  input: number;
  output: number;
  total: number;
  estimated: boolean;
}

export interface BudgetState {
  spent: number;
  ceiling: number | null;
  exceeded: boolean;
}

export interface ResultState {
  total_tokens: number;
  input: number;
  output: number;
  estimated: boolean;
  wall_s: number;
  result: string;
  /** Absolute path the result was saved to (session workspace); "" if not saved. */
  result_path: string;
}

export interface RunState {
  // ── header / lifecycle ──
  sessionId: string | null;
  status: RunStatus;
  mode: RunMode;
  session: SessionPayload | null;
  task: string | null;
  errorMessage: string | null;
  /** True when the run ended via /cancel — a partial result, distinct from a clean finish. */
  cancelled: boolean;

  // ── core surfaces ──
  phases: PhaseState[];
  graph: GraphState;
  tokens: TokenState;
  budget: BudgetState | null;
  streamText: string;
  result: ResultState | null;

  // ── per-panel arrays (each panel subscribes to its slice) ──
  memory: MemoryEntry[];
  /** Graceful-degradation message from the memory subsystem; "" when healthy. */
  memoryNotice: string;
  selfimprove: SelfImprovePayload[];
  evolve: EvolvePayload[];
  gates: GatePayload[];
  dag: DagPayload | null;
  verify: { findings: VerifyFinding[]; uncited: string[] } | null;
  router: RouterPayload[];
  agentEvents: AgentEventPayload[];

  // ── M7 Wave 1 ──
  /** Loop-library catalog matches from the last `loops` event. */
  loops: LoopMatch[];
  /** Set when the plan was pre-seeded from a loop; null otherwise. */
  loopSeed: LoopSeedPayload | null;
  /** Web/tool activity (tool_call merged with its tool_result by step+tool). */
  tools: ToolActivity[];

  // ── M8 ──
  /** Loop Doctor health checks from the last `loopdoctor` event (replaced each time). */
  loopDoctor: LoopDoctorCheck[];

  // ── Loop Engineering (agentkit.loop) ──
  goalMet: GoalMetPayload | null;
  configuredGoal: { end_state: string; evidence_cmd: string; success_pattern: string; constraints: string[]; max_turns: number; max_tokens: number; timeout_s: number } | null;
  setConfiguredGoal: (g: RunState["configuredGoal"]) => void;
  configuredHillClimb: { score_metric: string; min_improvement: number; max_epochs: number; auto_improve?: boolean } | null;
  setConfiguredHillClimb: (c: RunState["configuredHillClimb"]) => void;
  /** GUI rubric weights + deliverable template (DESIGN §11.6); null → backend defaults. */
  configuredRubric: RubricConfig | null;
  setConfiguredRubric: (r: RubricConfig | null) => void;
  setSchedulerTriggers: (p: SchedulerPayload) => void;
  hillClimb: HillClimbPayload[];
  currentTaskHash: string | null;
  schedulerTriggers: SchedulerPayload | null;
  chainResults: ChainPayload[];

  // ── actions ──
  apply: (event: StudioEvent) => void;
  beginRun: (sessionId: string, mode: RunMode) => void;
  reset: () => void;
  /** One-shot signal: ResultWindow sets this; RunBar consumes + clears it. */
  pendingContinue: string | null;
  setContinue: (req: string | null) => void;
}

const EMPTY_TOKENS: TokenState = { input: 0, output: 0, total: 0, estimated: false };

const initialState = {
  sessionId: null as string | null,
  status: "idle" as RunStatus,
  mode: "auto" as RunMode,
  session: null as SessionPayload | null,
  task: null as string | null,
  errorMessage: null as string | null,
  cancelled: false,
  phases: [] as PhaseState[],
  graph: { nodes: [], edges: [] } as GraphState,
  tokens: EMPTY_TOKENS,
  budget: null as BudgetState | null,
  streamText: "",
  result: null as ResultState | null,
  memory: [] as MemoryEntry[],
  memoryNotice: "",
  selfimprove: [] as SelfImprovePayload[],
  evolve: [] as EvolvePayload[],
  gates: [] as GatePayload[],
  dag: null as DagPayload | null,
  verify: null as { findings: VerifyFinding[]; uncited: string[] } | null,
  router: [] as RouterPayload[],
  agentEvents: [] as AgentEventPayload[],
  loops: [] as LoopMatch[],
  loopSeed: null as LoopSeedPayload | null,
  tools: [] as ToolActivity[],
  loopDoctor: [] as LoopDoctorCheck[],
  goalMet: null as GoalMetPayload | null,
  configuredGoal: null as RunState["configuredGoal"],
  configuredHillClimb: null as RunState["configuredHillClimb"],
  configuredRubric: null as RubricConfig | null,
  hillClimb: [] as HillClimbPayload[],
  currentTaskHash: null as string | null,
  schedulerTriggers: null as SchedulerPayload | null,
  chainResults: [] as ChainPayload[],
  pendingContinue: null as string | null,
};

// ── pure helpers (immutable phase transitions) ─────────────────────────────

function planToPhases(steps: PlanStep[]): PhaseState[] {
  return steps.map((s) => ({
    id: s.id,
    description: s.description,
    depends_on: s.depends_on,
    role: s.role,
    difficulty: s.difficulty,
    topology: null,
    state: "pending" as const,
    n_agents: null,
    tokens: null,
    wall_s: null,
    output: null,
  }));
}

function setPhase(
  phases: PhaseState[],
  id: string,
  patch: Partial<PhaseState>,
): PhaseState[] {
  return phases.map((p) => (p.id === id ? { ...p, ...patch } : p));
}

function mergeTokens(prev: TokenState, cumulative: CumulativeTokens): TokenState {
  return {
    input: cumulative.input,
    output: cumulative.output,
    total: cumulative.total,
    // sticky estimated — never un-set once true (SPEC §7)
    estimated: prev.estimated || cumulative.estimated,
  };
}

export const useRunStore = create<RunState>((set) => ({
  ...initialState,

  beginRun: (sessionId, mode) =>
    set((state) => ({
      ...initialState,
      sessionId,
      mode,
      status: "connecting",
      // Preserve loop config across run start — already sent to backend.
      configuredHillClimb: state.configuredHillClimb,
      configuredGoal: state.configuredGoal,
      configuredRubric: state.configuredRubric,
    })),

  reset: () => set({ ...initialState }),
  setConfiguredGoal: (g) => set({ configuredGoal: g }),
  setConfiguredHillClimb: (c) => set({ configuredHillClimb: c }),
  setConfiguredRubric: (r) => set({ configuredRubric: r }),
  setSchedulerTriggers: (p) => set({ schedulerTriggers: p }),

  setContinue: (req) => set({ pendingContinue: req }),

  apply: (event) =>
    set((state) => {
      switch (event.type) {
        case "session":
          return { session: event.payload, mode: event.payload.mode as RunMode };

        case "plan":
          return {
            task: event.payload.task,
            phases: planToPhases(event.payload.steps),
            status: "running",
          };

        case "topology": {
          let phases = state.phases;
          for (const t of event.payload.steps) {
            phases = setPhase(phases, t.id, { topology: toTopologyKind(t.topology) });
          }
          return { phases };
        }

        case "graph":
          return { graph: { nodes: event.payload.nodes, edges: event.payload.edges } };

        case "phase_start":
          // Adopt the PLANNED fan-out up front so agents render as running during
          // the phase; phase_done later reconciles to the real count.
          return {
            phases: setPhase(state.phases, event.payload.step_id, {
              state: "running",
              n_agents: event.payload.n_agents ?? null,
            }),
          };

        case "agent_event":
          return { agentEvents: [...state.agentEvents, event.payload] };

        case "token":
          return { tokens: mergeTokens(state.tokens, event.payload.cumulative) };

        case "text":
          return { streamText: state.streamText + event.payload.delta };

        case "phase_done":
          return {
            phases: setPhase(state.phases, event.payload.step_id, {
              state: "done",
              topology: toTopologyKind(event.payload.topology),
              n_agents: event.payload.n_agents,
              tokens: event.payload.tokens,
              wall_s: event.payload.wall_s,
              output: event.payload.output,
            }),
          };

        case "budget":
          return { budget: budgetFrom(event.payload) };

        case "router":
          return { router: [...state.router, event.payload] };

        case "memory":
          return {
            memory: event.payload.entries,
            memoryNotice: event.payload.notice,
          };

        case "selfimprove":
          return { selfimprove: [...state.selfimprove, event.payload] };

        case "evolve":
          return { evolve: [...state.evolve, event.payload] };

        case "gate":
          return { gates: [...state.gates, event.payload] };

        case "dag":
          return { dag: event.payload };

        case "verify":
          return {
            verify: {
              findings: event.payload.findings,
              uncited: event.payload.uncited,
            },
          };

        case "loops":
          return { loops: event.payload.matches };

        case "loop_seed":
          return { loopSeed: event.payload };

        case "tool_call":
          return { tools: [...state.tools, toolActivityFrom(event.payload)] };

        case "tool_result":
          return { tools: mergeToolResult(state.tools, event.payload) };

        case "loopdoctor":
          return { loopDoctor: event.payload.checks };

        case "goal_met":
          return { goalMet: event.payload };

        case "hill_climb":
          return {
            hillClimb: [...state.hillClimb, event.payload],
            currentTaskHash: event.payload.task_hash ?? state.currentTaskHash,
          };

        case "scheduler":
          return { schedulerTriggers: event.payload };

        case "chain":
          return { chainResults: [...state.chainResults, event.payload] };

        case "done":
          return {
            status: "done",
            cancelled: event.payload.cancelled,
            result: event.payload,
            tokens: {
              input: event.payload.input,
              output: event.payload.output,
              total: event.payload.total_tokens,
              estimated: state.tokens.estimated || event.payload.estimated,
            },
          };

        case "error":
          return {
            status: "error",
            errorMessage: `${event.payload.message} (${event.payload.where})`,
          };

        default:
          // Exhaustiveness guard: if a new event type is added to the union and
          // not handled above, this assignment fails to compile.
          return assertNever(event);
      }
    }),
}));

function budgetFrom(payload: BudgetPayload): BudgetState {
  return { spent: payload.spent, ceiling: payload.ceiling, exceeded: payload.exceeded };
}

function toolActivityFrom(payload: ToolCallPayload): ToolActivity {
  return {
    step_id: payload.step_id,
    tool: payload.tool,
    args: payload.args,
    summary: null,
    n_results: null,
    notice: "",
    rejected: false,
  };
}

/**
 * Merge a tool_result into the FIRST matching open tool_call (same step+tool with
 * no summary yet) — results pair with calls in FIFO order, so when a step fires the
 * same tool twice, result #1 fills call #1. If none is open (result without a
 * preceding call), append a result-only entry so nothing is dropped.
 */
function mergeToolResult(
  tools: ToolActivity[],
  payload: ToolResultPayload,
): ToolActivity[] {
  const openIndex = tools.findIndex(
    (t) =>
      t.step_id === payload.step_id &&
      t.tool === payload.tool &&
      t.summary === null,
  );
  if (openIndex === -1) {
    return [
      ...tools,
      {
        step_id: payload.step_id,
        tool: payload.tool,
        args: {},
        summary: payload.summary,
        n_results: payload.n_results,
        notice: payload.notice,
        rejected: payload.rejected,
      },
    ];
  }
  return tools.map((t, i) =>
    i === openIndex
      ? {
          ...t,
          summary: payload.summary,
          n_results: payload.n_results,
          notice: payload.notice,
          rejected: payload.rejected,
        }
      : t,
  );
}

function assertNever(event: never): never {
  throw new Error(`Unhandled StudioEvent: ${JSON.stringify(event)}`);
}
