/**
 * Shared tool presentation metadata (M7 Wave 1). Generic over tool name — known
 * tools get a distinct icon, anything else falls back to a generic wrench. Used by
 * both the phase-node badge (TopologyGraph) and the Tools panel so icons stay
 * consistent. Add new tools here, not at each call site.
 */
const TOOL_ICONS: Record<string, string> = {
  web_search: "🔍",
  read_file: "📄",
  write_file: "✏️",
};

const GENERIC_TOOL_ICON = "🛠";

export function toolIcon(tool: string): string {
  return TOOL_ICONS[tool] ?? GENERIC_TOOL_ICON;
}

/**
 * Compact badge label for a phase's per-tool counts. One tool → icon + name + count
 * (e.g. "🔍 web_search (2)"); multiple → generic "🛠 N tools" with the total.
 */
export function toolBadgeLabel(counts: Record<string, number>): string {
  const entries = Object.entries(counts);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (entries.length === 1) {
    const [tool, n] = entries[0];
    return `${toolIcon(tool)} ${tool} (${n})`;
  }
  return `${GENERIC_TOOL_ICON} ${total} tools`;
}

/** Full breakdown for the badge tooltip, e.g. "web_search: 2, read_file: 1". */
export function toolBadgeTitle(counts: Record<string, number>): string {
  return Object.entries(counts)
    .map(([tool, n]) => `${tool}: ${n}`)
    .join(", ");
}
