// The compact live-screening pill in the workspace title row — visible on
// EVERY tab while a run is in flight, and absent when nothing is running (the
// meta line's "N screened" covers the resting state without extra chrome).
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { ScreenJob } from "../types";

const REQ_ID = "financial-analyst";

function setup(active: { job: ScreenJob | null }) {
  mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/screen/active`]: active,
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/overview` }
  );
}

describe("Live screening pill", () => {
  it("shows live progress in the header while a run is in flight", async () => {
    setup({ job: { job_id: "j1", req_id: REQ_ID, status: "running", done: 3, total: 8, message: "Screening 3 of 8…", error: "" } });
    expect(await screen.findByText("Screening 3/8")).toBeInTheDocument();
  });

  it("wears the screening hue, never the green that means complete", async () => {
    // The dashboard's own live chip and the "Screened" stage both read orange;
    // green is this palette's COMPLETE, which is the one thing a run in flight
    // must not say. (2026-09-13 — the pill was green.)
    setup({ job: { job_id: "j1", req_id: REQ_ID, status: "running", done: 3, total: 8, message: "", error: "" } });
    const label = await screen.findByText("Screening 3/8");
    expect(label.style.color).toBe("var(--hue-orange-ink)");
    expect(label.parentElement?.style.background).toBe("var(--hue-orange-tint)");
  });

  it("stays out of the way when nothing is running", async () => {
    setup({ job: null }); // screened fixture, nothing in flight
    // the header renders (title visible) with no live pill — the meta line's
    // "4 screened" (pinned by workspace.test) carries the resting count
    expect(await screen.findByRole("heading", { name: "Financial Analyst" })).toBeInTheDocument();
    expect(screen.queryByText(/Screening \d+\/\d+/)).not.toBeInTheDocument();
  });
});
