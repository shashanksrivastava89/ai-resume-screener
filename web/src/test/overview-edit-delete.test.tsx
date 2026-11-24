// Overview tab: the prominent iCIMS Req ID, the create-like JD/details editor
// (req id locked, checklist-reset warning) and the delete-requisition danger
// card with loss counts.
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE, LOCATIONS_FEED } from "./fixtures";
import type { RoleDetail } from "../types";

const LOCATIONS = LOCATIONS_FEED;

function setup(role: RoleDetail, extraRoutes: Record<string, unknown> = {}) {
  const api = mockApi({
    [`/api/roles/${role.req_id}`]: role,
    [`/api/roles/${role.req_id}/jd`]: JD_FIXTURE,
    "/api/locations": LOCATIONS,
    "/api/features": { features: [] },
    ...extraRoutes,
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>all requisitions list</div>} />
    </Routes>,
    { route: `/reqs/${role.req_id}/overview` }
  );
  return api;
}

describe("Overview edit + delete", () => {
  it("shows the iCIMS Req ID prominently on the overview", async () => {
    setup(roleFixture());
    // Both the header meta line and the Overview banner carry it.
    const hits = await screen.findAllByText("REQ-2041");
    expect(hits.length).toBeGreaterThanOrEqual(2);
  });

  it("copies the Req ID and shows brief confirmation", async () => {
    setup(roleFixture());
    const copy = await screen.findByRole("button", { name: /Copy iCIMS Req ID/ });
    expect(copy).toHaveAttribute("title", "Copy iCIMS Req ID");
    await userEvent.click(copy);
    // jsdom has no clipboard, so the button reports the honest fallback rather than
    // a silent success (a copy button that visibly does nothing is the one failure
    // mode it must never have); either title proves the state changed on click
    await waitFor(() => expect(["Copied", "Couldn't copy — select the text instead"]).toContain(copy.getAttribute("title")));
  });

  it("shows a name-only hiring manager regardless of the portal flag", async () => {
    // portal flag OFF (default features mock is empty) + HM with a name, no email
    setup(roleFixture({ hm: { email: "", name: "Bhavika Lillis", status: null } }));
    expect(await screen.findByText("Hiring manager")).toBeInTheDocument();
    expect(screen.getByText("Bhavika Lillis")).toBeInTheDocument();
  });

  it("edits the hiring manager via the details form (records on file)", async () => {
    const api = setup(roleFixture({ hm: null }));
    api.route("/api/roles/financial-analyst/details", { ok: true });
    await screen.findByText(/Prepare monthly closes/);
    await userEvent.click(await screen.findByRole("button", { name: /Edit details/ }));

    // 2026-09-06: the HM field is a directory picker; a person the directory does
    // not know yet is entered through its manual path (name + email, both required)
    await userEvent.click(await screen.findByRole("button", { name: /Enter a name and email manually/ }));
    await userEvent.type(screen.getByLabelText("Name"), "Bhavika Lillis");
    await userEvent.type(screen.getByLabelText("Email"), "bhavika.lillis@example.com");
    await userEvent.click(screen.getByRole("button", { name: /Use this person/ }));
    expect(screen.getByTestId("picked-person")).toHaveTextContent("Bhavika Lillis");
    await userEvent.click(screen.getByRole("button", { name: /Save details/ }));

    await waitFor(() => {
      const put = api.callsTo("/api/roles/financial-analyst/details").find((c) => c.method === "PUT");
      expect(put).toBeTruthy();
      expect((put!.body as { hm_name?: string; hm_email?: string }).hm_name).toBe("Bhavika Lillis");
      expect((put!.body as { hm_name?: string; hm_email?: string }).hm_email).toBe("bhavika.lillis@example.com");
    });
  });

  it("deletes the requisition after confirming and shows the loss counts", async () => {
    const api = setup(roleFixture(), {
      [`/api/roles/financial-analyst`]: roleFixture(),
    });
    api.route("/api/roles/financial-analyst", (_url: string, init?: RequestInit) =>
      (init?.method ?? "GET") === "DELETE"
        ? { ok: true, deleted: "financial-analyst" }
        : roleFixture()
    );

    await userEvent.click(await screen.findByRole("button", { name: /Delete requisition…/ }));
    // the counts from the fixture (6 requirements, 4 résumés, 1 decision)
    expect(await screen.findByText(/6 checklist requirements/)).toBeInTheDocument();
    expect(screen.getByText(/4 candidate résumés/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Permanently delete/ }));

    await waitFor(() => {
      const del = api.calls.find((c) => c.method === "DELETE" && c.path === "/api/roles/financial-analyst");
      expect(del).toBeTruthy();
      expect(del!.body).toEqual({ confirm: true });
    });
    // lands back on the requisitions list
    expect(await screen.findByText("all requisitions list")).toBeInTheDocument();
  });

  it("warns that editing the JD resets the checklist, then PUTs details", async () => {
    const api = setup(roleFixture());
    api.route("/api/roles/financial-analyst/details", { ok: true });

    // wait for the JD to load into the read-only preview before editing
    await screen.findByText(/Prepare monthly closes/);
    await userEvent.click(await screen.findByRole("button", { name: /Edit details/ }));
    // the iCIMS field is present but read-only
    const reqField = await screen.findByDisplayValue("REQ-2041");
    expect(reqField).toHaveAttribute("readonly");

    // change the JD body (the only <textarea> in the editor)
    const jd = screen.getAllByRole("textbox").find((el) => el.tagName === "TEXTAREA")!;
    await userEvent.clear(jd);
    await userEvent.type(jd, "A brand-new job description for the role.");

    // first Save surfaces the reset warning (a checklist exists)
    await userEvent.click(screen.getByRole("button", { name: /Save details/ }));
    expect(await screen.findByText(/resets the screening checklist/i)).toBeInTheDocument();

    // confirming saves and re-drafts
    await userEvent.click(screen.getByRole("button", { name: /Save & re-draft checklist/ }));
    await waitFor(() => {
      expect(api.callsTo("/api/roles/financial-analyst/details").some((c) => c.method === "PUT")).toBe(true);
    });
  });
});
