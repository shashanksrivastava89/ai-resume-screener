// The Approvals page (v3 design, single-second-leader): the accountability
// card (diff table, leveled warnings incl. the red acknowledgment, rationale
// verbatim, the 60-second read), self-approval → withdraw, decline needs a
// note, tabs by capability.
import { describe, it, expect, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Approvals from "../pages/Approvals";
import { ToastProvider } from "../components/overlays";
import { mockApi, renderPage, resetApiMock, DEFAULT_ME } from "./utils";
import { SCORING_CONFIG } from "./fixtures";
import type { ChangeRequest } from "../types";

function change(over: Partial<ChangeRequest> = {}): ChangeRequest {
  return {
    change_id: "c1", kind: "scoring_model", target: "scoring_model", base_version: 1,
    summary: ["“Strong match” starts at: 88 → 85"],
    diff: [{ path: "bands.strong_at", label: "“Strong match” starts at", from: "88", to: "85" }],
    system_warnings: [], rationale: "July calibration",
    proposed_by: "bhavika@example.com", proposed_at: "2026-07-17T09:00:00+00:00",
    status: "pending", decided_by: null, decided_at: null, decline_note: null,
    resulting_version: null,
    ...over,
  };
}

function routes(changes: ChangeRequest[]) {
  return mockApi({
    "/api/changes?status=pending": { changes, pending_count: changes.length },
    "/api/changes/c1/approve": { ok: true, change: { ...changes[0], status: "approved" }, version: 2 },
    "/api/changes/c1/decline": { ok: true, change: { ...changes[0], status: "declined" } },
    "/api/changes/c1/withdraw": { ok: true, change: { ...changes[0], status: "withdrawn" } },
    "/api/preferences/pending": { pending: [], count: 0 },
    "/api/scoring-config": SCORING_CONFIG,
  });
}

function setup(changes: ChangeRequest[], me = DEFAULT_ME) {
  const api = routes(changes);
  renderPage(<ToastProvider><Approvals /></ToastProvider>, { route: "/approvals", me });
  return api;
}

beforeEach(() => resetApiMock());

describe("Approvals — the change queue", () => {
  it("renders the accountability card and approves with one click", async () => {
    const api = setup([change({
      system_warnings: [{ level: "warn", path: "bands.strong_at",
        text: "“Strong match” starts at: 97 is outside the usual range (75–95)." }],
    })], { ...DEFAULT_ME, capabilities: ["changes_approve"] });

    // title, pending chip, byLine scope, diff row old → new, warning, rationale
    expect(await screen.findByText("Scoring model v2 — 1 change")).toBeInTheDocument();
    expect(screen.getByText("Pending · awaiting a second leader")).toBeInTheDocument();
    // There is ONE scoring model. This pinned "applies to India (IN) requisitions
    // only — other regions keep their own model", which told an approver a global
    // change was local (2026-09-12).
    expect(screen.getByText(/applies to every requisition, everywhere/)).toBeInTheDocument();
    expect(screen.getByText("88")).toBeInTheDocument();
    expect(screen.getByText("85")).toBeInTheDocument();
    expect(screen.getByText(/outside the usual range/)).toBeInTheDocument();
    expect(screen.getByText(/Reasoning: “July calibration”/)).toBeInTheDocument();
    // the 60-second read is one click away, on the card
    await userEvent.click(screen.getByRole("button", { name: /How scoring works — the 60-second read/ }));
    expect(await screen.findByText(/The checklist does most of the work/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Approve & publish" }));
    await waitFor(() =>
      expect(api.callsTo("/api/changes/c1/approve").filter((c) => c.method === "POST")).toHaveLength(1));
  });

  it("red warnings change the button to acknowledge-and-approve", async () => {
    setup([change({
      system_warnings: [{ level: "red", path: "topics.salary_history",
        text: "“Salary history” is a policy-floor protection tied to anti-discrimination law." }],
    })], { ...DEFAULT_ME, capabilities: ["changes_approve"] });
    expect(await screen.findByText(/Acknowledge before approving:/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Acknowledge exposure & approve" })).toBeInTheDocument();
  });

  it("the proposer sees withdraw instead of approve", async () => {
    const api = setup([change({ proposed_by: DEFAULT_ME.user })],
      { ...DEFAULT_ME, capabilities: ["changes_approve"] });
    expect(await screen.findByText(/You proposed this — you can’t sign it off yourself/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve & publish" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Withdraw proposal" }));
    await waitFor(() =>
      expect(api.callsTo("/api/changes/c1/withdraw").filter((c) => c.method === "POST")).toHaveLength(1));
  });

  it("declining requires a note and posts it", async () => {
    const api = setup([change()], { ...DEFAULT_ME, capabilities: ["changes_approve"] });

    await userEvent.click(await screen.findByRole("button", { name: "Return to proposer with a note" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm decline" }));
    expect(await screen.findByText(/note explaining the decline is required/)).toBeInTheDocument();
    expect(api.callsTo("/api/changes/c1/decline")).toHaveLength(0);

    await userEvent.type(screen.getByLabelText("Decline note"), "Wait for the July batch.");
    await userEvent.click(screen.getByRole("button", { name: "Confirm decline" }));
    await waitFor(() => expect(api.callsTo("/api/changes/c1/decline")).toHaveLength(1));
    expect(api.callsTo("/api/changes/c1/decline")[0].body).toEqual({ note: "Wait for the July batch." });
  });

  it("shows both tabs to the HR Head, only the change queue otherwise", async () => {
    setup([], { ...DEFAULT_ME, pref_pending: 2, changes_pending: 0,
      capabilities: ["changes_approve", "prefs_approve"] });
    expect(await screen.findByRole("tab", { name: "Preferences · 2" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Scoring model · 0" })).toBeInTheDocument();
    // the prefs tab is default for the HR Head; its subtitle quotes the model
    expect(screen.getByText(/sets a 6-month re-review\. Each stays capped at \+2/)).toBeInTheDocument();
  });
});
