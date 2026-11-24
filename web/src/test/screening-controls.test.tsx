// Screening session controls: the ETA, the Pause/Cancel pair (initiator or
// Super Admin only), the per-row live status ("screened" / "screening…" /
// "not screened"), the mid-run batch freeze, and the persistent run history
// with the "met every must-have" link into a filtered Review.
import { describe, it, expect, afterEach, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { ScreenJob, ScreenRunsResponse } from "../types";

const REQ_ID = "financial-analyst";

const BATCH = {
  count: 4,
  files: [
    { name: "Anita Rao.pdf" },
    { name: "Vikram Shah.pdf" },
    { name: "Meera Roach.pdf" },
    { name: "Late Add.pdf" }, // uploaded after the run started
  ],
  folder: "data/roles/financial-analyst/resumes",
};

// A run in flight over the first three files; one already finished.
function runningJob(overrides: Partial<ScreenJob> = {}): ScreenJob {
  return {
    job_id: "j-live",
    req_id: REQ_ID,
    status: "running",
    done: 1,
    total: 3,
    message: "Screening 1 of 3…",
    error: "",
    eta_seconds: 300,
    actor: "ta@example.com",
    started_utc: "2026-07-17T09:00:00+00:00",
    planned: ["Anita Rao.pdf", "Vikram Shah.pdf", "Meera Roach.pdf"],
    completed: ["Anita Rao.pdf"],
    ...overrides,
  };
}

const HISTORY: ScreenRunsResponse = {
  runs: [
    {
      ts: "2026-07-17T11:00:00+00:00",
      kind: "screen_completed",
      actor: "ta@example.com",
      summary: "Screened 56 candidates — IN hiring rules · checklist v2 · run run-3",
      run_id: "run-3",
      counts: { scored: 56, resumes_in: 56, gate_passers: 2, errors: 0 },
      duration_seconds: 3.2,
    },
    {
      ts: "2026-07-16T10:00:00+00:00",
      kind: "screen_stopped",
      actor: "head@example.com",
      summary:
        "Screening stopped by head@example.com after 10 of 56 résumés — finished work is kept, screening again picks up where it left off",
    },
  ],
  note: null,
};

function setup(extra: Record<string, unknown> = {}) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/batch`]: BATCH,
    [`/api/roles/${REQ_ID}/screen/preflight`]: {
      will_run: true,
      reason: "files_changed",
      changed: { checklist: false, rules: false, location: false, preferences: false },
      files: BATCH.files.map((f) => ({ name: f.name, status: "new" as const })),
      counts: { total: 4, new: 4, updated: 0, cached: 0, removed: 0 },
    },
    [`/api/roles/${REQ_ID}/screen/active`]: { job: null },
    [`/api/roles/${REQ_ID}/screen/runs`]: { runs: [], note: null },
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

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Screening session controls", () => {
  it("shows the ETA, the Pause/Cancel pair and per-row live status while running", async () => {
    // the viewer started this run (actor = DEFAULT_ME), so the controls show
    setup({
      [`/api/roles/${REQ_ID}/screen/active`]: { job: runningJob({ actor: "dev@example.com" }) },
      "/api/screen/jobs/j-live": runningJob({ actor: "dev@example.com" }),
    });
    expect(await screen.findByText("Screening…")).toBeInTheDocument();
    // the run's own remaining-time estimate
    expect(screen.getByText(/about 5 min left/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeInTheDocument();
    // per-row live status: finished / in flight / added after the run started
    const doneRow = screen.getByText("Anita Rao.pdf").closest("div") as HTMLElement;
    expect(within(doneRow).getByText("screened")).toBeInTheDocument();
    expect(screen.getAllByText("screening…")).toHaveLength(2);
    const lateRow = screen.getByText("Late Add.pdf").closest("div") as HTMLElement;
    expect(within(lateRow).getByText("not screened")).toBeInTheDocument();
  });

  it("hides the controls from everyone but the initiator (or a Super Admin)", async () => {
    setup({
      [`/api/roles/${REQ_ID}/screen/active`]: { job: runningJob() }, // actor ta@…
      "/api/screen/jobs/j-live": runningJob(),
    });
    expect(await screen.findByText("Screening…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    // the initiator renders as a PERSON (2026-09-06) — a name span inside the sentence
    expect(screen.getByText(/Started by/)).toHaveTextContent(/Started by ta@example\.com — only they or a Super Admin/);
  });

  it("cancels a run: confirm → POST intent cancel → 'Screen again' returns", async () => {
    const cancelled = runningJob({
      actor: "dev@example.com",
      status: "stopped",
      message:
        "Screening cancelled by dev@example.com. The batch is unlocked — finished work stays cached, so the next screen only pays for new résumés.",
      eta_seconds: null,
    });
    const api = setup({
      [`/api/roles/${REQ_ID}/screen/active`]: { job: runningJob({ actor: "dev@example.com" }) },
      "/api/screen/jobs/j-live": cancelled, // the next poll sees the cancel
      "/api/screen/jobs/j-live/cancel": cancelled,
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    await userEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    expect(confirm.mock.calls[0][0]).toMatch(/Cancel this run\? The batch unlocks/);
    expect(api.callsTo("/api/screen/jobs/j-live/cancel")).toHaveLength(1);
    expect(api.callsTo("/api/screen/jobs/j-live/cancel")[0].body).toEqual({ intent: "cancel" });

    expect(await screen.findByText("Screening cancelled", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText(/cancelled by dev@example\.com/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Screen again" })).toBeInTheDocument();
  });

  it("pauses without a confirm and offers Resume + Cancel while paused", async () => {
    const paused = runningJob({
      actor: "dev@example.com",
      status: "paused",
      message: "Screening paused by dev@example.com. The batch stays locked — resume to pick up where it left off, or cancel the run to unlock the batch.",
      eta_seconds: null,
    });
    const api = setup({
      [`/api/roles/${REQ_ID}/screen/active`]: { job: runningJob({ actor: "dev@example.com" }) },
      "/api/screen/jobs/j-live": paused, // the next poll sees the pause
      "/api/screen/jobs/j-live/cancel": paused,
    });
    await userEvent.click(await screen.findByRole("button", { name: "Pause" }));
    expect(api.callsTo("/api/screen/jobs/j-live/cancel")[0].body).toEqual({ intent: "pause" });

    expect(await screen.findByText(/Screening paused — the batch stays locked/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume screening" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeInTheDocument();
  });

  it("freezes the batch while a run is live — remove/add/clear are disabled", async () => {
    const api = setup({
      [`/api/roles/${REQ_ID}/screen/active`]: { job: runningJob() },
      "/api/screen/jobs/j-live": runningJob(),
    });
    await screen.findByText("Screening…");

    // every row's remove is disabled; clicking it can't hit the API
    const doneRow = screen.getByText("Anita Rao.pdf").closest("div") as HTMLElement;
    expect(within(doneRow).getByLabelText(/^Remove .* from this batch$/)).toBeDisabled();
    await userEvent.click(within(doneRow).getByLabelText(/^Remove .* from this batch$/));
    expect(api.callsTo(`/api/roles/${REQ_ID}/batch/Anita%20Rao.pdf`)).toHaveLength(0);
    // …and so are the add/clear affordances
    expect(screen.getByRole("button", { name: "＋ Add résumés" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Clear batch" })).toBeDisabled();
  });

  it("renders the run history with the linked must-have count and earlier runs", async () => {
    setup({ [`/api/roles/${REQ_ID}/screen/runs`]: HISTORY });
    // scope to the history card (the workspace header also says "added by …")
    const title = await screen.findByText("Screening history");
    const card = title.closest("div")!.parentElement as HTMLElement;
    // the latest run: outcome + who + a link into the filtered Review
    expect(within(card).getByText(/Screened 56 of 56 résumés in 3\.2s/)).toBeInTheDocument();
    expect(within(card).getByText(/· by ta@example\.com/)).toBeInTheDocument();
    const link = within(card).getByRole("link", { name: "2 met every must-have" });
    expect(link).toHaveAttribute("href", `/reqs/${REQ_ID}/review?show=full_look`);
    // everyone-stays-listed reminder rides with the outcome line
    expect(screen.getByText(/Everyone stays listed/)).toBeInTheDocument();
    // earlier runs are collapsed behind a toggle
    expect(screen.queryByText(/stopped by head@example\.com/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Earlier runs (1)" }));
    expect(screen.getByText(/stopped by head@example\.com/)).toBeInTheDocument();
  });

  it("shows no history card when nothing was ever run", async () => {
    setup();
    await screen.findByRole("button", { name: /Screen résumés/ });
    expect(screen.queryByText("Screening history")).not.toBeInTheDocument();
  });

  it("re-shows a cancelled run (and its screened rows) on mount via `last`", async () => {
    // a partial cancel wrote no run_meta, so preflight can't know Anita was
    // screened — the reattached run's completed set carries that truth.
    const cancelled = runningJob({
      status: "stopped",
      done: 1,
      total: 3,
      message:
        "Screening cancelled by head@example.com. The batch is unlocked — finished work stays cached, so the next screen only pays for new résumés.",
      eta_seconds: null,
      completed: ["Anita Rao.pdf"],
    });
    setup({ [`/api/roles/${REQ_ID}/screen/active`]: { job: null, last: cancelled } });

    // the cancelled panel survives the reload / user switch, not a bare CTA
    expect(await screen.findByText("Screening cancelled")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Screen again" })).toBeInTheDocument();
    // the résumé it finished still reads "screened"; the rest, "not screened"
    const doneRow = screen.getByText("Anita Rao.pdf").closest("div") as HTMLElement;
    expect(within(doneRow).getByText("screened")).toBeInTheDocument();
    const pendingRow = screen.getByText("Meera Roach.pdf").closest("div") as HTMLElement;
    expect(within(pendingRow).getByText("not screened")).toBeInTheDocument();
  });

  it("ignores a completed `last` (the preflight CTA + history handle it)", async () => {
    const done = runningJob({ status: "done", done: 3, total: 3, message: "Scored 3/3 resumes." });
    setup({ [`/api/roles/${REQ_ID}/screen/active`]: { job: null, last: done } });
    expect(await screen.findByRole("button", { name: /Screen résumés/ })).toBeInTheDocument();
    expect(screen.queryByText("Done")).not.toBeInTheDocument();
  });
});
