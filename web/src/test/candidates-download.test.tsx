// Candidates tab résumé downloads: a per-row download link and a whole-batch
// "Download all (ZIP)" link, both pointing at the batch-download endpoints.
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";

const REQ_ID = "financial-analyst";

const BATCH = {
  count: 2,
  files: [{ name: "Anita Rao.pdf" }, { name: "Vikram Shah.pdf" }],
  folder: "data/roles/financial-analyst/resumes",
};

function setup() {
  mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/batch`]: BATCH,
    [`/api/roles/${REQ_ID}/screen/preflight`]: {
      will_run: true, reason: "never_run",
      changed: { checklist: false, rules: false, location: false, preferences: false },
      files: BATCH.files.map((f) => ({ name: f.name, status: "new" as const })),
      counts: { total: 2, new: 2, updated: 0, cached: 0, removed: 0 },
    },
    [`/api/roles/${REQ_ID}/screen/active`]: { job: null },
    [`/api/roles/${REQ_ID}/review`]: {
      ready: false, req_id: REQ_ID, title: "Financial Analyst", location_display: "India",
    },
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/candidates` }
  );
}

describe("Candidates résumé downloads", () => {
  it("gives every row a download link and a whole-batch ZIP link", async () => {
    setup();
    // per-row single-file download (URL-encoded filename)
    const anita = await screen.findByRole("link", { name: "Download Anita Rao.pdf" });
    expect(anita).toHaveAttribute(
      "href", `/api/roles/${REQ_ID}/batch/file/Anita%20Rao.pdf`);
    expect(anita).toHaveAttribute("download");
    expect(screen.getByRole("link", { name: "Download Vikram Shah.pdf" })).toHaveAttribute(
      "href", `/api/roles/${REQ_ID}/batch/file/Vikram%20Shah.pdf`);

    // whole-batch ZIP
    const zip = screen.getByRole("link", { name: /Download all \(ZIP\)/ });
    expect(zip).toHaveAttribute("href", `/api/roles/${REQ_ID}/batch/download`);
    expect(zip).toHaveAttribute("download");
  });
});
