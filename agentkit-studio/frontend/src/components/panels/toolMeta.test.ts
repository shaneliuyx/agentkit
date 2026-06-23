import { describe, expect, test } from "vitest";
import { toolBadgeLabel, toolBadgeTitle, toolIcon } from "./toolMeta";

describe("toolMeta — generic over tool name", () => {
  test("known tools get distinct icons, unknown falls back to wrench", () => {
    expect(toolIcon("web_search")).toBe("🔍");
    expect(toolIcon("read_file")).toBe("📄");
    expect(toolIcon("write_file")).toBe("✏️");
    expect(toolIcon("some_future_tool")).toBe("🛠");
  });

  test("single tool → icon + name + count", () => {
    expect(toolBadgeLabel({ web_search: 2 })).toBe("🔍 web_search (2)");
    expect(toolBadgeLabel({ read_file: 1 })).toBe("📄 read_file (1)");
  });

  test("multiple tools → generic count of the total calls", () => {
    expect(toolBadgeLabel({ web_search: 2, read_file: 1, write_file: 2 })).toBe(
      "🛠 5 tools",
    );
  });

  test("tooltip lists the full per-tool breakdown", () => {
    expect(toolBadgeTitle({ web_search: 2, write_file: 1 })).toBe(
      "web_search: 2, write_file: 1",
    );
  });
});
