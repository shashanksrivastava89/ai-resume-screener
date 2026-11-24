// The design's stale/refresh states on Review: the locked banner with change
// chips, paused decisions opening the "ground shifted" confirm (never a silent
// disable), the workspace stale chip, the Shortlist gate, and the score's dark
// one-line math tip.
import { describe, it, expect, beforeEach } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage, resetApiMock } from "./utils";
import { roleFixture } from "./fixtures";
import type { ReviewCard, ReviewResponse, ReviewStaleness, Staleness } from "../types";

const REQ_ID = "financial-analyst";

const MATH = {
  base: 78, adjustments: 4, suspended_count: 1, preferences: 2,
  before_limits: 84, final: 84, clamped: null, band_label: "Possible match",
};

function card(overrides: Partial<ReviewCard> = {}): ReviewCard {
  return {
    rank: 1, candidate_id: "c1", name: "Anita Rao", band: "Possible",
    band_label: "Possible match", score: 84, confidence: "high", flag: false,
    chips: [], gates: { total: 0, failed: [] }, must_have_gaps: [],
    decided: null, decided_reason: null, decided_ts: null, math: MATH,
    ...overrides,
  };
}

const STALE: ReviewStaleness = {
  mode: "locked",
  summary: "The scoring model changed after these scores were produced.",
  details: [{
    area: "scoring_model",
    headline: "Scoring model v1 → v2",
    changes: [{ path: "bands.strong_at", label: "Strong match starts at", from: "88", to: "85" }],
    publishes: [{ version: 2, author: "hr@example.com", approved_by: "legal@example.com",
                  rationale: "July calibration", summary: null, ts: "2026-07-17T09:00:00+00:00" }],
  }],
};

const ROLE_STALE: Staleness = {
  mode: "locked", changed: ["scoring_model"],
  summary: "The scoring model changed after these scores were produced.",
};

function review(staleness: ReviewStaleness | null, decided = false): ReviewResponse {
  return {
    ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "run-1",
    location_display: "India", meta: { screened: 1, rubric: "v2" },
    counts: { strong: 0, possible: 1, not_a_fit: 0, needs_look: 0,
              gates_passed: 1, gate_not_met: 0, total: 1 },
    decision_counts: decided
      ? { forward: 1, discuss: 0, not: 0, awaiting: 0 }
      : { forward: 0, discuss: 0, not: 0, awaiting: 1 },
    cards: [card(decided ? { decided: "forward" } : {})],
    staleness,
  };
}

function setup(payload: ReviewResponse, route = `/reqs/${REQ_ID}/review`, roleStale = !!payload.staleness) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(roleStale
      ? { staleness: ROLE_STALE, candidate_count: 1 }
      : { candidate_count: 1 }),
    [`/api/roles/${REQ_ID}/review`]: payload,
    [`/api/roles/${REQ_ID}/batch`]: { files: [], groups: [], count: 0 },
    [`/api/roles/${REQ_ID}/screen/preflight`]: {
      will_run: true, reason: "config_changed", files: [],
      counts: { total: 1, new: 0, updated: 0, cached: 1, removed: 0 },
      changed: { checklist: false, rules: false, location: false,
                 scoring_model: true, preferences: false },
      details: STALE.details, stale: { mode: "locked" },
    },
    [`/api/roles/${REQ_ID}/screen/runs`]: { entries: [], note: null },
    [`/api/roles/${REQ_ID}/screen`]: { job_id: "j1", req_id: REQ_ID, status: "running",
      done: 0, total: 1, message: "", error: "" },
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route }
  );
  return api;
}

beforeEach(() => resetApiMock());

describe("Review stale states", () => {
  it("locked: the banner carries change chips; paused decisions open the confirm", async () => {
    const api = setup(review(STALE, true));
    expect(await screen.findByText("The rules changed after this screen ran")).toBeInTheDocument();
    // itemized change chips
    expect(screen.getByText("Scoring model v1 → v2")).toBeInTheDocument();
    expect(screen.getByText("Strong match starts at: 88 → 85")).toBeInTheDocument();
    expect(screen.getByText(/Recording decisions is paused until the re-screen/)).toBeInTheDocument();
    // the workspace header chip
    expect(screen.getByRole("button", { name: /Scores stale · what changed\?/ })).toBeInTheDocument();

    // a paused NEW decision opens the design's confirm — nothing is posted
    await userEvent.click(screen.getByRole("button", { name: "Discuss" }));
    const dialog = await screen.findByRole("dialog", { name: "The ground shifted under these scores" });
    expect(within(dialog).getByText(/Decisions are paused so old numbers never mix/)).toBeInTheDocument();
    expect(api.callsTo(`/api/roles/${REQ_ID}/decisions`)).toHaveLength(0);
  });

  it("proceeding from the confirm primes the Candidates tab (no direct screen POST)", async () => {
    const api = setup(review(STALE, true));
    await userEvent.click(await screen.findByRole("button", { name: "Re-screen with current rules" }));
    const dialog = await screen.findByRole("dialog", { name: "The ground shifted under these scores" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Re-screen 1 candidate" }));
    // lands on Candidates — the re-screen is the ordinary screen click there
    expect(await screen.findByText(/re-screen against the new version|Screen résumés|rules changed/i)).toBeInTheDocument();
    expect(api.callsTo(`/api/roles/${REQ_ID}/screen`).filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("soft: quiet one-liner, no warn banner", async () => {
    setup(review({ ...STALE, mode: "soft" }), `/reqs/${REQ_ID}/review`, false);
    expect(await screen.findByText(/Hiring rules were updated — the next screen picks them up/)).toBeInTheDocument();
    expect(screen.queryByText("The rules changed after this screen ran")).not.toBeInTheDocument();
  });

  it("fresh: no stale chrome at all", async () => {
    setup(review(null));
    expect(await screen.findByText("Anita Rao")).toBeInTheDocument();
    expect(screen.queryByText(/rules changed|rules were updated/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move forward" })).toBeEnabled();
  });

  it("the Shortlist tab gates on stale scores", async () => {
    setup(review(STALE, true), `/reqs/${REQ_ID}/shortlist`);
    expect(await screen.findByText(/A shortlist has to stand on current rules/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-screen with current rules" })).toBeInTheDocument();
  });
});

describe("The score's dark math tip", () => {
  it("hovering the score shows the one-line arithmetic", async () => {
    setup(review(null));
    await screen.findByText("Anita Rao");
    // the rank badge carries the score too now (item 5), so aim at the big one
    await userEvent.hover(screen.getByRole("button", { name: "How this score adds up" }));
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent("78 base +4 signals +2 preference = 84 · click to open the breakdown");
    // recruiter language only — no jargon
    expect(tip).not.toHaveTextContent(/clamp|Layer 2|layer2/i);
  });
});
