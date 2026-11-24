// F2: a single decision is optimistic (the row chip appears instantly, the
// server echo reconciles the note + counts, a failure rolls back) and — the
// point of the fix — it never triggers a full-page "postback": the Workspace
// stays mounted through the background role reload, so the list, drawer and
// filters survive.
import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage, apiError, type RouteValue } from "./utils";
import { roleFixture } from "./fixtures";
import type { ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";
const ROLE_PATH = `/api/roles/${REQ_ID}`;
const REVIEW_PATH = `/api/roles/${REQ_ID}/review`;
const DECIDE_PATH = `/api/roles/${REQ_ID}/decisions`;

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
      strong: 1, possible: 1, not_a_fit: 0, needs_look: 0,
      gates_passed: cards.length, gate_not_met: 0, total: cards.length,
    },
    decision_counts: { forward: 0, discuss: 0, not: 0, awaiting: cards.length },
    cards,
  };
}

function decisionResponse(action: string, reason = "") {
  return {
    ok: true,
    action,
    decision: { candidate_id: "c1", action, reason, ts: "2026-07-14T09:00:00+00:00" },
    decision_counts: { forward: action === "forward" ? 1 : 0, discuss: 0, not: 0, awaiting: 1 },
  };
}

function setup(opts: { decideRoute?: RouteValue; roleRoute?: RouteValue } = {}) {
  const api = mockApi({
    [ROLE_PATH]: opts.roleRoute ?? roleFixture(),
    [REVIEW_PATH]: review(CARDS),
    [DECIDE_PATH]: opts.decideRoute ?? decisionResponse("forward"),
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

function rowOf(name: string): HTMLElement {
  return (screen.getByText(name).closest(".card-hover")! as HTMLElement);
}

describe("Review single decision", () => {
  it("offers no decisions on a closed requisition (2026-10-16)", async () => {
    const base = roleFixture();
    const api = setup({ roleRoute: { ...base, lifecycle: { ...base.lifecycle, status: "closed",
      outcome: { kind: "hired", hired_count: 1 }, closed_at: "2026-10-16T09:00:00+00:00" } } });
    await screen.findByText("Anita Rao");
    expect(screen.getByText(/screening, decisions and sharing are frozen/)).toBeInTheDocument();
    expect(within(rowOf("Anita Rao")).queryByRole("button", { name: "Move forward" })).not.toBeInTheDocument();
    await userEvent.keyboard("j1");
    expect(api.callsTo(DECIDE_PATH)).toHaveLength(0);
  });

  it("flips the row chip optimistically and reconciles the note", async () => {
    const api = setup({ decideRoute: decisionResponse("forward", "server-noted") });
    await screen.findByText("Anita Rao");
    const row = rowOf("Anita Rao");
    await userEvent.click(within(row).getByRole("button", { name: "Move forward" }));
    // optimistic: the chip appears before the network resolves
    expect(within(rowOf("Anita Rao")).getByText("· Move forward")).toBeInTheDocument();

    await waitFor(() => expect(api.callsTo(DECIDE_PATH)).toHaveLength(1));
    expect(api.callsTo(DECIDE_PATH)[0].body).toMatchObject({ candidate_id: "c1", action: "forward" });
    // the server's note reconciles onto the row
    expect(await within(rowOf("Anita Rao")).findByText("Noted: “server-noted”")).toBeInTheDocument();
    // the stage pill in the filter row reflects the server counts
    expect(screen.getByText("Move forward 1")).toBeInTheDocument();
    // blind review lives in this tab's filter row (moved out of the header)
    expect(screen.getByRole("switch")).toBeInTheDocument();
    expect(screen.getByText("Blind review")).toBeInTheDocument();
  });

  it("rolls the row back and shows an error when the decision fails", async () => {
    setup({ decideRoute: apiError(500, "Something went wrong on our side.") });
    await screen.findByText("Anita Rao");
    await userEvent.click(within(rowOf("Anita Rao")).getByRole("button", { name: "Move forward" }));
    // the optimistic chip disappears again
    await waitFor(() =>
      expect(within(rowOf("Anita Rao")).queryByText("· Move forward")).not.toBeInTheDocument());
    expect(screen.getByText("Something went wrong on our side.")).toBeInTheDocument();
  });

  it("does NOT unmount the workspace on the background role reload (no postback)", async () => {
    let roleCalls = 0;
    const api = setup({
      // 1st call: normal fixture. 2nd (the background reloadRole): never resolves,
      // so if the old code path ran it would pin the full-page spinner forever.
      roleRoute: () => {
        roleCalls += 1;
        return roleCalls === 1 ? roleFixture() : new Promise(() => {});
      },
    });
    await screen.findByText("Anita Rao");
    await userEvent.click(within(rowOf("Anita Rao")).getByRole("button", { name: "Move forward" }));

    await waitFor(() => expect(api.callsTo(ROLE_PATH).length).toBeGreaterThanOrEqual(2));
    // the list is STILL on screen and the full-page spinner never showed
    expect(screen.getByText("Anita Rao")).toBeInTheDocument();
    expect(screen.queryByText("Loading requisition…")).not.toBeInTheDocument();
  });

  it("keyboard '1' decides the active row through the same optimistic path", async () => {
    const api = setup();
    await screen.findByText("Anita Rao");
    await userEvent.keyboard("j"); // focus the first row
    await userEvent.keyboard("1"); // Move forward
    await waitFor(() => expect(api.callsTo(DECIDE_PATH)).toHaveLength(1));
    expect(api.callsTo(DECIDE_PATH)[0].body).toMatchObject({ candidate_id: "c1", action: "forward" });
  });
});
