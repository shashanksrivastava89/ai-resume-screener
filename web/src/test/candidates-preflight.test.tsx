// P35: the change-aware Candidates tab — the three preflight states above the
// screen CTA, the per-file tags, and the honesty rule: ✨ magic only when the
// run would actually call the model; a no-op offers Review with NO POST.
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { Preflight } from "../types";

const REQ_ID = "financial-analyst";

const BATCH = {
  count: 3,
  files: [{ name: "Anita Rao.pdf" }, { name: "Vikram Shah.pdf" }, { name: "Meera Roach.pdf" }],
  folder: "data/roles/financial-analyst/resumes",
};

function preflight(overrides: Partial<Preflight>): Preflight {
  return {
    will_run: true,
    reason: "never_run",
    changed: { checklist: false, rules: false, location: false, scoring_model: false, preferences: false },
  details: [],
  stale: null,
    files: BATCH.files.map((f) => ({ name: f.name, status: "new" as const })),
    counts: { total: 3, new: 3, updated: 0, cached: 0, removed: 0 },
    ...overrides,
  };
}

function setup(pre: Preflight, role = roleFixture(), extra: Record<string, unknown> = {}) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: role,
    [`/api/roles/${REQ_ID}/batch`]: BATCH,
    [`/api/roles/${REQ_ID}/screen/preflight`]: pre,
    // default: no run in flight (the auto-resume probe on mount)
    [`/api/roles/${REQ_ID}/screen/active`]: { job: null },
    [`/api/roles/${REQ_ID}/review`]: {
      ready: false, req_id: REQ_ID, title: "Financial Analyst", location_display: "India",
    },
    ...extra,
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

