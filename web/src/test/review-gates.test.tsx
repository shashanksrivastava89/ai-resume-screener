// P33: the gate-aware Review tab — gate filter pills only when the run has
// gates, the amber "Gate not met" chip with the transparency copy on rows that
// STAY listed, and the drawer's gate banner.
import { describe, it, expect } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { CandidateDetail, ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";
const GATE = "CA qualification stated";

function card(overrides: Partial<ReviewCard>): ReviewCard {
  return {
    rank: 1,
    candidate_id: "c-pass",
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

const PASSER = card({});
const FAILER = card({
  rank: 2, candidate_id: "c-fail", name: "Vikram Shah",
  band: "Not a fit", band_label: "Gate not met", score: 45,
  gates: { total: 1, failed: [GATE] },
});

function review(cards: ReviewCard[]): ReviewResponse {
  const failed = cards.filter((c) => c.gates?.failed.length).length;
  return {
    ready: true,
    req_id: REQ_ID,
    title: "Financial Analyst",
    run_id: "run-1",
    location_display: "India",
    meta: { screened: cards.length, rubric: "v1" },
    counts: {
      strong: cards.filter((c) => c.band === "Strong").length,
      possible: 0,
      not_a_fit: cards.filter((c) => c.band === "Not a fit").length,
      needs_look: 0,
      gates_passed: cards.length - failed,
      gate_not_met: failed,
      total: cards.length,
    },
    decision_counts: { forward: 0, discuss: 0, not: 0, awaiting: cards.length },
    cards,
  };
}

const FAILER_DETAIL: CandidateDetail = {
  candidate_id: "c-fail",
  name: "Vikram Shah",
  band: "Not a fit",
  band_label: "Gate not met",
  score: 45,
  confidence: "high",
  sub: "Covers 45% of the checklist · confidence high",
  verdict: "Solid accounting depth but the stated CA minimum isn't shown.",
  flag: false,
  flag_note: "",
  gate_warn:
    `Under the hard minimum: ${GATE}. A gate counts only when it's explicitly shown ` +
    "in the résumé — partial evidence doesn't pass it. Shown for transparency — you " +
    "can still open the evidence and override with a reason.",
  strengths: [],
  watchouts: [],
  req_summary: "1 of 2 met",
  reqs: [
    { label: "US GAAP", match: "full", glyph: "✓", must: true, gate: false, evidence: "US GAAP", note: "" },
    { label: GATE, match: "partial", glyph: "~", must: false, gate: true, evidence: "CA", note: "" },
  ],
  notes: [],
  decision: null,
  resume_media_type: null,
};

function setup(cards: ReviewCard[]) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/review`]: review(cards),
    [`/api/roles/${REQ_ID}/review/c-fail`]: FAILER_DETAIL,
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

describe("Review hard gates", () => {
  it("keeps the gate-failed row listed with the amber chip and transparency copy", async () => {
    setup([PASSER, FAILER]);
    // failed candidate STAYS listed, labelled Gate not met
    const name = await screen.findByText("Vikram Shah");
    const row = name.closest(".card-hover")! as HTMLElement;
    // the band badge still reads "Gate not met"; the graded chip shows the ratio
    expect(within(row).getByText("Gate not met")).toBeInTheDocument();
    expect(within(row).getByText("Gates 0/1")).toBeInTheDocument();
    // 2026-09-06: ONE line on the card naming what is not met; the ranking rule is
    // said once beside the gate filter (an info dot), not on every card
    expect(within(row).getByText("Not evidenced", { selector: "strong" })).toBeInTheDocument();
    expect(within(row).getByText(new RegExp(GATE))).toBeInTheDocument();
    expect(within(row).queryByText(/ranks below everyone who meets all gates/)).toBeNull();
    expect(screen.getByLabelText("How hard gates affect ranking")).toBeInTheDocument();
    // the card wears its band: a gate-failed candidate is "Not a fit" → slate wash
    expect(row.className).toContain("rev-band-not");
    // the passer wears the all-met gates chip
    const passRow = screen.getByText("Anita Rao").closest(".card-hover")! as HTMLElement;
    expect(within(passRow).getByText("Gates 1/1")).toBeInTheDocument();
  });

  it("marks rows that carry a reviewer note with a comment icon", async () => {
    const noted = card({
      candidate_id: "c-note", name: "Noted One", rank: 2,
      decided: "discuss", decided_reason: "Check GST depth", decided_by: "ta@example.com",
    });
    setup([PASSER, noted]);
    const row = (await screen.findByText("Noted One")).closest(".card-hover")! as HTMLElement;
    expect(within(row).getByLabelText("Has a reviewer note")).toBeInTheDocument();
    // a row with no note carries no icon
    const passRow = screen.getByText("Anita Rao").closest(".card-hover")! as HTMLElement;
    expect(within(passRow).queryByLabelText("Has a reviewer note")).toBeNull();
  });

  it("shows a graded ratio chip for a partial gate pass (1 of 2)", async () => {
    const partial = card({
      rank: 2, candidate_id: "c-partial", name: "Arabella Mittal",
      band: "Not a fit", band_label: "Gate not met", score: 60,
      gates: { total: 2, failed: [GATE] },
    });
    setup([PASSER, partial]);
    const row = (await screen.findByText("Arabella Mittal")).closest(".card-hover")! as HTMLElement;
    expect(within(row).getByText("Gates 1/2")).toBeInTheDocument();
    expect(within(row).getByText("Not evidenced", { selector: "strong" })).toBeInTheDocument();
    expect(within(row).getByText(new RegExp(GATE))).toBeInTheDocument();
  });

  it("shows the gate pills only when the run has gates, and they filter", async () => {
    setup([PASSER, FAILER]);
    await screen.findByText("Vikram Shah");
    expect(screen.getByRole("button", { name: "Gates · all" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Passed · 1" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Gate not met · 1" }));
    expect(screen.getByText("Vikram Shah")).toBeInTheDocument();
    expect(screen.queryByText("Anita Rao")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Passed · 1" }));
    expect(screen.getByText("Anita Rao")).toBeInTheDocument();
    expect(screen.queryByText("Vikram Shah")).not.toBeInTheDocument();
  });

  it("hides the gate pills when the checklist has no gates", async () => {
    setup([card({ gates: { total: 0, failed: [] } })]);
    await screen.findByText("Anita Rao");
    expect(screen.queryByRole("button", { name: "Gates · all" })).not.toBeInTheDocument();
    expect(screen.queryByText("Gates 0/0")).not.toBeInTheDocument();
  });

  it("opens the drawer with the amber gate banner and the Gate tag", async () => {
    setup([PASSER, FAILER]);
    await userEvent.click(await screen.findByText("Vikram Shah"));
    // the banner body is unique to the drawer (the row copy words it differently)
    expect(await screen.findByText(/Under the hard minimum:/)).toBeInTheDocument();
    expect(screen.getByText(/partial evidence doesn't pass it/)).toBeInTheDocument();
    expect(screen.getByText("Gate")).toBeInTheDocument(); // the checklist row tag
  });
});
