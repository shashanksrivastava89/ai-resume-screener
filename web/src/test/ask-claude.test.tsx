// P44: the Ask-Claude panel. The workspace header shows an "Ask Claude" button
// on the Review tab; the panel copies a server-assembled bundle (POST /handoff)
// and a picked starter question is appended. Since 2026-10-23 the button exists
// only while the ask_claude_handoff flag is on (it ships off).
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import type { FeatureFlag, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";

const REVIEW: ReviewResponse = {
  ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "run-1",
  location_display: "India", meta: { screened: 1, rubric: "v1" },
  counts: { strong: 1, possible: 0, not_a_fit: 0, needs_look: 0, gates_passed: 1, gate_not_met: 0, total: 1 },
  decision_counts: { forward: 0, discuss: 0, not: 0, awaiting: 1 },
  cards: [{
    rank: 1, candidate_id: "c1", name: "Anita Rao", band: "Strong", band_label: "Strong match",
    score: 82, confidence: "high", flag: false, chips: [], gates: { total: 0, failed: [] },
    must_have_gaps: [],
    decided: null, decided_reason: null, decided_ts: null,
  }],
};

function askClaudeFlag(on: boolean): { features: FeatureFlag[] } {
  return { features: [{ key: "ask_claude_handoff", name: "Ask Claude — copy to clipboard",
                        description: "", default: on, enabled: on, regions: {},
                        scope: "app", source: "file" }] };
}

function setup(flagOn = true) {
  const api = mockApi({
    "/api/features": askClaudeFlag(flagOn),
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
    [`/api/roles/${REQ_ID}/review`]: REVIEW,
    [`/api/roles/${REQ_ID}/handoff`]: { text: "# Screening context — Financial Analyst" },
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

describe("Ask Claude handoff", () => {
  beforeEach(() => {
    // defineProperty, not assignment: Navigator.clipboard is getter-only, and a
    // reused jsdom (vitest single-fork) rejects Object.assign on it.
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockResolvedValue(undefined) }, configurable: true,
    });
  });

  it("opens the panel from the review header and copies the bundle via POST", async () => {
    const api = setup();
    // the button is shown on the Review tab
    await userEvent.click(await screen.findByRole("button", { name: /Ask Claude/ }));
    // the panel explainer renders
    expect(await screen.findByText(/Copies a redacted context bundle/)).toBeInTheDocument();
    expect(screen.getByText("Phase 2: MCP connector — ask live with your own tokens.")).toBeInTheDocument();

    // pick a starter, then copy
    await userEvent.click(screen.getByRole("button", { name: "Why did #2 outrank #5?" }));
    await userEvent.click(screen.getByRole("button", { name: /Copy context/ }));

    await waitFor(() => expect(api.callsTo(`/api/roles/${REQ_ID}/handoff`)).toHaveLength(1));
    // the POST carries the current blind state
    expect(api.callsTo(`/api/roles/${REQ_ID}/handoff`)[0].body).toEqual({ blind: false });
    // clipboard got the bundle + the appended question
    const written = (navigator.clipboard.writeText as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(written).toContain("# Screening context");
    expect(written).toContain("Why did #2 outrank #5?");
    expect(await screen.findByText(/Copied — paste it/)).toBeInTheDocument();
  });

  it("has no button while the flag is off, and copies nothing", async () => {
    const api = setup(false);
    await screen.findByText("Anita Rao");                 // the Review tab has rendered
    await waitFor(() => expect(api.callsTo("/api/features").length).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: /Ask Claude/ })).not.toBeInTheDocument();
    expect(api.callsTo(`/api/roles/${REQ_ID}/handoff`)).toHaveLength(0);
  });
});
