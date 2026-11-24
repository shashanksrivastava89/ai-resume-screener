import { useEffect, useState } from "react";
import { IconPin, IconRadius, IconInfo } from "./icons";
import { Tip } from "./ui";
import type { WorkLocation } from "../types";
import { ceilings, kmToMi, miToKm, unitsFor } from "../lib/units";

// The job location + how far from it the recruiter is willing to look.
//
// WHY IT LOOKS LIKE THIS. The obvious build is a checklist requirement reading
// "Current location: within 25 km of Gurugram" — and that is exactly what it must not
// be. `commute_or_address` is a FLOOR never-scored topic (CLAUDE.md §6.6): commute
// distance correlates with income, and in India with community, so scoring it launders a
// protected attribute into a practical-looking number. Removing that floor takes a
// SuperAdmin structural proposal plus a second leader.
//
// So this control captures the same intent in the one place it is legitimate: as
// requisition data the reviewer SEES, next to what each résumé states, and decides on.
// It never becomes a weight, a gate or a score. The critic independently flags a
// checklist row phrased this way and routes it to HR-Head approval, so the scored path
// stays closed while this one stays open.
//
// UX decisions worth keeping:
//  * The label is generated, so the number and the place can never disagree —
//    "Within 25 km from “Gurugram”" is one string built from two fields.
//  * Range AND number together: the slider is for "roughly how far", the number box for
//    "exactly 25", and recruiters reach for both.
//  * The ceiling is itself adjustable, because 100 km is absurd for Mumbai and tight for
//    a territory sales role. Raising it never silently moves the chosen distance.
//  * 0 km is a real answer ("at this site only") and reads as such, not "within 0 km".

