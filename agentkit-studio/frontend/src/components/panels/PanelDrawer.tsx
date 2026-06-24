/**
 * Tabbed panel drawer hosting the comprehensive panels + the M7 Wave 1 additions
 * (Loops catalog, web/Tools activity). Each tab shows a live count badge so the
 * operator sees activity without switching tabs.
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
import { LoopsPanel } from "./LoopsPanel";
import { ToolsPanel } from "./ToolsPanel";
import { LoopDoctorPanel } from "./LoopDoctorPanel";
import { GoalPanel } from "./GoalPanel";
import { HillClimbPanel } from "./HillClimbPanel";
import { SchedulerPanel } from "./SchedulerPanel";
import { ChainComposerPanel } from "./ChainComposerPanel";
import "./panels.css";

type TabId =
  | "loops"
  | "tools"
  | "router"
  | "memory"
  | "selfimprove"
  | "evolve"
  | "security"
  | "doctor"
  | "dag"
  | "verify"
  | "goal"
  | "hillclimb"
  | "scheduler"
  | "chain";

const TABS: { id: TabId; label: string }[] = [
  { id: "loops", label: "Loops" },
  { id: "tools", label: "Tools" },
  { id: "router", label: "Router" },
  { id: "memory", label: "Memory" },
  { id: "selfimprove", label: "Self-improve" },
  { id: "evolve", label: "Evolve" },
  { id: "security", label: "Security" },
  { id: "doctor", label: "Loop Doctor" },
  { id: "dag", label: "DAG" },
  { id: "verify", label: "Verify" },
  { id: "goal", label: "Goal" },
  { id: "hillclimb", label: "Hill Climb" },
  { id: "scheduler", label: "Scheduler" },
  { id: "chain", label: "Chain" },
];

interface PanelDrawerProps {
  sessionId: string | null;
}

export function PanelDrawer({ sessionId }: PanelDrawerProps) {
  const [active, setActive] = useState<TabId>("loops");

  // Per-tab counts for the badges.
  const counts = useRunStore((s) => ({
    loops: s.loops.length,
    tools: s.tools.length,
    router: s.router.length,
    memory: s.memory.length,
    selfimprove: s.selfimprove.length + s.agentEvents.length,
    evolve: s.evolve.length,
    security: s.gates.length,
    doctor: s.loopDoctor.length,
    dag: s.dag ? s.dag.nodes.length : 0,
    verify: s.verify ? s.verify.findings.length : 0,
    goal: s.goalMet ? 1 : 0,
    hillclimb: s.hillClimb.length,
    scheduler: s.schedulerTriggers?.triggers.length ?? 0,
    chain: s.chainResults.length,
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
        {active === "loops" && <LoopsPanel sessionId={sessionId} />}
        {active === "tools" && <ToolsPanel />}
        {active === "router" && <RouterPanel />}
        {active === "memory" && <MemoryPanel />}
        {active === "selfimprove" && <SelfImprovePanel />}
        {active === "evolve" && <EvolvePanel />}
        {active === "security" && <SecurityPanel />}
        {active === "doctor" && <LoopDoctorPanel />}
        {active === "dag" && <DagPanel />}
        {active === "verify" && <VerifyPanel />}
        {active === "goal" && <GoalPanel />}
        {active === "hillclimb" && <HillClimbPanel />}
        {active === "scheduler" && <SchedulerPanel />}
        {active === "chain" && <ChainComposerPanel />}
      </div>
    </div>
  );
}
