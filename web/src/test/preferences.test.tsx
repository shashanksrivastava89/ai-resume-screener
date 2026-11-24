// P39 + the v3 design: My preferences (every preference goes for sign-off —
// ok→a sign-off confirm, warn→the approval confirm with the warning, blocked→recorded
// as a visible blocked entry) and the HR-Head approval queue (select-all → bulk
// approve posts the ids; nobody can select their own).
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MyPreferences from "../pages/MyPreferences";
import PreferencesSection from "../components/approvals/PreferencesSection";
import { ToastProvider } from "../components/overlays";
import { mockApi, renderPage } from "./utils";
import { SCORING_CONFIG } from "./fixtures";
import type { Me, Preference } from "../types";

function me(caps: string[]): Me {
  return {
    user: "ta@example.com", role: "TASpecialist", roles: ["TASpecialist"], known_role: true,
    capabilities: caps, has_admin: caps.includes("prefs_approve"), can_edit_rules: false,
    evaluation_mode: true, review_pending: false, last_review: null, pref_pending: 1,
  };
}

const OK_CHECK = { level: "ok", basis_ids: [], reason: "No conflicts with the fairness rules." };
const WARN_CHECK = { level: "warn", basis_ids: ["location_preference"],
  reason: "This looks like a location preference." };
const BLOCKED_CHECK = { level: "blocked", basis_ids: ["age"], reason: "This references age." };

function row(over: Partial<Preference> = {}): Preference {
  return {
    id: "p1", text: "prefers candidates in Mumbai", owner: "ta@example.com",
    status: "pending", status_label: "Waiting for HR Head sign-off", level: "warn",
    warning: "This looks like a location preference.", cap_note: "Capped at +2 if approved.",
    ...over,
  };
}

describe("MyPreferences", () => {
  it("a clean preference still goes for sign-off — confirm, then post (pen-test RS-01)", async () => {
    const api = mockApi({
      "/api/preferences/check": OK_CHECK,
      "/api/preferences": (_u: string, init?: RequestInit) =>
        init?.method === "POST" ? row({ level: "ok", warning: null }) : { preferences: [] },
    });
    renderPage(<ToastProvider><MyPreferences /></ToastProvider>, { route: "/preferences", me: me(["recruiter", "prefs_own"]) });

    await userEvent.type(await screen.findByLabelText("New preference"), "strong US GAAP experience");
    await userEvent.click(screen.getByRole("button", { name: /check & add/i }));

    expect(await screen.findByText("This needs HR Head sign-off")).toBeInTheDocument();
    expect(api.callsTo("/api/preferences").some((c) => c.method === "POST")).toBe(false);
    expect(screen.queryByText(/active immediately/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /send for sign-off/i }));
    await waitFor(() =>
      expect(api.callsTo("/api/preferences").some((c) => c.method === "POST")).toBe(true));
  });

  it("warn preference shows the approval confirm before committing", async () => {
    const api = mockApi({
      "/api/preferences/check": WARN_CHECK,
      "/api/preferences": (_u: string, init?: RequestInit) => (init?.method === "POST" ? row() : { preferences: [] }),
    });
    renderPage(<ToastProvider><MyPreferences /></ToastProvider>, { route: "/preferences", me: me(["recruiter", "prefs_own"]) });

    await userEvent.type(await screen.findByLabelText("New preference"), "prefers candidates in Mumbai");
    await userEvent.click(screen.getByRole("button", { name: /check & add/i }));

    // confirm dialog appears; no POST yet
    expect(await screen.findByText("This needs HR Head approval")).toBeInTheDocument();
    expect(api.callsTo("/api/preferences").some((c) => c.method === "POST")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: /send for approval/i }));
    await waitFor(() =>
      expect(api.callsTo("/api/preferences").some((c) => c.method === "POST")).toBe(true));
  });

  it("blocked preference shows the refusal confirm; recording keeps it visible", async () => {
    const api = mockApi({
      "/api/preferences/check": BLOCKED_CHECK,
      "/api/preferences": (_u: string, init?: RequestInit) =>
        init?.method === "POST"
          ? row({ status: "blocked", status_label: "Blocked", level: "blocked" })
          : { preferences: [] },
    });
    renderPage(<ToastProvider><MyPreferences /></ToastProvider>, { route: "/preferences", me: me(["recruiter", "prefs_own"]) });

    await userEvent.type(await screen.findByLabelText("New preference"), "prefers candidates under 30");
    await userEvent.click(screen.getByRole("button", { name: /check & add/i }));

    // the confirm names the protected attribute; nothing committed yet
    expect(await screen.findByText("This can't be a preference")).toBeInTheDocument();
    expect(screen.queryByText(/[Rr]eword/)).toBeNull();  // no route around the rule
    expect(api.callsTo("/api/preferences").some((c) => c.method === "POST")).toBe(false);
    // recording keeps a visible blocked entry (nothing disappears quietly)
    await userEvent.click(screen.getByRole("button", { name: "I understand" }));
    await waitFor(() =>
      expect(api.callsTo("/api/preferences").some((c) => c.method === "POST")).toBe(true));
  });
});

