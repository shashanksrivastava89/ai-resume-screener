// P46: the Audit Explorer UI over the P45 explorer API. Tabs render, the merged
// feed shows kind chips, the candidate timeline shows the verbatim decision
// reason, the retention hold button POSTs, and a ?req= deep link lands on the
// Requisitions tab with that card selected.
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AuditExplorer from "../pages/AuditExplorer";
import { mockApi, renderPage } from "./utils";

const FEED = {
  entries: [
    { ts: "2026-06-05T10:00:00+00:00", kind: "Decision", actor: "ta@example.com",
      summary: "Anita Rao: Move forward — strong treasury depth", req_id: "fin-analyst",
      candidate: "Anita Rao", region: "IN", source: "decision" },
    { ts: "2026-06-01T09:00:00+00:00", kind: "Data", actor: "", req_id: "fin-analyst",
      summary: "Screening run · Finance Analyst · India · 1 scored", candidate: null,
      region: "IN", source: "run" },
  ],
  kinds: ["Decision", "Preference", "Rules", "Close", "Data", "Views"],
};

const REPORTS = {
  reports: [
    { key: "throughput_by_region", label: "Screening throughput by region", status: "ready" },
    { key: "adverse_impact", label: "Adverse-impact monitor", status: "Phase 2" },
  ],
};

const MATCHES = {
  matches: [{ req_id: "fin-analyst", req_title: "Finance Analyst", candidate_id: "c1",
              name: "Anita Rao", region: "IN" }],
};

const TIMELINE = {
  req_id: "fin-analyst", candidate_id: "c1", name: "Anita Rao", tz: "Asia/Kolkata",
  steps: [
    { step: "screened", ts: "2026-06-01T09:00:00+00:00", score: 82, band: "Strong match",
      gates: { total: 1, failed: [] }, run_id: "run-1", checklist_version: "v1", pack: "IN" },
    { step: "decision", ts: "2026-06-05T10:00:00+00:00", who: "ta@example.com",
      action: "Move forward", reason: "strong treasury depth" },
    { step: "handed_off", note: "Handed off — hire status lives in iCIMS." },
  ],
  targets: { audit_pack_url: "/api/roles/fin-analyst/audit", checklist_version: "v1",
             jd_ref: "jds/fin-analyst.md", run_id: "run-1" },
};

const REQS = {
  requisitions: [{ req_id: "fin-analyst", title: "Finance Analyst", req_code: "REQ-1",
                   jurisdiction: "IN", region: "IN", status: "closed", created_by: "ta@example.com",
                   events: [{ ts: "2026-06-05T10:00:00+00:00", kind: "req_close",
                              actor: "ta@example.com", summary: "Closed — hired 1" }] }],
};

const RETENTION = {
  queue: [{ req_id: "fin-analyst", title: "Finance Analyst", jurisdiction: "IN", pack: "IN",
            closed_at: "2026-06-05T00:00:00+00:00", last_run_at: null, purge_due: "2027-06-05",
            keep_audit_until: "2029-06-04", status: "scheduled", hold: null }],
  summaries: ["IN — DPDP Act 2023: audit records kept ≥ 3 yrs · candidate PII purged 365 days after close"],
};

function setup(route = "/audit") {
  const api = mockApi({
    "/api/audit/feed": FEED,
    "/api/audit/reports": REPORTS,
    "/api/audit/candidates": MATCHES,
    "/api/audit/candidates/fin-analyst/c1/timeline": TIMELINE,
    "/api/audit/requisitions": REQS,
    "/api/audit/rules": { entries: [] },
    "/api/audit/retention": RETENTION,
    "/api/audit/retention/fin-analyst/hold": { ok: true, hold: { by: "dev@example.com", ts: "x", reason: "litigation" } },
  });
  renderPage(<AuditExplorer />, { route });
  return api;
}

describe("Audit Explorer", () => {
  it("renders the five tabs and the feed with kind chips", async () => {
    setup();
    for (const label of ["All", "Candidates", "Requisitions", "Rules & criteria", "PII & retention"]) {
      expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
    }
    // the verbatim decision summary appears in the feed
    expect(await screen.findByText("Anita Rao: Move forward — strong treasury depth")).toBeInTheDocument();
    // kind filter chips render (a "Decision" filter pill among them)
    expect(screen.getByRole("button", { name: "Decision" })).toBeInTheDocument();
    // the reports rail shows the Phase-2 chip
    expect(screen.getByText("Phase 2")).toBeInTheDocument();
  });

  it("shows a candidate timeline with the verbatim decision reason", async () => {
    setup();
    await userEvent.click(screen.getByRole("tab", { name: "Candidates" }));
    await userEvent.type(screen.getByPlaceholderText("Search a candidate by name"), "anita");
    await userEvent.click(await screen.findByRole("button", { name: /Anita Rao/ }));
    // the decision step renders the reviewer's verbatim reason
    expect(await screen.findByText(/strong treasury depth/)).toBeInTheDocument();
    expect(screen.getByText("Handed off — hire status lives in iCIMS.")).toBeInTheDocument();
  });

  it("applies a legal hold via POST", async () => {
    const api = setup();
    await userEvent.click(screen.getByRole("tab", { name: "PII & retention" }));
    await userEvent.click(await screen.findByRole("button", { name: "Apply legal hold" }));
    await userEvent.type(screen.getByPlaceholderText(/Reason for the legal hold/), "litigation");
    await userEvent.click(screen.getByRole("button", { name: "Place hold" }));
    await waitFor(() => expect(api.callsTo("/api/audit/retention/fin-analyst/hold")).toHaveLength(1));
    expect(api.callsTo("/api/audit/retention/fin-analyst/hold")[0].body).toEqual({ reason: "litigation" });
  });

  it("honors the ?req= deep link on the Requisitions tab", async () => {
    const api = setup("/audit?req=fin-analyst");
    // lands on Requisitions with that card shown
    expect(screen.getByRole("tab", { name: "Requisitions" })).toHaveAttribute("aria-selected", "true");
    const card = (await screen.findByText("Finance Analyst")).closest("[data-req]")!;
    expect(card).toHaveAttribute("data-req", "fin-analyst");
    // the fetch carried the deep-linked req
    expect(api.callsTo("/api/audit/requisitions")[0].url).toContain("req=fin-analyst");
  });
});
