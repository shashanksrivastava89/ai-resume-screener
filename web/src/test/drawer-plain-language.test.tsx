/** 2026-09-11 pilot asks 1, 2, 3 and 5 — the drawer in words a recruiter uses.
 *
 *  1. The résumé's "Open in a new tab" bar must survive a scroll.
 *  2. ✓ and ~ must say what they mean; "~" read as "no" turns a part-way match into
 *     a rejection.
 *  3. The dated positions behind a length-of-experience requirement are a journey,
 *     shown for every candidate — even one whose résumé states the total in words.
 *  5. "Covers 96% of weighted requirements (base 96). 2 evidenced signal
 *     adjustment(s), net +6.0." said nothing to the person reading it.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture } from "./fixtures";
import { ExperienceJourney, ScoreLine, periodLabel } from "../pages/ReviewDrawer";
// The drawer's own source, for the one guarantee that is about what the file SAYS
// rather than what it renders (see raw-modules.d.ts).
import drawerSource from "../pages/ReviewDrawer.tsx?raw";
import type { CandidateDetail, ReqSpan, ReviewCard, ReviewResponse, ScoreMath } from "../types";

// Newest first — the SERVER orders them (review._span_view), so the fixture arrives the
// way the drawer receives it (2026-09-11).
const SPANS: ReqSpan[] = [
  { quote: "[CANDIDATE], Acme — 06/2022 – Present", title: "Senior Accounts Payable Analyst", start: "2022-06", end: "present", months: 40, ongoing: true },
  { quote: "Accounts Payable Analyst, Globex — 11/2019 – 06/2022", title: "Accounts Payable Analyst", start: "2019-11", end: "2022-06", months: 32, ongoing: false },
];

describe("the experience journey (ask 3)", () => {
  it("labels a period the way a résumé prints one", () => {
    expect(periodLabel(SPANS[1])).toBe("Nov 2019 – Jun 2022");
    expect(periodLabel(SPANS[0])).toBe("Jun 2022 – Present");
    // A résumé that prints only years is left as it is, never invented into a month.
    expect(periodLabel({ quote: "", start: "2019", end: "2021" })).toBe("2019 – 2021");
    expect(periodLabel({ quote: "", start: "whenever", end: "later" })).toBe("whenever – later");
  });

  it("shows every role with its own length, and the total it was counted into", () => {
    render(<ExperienceJourney spans={SPANS} months={72} />);
    expect(screen.getByText("Counted toward this requirement")).toBeInTheDocument();
    expect(screen.getByText("6 yr")).toBeInTheDocument();          // the merged total
    expect(screen.getByText("2 roles")).toBeInTheDocument();
    expect(screen.getByText("Nov 2019 – Jun 2022")).toBeInTheDocument();
    expect(screen.getByText("2 yr 8 mo")).toBeInTheDocument();     // this role alone
    expect(screen.getByText(/Globex/)).toBeInTheDocument();        // the line it was read off
  });

  it("says so when concurrent roles were counted once, rather than looking wrong", () => {
    // 32 + 40 = 72 months of employment but 60 months of a working life.
    render(<ExperienceJourney spans={SPANS} months={60} />);
    expect(screen.getByText(/counted once/)).toBeInTheDocument();
  });

  it("does not claim a length it could not work out", () => {
    render(<ExperienceJourney spans={[{ quote: "Analyst, Globex", start: "?", end: "?", months: null }]} months={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText(/counted once/)).toBeNull();
  });

  // 2026-09-11 pilot: the journey headed a row "[CANDIDATE], McKinsey…" for someone who
  // was a Finance Process Lead. The quoted employment line carries the NAME (redacted
  // before the model ever sees it), so the title has to come as its own field.
  it("names the role, not the line the résumé happened to print", () => {
    render(<ExperienceJourney spans={SPANS} months={72} />);
    expect(screen.getByText("Senior Accounts Payable Analyst")).toBeInTheDocument();
    expect(screen.getByText("Accounts Payable Analyst")).toBeInTheDocument();
  });

  it("still draws a run screened before titles were asked for", () => {
    render(<ExperienceJourney spans={[{ quote: "Analyst, Globex — 11/2019 – 06/2022", start: "2019-11", end: "2022-06", months: 32 }]} months={32} />);
    expect(screen.getByText("Nov 2019 – Jun 2022")).toBeInTheDocument();
    // twice over: the row's own length, and the total beside the headline
    expect(screen.getAllByText("2 yr 8 mo")).toHaveLength(2);
  });

  it("folds away on a click and keeps the answer", async () => {
    render(<ExperienceJourney spans={SPANS} months={72} />);
    const head = screen.getByRole("button", { name: /Counted toward this requirement/ });
    expect(head).toHaveAttribute("aria-expanded", "true");   // the working shows by default
    await userEvent.click(head);
    expect(head).toHaveAttribute("aria-expanded", "false");
    // the headline survives — the total is the thing a reviewer keeps
    expect(screen.getByText("Counted toward this requirement")).toBeInTheDocument();
    expect(screen.getByText("6 yr")).toBeInTheDocument();
    expect(screen.queryByText("Nov 2019 – Jun 2022")).toBeNull();
    await userEvent.click(head);
    expect(screen.getByText("Nov 2019 – Jun 2022")).toBeInTheDocument();
  });

  // 2026-09-25 (p38): "Minimum 7 years managing AP regional projects" counted two of a
  // ten-year résumé's five jobs, and the drawer showed only those two under "Counted
  // from the résumé" — 2 yr 1 mo read as the candidate's whole career.
  const WITH_LEFT_OUT: ReqSpan[] = [
    ...SPANS,
    { quote: "Retail Sales Associate — 01/2018 – 10/2019", title: "Retail Sales Associate", start: "2018-01", end: "2019-10", months: 22, ongoing: false,
      counted: false, why_not: "sales role, not payables" },
  ];

  it("shows the jobs a requirement left out, and the total they belong to", () => {
    render(<ExperienceJourney spans={WITH_LEFT_OUT} months={72} monthsTotal={94} />);
    expect(screen.getByText("Counted toward this requirement")).toBeInTheDocument();
    expect(screen.getByText("2 of 3 roles")).toBeInTheDocument();
    expect(screen.getByText("All dated roles on the résumé: 7 yr 10 mo")).toBeInTheDocument();
    expect(screen.getByText("Not counted — sales role, not payables")).toBeInTheDocument();
    // the left-out job keeps its place on the timeline, muted
    expect(screen.getByText("Retail Sales Associate").closest("li")).toHaveClass("not-counted");
  });

  it("keeps the all-jobs total when the working is folded away", async () => {
    render(<ExperienceJourney spans={WITH_LEFT_OUT} months={72} monthsTotal={94} />);
    await userEvent.click(screen.getByRole("button", { name: /Counted toward this requirement/ }));
    expect(screen.queryByText(/Not counted/)).toBeNull();
    expect(screen.getByText("All dated roles on the résumé: 7 yr 10 mo")).toBeInTheDocument();
  });

  it("adds no second total to a run screened before p38", () => {
    render(<ExperienceJourney spans={SPANS} months={72} />);
    expect(screen.queryByText(/All dated roles/)).toBeNull();
    expect(screen.queryByText(/Not counted/)).toBeNull();
  });
});

const MATH: ScoreMath = {
  as_of: { scoring_model_version: 1, checklist: "v2", approximate: false },
  base: { total: 96, explainer: "", lines: [] },
  adjustments: { net: 6, cap_total: 8, cap_note: "", lines: [{ label: "Regulated exposure", points: 6, flagged: false }], suspended: [], enforcement_notes: [] },
  preferences: { net: 0, per_item_cap: 2, cap_note: "", lines: [] },
  total: { before_limits: 102, final: 100, clamped: "high" },
  band: { result: "Strong", label: "Strong match", strong_at: 88, possible_at: 60,
          capped_by_must_have: null, cap_note: null, gate_note: null, clamp_min: 0, clamp_max: 100 },
  confidence: { level: "high", explainer: "" },
};

describe("how the score adds up (ask 5)", () => {
  it("names the two parts a recruiter can act on, and each one leads somewhere", async () => {
    const seen: string[] = [];
    render(<ScoreLine math={MATH} verdict="unused"
                      onChecklist={() => seen.push("checklist")}
                      onAdjustments={() => seen.push("adjustments")} />);
    const checklist = screen.getByRole("button", { name: "96% of the checklist" });
    const ai = screen.getByRole("button", { name: /[+]6 by the AI/ });
    expect(screen.getByText("100")).toBeInTheDocument();
    // 96 + 6 is 102; the score stops at 100 and says so rather than looking wrong.
    expect(screen.getByText("capped at 100")).toBeInTheDocument();
    checklist.click();
    ai.click();
    expect(seen).toEqual(["checklist", "adjustments"]);
    // the engine's own sentence is nowhere near the reader
    expect(screen.queryByText(/weighted requirements/)).toBeNull();
  });

  it("falls back to the stored sentence when a run is too old to carry the trace", () => {
    render(<ScoreLine math={null} verdict="Covers 80% of weighted requirements (base 80)."
                      onChecklist={() => {}} onAdjustments={() => {}} />);
    expect(screen.getByText(/Covers 80% of weighted requirements/)).toBeInTheDocument();
  });

  it("shows a set-aside adjustment as its own fact — it is shown but does not count", () => {
    const math: ScoreMath = { ...MATH,
      adjustments: { ...MATH.adjustments, suspended: [{ label: "Domain transfer", points: 2, note: "" }] } };
    render(<ScoreLine math={math} verdict="" onChecklist={() => {}} onAdjustments={() => {}} />);
    expect(screen.getByRole("button", { name: "1 set aside" })).toBeInTheDocument();
  });

  it("leads with the gate when one was not met — the number is not the story then", () => {
    const math: ScoreMath = { ...MATH,
      band: { ...MATH.band, gate_note: "A hard requirement wasn't met, so the rating reads \"Gate not met\" whatever the number says." } };
    render(<ScoreLine math={math} verdict="" onChecklist={() => {}} onAdjustments={() => {}} />);
    expect(screen.getByText(/Gate not met/)).toBeInTheDocument();
  });
});

describe("the résumé tab (ask 1)", () => {
  it("keeps the way out on screen: the pane scrolls and the bar is pinned to its top", async () => {
    setupDrawer();
    await userEvent.click(await screen.findByText("Vaani Daiken"));
    await userEvent.click(screen.getByRole("tab", { name: /Résumé/ }));

    const bar = (await screen.findByText("Original résumé")).closest(".resume-bar") as HTMLElement;
    // jsdom computes no sticky, so what is asserted is that the app ASKS for it —
    // which is what a later edit would drop. The behaviour itself was measured in a
    // real browser at 1280×620: pane scrollHeight 455 vs clientHeight 172, and at the
    // bottom of that scroll the bar's top still equalled the pane's.
    expect(bar.style.position).toBe("sticky");
    expect(bar.style.top).toBe("0px");
    expect(within(bar).getByRole("link", { name: /Open in a new tab/ })).toBeInTheDocument();

    // and the pane scrolls instead of clipping what will not fit
    expect((bar.parentElement as HTMLElement).style.overflowY).toBe("auto");
  });
});

describe("what ✓ and ~ mean (ask 2)", () => {
  it("spells out every match level on hover, in a reviewer's words", async () => {
    const { MATCH_MEANING } = await import("../pages/ReviewDrawer");
    expect(MATCH_MEANING.partial).toMatch(/Partly met/);
    expect(MATCH_MEANING.full).toMatch(/^Met/);
    expect(MATCH_MEANING.none).toMatch(/Not found/);
    // "~" is the one nobody reads correctly on its own, so it says whose call it is.
    expect(MATCH_MEANING.partial).toMatch(/Your call/);
  });
});

const REQ_ID = "financial-analyst";

const DETAIL: CandidateDetail = {
  candidate_id: "c1", name: "Vaani Daiken", band: "Strong", band_label: "Strong match",
  score: 100, confidence: "high", sub: "Covers 96% of the checklist · confidence high",
  verdict: "Covers 96% of weighted requirements (base 96). 2 evidenced signal adjustment(s), net +6.0.",
  flag: false, flag_note: "", gate_warn: "", strengths: [], watchouts: [],
  req_summary: "1 of 2 met",
  reqs: [
    { label: "At least 5 years in payables", match: "partial", glyph: "~", must: true,
      gate: false, evidence: "5+ years of experience in accounts payable", note: "",
      spans: SPANS, months: 72 },
    { label: "SAP", match: "full", glyph: "✓", must: false, gate: false,
      evidence: "Processed vendor invoices in SAP", note: "" },
  ],
  notes: [], decision: null, resume_media_type: "pdf", score_math: MATH,
};

function setupDrawer() {
  const card: ReviewCard = {
    rank: 1, candidate_id: "c1", name: "Vaani Daiken", band: "Strong",
    band_label: "Strong match", score: 100, confidence: "high", flag: false,
    chips: [], gates: { total: 0, failed: [] }, must_have_gaps: [],
    decided: null, decided_reason: null, decided_ts: null,
  };
  mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/review`]: {
      ready: true, req_id: REQ_ID, title: "Financial Analyst", run_id: "r1",
      location_display: "India", meta: { screened: 1, rubric: "v2" },
      counts: { strong: 1, possible: 0, not_a_fit: 0, needs_look: 0, gates_passed: 1, gate_not_met: 0, total: 1 },
      decision_counts: { forward: 0, discuss: 0, not: 0, awaiting: 1 },
      cards: [card],
    } satisfies ReviewResponse,
    [`/api/roles/${REQ_ID}/review/c1`]: DETAIL,
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/review` }
  );
}

describe("nothing is explained twice", () => {
  it("keeps the score-adjustments explainer on the info dot only (ask 4)", () => {
    const uses = drawerSource.split("ADJUSTMENTS_EXPLAINER").length - 1;
    // once as the constant, once on the InfoDot — never also as a paragraph above
    // the list it explains.
    expect(uses).toBe(2);
  });
});

describe("the drawer's checklist, opened for real", () => {
  it("says what ~ means and draws the journey inside the row it belongs to", async () => {
    setupDrawer();
    await userEvent.click(await screen.findByText("Vaani Daiken"));

    // ask 2: the glyph carries its meaning for hover AND for a screen reader
    const glyph = await screen.findByLabelText(/Partly met/);
    expect(glyph).toHaveTextContent("~");
    expect(screen.getByLabelText(/^Met —/)).toHaveTextContent("✓");

    // ask 3: the journey sits under the requirement it settles, not in a corner
    const row = glyph.closest("[id^='req-row-']") as HTMLElement;
    expect(within(row).getByText("Counted toward this requirement")).toBeInTheDocument();
    expect(within(row).getByText("Nov 2019 – Jun 2022")).toBeInTheDocument();
    expect(within(row).getByText("6 yr")).toBeInTheDocument();

    // ask 5: the score reads as its parts, not as engine vocabulary
    // exact name: the drawer header ALSO carries "Covers 96% of the checklist …"
    expect(screen.getByRole("button", { name: "96% of the checklist" })).toBeInTheDocument();
    expect(screen.queryByText(/evidenced signal adjustment/)).toBeNull();
  });
});
