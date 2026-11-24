// The ?show=full_look deep link: the run history's "N met every must-have"
// count lands on Review showing exactly that set — passed every hard gate AND
// no must-have gap — behind a dismissible chip, so the criterion is visible
// and the full list is one click away.
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";

function card(overrides: Partial<ReviewCard>): ReviewCard {
  return {
    rank: 1,
    candidate_id: "c1",
    name: "Anita Rao",
    band: "Strong",
    band_label: "Strong match",
    score: 82,
    confidence: "high",
    flag: false,
    chips: [],
    gates: { total: 1, failed: [] },
    must_have_gaps: [],
    decided: null,
    decided_reason: null,
    decided_ts: null,
    ...overrides,
  };
}

// Full look = every gate passed AND no must-have gap. The other two each fail
// one half of the criterion.
const FULL_LOOK = card({});
const GAPPED = card({
  rank: 2, candidate_id: "c2", name: "Vikram Shah", band: "Possible",
  band_label: "Possible", score: 61, must_have_gaps: ["Chartered Accountant"],
});
const GATE_FAILED = card({
  rank: 3, candidate_id: "c3", name: "Meera Roach", band: "Not a fit",
  band_label: "Gate not met", score: 45,
  gates: { total: 1, failed: ["CA qualification stated"] },
});

const REVIEW: ReviewResponse = {
  ready: true,
  req_id: REQ_ID,
  title: "Financial Analyst",
  run_id: "run-1",
  location_display: "India",
  meta: { screened: 3, rubric: "v2" },
  counts: {
    strong: 1, possible: 1, not_a_fit: 1, needs_look: 0,
    gates_passed: 2, gate_not_met: 1, total: 3,
  },
  decision_counts: { forward: 0, discuss: 0, not: 0, awaiting: 3 },
  cards: [FULL_LOOK, GAPPED, GATE_FAILED],
};

function setup(route: string) {
  mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/review`]: REVIEW,
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route }
  );
}

describe("Review full-look deep link", () => {
  it("?show=full_look shows only candidates who met every must-have, behind a chip", async () => {
    setup(`/reqs/${REQ_ID}/review?show=full_look`);
    expect(await screen.findByText("Anita Rao")).toBeInTheDocument();
    // a must-have gap or a failed gate both exclude a candidate from the set
    expect(screen.queryByText("Vikram Shah")).not.toBeInTheDocument();
    expect(screen.queryByText("Meera Roach")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Showing: met every must-have/ })).toBeInTheDocument();
  });

  it("dismissing the chip restores the full list", async () => {
    setup(`/reqs/${REQ_ID}/review?show=full_look`);
    await userEvent.click(await screen.findByRole("button", { name: /Showing: met every must-have/ }));
    expect(await screen.findByText("Vikram Shah")).toBeInTheDocument();
    expect(screen.getByText("Meera Roach")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Showing: met every must-have/ })).not.toBeInTheDocument();
  });

  it("no chip and no filtering without the query param", async () => {
    setup(`/reqs/${REQ_ID}/review`);
    expect(await screen.findByText("Vikram Shah")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Showing: met every must-have/ })).not.toBeInTheDocument();
  });
});
