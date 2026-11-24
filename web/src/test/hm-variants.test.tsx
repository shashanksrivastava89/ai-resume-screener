// P42: the dark-launched hiring-manager portal, driven entirely by capabilities.
//  - HM (hm_take, no recruiter): the requisitions list hides create/metrics and
//    the region pills; the Review tab shows take buttons, not the decision triad,
//    and a "Send takes" footer.
//  - HM with the portal flag off (hm_take suppressed): the plain notice renders.
//  - TA (recruiter): sees the "HM · <take>" chip when the review payload carries
//    one, and still has the decision triad.
import { describe, it, expect } from "vitest";
import { screen, within, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import Requisitions from "../pages/Requisitions";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage, DEFAULT_ME } from "./utils";
import { roleFixture } from "./fixtures";
import type { Me, ReviewCard, ReviewResponse, RoleDetail } from "../types";

const REQ_ID = "financial-analyst";
const HM_EMAIL = "gerard@example.com";

const HM_ME: Me = {
  user: HM_EMAIL,
  role: "HiringManager",
  roles: ["HiringManager"],
  known_role: true,
  capabilities: ["hm_take", "prefs_own"],
  has_admin: false,
  can_edit_rules: false,
  evaluation_mode: true,
  review_pending: false,
  last_review: null,
};

// Same manager, portal flag OFF: the server suppresses hm_take.
const HM_ME_DARK: Me = { ...HM_ME, capabilities: ["prefs_own"] };

function rolesResponse(roles: RoleDetail[]) {
  const open = roles.filter((r) => r.lifecycle.status === "active");
  return {
    metrics: {
      active_roles: open.length, candidates_screened: 0, awaiting_review: 0,
      total: roles.length, need_action: 0, awaiting_hm: open.filter((r) => r.hm).length,
      suggested_close: 0, history_count: roles.length - open.length,
    },
    roles,
  };
}

function reviewCard(overrides: Partial<ReviewCard> = {}): ReviewCard {
  return {
    rank: 1, candidate_id: "c1", name: "Anita Rao", band: "Strong",
    band_label: "Strong match", score: 82, confidence: "high", flag: false,
    chips: [], gates: { total: 0, failed: [] }, must_have_gaps: [],
    decided: null, decided_reason: null, decided_ts: null, ...overrides,
  };
}

function review(cards: ReviewCard[]): ReviewResponse {
  return {
    ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "run-1",
    location_display: "India", meta: { screened: cards.length, rubric: "v1" },
    counts: { strong: cards.length, possible: 0, not_a_fit: 0, needs_look: 0, gates_passed: cards.length, gate_not_met: 0, total: cards.length },
    decision_counts: { forward: 0, discuss: 0, not: 0, awaiting: cards.length },
    cards,
    // snapshot semantics (2026-07-19): the HM variant only lists candidates
    // after a share, so these fixtures model a shared run.
    hm_shared: true,
    hm_share: { shared_by: "ta@example.com", ts: "2026-07-19T09:00:00+00:00", run_id: "run-1" },
  };
}

describe("HM requisitions list variant", () => {
  it("hides create + metrics and renames the column when the caller is a hiring manager", async () => {
    mockApi({ "/api/roles": rolesResponse([roleFixture({ created_by: "ta@example.com", hm: { email: HM_EMAIL, name: "Rahul", status: "review_requested" } })]) });
    renderPage(<Routes><Route path="/reqs" element={<Requisitions />} /></Routes>, { route: "/reqs", me: HM_ME });

    await screen.findByText("Financial Analyst");
    // no create affordance, no metric cards
    expect(screen.queryByRole("button", { name: /New requisition/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Awaiting HM input")).not.toBeInTheDocument();
    // region pills are gone; My reqs / All / History remain
    expect(screen.queryByRole("button", { name: "APAC" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /My reqs/ })).toBeInTheDocument();
    // the column is "Recruiter" (the TA); the cell shows the PERSON (2026-09-06):
    // the directory name when the app knows them, else the address itself — an
    // address like "ta@" is not name-shaped, so it is never mangled into "Ta"
    expect(screen.getByRole("columnheader", { name: "Recruiter" })).toBeInTheDocument();
    const recruiter = screen.getByText("ta@example.com");
    expect(recruiter.closest(".person")).not.toBeNull();
    expect(screen.getByText("requested your review")).toBeInTheDocument();
  });

  it("shows the plain notice when the portal flag is off (hm_take suppressed)", async () => {
    mockApi({});
    renderPage(<Routes><Route path="/reqs" element={<Requisitions />} /></Routes>, { route: "/reqs", me: HM_ME_DARK });
    expect(await screen.findByText("The hiring-manager portal isn't enabled yet")).toBeInTheDocument();
    // never the broken list shell
    expect(screen.queryByText("Awaiting HM input")).not.toBeInTheDocument();
  });
});

function setupReview(me: Me, cards: ReviewCard[]) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/review`]: review(cards),
    [`/api/roles/${REQ_ID}/takes`]: { ok: true, recorded: 1 },
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/review`, me }
  );
  return api;
}

describe("HM review tab variant", () => {
  it("shows take buttons and the send footer, never the decision triad", async () => {
    // decided = "forward" → the card sits in the shared working set
    setupReview(HM_ME, [reviewCard({ decided: "forward" })]);
    const row = (await screen.findByText("Anita Rao")).closest(".card-hover")! as HTMLElement;
    expect(within(row).getByRole("button", { name: "Strong yes" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Move forward" })).not.toBeInTheDocument();
    // the footer sends takes to the TA rather than opening a shortlist
    expect(screen.getByRole("button", { name: /Send takes to/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Shortlist →" })).not.toBeInTheDocument();
  });

  it("records a take via POST /takes when a take button is clicked", async () => {
    const api = setupReview(HM_ME, [reviewCard({ decided: "forward" })]);
    const row = (await screen.findByText("Anita Rao")).closest(".card-hover")! as HTMLElement;
    within(row).getByRole("button", { name: "Strong yes" }).click();
    await waitFor(() => expect(api.callsTo(`/api/roles/${REQ_ID}/takes`)).toHaveLength(1));
    expect(api.callsTo(`/api/roles/${REQ_ID}/takes`)[0].body).toEqual({
      takes: [{ candidate_id: "c1", take: "strong_yes" }],
    });
  });

  it("minimizes the not-yet-decided group until expanded", async () => {
    setupReview(HM_ME, [reviewCard({ decided: "forward" }),
                        reviewCard({ candidate_id: "c2", name: "Vikram Shah", decided: null })]);
    await screen.findByText("Anita Rao");
    // the undecided candidate hides behind the collapsed group…
    expect(screen.queryByText("Vikram Shah")).not.toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: /Not yet decided — 1 still with the recruiter/ });
    toggle.click();
    expect(await screen.findByText("Vikram Shah")).toBeInTheDocument();
  });
});

describe("TA sees the HM take", () => {
  it("renders the HM chip beside the band and keeps the decision triad", async () => {
    setupReview(DEFAULT_ME, [reviewCard({ hm_take: { take: "strong_yes", hm_name: "Rahul" } })]);
    const row = (await screen.findByText("Anita Rao")).closest(".card-hover")! as HTMLElement;
    expect(within(row).getByText("HM · Strong yes")).toBeInTheDocument();
    // recruiter still decides
    expect(within(row).getByRole("button", { name: "Move forward" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Strong yes" })).not.toBeInTheDocument();
  });
});
