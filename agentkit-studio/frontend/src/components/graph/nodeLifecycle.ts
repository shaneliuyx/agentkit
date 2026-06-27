/**
 * Pure event-timeline → per-node lifecycle derivation (the temporal layer over the
 * static topology graph). React Flow + `buildGraph` own STRUCTURE; this owns the
 * TIME SEQUENCE: which node is `pending` (not yet reached), `active` (running now),
 * or `done` (settled) at the current point in the run.
 *
 * Why this is a separate pure helper (not folded into buildGraph): the lifecycle is
 * a projection of the materialized event timeline (`phase.state`, itself the result
 * of phase_start → token… → phase_done) onto each node by its role within the phase.
 * Keeping it pure + side-effect-free makes the temporal model unit-testable
 * (nodeLifecycle.test.ts) independent of any animation frame.
 *
 * The phase-level state machine the store exposes is coarse: a phase is `pending`,
 * `running`, or `done` — there is NO sub-phase event telling us "spokes started but
 * reduce hasn't". So the honest lifecycle mapping is by phase state:
 *   phase pending → every node pending (ghosted; this phase's turn has not come)
 *   phase running → every node active (the fan-out is happening now)
 *   phase done    → every node done (results have converged)
 * The intra-phase REVEAL ORDER (orchestrator first, spokes fan out together, reduce
 * converges last) and the phase-to-phase left-to-right cascade are a MOTION concern,
 * staged by `entranceDelayMs` below — not encoded as extra lifecycle states, since
 * no event distinguishes them. This keeps the pure model faithful to the data while
 * the animation layer (nodeAnim.ts) renders the sequence.
 */
import type { AgentRunState, StudioNodeData } from "./topologyLayout";

/** A node's lifecycle phase. Mirrors `AgentRunState` but named for the temporal model. */
export type NodeLifecycle = AgentRunState; // "pending" | "running" | "done"

/** Phase-level run state as the store tracks it. */
export type PhaseRunState = "pending" | "running" | "done";

/**
 * Map a phase's run state to the lifecycle of one of its nodes. Faithful to the
 * coarse phase-level timeline: a node's lifecycle IS its phase's state — the reveal
 * ordering within a running phase is handled by motion staging, not by state.
 *
 * `kind` is accepted so callers and tests document that the mapping is intentionally
 * uniform across node roles (it is NOT a bug that a reduce node and a spoke share a
 * lifecycle — the data does not separate them).
 */
export function nodeLifecycle(
  phaseState: PhaseRunState,
  _kind: StudioNodeData["kind"],
): NodeLifecycle {
  // The mapping is the identity: a node's lifecycle IS its phase's state, uniform
  // across roles (the data carries no sub-phase distinction). Reveal ORDERING lives
  // in entranceDelayMs, not here. `_kind` is kept so callers/tests document that the
  // uniformity is intentional, not an oversight.
  return phaseState;
}

// ── Reveal staging (motion timing, derived deterministically from node role) ──
//
// These are DELAYS, not states: when a node enters `active`/`done`, the animation
// layer waits this long before playing its entrance so the fan-out reads as a time
// sequence. Within one phase: orchestrator (phase header / hub) reveals first, the
// worker spokes fan out together with a small per-spoke stagger, and the reduce
// (summarizer) converges last. Kept as named constants — no magic numbers.

/** Base reveal delay per node role, in ms (orchestrator first → reduce last). */
const ROLE_BASE_DELAY_MS: Record<StudioNodeData["kind"], number> = {
  phase: 0, // orchestrator header activates first
  hub: 0, // STAR hub is also an orchestrator
  agent: 120, // spokes / debaters fan out after the orchestrator
  stage: 120, // PIPELINE stages reveal after the header (own index stagger added)
  reduce: 320, // summarizer converges last
};

/** Per-step stagger applied to parallel siblings (spokes, debaters, stages). */
const SIBLING_STAGGER_MS = 60;

/**
 * Deterministic entrance delay (ms) for a node, given its role and its index among
 * parallel siblings. Pure: same inputs → same delay, so it is unit-testable and the
 * "fan out together with a slight stagger" ordering is verifiable without timers.
 *
 * `siblingIndex` is the node's position among its parallel peers (0-based); 0 for
 * solitary nodes (header, hub, reduce, single agent). The stagger makes spokes
 * animate in TOGETHER but not on the exact same frame (~60ms apart).
 */
export function entranceDelayMs(
  kind: StudioNodeData["kind"],
  siblingIndex = 0,
): number {
  const base = ROLE_BASE_DELAY_MS[kind];
  const staggered = kind === "agent" || kind === "stage";
  return base + (staggered ? siblingIndex * SIBLING_STAGGER_MS : 0);
}

/** Animation a node should play on a lifecycle change, or null for none. */
export type NodeTransition = "reveal" | "settle" | null;

/**
 * Decide the entrance/exit animation for a `prev → next` lifecycle change. Pure so
 * the rule is unit-testable without a render or timer.
 *
 * REVEAL fires whenever a node LEAVES pending — to running OR straight to done. The
 * "straight to done" arm is the fix for late-mounted nodes: when a phase re-expands
 * its agent count mid-run (n_agents only lands at `phase_done`, so spokes are added
 * after the phase is already running/done), the new spoke nodes mount with an
 * initial `prev` of "pending" and must still animate IN rather than pop in static.
 * SETTLE fires on the running→done convergence (results landing).
 */
export function nodeTransition(prev: NodeLifecycle, next: NodeLifecycle): NodeTransition {
  if (prev === "pending" && next !== "pending") {
    return "reveal";
  }
  if (prev === "running" && next === "done") {
    return "settle";
  }
  return null;
}
