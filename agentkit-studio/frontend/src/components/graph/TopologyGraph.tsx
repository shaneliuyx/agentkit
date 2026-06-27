/**
 * The live topology canvas (SPEC §6). React Flow renders structure; the custom
 * `StudioNode` renderer drives anime.js pulse on running nodes. Nodes/edges are
 * derived from store phases via the pure `buildGraph`.
 */
import { useEffect, useMemo, useRef } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import { useRunStore } from "../../store/runStore";
import { buildGraph, type StudioNodeData } from "./topologyLayout";
import { entranceDelayMs, nodeTransition } from "./nodeLifecycle";
import { pulseRunning, revealNode, settleNode } from "./nodeAnim";
import { toolBadgeLabel, toolBadgeTitle } from "../panels/toolMeta";
import "./graph.css";

function StudioNodeView({ data }: NodeProps<StudioNodeData>) {
  const ref = useRef<HTMLDivElement>(null);
  // Remember the previous lifecycle so we fire the staged ENTRANCE only on the
  // leaving-pending transition (the moment this node joins the time sequence), not on
  // every unrelated re-render of an already-active node. Initialised to "pending"
  // (NOT data.state) so a node mounted LATE — when a phase re-expanded its agent
  // count mid-run and the new spoke first appears already running/done — still
  // animates in instead of popping in static (the "animation must follow newly
  // added agents" fix).
  const prevState = useRef<StudioNodeData["state"]>("pending");

  // Time-sequence transitions: stage the fan-out ENTRANCE when a node first
  // activates (orchestrator→spokes→reduce ordering via the stagger delay), and play
  // the convergence SETTLE when it finishes. Fires only on the transition edge — the
  // prevState ref survives re-renders because React Flow reconciles by node id.
  useEffect(() => {
    const prev = prevState.current;
    prevState.current = data.state;
    const el = ref.current;
    if (!el) {
      return;
    }
    const transition = nodeTransition(prev, data.state);
    if (transition === "reveal") {
      return revealNode(el, entranceDelayMs(data.kind, data.siblingIndex));
    }
    if (transition === "settle") {
      return settleNode(el);
    }
  }, [data.state, data.kind, data.siblingIndex]);

  // Pulse while running; cleanup on state change / unmount.
  useEffect(() => {
    if (data.state !== "running" || !ref.current) {
      return;
    }
    return pulseRunning(ref.current);
  }, [data.state]);

  return (
    <div
      ref={ref}
      className="topo-node"
      data-kind={data.kind}
      data-state={data.state}
    >
      <Handle type="target" position={Position.Top} />
      <span className="topo-node-kind">{data.kind}</span>
      <span className="topo-node-label">{data.label}</span>
      {data.kind === "phase" ? (
        <span className="topo-node-meta mono">
          {data.topology ? <span className="topo-node-topo">{data.topology}</span> : null}
          {data.nCalls != null && data.nCalls > 0 ? (
            <span
              className="topo-node-calls"
              title="Raw agent-call count (incl. debate rounds + reduce)"
            >
              {data.nCalls} calls
            </span>
          ) : null}
          {data.toolCounts && Object.keys(data.toolCounts).length > 0 ? (
            <span
              className="topo-node-web"
              title={toolBadgeTitle(data.toolCounts)}
            >
              {toolBadgeLabel(data.toolCounts)}
            </span>
          ) : null}
        </span>
      ) : null}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const NODE_TYPES = { studio: StudioNodeView };

export function TopologyGraph() {
  const phases = useRunStore((s) => s.phases);
  const tools = useRunStore((s) => s.tools);
  const status = useRunStore((s) => s.status);
  // The DAG draws PHASES only. After every phase settles to `done`, the run is
  // STILL active (verify → score → weakness mining → next epoch) — work the graph
  // has no node for. Without this, the diagram reads "complete" while the run
  // churns (the v27 1M-token tail). Surface a run-level badge until the terminal
  // `done` event so the diagram never claims done prematurely.
  const allPhasesDone = phases.length > 0 && phases.every((p) => p.state === "done");
  const runBadge =
    status === "running"
      ? allPhasesDone
        ? "finalizing — verify · score · improve"
        : "running"
      : null;

  const { nodes, edges } = useMemo(() => {
    const built = buildGraph(phases);
    // Inject per-phase, per-tool call counts onto the phase header nodes (the pure
    // buildGraph has no tool data — it lives in a separate store slice). Counts are
    // generic over tool name (web_search/read_file/write_file/…).
    const perStep = new Map<string, Record<string, number>>();
    for (const t of tools) {
      const byTool = perStep.get(t.step_id) ?? {};
      byTool[t.tool] = (byTool[t.tool] ?? 0) + 1;
      perStep.set(t.step_id, byTool);
    }
    const nodesWithTools = built.nodes.map((n) =>
      n.data.kind === "phase" && perStep.has(n.data.phaseId)
        ? { ...n, data: { ...n.data, toolCounts: perStep.get(n.data.phaseId) } }
        : n,
    );
    return { nodes: nodesWithTools, edges: built.edges };
  }, [phases, tools]);

  if (phases.length === 0) {
    return (
      <div className="topo-empty">
        <p className="eyebrow">Topology</p>
        <p className="dim">
          Configure a backend, enter a requirement, and press Run to watch the
          plan deploy as a live agent topology.
        </p>
      </div>
    );
  }

  return (
    <div className="topo-canvas">
      {runBadge ? (
        <div className="topo-run-badge" data-finalizing={allPhasesDone}>
          <span className="topo-run-dot" />
          {runBadge}
        </div>
      ) : null}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.25 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#2a3550" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
