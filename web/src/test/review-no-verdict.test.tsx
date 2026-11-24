// A viewer WITHOUT verdict_view. Since 2026-10-21 every role that opens candidates
// holds it, but a deployment can narrow it again in rbac.yaml (HR-only, as from
// 2026-10-14) — and then the server sends review cards and drawer payloads with no
// band, score, evidence or score math, ordered by decision then name. The UI renders by PRESENCE: no empty badge, no "undefined / 100",
// no band filters or "Match rank" sort that would filter on something not there, no
// Evidence or Score section, and no Ask-Claude button (the bundle IS the verdict).
import { describe, it, expect } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { DEFAULT_ME, mockApi, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import type { CandidateDetail, Me, ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";

const LEGAL: Me = {
  ...DEFAULT_ME, user: "legal@example.com", role: "Legal",
  capabilities: ["rules_view", "rules_edit", "scoring_edit", "changes_approve", "runs_audit", "reqs_read"],
};

// What the server sends a non-HR viewer: identity, decision, note, comments — no verdict.
const CARDS: ReviewCard[] = [
  { rank: 1, candidate_id: "c-anita", name: "Anita Rao", decided: "forward",
    decided_reason: "Strong treasury interview", decided_ts: "2026-10-14T09:00:00Z",
    decided_by: "ta@example.com", comments: 1 },
  { rank: 2, candidate_id: "c-upkaar", name: "Upkaar Roach", decided: null,
    decided_reason: null, decided_ts: null },
];

const REVIEW: ReviewResponse = {
  ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "run-1",
  location_display: "India", meta: { screened: 2, rubric: "v1" },
  counts: { total: 2 },
  decision_counts: { forward: 1, discuss: 0, not: 0, awaiting: 1 },
  cards: CARDS, batch_quality: null,
};

const DETAIL: CandidateDetail = {
  candidate_id: "c-anita", name: "Anita Rao",
  decision: { action: "forward", reason: "Strong treasury interview", reviewer: "ta@example.com" },
  comments: [], resume_media_type: "pdf",
};

function setup(me: Me = LEGAL) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
    [`/api/roles/${REQ_ID}/review`]: REVIEW,
    [`/api/roles/${REQ_ID}/review/c-anita`]: DETAIL,
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/review`, me }
  );
  return api;
}

describe("Review without the AI verdict", () => {
  it("lists the candidates with the decision and note, and nothing score-shaped", async () => {
    setup();
    const row = (await screen.findByText("Anita Rao")).closest(".card-hover") as HTMLElement;
    expect(within(row).getByText(/Strong treasury interview/)).toBeInTheDocument();
    expect(within(row).queryByText("/ 100")).not.toBeInTheDocument();
    expect(row.querySelector(".rank-badge")).toBeNull();
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
    // filters that would filter on a band nobody here can see are not offered
    expect(screen.queryByRole("button", { name: /All bands/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sort: By decision/ })).toBeInTheDocument();
  });

  it("has no Ask-Claude button — the bundle is the verdict", async () => {
    setup();
    await screen.findByText("Anita Rao");
    expect(screen.queryByRole("button", { name: /Ask Claude/ })).not.toBeInTheDocument();
  });

  it("opens the drawer on the résumé, with no Evidence or Score section", async () => {
    setup();
    await userEvent.click(await screen.findByText("Anita Rao"));
    const tabs = await screen.findByRole("tablist", { name: "Candidate sections" });
    const names = within(tabs).getAllByRole("tab").map((t) => t.textContent ?? "");
    expect(names.some((n) => n.startsWith("Résumé"))).toBe(true);
    expect(names.some((n) => n.startsWith("Notes"))).toBe(true);
    expect(names.some((n) => n.startsWith("Evidence"))).toBe(false);
    expect(names.some((n) => n.startsWith("Score"))).toBe(false);
    expect(within(tabs).getByRole("tab", { name: /Résumé/ })).toHaveAttribute("aria-selected", "true");
  });
});
