// "Upload & start screening" hands the intent to the SERVER (?auto_screen=1) so it
// survives the tab closing. That meant the run began with nothing telling this tab
// about it: the page went on offering a Screen button for a requisition that was
// already screening, until someone reloaded (2026-09-10).
//
// The tab now asks once, at the single moment a run can have appeared without it
// starting one — the end of preparation. One request at a transition, not a poll.
import { describe, it, expect } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { BatchPreparing, Preflight, ScreenJob } from "../types";

const REQ_ID = "financial-analyst";

const DONE: BatchPreparing = {
  active: false, live: false, status: "done",
  processed: 6, pending: 0, total: 6,
  added: 6, duplicates: 0, unsupported: 0,
  split_files: 1, split_resumes: 6, failed: 0,
  current: null, sentence: "6 résumés ready.", message: null,
  auto_screen: true, stopping: false,
};

const RUNNING: ScreenJob = {
  job_id: "job-1", req_id: REQ_ID, status: "running",
  done: 2, total: 6, started_utc: "2026-09-10T10:00:00", actor: "ta@example.com",
} as ScreenJob;

const PREFLIGHT: Preflight = {
  will_run: true, reason: "never_run",
  changed: { checklist: false, rules: false, location: false, scoring_model: false, preferences: false },
  details: [], stale: null,
  files: [{ name: "01 - Alice Alpha.pdf", status: "new" as const }],
  counts: { total: 6, new: 6, updated: 0, cached: 0, removed: 0 },
};

function setup(activeJob: ScreenJob | null) {
  return mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/batch`]: {
      count: 6, files: [{ name: "01 - Alice Alpha.pdf" }],
      folder: "data/roles/financial-analyst/resumes", preparing: DONE,
    },
    [`/api/roles/${REQ_ID}/screen/preflight`]: PREFLIGHT,
    [`/api/roles/${REQ_ID}/screen/active`]: { job: activeJob },
    [`/api/screen/jobs/job-1`]: RUNNING,
    [`/api/roles/${REQ_ID}/screen/runs`]: { runs: [] },
    [`/api/roles/${REQ_ID}/review`]: {
      ready: false, req_id: REQ_ID, title: "Financial Analyst", location_display: "India",
    },
  });
}

function render() {
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/candidates` }
  );
}

describe("a run the tab did not start", () => {
  it("shows the live screening instead of offering to screen again", async () => {
    setup(RUNNING);
    render();
    // The Screen call-to-action belongs to a requisition that is NOT running.
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /Screen résumés/i })).toBeNull();
    }, { timeout: 4000 });
  });

  it("leaves the Screen call-to-action alone when nothing is running", async () => {
    setup(null);
    render();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Screen résumés/i })).toBeInTheDocument();
    }, { timeout: 4000 });
  });

  it("picks up a run that starts while the reader is sitting on the page", async () => {
    // The case the mount re-attach cannot cover: preparation is still going when the
    // tab renders, and the server starts the run when it finishes.
    const api = setup(null);
    api.route(`/api/roles/${REQ_ID}/batch`, {
      count: 6, files: [{ name: "01 - Alice Alpha.pdf" }],
      folder: "data/roles/financial-analyst/resumes",
      preparing: { ...DONE, active: true, live: true, status: "working", pending: 4, processed: 2 },
    });
    render();
    await waitFor(() => {
      const btn = screen.queryByRole("button", { name: /Screen résumés/i }) as HTMLButtonElement | null;
      expect(!btn || btn.disabled).toBe(true);   // preparation holds it anyway
    }, { timeout: 4000 });

    // Preparation finishes, and the server has already started the run.
    api.route(`/api/roles/${REQ_ID}/screen/active`, { job: RUNNING });
    api.route(`/api/roles/${REQ_ID}/batch`, {
      count: 6, files: [{ name: "01 - Alice Alpha.pdf" }],
      folder: "data/roles/financial-analyst/resumes", preparing: DONE,
    });

    // The batch poll notices preparation finished within its 2.5 s tick; the tab then
    // asks once and swaps the call-to-action for the live progress. Waiting on the
    // request count would prove nothing — the mount re-attach already made one.
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /Screen résumés/i })).toBeNull();
    }, { timeout: 8000 });
  });

  it("never flashes the Screen button while it is finding out", async () => {
    // Preparation ending and the run being discovered are one round trip apart. The
    // reader must not be offered an action that is already under way, even briefly.
    const api = setup(null);
    api.route(`/api/roles/${REQ_ID}/batch`, {
      count: 6, files: [{ name: "01 - Alice Alpha.pdf" }],
      folder: "data/roles/financial-analyst/resumes",
      preparing: { ...DONE, active: true, live: true, status: "working", pending: 4, processed: 2 },
    });
    render();
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /Screen résumés/i })).toBeNull();
    }, { timeout: 4000 });

    api.route(`/api/roles/${REQ_ID}/screen/active`, { job: RUNNING });
    api.route(`/api/roles/${REQ_ID}/batch`, {
      count: 6, files: [{ name: "01 - Alice Alpha.pdf" }],
      folder: "data/roles/financial-analyst/resumes", preparing: DONE,
    });

    // Watch continuously. The button may be on screen — it is disabled while the
    // batch prepares — but it must never become CLICKABLE in the gap between
    // preparation ending and the run being found, or a recruiter can start a second
    // run over the top of the one their upload already asked for.
    const offered: boolean[] = [];
    for (let i = 0; i < 40; i++) {
      const btn = screen.queryByRole("button", { name: /Screen résumés/i }) as HTMLButtonElement | null;
      offered.push(!!btn && !btn.disabled);
      await new Promise((r) => setTimeout(r, 100));
    }
    expect(offered.some(Boolean)).toBe(false);
  });

});

