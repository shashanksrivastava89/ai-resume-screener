// The "closer look" flag correlates with the Context notes: the yellow banner
// links to the notes, and each critic-flagged note is marked "Flagged" (kept in
// the score — a flag is about reasoning quality, not the sign of the points).
import { describe, it, expect } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { CandidateDetail, ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";

const CARD: ReviewCard = {
  rank: 1, candidate_id: "c1", name: "Vaani Daiken", band: "Possible",
  band_label: "Possible match", score: 64, confidence: "high", flag: true,
  chips: [], gates: { total: 0, failed: [] }, must_have_gaps: [],
  decided: null, decided_reason: null, decided_ts: null,
};

// Two flagged notes, BOTH positive — a flag is about reasoning, not sign.
const DETAIL: CandidateDetail = {
  candidate_id: "c1", name: "Vaani Daiken", band: "Possible", band_label: "Possible match",
  score: 64, confidence: "high", sub: "Covers 64% of the checklist · confidence high",
  verdict: "Strong evidenced depth across the checklist.",
  flag: true,
  flag_note: "2 score adjustments are counted but worth your eye.",
  // 2026-09-07: the server sends each reason WITH somewhere to go (item 9).
  flag_items: [{
    text: "2 score adjustments are counted but worth your eye — the reasoning is thin or stretched.",
    where: "score", action: "See the flagged notes",
  }],
  gate_warn: "", strengths: [], watchouts: [], req_summary: "4 of 6 met", reqs: [],
  notes: [
    { signal: "Regulated-environment exposure", points: 3, evidence: "SOX controls", rationale: "r", suspended: false, flagged: true },
    { signal: "Growing scope over time", points: 2, evidence: "led a team", rationale: "r", suspended: false, flagged: true },
    { signal: "Recent, current skills", points: 2, evidence: "IFRS 2025", rationale: "r", suspended: false, flagged: false },
  ],
  decision: null, resume_media_type: null,
};

function review(): ReviewResponse {
  return {
    ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "r1",
    location_display: "India", meta: { screened: 1, rubric: "v1" },
    counts: { strong: 0, possible: 1, not_a_fit: 0, needs_look: 1, gates_passed: 1, gate_not_met: 0, total: 1 },
    decision_counts: { forward: 0, discuss: 0, not: 0, awaiting: 1 },
    cards: [CARD],
  };
}

function setup() {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/review`]: review(),
    [`/api/roles/${REQ_ID}/review/c1`]: DETAIL,
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

describe("Flagged context notes", () => {
  it("banner links to the notes and only flagged notes carry the chip", async () => {
    setup();
    await userEvent.click(await screen.findByText("Vaani Daiken"));

    // 2026-09-07 (item 9): each reason is its own line with somewhere to GO — the
    // critic's raw sentence told nobody what to do about it.
    expect(await screen.findByText(/2 score adjustments are counted but worth your eye/))
      .toBeInTheDocument();
    // 2026-09-07: the action opens a plain-language explainer first — "where do I
    // go" was never the reader's question; "what does this mean" was.
    await userEvent.click(screen.getByRole("button", { name: /What does this mean/ }));
    const help = await screen.findByRole("dialog", { name: "About the extra points" });
    expect(help).toHaveTextContent(/add or take away a few points/);
    expect(help).toHaveTextContent(/What to do:/);
    await userEvent.click(within(help).getByRole("button", { name: "Open Score" }));

    // exactly the two flagged notes are marked; the unflagged one is not
    const flags = await screen.findAllByText("Flagged");
    expect(flags).toHaveLength(2);

    // The flagged notes are positive — that is expected, not a bug: an adjustment
    // adds OR deducts. Since 2026-09-11 (ask 4) that explainer is on the info dot
    // rather than printed above the list twice; it is still one hover away.
    expect(screen.getByLabelText("What the score adjustments are")).toBeInTheDocument();
    const notes = screen.getByText("Score adjustments").parentElement!.parentElement!;
    expect(within(notes).getByText("+3")).toBeInTheDocument();
    expect(within(notes).getByText("Growing scope over time")).toBeInTheDocument();
  });
});
