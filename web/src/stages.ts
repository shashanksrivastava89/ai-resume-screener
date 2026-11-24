// Stage chip colors for the six-step requisition lifecycle (P32). Each stage
// gets its OWN hue so a mixed dashboard reads at a glance: gray = not started,
// violet → blue → teal = the work moving forward, amber = waiting on a human
// decision (the one "needs-a-look" use), green = done.
export const STAGE_CHIP: Record<string, { bg: string; fg: string; label: string }> = {
  // fixed light bg + dark fg (like the other stages) — a flipping token bg with a
  // fixed dark fg read as dark-on-dark in dark mode.
  // 2026-09-05: the semantic hues (tokens.css). Both ink and tint are tokens that
  // flip together in dark mode, which is what the fixed-hex workaround above was
  // guarding against. The lifecycle reads left-to-right as a colour progression:
  // slate (nothing yet) → teal (configured) → blue (material in) → orange (being
  // worked on) → violet (with people) → green (done).
  draft_checklist: { bg: "var(--hue-slate-tint)", fg: "var(--hue-slate-ink)", label: "Draft checklist" },
  checklist_approved: { bg: "var(--hue-teal-tint)", fg: "var(--hue-teal-ink)", label: "Checklist approved" },
  candidates_added: { bg: "var(--hue-blue-tint)", fg: "var(--hue-blue-ink)", label: "Candidates added" },
  screened: { bg: "var(--hue-orange-tint)", fg: "var(--hue-orange-ink)", label: "Screened" },
  in_review: { bg: "var(--hue-violet-tint)", fg: "var(--hue-violet-ink)", label: "In review" },
  complete: { bg: "var(--hue-green-tint)", fg: "var(--hue-green-ink)", label: "Complete" },
};