describe("Candidates preflight", () => {
  it("nothing_changed: no-op copy, plain CTA navigates to Review with NO POST", async () => {
    const api = setup(preflight({
      will_run: false,
      reason: "nothing_changed",
      files: BATCH.files.map((f) => ({ name: f.name, status: "cached" as const })),
      counts: { total: 3, new: 0, updated: 0, cached: 3, removed: 0 },
    }));
    expect(await screen.findByText("Nothing changed since the last run")).toBeInTheDocument();
    expect(screen.getByText(
      "Same checklist, rules and batch — screening again would be a no-op, so the shortlist is already current."
    )).toBeInTheDocument();
    // every row says where it stands — "screened" chips show here too, not
    // only on a partial re-run (and "cached" never surfaces as a term)
    expect(screen.getAllByText("screened")).toHaveLength(3);
    expect(screen.queryByText("cached")).not.toBeInTheDocument();

    const cta = screen.getByRole("button", { name: "Proceed to review" });
    expect(cta.className).toContain("btn-primary"); // plain green, never magic
    expect(document.querySelector(".btn-magic")).toBeNull();

    await userEvent.click(cta);
    expect(await screen.findByText("No candidates to review yet")).toBeInTheDocument();
    expect(api.callsTo(`/api/roles/${REQ_ID}/screen`)).toHaveLength(0); // NO POST
  });

  it("files_changed: partial-run headline, per-file tags, magic CTA", async () => {
    setup(preflight({
      reason: "files_changed",
      files: [
        { name: "Anita Rao.pdf", status: "cached" },
        { name: "Vikram Shah.pdf", status: "new" },
        { name: "Meera Roach.pdf", status: "updated" },
      ],
      counts: { total: 3, new: 1, updated: 1, cached: 1, removed: 0 },
    }));
    // the design's split: the headline asks, the note explains the partial run
    expect(await screen.findByText("✨ Screen 3 résumés")).toBeInTheDocument();
    expect(screen.getByText(
      "2 file(s) new or updated will screen — 1 cached result is reused free."
    )).toBeInTheDocument();
    // per-file tags in the batch list — the four-term vocabulary
    expect(screen.getByText("screened")).toBeInTheDocument();
    expect(screen.getByText("not screened")).toBeInTheDocument();
    expect(screen.getByText("re-screen")).toBeInTheDocument();

    const magic = screen.getByRole("button", { name: /Screen résumés/ });
    expect(magic.className).toContain("btn-magic");
    expect(magic).toBeEnabled();
  });

  it("config_changed: full re-screen headline names what moved, with the magic CTA", async () => {
    setup(preflight({
      reason: "config_changed",
      changed: { checklist: true, rules: false, location: false, scoring_model: false, preferences: false },
      files: BATCH.files.map((f) => ({ name: f.name, status: "cached" as const })),
      counts: { total: 3, new: 0, updated: 0, cached: 3, removed: 0 },
    }));
    expect(await screen.findByText(
      "Checklist changed — all 3 re-screen against the new version."
    )).toBeInTheDocument();
    // per-file tags show in EVERY state — a setup change flips previously
    // screened rows to "re-screen", matching what the run will actually do
    expect(screen.getAllByText("re-screen")).toHaveLength(3);
    expect(screen.queryByText("screened")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Screen résumés/ }).className).toContain("btn-magic");
  });

  // P40: a hiring-preference change is a config change — the CTA offers a fresh
  // screen so the ±2 nudge lands (previously stuck on "nothing changed").
  it("config_changed via preferences: names hiring preferences, magic CTA", async () => {
    setup(preflight({
      reason: "config_changed",
      changed: { checklist: false, rules: false, location: false, scoring_model: false, preferences: true },
      files: BATCH.files.map((f) => ({ name: f.name, status: "cached" as const })),
      counts: { total: 3, new: 0, updated: 0, cached: 3, removed: 0 },
    }));
    expect(await screen.findByText(
      "Hiring preferences changed — all 3 re-screen against the new version."
    )).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Screen résumés/ }).className).toContain("btn-magic");
  });

  // F3: screening is gated on checklist_ready; the reason rides the button tooltip.
  it("blocks screening with the approval reason when the checklist awaits sign-off", async () => {
    setup(
      preflight({}),
      roleFixture({
        checklist_ready: false,
        checklist_block_reason: "This checklist needs HR Head approval before screening.",
        rubric_version: 2,
      })
    );
    const btn = await screen.findByRole("button", { name: /Screen résumés/ });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "This checklist needs HR Head approval before screening.");
    // adding résumés is never blocked
    expect(screen.getByRole("button", { name: /Add résumés|Choose files|Add files/i })).toBeEnabled();
  });

  it("blocks screening with the publish reason when there's no checklist", async () => {
    setup(
      preflight({}),
      roleFixture({ checklist_ready: false, checklist_block_reason: null, rubric_version: null })
    );
    const btn = await screen.findByRole("button", { name: /Screen résumés/ });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "Publish a screening checklist first.");
  });

  // Auto-resume: returning to the tab while a run is in flight re-attaches the
  // live progress bar instead of showing the CTA (the run never stopped).
  it("re-attaches the progress bar to an in-flight run on mount", async () => {
    const api = setup(preflight({}), roleFixture(), {
      [`/api/roles/${REQ_ID}/screen/active`]: {
        job: { job_id: "j-live", req_id: REQ_ID, status: "running", done: 2, total: 3, message: "Screening 2 of 3…", error: "" },
      },
      // the progress bar then polls this job id
      "/api/screen/jobs/j-live": {
        job_id: "j-live", req_id: REQ_ID, status: "running", done: 2, total: 3, message: "Screening 2 of 3…", error: "",
      },
    });
    // the running headline + progress copy show without the user clicking Screen
    expect(await screen.findByText("Screening…")).toBeInTheDocument();
    expect(screen.getByText("Screening 2 of 3…")).toBeInTheDocument();
    // no new run was started — we only re-attached
    expect(api.callsTo(`/api/roles/${REQ_ID}/screen`)).toHaveLength(0);
  });

  it("shows the CTA (not a progress bar) when no run is active", async () => {
    setup(preflight({})); // default active route returns {job: null}
    expect(await screen.findByRole("button", { name: /Screen résumés/ })).toBeInTheDocument();
    expect(screen.queryByText("Screening…")).not.toBeInTheDocument();
  });
});
