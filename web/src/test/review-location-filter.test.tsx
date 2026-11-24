// The non-scoring location lens on Review (2026-08-27; one-behaviour rework 2026-09-06).
//
// THE INVARIANT THIS PROTECTS. Only a KNOWN distance beyond the radius may hide a
// candidate. "States somewhere we could not place" and "states nothing" are both
// unknown, and an omission is not evidence of distance — so unknowns are included by
// DEFAULT and the recruiter has to opt out deliberately.
//
// ONE behaviour (pilot feedback item 7): the pill opens the distance dialog; the
// distance is set AND the filter applied there ("Show only these N"); while the
// filter is on, the pill's ✕ shows everyone again. The old pill toggled and a tiny
// gear beside it edited — two controls nobody could tell apart.
//
// Also pinned: the place is auto-populated from the job description and NOT editable
// here (a free-text box invited someone to retype what the JD already said, and two
// spellings of one city read as two sites), and the in-radius count is stated on the
// panel as well as the chip.
import { describe, it, expect, beforeEach } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import type { ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";

function card(over: Partial<ReviewCard>): ReviewCard {
  return {
    rank: 1, candidate_id: "c1", name: "Anita Rao", band: "Strong",
    band_label: "Strong match", score: 82, confidence: "high", flag: false,
    chips: [], gates: { total: 0, failed: [] }, must_have_gaps: [],
    decided: null, decided_reason: null, decided_ts: null, ...over,
  };
}

const NEAR = card({ candidate_id: "near", name: "Near Nita", location_within: true });
const FAR = card({ rank: 2, candidate_id: "far", name: "Far Farid", location_within: false });
const UNPLACEABLE = card({ rank: 3, candidate_id: "unk", name: "Unknown Uma", location_within: null });
const SILENT = card({ rank: 4, candidate_id: "sil", name: "Silent Sam", location_within: null });
const ALL = ["Near Nita", "Far Farid", "Unknown Uma", "Silent Sam"];

function setup(radius: number | null) {
  const cards = [NEAR, FAR, UNPLACEABLE, SILENT];
  const payload: ReviewResponse = {
    ready: true, req_id: REQ_ID, title: "Financial Analyst",
    location_display: "India", cards,
    counts: { total: 4, strong: 1, possible: 0, weak: 1, gate_not_met: 0 },
    decision_counts: { forward: 0, discuss: 0, not: 0 },
    work_location: { place: "Gurugram", radius_km: radius, max_km: 100,
                     summary: radius ? `Within ${radius} km from “Gurugram”` : "Job location: “Gurugram”" },
  } as unknown as ReviewResponse;
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/review`]: payload,
    [`/api/roles/${REQ_ID}/batch`]: { count: 4, files: [], folder: "x" },
    [`/api/roles/${REQ_ID}/screen/preflight`]: {
      will_run: false, reason: "nothing_changed",
      changed: { checklist: false, rules: false, location: false, scoring_model: false, preferences: false },
      details: [], stale: null, files: [],
      counts: { total: 4, new: 0, updated: 0, cached: 4, removed: 0 },
    },
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/review` }
  );
  return api;
}

const openDialog = async (name: RegExp) => {
  await userEvent.click(await screen.findByRole("button", { name }));
  return screen.findByRole("dialog", { name: "Distance from the job location" });
};

beforeEach(() => { sessionStorage.clear(); });

