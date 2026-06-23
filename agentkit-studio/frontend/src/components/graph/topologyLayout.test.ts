import { describe, expect, test } from "vitest";
import { buildGraph, edgeRelationshipLabel } from "./topologyLayout";
import type { PhaseState } from "../../store/runStore";
import type { TopologyKind } from "../../api/types";

/**
 * Build a minimal phase fixture. `nAgents` is the RAW agent-call count (as emitted
 * verbatim in phase_done) — NOT the worker-node count. buildGraph inverts it per
 * topology (STAR: spokes=n-1, MESH: debaters=(n-1)/2, PIPELINE: stages=n, SINGLE:1).
 */
function phase(
  id: string,
  topology: TopologyKind,
  nAgents: number,
  dependsOn: string[] = [],
): PhaseState {
  return {
    id,
    description: `phase ${id}`,
    depends_on: dependsOn,
    role: "researcher",
    difficulty: "medium",
    topology,
    state: "running",
    n_agents: nAgents,
    tokens: null,
    wall_s: null,
    output: null,
  };
}

describe("topologyLayout.buildGraph", () => {
  test("SINGLE (n_agents=1) → phase header + 1 agent, 1 intra edge", () => {
    const { nodes, edges } = buildGraph([phase("a", "SINGLE", 1)]);
    // nodes: phase + agent
    expect(nodes).toHaveLength(2);
    // edges: header → agent
    expect(edges).toHaveLength(1);
    expect(nodes.filter((n) => n.data.kind === "phase")).toHaveLength(1);
    expect(nodes.filter((n) => n.data.kind === "agent")).toHaveLength(1);
  });

  test("STAR n_agents=5 → 4 spokes (n-1) + hub + reduce", () => {
    const { nodes, edges } = buildGraph([phase("a", "STAR", 5)]);
    const spokes = 4; // workerCount = n_agents - 1 (reduce already folded in)
    // nodes: phase + hub + reduce + spokes
    expect(nodes).toHaveLength(3 + spokes);
    expect(nodes.filter((x) => x.data.kind === "hub")).toHaveLength(1);
    expect(nodes.filter((x) => x.data.kind === "reduce")).toHaveLength(1);
    expect(nodes.filter((x) => x.data.kind === "agent")).toHaveLength(spokes);
    // edges: header→hub (1) + per spoke: hub→spoke + spoke→reduce (2 * spokes)
    expect(edges).toHaveLength(1 + 2 * spokes);
  });

  test("MESH n_agents=7 → 3 debaters ((n-1)/2) fully connected + reduce", () => {
    const { nodes, edges } = buildGraph([phase("a", "MESH", 7)]);
    const debaters = 3; // workerCount = (n_agents - 1) / 2 (two rounds + reduce)
    // nodes: phase + debaters + reduce
    expect(nodes).toHaveLength(2 + debaters);
    expect(nodes.filter((x) => x.data.kind === "agent")).toHaveLength(debaters);
    expect(nodes.filter((x) => x.data.kind === "reduce")).toHaveLength(1);
    // edges: per node header→node + node→reduce (2 * debaters) + full mesh C(d,2)
    const meshPairs = (debaters * (debaters - 1)) / 2;
    expect(edges).toHaveLength(2 * debaters + meshPairs);
    expect(edges.filter((e) => e.data?.kind === "mesh")).toHaveLength(meshPairs);
  });

  test("PIPELINE n_agents=4 → 4 stages (one call per stage), 4 edges", () => {
    const stages = 4; // workerCount = n_agents
    const { nodes, edges } = buildGraph([phase("a", "PIPELINE", stages)]);
    // nodes: phase + stages
    expect(nodes).toHaveLength(1 + stages);
    expect(nodes.filter((x) => x.data.kind === "stage")).toHaveLength(stages);
    // edges: header→stage0 then stage(i)→stage(i+1) = `stages` edges total
    expect(edges).toHaveLength(stages);
  });

  test("phase header carries raw n_agents as the honest call-count badge", () => {
    const { nodes } = buildGraph([phase("a", "STAR", 5)]);
    const header = nodes.find((n) => n.data.kind === "phase");
    // The badge surfaces the RAW call count (5), not the derived 4 worker spokes.
    expect(header?.data.nCalls).toBe(5);
  });

  test("inter-phase depends_on edges connect phase headers", () => {
    const { edges } = buildGraph([
      phase("a", "SINGLE", 1),
      phase("b", "SINGLE", 1, ["a"]),
    ]);
    const dep = edges.find((e) => e.id === "dep:a->b");
    expect(dep).toBeDefined();
    expect(dep?.source).toBe("phase:a");
    expect(dep?.target).toBe("phase:b");
  });

  test("worker count inverts raw n_agents per topology", () => {
    // STAR: spokes = n_agents - 1
    const star = buildGraph([phase("a", "STAR", 7)]);
    expect(star.nodes.filter((n) => n.data.kind === "agent")).toHaveLength(6);
    // MESH: debaters = (n_agents - 1) / 2
    const mesh = buildGraph([phase("a", "MESH", 9)]);
    expect(mesh.nodes.filter((n) => n.data.kind === "agent")).toHaveLength(4);
    // PIPELINE: stages = n_agents
    const pipe = buildGraph([phase("a", "PIPELINE", 5)]);
    expect(pipe.nodes.filter((n) => n.data.kind === "stage")).toHaveLength(5);
  });
});

