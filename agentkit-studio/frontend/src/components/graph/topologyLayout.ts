/**
 * Pure topology → React Flow nodes/edges (SPEC §6 "Graph").
 *
 * Each phase becomes a labelled phase node; its `topology` expands into intra-phase
 * agent nodes/edges:
 *   SINGLE   → phase + 1 agent
 *   STAR     → phase + hub + N spokes + reduce
 *   MESH     → phase + N fully-connected debate nodes + reduce
 *   PIPELINE → phase + chain of N stage nodes
 * Inter-phase edges come from `depends_on`.
 *
 * IMPORTANT — `n_agents` is a raw agent-CALL count from agentkit's runners
 * (ground truth: `agentkit/topology/dynamic.py`), NOT a worker-node count. It
 * already folds in the reduce call (and for MESH, both debate rounds). To draw the
 * topology we INVERT it to recover the number of worker nodes, then add the shape
 * extras (STAR hub+reduce, MESH reduce) ON TOP — the reduce is already counted in
 * `n_agents`, so we never add it twice. Inversion (see `workerCount`):
 *   SINGLE   → 1
 *   STAR     → n_agents - 1           (the -1 is the reduce call)
 *   MESH     → (n_agents - 1) / 2     (two debate rounds + reduce)
 *   PIPELINE → n_agents               (one call per stage)
 * The raw `n_agents` itself is surfaced verbatim as an honest "N calls" badge on
 * the phase node — distinct from the worker-node count. Before `phase_done` lands
 * (n_agents unknown) we fall back to a default worker count and show no badge.
 *
 * This module is intentionally framework-light and side-effect free so it can be
 * unit-tested (see topologyLayout.test.ts) and so React Flow only owns rendering.
 */
import type { Edge, Node } from "reactflow";
import type { PhaseState } from "../../store/runStore";
import type { TopologyKind } from "../../api/types";

export type AgentRunState = "pending" | "running" | "done";

/** Data carried on every graph node, consumed by the custom node renderer. */
export interface StudioNodeData {
  label: string;
  /** Logical role of the node within a phase. */
  kind: "phase" | "agent" | "hub" | "reduce" | "stage";
  phaseId: string;
  topology: TopologyKind | null;
  state: AgentRunState;
  /**
   * Raw agent-call count for this phase (only on the `phase` header node, and only
   * once `phase_done` has reported it). The honest invocation count incl. rounds +
   * reduce — rendered as an "N calls" badge, distinct from the worker-node count.
   */
  nCalls?: number | null;
  /**
   * Per-tool call counts this phase emitted (M7 Wave 1, generalized over any tool
   * name — web_search/read_file/write_file/…). Set on the `phase` header node from
   * store tool activity; drives the tool badge, distinct from `nCalls`. Injected
   * post-`buildGraph` since tool data lives outside the pure phase list.
   */
  toolCounts?: Record<string, number>;
}

export type StudioNode = Node<StudioNodeData>;
export type StudioEdge = Edge;

// Layout geometry — kept as named constants, not magic numbers.
const PHASE_COL_W = 320; // horizontal gap between phases
const PHASE_Y = 0; // phase header row
const AGENT_ROW_Y = 130; // hub / first agent row
const SPOKE_ROW_Y = 250; // spoke / debate / stage row
const REDUCE_ROW_Y = 380;
const AGENT_X_GAP = 90;
const DEFAULT_WORKERS = 3; // worker count before phase_done reports n_agents

/**
 * Invert the raw agent-call count `n_agents` into the number of WORKER nodes to
 * draw, per topology (the reduce/rounds are folded into n_agents — see file header).
 * Before `phase_done` (n_agents null) fall back to the layout default. Guards keep
 * each topology's minimum sensible worker count.
 */
function workerCount(topology: TopologyKind, nAgents: number | null): number {
  if (!nAgents || nAgents <= 0) {
    return DEFAULT_WORKERS;
  }
  switch (topology) {
    case "SINGLE":
      return 1;
    case "STAR":
      return Math.max(1, nAgents - 1); // minus the reduce call
    case "MESH":
      return Math.max(2, Math.round((nAgents - 1) / 2)); // two rounds + reduce
    case "PIPELINE":
      return Math.max(1, nAgents); // one call per stage
  }
}

function phaseNodeId(phaseId: string): string {
  return `phase:${phaseId}`;
}

/** Build all nodes + edges for a list of phases. */
export function buildGraph(phases: PhaseState[]): {
  nodes: StudioNode[];
  edges: StudioEdge[];
} {
  const nodes: StudioNode[] = [];
  const edges: StudioEdge[] = [];

  phases.forEach((phase, index) => {
    const baseX = index * PHASE_COL_W;
    const { nodes: phaseNodes, edges: phaseEdges } = buildPhase(phase, baseX);
    nodes.push(...phaseNodes);
    edges.push(...phaseEdges);
  });

  // Inter-phase dependency edges (phase → phase).
  for (const phase of phases) {
    for (const dep of phase.depends_on) {
      edges.push({
        id: `dep:${dep}->${phase.id}`,
        source: phaseNodeId(dep),
        target: phaseNodeId(phase.id),
        type: "smoothstep",
        animated: phase.state === "running",
        data: { kind: "depends_on" },
      });
    }
  }

  return { nodes, edges };
}

