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

// `nodeLifecycle` projects the coarse phase state onto each node — the single source
// of truth for the per-node temporal state (kept here so buildGraph and the renderer
// agree). Defined in nodeLifecycle.ts alongside the reveal-stagger timing.
import { nodeLifecycle } from "./nodeLifecycle";

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
  /**
   * 0-based position among parallel siblings (spokes/debaters/stages). Drives the
   * fan-out reveal STAGGER in the temporal layer (see nodeLifecycle.entranceDelayMs)
   * so parallel workers animate in together but not on the same frame. 0 for solitary
   * nodes (header, hub, reduce, single agent).
   */
  siblingIndex: number;
}

export type StudioNode = Node<StudioNodeData>;
export type StudioEdge = Edge;

/**
 * Relationship kind carried on every edge — what the edge MEANS, so the viewer can
 * read WHO relates to WHOM, not just see nodes light up:
 *   fanout     orchestrator → worker (the fan-out)
 *   converge   worker → reducer/summarizer (results converging)
 *   mesh       worker ↔ worker (peer debate)
 *   pipeline   stage → next stage (sequential hand-off)
 *   depends_on phase → phase (inter-phase dependency)
 * Drives per-kind edge color + label in the renderer (see edgeRelationshipLabel).
 */
export type EdgeKind = "fanout" | "converge" | "mesh" | "pipeline" | "depends_on";

/** Short human label per edge relationship — rendered on the edge so it is legible. */
export function edgeRelationshipLabel(kind: EdgeKind): string {
  switch (kind) {
    case "fanout":
      return "fan-out";
    case "converge":
      return "converge";
    case "mesh":
      return "debate";
    case "pipeline":
      return "pipeline";
    case "depends_on":
      return "depends on";
  }
}

/**
 * The relationship-presentation triple every edge carries: the `topo-edge-<kind>`
 * className (per-kind color), the human label, and `data.kind`. Factored out so the
 * three edge builders (relEdge + the depends_on / mesh inline edges) cannot drift.
 */
function relProps(kind: EdgeKind): {
  className: string;
  label: string;
  data: { kind: EdgeKind };
} {
  return {
    className: `topo-edge-${kind}`,
    label: edgeRelationshipLabel(kind),
    data: { kind },
  };
}

// Layout geometry — kept as named constants, not magic numbers.
const PHASE_COL_W = 400; // horizontal gap between phases (wider to avoid inter-phase overlap)
const PHASE_Y = 0;        // phase header row
const AGENT_ROW_Y = 160;  // hub / first agent row (extra clearance for 3-line clamped header)
const SPOKE_ROW_Y = 290;  // spoke / debate / stage row
const REDUCE_ROW_Y = 420;
const AGENT_X_GAP = 110;  // wider agent spread so parallel nodes don't touch
const NODE_WIDTH = 220;   // explicit width for React Flow fitView bounds calculation
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
    case "MAP":
      return Math.max(1, nAgents - 1); // one worker per upstream item, minus reduce
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
        ...relProps("depends_on"),
      });
    }
  }

  return { nodes, edges };
}

function makeNode(
  id: string,
  data: Omit<StudioNodeData, "siblingIndex"> & { siblingIndex?: number },
  x: number,
  y: number,
): StudioNode {
  return {
    id,
    type: "studio",
    position: { x, y },
    width: NODE_WIDTH,
    data: { siblingIndex: 0, ...data },
  };
}

/**
 * Build an intra-phase edge tagged with its RELATIONSHIP kind. The kind drives the
 * per-relationship color + label (className `topo-edge-<kind>` + edge label) so the
 * viewer reads fan-out vs converge vs pipeline, and `animated` illuminates it while
 * the phase runs (compositor-friendly flowing stroke via existing CSS).
 */
function relEdge(
  source: string,
  target: string,
  state: AgentRunState,
  kind: EdgeKind,
): StudioEdge {
  return {
    id: `e:${source}->${target}`,
    source,
    target,
    type: "smoothstep",
    animated: state === "running",
    ...relProps(kind),
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
  // Per-node temporal lifecycle, projected from the phase's coarse run state via the
  // single-source-of-truth helper. Uniform across node kinds (the data carries no
  // sub-phase distinction); the fan-out reveal ORDER is staged in motion, not here.
  const state = nodeLifecycle(phase.state, "phase");

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
      edges.push(relEdge(header.id, aid, state, "fanout"));
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
      edges.push(relEdge(header.id, hubId, state, "fanout"));
      for (let i = 0; i < n; i++) {
        const spokeId = `${pid}:spoke:${i}`;
        nodes.push(
          makeNode(
            spokeId,
            { label: `agent ${i + 1}`, kind: "agent", phaseId: pid, topology, state, siblingIndex: i },
            baseX + (i - (n - 1) / 2) * AGENT_X_GAP,
            SPOKE_ROW_Y,
          ),
        );
        // hub → spoke is the fan-out; spoke → reduce is the convergence.
        edges.push(relEdge(hubId, spokeId, state, "fanout"));
        edges.push(relEdge(spokeId, reduceId, state, "converge"));
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

    case "MAP": {
      // MAP: header → N item-workers (one per upstream item) → reduce.
      // No hub — items come from upstream, not from sub-dividing the description.
      const reduceId = `${pid}:reduce`;
      const n = workerCount("MAP", phase.n_agents);
      for (let i = 0; i < n; i++) {
        const workerId = `${pid}:item:${i}`;
        nodes.push(
          makeNode(
            workerId,
            { label: `item ${i + 1}`, kind: "agent", phaseId: pid, topology, state, siblingIndex: i },
            baseX + (i - (n - 1) / 2) * AGENT_X_GAP,
            AGENT_ROW_Y,
          ),
        );
        edges.push(relEdge(header.id, workerId, state, "fanout"));
        edges.push(relEdge(workerId, reduceId, state, "converge"));
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
            { label: `debater ${i + 1}`, kind: "agent", phaseId: pid, topology, state, siblingIndex: i },
            baseX + (i - (n - 1) / 2) * AGENT_X_GAP,
            SPOKE_ROW_Y,
          ),
        );
        // header → debater is the fan-out; debater → reduce is the convergence.
        edges.push(relEdge(header.id, nodeId, state, "fanout"));
        edges.push(relEdge(nodeId, reduceId, state, "converge"));
      }
      // Fully-connected peer debate links (undirected → one per unordered pair),
      // tagged `mesh` so the viewer reads the worker↔worker relationship.
      for (let i = 0; i < meshIds.length; i++) {
        for (let j = i + 1; j < meshIds.length; j++) {
          edges.push({
            id: `mesh:${meshIds[i]}<->${meshIds[j]}`,
            source: meshIds[i],
            target: meshIds[j],
            type: "straight",
            animated: state === "running",
            ...relProps("mesh"),
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
            { label: `stage ${i + 1}`, kind: "stage", phaseId: pid, topology, state, siblingIndex: i },
            baseX,
            AGENT_ROW_Y + i * 100,
          ),
        );
        edges.push(relEdge(prevId, stageId, state, "pipeline"));
        prevId = stageId;
      }
      break;
    }
  }

  return { nodes, edges };
}
