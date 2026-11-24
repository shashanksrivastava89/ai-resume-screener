// Thin-line icons (stroke 1.7–2, rounded joins) transcribed from the design.
// Single source so pages stay readable.
import type { CSSProperties } from "react";

interface IconProps {
  size?: number;
  className?: string;
  style?: CSSProperties;
  strokeWidth?: number;
}

function Svg({
  size = 18,
  strokeWidth = 1.7,
  className,
  style,
  children,
}: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export const IconRoles = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="14" width="7" height="7" rx="1.5" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" />
  </Svg>
);

export const IconScreen = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 7V5a2 2 0 0 1 2-2h2" />
    <path d="M17 3h2a2 2 0 0 1 2 2v2" />
    <path d="M21 17v2a2 2 0 0 1-2 2h-2" />
    <path d="M7 21H5a2 2 0 0 1-2-2v-2" />
    <circle cx="11" cy="11" r="3.2" />
    <path d="m15 15 2.5 2.5" />
  </Svg>
);

export const IconReview = (p: IconProps) => (
  <Svg {...p}>
    <rect x="4" y="4" width="16" height="17" rx="2.5" />
    <path d="M9 3.5h6a1 1 0 0 1 1 1V6a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1z" />
    <path d="m9 13 2 2 4-4" />
  </Svg>
);

export const IconRules = (p: IconProps) => (
  <Svg {...p}>
    <line x1="4" y1="7" x2="14" y2="7" />
    <line x1="18" y1="7" x2="20" y2="7" />
    <circle cx="16" cy="7" r="2.3" />
    <line x1="4" y1="17" x2="8" y2="17" />
    <line x1="12" y1="17" x2="20" y2="17" />
    <circle cx="10" cy="17" r="2.3" />
  </Svg>
);

export const IconGlobe = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18" />
    <path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18z" />
  </Svg>
);

export const IconAccess = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="9" cy="8" r="3.2" />
    <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
    <path d="M17 8h4" />
    <path d="M17 12h4" />
    <path d="M19 16h2" />
  </Svg>
);

export const IconRuns = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 4v16h16" />
    <path d="M8 16l3.5-4 2.5 2.5L19 8" />
  </Svg>
);

export const IconComment = (p: IconProps) => (
  <Svg {...p}>
    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
  </Svg>
);

export const IconFlag = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 21V4" />
    <path d="M5 4h11l-2 3.5L16 11H5" />
  </Svg>
);

export const IconSearch = (p: IconProps) => (
  <Svg strokeWidth={2} {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.2-3.2" />
  </Svg>
);

export const IconPlus = (p: IconProps) => (
  <Svg strokeWidth={2} {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

export const IconChevronRight = (p: IconProps) => (
  <Svg strokeWidth={2} {...p}>
    <path d="m9 18 6-6-6-6" />
  </Svg>
);

export const IconChevronDown = (p: IconProps) => (
  <Svg strokeWidth={2} {...p}>
    <path d="m6 9 6 6 6-6" />
  </Svg>
);

export const IconBack = (p: IconProps) => (
  <Svg strokeWidth={2} {...p}>
    <path d="m15 18-6-6 6-6" />
  </Svg>
);

export const IconCheck = (p: IconProps) => (
  <Svg strokeWidth={2.4} {...p}>
    <path d="M20 6 9 17l-5-5" />
  </Svg>
);

export const IconX = (p: IconProps) => (
  <Svg strokeWidth={2} {...p}>
    <path d="M18 6 6 18M6 6l12 12" />
  </Svg>
);

export const IconAlert = (p: IconProps) => (
  <Svg strokeWidth={2} {...p}>
    <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    <path d="M12 9v4" />
    <path d="M12 17h.01" />
  </Svg>
);

export const IconInfo = (p: IconProps) => (
  <Svg strokeWidth={2} {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 8v5" />
    <path d="M12 16h.01" />
  </Svg>
);

export const IconFile = (p: IconProps) => (
  <Svg {...p}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6" />
  </Svg>
);

export const IconDownload = (p: IconProps) => (
  <Svg strokeWidth={2} {...p}>
    <path d="M12 3v12" />
    <path d="m7 10 5 5 5-5" />
    <path d="M5 21h14" />
  </Svg>
);

export const IconBriefcase = (p: IconProps) => (
  <Svg strokeWidth={1.8} {...p}>
    <rect x="3" y="7" width="18" height="13" rx="2" />
    <path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
  </Svg>
);

export const IconLock = (p: IconProps) => (
  <Svg strokeWidth={1.8} {...p}>
    <rect x="4" y="11" width="16" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 8 0v4" />
  </Svg>
);

export const IconChecklist = (p: IconProps) => (
  <Svg strokeWidth={1.8} {...p}>
    <path d="m9 11 3 3 8-8" />
    <path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9" />
  </Svg>
);

// Filled sparkle for AI/DI "magic" actions.
export const IconSparkle = ({ size = 16, style }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" style={style} aria-hidden="true">
    <path d="M12 2.5l1.6 4.9 4.9 1.6-4.9 1.6L12 15.5l-1.6-4.9L5.5 9l4.9-1.6z" />
  </svg>
);

// P51: the Scoring nav item — a gauge (half-arc + needle).
export const IconGauge = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 14a8 8 0 0 1 16 0" />
    <path d="M12 14l3.5-4" />
    <circle cx="12" cy="14" r="1" />
    <path d="M4 18h16" />
  </Svg>
);

// P51: the stale-score marker — a clock with a counter-clockwise refresh arrow.
export const IconOutdated = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4.6 7A8.5 8.5 0 1 1 3.5 12" />
    <path d="M3.5 6.5v4h4" />
    <path d="M12 8.5v4l2.6 1.6" />
  </Svg>
);

// --- the v3 design's nav icons (paths transcribed verbatim from the prototype) ------

// My preferences — a person with rays (the design's goPrefs icon).
export const IconPrefsUser = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3v3" />
    <path d="M18.5 5.5 16.4 7.6" />
    <path d="M21 12h-3" />
    <path d="M6 12H3" />
    <path d="M7.6 7.6 5.5 5.5" />
    <circle cx="12" cy="13" r="4" />
    <path d="M8 21a6 6 0 0 1 8 0" />
  </Svg>
);

// Audit & History — a clock (the design's goAudit icon).
export const IconClockAudit = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 3" />
  </Svg>
);

