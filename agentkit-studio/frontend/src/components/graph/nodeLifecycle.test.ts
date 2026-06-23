import { describe, expect, test } from "vitest";
import { nodeLifecycle, entranceDelayMs } from "./nodeLifecycle";
import { buildGraph, type StudioNodeData } from "./topologyLayout";
import type { PhaseState } from "../../store/runStore";
import type { TopologyKind } from "../../api/types";

/**
 * Unit tests for the TEMPORAL layer — the pure projection of the event timeline
 * (materialized as phase.state) onto per-node lifecycle + reveal stagger. These test
 * the state-derivation logic, NOT animation frames (timers/anime.js are untouched).
 */

function phase(
  id: string,
  topology: TopologyKind,
  nAgents: number,
  state: PhaseState["state"],
  dependsOn: string[] = [],
): PhaseState {
  return {
    id,
    description: `phase ${id}`,
    depends_on: dependsOn,
    role: "researcher",
    difficulty: "medium",
    topology,
    state,
    n_agents: nAgents,
    tokens: null,
    wall_s: null,
    output: null,
  };
}

describe("nodeLifecycle (phase state → per-node lifecycle)", () => {
  const kinds: StudioNodeData["kind"][] = [
    "phase",
    "hub",
    "agent",
    "stage",
    "reduce",
  ];

  test("pending phase → every node kind is pending (ghosted, turn not reached)", () => {
    for (const k of kinds) {
      expect(nodeLifecycle("pending", k)).toBe("pending");
    }
  });

  test("running phase → every node kind is running (fan-out happening now)", () => {
    for (const k of kinds) {
      expect(nodeLifecycle("running", k)).toBe("running");
    }
  });

  test("done phase → every node kind is done (results converged)", () => {
    for (const k of kinds) {
      expect(nodeLifecycle("done", k)).toBe("done");
    }
  });
});

describe("entranceDelayMs (fan-out reveal staging)", () => {
  test("orchestrator (phase header / hub) reveals first — zero delay", () => {
    expect(entranceDelayMs("phase")).toBe(0);
    expect(entranceDelayMs("hub")).toBe(0);
  });

  test("reduce converges last — strictly after every worker reveal", () => {
    const reduceDelay = entranceDelayMs("reduce");
    expect(reduceDelay).toBeGreaterThan(entranceDelayMs("phase"));
    expect(reduceDelay).toBeGreaterThan(entranceDelayMs("agent", 0));
    expect(reduceDelay).toBeGreaterThan(entranceDelayMs("hub"));
  });

  test("spokes fan out AFTER the orchestrator but TOGETHER (staggered, not serial)", () => {
    // Each spoke starts after the orchestrator (header at 0)...
    expect(entranceDelayMs("agent", 0)).toBeGreaterThan(entranceDelayMs("phase"));
    // ...and siblings are only a small stagger apart (parallel feel, not a queue).
    const gap = entranceDelayMs("agent", 1) - entranceDelayMs("agent", 0);
    expect(gap).toBe(60);
    // Monotonic in sibling index so the stagger reads left-to-right deterministically.
    expect(entranceDelayMs("agent", 2)).toBeGreaterThan(entranceDelayMs("agent", 1));
  });

  test("stages stagger like spokes (PIPELINE reveal order is deterministic)", () => {
    expect(entranceDelayMs("stage", 1)).toBeGreaterThan(entranceDelayMs("stage", 0));
  });

  test("solitary nodes ignore sibling index (no spurious stagger)", () => {
    expect(entranceDelayMs("phase", 3)).toBe(entranceDelayMs("phase", 0));
    expect(entranceDelayMs("reduce", 3)).toBe(entranceDelayMs("reduce", 0));
    expect(entranceDelayMs("hub", 3)).toBe(entranceDelayMs("hub", 0));
  });
});

describe("buildGraph carries the temporal data the reveal needs", () => {
  test("a pending phase ghosts ALL its nodes; a later running phase activates its own", () => {
    // Dependency-ordered: 'a' running, 'b' still pending → left-to-right time sequence.
    const { nodes } = buildGraph([
      phase("a", "STAR", 5, "running"),
      phase("b", "SINGLE", 1, "pending", ["a"]),
    ]);
    const aNodes = nodes.filter((n) => n.data.phaseId === "a");
    const bNodes = nodes.filter((n) => n.data.phaseId === "b");
    expect(aNodes.every((n) => n.data.state === "running")).toBe(true);
    expect(bNodes.every((n) => n.data.state === "pending")).toBe(true);
  });

  test("STAR spokes carry monotonic siblingIndex so the fan-out stagger is ordered", () => {
    const { nodes } = buildGraph([phase("a", "STAR", 5, "running")]);
    const spokes = nodes
      .filter((n) => n.data.kind === "agent")
      .sort((x, y) => x.data.siblingIndex - y.data.siblingIndex);
    expect(spokes.map((s) => s.data.siblingIndex)).toEqual([0, 1, 2, 3]);
  });

  test("solitary nodes (header, hub, reduce) default to siblingIndex 0", () => {
    const { nodes } = buildGraph([phase("a", "STAR", 5, "running")]);
    expect(nodes.find((n) => n.data.kind === "phase")?.data.siblingIndex).toBe(0);
    expect(nodes.find((n) => n.data.kind === "hub")?.data.siblingIndex).toBe(0);
    expect(nodes.find((n) => n.data.kind === "reduce")?.data.siblingIndex).toBe(0);
  });

  test("a done phase settles ALL its nodes to done", () => {
    const { nodes } = buildGraph([phase("a", "MESH", 7, "done")]);
    expect(nodes.every((n) => n.data.state === "done")).toBe(true);
  });
});
