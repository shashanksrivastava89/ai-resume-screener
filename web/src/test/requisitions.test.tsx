// P32: the requisitions list — dense rows with the mono req code and ONE green
// action, metric cards as clickable filters, the History view with outcome +
// retention, and the suggested-close banner firing close/snooze.
import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import Requisitions from "../pages/Requisitions";
import { mockApi, renderPage, DEFAULT_ME } from "./utils";
import { roleFixture } from "./fixtures";
import type { RoleDetail } from "../types";

// List cards are the detail payload minus nothing the list needs — reuse the
// fixture, stamped with the signed-in user so rows land in the default My view.
function card(overrides: Partial<RoleDetail> = {}): RoleDetail {
  return roleFixture({ created_by: DEFAULT_ME.user, ...overrides });
}

function rolesResponse(roles: RoleDetail[]) {
  const open = roles.filter((r) => r.lifecycle.status === "active");
  return {
    metrics: {
      active_roles: open.length,
      candidates_screened: 0,
      awaiting_review: 0,
      total: roles.length,
      need_action: open.filter((r) => r.stage === "screened" || r.stage === "in_review").length,
      awaiting_hm: 0,
      suggested_close: open.filter((r) => r.suggest_close.suggest).length,
      history_count: roles.length - open.length,
    },
    roles,
  };
}

function setup(roles: RoleDetail[], extraRoutes: Record<string, unknown> = {}) {
  const api = mockApi({ "/api/roles": rolesResponse(roles), ...extraRoutes });
  renderPage(
    <Routes>
      <Route path="/reqs" element={<Requisitions />} />
    </Routes>,
    { route: "/reqs" }
  );
  return api;
}

