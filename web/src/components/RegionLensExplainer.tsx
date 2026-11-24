// What changes when you switch region — and what does not (2026-09-12).
//
// Region and jurisdiction are two different lenses over the same requisition and
// they are easy to confuse: one decides WHO CAN SEE a requisition, the other
// decides WHICH RULES apply to it. Someone switching the picker could reasonably
// fear they have just changed how candidates are scored, or which law the app
// thinks applies. They have not, and the app should say so where the switch is
// rather than in a document nobody opens.
import { InfoDot } from "./ui";

/** Exported as data so a test pins the claims rather than the markup. */
export const REGION_LENS_COPY = {
  changes: [
    "Which requisitions you see in the list.",
    "Which requisitions appear in Audit & History, and in reports.",
  ],
  neverChanges: [
    "The hiring rules — those follow each requisition's own job location, not your lens.",
    "How candidates are scored — one scoring model, everywhere.",
    "What you are allowed to do — your permissions come from your role.",
    "Approvals and notifications.",
    "How long records are kept.",
  ],
  note: "Optional features that are set per region follow the requisition's region, not the one you are looking through.",
};

function Section({ title, items }: { title: string; items: string[] }) {
  return (
    <>
      <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--ink-muted)", margin: "8px 0 4px" }}>
        {title}
      </div>
      <ul style={{ margin: 0, paddingLeft: 16, display: "flex", flexDirection: "column", gap: 3, fontSize: 12.5, lineHeight: 1.45 }}>
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </>
  );
}

export default function RegionLensInfo() {
  return (
    <InfoDot
      label="What switching region changes"
      side="top"
      content={
        <div style={{ maxWidth: 320 }}>
          <Section title="Changes" items={REGION_LENS_COPY.changes} />
          <Section title="Never changes" items={REGION_LENS_COPY.neverChanges} />
          <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 8, lineHeight: 1.45 }}>
            {REGION_LENS_COPY.note}
          </div>
        </div>
      }
    />
  );
}
