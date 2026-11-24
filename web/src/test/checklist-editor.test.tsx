// The design-pass checklist editor: one card per requirement (text · kind pill ·
// importance), an auto-growing textarea that swallows Enter, session-only undo
// for deleted rows, and the "Check & add" row that dry-runs the bias critic
// (ok adds · warn asks · blocked stops).
//
// Since 2026-08-20 the rows are GROUPED into Skills / Experience / Qualifications
// (derived from each row's `type`), and the single bottom add-row became one
// collapsed add-row per group — opening it is what supplies the new row's category.
// The flat ordered list is still the contract; grouping is a view over it.
import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChecklistEditor, { type CheckVerdict } from "../components/ChecklistEditor";
import type { Requirement } from "../types";

// A tiny controlled harness — the editor is controlled, so a wrapper owns state.
function Harness({ initial, resetSignal, onCheckText }: {
  initial: Requirement[]; resetSignal?: unknown;
  onCheckText?: (text: string) => Promise<CheckVerdict>;
}) {
  const [reqs, setReqs] = useState(initial);
  return <ChecklistEditor requirements={reqs} onChange={setReqs} resetSignal={resetSignal} onCheckText={onCheckText} />;
}

const ROWS: Requirement[] = [
  { text: "US GAAP experience", kind: "must_have", importance: 5, type: "other" },
  { text: "Oracle EBS exposure", kind: "nice_to_have", importance: 2, type: "other" },
  { text: "Team leadership", kind: "nice_to_have", importance: 3, type: "other" },
];

// The add-row is collapsed per group since 2026-08-20 — open it, then type.
async function openAddRow(group = /Add other requirement/i) {
  await userEvent.click(screen.getByRole("button", { name: group }));
}

// Row textareas only (the add row is an <input>, not a textarea).
function values(): string[] {
  return screen.getAllByRole("textbox")
    .filter((el) => el.tagName === "TEXTAREA")
    .map((el) => (el as HTMLTextAreaElement).value);
}

