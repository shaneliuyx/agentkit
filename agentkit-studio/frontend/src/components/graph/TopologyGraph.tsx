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
import { pulseRunning } from "./nodeAnim";
import "./graph.css";

function StudioNodeView({ data }: NodeProps<StudioNodeData>) {
  const ref = useRef<HTMLDivElement>(null);

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
        </span>
      ) : null}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const NODE_TYPES = { studio: StudioNodeView };

export function TopologyGraph() {
  const phases = useRunStore((s) => s.phases);

  const { nodes, edges } = useMemo(() => buildGraph(phases), [phases]);

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
  );
}
