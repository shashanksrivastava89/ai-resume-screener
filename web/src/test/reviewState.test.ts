// reviewState.ts (2026-09-06): per-requisition memory of the Review tab's working
// state, best-effort and self-cleaning.
import { describe, it, expect, beforeEach } from "vitest";
import { clearReviewState, loadReviewState, saveReviewState } from "../reviewState";

const STATE = { sel: ["a", "b"], tab: "awaiting", band: "Strong", gate: "passed", query: "ra", sortMode: "name" };

beforeEach(() => { sessionStorage.clear(); });

describe("reviewState", () => {
  it("round-trips per requisition and clears", () => {
    saveReviewState("req-1", STATE);
    saveReviewState("req-2", { ...STATE, sel: [] });
    expect(loadReviewState("req-1")).toEqual(STATE);
    expect(loadReviewState("req-2")?.sel).toEqual([]);
    clearReviewState("req-1");
    expect(loadReviewState("req-1")).toBeNull();
    expect(loadReviewState("req-2")).not.toBeNull();
  });

  it("returns nothing for an unknown requisition and removes a corrupt entry", () => {
    expect(loadReviewState("nope")).toBeNull();
    sessionStorage.setItem("rs.review.bad", "{not json");
    expect(loadReviewState("bad")).toBeNull();
    expect(sessionStorage.getItem("rs.review.bad")).toBeNull();
    sessionStorage.setItem("rs.review.shape", JSON.stringify({ sel: "not-an-array" }));
    expect(loadReviewState("shape")).toBeNull();
  });
});
