// P31: the Overview tab — the single stage CTA (magic only where AI runs) and
// the close-requisition danger zone (outcome picker → POST → navigate).
import { describe, it, expect } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import type { RoleDetail } from "../types";

function setup(role: RoleDetail) {
  const api = mockApi({
    [`/api/roles/${role.req_id}`]: role,
    [`/api/roles/${role.req_id}/jd`]: JD_FIXTURE,
    [`/api/roles/${role.req_id}/close`]: { ok: true, status: "closed" },
    [`/api/roles/${role.req_id}/events`]: { ok: true },
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>all requisitions list</div>} />
    </Routes>,
    { route: `/reqs/${role.req_id}` }
  );
  return api;
}

describe("OverviewTab", () => {
  it("renders the magic CTA when the next step runs AI (draft checklist)", async () => {
    setup(roleFixture({
      stage: "draft_checklist",
      stage_label: "Draft checklist",
      rubric_version: null,
      cta: { label: "Create checklist", goto: "checklist", magic: true },
    }));
    const cta = await screen.findByRole("button", { name: /Create checklist/ });
    expect(cta.className).toContain("btn-magic");
    expect(screen.getByText("Draft the screening checklist")).toBeInTheDocument();
  });

  it("renders a plain primary CTA when the next step is human review", async () => {
    setup(roleFixture()); // stage screened, cta "Start review", magic false
    const cta = await screen.findByRole("button", { name: "Start review" });
    expect(cta.className).toContain("btn-primary");
    expect(cta.className).not.toContain("btn-magic");
  });

  it("shows the full JD panel (expandable) with the design's provenance footnote", async () => {
    setup(roleFixture());
    // a fragment from the JD body (not just the excerpt) renders
    expect(await screen.findByText(/Variance analysis and board decks/)).toBeInTheDocument();
    expect(screen.getByText(/Maintained in iCIMS · auto-fetched via Req ID/)).toBeInTheDocument();
    // expand toggle flips label
    const toggle = screen.getByRole("button", { name: "Show full description" });
    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Show less" })).toBeInTheDocument();
    // 2026-09-06: the facts strip leads with the LOCATION and the law is an opener
    // (hover/click explains what the pack asks) — no sentence about it needed
    // (the workspace header's location chip says it too, so at least one)
    expect(screen.getAllByText("India").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "DPDP Act 2023" }).length).toBeGreaterThanOrEqual(1);
    // the distance card is gone from Overview — the distance is a Review filter now
    expect(screen.queryByText(/Job location & distance/)).toBeNull();
  });

  it("closes with the cancelled outcome and navigates back to the list", async () => {
    const role = roleFixture();
    const api = setup(role);
    // the consequence, in one line (pilot feedback item 8: shorter everywhere)
    expect(await screen.findByText(
      "Open. Closing moves it to History here — iCIMS is unchanged."
    )).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Close requisition" }));
    await userEvent.click(screen.getByLabelText("No hire — cancelled"));
    await userEvent.click(screen.getByRole("button", { name: "Confirm close" }));

    await waitFor(() => expect(api.callsTo(`/api/roles/${role.req_id}/close`)).toHaveLength(1));
    expect(api.callsTo(`/api/roles/${role.req_id}/close`)[0].body).toEqual({ outcome: "cancelled" });
    expect(await screen.findByText("all requisitions list")).toBeInTheDocument();
  });

  it("closes with the hired outcome carrying the hired count", async () => {
    const role = roleFixture();
    const api = setup(role);
    await userEvent.click(await screen.findByRole("button", { name: "Close requisition" }));
    await userEvent.click(screen.getByLabelText("Hired"));
    // one change event — the input clamps to >= 1, so clear-then-type would compose
    fireEvent.change(screen.getByLabelText("Hired count"), { target: { value: "2" } });
    await userEvent.click(screen.getByRole("button", { name: "Confirm close" }));

    await waitFor(() => expect(api.callsTo(`/api/roles/${role.req_id}/close`)).toHaveLength(1));
    expect(api.callsTo(`/api/roles/${role.req_id}/close`)[0].body).toEqual({
      outcome: "hired",
      hired_count: 2,
    });
  });

  it("replaces the close action with the outcome note on a closed req", async () => {
    setup(roleFixture({
      lifecycle: {
        status: "closed",
        outcome: { kind: "hired", hired_count: 1 },
        closed_at: "2026-07-11T09:00:00+00:00",
        closed_by: "ta@example.com",
        snooze_until: null,
        created_by: "ta@example.com",
        created_at: "2026-07-01T08:00:00+00:00",
      },
    }));
    expect(await screen.findByText("Hired 1")).toBeInTheDocument();
    expect(screen.getByText(/Closed —/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close requisition" })).not.toBeInTheDocument();
    const reopen = screen.getByRole("button", { name: "Reopen" });
    expect(reopen).toBeDisabled(); // P37 flag-gates it
  });

  // 2026-09-11: a Budapest requisition made every candidate read "distance unknown"
  // in Review, because the city was missing from the places table. The gap is ours,
  // so it is named beside the city — not forty times on the candidates.
  it("says when the job's city is one we cannot place", async () => {
    setup(roleFixture({
      work_location: { place: "Budapest", radius_km: 25, max_km: 100, place_known: false },
    }));
    expect(await screen.findByText(/Not in the location list/)).toBeInTheDocument();
  });

  it("says nothing when the city is a place we know", async () => {
    setup(roleFixture({
      work_location: { place: "Gurugram", radius_km: 25, max_km: 100, place_known: true },
    }));
    await screen.findAllByText("Gurugram");
    expect(screen.queryByText(/Not in the location list/)).toBeNull();
  });
});
