// Sharing one requisition with a colleague (2026-09-10). Own-req scoping is the
// right default and the wrong absolute — cover and handovers are ordinary
// recruiting. The controls belong to the OWNER; everyone else sees who it is
// shared with, or nothing at all.
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage, DEFAULT_ME } from "./utils";
import { roleFixture, LOCATIONS_FEED } from "./fixtures";

const REQ_ID = "financial-analyst";
const OWNER = "bhavika@example.com";
const MATE = { email: "arjun2@example.com", name: "Arjun Two", added_by: OWNER, added_at: "2026-09-10T10:00:00" };

function setup(opts: { me: string; collaborators?: typeof MATE[]; createdBy?: string; caps?: string[] }) {
  const role = roleFixture();
  const api = mockApi({
    "/api/me": { ...DEFAULT_ME, user: opts.me,
                 ...(opts.caps ? { capabilities: opts.caps } : {}) },
    [`/api/roles/${REQ_ID}`]: {
      ...role,
      created_by: opts.createdBy ?? OWNER,
      lifecycle: { ...role.lifecycle, created_by: opts.createdBy ?? OWNER,
                   collaborators: opts.collaborators ?? [] },
    },
    [`/api/roles/${REQ_ID}/jd`]: { title: "Financial Analyst", jd_text: "Do the numbers." },
    "/api/locations": LOCATIONS_FEED,
    "/api/people": { people: [] },
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/overview` }
  );
  return api;
}

describe("sharing a requisition", () => {
  it("offers the owner a way to share it", async () => {
    setup({ me: OWNER });
    expect(await screen.findByRole("button", { name: /Share with someone/i })).toBeInTheDocument();
  });

  it("does not offer it to someone who does not own it", async () => {
    setup({ me: "someone.else@example.com" });
    await screen.findByText(/Do the numbers/);
    expect(screen.queryByRole("button", { name: /Share with someone/i })).toBeNull();
  });

  it("shows a collaborator to everyone, and the remove control only to the owner", async () => {
    setup({ me: "someone.else@example.com", collaborators: [MATE] });
    expect(await screen.findByText("Arjun Two")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove Arjun Two/i })).toBeNull();
  });

  it("lets the owner take someone off", async () => {
    const api = setup({ me: OWNER, collaborators: [MATE] });
    const remove = await screen.findByRole("button", { name: /Remove Arjun Two/i });
    await userEvent.click(remove);
    await waitFor(() => {
      expect(api.calls.some((c) => c.method === "DELETE" && c.path.includes("/collaborators/"))).toBe(true);
    });
  });

  it("offers it to someone who already sees every requisition", async () => {
    // The server lets an HR Head share any requisition; showing the control only to
    // the owner would hide a power they actually have.
    setup({ me: "hrhead@example.com", caps: [...DEFAULT_ME.capabilities, "reqs_all"] });
    expect(await screen.findByRole("button", { name: /Share with someone/i })).toBeInTheDocument();
  });

  it("says plainly that sharing is not a role", async () => {
    setup({ me: OWNER, collaborators: [MATE] });
    expect(await screen.findByText(/What they may DO still comes from/i)).toBeInTheDocument();
  });
});
