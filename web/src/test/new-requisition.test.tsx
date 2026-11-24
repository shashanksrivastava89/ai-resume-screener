// P32: the New-requisition form — the iCIMS Req ID is required (exact inline
// error), and the location picker declares its consequences up front from
// /api/locations.
import { describe, it, expect, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import * as hooks from "../hooks";
import NewRequisition from "../pages/NewRequisition";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage, apiError } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import { LOCATIONS_FEED } from "./fixtures";

const LOCATIONS = LOCATIONS_FEED;

function setup(extraRoutes: Record<string, unknown> = {}) {
  const api = mockApi({ "/api/locations": LOCATIONS, ...extraRoutes });
  renderPage(
    <Routes>
      <Route path="/reqs/new" element={<NewRequisition />} />
    </Routes>,
    { route: "/reqs/new" }
  );
  return api;
}

async function fillBasics() {
  await userEvent.type(screen.getByLabelText("Job title"), "Treasury Analyst");
  await userEvent.type(screen.getByLabelText("Job description"), "Owns cash and banking.");
  // WHERE the job is, which the form now requires (2026-09-12): nothing is
  // preselected, so a requisition can no longer be created by not answering.
  await userEvent.selectOptions(screen.getByLabelText("Country"), "IN");
}

describe("NewRequisition", () => {
  it("requires the iCIMS Req ID with the exact inline error", async () => {
    const api = setup();
    await screen.findByLabelText(/Country/); // locations loaded
    await fillBasics();
    await userEvent.click(screen.getByRole("button", { name: /Create draft checklist/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "iCIMS Req ID is required — no mapping to the requisition is possible without it."
    );
    expect(api.callsTo("/api/roles")).toHaveLength(0);
  });

  it("invalidates the cached roles list on create so the dashboard shows the new req without a refresh", async () => {
    const spy = vi.spyOn(hooks, "invalidate");
    setup({
      "/api/roles": { req_id: "treasury-analyst", title: "Treasury Analyst" },
      "/api/roles/treasury-analyst/rubric/draft": {
        version: null,
        requirements: [{ text: "5+ years treasury accounting", kind: "must_have", importance: 4 }],
      },
    });
    await screen.findByLabelText(/Country/);
    await userEvent.type(screen.getByLabelText("iCIMS Req ID"), "2026-1042");
    await fillBasics();
    await userEvent.click(screen.getByRole("button", { name: /Create draft checklist/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("/api/roles"));
    spy.mockRestore();
  });

  it("disables browser autofill so picking one field can't overwrite the others", async () => {
    setup();
    await screen.findByLabelText(/Country/); // form rendered
    // Every field opts out of native autofill (Edge/Chrome grouping was filling
    // the whole form from one pick). type=email is the strongest trigger.
    for (const label of ["iCIMS Req ID", "Job title", "Job description"]) {
      expect(screen.getByLabelText(label)).toHaveAttribute("autocomplete", "off");
    }
  });

  it("says nothing about hiring rules until it knows where the job is, then declares them", async () => {
    setup();
    await screen.findByLabelText(/Country/);
    // NOTHING is preselected (2026-09-12): the form has no opinion about where a
    // job is, and the old India-first default is how a recruiter in Budapest
    // created an India requisition by not touching the dropdown.
    expect(screen.getByLabelText("Country")).toHaveValue("");
    expect(screen.queryByText(/pack applies/)).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Country"), "IN");
    expect(await screen.findByText(
      "DPDP Act 2023 pack applies · audit records kept at least 1095 days · candidate PII purged 180 days after close"
    )).toBeInTheDocument();

    // Hungary is governed by the EU pack — a recruiter never has to know that
    await userEvent.selectOptions(screen.getByLabelText("Country"), "HU");
    expect(screen.getByText(/audit records kept at least 365 days · candidate PII purged 180 days after close/)).toBeInTheDocument();
    expect(screen.queryByText(/DPDP/)).not.toBeInTheDocument();
  });

  it("asks for the state in the United States, and for the city only in New York", async () => {
    setup();
    await screen.findByLabelText(/Country/);
    expect(screen.queryByLabelText("State")).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Country"), "US");
    await userEvent.selectOptions(await screen.findByLabelText("State"), "CA");
    expect(screen.getByText(/FEHA/)).toBeInTheDocument();
    // California is not New York: no city question
    expect(screen.queryByLabelText(/New York City/)).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("State"), "NY");
    await userEvent.click(await screen.findByLabelText(/This job is in New York City/));
    expect(screen.getByText(/Local Law 144/)).toBeInTheDocument();
  });

  it("refuses a country we have no hiring rules for, by name", async () => {
    setup();
    await screen.findByLabelText(/Country/);
    await userEvent.selectOptions(screen.getByLabelText("Country"), "CA");
    expect(screen.getByText(/no hiring rules for Canada yet/)).toBeInTheDocument();
    expect(screen.getByText(/ask HR and Legal/)).toBeInTheDocument();
  });

  it("creates the requisition with the iCIMS id and location, then drafts", async () => {
    const api = setup({
      "/api/roles": { req_id: "treasury-analyst", title: "Treasury Analyst" },
      "/api/roles/treasury-analyst/rubric/draft": {
        version: null,
        requirements: [{ text: "5+ years treasury accounting", kind: "must_have", importance: 4 }],
      },
    });
    await screen.findByLabelText(/Country/);
    await userEvent.type(screen.getByLabelText("iCIMS Req ID"), "2026-1042");
    await fillBasics();
    await userEvent.click(screen.getByRole("button", { name: /Create draft checklist/ }));

    await waitFor(() => expect(api.callsTo("/api/roles")).toHaveLength(1));
    expect(api.callsTo("/api/roles")[0].body).toEqual({
      title: "Treasury Analyst",
      jd_text: "Owns cash and banking.",
      job_location: "IN",
      icims_req_id: "2026-1042",
      // the place, alongside the pack it resolves to
      country: "IN", us_state: null, nyc: false,
    });
    // lands on the draft-checklist review step
    expect(await screen.findByRole("button", { name: /Approve checklist & add résumés/ })).toBeInTheDocument();
    expect(screen.getByDisplayValue("5+ years treasury accounting")).toBeInTheDocument();
  });

  // --- F5: back/forward without re-POST, PUT on edit, re-draft offer, 409 link ---

  async function createTo(api = setup({
    "/api/roles": { req_id: "treasury-analyst", title: "Treasury Analyst" },
    "/api/roles/treasury-analyst/rubric/draft": {
      version: null, requirements: [{ text: "5+ years treasury accounting", kind: "must_have", importance: 4 }],
    },
    "/api/roles/treasury-analyst/details": roleFixture({ req_id: "treasury-analyst", title: "Treasury Analyst" }),
    "/api/roles/treasury-analyst/rubric/draft2": {}, // placeholder, unused
  })) {
    await screen.findByLabelText(/Country/);
    await userEvent.type(screen.getByLabelText("iCIMS Req ID"), "2026-1042");
    await fillBasics();
    await userEvent.click(screen.getByRole("button", { name: /Create draft checklist/ }));
    await screen.findByRole("button", { name: /Approve checklist & add résumés/ });
    return api;
  }

  it("going Back then forward with no edits does NOT re-POST /api/roles", async () => {
    const api = await createTo();
    await userEvent.click(screen.getByRole("button", { name: "Back to details" }));
    await userEvent.click(await screen.findByRole("button", { name: "Save & continue to checklist" }));
    await screen.findByRole("button", { name: /Approve checklist & add résumés/ });
    expect(api.callsTo("/api/roles")).toHaveLength(1); // still just the one create
    expect(api.callsTo("/api/roles/treasury-analyst/details")).toHaveLength(0);
    // the drafted row survived the round trip
    expect(screen.getByDisplayValue("5+ years treasury accounting")).toBeInTheDocument();
  });

  it("editing the title on Back sends a details PUT, no re-draft offer", async () => {
    const api = await createTo();
    await userEvent.click(screen.getByRole("button", { name: "Back to details" }));
    const title = await screen.findByLabelText("Job title");
    await userEvent.clear(title);
    await userEvent.type(title, "Senior Treasury Analyst");
    await userEvent.click(screen.getByRole("button", { name: "Save & continue to checklist" }));
    await waitFor(() => expect(api.callsTo("/api/roles/treasury-analyst/details")).toHaveLength(1));
    expect(api.callsTo("/api/roles/treasury-analyst/details")[0].body).toMatchObject({ title: "Senior Treasury Analyst" });
    // JD unchanged → no re-draft offer
    expect(screen.queryByText("The job description changed")).not.toBeInTheDocument();
  });

  it("editing the JD offers a re-draft, and Keep my rows dismisses it", async () => {
    const api = await createTo();
    await userEvent.click(screen.getByRole("button", { name: "Back to details" }));
    const jd = await screen.findByLabelText("Job description");
    await userEvent.type(jd, " Additional treasury duties.");
    await userEvent.click(screen.getByRole("button", { name: "Save & continue to checklist" }));
    await waitFor(() => expect(api.callsTo("/api/roles/treasury-analyst/details")).toHaveLength(1));
    expect(await screen.findByText("The job description changed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Keep my rows" }));
    expect(screen.queryByText("The job description changed")).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("5+ years treasury accounting")).toBeInTheDocument();
  });

  it("shows a structured 409 inline with a deep link to the existing req", async () => {
    const api = setup({
      "/api/roles": apiError(409, {
        message: "iCIMS Req ID '2026-1042' is already used by 'Treasury Analyst'.",
        field: "icims_req_id", req_id: "treasury-analyst", title: "Treasury Analyst",
      }),
    });
    await screen.findByLabelText(/Country/);
    await userEvent.type(screen.getByLabelText("iCIMS Req ID"), "2026-1042");
    await fillBasics();
    await userEvent.click(screen.getByRole("button", { name: /Create draft checklist/ }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("already used by 'Treasury Analyst'");
    expect(within(alert).getByRole("link", { name: /Open Treasury Analyst/ })).toHaveAttribute(
      "href", "/reqs/treasury-analyst");
    // it did NOT surface in the generic top callout
    expect(api.callsTo("/api/roles")).toHaveLength(1);
  });

  it("a flagged checklist routes Approve to the Checklist tab, not Candidates", async () => {
    const api = mockApi({
      "/api/locations": LOCATIONS,
      "/api/roles": { req_id: "treasury-analyst", title: "Treasury Analyst" },
      "/api/roles/treasury-analyst/rubric/draft": {
        version: null, requirements: [{ text: "5+ years treasury accounting", kind: "must_have", importance: 4 }],
      },
      // the PUT reports the just-published version needs approval
      "/api/roles/treasury-analyst/rubric": (_u: string, init?: RequestInit) =>
        init?.method === "PUT"
          ? { version: 1, requirements: [], needs_approval: true }
          : { version: 1, role_title: "Treasury Analyst", requirements: [], approval: { version: 1, status: "none", flagged_requirement_ids: ["r01"] } },
      "/api/roles/treasury-analyst": roleFixture({ req_id: "treasury-analyst", title: "Treasury Analyst" }),
      "/api/roles/treasury-analyst/jd": JD_FIXTURE,
    });
    renderPage(
      <Routes>
        <Route path="/reqs/new" element={<NewRequisition />} />
        {workspaceRoutes()}
      </Routes>,
      { route: "/reqs/new" }
    );
    await screen.findByLabelText(/Country/);
    await userEvent.type(screen.getByLabelText("iCIMS Req ID"), "2026-1042");
    await fillBasics();
    await userEvent.click(screen.getByRole("button", { name: /Create draft checklist/ }));
    await screen.findByRole("button", { name: /Approve checklist & add résumés/ });
    await userEvent.click(screen.getByRole("button", { name: /Approve checklist/ }));
    // lands on the Checklist tab (its approval machinery), NOT Candidates
    await waitFor(() => expect(api.callsTo("/api/roles/treasury-analyst/rubric").filter((c) => c.method === "PUT")).toHaveLength(1));
    expect(await screen.findByRole("button", { name: "Send v1 for HR Head approval" })).toBeInTheDocument();
  });
});