describe("what a preference does (2026-09-25)", () => {
  // A pen tester added a preference, screened a résumé stating the opposite, saw no
  // change and asked what he was missing. The page never said a preference only adds,
  // only past the hard gates, only from the next screen.
  it("says up front that it only adds, when it applies, and to whom", async () => {
    mockApi({
      "/api/preferences": { preferences: [] },
      "/api/scoring-config": { ...SCORING_CONFIG,
        preferences: { per_preference_cap_points: 3, review_months: 9 } },
    });
    renderPage(<ToastProvider><MyPreferences /></ToastProvider>, { route: "/preferences", me: me(["recruiter", "prefs_own"]) });
    const intro = await screen.findByText(/A preference only adds/);
    await waitFor(() => expect(intro).toHaveTextContent("up to +3"));   // the cap from the API
    expect(intro).toHaveTextContent("never a deduction");
    expect(intro).toHaveTextContent("meet every hard gate");
    expect(intro).toHaveTextContent("next screening run");
    expect(intro).toHaveTextContent("created or are the hiring manager for");
  });
});

describe("Preference approvals (the Approvals-page section)", () => {
  it("select-all then approve posts the selected ids; copy is dynamic", async () => {
    const api = mockApi({
      "/api/preferences/pending": { pending: [row({ owner: "other@example.com" }),
        row({ id: "p2", text: "tier-1 only", owner: "other@example.com" })], count: 2 },
      "/api/preferences/approve": { ok: true, approved: 2, preferences: [] },
      "/api/scoring-config": { ...SCORING_CONFIG,
        preferences: { per_preference_cap_points: 3, review_months: 9 },
        display: { ...SCORING_CONFIG.display,
          preference_cap_note: "Each preference can move a score by at most 3 points and never hides anyone." } },
    });
    renderPage(<ToastProvider><PreferencesSection /></ToastProvider>, { route: "/approvals",
      me: me(["recruiter", "prefs_own", "prefs_approve"]) });

    // the cap comes from the API, never a literal (the card's bold cap note)
    expect((await screen.findAllByText(/Capped at \+3 if approved/)).length).toBe(2);

    await userEvent.click(await screen.findByLabelText("Select all pending"));
    await userEvent.click(screen.getByRole("button", { name: /approve selected \(2\)/i }));

    const post = api.callsTo("/api/preferences/approve").find((c) => c.method === "POST");
    expect(post?.body).toEqual({ pref_ids: ["p1", "p2"] });
  });
});

describe("nobody approves their own preference (pen-test RS-01)", () => {
  it("an approver's own row cannot be selected, and a clean row says why it is here", async () => {
    const api = mockApi({
      "/api/preferences/pending": { pending: [
        row({ id: "mine", text: "strong US GAAP experience", level: "ok", warning: null }),
        row({ id: "theirs", owner: "other@example.com" }),
      ], count: 2 },
      "/api/preferences/approve": { ok: true, approved: 1, preferences: [] },
    });
    renderPage(<ToastProvider><PreferencesSection /></ToastProvider>, { route: "/approvals",
      me: me(["recruiter", "prefs_own", "prefs_approve"]) });

    expect(await screen.findByText(/Yours — another HR Head signs it off/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Select strong US GAAP experience")).toBeNull();
    expect(screen.getByText(/automated checks found no conflicts/)).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Select all pending"));
    await userEvent.click(screen.getByRole("button", { name: /approve selected \(1\)/i }));
    const post = api.callsTo("/api/preferences/approve").find((c) => c.method === "POST");
    expect(post?.body).toEqual({ pref_ids: ["theirs"] });
  });
});
