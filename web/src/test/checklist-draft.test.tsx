// F8: the Checklist tab autosaves unsaved edits to localStorage and offers to
// restore them on reopen (never auto-applies); the draft clears on publish; a
// corrupt entry is ignored; and a published-advanced draft shows the alternate
// copy.
import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage, type RouteValue } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import type { Requirement } from "../types";

const REQ_ID = "financial-analyst";
const RUBRIC_PATH = `/api/roles/${REQ_ID}/rubric`;
const KEY = `rs.checklistDraft.${REQ_ID}`;

const PUBLISHED: Requirement[] = [
  { id: "r01", text: "US GAAP experience", type: "other", kind: "must_have", importance: 5 },
];

function setup(rubricRoute?: RouteValue) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture({ rubric_version: 2 }),
    [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
    [RUBRIC_PATH]: rubricRoute ?? ((_u: string, init?: RequestInit) =>
      init?.method === "PUT"
        ? { version: 3, requirements: [] }
        : { version: 2, role_title: "Financial Analyst", requirements: PUBLISHED }),
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/checklist` }
  );
  return api;
}

function seedDraft(rows: Requirement[], baseVersion: number | null) {
  localStorage.setItem(KEY, JSON.stringify({ savedAt: "2026-07-14T09:00:00+00:00", baseVersion, rows }));
}

describe("Checklist draft persistence", () => {
  it("offers to restore a differing draft, and applies it on Restore", async () => {
    seedDraft([{ id: "r01", text: "US GAAP AND IFRS", type: "other", kind: "must_have", importance: 5 }], 2);
    setup();
    expect(await screen.findByText("You have unsaved checklist edits")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Restore my edits" }));
    expect(screen.getByDisplayValue("US GAAP AND IFRS")).toBeInTheDocument();
    expect(screen.queryByText("You have unsaved checklist edits")).not.toBeInTheDocument();
  });

  it("Discard removes the stored draft and keeps the published rows", async () => {
    seedDraft([{ id: "r01", text: "US GAAP AND IFRS", type: "other", kind: "must_have", importance: 5 }], 2);
    setup();
    await screen.findByText("You have unsaved checklist edits");
    await userEvent.click(screen.getByRole("button", { name: "Discard draft" }));
    expect(localStorage.getItem(KEY)).toBeNull();
    expect(screen.getByDisplayValue("US GAAP experience")).toBeInTheDocument();
  });

  it("silently drops a draft older than the published version — no prompt (2026-07-21)", async () => {
    // baseVersion 1, but the checklist was published again since (v2): the stale
    // draft is obsolete, so it's cleared instead of the "random" restore prompt.
    seedDraft([{ id: "r01", text: "US GAAP AND IFRS", type: "other", kind: "must_have", importance: 5 }], 1);
    setup();
    expect(await screen.findByDisplayValue("US GAAP experience")).toBeInTheDocument();
    expect(screen.queryByText("Your saved edits are older than the published checklist")).not.toBeInTheDocument();
    expect(screen.queryByText("You have unsaved checklist edits")).not.toBeInTheDocument();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("ignores a corrupt draft entry without crashing", async () => {
    localStorage.setItem(KEY, "{not valid json");
    setup();
    expect(await screen.findByDisplayValue("US GAAP experience")).toBeInTheDocument();
    expect(screen.queryByText(/unsaved checklist edits/)).not.toBeInTheDocument();
    expect(localStorage.getItem(KEY)).toBeNull(); // removed on read
  });

  it("autosaves edits and clears the draft on publish", async () => {
    vi.useFakeTimers();
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const api = setup();
      // wait for the rubric to load (fake timers — flush microtasks via act)
      await vi.waitFor(() => expect(screen.getByDisplayValue("US GAAP experience")).toBeInTheDocument());
      const box = screen.getByDisplayValue("US GAAP experience");
      await user.clear(box);
      await user.type(box, "US GAAP and IFRS");
      vi.advanceTimersByTime(700); // past the 600ms debounce
      await vi.waitFor(() => expect(localStorage.getItem(KEY)).not.toBeNull());
      expect(JSON.parse(localStorage.getItem(KEY)!).rows[0].text).toBe("US GAAP and IFRS");

      // publish clears the draft
      await user.click(screen.getByRole("button", { name: "Approve & publish v3" }));
      await vi.waitFor(() => expect(api.callsTo(RUBRIC_PATH).some((c) => c.method === "PUT")).toBe(true));
      expect(localStorage.getItem(KEY)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
