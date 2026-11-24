// The sticky "whole formula" strip (v3 design, verbatim): every score control
// below tunes one of these pieces; the confidence read sits outside it.
import type { ScoringDoc } from "../../types";
import { fmtN } from "./model";

const chip = (bg: string, fg: string): React.CSSProperties => ({
  display: "inline-flex", padding: "4px 11px", borderRadius: 999,
  background: bg, color: fg, whiteSpace: "nowrap",
});

export default function FormulaStrip({ doc }: { doc: ScoringDoc }) {
  return (
    <div
      style={{
        position: "sticky", top: 0, zIndex: 25, background: "var(--white)",
        border: "1px solid var(--border-subtle)", borderRadius: 14,
        padding: "11px 18px", marginBottom: 14, boxShadow: "var(--shadow-sm)",
      }}
    >
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--ink-soft)", marginBottom: 8 }}>
        The whole formula — every score control below tunes one of these pieces · the confidence read sits outside it (display only)
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 12.5, fontWeight: 700 }}>
        <span>Final score =</span>
        <span style={chip("var(--brand-green-tint)", "var(--brand-green-deep)")}>Checklist base — pure math, no AI</span>
        <span>+</span>
        <span style={chip("var(--accent-blue-tint)", "#16729E")}>AI signals — AI-judged, you calibrate</span>
        <span>+</span>
        <span style={chip("var(--surface-warn)", "var(--warn-ink)")}>
          Preference nudges ±{fmtN(doc.preferences.per_preference_cap_points)}
        </span>
        <span style={{ color: "var(--ink-soft)" }}>
          → held {fmtN(doc.clamp.min)}–{fmtN(doc.clamp.max)} →
        </span>
        <span style={chip("var(--neutral-150)", "var(--ink)")}>
          Strong ≥ {fmtN(doc.bands.strong_at)} · Possible ≥ {fmtN(doc.bands.possible_at)} · else Weak
        </span>
      </div>
    </div>
  );
}