// The gap the 2026-09-10 pass did not close (2026-09-11 pilot): "Upload and screen"
// leaves the tab holding nothing to wait for.
//
// The hold above hangs off PREPARATION — but a handful of résumés can be prepared
// before the upload's own response comes back, so this tab never sees preparation at
// all. It sees a finished batch, a run that has not started yet, and (because the mount
// probe already answered "nothing running") a live Screen button for work that is
// already on its way. Nothing arrives to correct it: the batch poll only runs while
// preparation does.
describe("upload and screen, from the moment of the drop", () => {
  const READY_BATCH = {
    count: 1, files: [{ name: "01 - Alice Alpha.pdf" }],
    folder: "data/roles/financial-analyst/resumes", preparing: null,
  };
  // What the upload returns in the case that bit the pilot: preparation already over,
  // the intent recorded, and no run yet.
  const UPLOADED = {
    count: 2, files: [{ name: "01 - Alice Alpha.pdf" }, { name: "Bhavna Rao.pdf" }],
    folder: "data/roles/financial-analyst/resumes", preparing: DONE,
    sentence: "1 résumé added.",
  };

  const screenBtn = () =>
    screen.queryByRole("button", { name: /Screen résumés/i }) as HTMLButtonElement | null;

  async function dropAndChooseScreening() {
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([new Uint8Array(1)], "Bhavna Rao.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });
    await userEvent.click(await screen.findByRole("button", { name: /Upload and screen when ready/i }));
  }

  it("never offers to screen between the click and the run appearing", async () => {
    const api = setup(null);
    api.route(`/api/roles/${REQ_ID}/batch`, READY_BATCH);
    api.route(`/api/roles/${REQ_ID}/batch/upload`, UPLOADED);
    render();

    // The button is live to begin with — so what follows measures the hold, not some
    // other block (no checklist, no résumés) that would pass this test for free.
    await waitFor(() => expect(screenBtn()?.disabled).toBe(false), { timeout: 4000 });

    await dropAndChooseScreening();

    // The server starts the run at the end of preparation; the tab finds out when it
    // asks. Until then it must not offer to start a second one.
    api.route(`/api/roles/${REQ_ID}/screen/active`, { job: RUNNING });
    const offered: boolean[] = [];
    for (let i = 0; i < 25; i++) {
      const btn = screenBtn();
      offered.push(!!btn && !btn.disabled);
      await new Promise((r) => setTimeout(r, 100));
    }
    expect(offered.some(Boolean)).toBe(false);

    // ...and it ends on the live run, not on a button.
    await waitFor(() => expect(screenBtn()).toBeNull(), { timeout: 8000 });
  });

  it("tells every résumé it is on its way to being screened", async () => {
    const api = setup(null);
    api.route(`/api/roles/${REQ_ID}/batch`, READY_BATCH);
    api.route(`/api/roles/${REQ_ID}/batch/upload`, UPLOADED);
    render();
    await waitFor(() => expect(screenBtn()?.disabled).toBe(false), { timeout: 4000 });
    // Before the choice, an unscreened résumé says exactly that.
    expect(screen.getByText("not screened")).toBeInTheDocument();

    await dropAndChooseScreening();

    await waitFor(() => {
      // The freshly added file has no preflight entry yet — that is the row that most
      // needs to say something, and it used to say nothing at all.
      const row = screen.getByText("Bhavna Rao.pdf").closest(".file-row") as HTMLElement;
      expect(within(row).getByText("preparing to screen")).toBeInTheDocument();
      expect(row.className).toContain("st-preparing");
    }, { timeout: 4000 });

    // "not screened" is gone: it is true and it reads as a dead end.
    expect(screen.queryByText("not screened")).toBeNull();
    // And the call-to-action answers the question the button no longer can.
    expect(screen.getByText("Screening starts on its own")).toBeInTheDocument();
  });

  it("gives the button back if the run never comes — a stuck hold is worse", async () => {
    // The checklist gate could have closed behind the upload, or the worker could have
    // failed. Three attempts, then the recruiter gets their button back.
    const api = setup(null);
    api.route(`/api/roles/${REQ_ID}/batch`, READY_BATCH);
    api.route(`/api/roles/${REQ_ID}/batch/upload`, UPLOADED);
    render();
    await waitFor(() => expect(screenBtn()?.disabled).toBe(false), { timeout: 4000 });
    await dropAndChooseScreening();
    await waitFor(() => expect(screenBtn()?.disabled).toBe(true), { timeout: 4000 });
    await waitFor(() => expect(screenBtn()?.disabled).toBe(false), { timeout: 12000 });
  });
});
