// P43 + the design pass: the checklist critic + approval UI. A warn row renders
// the amber strip with its plain reason + "Needs HR Head approval at publish.";
// the publish-card button switches to "Send vN for HR Head approval"; the HR
// Head sees an Approve button; a blocked save surfaces the server 400 inline;
// clean text publishes as "Approve & publish vN+1".
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage, DEFAULT_ME, apiError } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import type { Rubric } from "../types";

const REQ_ID = "financial-analyst";

const WARN_RUBRIC: Rubric = {
  version: 1,
  role_title: "Financial Analyst",
  requirements: [
    { id: "r01", text: "Strong US GAAP experience", type: "other", kind: "must_have", importance: 5, critic_level: null, critic_reason: "" },
    { id: "r02", text: "prefers candidates from tier-1 colleges", type: "other", kind: "nice_to_have", importance: 2, critic_level: "warn", critic_reason: "This looks like school prestige, which isn't job-related." },
  ],
  approval: { version: 1, status: "none", flagged_requirement_ids: ["r02"] },
};

function setup(rubricGet: unknown, extra: Record<string, unknown> = {}, me = DEFAULT_ME) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
    [`/api/roles/${REQ_ID}/rubric`]: rubricGet,
    ...extra,
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/checklist`, me }
  );
  return api;
}

describe("Checklist critic + approval", () => {
  it("renders the warn row's amber strip and the send-for-approval button", async () => {
    setup(WARN_RUBRIC);
    // the flagged row shows the critic's plain reason + the approval sentence
    expect(await screen.findByText(/This looks like school prestige/)).toBeInTheDocument();
    expect(screen.getByText(/Needs HR Head approval at publish/)).toBeInTheDocument();
    // published-with-flags but never sent → the ONE button sends v1 (no version bump)
    expect(screen.getByRole("button", { name: "Send v1 for HR Head approval" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve v1/ })).not.toBeInTheDocument();
  });

  it("requests HR Head approval → POST, no re-publish", async () => {
    const api = setup(WARN_RUBRIC, {
      [`/api/roles/${REQ_ID}/checklist-approval/request`]: { ok: true, approval: { ...WARN_RUBRIC.approval, status: "pending" } },
    });
    await screen.findByRole("button", { name: "Send v1 for HR Head approval" });
    await userEvent.click(screen.getByRole("button", { name: "Send v1 for HR Head approval" }));
    await waitFor(() =>
      expect(api.callsTo(`/api/roles/${REQ_ID}/checklist-approval/request`)).toHaveLength(1)
    );
    // request only — never a PUT (that would bump a no-change version)
    expect(api.callsTo(`/api/roles/${REQ_ID}/rubric`).filter((c) => c.method === "PUT")).toHaveLength(0);
  });

  it("shows Approve for the HR Head when approval is pending", async () => {
    setup({ ...WARN_RUBRIC, approval: { version: 1, status: "pending", flagged_requirement_ids: ["r02"] } });
    expect(await screen.findByRole("button", { name: "Approve v1 (HR Head)" })).toBeInTheDocument();
    expect(screen.getByText(/waiting on HR-Head approval/)).toBeInTheDocument();
  });

  it("surfaces the server 400 inline when a blocked requirement is saved", async () => {
    // clean GET, but PUT rejects a protected-attribute row
    setup(
      (_url: string, init?: RequestInit) =>
        init?.method === "PUT"
          ? apiError(400, "Cannot add this requirement — it references a protected attribute (age). Reword it without the protected attribute.")
          : { version: 1, role_title: "Financial Analyst", requirements: [{ id: "r01", text: "no candidates over 40", kind: "must_have", importance: 4 }] }
    );
    await screen.findByDisplayValue("no candidates over 40");
    // publish needs a real change first (2026-09-06)
    await userEvent.type(screen.getByDisplayValue("no candidates over 40"), " please");
    await userEvent.click(screen.getByRole("button", { name: "Approve & publish v2" }));
    expect(await screen.findByText(/references a protected attribute/)).toBeInTheDocument();
  });

  it("clean text publishes with the versioned button and no approval UI", async () => {
    setup({ version: 2, role_title: "Financial Analyst", requirements: [
      { id: "r01", text: "Strong US GAAP experience", kind: "must_have", importance: 5, critic_level: null },
    ] });
    expect(await screen.findByRole("button", { name: "Approve & publish v3" })).toBeInTheDocument();
    expect(screen.queryByText(/Needs HR Head approval at publish/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /HR Head approval/ })).not.toBeInTheDocument();
  });
});