describe("Review location filter", () => {
  it("the pill opens the dialog; applying hides only a KNOWN-far candidate, unknowns stay by default", async () => {
    setup(25);
    // The pill carries the in-radius count and OPENS the dialog (never toggles blind).
    const pill = await screen.findByRole("button", { name: /Within 25 km of Gurugram/ });
    expect(pill).toHaveTextContent("1");
    await userEvent.click(pill);
    await screen.findByRole("dialog", { name: "Distance from the job location" });
    for (const name of ALL) expect(screen.getByText(name)).toBeInTheDocument(); // nothing hidden yet

    // Apply: near (1) + the two unknowns = 3
    await userEvent.click(screen.getByRole("button", { name: "Show only these 3" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Near Nita")).toBeInTheDocument();
    expect(screen.queryByText("Far Farid")).not.toBeInTheDocument();  // known, beyond
    // Both kinds of unknown survive: an omission is not evidence of distance.
    expect(screen.getByText("Unknown Uma")).toBeInTheDocument();
    expect(screen.getByText("Silent Sam")).toBeInTheDocument();
    // the readout says what happened, and the ✕ on the pill undoes it in one click
    expect(screen.getByRole("status", { name: "3 of 4 candidates shown" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show everyone again" }));
    expect(screen.getByText("Far Farid")).toBeInTheDocument();
  });

  it("lets the recruiter opt OUT of unknowns, and starts opted in", async () => {
    setup(25);
    await openDialog(/Within 25 km of Gurugram/);
    const box = await screen.findByRole("checkbox", { name: /Also show the 2 whose distance is unknown/ });
    expect(box).toBeChecked();          // inclusive by default — never the system's choice
    await userEvent.click(box);
    // the apply button counts what it will leave on screen
    await userEvent.click(screen.getByRole("button", { name: "Show only these 1" }));

    expect(screen.getByText("Near Nita")).toBeInTheDocument();
    expect(screen.queryByText("Unknown Uma")).not.toBeInTheDocument();
    expect(screen.queryByText("Silent Sam")).not.toBeInTheDocument();
    expect(screen.queryByText("Far Farid")).not.toBeInTheDocument();
  });

  it("counts what the button will show, including the unknowns when asked", async () => {
    // Reported 2026-09-10: filtering 50 km from Gurugram found 16 in range plus 2
    // whose distance could not be worked out, and the panel said 16 while its own
    // button said "Show only these 18". The hero is what the button will DO — and a
    // total that includes unknowns cannot be described as "within N km".
    setup(25);
    await openDialog(/Within 25 km of Gurugram/);
    const dialog = await screen.findByRole("dialog", { name: /Distance from the job location/i });
    expect(within(dialog).getByText("3")).toBeInTheDocument();          // 1 near + 2 unknown
    // Terse since 2026-09-11 (ask 4) — the same facts, none of the explanation.
    expect(within(dialog).getByText(/of 4 — 1 within 25 km of Gurugram, 2 unknown/))
      .toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /Show only these 3/ })).toBeInTheDocument();
  });

  it("counts only those in range once the unknowns are unticked", async () => {
    setup(25);
    await openDialog(/Within 25 km of Gurugram/);
    const dialog = await screen.findByRole("dialog", { name: /Distance from the job location/i });
    await userEvent.click(within(dialog).getByRole("checkbox"));
    expect(within(dialog).getByText("1")).toBeInTheDocument();
    expect(within(dialog).getByText(/of 4 within 25 km of Gurugram/))
      .toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /Show only these 1/ })).toBeInTheDocument();
  });

  it("never offers a box to type the place into — it comes from the JD", async () => {
    setup(25);
    await openDialog(/Within 25 km of Gurugram/);
    expect(await screen.findByRole("checkbox")).toBeInTheDocument();  // panel is open
    expect(screen.queryByLabelText(/Job location place name/)).not.toBeInTheDocument();
    // there is exactly ONE control for the lens — no separate gear beside the pill
    expect(screen.queryByRole("button", { name: /Change the distance|Set the distance/ })).toBeNull();
  });

  it("with no distance set, the pill invites setting one and nothing can be applied yet", async () => {
    setup(null);
    await openDialog(/Set distance from Gurugram/);
    expect(await screen.findByText(/Set a distance from Gurugram/))
      .toBeInTheDocument();
    // the apply button waits for a distance; the list is untouched
    expect(screen.getByRole("button", { name: "Set a distance first" })).toBeDisabled();
    for (const name of ALL) expect(screen.getByText(name)).toBeInTheDocument();
  });

  it("Escape closes the dialog and leaves the selection underneath alone", async () => {
    setup(25);
    const row = (await screen.findByText("Near Nita")).closest(".rev-card") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Select" }));
    await openDialog(/Within 25 km of Gurugram/);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    // the mark survived — Escape used to fall through to "clear the marks"
    expect(screen.getByRole("region", { name: "Mark candidates" })).toHaveTextContent("1 marked");
  });

  it("Show everyone turns the filter off from inside the dialog", async () => {
    setup(25);
    await openDialog(/Within 25 km of Gurugram/);
    await userEvent.click(screen.getByRole("button", { name: "Show only these 3" }));
    expect(screen.queryByText("Far Farid")).not.toBeInTheDocument();
    await openDialog(/Within 25 km of Gurugram/);
    await userEvent.click(screen.getByRole("button", { name: "Show everyone" }));
    expect(screen.getByText("Far Farid")).toBeInTheDocument();
  });
});

