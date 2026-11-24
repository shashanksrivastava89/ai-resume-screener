// The notification panel (2026-07-20): real names, grouped by requisition, a
// scoring-model approval routing to the Scoring-model tab, and staying open
// while you click through.
import { useLocation } from "react-router-dom";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect } from "vitest";
import NotificationsBell, { notificationTarget } from "../components/NotificationsBell";
import type { AppNotification } from "../types";
import { mockApi, renderPage } from "./utils";

function Loc() {
  const l = useLocation();
  return <div data-testid="loc">{l.pathname}{l.search}</div>;
}

describe("Notifications panel", () => {
  it("shows names, groups by requisition, keeps a count, and routes a scoring-model approval to the model tab", async () => {
    mockApi({
      "/api/notifications": {
        notifications: [
          { ts: "2026-07-20T10:00:00+00:00", summary: "Rahul commented on a candidate",
            from: "gerard@example.com", from_name: "Rahul", req_id: "fin",
            req_title: "Finance Analyst", kind: "comment_added", data: { candidate_id: "c1" } },
          { ts: "2026-07-20T09:00:00+00:00", summary: "Your change to the scoring model was approved by Arjun",
            from: "arjun@example.com", from_name: "Arjun", req_id: null,
            kind: "change_approved", data: { change_id: "x" } },
        ],
      },
    });
    renderPage(<><NotificationsBell /><Loc /></>, { route: "/reqs" });

    // unread count on the bell (both are new — no last-seen yet)
    const bell = await screen.findByRole("button", { name: "Notifications — 2 new" });
    await userEvent.click(bell);

    // real name in the row + the requisition group header
    expect(await screen.findByText("Rahul commented on a candidate")).toBeInTheDocument();
    expect(screen.getByText("Finance Analyst")).toBeInTheDocument();

    // pure routing: a scoring-model approval lands on the Scoring-model tab
    expect(notificationTarget({ kind: "change_approved", req_id: null } as AppNotification))
      .toBe("/approvals?tab=model");

    // clicking it navigates AND the panel stays open (non-modal)
    await userEvent.click(screen.getByText(/scoring model was approved/));
    expect(screen.getByTestId("loc").textContent).toBe("/approvals?tab=model");
    expect(screen.getByText("Rahul commented on a candidate")).toBeInTheDocument();
  });

  it("routes checklist notifications to the Checklist tab (not Review)", () => {
    const t = (kind: string, req_id: string | null, data: Record<string, unknown> = {}) =>
      notificationTarget({ kind, req_id, data } as AppNotification);
    // checklist-related → Checklist tab
    expect(t("suggestion_created", "r1", { suggestion_id: "s1" })).toBe("/reqs/r1/checklist?focus=sugg:s1");
    expect(t("suggestion_accepted", "r1", { suggestion_id: "s1" })).toBe("/reqs/r1/checklist?focus=sugg:s1");
    expect(t("hm_invited", "r1")).toBe("/reqs/r1/checklist");
    // candidate-review-related → Review tab
    expect(t("hm_review_requested", "r1")).toBe("/reqs/r1/review");
    expect(t("comment_added", "r1", { candidate_id: "c1" })).toContain("/reqs/r1/review");
  });

  it("filters notifications by search", async () => {
    mockApi({
      "/api/notifications": {
        notifications: [
          { ts: "2026-07-20T10:00:00+00:00", summary: "Bhavika shared a shortlist with you",
            from: "bhavika@example.com", from_name: "Bhavika", req_id: "sales", req_title: "Sales Lead",
            kind: "shortlist_shared", data: {} },
          { ts: "2026-07-20T09:00:00+00:00", summary: "Rahul mentioned you in a comment",
            from: "gerard@example.com", from_name: "Rahul", req_id: "fin", req_title: "Finance Analyst",
            kind: "comment_mention", data: { candidate_id: "c1" } },
        ],
      },
    });
    renderPage(<NotificationsBell />, { route: "/reqs" });
    await userEvent.click(await screen.findByRole("button", { name: /Notifications/ }));
    await userEvent.type(screen.getByPlaceholderText("Search notifications…"), "shortlist");
    expect(screen.getByText("Bhavika shared a shortlist with you")).toBeInTheDocument();
    expect(screen.queryByText("Rahul mentioned you in a comment")).not.toBeInTheDocument();
  });
});
