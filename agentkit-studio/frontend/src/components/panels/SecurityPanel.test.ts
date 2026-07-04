import { describe, expect, test } from "vitest";
import { isQualityGate, outcomeState } from "./SecurityPanel";

describe("SecurityPanel outcomeState mapping", () => {
  test("accept maps to the green done state", () => {
    expect(outcomeState("accept")).toBe("done");
  });

  test("reject maps to the red error state", () => {
    expect(outcomeState("reject")).toBe("error");
  });

  test("existing allow/deny outcomes still map", () => {
    expect(outcomeState("allow")).toBe("done");
    expect(outcomeState("deny")).toBe("error");
  });
});

describe("SecurityPanel quality-action grouping", () => {
  test("presentation and depth gates are quality actions", () => {
    expect(isQualityGate("content_presentation")).toBe(true);
    expect(isQualityGate("section_presentation")).toBe(true);
    expect(isQualityGate("depth-expansion")).toBe(true);
  });

  test("scoring keep/discard gates are not quality actions", () => {
    expect(isQualityGate("keep_discard")).toBe(false);
  });
});