// A requisition for a place we cannot locate (2026-09-11).
//
// The pilot raised one for BUDAPEST and dropped in résumés that plainly state Gurugram.
// Every row read "distance is unknown", which reads as a broken filter and sends the
// reader to the résumés — but the unresolvable side was the JOB's own city, missing
// from the places table, so no pair could ever resolve. The gap is ours, it is the same
// for everyone in the list, and it is said ONCE.
describe("a job location we cannot place", () => {
  function setupUnplaceable() {
    const cards = [NEAR, FAR, UNPLACEABLE, SILENT].map((c) => ({ ...c, location_within: null }));
    const payload = {
      ready: true, req_id: REQ_ID, title: "Financial Analyst",
      location_display: "Hungary", cards,
      counts: { total: 4, strong: 1, possible: 0, weak: 1, gate_not_met: 0 },
      decision_counts: { forward: 0, discuss: 0, not: 0 },
      work_location: { place: "Budapest", radius_km: 25, max_km: 100,
                       summary: "Within 25 km from “Budapest”", place_known: false },
    } as unknown as ReviewResponse;
    mockApi({
      [`/api/roles/${REQ_ID}`]: roleFixture(),
      [`/api/roles/${REQ_ID}/review`]: payload,
      [`/api/roles/${REQ_ID}/batch`]: { count: 4, files: [], folder: "x" },
      [`/api/roles/${REQ_ID}/screen/preflight`]: {
        will_run: false, reason: "nothing_changed",
        changed: { checklist: false, rules: false, location: false, scoring_model: false, preferences: false },
        details: [], stale: null, files: [],
        counts: { total: 4, new: 0, updated: 0, cached: 4, removed: 0 },
      },
    });
    renderPage(
      <Routes>
        {workspaceRoutes()}
        <Route path="/reqs" element={<div>list</div>} />
      </Routes>,
      { route: `/reqs/${REQ_ID}/review` }
    );
  }

  it("names the gap as ours, and offers no filter that could only hide people for it", async () => {
    setupUnplaceable();
    await screen.findByText("Near Nita");

    // The pill says what is true instead of inviting a distance nobody can use.
    const dialog = await openDialog(/No distances for Budapest/);
    expect(within(dialog).getByText(/We don't have Budapest in the location list/))
      .toBeInTheDocument();

    // Applying would hide EVERYONE (every distance is unknown), for a missing row in
    // our own table. So there is nothing to apply, and nothing to set.
    expect(within(dialog).queryByRole("button", { name: /Show only these/ })).toBeNull();
    expect(within(dialog).queryByText(/Also show the/)).toBeNull();
    expect(within(dialog).queryByRole("slider")).toBeNull();

    // Everyone is still listed, which is the invariant this whole lens rests on.
    for (const name of ALL) expect(screen.getByText(name)).toBeInTheDocument();
  });
});
