// The Candidates tab groups a split bulk file's derived résumés under a
// provenance header (temporary iCIMS bridge), while ordinary single-résumé files
// still render flat — and a derived résumé is never listed twice.
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { Preflight } from "../types";

const REQ_ID = "financial-analyst";

const BATCH = {
  count: 4,
  files: [
    { name: "01 - Alice Alpha.pdf" },
    { name: "02 - Bob Beta.pdf" },
    { name: "03 - Carol Gamma.pdf" },
    { name: "dan-delta.pdf" },
  ],
  folder: "data/roles/financial-analyst/resumes",
  groups: [{
    source: "bulk_print_output.pdf",
    derived: ["01 - Alice Alpha.pdf", "02 - Bob Beta.pdf", "03 - Carol Gamma.pdf"],
    count: 3,
  }],
};

const PREFLIGHT: Preflight = {
  will_run: true, reason: "never_run",
  changed: { checklist: false, rules: false, location: false, scoring_model: false, preferences: false },
  details: [],
  stale: null,
  files: BATCH.files.map((f) => ({ name: f.name, status: "new" as const })),
  counts: { total: 4, new: 4, updated: 0, cached: 0, removed: 0 },
};

function setup() {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/batch`]: BATCH,
    [`/api/roles/${REQ_ID}/screen/preflight`]: PREFLIGHT,
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
  return api;
}

describe("Candidates grouping (split bulk file)", () => {
  it("shows the source header with its count and lists each résumé once", async () => {
    setup();
    expect(await screen.findByText("bulk_print_output.pdf")).toBeInTheDocument();
    expect(screen.getByText("split into 3 résumés")).toBeInTheDocument();
    // all three derived résumés + the standalone file are shown, each exactly once
    for (const name of ["01 - Alice Alpha.pdf", "02 - Bob Beta.pdf", "03 - Carol Gamma.pdf", "dan-delta.pdf"]) {
      expect(screen.getAllByText(name)).toHaveLength(1);
    }
  });
});

describe("Group header counts people, not files", () => {
  it("states everyone present and stays silent about 'of N' when nobody is missing", async () => {
    // The real 4_01 packet: 15 names, 14 new files, 1 person already in the batch
    // from another export. Reporting the WRITTEN count against the contents page
    // ("split into 14 résumés of 15") read as a loss when nothing was lost.
    mockApi({
      [`/api/roles/${REQ_ID}`]: roleFixture(),
      [`/api/roles/${REQ_ID}/batch`]: {
        ...BATCH,
        groups: [{ source: "bulk.pdf", derived: BATCH.groups[0].derived, count: 14,
                   expected: 15, present: 15, duplicates_dropped: 1 }],
      },
      [`/api/roles/${REQ_ID}/screen/preflight`]: PREFLIGHT,
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
    expect(await screen.findByText("split into 15 résumés")).toBeInTheDocument();
    expect(screen.queryByText(/of 15/)).not.toBeInTheDocument();
    expect(screen.getByText(/Nobody from this file is missing/)).toBeInTheDocument();
  });

  it("still shows 'of N' when people genuinely could not be separated", async () => {
    mockApi({
      [`/api/roles/${REQ_ID}`]: roleFixture(),
      [`/api/roles/${REQ_ID}/batch`]: {
        ...BATCH,
        groups: [{ source: "bulk.pdf", derived: BATCH.groups[0].derived, count: 3,
                   expected: 5, present: 3, shortfall: 2 }],
      },
      [`/api/roles/${REQ_ID}/screen/preflight`]: PREFLIGHT,
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
    // "of 5" appears in both the chip and the shortfall warning — assert the chip.
    expect(await screen.findByText(/split into 3 résumés/)).toBeInTheDocument();
    expect(screen.getByText(/of 5/, { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(/2 of 5 résumés couldn't be separated/)).toBeInTheDocument();
  });
});
