// P34: the hidden review keyboard shortcuts — j/k move the focus ring, o opens
// the drawer, 1 decides the active row, esc clears; all inert while typing in
// an input (the drawer's note field included).
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { CandidateDetail, ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";
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
  card({ rank: 2, candidate_id: "c2", name: "Vikram Shah", score: 74 }),
];

const DETAIL: CandidateDetail = {
  candidate_id: "c1",
  name: "Anita Rao",
  band: "Strong",
  band_label: "Strong match",
  score: 82,
  confidence: "high",
  sub: "Covers 82% of the checklist · confidence high",
  verdict: "Strong treasury profile with current GST filings.",
  flag: false,
  flag_note: "",
  gate_warn: "",
  strengths: [],
  watchouts: [],
  req_summary: "1 of 1 met",
  reqs: [],
  notes: [],
  decision: null,
  resume_media_type: null,
};

function review(cards: ReviewCard[]): ReviewResponse {
  return {
    ready: true,
    req_id: REQ_ID,
    title: "Financial Analyst",
    run_id: "run-1",
    location_display: "India",
    meta: { screened: cards.length, rubric: "v1" },
    counts: {
      strong: 2, possible: 0, not_a_fit: 0, needs_look: 0,
      gates_passed: cards.length, gate_not_met: 0, total: cards.length,
    },
    decision_counts: { forward: 0, discuss: 0, not: 0, awaiting: cards.length },
    cards,
  };
}

function setup() {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/review`]: review(CARDS),
    [`/api/roles/${REQ_ID}/review/c1`]: DETAIL,
    [DECIDE_PATH]: {
      ok: true, action: "forward",
      decision: { candidate_id: "c1", action: "forward", reason: "", ts: "2026-07-14T09:00:00+00:00" },
      decision_counts: { forward: 1, discuss: 0, not: 0, awaiting: CARDS.length - 1 },
    },
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
  return screen.getByText(name).closest(".card-hover")! as HTMLElement;
}

describe("Review keyboard shortcuts", () => {
  it("j/k move the focus ring and 1 decides the active row", async () => {
    const api = setup();
    await screen.findByText("Anita Rao");

    await userEvent.keyboard("j");
    expect(rowOf("Anita Rao").getAttribute("data-active")).toBe("true");

    await userEvent.keyboard("j");
    expect(rowOf("Vikram Shah").getAttribute("data-active")).toBe("true");
    expect(rowOf("Anita Rao").getAttribute("data-active")).toBeNull();

    await userEvent.keyboard("k");
    expect(rowOf("Anita Rao").getAttribute("data-active")).toBe("true");

    await userEvent.keyboard("1");
    await waitFor(() => expect(api.callsTo(DECIDE_PATH)).toHaveLength(1));
    expect(api.callsTo(DECIDE_PATH)[0].body).toEqual({
      candidate_id: "c1",
      action: "forward",
    });
  });

  it("o opens the active row's drawer, esc closes it, esc then clears selection", async () => {
    setup();
    await screen.findByText("Anita Rao");

    await userEvent.keyboard("j");
    await userEvent.keyboard("o");
    expect(await screen.findByText(DETAIL.verdict!)).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText(DETAIL.verdict!)).not.toBeInTheDocument());

    // select a row, then esc clears the selection
    await userEvent.click(screen.getAllByRole("button", { name: "Select" })[0]);
    expect(screen.getByText("1 marked")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByText("1 marked")).not.toBeInTheDocument();
  });

  it("is inert while typing in the drawer's note input", async () => {
    const api = setup();
    await userEvent.click(await screen.findByText("Anita Rao")); // open drawer
    const note = await screen.findByPlaceholderText("Decision note — the team sees this (optional)…");

    await userEvent.type(note, "j1o"); // shortcut keys as TEXT
    expect(note).toHaveValue("j1o");
    expect(api.callsTo(DECIDE_PATH)).toHaveLength(0); // nothing decided
    expect(screen.getByText(DETAIL.verdict!)).toBeInTheDocument(); // drawer still open
  });
});
