// The checklist's publish action sits at the END of the form (2026-09-06, pilot
// feedback item 2), in a bar that reads the state plainly: "vN is in force" when
// nothing changed, teal "Unpublished edits" once a line is touched. The rail keeps
// activity and the JD only.
import { describe, it, expect } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import type { Requirement } from "../types";

const REQ_ID = "financial-analyst";

const ROWS: Requirement[] = [
  { id: "r01", text: "CA / CPA qualification", type: "other", kind: "hard_gate", importance: 3 },
  { id: "r02", text: "Treasury accounting depth", type: "experience", kind: "must_have", importance: 4 },
];

function setup(opts: { closed?: boolean } = {}) {
  const base = roleFixture();
  mockApi({
    [`/api/roles/${REQ_ID}`]: opts.closed
      ? { ...base, lifecycle: { ...base.lifecycle, status: "closed", outcome: { kind: "cancelled" },
                                closed_at: "2026-10-16T09:00:00+00:00" } }
      : base,
    [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
    [`/api/roles/${REQ_ID}/rubric`]: { version: 2, role_title: "Financial Analyst", requirements: ROWS },
  });
  renderPage(
    <Routes>
      {workspaceRoutes()}
      <Route path="/reqs" element={<div>list</div>} />
    </Routes>,
    { route: `/reqs/${REQ_ID}/checklist` }
  );
}

describe("Checklist publish bar", () => {
  it("freezes the whole editor on a closed requisition (2026-10-16)", async () => {
    setup({ closed: true });
    const row = await screen.findByDisplayValue("Treasury accounting depth");
    expect(row).toBeDisabled();
    const publish = within(screen.getByTestId("publish-bar")).getByRole("button", { name: /Approve & publish/ });
    expect(publish).toBeDisabled();
    expect(publish.parentElement).toHaveAttribute("title", "This requisition is closed — its checklist is frozen.");
  });

  it("leaves an open requisition's editor editable", async () => {
    setup();
    expect(await screen.findByDisplayValue("Treasury accounting depth")).toBeEnabled();
  });

  it("sits after the rows, reads the in-force version, and turns teal on a real edit", async () => {
    setup();
    const lastRow = await screen.findByDisplayValue("Treasury accounting depth");
    const bar = screen.getByTestId("publish-bar");
    // AFTER the form, in document order
    expect(lastRow.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(bar).toHaveTextContent("v2 is in force. Edit any line to draft v3.");
    expect(bar.className).not.toContain("is-dirty");
    const publish = within(bar).getByRole("button", { name: "Approve & publish v3" });
    expect(publish).toBeDisabled();
    expect(publish).toHaveAttribute("title", "Nothing changed since the published version.");

    await userEvent.type(lastRow, " and month-end close");
    expect(bar.className).toContain("is-dirty");
    expect(bar).toHaveTextContent("Unpublished edits — screening still uses v2 until you publish.");
    expect(within(bar).getByRole("button", { name: "Approve & publish v3" })).toBeEnabled();
  });

  it("has no side rail at all — the form gets the width (2026-09-07, item 2)", async () => {
    setup();
    await screen.findByDisplayValue("Treasury accounting depth");
    expect(document.querySelector(".checklist-rail")).toBeNull();
    // the job description moved to the top, collapsed: available, not permanent
    const jd = screen.getByRole("button", { name: /Job description/ });
    expect(jd).toHaveAttribute("aria-expanded", "false");
  });

  it("offers Discard edits only once something changed", async () => {
    setup();
    const row = await screen.findByDisplayValue("Treasury accounting depth");
    expect(screen.queryByRole("button", { name: "Discard edits" })).toBeNull();
    await userEvent.type(row, " x");
    await userEvent.click(await screen.findByRole("button", { name: "Discard edits" }));
    const dialog = await screen.findByRole("dialog", { name: "Discard your unpublished edits?" });
    expect(dialog).toHaveTextContent("goes back to v2, the published version");
    await userEvent.click(within(dialog).getByRole("button", { name: "Discard edits" }));
    expect(await screen.findByDisplayValue("Treasury accounting depth")).toBeInTheDocument();
    expect(screen.getByTestId("publish-bar").className).not.toContain("is-dirty");
  });
});
