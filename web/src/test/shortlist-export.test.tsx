// The Shortlist export: a server-built CSV (the "Resume file" column matches the
// ZIP layout) and resume downloads packed into one ZIP, foldered by decision.
//
// 2026-09-07 (item 16): the checkboxes are gone. Picking resumes to download was a
// second selection model sitting on top of the decisions people had already made, so
// each bucket downloads ITSELF and one button takes everyone. A row is a link back
// into the candidate drawer, because the shortlist is a second pass over the
// evidence, not a list of names.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, resetApiMock, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import type { ReviewCard, ReviewResponse } from "../types";

const REQ_ID = "financial-analyst";

function card(o: Partial<ReviewCard>): ReviewCard {
  return {
    rank: 1, candidate_id: "c1", name: "Anita Rao", band: "Strong", band_label: "Strong match",
    score: 82, confidence: "high", flag: false,
    chips: [
      { text: "GST/TDS filing depth", kind: "strength" },
      { text: "Missing: Ind AS", kind: "watchout" },
    ],
    gates: { total: 1, failed: [] }, must_have_gaps: [],
    decided: "forward", decided_reason: "Strong treasury depth",
    decided_ts: "2026-07-13T09:00:00+00:00", ...o,
  };
}

function review(): ReviewResponse {
  return {
    ready: true, req_id: REQ_ID, title: "Financial Analyst", req_code: "REQ-2041",
    run_id: "run-1", location_display: "India",
    counts: { strong: 1, possible: 1, not_a_fit: 0, needs_look: 0, gates_passed: 2, gate_not_met: 0, total: 2 },
    decision_counts: { forward: 1, discuss: 0, not: 0, awaiting: 1 },
    cards: [
      card({}),
      card({ rank: 2, candidate_id: "c2", name: "Vikram Lillis", score: 71, band: "Possible",
             band_label: "Possible match", decided: null, decided_reason: null, decided_ts: null }),
    ],
  };
}

// Capture anchor downloads + stub the object-URL plumbing jsdom lacks.
function trapDownloads() {
  const clicks: string[] = [];
  const origClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) { clicks.push(this.download); };
  const origCreate = URL.createObjectURL;
  const origRevoke = URL.revokeObjectURL;
  URL.createObjectURL = vi.fn(() => "blob:x") as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn();
  return {
    clicks,
    restore() {
      HTMLAnchorElement.prototype.click = origClick;
      URL.createObjectURL = origCreate;
      URL.revokeObjectURL = origRevoke;
    },
  };
}

function renderShortlist() {
  return renderPage(
    <Routes>{workspaceRoutes()}<Route path="/reqs" element={<div>list</div>} /></Routes>,
    { route: `/reqs/${REQ_ID}/shortlist` }
  );
}

describe("Shortlist export", () => {
  beforeEach(() => resetApiMock());
  afterEach(() => resetApiMock());

  it("Export (CSV) downloads from the server CSV endpoint", async () => {
    const dl = trapDownloads();
    try {
      const api = mockApi({
        [`/api/roles/${REQ_ID}`]: roleFixture(),
        [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
        [`/api/roles/${REQ_ID}/review`]: review(),
        [`/api/roles/${REQ_ID}/shortlist/export.csv`]:
          "iCIMS Req ID,Rank,Candidate,Resume file\nREQ-2041,1,Anita Rao,Move forward/Anita Rao.pdf\n",
      });
      renderShortlist();
      await userEvent.click(await screen.findByRole("button", { name: /Export \(CSV\)/ }));
      expect(api.callsTo(`/api/roles/${REQ_ID}/shortlist/export.csv`).length).toBe(1);
      expect(dl.clicks[0]).toBe("shortlist-REQ-2041.csv");
    } finally {
      dl.restore();
    }
  });

  it("offers no checkboxes - the decision buckets already are the selection", async () => {
    mockApi({
      [`/api/roles/${REQ_ID}`]: roleFixture(),
      [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
      [`/api/roles/${REQ_ID}/review`]: review(),
    });
    renderShortlist();
    await screen.findByText("Anita Rao");
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(await screen.findByRole("button", { name: /Download all \(2\)/ })).toBeEnabled();
  });

  it("Download all posts every candidate id, across buckets", async () => {
    const dl = trapDownloads();
    try {
      const api = mockApi({
        [`/api/roles/${REQ_ID}`]: roleFixture(),
        [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
        [`/api/roles/${REQ_ID}/review`]: review(),
        [`/api/roles/${REQ_ID}/shortlist/download`]: "PKzip-bytes",
      });
      renderShortlist();
      await userEvent.click(await screen.findByRole("button", { name: /Download all \(2\)/ }));
      const posts = api.callsTo(`/api/roles/${REQ_ID}/shortlist/download`);
      expect(posts.length).toBe(1);
      expect(posts[0].method).toBe("POST");
      expect((posts[0].body as { candidate_ids: string[] }).candidate_ids.sort()).toEqual(["c1", "c2"]);
      expect(dl.clicks[0]).toBe("shortlist-REQ-2041.zip");
    } finally {
      dl.restore();
    }
  });

  it("a bucket downloads exactly its own candidates", async () => {
    const dl = trapDownloads();
    try {
      const api = mockApi({
        [`/api/roles/${REQ_ID}`]: roleFixture(),
        [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
        [`/api/roles/${REQ_ID}/review`]: review(),
        [`/api/roles/${REQ_ID}/shortlist/download`]: "PKzip-bytes",
      });
      renderShortlist();
      // "Move forward" holds c1; c2 is undecided and must not ride along
      const bucket = (await screen.findByText("Move forward")).closest("div")! as HTMLElement;
      await userEvent.click(within(bucket).getByRole("button", { name: /Download/ }));
      const posts = api.callsTo(`/api/roles/${REQ_ID}/shortlist/download`);
      expect(posts.length).toBe(1);
      expect(posts[0].body).toEqual({ candidate_ids: ["c1"] });
    } finally {
      dl.restore();
    }
  });

  it("a row leads back into that candidate's drawer, with a way back", async () => {
    mockApi({
      [`/api/roles/${REQ_ID}`]: roleFixture(),
      [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
      [`/api/roles/${REQ_ID}/review`]: review(),
      [`/api/roles/${REQ_ID}/review/c1`]: {
        candidate_id: "c1", name: "Anita Rao", band: "Strong", band_label: "Strong match",
        score: 82, confidence: "high", sub: "Covers 80%", verdict: "Solid treasury depth.",
        flag: false, flag_note: "", gate_warn: "", strengths: [], watchouts: [],
        req_summary: "4 of 5 met", reqs: [], notes: [], decision: null, resume_media_type: null,
      },
      [`/api/roles/${REQ_ID}/participants`]: { participants: [] },
      [`/api/roles/${REQ_ID}/candidates/c1/comments`]: { comments: [] },
    });
    renderShortlist();
    await userEvent.click(await screen.findByRole("button", { name: /Open Anita Rao/ }));
    // the Review tab, that candidate's drawer already open on the evidence
    expect(await screen.findByText("Solid treasury depth.")).toBeInTheDocument();
    // ...and one obvious way back to where the second pass started
    expect(screen.getByRole("button", { name: /Back to shortlist/ })).toBeInTheDocument();
  });
});
