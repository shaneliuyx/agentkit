/**
 * Tabbed panel drawer (SPEC §6) hosting all 7 comprehensive panels. Each tab shows
 * a live count badge so the operator sees activity without switching tabs.
 */
import { useState } from "react";
import { useRunStore } from "../../store/runStore";
import { MemoryPanel } from "./MemoryPanel";
import { SelfImprovePanel } from "./SelfImprovePanel";
import { EvolvePanel } from "./EvolvePanel";
import { SecurityPanel } from "./SecurityPanel";
import { DagPanel } from "./DagPanel";
import { VerifyPanel } from "./VerifyPanel";
import { RouterPanel } from "./RouterPanel";
import "./panels.css";

type TabId =
  | "router"
  | "memory"
  | "selfimprove"
  | "evolve"
  | "security"
  | "dag"
  | "verify";

const TABS: { id: TabId; label: string }[] = [
  { id: "router", label: "Router" },
  { id: "memory", label: "Memory" },
  { id: "selfimprove", label: "Self-improve" },
  { id: "evolve", label: "Evolve" },
  { id: "security", label: "Security" },
  { id: "dag", label: "DAG" },
  { id: "verify", label: "Verify" },
];

export function PanelDrawer() {
  const [active, setActive] = useState<TabId>("router");

  // Per-tab counts for the badges.
  const counts = useRunStore((s) => ({
    router: s.router.length,
    memory: s.memory.length,
    selfimprove: s.selfimprove.length + s.agentEvents.length,
    evolve: s.evolve.length,
    security: s.gates.length,
    dag: s.dag ? s.dag.nodes.length : 0,
    verify: s.verify ? s.verify.findings.length : 0,
  }));

  return (
    <div className="panel">
      <div className="drawer-tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={active === tab.id}
            className="drawer-tab"
            data-active={active === tab.id}
            onClick={() => setActive(tab.id)}
          >
            {tab.label}
            {counts[tab.id] > 0 ? (
              <span className="badge mono">{counts[tab.id]}</span>
            ) : null}
          </button>
        ))}
      </div>
      <div className="drawer-body">
        {active === "router" && <RouterPanel />}
        {active === "memory" && <MemoryPanel />}
        {active === "selfimprove" && <SelfImprovePanel />}
        {active === "evolve" && <EvolvePanel />}
        {active === "security" && <SecurityPanel />}
        {active === "dag" && <DagPanel />}
        {active === "verify" && <VerifyPanel />}
      </div>
    </div>
  );
}