// Approvals — a check breaking out of a box (the design's goApprovals icon).
export const IconApprovals = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 6 9 17l-5-5" />
    <path d="M20 12v7a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h9" />
  </Svg>
);

// Scoring model — vertical slider bars with tick handles (the design's goModel icon).
export const IconSliders = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 21v-7" />
    <path d="M4 10V3" />
    <path d="M12 21v-9" />
    <path d="M12 8V3" />
    <path d="M20 21v-5" />
    <path d="M20 12V3" />
    <path d="M2 14h4" />
    <path d="M10 8h4" />
    <path d="M18 16h4" />
  </Svg>
);

// --- 2026-08-20 additions -------------------------------------------------------
// The app had 30 icons but leaned on bare unicode (✕ ⚠ ✓ ⏱ ✨) in a dozen places,
// which is why it read as "no glyphs or icons": unicode inherits no stroke weight,
// sits on a different baseline per font, and cannot take currentColor consistently.
// These cover the remaining glyph sites plus the checklist groups and the new
// location control.

/** Skills group — a wrench/tool: an ability you apply. */
export const IconSkill = (p: IconProps) => (
  <Svg {...p}>
    <path d="M14.7 6.3a4 4 0 1 0 5 5L21 21H3l9.7-9.7a4 4 0 0 1 2-5Z" />
  </Svg>
);

/** Experience group — a timeline: time spent doing the work. */
export const IconExperience = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 12h18" />
    <circle cx="7" cy="12" r="2.2" />
    <circle cx="16" cy="12" r="2.2" />
    <path d="M7 9.8V5m9 4.2V5M7 14.2V19m9-4.8V19" />
  </Svg>
);

/** Qualifications group — a certificate rosette (degrees + certifications). */
export const IconQualification = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="9" r="5.5" />
    <path d="M8.5 13.6 7 21l5-2.5L17 21l-1.5-7.4" />
  </Svg>
);

/** A map pin — the job-location / radius control. */
export const IconPin = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11Z" />
    <circle cx="12" cy="10" r="2.6" />
  </Svg>
);

/** Concentric rings — "within N km of", distinct from the plain pin. */
export const IconRadius = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="2" />
    <circle cx="12" cy="12" r="6" strokeDasharray="2.5 2.5" />
    <circle cx="12" cy="12" r="10" strokeDasharray="2.5 3.5" />
  </Svg>
);

/** A clock — replaces the ⏱ in the "Scores stale" chip. */
export const IconClock = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5V12l3.2 2" />
  </Svg>
);

/** A trash can — replaces the ✕ on destructive row removal, where ✕ read as
 *  "close" rather than "delete". */
export const IconTrash = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 7h16M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7" />
    <path d="M6 7l1 12.5A1.5 1.5 0 0 0 8.5 21h7a1.5 1.5 0 0 0 1.5-1.5L18 7" />
    <path d="M10 11v6m4-6v6" />
  </Svg>
);

/** An undo arrow. */
export const IconUndo = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 8h9.5a5.5 5.5 0 0 1 0 11H7" />
    <path d="M6.5 4.5 3 8l3.5 3.5" />
  </Svg>
);

/** A person — hiring manager / reviewer attribution. */
export const IconUser = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="8" r="3.6" />
    <path d="M5 20c0-3.6 3.1-5.6 7-5.6s7 2 7 5.6" />
  </Svg>
);

/** A scales/balance glyph — the jurisdiction law chip. */
export const IconLaw = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4v16M7 20h10" />
    <path d="M4 8h16M6.5 8 4 13h5L6.5 8Zm11 0L15 13h5l-2.5-5Z" />
  </Svg>
);

/** Sun / moon for the theme toggle. */
export const IconSun = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
  </Svg>
);
export const IconMoon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />
  </Svg>
);

/** An open padlock — the "no sign-in" (dev) entry mode, paired with IconLock. */
export const IconUnlocked = (p: IconProps) => (
  <Svg {...p}>
    <rect x="4" y="11" width="16" height="10" rx="2" />
    <path d="M8 11V7.5a4 4 0 0 1 7.5-1.9" />
  </Svg>
);

/** Two overlapping sheets — copy to clipboard (JD, Req ID). */
export const IconCopy = (p: IconProps) => (
  <Svg {...p}>
    <rect x="9" y="9" width="13" height="13" rx="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </Svg>
);

/** A shield with a tick — personal details are hidden before scoring. */
export const IconShield = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3 4 6v6c0 5 3.4 8.4 8 9 4.6-.6 8-4 8-9V6l-8-3Z" />
    <path d="m9 12 2 2 4-4" />
  </Svg>
);

/** A person silhouette inside a circle — a hiring manager / a colleague. */
export const IconPerson = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="8" r="3.5" />
    <path d="M5 20a7 7 0 0 1 14 0" />
  </Svg>
);

