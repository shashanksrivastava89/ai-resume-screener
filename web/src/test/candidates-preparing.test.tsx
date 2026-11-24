// Background résumé preparation (2026-08-26): uploads return instantly and the
// Candidates tab narrates the server-side work — a progress strip with live
// counts (never a blocking spinner), screening held until the batch is complete,
// and a plain Try-again when preparation errors.
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { BatchPreparing, Preflight } from "../types";

const REQ_ID = "financial-analyst";

const PREPARING: BatchPreparing = {
  active: true, live: true, status: "working",
  processed: 2, pending: 4, total: 6,
  added: 2, duplicates: 1, unsupported: 0,
  split_files: 1, split_resumes: 40, failed: 0,
  current: "bulk_print_3.pdf", sentence: null, message: null,
  auto_screen: false, stopping: false,
};

const PREFLIGHT: Preflight = {
  will_run: true, reason: "never_run",
  changed: { checklist: false, rules: false, location: false, scoring_model: false, preferences: false },
  details: [],
  stale: null,
  files: [{ name: "01 - Alice Alpha.pdf", status: "new" as const }],
  counts: { total: 1, new: 1, updated: 0, cached: 0, removed: 0 },
};

function setup(preparing: BatchPreparing, roleOverride: Record<string, unknown> = {}) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: { ...roleFixture(), ...roleOverride },
    [`/api/roles/${REQ_ID}/batch`]: {
      count: 1,
      files: [{ name: "01 - Alice Alpha.pdf" }],
      folder: "data/roles/financial-analyst/resumes",
      preparing,
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
  return api;
}

describe("Candidates preparing strip", () => {
  it("narrates live progress and holds the Screen CTA while work is outstanding", async () => {
    setup(PREPARING);
    expect(await screen.findByText("Getting résumés ready — 2 of 6 files done")).toBeInTheDocument();
    // 2 added − 1 packet + 40 slices = 41 candidates ready so far.
    expect(screen.getByText(/41 candidates ready/)).toBeInTheDocument();
    expect(screen.getByText(/1 duplicate skipped/)).toBeInTheDocument();
    expect(screen.getByText(/working on bulk_print_3\.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/You can leave this page/)).toBeInTheDocument();
    // Screening is gated with the plain reason…
    expect(await screen.findByText("Not ready to screen yet")).toBeInTheDocument();
    expect(screen.getByText(/screening unlocks when they're ready/)).toBeInTheDocument();
    // …but adding MORE résumés stays available (staging is always open).
    expect(screen.getByRole("button", { name: /Add résumés/ })).toBeEnabled();
  });

  it("shows a plain error with Try again when preparation failed", async () => {
    setup({
      ...PREPARING, live: false, status: "error", current: null,
      message: "Preparation stopped: disk full",
    });
    expect(await screen.findByText("Preparing these résumés hit a problem")).toBeInTheDocument();
    expect(screen.getByText("Preparation stopped: disk full")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });
});

describe("Dropping files asks what to do next", () => {
  // The file input is hidden and only exists once the batch card has rendered, so
  // every case waits for the card before touching it.
  async function dropOneFile() {
    expect(await screen.findByText(/Candidate batch/)).toBeInTheDocument();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toBeTruthy();
    await userEvent.upload(input, new File([new Uint8Array([1, 2, 3])], "cv.pdf",
                                          { type: "application/pdf" }));
  }

  it("offers upload-only and upload-and-screen, and records the intent in the URL", async () => {
    const api = setup({ ...PREPARING, active: false, live: false, status: "idle" });
    await dropOneFile();

    // Both paths are offered — the choice changes whether the recruiter can walk away.
    expect(await screen.findByText("Add 1 file")).toBeInTheDocument();
    const auto = screen.getByRole("button", { name: /Upload and screen when ready/ });
    expect(auto).toBeEnabled();

    await userEvent.click(auto);
    // The intent rides on the URL so every chunk carries it and the SERVER owns it —
    // a client-side trigger would die with the tab, which is the case this exists for.
    // `url` keeps the query; `path` has it stripped, so assert on the url.
    const uploads = api.calls.filter((c) => c.path.includes("/batch/upload"));
    expect(uploads.length).toBeGreaterThan(0);
    expect(uploads[0].url).toContain("auto_screen=1");
  });

  it("uploads without the flag when only adding files", async () => {
    const api = setup({ ...PREPARING, active: false, live: false, status: "idle" });
    await dropOneFile();
    await userEvent.click(await screen.findByRole("button", { name: "Upload only" }));
    const uploaded = api.calls.find((c) => c.path.includes("/batch/upload"));
    expect(uploaded?.url).not.toContain("auto_screen");
  });

  it("disables auto-screen with a reason when the checklist isn't approved", async () => {
    setup({ ...PREPARING, active: false, live: false, status: "idle" },
          { checklist_ready: false, checklist_block_reason: "waiting on HR Head" });
    await dropOneFile();

    expect(await screen.findByText("Add 1 file")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Upload and screen when ready/ })).toBeDisabled();
    expect(screen.getByText(/checklist needs to be approved/)).toBeInTheDocument();
    // ...and plain upload is still available: the gate is on screening, not on adding.
    expect(screen.getByRole("button", { name: "Upload only" })).toBeEnabled();
  });

  it("offers a Stop while preparing and says a run is owed", async () => {
    setup({ ...PREPARING, auto_screen: true });
    expect(await screen.findByText(/screening starts on its own/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    // The confirm states what survives — stopping is not undoing.
    expect(await screen.findByText("Stop preparing résumés?")).toBeInTheDocument();
    expect(screen.getByText(/Those candidates stay in the batch/)).toBeInTheDocument();
  });
});

// NOT TESTED HERE, deliberately. The busy overlay's label ("Clearing the batch…" rather
// than the old "Uploading…") is only on screen while the request is in flight, and the
// mock fetch resolves synchronously — the request is even issued BEFORE setBusy runs, so
// there is no window in which a test can observe the overlay. Rather than ship a
// vacuous assertion, this one is verified by eye on a real deployment; the code change
// is a single label per action in refreshBatch.
