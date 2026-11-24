// The team's decisions (2026-10-21). Outside a requisition's team — an administrator,
// Legal — the server sends every candidate with the AI's read but no decision at all,
// says `decisions_visible: false`, and sends the sentence to show instead. The UI must
// never let an absent decision read as "awaiting": no decision tallies, no Move
// forward / Discuss / Not a fit, no "Recently decided" sort, no shortlist — the
// sentence in their place.
import { describe, it, expect } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { DEFAULT_ME, mockApi, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import type { CandidateDetail, Me, ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";
const NOTE = "Decisions on this requisition are shown to its team — the recruiter who owns it, "
  + "the people it's shared with, its hiring manager and HR Heads.";

const ADMIN: Me = {
  ...DEFAULT_ME, user: "admin@example.com", role: "SuperAdmin", roles: ["SuperAdmin"],
  capabilities: ["recruiter", "reqs_all", "reqs_read", "rules_view", "runs_audit",
                 "prefs_own", "verdict_view"],
};

function card(overrides: Partial<ReviewCard>): ReviewCard {
  return {
    rank: 1, candidate_id: "c-anita", name: "Anita Rao", band: "Strong",
    band_label: "Strong match", score: 82, confidence: "high", flag: false, chips: [],
    gates: { total: 0, failed: [] }, must_have_gaps: [], comments: 0,
    ...overrides,
  };
}

const REVIEW: ReviewResponse = {
  ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "run-1",
  location_display: "India", meta: { screened: 2, rubric: "v1" },
  counts: { strong: 1, possible: 1, not_a_fit: 0, needs_look: 0, gates_passed: 2, gate_not_met: 0, total: 2 },
  decisions_visible: false, decisions_note: NOTE,
  cards: [card({}), card({ rank: 2, candidate_id: "c-upkaar", name: "Upkaar Roach", band: "Possible", band_label: "Possible match", score: 70 })],
};

const DETAIL: CandidateDetail = {
  candidate_id: "c-anita", name: "Anita Rao", comments: [], resume_media_type: "pdf",
  decisions_visible: false, decisions_note: NOTE,
};

function setup(route = "review") {
  mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
    [`/api/roles/${REQ_ID}/review`]: REVIEW,
    [`/api/roles/${REQ_ID}/review/c-anita`]: DETAIL,
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/${route}`, me: ADMIN }
  );
}

describe("Review outside the requisition's team", () => {
  it("shows every candidate with the AI's read, and the team's decisions nowhere", async () => {
    setup();
    const row = (await screen.findByText("Anita Rao")).closest(".card-hover") as HTMLElement;
    expect(within(row).getAllByText("82").length).toBeGreaterThan(0);   // the AI's read is there
    expect(within(row).queryByRole("button", { name: "Move forward" })).not.toBeInTheDocument();
    expect(screen.queryByText("Your decisions")).not.toBeInTheDocument();
    expect(screen.queryByText(/Awaiting/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Shortlist/ })).not.toBeInTheDocument();
    expect(screen.getByText(NOTE)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Sort:/ }));
    expect(screen.queryByRole("button", { name: "Recently decided" })).not.toBeInTheDocument();
  });

  it("says why the drawer has no call", async () => {
    setup();
    await userEvent.click(await screen.findByText("Anita Rao"));
    await screen.findByRole("tablist", { name: "Candidate sections" });
    expect(screen.queryByText("Your call:")).not.toBeInTheDocument();
    expect(screen.getAllByText(NOTE).length).toBeGreaterThan(0);
  });

  it("has no shortlist to show — it is the team's decisions", async () => {
    setup("shortlist");
    expect(await screen.findByText("The shortlist is the requisition team's")).toBeInTheDocument();
    expect(screen.getByText(NOTE)).toBeInTheDocument();
  });
});
