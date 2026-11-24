// Jurisdictions page (2026-07-21, Phase 2): the "Blocked here" section shows the
// universal floor count + region-specific patterns, and the pack editor's
// "Derive from rules" appends AI-proposed patterns for review before proposal.
import { describe, it, expect, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Jurisdictions from "../pages/Jurisdictions";
import { mockApi, renderPage, resetApiMock, DEFAULT_ME } from "./utils";
import type { BlockedPattern } from "../types";

function bp(over: Partial<BlockedPattern>): BlockedPattern {
  return {
    id: "x", name: "X", reason: "r", severity: "blocked", floor: false,
    stages: ["preferences", "checklist"], phrases: [], regexes: [], source: "seed", ...over,
  };
}

const CA = {
  code: "US-CA", name: "California", law: "FEHA / CRD", active: false, badge: null,
  obligations: ["CRD automated-decision rules"], keep_at_least_days: 1460, purge_by_days: 1460,
  monitoring_categories: null,
  blocked_patterns: [
    bp({ id: "protected_demographic", name: "Protected demographic", floor: true }),
    bp({ id: "age", name: "Age", floor: true }),
    bp({ id: "ca_salary_history", name: "Salary history (California)", floor: false, phrases: ["salary history"] }),
  ],
  own_blocked_patterns: [
    bp({ id: "ca_salary_history", name: "Salary history (California)", floor: false, phrases: ["salary history"] }),
  ],
  own_obligations: ["CRD automated-decision rules"], own_keep_at_least_days: 1460,
  own_purge_by_days: 1460, own_monitoring_categories: null, version: 1,
};

function routes(extra: Record<string, unknown> = {}) {
  return mockApi({
    "/api/jurisdictions": { default: "IN", jurisdictions: [CA] },
    ...extra,
  });
}

beforeEach(() => resetApiMock());

describe("Jurisdictions — blocked patterns", () => {
  it("shows the universal floor count and region-specific blocked factors", async () => {
    routes();
    renderPage(<Jurisdictions />, { route: "/jurisdictions", me: DEFAULT_ME });
    expect(await screen.findByText("Blocked here")).toBeInTheDocument();
    expect(screen.getByText(/2 protected-attribute rules/)).toBeInTheDocument();
    expect(screen.getByText("Salary history (California)")).toBeInTheDocument();
  });

  it("derives proposed patterns into the editor for review", async () => {
    routes({
      "/api/jurisdictions/US-CA/derive": {
        patterns: [bp({ id: "us_ca_criminal_history", name: "Criminal history", severity: "warn",
          floor: false, source: "derived", phrases: ["criminal history"] })],
        conflicts: [{ signal_id: "skill_recency", note: "unrelated" }],
        source: "model", note: "",
      },
    });
    renderPage(<Jurisdictions />, { route: "/jurisdictions", me: DEFAULT_ME });
    await userEvent.click(await screen.findByText(/Edit signals, obligations, retention/));
    await userEvent.click(screen.getByText(/Derive from rules/));
    await waitFor(() => expect(screen.getByDisplayValue("Criminal history")).toBeInTheDocument());
    expect(screen.getByText(/Added 1 proposed pattern/)).toBeInTheDocument();
  });
});
