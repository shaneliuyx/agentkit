import { describe, expect, test } from "vitest";
import { statusState } from "./LoopDoctorPanel";

describe("LoopDoctor statusState mapping", () => {
  test("pass maps to the green done state", () => {
    expect(statusState("pass")).toBe("done");
  });

  test("warn maps to the amber warn state", () => {
    expect(statusState("warn")).toBe("warn");
  });

  test("fail maps to the red error state", () => {
    expect(statusState("fail")).toBe("error");
  });
});
