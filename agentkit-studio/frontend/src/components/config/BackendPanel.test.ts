import { describe, expect, test } from "vitest";
import { JUDGE_DEFAULT, judgeSelection } from "./BackendPanel";

describe("judgeSelection request-body mapping", () => {
  test("default sentinel sends nothing so the backend applies its haiku default", () => {
    expect(judgeSelection(JUDGE_DEFAULT)).toBeUndefined();
  });

  test("a named profile becomes a {profile} selection", () => {
    expect(judgeSelection("haiku")).toEqual({ profile: "haiku" });
  });
});
