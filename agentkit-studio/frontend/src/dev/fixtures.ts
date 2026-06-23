/**
 * Canned event sequence for local development + verification (SPEC §8 milestone 2).
 *
 * Covers every StudioEvent type and all four topology kinds, in the SPEC §4
 * ordering. `replayFixture` feeds them to the store on a timer so the UI can be
 * exercised without a live backend. Imported by App when `?demo=1` is set.
 */
import type { StudioEvent } from "../api/types";

const SID = "demo-session";
let clock = 0;
const t = (): number => (clock += 0.4);

export const FIXTURE_EVENTS: StudioEvent[] = [
  {
    type: "session",
    session_id: SID,
    ts: t(),
    payload: {
      llm: { label: "Haiku 4.5 (VibeProxy)", model: "claude-haiku-4-5" },
      embed: { label: "BGE-M3 (oMLX)", model: "bge-m3" },
      mode: "auto",
    },
  },
  {
    type: "plan",
    session_id: SID,
    ts: t(),
    payload: {
      task: "Research and summarize the safety landscape for autonomous agents.",
      steps: [
        { id: "s1", description: "Gather sources", depends_on: [], role: "researcher", difficulty: "easy" },
        { id: "s2", description: "Cross-examine claims", depends_on: ["s1"], role: "reviewer", difficulty: "hard" },
        { id: "s3", description: "Refine through stages", depends_on: ["s2"], role: "writer", difficulty: "medium" },
        { id: "s4", description: "Verify and finalize", depends_on: ["s2", "s3"], role: "verifier", difficulty: "medium" },
      ],
    },
  },
  {
    type: "topology",
    session_id: SID,
    ts: t(),
    payload: {
      steps: [
        { id: "s1", topology: "STAR" },
        { id: "s2", topology: "MESH" },
        { id: "s3", topology: "PIPELINE" },
        { id: "s4", topology: "SINGLE" },
      ],
    },
  },
  {
    type: "graph",
    session_id: SID,
    ts: t(),
    payload: {
      nodes: [
        { id: "phase:s1", kind: "phase", phase: "s1", label: "Gather sources", state: "pending" },
        { id: "phase:s2", kind: "phase", phase: "s2", label: "Cross-examine claims", state: "pending" },
      ],
      edges: [{ from: "phase:s1", to: "phase:s2", kind: "depends_on" }],
    },
  },

  // ── phase s1 (STAR) ──
  { type: "phase_start", session_id: SID, ts: t(), payload: { step_id: "s1" } },
  { type: "router", session_id: SID, ts: t(), payload: { step_id: "s1", difficulty: "easy", tier: "small" } },
  { type: "agent_event", session_id: SID, ts: t(), payload: { step_id: "s1", name: "tool_call", data: { tool: "web.fetch", url: "https://example.com" } } },
  {
    type: "token",
    session_id: SID,
    ts: t(),
    payload: { step_id: "s1", input: 820, output: 240, total: 1060, estimated: false, cumulative: { input: 820, output: 240, total: 1060, estimated: false } },
  },
  { type: "text", session_id: SID, ts: t(), payload: { step_id: "s1", delta: "Collected 7 sources on agent safety. " } },
  {
    type: "phase_done",
    session_id: SID,
    ts: t(),
    // n_agents=4 is the raw call count (3 spokes + 1 reduce) → 3 spoke nodes.
    payload: { step_id: "s1", topology: "STAR", n_agents: 4, tokens: 1060, wall_s: 3.2, output: "7 sources gathered." },
  },
  { type: "memory", session_id: SID, ts: t(), payload: { entries: [{ id: "m1", text: "Source: arXiv 2401.xxxx on sandboxing", tier: "episodic", score: 0.91 }], notice: "" } },

  // ── phase s2 (MESH) ──
  { type: "phase_start", session_id: SID, ts: t(), payload: { step_id: "s2" } },
  { type: "router", session_id: SID, ts: t(), payload: { step_id: "s2", difficulty: "hard", tier: "large" } },
  { type: "selfimprove", session_id: SID, ts: t(), payload: { round: 1, stalled: false, assessment: "progressing", action: "continue" } },
  { type: "evolve", session_id: SID, ts: t(), payload: { round: 1, score: 0.62, delta: 0.0, variant: "baseline" } },
  { type: "evolve", session_id: SID, ts: t(), payload: { round: 2, score: 0.78, delta: 0.16, variant: "distilled" } },
  {
    type: "token",
    session_id: SID,
    ts: t(),
    payload: { step_id: "s2", input: 2400, output: 900, total: 3300, estimated: false, cumulative: { input: 3220, output: 1140, total: 4360, estimated: false } },
  },
  {
    type: "phase_done",
    session_id: SID,
    ts: t(),
    // n_agents=7 is the raw call count (3 debaters × 2 rounds + 1 reduce) → 3 debater nodes.
    payload: { step_id: "s2", topology: "MESH", n_agents: 7, tokens: 3300, wall_s: 8.1, output: "Debate converged on 3 key risks." },
  },

  // ── phase s3 (PIPELINE) — runs on a non-reporting backend → estimated flips on ──
  { type: "phase_start", session_id: SID, ts: t(), payload: { step_id: "s3" } },
  { type: "router", session_id: SID, ts: t(), payload: { step_id: "s3", difficulty: "medium", tier: "medium" } },
  { type: "gate", session_id: SID, ts: t(), payload: { name: "net_guard", outcome: "allow", detail: "no egress requested", sandboxed: true } },
  {
    type: "token",
    session_id: SID,
    ts: t(),
    payload: { step_id: "s3", input: 0, output: 0, total: 1200, estimated: true, cumulative: { input: 3220, output: 1140, total: 5560, estimated: true } },
  },
  {
    type: "phase_done",
    session_id: SID,
    ts: t(),
    payload: { step_id: "s3", topology: "PIPELINE", n_agents: 3, tokens: 1200, wall_s: 5.0, output: "Draft refined through 3 stages." },
  },

  // ── phase s4 (SINGLE) ──
  { type: "phase_start", session_id: SID, ts: t(), payload: { step_id: "s4" } },
  { type: "router", session_id: SID, ts: t(), payload: { step_id: "s4", difficulty: "medium", tier: "medium" } },
  {
    type: "phase_done",
    session_id: SID,
    ts: t(),
    payload: { step_id: "s4", topology: "SINGLE", n_agents: 1, tokens: 640, wall_s: 2.4, output: "Final summary assembled." },
  },

  { type: "budget", session_id: SID, ts: t(), payload: { spent: 0.042, ceiling: 0.1, exceeded: false } },
  {
    type: "dag",
    session_id: SID,
    ts: t(),
    payload: {
      graph_id: "g1",
      nodes: [
        { id: "s1", status: "done" },
        { id: "s2", status: "done" },
        { id: "s3", status: "done" },
        { id: "s4", status: "done" },
      ],
      edges: [["s1", "s2"], ["s2", "s3"], ["s3", "s4"]],
    },
  },
  {
    type: "verify",
    session_id: SID,
    ts: t(),
    payload: {
      findings: [
        { claim: "Sandboxing reduces blast radius", supported: true, sources: ["m1"] },
        { claim: "Agents are fully autonomous today", supported: false, sources: [] },
      ],
      uncited: ["Agents are fully autonomous today"],
    },
  },
  {
    type: "done",
    session_id: SID,
    ts: t(),
    payload: { total_tokens: 6200, input: 3220, output: 1140, estimated: true, wall_s: 18.7, result: "Summary: three principal agent-safety risks identified, two with cited support.", cancelled: false },
  },
];

/** Replay the fixture into a store-apply function on an interval. Returns a stop fn. */
export function replayFixture(
  apply: (event: StudioEvent) => void,
  stepMs = 450,
): () => void {
  let i = 0;
  const id = setInterval(() => {
    if (i >= FIXTURE_EVENTS.length) {
      clearInterval(id);
      return;
    }
    apply(FIXTURE_EVENTS[i]);
    i += 1;
  }, stepMs);
  return () => clearInterval(id);
}