describe("Requisitions list", () => {
  it("renders a dense row: mono req code, law subline, title link — and NO per-row action button", async () => {
    setup([card()]); // stage screened
    const title = await screen.findByText("Financial Analyst");
    const row = title.closest("tr")!;
    const code = within(row).getByText("REQ-2041");
    expect(code.style.fontFamily).toContain("monospace");
    expect(within(row).getByText("DPDP Act 2023")).toBeInTheDocument();
    // 2026-09-06: one row = one click. No Action column, no green button per row —
    // the next step lives on the workspace Overview, which is where the row goes.
    expect(row.querySelectorAll(".btn-primary, .btn-magic")).toHaveLength(0);
    // the candidates count is a plain number now, not a second destination
    expect(within(row).queryByRole("link", { name: "4" })).toBeNull();
    expect(within(row).getByText("4")).toBeInTheDocument();
    // the title stays a real link (middle-click / copy address) to the Overview
    expect(within(row).getByRole("link", { name: "Financial Analyst" })).toHaveAttribute(
      "href", "/reqs/financial-analyst");
    // and the ROW itself is the click target
    expect(row.className).toContain("row-link");
    expect(row).toHaveAttribute("aria-label", "Open Financial Analyst");
  });

  it("clicking anywhere on a row opens the workspace overview", async () => {
    const api = mockApi({ "/api/roles": rolesResponse([card()]) });
    renderPage(
      <Routes>
        <Route path="/reqs" element={<Requisitions />} />
        <Route path="/reqs/:reqId" element={<div>workspace overview</div>} />
      </Routes>,
      { route: "/reqs" }
    );
    const row = (await screen.findByText("Financial Analyst")).closest("tr")!;
    // click the STAGE cell — not the title link — and land on the workspace
    await userEvent.click(within(row).getByText("Screened"));
    expect(await screen.findByText("workspace overview")).toBeInTheDocument();
    expect(api.calls.length).toBeGreaterThan(0);
  });

  it("the Added column is the default sort, newest first", async () => {
    setup([
      card({ req_id: "older", title: "Older Role", lifecycle: { ...card().lifecycle, created_at: "2026-06-01T08:00:00+00:00" } }),
      card({ req_id: "newer", title: "Newer Role", lifecycle: { ...card().lifecycle, created_at: "2026-07-20T08:00:00+00:00" } }),
    ]);
    await screen.findByText("Newer Role");
    expect(screen.getByRole("columnheader", { name: /Added/ })).toBeInTheDocument();
    const titles = screen.getAllByRole("link").map((a) => a.textContent).filter((t) => t?.endsWith("Role"));
    expect(titles.indexOf("Newer Role")).toBeLessThan(titles.indexOf("Older Role"));
  });

  it("shows the stale badge + refresh at any stage; refresh confirms then fires the re-screen", async () => {
    const api = setup(
      [card({
        stage: "complete", stage_label: "Complete", reviewed_count: 3,
        cta: { label: "View decisions", goto: "review", magic: false },
        staleness: { mode: "locked", changed: ["scoring_model"], summary: "The scoring model changed after these scores were produced." },
      })],
      { "/api/roles/financial-analyst/screen": { job_id: "j9" } }
    );
    const row = (await screen.findByText("Financial Analyst")).closest("tr")!;
    // The clock is an <IconClock> since 2026-08-20, not a "⏱" character, so the
    // chip's text is the label alone. Asserting the label (not the glyph) is also
    // the more durable assertion — it survives the next icon change.
    expect(within(row).getByText("Scores stale")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: /Refresh scores — Financial Analyst/ }));
    // the confirm spells out that decisions survive in the audit trail
    expect(await screen.findByText(/3 recorded decisions stay in the audit trail/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Refresh scores" }));
    await waitFor(() =>
      expect(api.callsTo("/api/roles/financial-analyst/screen")).toHaveLength(1));
  });

  it("shows the batch-quality chip under the candidates count", async () => {
    setup([card({
      batch_quality: {
        score: 78.2, band: "strong", label: "Strong batch",
        basis: "Average score of the top 5 gate-passing candidates",
        top_n: 5, thresholds: { strong_at: 75, okay_at: 55 },
      },
    })]);
    const row = (await screen.findByText("Financial Analyst")).closest("tr")!;
    // band only on the dashboard (2026-07-19) — the number lives in the workspace
    expect(within(row).getByText("Batch: Strong")).toBeInTheDocument();
  });

  it("shows live batch progress in the Candidates column while a screen runs", async () => {
    setup(
      [card({ req_id: "financial-analyst", screened: false, stage: "candidates_added", batch_count: 8 })],
      {
        "/api/screen/active": {
          jobs: [{ job_id: "j1", req_id: "financial-analyst", status: "running", done: 2, total: 8, message: "Screening 2 of 8…", error: "" }],
        },
      }
    );
    // the plain count is replaced by a live "Screening N/M" readout (the row opens
    // the workspace, where the Candidates tab shows the run)
    expect(await screen.findByText(/Screening 2\/8/)).toBeInTheDocument();
  });

  it("header carries the person's OWN screened total as a quiet readout, plus the live pill", async () => {
    const api = mockApi({
      "/api/roles": { ...rolesResponse([card()]), metrics: { ...rolesResponse([card()]).metrics, my_candidates_screened: 128, candidates_screened: 900 } },
      "/api/screen/active": {
        jobs: [
          { job_id: "j1", req_id: "financial-analyst", status: "running", done: 2, total: 8, message: "", error: "" },
          { job_id: "j2", req_id: "other", status: "running", done: 1, total: 4, message: "", error: "" },
        ],
      },
    });
    renderPage(<Routes><Route path="/reqs" element={<Requisitions />} /></Routes>, { route: "/reqs" });
    await screen.findByText("Financial Analyst");
    // the requisitions page is about REQUISITIONS: the all-reqs candidates total (900)
    // is no longer a hero; the person's own tally is a side line
    expect(screen.getByText(/You've screened/)).toBeInTheDocument();
    expect(screen.getByText("128")).toBeInTheDocument();
    expect(screen.queryByText("900")).not.toBeInTheDocument();
    expect(await screen.findByText("2 screening now")).toBeInTheDocument();
    expect(api.callsTo("/api/screen/active").length).toBeGreaterThan(0);
  });

  it("the counters count REQUISITIONS and say so; the HM counter waits for the portal flag", async () => {
    setup([card(), card({ req_id: "r2", title: "Second" })]);
    await screen.findByText("Second");
    expect(screen.getByRole("button", { name: /Active requisitions/ })).toHaveTextContent("2");
    expect(screen.getByRole("button", { name: /Need your action/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Suggested to close/ })).toBeInTheDocument();
    // no /api/features mock → hm_portal reads as off → the HM counter is not offered
    expect(screen.queryByRole("button", { name: /With the hiring manager/ })).toBeNull();
    // the region pills are gone (the sidebar lens is the region filter)
    for (const name of ["APAC", "EMEA", "Americas"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("filters the table when a metric card is clicked", async () => {
    setup([
      card(), // screened → needs action
      card({
        req_id: "fresh-role", title: "Fresh Role", stage: "draft_checklist",
        screened: false,
        cta: { label: "Create checklist", goto: "checklist", magic: true },
      }),
    ]);
    await screen.findByText("Fresh Role");
    await userEvent.click(screen.getByRole("button", { name: /Need your action/ }));
    expect(screen.getByText("Financial Analyst")).toBeInTheDocument();
    expect(screen.queryByText("Fresh Role")).not.toBeInTheDocument();
  });

  it("sorts by hiring manager name when the column header is clicked", async () => {
    setup([
      card({ req_id: "r-z", title: "Zeta Role", hm: { email: "", name: "Zara Khan", status: null } }),
      card({ req_id: "r-a", title: "Alpha Role", hm: { email: "", name: "Aarti Rao", status: null } }),
    ]);
    await screen.findByText("Zeta Role");
    await userEvent.click(screen.getByRole("button", { name: /Hiring manager/ }));
    // ascending by HM name → Aarti's row (Alpha Role) comes before Zara's (Zeta Role)
    const titles = screen.getAllByRole("link").map((a) => a.textContent).filter((t) => t?.endsWith("Role"));
    expect(titles.indexOf("Alpha Role")).toBeLessThan(titles.indexOf("Zeta Role"));
  });

  it("History shows the outcome chip and the retention clock", async () => {
    const purgeBy = new Date(Date.now() + 10 * 86_400_000).toISOString().slice(0, 10);
    setup([card({
      lifecycle: {
        status: "closed",
        outcome: { kind: "hired", hired_count: 2 },
        closed_at: "2026-07-01T09:00:00+00:00",
        closed_by: DEFAULT_ME.user,
        snooze_until: null,
        created_by: DEFAULT_ME.user,
        created_at: "2026-06-01T08:00:00+00:00",
      },
      retention: { keep_at_least_days: 1095, purge_by_days: 180, purge_by: purgeBy, purged: false },
    })]);
    await userEvent.click(await screen.findByRole("button", { name: /History · 1/ }));
    const row = (await screen.findByText("Financial Analyst")).closest("tr")!;
    // outcome chip links to the audit trail
    expect(within(row).getByRole("link", { name: "Hired · 2" })).toHaveAttribute(
      "href", "/reqs/financial-analyst/audit");
    expect(within(row).getByText(/PII purges in \d+d · audit kept 3y/)).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "View audit" })).toBeInTheDocument();
    // Reopen / Clone ship dark until P37 flag-gates them
    expect(within(row).getByRole("button", { name: "Reopen" })).toBeDisabled();
    expect(within(row).getByRole("button", { name: "Clone" })).toBeDisabled();
  });

  it("shows a cancelled close as a neutral chip", async () => {
    setup([card({
      lifecycle: {
        status: "closed", outcome: { kind: "cancelled" },
        closed_at: "2026-07-01T09:00:00+00:00", closed_by: DEFAULT_ME.user,
        snooze_until: null, created_by: DEFAULT_ME.user,
        created_at: "2026-06-01T08:00:00+00:00",
      },
      retention: { keep_at_least_days: 1095, purge_by_days: 180, purge_by: "2026-12-28", purged: false },
    })]);
    await userEvent.click(await screen.findByRole("button", { name: /History · 1/ }));
    expect(await screen.findByText("No hire — cancelled")).toBeInTheDocument();
  });

  it("suggestion banner: Keep open snoozes for 30 days; decisions link opens the popup", async () => {
    const suggested = card({ suggest_close: { suggest: true, decided: 3, quiet_days: 20 } });
    const api = setup([suggested], {
      "/api/roles/financial-analyst/snooze": { ok: true },
      "/api/roles/financial-analyst/decisions": {
        req_id: "financial-analyst", title: "Financial Analyst",
        decisions: [
          { candidate_id: "c1", name: "Bhavika Sharma", action: "forward", reason: "", reviewer: DEFAULT_ME.user, ts: "2026-07-10T09:00:00+00:00" },
          { candidate_id: "c2", name: "Gerard Branaola", action: "not", reason: "", reviewer: DEFAULT_ME.user, ts: "2026-07-10T09:05:00+00:00" },
        ],
      },
    });
    // the decided count is the design's popup link; the rest of the line follows it
    const link = await screen.findByRole("button", { name: "All 3 decisions recorded" });
    expect(screen.getByText(/quiet for 14 days · closing here leaves iCIMS unchanged/)).toBeInTheDocument();
    await userEvent.click(link);
    expect(await screen.findByText("Bhavika Sharma")).toBeInTheDocument();
    expect(screen.getByText("Move forward")).toBeInTheDocument();
    expect(screen.getByText("Not a fit")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close decisions" }));
    await userEvent.click(screen.getByRole("button", { name: "Keep open" }));
    await waitFor(() =>
      expect(api.callsTo("/api/roles/financial-analyst/snooze")).toHaveLength(1));
    expect(api.callsTo("/api/roles/financial-analyst/snooze")[0].body).toEqual({ days: 30 });
  });

  it("suggestion banner: Confirm close posts the picked outcome", async () => {
    const suggested = card({ suggest_close: { suggest: true, decided: 3, quiet_days: 20 } });
    const api = setup([suggested], {
      "/api/roles/financial-analyst/close": { ok: true, status: "closed" },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Confirm close" }));
    await userEvent.click(screen.getByLabelText("No hire"));
    await userEvent.click(screen.getByRole("button", { name: "Close requisition" }));
    await waitFor(() =>
      expect(api.callsTo("/api/roles/financial-analyst/close")).toHaveLength(1));
    expect(api.callsTo("/api/roles/financial-analyst/close")[0].body).toEqual({
      outcome: "cancelled",
    });
  });
});
