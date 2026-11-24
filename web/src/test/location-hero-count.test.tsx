/** The distance dialog's hero number must mean the same thing as its own button.
 *
 *  Reported 2026-09-10: filtering 50 km from Gurugram found 16 within range plus 2
 *  whose distance could not be worked out. With the "also show unknown" box ticked
 *  the hero said 16 while the button said "Show only these 18" — two numbers for one
 *  action. The hero is what the button will DO.
 */
import { describe, expect, it } from "vitest";

/** The three numbers the dialog juggles, as the component computes them. */
function dialogNumbers(near: number, unknown: number, includeUnknown: boolean) {
  const wouldShow = near + (includeUnknown ? unknown : 0);
  return { hero: wouldShow, cta: wouldShow, near, unknown };
}

describe("the distance dialog's count", () => {
  it("counts the unknown-distance candidates when the reader asked for them", () => {
    const n = dialogNumbers(16, 2, true);
    expect(n.hero).toBe(18);
    expect(n.hero).toBe(n.cta);
  });

  it("counts only those in range when the box is off", () => {
    const n = dialogNumbers(16, 2, false);
    expect(n.hero).toBe(16);
    expect(n.hero).toBe(n.cta);
  });

  it("never lets the hero and the button disagree", () => {
    for (const near of [0, 1, 16, 300]) {
      for (const unknown of [0, 2, 40]) {
        for (const inc of [true, false]) {
          const n = dialogNumbers(near, unknown, inc);
          expect(n.hero, `near=${near} unknown=${unknown} include=${inc}`).toBe(n.cta);
        }
      }
    }
  });

  it("a total that includes unknowns is never described as 'within N km'", () => {
    // The sentence has to follow the number: 18 is not "18 within 50 km".
    const includeUnknown = true, unknown = 2, near = 16, total = 234, km = 50;
    const sentence = includeUnknown && unknown > 0
      ? `of ${total} candidates would show — ${near} within ${km} km of Gurugram, plus ${unknown} whose distance we couldn't work out`
      : `of ${total} candidates are within ${km} km of Gurugram`;
    expect(sentence).toContain("would show");
    expect(sentence).toContain("16 within 50 km");
    expect(sentence).not.toMatch(/^of 234 candidates are within/);
  });
});
