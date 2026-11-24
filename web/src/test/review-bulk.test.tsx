// P34, reworked 2026-09-07 (item 14): marking many, then ONE decision. "Mark all"
// covers the filtered list; a decision on any marked card sends ONE POST to
// /decisions/batch with optimistic row chips (rolled back on error). The recorded
// note shows on the row.
import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage, apiError, type RouteValue } from "./utils";
import { roleFixture } from "./fixtures";
import type { ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";
const BATCH_PATH = `/api/roles/${REQ_ID}/decisions/batch`;

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
    gates: { total: 0, failed: [] },
    must_have_gaps: [],
    decided: null,
    decided_reason: null,
    decided_ts: null,
    ...overrides,
  };
}

const CARDS = [
  card({}),
  card({ rank: 2, candidate_id: "c2", name: "Vikram Shah", score: 74, band: "Possible", band_label: "Possible match" }),
  card({ rank: 3, candidate_id: "c3", name: "Meera Roach", score: 61, band: "Possible", band_label: "Possible match" }),
];

function review(cards: ReviewCard[]): ReviewResponse {
  return {
    ready: true,
    req_id: REQ_ID,
    title: "Financial Analyst",
    run_id: "run-1",
    location_display: "India",
    meta: { screened: cards.length, rubric: "v1" },
    counts: {
      strong: 1, possible: 2, not_a_fit: 0, needs_look: 0,
      gates_passed: cards.length, gate_not_met: 0, total: cards.length,
    },
    decision_counts: { forward: 0, discuss: 0, not: 0, awaiting: cards.length },
    cards,
  };
}

function setup(cards: ReviewCard[], batchRoute: RouteValue) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/review`]: review(cards),
    [BATCH_PATH]: batchRoute,
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/review` }
  );
  return api;
}

describe("Review bulk decisions", () => {
  it("select-all → bulk Discuss makes ONE batch POST with optimistic chips", async () => {
    const api = setup(CARDS, {
      ok: true,
      applied: 3,
      decisions: {
        c1: { action: "discuss", reason: "" },
        c2: { action: "discuss", reason: "" },
        c3: { action: "discuss", reason: "" },
      },
    });
    await screen.findByText("Anita Rao");
    await userEvent.click(screen.getByRole("button", { name: "Mark all" }));
    expect(screen.getByText("3 marked")).toBeInTheDocument();
    // deciding on ANY marked card decides for all of them
    const row = (screen.getByText("Anita Rao").closest(".rev-card")) as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Discuss" }));
    // chips appear immediately (optimistic), and the footer count moved
    expect(screen.getAllByText("· Discuss")).toHaveLength(3);
    expect(screen.getByText("Discuss 3")).toBeInTheDocument();

    await waitFor(() => expect(api.callsTo(BATCH_PATH)).toHaveLength(1));
    expect(api.callsTo(BATCH_PATH)[0].body).toEqual({
      candidate_ids: ["c1", "c2", "c3"],
      action: "discuss",
    });
    // ONE call total — never a loop over the single-decision route
    expect(api.callsTo(`/api/roles/${REQ_ID}/decisions`)).toHaveLength(0);
  });

  it("rolls the rows back when the server refuses the batch", async () => {
    setup(CARDS, apiError(404, "Not in this run: c3"));
    await screen.findByText("Anita Rao");
    await userEvent.click(screen.getByRole("button", { name: "Mark all" }));
    const row = (screen.getByText("Anita Rao").closest(".rev-card")) as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Move forward" }));

    // all-or-nothing: the optimistic chips must disappear again
    await waitFor(() =>
      expect(screen.queryAllByText("· Move forward")).toHaveLength(0));
    // ...and the marks come back so the reviewer can retry
    expect(await screen.findByText("3 marked")).toBeInTheDocument();
  });

  it("shows the recorded note on the row, single line", async () => {
    setup([card({
      decided: "forward",
      decided_reason: "Strong GST filing evidence",
      decided_ts: "2026-07-12T09:00:00+00:00",
    })], { ok: true });
    const row = (await screen.findByText("Anita Rao")).closest(".card-hover")! as HTMLElement;
    expect(within(row).getByText("Noted: “Strong GST filing evidence”")).toBeInTheDocument();
  });
});