/**
 * Edges must SHOW the relationship (who relates to whom), not just connect nodes.
 * Each edge carries a relationship `kind`, a `topo-edge-<kind>` className (for the
 * per-relationship color), and a human `label`. These tests pin the relationship
 * derivation per topology — the data the renderer needs to make edges legible.
 */
describe("edge relationships (who relates to whom)", () => {
  test("STAR: hub→spoke edges are fan-out, spoke→reduce edges are converge", () => {
    const { edges } = buildGraph([phase("a", "STAR", 5)]); // 4 spokes
    const fanout = edges.filter((e) => e.data?.kind === "fanout");
    const converge = edges.filter((e) => e.data?.kind === "converge");
    // header→hub (1) + hub→spoke (4) = 5 fan-out; spoke→reduce (4) = 4 converge.
    expect(fanout).toHaveLength(5);
    expect(converge).toHaveLength(4);
    // Convergence edges all TARGET the reducer — that IS the "results converge" relation.
    expect(converge.every((e) => e.target === "a:reduce")).toBe(true);
  });

  test("MESH: peer↔peer debate links are tagged mesh; workers→reduce are converge", () => {
    const { edges } = buildGraph([phase("a", "MESH", 7)]); // 3 debaters
    const mesh = edges.filter((e) => e.data?.kind === "mesh");
    const converge = edges.filter((e) => e.data?.kind === "converge");
    const fanout = edges.filter((e) => e.data?.kind === "fanout");
    expect(mesh).toHaveLength(3); // C(3,2) peer debate pairs
    expect(converge).toHaveLength(3); // each debater → reduce
    expect(fanout).toHaveLength(3); // header → each debater
  });

  test("PIPELINE: stage hand-offs are tagged pipeline (sequential relationship)", () => {
    const { edges } = buildGraph([phase("a", "PIPELINE", 4)]);
    expect(edges.every((e) => e.data?.kind === "pipeline")).toBe(true);
    expect(edges).toHaveLength(4);
  });

  test("inter-phase dependency edges are tagged depends_on", () => {
    const { edges } = buildGraph([
      phase("a", "SINGLE", 1),
      phase("b", "SINGLE", 1, ["a"]),
    ]);
    const dep = edges.find((e) => e.id === "dep:a->b");
    expect(dep?.data?.kind).toBe("depends_on");
  });

  test("every edge carries a relationship className + label for legibility", () => {
    const { edges } = buildGraph([
      phase("a", "STAR", 5),
      phase("b", "MESH", 7, ["a"]),
      phase("c", "PIPELINE", 3, ["b"]),
    ]);
    for (const e of edges) {
      const kind = e.data?.kind as Parameters<typeof edgeRelationshipLabel>[0];
      expect(e.className).toBe(`topo-edge-${kind}`);
      expect(e.label).toBe(edgeRelationshipLabel(kind));
    }
  });

  test("edgeRelationshipLabel gives a distinct human label per relationship", () => {
    expect(edgeRelationshipLabel("fanout")).toBe("fan-out");
    expect(edgeRelationshipLabel("converge")).toBe("converge");
    expect(edgeRelationshipLabel("mesh")).toBe("debate");
    expect(edgeRelationshipLabel("pipeline")).toBe("pipeline");
    expect(edgeRelationshipLabel("depends_on")).toBe("depends on");
  });
});
