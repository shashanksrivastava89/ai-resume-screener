// 2026-07-19: the simplified score summary in the Review drawer (hero number +
// band badge + contribution chips + threshold meter) and the design's
// "Show the math" / "Hide the math" fold over the full arithmetic.
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { CandidateDetail, ReviewCard, ReviewResponse, ScoreMath } from "../types";

const REQ_ID = "financial-analyst";

const MATH: ScoreMath = {
  as_of: { scoring_model_version: 1, checklist: "v2", approximate: false },
  base: {
    total: 79.1,
    explainer: "Each checklist line contributes its share.",
    lines: [
      { label: "US GAAP depth", kind: "must_have", weight_pct: 60, credit: "full", credit_value: 1, points: 60 },
      { label: "Oracle EBS", kind: "nice_to_have", weight_pct: 40, credit: "partial", credit_value: 0.5, points: 19.1 },
    ],
  },
  adjustments: { net: 8, cap_total: 8, cap_note: "capped at ±8 in total", lines: [
    { label: "Regulated-environment exposure", points: 8, flagged: false },
  ], suspended: [], enforcement_notes: [] },
  preferences: { net: 2, per_item_cap: 2, cap_note: "each capped at ±2", lines: [{ label: "Notice period ≤ 30 days", points: 2 }] },
  total: { before_limits: 89.1, final: 89.1, clamped: null },
  band: { result: "Strong", label: "Strong match", strong_at: 88, possible_at: 60,
          capped_by_must_have: null, cap_note: null, gate_note: null, clamp_min: 0, clamp_max: 100 },
  confidence: { level: "high", explainer: "every requirement had clear evidence" },
};

const CARD: ReviewCard = {
  rank: 1, candidate_id: "c1", name: "Ananya Sharma", band: "Strong",
  band_label: "Strong match", score: 89, confidence: "high", flag: false,
  chips: [], gates: { total: 0, failed: [] }, must_have_gaps: [],
  decided: null, decided_reason: null, decided_ts: null,
};

const DETAIL: CandidateDetail = {
  candidate_id: "c1", name: "Ananya Sharma", band: "Strong", band_label: "Strong match",
  score: 89, confidence: "high", sub: "Covers 89% of the checklist",
  verdict: "Strong evidenced depth.", flag: false, flag_note: "", gate_warn: "",
  strengths: [], watchouts: [], req_summary: "6 of 6 met", reqs: [], notes: [],
  decision: null, resume_media_type: null,
  score_math: MATH,
};

function setup() {
  const api = mockApi({
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
  return api;
}

describe("Score summary + the math fold", () => {
  it("shows the simplified summary card with contribution chips and thresholds", async () => {
    setup();
    await userEvent.click(await screen.findByText("Ananya Sharma"));
    // the drawer opens on Evidence; the summary card is one click away, on Score
    await userEvent.click(await screen.findByRole("tab", { name: "Score" }));
    // hero + band badge (the math section, open by default, repeats the total)
    expect((await screen.findAllByText("89.1")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Strong match").length).toBeGreaterThanOrEqual(1);
    // the three contribution chips
    expect(screen.getByText("Checklist 79.1")).toBeInTheDocument();
    expect(screen.getByText("AI signals +8")).toBeInTheDocument();
    expect(screen.getByText("Preferences +2")).toBeInTheDocument();
    // the threshold labels under the meter
    expect(screen.getByText("Possible 60")).toBeInTheDocument();
    expect(screen.getByText("Strong 88")).toBeInTheDocument();
  });

  it("shows the full arithmetic by default, with Hide the math / Show the math to fold it", async () => {
    setup();
    await userEvent.click(await screen.findByText("Ananya Sharma"));
    await userEvent.click(await screen.findByRole("tab", { name: "Score" }));
    // OPEN by default (2026-09-06, pilot feedback: the score has its own section now)
    expect(await screen.findByText("How this score adds up")).toBeInTheDocument();
    expect(screen.getByText(/US GAAP depth/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Hide the math" }));
    expect(screen.queryByText("How this score adds up")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Show the math — how 89 was built/ }));
    expect(await screen.findByText("How this score adds up")).toBeInTheDocument();
  });
});
