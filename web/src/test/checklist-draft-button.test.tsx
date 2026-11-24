// F4: an existing req with no checklist gets a "Draft checklist from the job
// description" button on the Checklist tab — it calls the same AI draft the
// wizard uses, fills the editor, and does NOT auto-publish.
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";

const REQ_ID = "financial-analyst";
const RUBRIC_PATH = `/api/roles/${REQ_ID}/rubric`;
const DRAFT_PATH = `/api/roles/${REQ_ID}/rubric/draft`;

function setup() {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture({ rubric_version: null, requirement_count: 0, stage: "draft_checklist" }),
    [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
    [RUBRIC_PATH]: { version: null, requirements: [] }, // empty → the tab seeds one blank row
    [DRAFT_PATH]: {
      version: null,
      requirements: [
        { id: "r01", text: "Owns the monthly close", type: "experience", kind: "must_have", importance: 4 },
        { id: "r02", text: "GST/TDS filing depth", type: "skill", kind: "nice_to_have", importance: 3 },
      ],
    },
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/checklist` }
  );
  return api;
}

describe("Draft checklist from the JD", () => {
  it("fills the editor from the AI draft without publishing", async () => {
    const api = setup();
    const btn = await screen.findByRole("button", { name: /Draft checklist from the job description/ });
    await userEvent.click(btn);

    // the drafted rows land in the editor
    expect(await screen.findByDisplayValue("Owns the monthly close")).toBeInTheDocument();
    expect(screen.getByDisplayValue("GST/TDS filing depth")).toBeInTheDocument();

    // exactly one draft POST, and NO publish (PUT) happened
    await waitFor(() => expect(api.callsTo(DRAFT_PATH)).toHaveLength(1));
    expect(api.callsTo(RUBRIC_PATH).filter((c) => c.method === "PUT")).toHaveLength(0);

    // the button is gone once real rows exist
    expect(screen.queryByRole("button", { name: /Draft checklist from the job description/ })).not.toBeInTheDocument();
  });
});
