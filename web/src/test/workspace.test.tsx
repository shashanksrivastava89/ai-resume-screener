// P31: the requisition workspace shell — tabs render and switch, the header
// meta line (monospace req code), and the closed-req read-only banner that
// records a req_history_view event.
import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE, TRAIL } from "./fixtures";
import type { RoleDetail } from "../types";

function setup(role: RoleDetail, extraRoutes: Record<string, unknown> = {}) {
  const api = mockApi({
    [`/api/roles/${role.req_id}`]: role,
    [`/api/roles/${role.req_id}/jd`]: JD_FIXTURE,
    [`/api/roles/${role.req_id}/trail`]: TRAIL,
    [`/api/roles/${role.req_id}/events`]: { ok: true },
    ...extraRoutes,
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

describe("Workspace", () => {
  it("renders the six tabs with the overview open by default", async () => {
    setup(roleFixture());
    expect(await screen.findByRole("tab", { name: /Overview/ })).toBeInTheDocument();
    for (const label of ["Checklist", "Candidates", "Review", "Shortlist", "Audit"]) {
      expect(screen.getByRole("tab", { name: new RegExp(label) })).toBeInTheDocument();
    }
    // overview content (the stage headline for "screened")
    expect(await screen.findByText("Review the shortlist")).toBeInTheDocument();
    // blind review moved into the Review tab's filter row — the header stays
    // clean on the other tabs
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("shows the header facts row: location, law, mono req code, checklist, screened, added by", async () => {
    setup(roleFixture());
    // The req code also appears on the Overview banner — scope to the header's
    // facts row (2026-09-06: coloured chips replaced the grey dotted meta line).
    const code = (await screen.findAllByText("REQ-2041"))
      .find((el) => el.classList.contains("fact-mono"))!;
    expect(code).toBeTruthy();
    const facts = code.closest(".ws-facts") as HTMLElement;
    expect(facts).toHaveTextContent("India");
    expect(facts).toHaveTextContent("Checklist v2 · 6");
    expect(facts).toHaveTextContent("4 screened");
    expect(facts).toHaveTextContent("Added by");
    expect(facts).toHaveTextContent("ta@example.com"); // a Person (no name known yet → the address)
    // 2026-09-07: these say what the requisition IS and must not compete with the
    // stage chip, which says what to do about it — outlined, not tinted.
    expect(facts.querySelector(".fact-blue")).toBeNull();
    // the jurisdiction chip moved off the header: the pack lives on Overview, where
    // its obligations are something you can act on
    expect(within(facts).queryByRole("button", { name: "DPDP Act 2023" })).toBeNull();
    // the scoring-model stamp is not a header fact either (a version hash is not recruiter language)
    expect(facts).not.toHaveTextContent("scoring model");
  });

  it("renders an em-dash req code for a pre-v2 role without one", async () => {
    setup(roleFixture({ req_code: null }));
    // Both the header meta line and the Overview banner render the em-dash in
    // monospace when there's no iCIMS id.
    const dashes = await screen.findAllByText("—", { selector: "span" });
    expect(dashes.some((el) => el.style.fontFamily.includes("monospace"))).toBe(true);
  });

  it("switches tabs and renders the audit trail", async () => {
    setup(roleFixture());
    await userEvent.click(await screen.findByRole("tab", { name: /Audit/ }));
    expect(await screen.findByText("Requisition created")).toBeInTheDocument();
    expect(screen.getByText("Checklist v2 approved")).toBeInTheDocument();
    // verbatim decision quote
    expect(screen.getByText("“Strong GST filing evidence”")).toBeInTheDocument();
    // footer points at the full explorer
    expect(screen.getByText(/Full explorer: Audit & History/)).toBeInTheDocument();
    // the overview headline is gone
    expect(screen.queryByText("Review the shortlist")).not.toBeInTheDocument();
  });

  it("shows the read-only banner on a closed req and records the history view", async () => {
    const closed = roleFixture({
      lifecycle: {
        status: "closed",
        outcome: { kind: "cancelled" },
        closed_at: "2026-07-11T09:00:00+00:00",
        closed_by: "ta@example.com",
        snooze_until: null,
        created_by: "ta@example.com",
        created_at: "2026-07-01T08:00:00+00:00",
      },
      retention: { keep_at_least_days: 1095, purge_by_days: 365, purge_by: "2027-07-11", purged: false },
    });
    const api = setup(closed);
    expect(await screen.findByText(/This requisition is closed/)).toBeInTheDocument();
    await waitFor(() => expect(api.callsTo(`/api/roles/${closed.req_id}/events`)).toHaveLength(1));
    expect(api.callsTo(`/api/roles/${closed.req_id}/events`)[0].body).toEqual({
      action: "req_history_view",
    });
  });

  it("does not record a history view for an active req", async () => {
    const api = setup(roleFixture());
    await screen.findByRole("tab", { name: /Overview/ });
    expect(api.callsTo("/api/roles/financial-analyst/events")).toHaveLength(0);
  });

  // F3: Review + Shortlist stay disabled (with a tooltip) until a run exists.
  it("disables Review and Shortlist until the role is screened", async () => {
    setup(roleFixture({ screened: false, candidate_count: 0, reviewed_count: 0, stage: "candidates_added" }));
    const review = await screen.findByRole("tab", { name: /Review/ });
    expect(review).toHaveAttribute("aria-disabled", "true");
    expect(review).toHaveAttribute("title", "Screen candidates first — the ranked shortlist appears here.");
    const shortlist = screen.getByRole("tab", { name: /Shortlist/ });
    expect(shortlist).toHaveAttribute("aria-disabled", "true");

    // clicking a disabled tab is a no-op — the overview headline stays
    await userEvent.click(review);
    expect(screen.getByRole("tab", { name: /Overview/ })).toHaveAttribute("aria-selected", "true");
  });
});
