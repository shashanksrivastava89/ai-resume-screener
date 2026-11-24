// Distance in the unit the reader thinks in (2026-09-12).
//
// Kilometres are CANONICAL: `radius_km`, the gazetteer, the haversine, every
// review card's `location_km`, the MCP results and the CLI. A stored number that
// changes meaning with the reader is a number nobody can audit. Only the DISPLAY
// follows the requisition's market — and it follows the REQUISITION, not the
// viewer's browser, because two people reviewing the same California role should
// read the same number and one of them may be in India.
import { describe, it, expect } from "vitest";
import { ceilings, formatDistance, formatRadius, kmToMi, miToKm, unitsFor } from "../lib/units";

describe("distance units", () => {
  it("reads miles for a US requisition and kilometres everywhere else", () => {
    expect(unitsFor("US-CA")).toBe("mi");
    expect(unitsFor("US-default")).toBe("mi");
    expect(unitsFor("US-NYC")).toBe("mi");
    expect(unitsFor("EU")).toBe("km");
    expect(unitsFor("IN")).toBe("km");
    // a requisition that predates the country picker still reads in km
    expect(unitsFor(null)).toBe("km");
  });

  it("round-trips a radius without drifting", () => {
    for (const km of [8, 25, 40, 100, 250]) {
      expect(Math.round(miToKm(Math.round(kmToMi(km))))).toBeCloseTo(km, -1);
    }
  });

  it("marks a measured distance as approximate and a chosen one as exact", () => {
    // measured: city-centre to city-centre, great-circle, ~1 km precision
    expect(formatDistance(40, "mi")).toBe("≈ 25 mi");
    expect(formatDistance(40, "km")).toBe("≈ 40 km");
    // chosen: a number someone picked
    expect(formatRadius(40, "mi")).toBe("25 mi");
    expect(formatRadius(40, "km")).toBe("40 km");
  });

  it("says nothing when the distance is unknown", () => {
    // an unknown distance never reads as a number, and never hides anyone
    expect(formatDistance(null, "mi")).toBe("");
    expect(formatDistance(undefined, "km")).toBe("");
  });

  it("offers round ceilings in the unit on screen", () => {
    expect(ceilings("km")).toEqual([25, 50, 100, 250, 500]);
    // 15/30/60/150/300 miles — round to the reader, stored as their km equivalent
    expect(ceilings("mi").map((km) => Math.round(kmToMi(km)))).toEqual([15, 30, 60, 150, 300]);
  });
});
