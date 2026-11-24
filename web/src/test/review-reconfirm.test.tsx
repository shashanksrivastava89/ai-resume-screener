// Decisions carried across a re-screen (2026-07-20): the review card shows a
// "Reconfirm" badge + a "reconfirm all" banner; one click reconfirms them.
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";

function card(o: Partial<ReviewCard>): ReviewCard {
  return {
    rank: 1, candidate_id: "c1", name: "Anita Rao", band: "Strong", band_label: "Strong match",
    score: 82, confidence: "high", flag: false, chips: [], gates: { total: 0, failed: [] },
    must_have_gaps: [], decided: null, decided_reason: null, decided_ts: null, ...o,
  };
}

function review(cards: ReviewCard[]): ReviewResponse {
  return {
    ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "run-2",
    location_display: "India", meta: { screened: cards.length, rubric: "v1" },
    counts: { strong: 1, possible: 0, not_a_fit: 0, needs_look: 0, gates_passed: cards.length, gate_not_met: 0, total: cards.length },
    decision_counts: { forward: 1, discuss: 0, not: 0, awaiting: 0 }, cards,
  };
}

describe("Decision reconfirm (carried across a re-screen)", () => {
  it("shows the reconfirm badge + banner and reconfirms all in one click", async () => {
    const api = mockApi({
      [`/api/roles/${REQ_ID}`]: roleFixture(),
      [`/api/roles/${REQ_ID}/review`]: review([
        card({ decided: "forward", decided_reason: "Strong GL", decided_reconfirm: true }),
      ]),
      [`/api/roles/${REQ_ID}/decisions/reconfirm-all`]: {
        ok: true, reconfirmed: 1, decision_counts: { forward: 1, discuss: 0, not: 0, awaiting: 0 },
      },
    });
    renderPage(
      <Routes>{workspaceRoutes()}<Route path="/reqs" element={<div>list</div>} /></Routes>,
      { route: `/reqs/${REQ_ID}/review` }
    );
    await screen.findByText("Anita Rao");
    // per-card badge (exact name distinguishes it from "Reconfirm all")
    expect(screen.getByRole("button", { name: "Reconfirm" })).toBeInTheDocument();
    // top banner
    expect(screen.getByText(/carried over from earlier scores/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Reconfirm all" }));
    expect(api.callsTo(`/api/roles/${REQ_ID}/decisions/reconfirm-all`).length).toBe(1);
  });
});
