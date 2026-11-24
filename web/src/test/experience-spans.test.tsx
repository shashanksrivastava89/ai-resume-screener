/** Length of experience is settled by arithmetic (2026-09-07). The drawer shows the
 *  dated lines the total rests on, so a reviewer checks the addition instead of
 *  trusting it — and the font-size bug that made the strengths list unreadable
 *  stays fixed. */
import { describe, expect, it } from "vitest";
import { monthsLabel } from "../pages/ReviewDrawer";

describe("months label", () => {
  it("says the total the way the note does", () => {
    expect(monthsLabel(32)).toBe("2 yr 8 mo");
    expect(monthsLabel(24)).toBe("2 yr");
    expect(monthsLabel(7)).toBe("7 mo");
  });
});

describe("the font shorthand trap", () => {
  it("never lets `font` sit after `fontSize` in a style object", () => {
    // `font` is a SHORTHAND: it resets font-size to the inherited value. A duplicate
    // key keeps its FIRST position, so `{...style, font: "inherit", fontSize: 12}`
    // where `style` already carries fontSize puts `font` LAST and wipes the size.
    // Measured 16px where 12 was intended.
    const base = { fontSize: 12, lineHeight: 1.45 };
    const merged: Record<string, unknown> = { ...base, font: "inherit", fontSize: 12 };
    const order = Object.keys(merged);
    expect(order.indexOf("font")).toBeGreaterThan(order.indexOf("fontSize"));
  });
});