function makeNode(
  id: string,
  data: StudioNodeData,
  x: number,
  y: number,
): StudioNode {
  return {
    id,
    type: "studio",
    position: { x, y },
    data,
  };
}

function intraEdge(
  source: string,
  target: string,
  state: AgentRunState,
): StudioEdge {
  return {
    id: `e:${source}->${target}`,
    source,
    target,
    type: "smoothstep",
    animated: state === "running",
    data: { kind: "intra" },
  };
}

/** Build a single phase's subgraph at horizontal offset `baseX`. */
function buildPhase(
  phase: PhaseState,
  baseX: number,
): { nodes: StudioNode[]; edges: StudioEdge[] } {
  const nodes: StudioNode[] = [];
  const edges: StudioEdge[] = [];
  const pid = phase.id;
  const state = phase.state;

  // Every phase has a header node (inter-phase edges connect to it).
  const header = makeNode(
    phaseNodeId(pid),
    {
      label: phase.description,
      kind: "phase",
      phaseId: pid,
      topology: phase.topology,
      state,
      nCalls: phase.n_agents,
    },
    baseX,
    PHASE_Y,
  );
  nodes.push(header);

  const topology = phase.topology ?? "SINGLE";

  switch (topology) {
    case "SINGLE": {
      const aid = `${pid}:agent`;
      nodes.push(
        makeNode(
          aid,
          { label: phase.role || "agent", kind: "agent", phaseId: pid, topology, state },
          baseX,
          AGENT_ROW_Y,
        ),
      );
      edges.push(intraEdge(header.id, aid, state));
      break;
    }

    case "STAR": {
      const hubId = `${pid}:hub`;
      const reduceId = `${pid}:reduce`;
      const n = workerCount("STAR", phase.n_agents);
      nodes.push(
        makeNode(
          hubId,
          { label: "hub", kind: "hub", phaseId: pid, topology, state },
          baseX,
          AGENT_ROW_Y,
        ),
      );
      edges.push(intraEdge(header.id, hubId, state));
      for (let i = 0; i < n; i++) {
        const spokeId = `${pid}:spoke:${i}`;
        nodes.push(
          makeNode(
            spokeId,
            { label: `agent ${i + 1}`, kind: "agent", phaseId: pid, topology, state },
            baseX + (i - (n - 1) / 2) * AGENT_X_GAP,
            SPOKE_ROW_Y,
          ),
        );
        edges.push(intraEdge(hubId, spokeId, state));
        edges.push(intraEdge(spokeId, reduceId, state));
      }
      nodes.push(
        makeNode(
          reduceId,
          { label: "reduce", kind: "reduce", phaseId: pid, topology, state },
          baseX,
          REDUCE_ROW_Y,
        ),
      );
      break;
    }

    case "MESH": {
      const reduceId = `${pid}:reduce`;
      const n = workerCount("MESH", phase.n_agents);
      const meshIds: string[] = [];
      for (let i = 0; i < n; i++) {
        const nodeId = `${pid}:mesh:${i}`;
        meshIds.push(nodeId);
        nodes.push(
          makeNode(
            nodeId,
            { label: `debater ${i + 1}`, kind: "agent", phaseId: pid, topology, state },
            baseX + (i - (n - 1) / 2) * AGENT_X_GAP,
            SPOKE_ROW_Y,
          ),
        );
        edges.push(intraEdge(header.id, nodeId, state));
        edges.push(intraEdge(nodeId, reduceId, state));
      }
      // Fully-connected debate edges (undirected → emit one per unordered pair).
      for (let i = 0; i < meshIds.length; i++) {
        for (let j = i + 1; j < meshIds.length; j++) {
          edges.push({
            id: `mesh:${meshIds[i]}<->${meshIds[j]}`,
            source: meshIds[i],
            target: meshIds[j],
            type: "straight",
            animated: state === "running",
            data: { kind: "mesh" },
          });
        }
      }
      nodes.push(
        makeNode(
          reduceId,
          { label: "reduce", kind: "reduce", phaseId: pid, topology, state },
          baseX,
          REDUCE_ROW_Y,
        ),
      );
      break;
    }

    case "PIPELINE": {
      const n = workerCount("PIPELINE", phase.n_agents);
      let prevId = header.id;
      for (let i = 0; i < n; i++) {
        const stageId = `${pid}:stage:${i}`;
        nodes.push(
          makeNode(
            stageId,
            { label: `stage ${i + 1}`, kind: "stage", phaseId: pid, topology, state },
            baseX,
            AGENT_ROW_Y + i * 100,
          ),
        );
        edges.push(intraEdge(prevId, stageId, state));
        prevId = stageId;
      }
      break;
    }
  }

  return { nodes, edges };
}
