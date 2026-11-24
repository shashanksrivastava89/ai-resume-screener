// Distance in the unit the reader thinks in (2026-09-12).
//
// Kilometres are CANONICAL and stay that way: `WorkLocation.radius_km`, the
// gazetteer, the haversine, `location_km` on every review card, the MCP results
// and the CLI are all km, and a stored number that changes meaning with the
// reader is a number nobody can audit. What changes is the DISPLAY: a recruiter
// in California thinks in miles, and asking them to convert 40 km in their head
// while deciding whether someone is local is a small tax on every row.
//
// The unit follows the REQUISITION (its hiring-rules pack), not the viewer's
// browser: two people reviewing the same California requisition should read the
// same number, and one of them may well be in India.

const KM_PER_MILE = 1.609344;

export type DistanceUnit = "km" | "mi";

/** Which unit this requisition's distances read in. */
export function unitsFor(pack: string | null | undefined): DistanceUnit {
  return (pack ?? "").startsWith("US") ? "mi" : "km";
}

export function kmToMi(km: number): number {
  return km / KM_PER_MILE;
}

export function miToKm(mi: number): number {
  return mi * KM_PER_MILE;
}

/** A distance for the eye: "≈ 25 mi". The approximation marker is deliberate —
 *  these are city-centre to city-centre great-circle distances at about 1 km of
 *  coordinate precision, and rounding them to the mile does not make them
 *  sharper. */
export function formatDistance(km: number | null | undefined,
                               unit: DistanceUnit): string {
  if (km === null || km === undefined) return "";
  const value = unit === "mi" ? kmToMi(km) : km;
  return `≈ ${Math.round(value)} ${unit}`;
}

/** A radius for a sentence: "25 mi". No approximation marker — a radius is a
 *  number someone CHOSE, not one we measured. */
export function formatRadius(km: number | null | undefined,
                             unit: DistanceUnit): string {
  if (km === null || km === undefined) return "";
  const value = unit === "mi" ? Math.round(kmToMi(km)) : km;
  return `${value} ${unit}`;
}

/** The ceilings a radius slider offers, in display units, as stored km.
 *  Round numbers in the unit on screen — 15/30/60 mi, not 15.5/31.1. */
export function ceilings(unit: DistanceUnit): number[] {
  return unit === "mi"
    ? [15, 30, 60, 150, 300].map((mi) => Math.round(miToKm(mi)))
    : [25, 50, 100, 250, 500];
}
