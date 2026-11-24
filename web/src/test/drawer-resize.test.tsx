// F7: the Review drawer can be dragged wider. clampDrawerWidth is the pure
// resize math; the keyboard path is the reliable jsdom assertion; and the width
// persists across tabs within a req but resets when the req changes (it lives on
// the workspace context).
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { clampDrawerWidth, DRAWER_MIN, DRAWER_DEFAULT } from "../pages/ReviewDrawer";
import { mockApi, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import type { CandidateDetail, ReviewCard, ReviewResponse } from "../types";

describe("clampDrawerWidth", () => {
  it("never goes below the minimum", () => {
    expect(clampDrawerWidth(400, 2000)).toBe(DRAWER_MIN);
  });
  it("caps at 88% of the viewport", () => {
    expect(clampDrawerWidth(5000, 1000)).toBe(880);
  });
  it("passes a value through when it's in range", () => {
    expect(clampDrawerWidth(700, 2000)).toBe(700);
  });
  it("handles a viewport narrower than the minimum", () => {
    expect(clampDrawerWidth(600, 500)).toBe(440); // 88% of 500
  });
});

const REQ_ID = "financial-analyst";

function card(overrides: Partial<ReviewCard>): ReviewCard {
  return {
    rank: 1, candidate_id: "c1", name: "Anita Rao", band: "Strong", band_label: "Strong match",
    score: 82, confidence: "high", flag: false, chips: [], gates: { total: 0, failed: [] },
    must_have_gaps: [], decided: null, decided_reason: null, decided_ts: null, ...overrides,
  };
}

function review(): ReviewResponse {
  return {
    ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "run-1", location_display: "India",
    meta: { screened: 1, rubric: "v1" },
    counts: { strong: 1, possible: 0, not_a_fit: 0, needs_look: 0, gates_passed: 1, gate_not_met: 0, total: 1 },
    decision_counts: { forward: 0, discuss: 0, not: 0, awaiting: 1 },
    cards: [card({})],
  };
}

const DETAIL: CandidateDetail = {
  candidate_id: "c1", name: "Anita Rao", band: "Strong", band_label: "Strong match", score: 82,
  confidence: "high", sub: "Covers 82%", verdict: "Strong profile.", flag: false, flag_note: "",
  gate_warn: "", strengths: [], watchouts: [], req_summary: "1 of 1 met", reqs: [], notes: [],
  decision: null, resume_media_type: null,
};

function panelWidth(): string {
  return (screen.getByRole("separator").parentElement as HTMLElement).style.width;
}

async function openDrawer() {
  await userEvent.click(await screen.findByText("Anita Rao"));
  await screen.findByRole("separator");
}

describe("Review drawer resize", () => {
  function setup(route = `/reqs/${REQ_ID}/review`, extra: Record<string, unknown> = {}) {
    mockApi({
      [`/api/roles/${REQ_ID}`]: roleFixture(),
      [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
      [`/api/roles/${REQ_ID}/review`]: review(),
      [`/api/roles/${REQ_ID}/review/c1`]: DETAIL,
      [`/api/roles/other-role`]: roleFixture({ req_id: "other-role", title: "Other Role" }),
      [`/api/roles/other-role/jd`]: JD_FIXTURE,
      [`/api/roles/other-role/review`]: { ...review(), req_id: "other-role" },
      [`/api/roles/other-role/review/c1`]: DETAIL,
      ...extra,
    });
    renderPage(
      <Routes>
        {workspaceRoutes()}
        <Route path="/reqs" element={<div>list</div>} />
      </Routes>,
      { route }
    );
  }

  it("widens via the keyboard and clamps at the minimum", async () => {
    setup();
    await openDrawer();
    const handle = screen.getByRole("separator");
    handle.focus();
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}"); // +48
    expect(panelWidth()).toBe(`${DRAWER_MIN + 48}px`);
    // shrinking past the min clamps back to the min
    await userEvent.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(panelWidth()).toBe(`${DRAWER_MIN}px`);
  });

  it("keeps the width across a tab switch within the same req", async () => {
    setup();
    await openDrawer();
    screen.getByRole("separator").focus();
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}"); // 608
    // close the drawer, go to Candidates, come back, reopen
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("tab", { name: /Candidates/ }));
    await userEvent.click(screen.getByRole("tab", { name: /Review/ }));
    await openDrawer();
    expect(panelWidth()).toBe(`${DRAWER_MIN + 48}px`); // width lived on the workspace
  });

  it("resets to the default when a different req opens", async () => {
    mockApi({
      [`/api/roles/${REQ_ID}`]: roleFixture(),
      [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
      [`/api/roles/${REQ_ID}/review`]: review(),
      [`/api/roles/${REQ_ID}/review/c1`]: DETAIL,
      [`/api/roles/other-role`]: roleFixture({ req_id: "other-role", title: "Other Role" }),
      [`/api/roles/other-role/jd`]: JD_FIXTURE,
      [`/api/roles/other-role/review`]: { ...review(), req_id: "other-role" },
      [`/api/roles/other-role/review/c1`]: DETAIL,
    });
    renderPage(
      <>
        {/* an always-mounted link so the Workspace instance survives the req
            change — that's what exercises the reset-on-reqId effect. */}
        <Link to="/reqs/other-role/review">switch-req</Link>
        <Routes>{workspaceRoutes()}</Routes>
      </>,
      { route: `/reqs/${REQ_ID}/review` }
    );
    await openDrawer();
    screen.getByRole("separator").focus();
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(panelWidth()).toBe(`${DRAWER_MIN + 48}px`);

    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("link", { name: "switch-req" }));
    await waitFor(() => expect(screen.getByText("Other Role")).toBeInTheDocument());
    await openDrawer();
    // Follows the constant, never a literal: a width change is a design decision, not
    // a reason for the suite to go red (the tests/packdata.py lesson, in TS).
    expect(panelWidth()).toBe(`min(${DRAWER_DEFAULT}px, 88%)`);
  });
});
