// A bulk batch (a single iCIMS Bulk Print split into many résumés) shouldn't
// bury the Screen CTA under an endless scroll: the split group's children become
// collapsible (default expanded), with a Collapse all / Expand all control.
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { Preflight } from "../types";

const REQ_ID = "financial-analyst";

// 35 derived résumés from one bulk source → over the bulk threshold (30).
const NAMES = Array.from({ length: 35 }, (_, i) => `cand-${String(i + 1).padStart(2, "0")}.pdf`);

const BATCH = {
  count: 35,
  files: NAMES.map((name) => ({ name })),
  folder: "data/roles/financial-analyst/resumes",
  groups: [{ source: "bulk_print_output.pdf", derived: NAMES, count: 35 }],
};

const PREFLIGHT: Preflight = {
  will_run: true, reason: "never_run",
  changed: { checklist: false, rules: false, location: false, scoring_model: false, preferences: false },
  details: [],
  stale: null,
  files: [],
  counts: { total: 35, new: 35, updated: 0, cached: 0, removed: 0 },
};

function setup() {
  mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/batch`]: BATCH,
    [`/api/roles/${REQ_ID}/screen/preflight`]: PREFLIGHT,
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/candidates` }
  );
}

describe("Candidates bulk batch", () => {
  it("collapses and re-expands the split group's children", async () => {
    setup();
    // default expanded: the source header and its children are all visible
    expect(await screen.findByText("bulk_print_output.pdf")).toBeInTheDocument();
    expect(screen.getByText("cand-01.pdf")).toBeInTheDocument();

    // Collapse all → children hidden, source header (provenance) stays
    await userEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.queryByText("cand-01.pdf")).not.toBeInTheDocument();
    expect(screen.getByText("bulk_print_output.pdf")).toBeInTheDocument();
    expect(screen.getByText("split into 35 résumés")).toBeInTheDocument();

    // Expand all → children back
    await userEvent.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.getByText("cand-01.pdf")).toBeInTheDocument();
  });
});
