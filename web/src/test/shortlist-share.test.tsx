// Sharing the shortlist with the hiring manager (HR's UAT ask, 2026-10-05: "share
// shortlisted CVs with the hiring manager" — the feature existed but read as missing).
// Every reason the Share button is disabled used to live in the tooltip of a DISABLED
// button, which never shows; each reason is now said on the page, and after a share
// the recruiter gets the link to send, because no email leaves the app.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import type { ReviewResponse, RoleDetail } from "../types";

const REQ_ID = "financial-analyst";

function review(decided: boolean, shared: boolean): ReviewResponse {
  return {
    ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "run-1",
    location_display: "India", counts: { total: 1 },
    decision_counts: { forward: decided ? 1 : 0, discuss: 0, not: 0, awaiting: decided ? 0 : 1 },
    cards: [{ rank: 1, candidate_id: "c1", name: "Anita Rao", band: "Strong",
              band_label: "Strong match", score: 82,
              decided: decided ? "forward" : null, decided_reason: null,
              decided_ts: decided ? "2026-10-14T09:00:00Z" : null }],
    hm_shared: shared,
    hm_share: shared ? { shared_by: "ta@example.com", ts: "2026-10-14T10:00:00Z", run_id: "run-1" } : null,
    hm_updates: shared ? { pending: false, decisions: [], new_candidates: [] } : null,
  } as ReviewResponse;
}

function setup(role: RoleDetail, data: ReviewResponse, portal = true) {
  mockApi({
    [`/api/roles/${REQ_ID}`]: role,
    [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
    [`/api/roles/${REQ_ID}/review`]: data,
    "/api/features": { features: [{ key: "hm_portal", default: portal, control: "file", enabled: portal }] },
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/shortlist` }
  );
}

const HM = { email: "u123456@example.com", name: "Rahul Mahmood", status: null };

describe("Share with the hiring manager", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockResolvedValue(undefined) }, configurable: true,
    });
  });

  it("says on the page that a hiring manager has to be named first", async () => {
    setup(roleFixture({ hm: null }), review(true, false));
    expect(await screen.findByText(/add a hiring manager first/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "on the Overview tab" }))
      .toHaveAttribute("href", `/reqs/${REQ_ID}/overview`);
  });

  it("says when the portal is switched off, so a share would reach nobody", async () => {
    setup(roleFixture({ hm: HM }), review(true, false), false);
    expect(await screen.findByText(/hiring-manager portal is switched off/)).toBeInTheDocument();
  });

  it("offers the link to send, because nobody is emailed", async () => {
    setup(roleFixture({ hm: HM }), review(true, true));
    await userEvent.click(await screen.findByRole("button", { name: "copy the link to send them" }));
    await waitFor(() => expect(navigator.clipboard.writeText)
      .toHaveBeenCalledWith(`${window.location.origin}/reqs/${REQ_ID}/review`));
    expect(await screen.findByText(/link copied/)).toBeInTheDocument();
  });
});
