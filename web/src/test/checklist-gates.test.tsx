// P33: the checklist editor's three-state kind control — gate rows show
// "pass / fail" (the one-line explainer behind its info dot), toggling preserves
// the typed importance within the session, and Publish (enabled only once
// something actually changed, 2026-09-06) sends the single ordered list (kinds + order).
import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { workspaceRoutes } from "../pages/workspace/Workspace";
import { mockApi, renderPage } from "./utils";
import { roleFixture, JD_FIXTURE } from "./fixtures";
import type { Requirement } from "../types";

const REQ_ID = "financial-analyst";

function setup(requirements: Requirement[]) {
  const api = mockApi({
    [`/api/roles/${REQ_ID}`]: roleFixture(),
    [`/api/roles/${REQ_ID}/jd`]: JD_FIXTURE,
    // same path serves GET (load) and PUT (save)
    [`/api/roles/${REQ_ID}/rubric`]: (_url: string, init?: RequestInit) =>
      init?.method === "PUT"
        ? { version: 3, requirements }
        : { version: 2, role_title: "Financial Analyst", requirements },
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

const THREE_ROWS: Requirement[] = [
  { id: "r01", text: "CA / CPA qualification", type: "other", kind: "hard_gate", importance: 3 },
  { id: "r02", text: "Treasury accounting depth", type: "experience", kind: "must_have", importance: 4 },
  { id: "r03", text: "Oracle EBS exposure", type: "skill", kind: "nice_to_have", importance: 2 },
];

function rowFor(text: string): HTMLElement {
  // input -> the grid div -> the bordered row wrapper (grid + helper line)
  return screen.getByDisplayValue(text).closest("div")!.parentElement!;
}

describe("Checklist editor hard gates", () => {
  it("renders the three-state control and the gate row copy", async () => {
    setup(THREE_ROWS);
    await screen.findByDisplayValue("CA / CPA qualification");
    const gateRow = rowFor("CA / CPA qualification");
    // the gate row: pressed Hard gate, no importance meter, the two copy lines
    const control = within(gateRow).getByRole("group", { name: "Requirement kind" });
    expect(within(control).getByRole("button", { name: "Hard gate" })).toHaveAttribute("aria-pressed", "true");
    expect(within(gateRow).getByText("pass / fail")).toBeInTheDocument();
    // the gate explainer, one sentence, lives behind the slot's info dot (2026-09-06:
    // it used to be a line under the row, which made the row grow on every toggle)
    await userEvent.hover(within(gateRow).getByLabelText("What a hard gate means"));
    expect(await screen.findByText(/rank below everyone who has it/)).toBeInTheDocument();
    // the row wears the gate colour class (app.css .kind-hard_gate)
    expect(gateRow.closest(".req-card")!.className).toContain("kind-hard_gate");
    // a weighted row keeps its meter and shows no gate copy
    const mustRow = rowFor("Treasury accounting depth");
    expect(within(mustRow).getByLabelText("Importance 4 of 5")).toBeInTheDocument();
    expect(within(mustRow).queryByText("pass / fail")).not.toBeInTheDocument();
    expect(mustRow.closest(".req-card")!.className).toContain("kind-must_have");
  });

  it("toggling to Hard gate and back preserves the typed importance", async () => {
    setup([{ id: "r01", text: "Treasury accounting depth", type: "experience", kind: "must_have", importance: 4 }]);
    await screen.findByDisplayValue("Treasury accounting depth");
    expect(screen.getByText("High")).toBeInTheDocument(); // importance 4 label

    await userEvent.click(screen.getByRole("button", { name: "Hard gate" }));
    expect(screen.getByText("pass / fail")).toBeInTheDocument();
    expect(screen.queryByText("High")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Must-have" }));
    expect(screen.queryByText("pass / fail")).not.toBeInTheDocument();
    expect(screen.getByText("High")).toBeInTheDocument(); // the 4 survived the round trip
  });

  it("Publish is disabled until something changed, then sends the single ordered list with kinds", async () => {
    const api = setup(THREE_ROWS);
    await screen.findByDisplayValue("CA / CPA qualification");
    // the design's publish card: the next version is named on the button — but
    // nothing has changed yet, so it is NOT enabled (pilot feedback: it used to light
    // up on a mere click into a textbox, and people published no-op versions)
    const publish = screen.getByRole("button", { name: "Approve & publish v3" });
    expect(publish).toBeDisabled();
    expect(publish).toHaveAttribute("title", "Nothing changed since the published version.");

    // a REAL edit enables it
    await userEvent.type(screen.getByDisplayValue("Oracle EBS exposure"), " (SAP ok)");
    expect(publish).toBeEnabled();
    await userEvent.click(publish);

    await waitFor(() => {
      const puts = api.callsTo(`/api/roles/${REQ_ID}/rubric`).filter((c) => c.method === "PUT");
      expect(puts).toHaveLength(1);
      const body = puts[0].body as { requirements: Requirement[] };
      expect(Object.keys(body)).toEqual(["requirements"]); // no hard_filters key, ever
      expect(body.requirements.map((r) => [r.text, r.kind])).toEqual([
        ["CA / CPA qualification", "hard_gate"],
        ["Treasury accounting depth", "must_have"],
        ["Oracle EBS exposure (SAP ok)", "nice_to_have"],
      ]);
    });
  });
});