export default function LocationRadius({
  value,
  onChange,
  disabled = false,
  mode = "full",
  pack = null,
}: {
  value: WorkLocation | null;
  /** null clears the location entirely. */
  onChange: (next: WorkLocation | null) => void;
  disabled?: boolean;
  /**
   * Which parts are editable. Split out 2026-08-26 so each surface offers exactly
   * what belongs there:
   *  - "readonly": Overview. The place comes from the job description, so showing an
   *    editable box invited people to retype what the JD already said.
   *  - "radius": the Review filter. The distance is a REVIEWER's question ("how far
   *    am I willing to look?"), so it is adjusted where it is used, next to the
   *    filter it drives, instead of on another tab.
   *  - "full": place + distance together (the create wizard).
   */
  mode?: "full" | "readonly" | "radius";
  /**
   * The requisition's hiring-rules pack, which decides the DISPLAY unit: a
   * Californian recruiter thinks in miles, and converting 40 km in your head on
   * every row is a small tax on every decision (2026-09-12). Kilometres stay
   * canonical — `radius_km` is what is stored and what the engine measures — so a
   * stored number never changes meaning with the reader. The unit follows the
   * REQUISITION rather than the viewer, because two people reviewing the same
   * California role should read the same number and one of them may be in India.
   */
  pack?: string | null;
}) {
  const unit = unitsFor(pack);
  const [place, setPlace] = useState(value?.place ?? "");
  const [radius, setRadius] = useState<number | null>(value?.radius_km ?? null);
  const [maxKm, setMaxKm] = useState(value?.max_km ?? 100);

  // Re-sync when the requisition reloads under us (a save elsewhere, a tab switch).
  useEffect(() => {
    setPlace(value?.place ?? "");
    setRadius(value?.radius_km ?? null);
    setMaxKm(value?.max_km ?? 100);
  }, [value?.place, value?.radius_km, value?.max_km]);

  const commit = (next: { place?: string; radius?: number | null; maxKm?: number }) => {
    const p = (next.place ?? place).trim();
    const r = next.radius === undefined ? radius : next.radius;
    const m = next.maxKm ?? maxKm;
    onChange(p ? { place: p, radius_km: r === null ? null : Math.min(r, m), max_km: m } : null);
  };

  // The single source of the sentence — mirrors WorkLocation.summary() server-side.
  const sentence = !place.trim()
    ? "Add the job location to offer a distance filter in Review."
    : radius === null
      ? `Job location: “${place.trim()}”`
      : radius === 0
        ? `Current location: at “${place.trim()}”`
        : `Current location: Within ${radius} km from “${place.trim()}”`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <IconPin size={15} style={{ color: "var(--brand-green-deep)", flex: "0 0 auto" }} />
        <strong style={{ fontSize: 13.5 }}>Job location &amp; distance</strong>
        <Tip content="Recruiter context only. This is never scored and never rejects anyone — commute and address are on the never-scored list, because distance from the office tracks income and community. It gives you a filter in Review and a line beside each candidate; the judgement stays yours.">
          <span style={{ display: "inline-flex", color: "var(--ink-muted)", cursor: "help" }}>
            <IconInfo size={14} />
          </span>
        </Tip>
      </div>

      {mode === "full" && (
      <label style={{ display: "flex", flexDirection: "column", gap: 5, maxWidth: 340 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--ink-soft)" }}>
          Where the job is
        </span>
        <input
          value={place}
          disabled={disabled}
          onChange={(e) => setPlace(e.target.value)}
          onBlur={() => commit({})}
          placeholder="e.g. Gurugram"
          aria-label="Job location place name"
          className="input"
          style={{ fontSize: 13, padding: "8px 11px" }}
        />
      </label>
      )}
      {mode === "readonly" && place.trim() && (
        <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
          Read from the job description. Set the distance you want to search on the
          Review tab, where it filters the list.
        </div>
      )}

      {place.trim() && mode !== "readonly" && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <IconRadius size={15} style={{ color: "var(--ink-muted)", flex: "0 0 auto" }} />
            <input
              type="range"
              min={0}
              max={unit === "mi" ? Math.round(kmToMi(maxKm)) : maxKm}
              step={1}
              disabled={disabled}
              value={unit === "mi" ? Math.round(kmToMi(radius ?? 0)) : (radius ?? 0)}
              aria-label={`Distance from ${place.trim()} in ${unit === "mi" ? "miles" : "kilometres"}, 0 to ${unit === "mi" ? Math.round(kmToMi(maxKm)) : maxKm}`}
              onChange={(e) => {
                const shown = Number(e.target.value);
                setRadius(unit === "mi" ? Math.round(miToKm(shown)) : shown);
              }}
              onMouseUp={() => commit({})}
              onKeyUp={() => commit({})}
              onTouchEnd={() => commit({})}
              style={{ flex: "1 1 180px", minWidth: 140, accentColor: "var(--brand-green)" }}
            />
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <input
                type="number"
                min={0}
                max={unit === "mi" ? Math.round(kmToMi(maxKm)) : maxKm}
                disabled={disabled}
                value={radius === null ? "" : (unit === "mi" ? Math.round(kmToMi(radius)) : radius)}
                placeholder="—"
                aria-label={`Distance in ${unit === "mi" ? "miles" : "kilometres"}`}
                onChange={(e) => {
                  const raw = e.target.value;
                  if (raw === "") return setRadius(null);
                  const shown = Math.max(0, Number(raw));
                  const km = unit === "mi" ? Math.round(miToKm(shown)) : shown;
                  setRadius(Math.min(maxKm, km));
                }}
                onBlur={() => commit({})}
                style={{ width: 68, height: 32, textAlign: "right", border: "1px solid var(--border-strong)", borderRadius: 8, padding: "0 8px", fontSize: 13, fontFamily: "inherit", background: "var(--white)", color: "var(--ink)" }}
              />
              <span style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>{unit}</span>
            </label>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>Range up to</span>
            {ceilings(unit).map((c) => (
              <button
                key={c}
                disabled={disabled}
                onClick={() => {
                  setMaxKm(c);
                  // Raising the ceiling must never move the chosen distance; lowering it
                  // clamps, because a radius above its own maximum is not a state the
                  // slider can render.
                  const clamped = radius === null ? null : Math.min(radius, c);
                  setRadius(clamped);
                  commit({ maxKm: c, radius: clamped });
                }}
                aria-pressed={maxKm === c}
                style={{
                  height: 24, padding: "0 10px", borderRadius: 999, cursor: "pointer",
                  fontFamily: "inherit", fontSize: 11, fontWeight: 700,
                  border: maxKm === c ? "1px solid var(--brand-green)" : "1px solid var(--border-subtle)",
                  background: maxKm === c ? "var(--brand-green-tint)" : "var(--white)",
                  color: maxKm === c ? "var(--brand-green-deep)" : "var(--ink-soft)",
                }}
              >
                {c} km
              </button>
            ))}
            {radius !== null && (
              <button
                disabled={disabled}
                onClick={() => { setRadius(null); commit({ radius: null }); }}
                style={{ marginLeft: "auto", background: "none", border: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 11.5, color: "var(--ink-muted)", textDecoration: "underline", textUnderlineOffset: 2 }}
              >
                No distance limit
              </button>
            )}
          </div>
        </>
      )}

      {/* "radius" is the Review filter dialog, where the count above the control
          already says what this distance will DO — repeating it as a sentence and
          then explaining the feature underneath is reading, not doing (2026-09-11,
          pilot ask 4). The wizard and Overview still carry both: there the sentence
          IS the requisition's stored line, and the caveat is new information. */}
      {mode !== "radius" && (
        <p
          data-testid="location-sentence"
          style={{ margin: 0, fontSize: 12.5, fontWeight: place.trim() ? 700 : 400, color: place.trim() ? "var(--ink)" : "var(--ink-soft)" }}
        >
          {sentence}
        </p>
      )}
      {place.trim() && mode === "full" && (
        <p style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, color: "var(--ink-muted)" }}>
          Shown beside each candidate in Review and available as a filter there. It never
          changes a score and never rejects anyone.
        </p>
      )}
    </div>
  );
}