describe("ChecklistEditor", () => {
  it("renders one numbered card per row with the kind pill; plain add without a checker", async () => {
    render(<Harness initial={ROWS} />);
    expect(values()).toEqual(["US GAAP experience", "Oracle EBS exposure", "Team leadership"]);
    // each card carries the three-state kind pill + its serial number
    expect(screen.getAllByRole("group", { name: "Requirement kind" })).toHaveLength(3);
    // serial numbers still count across the WHOLE checklist, not per group
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    // no onCheckText (the wizard before the req exists) → a plain add button, reached
    // by opening the group's add-row first
    await userEvent.click(screen.getByRole("button", { name: /Add other requirement/i }));
    expect(screen.getByRole("button", { name: "Add requirement" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check & add" })).not.toBeInTheDocument();
  });

  it("uses a textarea that ignores Enter (a requirement stays one line)", async () => {
    render(<Harness initial={[ROWS[0]]} />);
    const box = screen.getByDisplayValue("US GAAP experience") as HTMLTextAreaElement;
    expect(box.tagName).toBe("TEXTAREA");
    box.focus();
    await userEvent.keyboard("{Enter}");
    expect(box.value).toBe("US GAAP experience"); // no newline inserted
  });

  it("undoes a deleted row, restoring it at its original index", async () => {
    render(<Harness initial={ROWS} />);
    // delete row 2 (Oracle EBS)
    const row2 = screen.getByDisplayValue("Oracle EBS exposure").closest("div")!.parentElement!;
    await userEvent.click(within(row2).getByRole("button", { name: "Remove requirement" }));
    expect(values()).toEqual(["US GAAP experience", "Team leadership"]);
    // an undo bar appears
    const bar = screen.getByText(/Row 2 removed/);
    await userEvent.click(within(bar.closest("div")!).getByRole("button", { name: "Undo" }));
    // restored in the middle, not appended
    expect(values()).toEqual(["US GAAP experience", "Oracle EBS exposure", "Team leadership"]);
    expect(screen.queryByText(/Row 2 removed/)).not.toBeInTheDocument();
  });

  it("stacks multiple undo bars, newest first", async () => {
    render(<Harness initial={ROWS} />);
    // delete row 1 then row 1 again (now 'Oracle EBS')
    await userEvent.click(screen.getAllByRole("button", { name: "Remove requirement" })[0]);
    await userEvent.click(screen.getAllByRole("button", { name: "Remove requirement" })[0]);
    expect(screen.getByText(/Row 1 removed.*Oracle EBS/)).toBeInTheDocument();
    expect(screen.getByText(/Row 1 removed.*US GAAP/)).toBeInTheDocument();
  });

  it("clears pending undo bars when resetSignal changes", async () => {
    const { rerender } = render(<Harness initial={ROWS} resetSignal={0} />);
    await userEvent.click(screen.getAllByRole("button", { name: "Remove requirement" })[0]);
    expect(screen.getByText(/removed/)).toBeInTheDocument();
    rerender(<Harness initial={ROWS} resetSignal={1} />);
    expect(screen.queryByText(/removed/)).not.toBeInTheDocument();
  });

  it("Check & add: a clean row adds directly", async () => {
    const check = vi.fn().mockResolvedValue({ level: "ok", reason: "" });
    render(<Harness initial={[ROWS[0]]} onCheckText={check} />);
    await openAddRow();
    await userEvent.type(screen.getByRole("textbox", { name: /Add a Other requirement/i }), "SAP FICO exposure");
    await userEvent.click(screen.getByRole("button", { name: "Check & add" }));
    expect(check).toHaveBeenCalledWith("SAP FICO exposure");
    expect(await screen.findByDisplayValue("SAP FICO exposure")).toBeInTheDocument();
  });

  it("Check & add: warn asks first, then adds flagged", async () => {
    const check = vi.fn().mockResolvedValue({
      level: "warn", reason: "Tier-1 institute filters can proxy socioeconomic background.",
    });
    render(<Harness initial={[ROWS[0]]} onCheckText={check} />);
    await openAddRow();
    await userEvent.type(screen.getByRole("textbox", { name: /Add a Other requirement/i }), "Tier-1 institute only");
    await userEvent.click(screen.getByRole("button", { name: "Check & add" }));
    // the design's confirm: proceed knowingly → the row lands with the amber strip
    expect(await screen.findByText("This needs HR Head approval")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Proceed — needs approval" }));
    expect(await screen.findByDisplayValue("Tier-1 institute only")).toBeInTheDocument();
    expect(screen.getByText(/Needs HR Head approval at publish/)).toBeInTheDocument();
  });

  it("Check & add: blocked is a hard stop (no proceed button)", async () => {
    const check = vi.fn().mockResolvedValue({
      level: "blocked", reason: "Age is a protected attribute.",
    });
    render(<Harness initial={[ROWS[0]]} onCheckText={check} />);
    await openAddRow();
    await userEvent.type(screen.getByRole("textbox", { name: /Add a Other requirement/i }), "Under 35 preferred");
    await userEvent.click(screen.getByRole("button", { name: "Check & add" }));
    // 2026-09-07: the copy states the rule; it never suggests a rewording that
    // would carry the same intent past it.
    expect(await screen.findByText("This can't go on the checklist")).toBeInTheDocument();
    expect(screen.queryByText(/[Rr]eword/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Proceed/ })).not.toBeInTheDocument();
    // cancel — no ROW added (the text stays in the add input for rewording)
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(values()).toEqual(["US GAAP experience"]);
  });
});

describe("ChecklistEditor grouping (2026-08-20)", () => {
  const MIXED: Requirement[] = [
    { text: "Ind AS reporting", kind: "must_have", importance: 5, type: "skill" },
    { text: "5+ years in listed-company finance", kind: "must_have", importance: 5, type: "experience" },
    { text: "CA qualified", kind: "hard_gate", importance: 3, type: "certification" },
    { text: "Conversational Hindi", kind: "nice_to_have", importance: 2, type: "language" },
    { text: "Led a statutory audit", kind: "nice_to_have", importance: 3, type: "achievement" },
    { text: "MBA Finance", kind: "nice_to_have", importance: 2, type: "education" },
  ];

  it("files each row under one of the three headings, derived from its type", () => {
    render(<Harness initial={MIXED} />);
    for (const h of ["Skills", "Experience", "Qualifications"]) {
      expect(screen.getByText(h)).toBeInTheDocument();
    }
    // skill + language -> Skills; experience + achievement -> Experience;
    // certification + education -> Qualifications. Two rows each.
    expect(screen.getAllByText("2 requirements")).toHaveLength(3);
    // nothing unclassifiable here, so no fourth heading
    expect(screen.queryByText("Other")).not.toBeInTheDocument();
  });

  it("numbers rows across the WHOLE checklist, not within each group", () => {
    render(<Harness initial={MIXED} />);
    // 6 rows -> serials 1..6 exist exactly once each, even though they are split
    // across three sections and the groups reorder them on screen.
    for (const n of ["1", "2", "3", "4", "5", "6"]) {
      expect(screen.getByText(n)).toBeInTheDocument();
    }
  });

  it("shows Other only when a row does not classify, and never hides it", () => {
    render(<Harness initial={[...MIXED, { text: "Willing to travel", kind: "nice_to_have", importance: 2, type: "other" }]} />);
    expect(screen.getByText("Other")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Willing to travel")).toBeInTheDocument();
  });

  it("adding under a heading files the row into THAT group", async () => {
    render(<Harness initial={MIXED} />);
    await userEvent.click(screen.getByRole("button", { name: /Add qualifications requirement/i }));
    await userEvent.type(
      screen.getByRole("textbox", { name: /Add a Qualifications requirement/i }),
      "NISM Series-V A",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add requirement" }));
    // Qualifications went from 2 to 3 rows; the other two groups are untouched.
    expect(await screen.findByDisplayValue("NISM Series-V A")).toBeInTheDocument();
    expect(screen.getByText("3 requirements")).toBeInTheDocument();
    expect(screen.getAllByText("2 requirements")).toHaveLength(2);
  });

  it("an empty group explains what belongs in it rather than vanishing", () => {
    render(<Harness initial={[MIXED[0]]} />);   // a Skills row only
    expect(screen.getByText("1 requirement")).toBeInTheDocument();
    expect(screen.getByText(/Time spent doing the work/)).toBeInTheDocument();
    expect(screen.getByText(/Degrees, professional qualifications/)).toBeInTheDocument();
  });
});
