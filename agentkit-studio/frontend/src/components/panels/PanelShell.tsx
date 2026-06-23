/**
 * Shared panel chrome. Each comprehensive panel (SPEC §5.5) renders its own data
 * but shares the empty-state contract: an italic hint until its first event lands.
 */
import type { ReactNode } from "react";
import "./panels.css";

interface PanelShellProps {
  empty: boolean;
  emptyHint: string;
  children: ReactNode;
}

export function PanelShell({ empty, emptyHint, children }: PanelShellProps) {
  if (empty) {
    return <div className="panel-empty">{emptyHint}</div>;
  }
  return <div className="panel-scroll">{children}</div>;
}
