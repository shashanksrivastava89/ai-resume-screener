// Review: marking is WORK, and it is kept (2026-09-06 item 12; reworked 2026-09-07
// item 14).
//
// Marking a candidate records NOTHING by itself — it gathers candidates so that one
// decision covers them all. The bar says exactly that; a decision on a marked card
// applies to every marked card and then spends the marks; the Shortlist CTA carries
// the number it will actually carry forward and warns when marked candidates were
// never decided on. Marks + filters survive leaving the tab (sessionStorage, per
// requisition). The "N of M shown" readout (item 6) is pinned here too, since it is
// what a recruiter watches while narrowing the list.
import { describe, it, expect, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import { loadReviewState, saveReviewState } from "../reviewState";
import type { ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";
const BATCH = `/api/roles/${REQ_ID}/decisions/batch`;
const ONE = `/api/roles/${REQ_ID}/decisions`;

function card(over: Partial<ReviewCard>): ReviewCard {
  return {
    rank: 1, candidate_id: "c1", name: "Anita Rao", band: "Strong",
    band_label: "Strong match", score: 82, confidence: "high", flag: false,
    chips: [], gates: { total: 0, failed: [] }, must_have_gaps: [],
    decided: null, decided_reason: null, decided_ts: null, ...over,
  };
}
const CARDS = [
  card({}),
  card({ rank: 2, candidate_id: "c2", name: "Vikram Shah", score: 74, band: "Possible", band_label: "Possible match" }),
  card({ rank: 3, candidate_id: "c3", name: "Meera Roach", score: 61, band: "Possible", band_label: "Possible match" }),
];

function setup(cards: ReviewCard[] = CARDS) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [ONE]: { ok: true, action: "discuss", decision: { action: "discuss", reason: "" } },
    [BATCH]: { ok: true, applied: 2, decisions: {} },
    [`/api/roles/${REQ_ID}/review`]: {
      ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "run-1",
      location_display: "India", meta: { screened: cards.length, rubric: "v1" },
      counts: { strong: 1, possible: 2, not_a_fit: 0, needs_look: 0, gates_passed: 3, gate_not_met: 0, total: cards.length },
      decision_counts: {
        forward: cards.filter((c) => c.decided === "forward").length,
        discuss: 0, not: 0, awaiting: cards.filter((c) => !c.decided).length,
      },
      cards,
    } satisfies ReviewResponse,
  });
  const view = renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/review` }
  );
  return { api, view };
}

const rowOf = (name: string) => screen.getByText(name).closest(".rev-card") as HTMLElement;
const tick = (name: string) => userEvent.click(within(rowOf(name)).getByRole("button", { name: "Select" }));
// One row for marking, always present (it holds "Mark all"); it turns green and
// grows the rest only once something is marked.
const bar = () => screen.getByRole("region", { name: "Mark candidates" });
const marked = () => bar().className.includes("is-on");

beforeEach(() => { sessionStorage.clear(); });

describe("Review marking", () => {
  it("says marking records nothing and scopes the next decision", async () => {
    setup();
    await screen.findByText("Anita Rao");
    // the row is there to offer "Mark all", but nothing is marked yet
    expect(marked()).toBe(false);
    expect(bar()).toHaveTextContent("Mark all 3 — to decide on them together");
    expect(screen.getByRole("status", { name: "3 candidates" })).toBeInTheDocument();

    await tick("Anita Rao");
    await tick("Vikram Shah");
    expect(bar()).toHaveTextContent("2 marked");
    expect(bar()).toHaveTextContent("Your next decision applies to all 2");
    expect(bar()).toHaveTextContent("marking records nothing on its own");
    // The decision buttons are NOT in the bar any more — they live on the cards,
    // where a decision means the same thing whether one is marked or fourteen. And
    // "Clear decision" is gone: it was the button that lost someone their work.
    expect(within(bar()).queryByRole("button", { name: "Move forward" })).toBeNull();
    expect(within(bar()).queryByRole("button", { name: /Clear decision/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Shortlist →" })).toBeDisabled();
  });

  it("one decision on a marked card applies to every marked card, then spends the marks", async () => {
    const { api } = setup();
    await screen.findByText("Anita Rao");
    await tick("Anita Rao");
    await tick("Vikram Shah");

    await userEvent.click(within(rowOf("Anita Rao")).getByRole("button", { name: "Move forward" }));
    await waitFor(() => expect(api.callsTo(BATCH)).toHaveLength(1));
    expect((api.callsTo(BATCH)[0].body as { candidate_ids: string[] }).candidate_ids.sort())
      .toEqual(["c1", "c2"]);
    // the marks are spent, so the NEXT decision is a single one again
    await waitFor(() => expect(marked()).toBe(false));
    await userEvent.click(within(rowOf("Meera Roach")).getByRole("button", { name: "Discuss" }));
    await waitFor(() => expect(api.callsTo(ONE)).toHaveLength(1));
    expect(api.callsTo(BATCH)).toHaveLength(1); // the group call didn't fire again
  });

  it("deciding on an UNMARKED card while marks exist touches only that card", async () => {
    const { api } = setup();
    await screen.findByText("Anita Rao");
    await tick("Anita Rao");
    await userEvent.click(within(rowOf("Meera Roach")).getByRole("button", { name: "Discuss" }));
    await waitFor(() => expect(api.callsTo(ONE)).toHaveLength(1));
    expect(api.callsTo(BATCH)).toHaveLength(0);
    expect(bar()).toHaveTextContent("1 marked"); // the mark is untouched
  });

  it("the Shortlist CTA carries the decided count and warns about marks with no decision", async () => {
    setup([{ ...CARDS[0], decided: "forward" }, CARDS[1], CARDS[2]]);
    await screen.findByText("Anita Rao");
    const cta = await screen.findByRole("button", { name: /Shortlist 1/ });
    await tick("Vikram Shah");           // marked, never decided
    await userEvent.click(cta);
    const dialog = await screen.findByRole("dialog", { name: /1 marked candidate without a decision/ });
    expect(dialog).toHaveTextContent("doesn't record anything by itself");
    expect(dialog).toHaveTextContent("The shortlist will show the 1 you have decided on");
    // backing out shows exactly those marks instead of leaving
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Show everyone" })).toBeInTheDocument();
    expect(screen.queryByText("Anita Rao")).toBeNull();
  });

  it("View marked narrows the list to the marked rows and the readout follows", async () => {
    setup();
    await screen.findByText("Anita Rao");
    await tick("Meera Roach");
    await userEvent.click(screen.getByRole("button", { name: "View marked" }));
    expect(screen.getByText("Meera Roach")).toBeInTheDocument();
    expect(screen.queryByText("Anita Rao")).toBeNull();
    expect(screen.getByRole("status", { name: "1 of 3 candidates shown" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show everyone" }));
    expect(screen.getByText("Anita Rao")).toBeInTheDocument();
    // a band filter narrows it the same way, and Clear puts everything back
    await userEvent.click(screen.getByRole("button", { name: /^Strong/ }));
    expect(screen.getByRole("status", { name: "1 of 3 candidates shown" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByRole("status", { name: "3 candidates" })).toBeInTheDocument();

    await userEvent.click(within(rowOf("Meera Roach")).getByRole("button", { name: "Deselect" }));
    expect(marked()).toBe(false);
  });

  it("remembers the marks and the filters when the tab is left and reopened", async () => {
    const { view } = setup();
    await screen.findByText("Anita Rao");
    await tick("Vikram Shah");
    await userEvent.click(screen.getByRole("button", { name: /^Possible/ }));
    expect(loadReviewState(REQ_ID)).toMatchObject({ sel: ["c2"], band: "Possible", tab: "all" });

    view.unmount(); // leave the tab (or reload the browser)
    setup();
    await screen.findByText("Vikram Shah");
    expect(bar()).toHaveTextContent("1 marked");
    expect(screen.queryByText("Anita Rao")).toBeNull(); // the Possible filter came back too
    expect(within(rowOf("Vikram Shah")).getByRole("button", { name: "Deselect" })).toBeInTheDocument();
  });

  it("drops remembered ids that no longer exist (a re-screen changed the run)", async () => {
    saveReviewState(REQ_ID, { sel: ["gone", "c3"], tab: "all", band: "all", gate: "all", query: "", sortMode: "rank" });
    setup();
    await screen.findByText("Meera Roach");
    // the stale id is pruned in an effect once the list is in: wait for it rather than
    // read the one render before (a slower CI runner sees that render)
    await waitFor(() => expect(bar()).toHaveTextContent("1 marked"));
    await waitFor(() => expect(loadReviewState(REQ_ID)?.sel).toEqual(["c3"]));
  });
});

describe("Band filters can't offer an impossible view (item 15)", () => {
  it("disables Strong and Possible while the gate filter shows only gate failures", async () => {
    const gated = [
      card({ candidate_id: "g1", name: "Gate Fail", band: "Not a fit", band_label: "Gate not met",
             gates: { total: 1, failed: ["CA qualification"], partial: [] } }),
      card({ rank: 2, candidate_id: "g2", name: "Clean Pass", gates: { total: 1, failed: [], partial: [] } }),
    ];
    setup(gated);
    await screen.findByText("Gate Fail");
    // counts ride on the pills, so "why is this empty?" is answered before the click
    expect(screen.getByRole("button", { name: "Strong · 1" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: /Gate not met/ }));
    const strong = screen.getByRole("button", { name: "Strong · 0" });
    expect(strong).toBeDisabled();
    expect(strong.getAttribute("title")).toMatch(/never Strong or Possible/);
    // the pills stay in place rather than vanishing — a disappearing pill reflows
    // the row, which is the dancing this pass is removing
    expect(screen.getByRole("button", { name: "Possible · 0" })).toBeInTheDocument();
  });
});
