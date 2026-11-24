// The drawer's checklist view switches (2026-09-06, pilot feedback item 8): the
// header offers All · Gates m/n · Must-have m/n · Nice-to-have m/n · Gaps, each
// with its own met/total, and picking one narrows the rows to that kind.
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
  rank: 1, candidate_id: "c1", name: "Ananya Sharma", band: "Strong",
  band_label: "Strong match", score: 89, confidence: "high", flag: false,
  chips: [], gates: { total: 1, failed: [] }, must_have_gaps: [],
  decided: null, decided_reason: null, decided_ts: null,
};

const DETAIL: CandidateDetail = {
  candidate_id: "c1", name: "Ananya Sharma", band: "Strong", band_label: "Strong match",
  score: 89, confidence: "high", sub: "Covers 3 of 4", verdict: "Solid.", flag: false, flag_note: "",
  gate_warn: "", strengths: [], watchouts: [], req_summary: "2 of 4 met",
  reqs: [
    { label: "CA qualification", match: "full", glyph: "✓", must: false, gate: true, evidence: "Chartered Accountant, 2016", note: "" },
    { label: "US GAAP depth", match: "full", glyph: "✓", must: true, gate: false, evidence: "US GAAP reporting for 4 years", note: "" },
    { label: "Treasury depth", match: "none", glyph: "✗", must: true, gate: false, evidence: "", note: "No treasury work stated" },
    { label: "Oracle EBS", match: "partial", glyph: "~", must: false, gate: false, evidence: "Oracle modules", note: "" },
  ],
  notes: [], decision: null, resume_media_type: null,
};

function setup() {
  mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/review`]: {
      ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "r1",
      location_display: "India", meta: { screened: 1, rubric: "v2" },
      counts: { strong: 1, possible: 0, not_a_fit: 0, needs_look: 0, gates_passed: 1, gate_not_met: 0, total: 1 },
      decision_counts: { forward: 0, discuss: 0, not: 0, awaiting: 1 },
      cards: [CARD],
    } satisfies ReviewResponse,
    [`/api/roles/${REQ_ID}/review/c1`]: DETAIL,
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/review` }
  );
}

describe("Drawer checklist views", () => {
  it("offers per-kind met/total switches and narrows the rows", async () => {
    setup();
    await userEvent.click(await screen.findByText("Ananya Sharma"));
    const tabs = await screen.findByRole("tablist", { name: "Checklist view" });
    expect(within(tabs).getByRole("tab", { name: "All · 2 of 4 met" })).toHaveAttribute("aria-selected", "true");
    expect(within(tabs).getByRole("tab", { name: "Gates 1/1" })).toBeInTheDocument();
    expect(within(tabs).getByRole("tab", { name: "Must-have 1/2" })).toBeInTheDocument();
    expect(within(tabs).getByRole("tab", { name: "Nice-to-have 0/1" })).toBeInTheDocument();
    expect(within(tabs).getByRole("tab", { name: "Gaps · 2" })).toBeInTheDocument();
    // all four rows to begin with
    for (const label of ["CA qualification", "US GAAP depth", "Treasury depth", "Oracle EBS"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }

    await userEvent.click(within(tabs).getByRole("tab", { name: "Must-have 1/2" }));
    expect(screen.getByText("US GAAP depth")).toBeInTheDocument();
    expect(screen.getByText("Treasury depth")).toBeInTheDocument();
    expect(screen.queryByText("CA qualification")).toBeNull();
    expect(screen.queryByText("Oracle EBS")).toBeNull();

    // gaps = anything not fully met, whatever its kind (a partial counts as a gap)
    await userEvent.click(within(tabs).getByRole("tab", { name: "Gaps · 2" }));
    expect(screen.getByText("Treasury depth")).toBeInTheDocument();
    expect(screen.getByText("Oracle EBS")).toBeInTheDocument();
    expect(screen.queryByText("US GAAP depth")).toBeNull();

    await userEvent.click(within(tabs).getByRole("tab", { name: "Gates 1/1" }));
    expect(screen.getByText("CA qualification")).toBeInTheDocument();
    expect(screen.queryByText("US GAAP depth")).toBeNull();
  });
});
