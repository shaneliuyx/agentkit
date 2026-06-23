import { beforeEach, describe, expect, test } from "vitest";
import { useRunStore } from "./runStore";
import type { StudioEvent } from "../api/types";

const SID = "test";
const frame = <T extends StudioEvent["type"]>(
  type: T,
  payload: Extract<StudioEvent, { type: T }>["payload"],
): StudioEvent => ({ type, session_id: SID, ts: 0, payload }) as StudioEvent;

describe("runStore M7 Wave 1 reducer cases", () => {
  beforeEach(() => {
    useRunStore.getState().reset();
  });

  test("loops stores catalog matches", () => {
    useRunStore.getState().apply(
      frame("loops", {
        matches: [
          { id: "l1", title: "Loop One", summary: "s", url: "u", trigger: "research", keywords: ["x"], score: 0.8 },
        ],
      }),
    );
    expect(useRunStore.getState().loops).toHaveLength(1);
    expect(useRunStore.getState().loops[0].id).toBe("l1");
  });

  test("loop_seed records the seed", () => {
    useRunStore.getState().apply(
      frame("loop_seed", {
        loop_id: "l1",
        steps: [{ id: "s1", description: "d", depends_on: [], role: "researcher" }],
      }),
    );
    expect(useRunStore.getState().loopSeed?.loop_id).toBe("l1");
  });

  test("tool_result merges into its preceding open tool_call", () => {
    const store = useRunStore.getState();
    store.apply(frame("tool_call", { step_id: "s1", tool: "web_search", args: { query: "x" } }));
    store.apply(
      frame("tool_result", { step_id: "s1", tool: "web_search", summary: "found", n_results: 5, notice: "", rejected: false }),
    );
    const tools = useRunStore.getState().tools;
    expect(tools).toHaveLength(1); // merged, not appended
    expect(tools[0].summary).toBe("found");
    expect(tools[0].n_results).toBe(5);
    expect(tools[0].args).toEqual({ query: "x" }); // args preserved from the call
  });

  test("two calls of the same tool pair with their own results in order", () => {
    const store = useRunStore.getState();
    store.apply(frame("tool_call", { step_id: "s1", tool: "web_search", args: { query: "a" } }));
    store.apply(frame("tool_call", { step_id: "s1", tool: "web_search", args: { query: "b" } }));
    store.apply(
      frame("tool_result", { step_id: "s1", tool: "web_search", summary: "ra", n_results: 1, notice: "", rejected: false }),
    );
    store.apply(
      frame("tool_result", { step_id: "s1", tool: "web_search", summary: "rb", n_results: 2, notice: "DDG fallback", rejected: false }),
    );
    const tools = useRunStore.getState().tools;
    expect(tools).toHaveLength(2);
    // First result fills the first open call; second fills the second.
    expect(tools[0].summary).toBe("ra");
    expect(tools[1].summary).toBe("rb");
    expect(tools[1].notice).toBe("DDG fallback");
  });

  test("tool_result with no preceding call appends a result-only entry", () => {
    useRunStore.getState().apply(
      frame("tool_result", { step_id: "s1", tool: "web_search", summary: "orphan", n_results: 0, notice: "", rejected: false }),
    );
    const tools = useRunStore.getState().tools;
    expect(tools).toHaveLength(1);
    expect(tools[0].summary).toBe("orphan");
  });
});
