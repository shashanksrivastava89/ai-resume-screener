// Small, token-driven brand components — our reimplementation of the Acme DS
// pieces the mockup imported (Button, Badge, Card, Stat, Callout, Input, Select,
// Switch, Tabs) plus a few app-specific ones (MagicButton, ImportanceMeter,
// Stepper, Spinner, and the P51 Tip/InfoTip explainer primitives).
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from "react";
import { IconInfo, IconSparkle } from "./icons";
import type { Step } from "../types";

type BtnVariant = "primary" | "secondary" | "ghost";
type BtnSize = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant;
  size?: BtnSize;
}

export function Button({ variant = "primary", size = "md", className = "", ...rest }: ButtonProps) {
  return <button className={`btn btn-${variant} btn-${size} ${className}`} {...rest} />;
}

export function MagicButton({ size = "md", className = "", children, ...rest }: ButtonProps) {
  return (
    <button className={`btn btn-magic btn-${size} ${className}`} {...rest}>
      <IconSparkle size={size === "sm" ? 14 : 16} />
      {children}
    </button>
  );
}

// DS .br-badge match variants (per the Component Library): Strong→solid green,
// Possible→warn (amber), Not a fit/Weak→neutral. Value-for-value from the DS.
// Strong uses --brand-green-solid, not --brand-green: white text on #00A651 is
// 3.19:1, which fails AA, and the band badge is the single most-read label in the
// app. --brand-green-solid is the same hue darkened until white passes (6.72:1).
// 2026-09-05: "Not a fit" moves from body-text grey to the SLATE hue — still
// neutral (never rose: the tool never rejects anyone), but now a colour of its
// own, so the three bands are three distinguishable things at a glance.
const BAND_STYLE: Record<string, CSSProperties> = {
  Strong: { background: "var(--brand-green-solid)", color: "var(--white)" },
  Possible: { background: "var(--hue-amber-tint)", color: "var(--hue-amber-ink)" },
  "Not a fit": { background: "var(--hue-slate-tint)", color: "var(--hue-slate-ink)" },
};

export function Badge({ band, children }: { band?: string; children: ReactNode }) {
  const style = (band && BAND_STYLE[band]) || {
    background: "var(--neutral-150)",
    color: "var(--ink-soft)",
  };
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontFamily: "var(--font-sans)",
        fontWeight: 700,
        fontSize: 12,
        lineHeight: 1,
        letterSpacing: "0.02em",
        padding: "5px 11px",
        borderRadius: "var(--radius-pill)",
        ...style,
      }}
    >
      {children}
    </span>
  );
}

type ChipTone = "neutral" | "brand" | "warn" | "info" | "quiet" | "solid"
  | "green" | "violet" | "blue" | "teal" | "amber" | "orange" | "rose" | "slate";

/**
 * A one-line label that never widens its container (2026-08-20).
 *
 * Chips were the app's most-repeated inline style and its most-reported visual
 * defect. Each site re-declared the same eight properties, none capped its width,
 * and the result was a width cascade: a wordy chip widened its table cell, which
 * widened the table, which pushed the row's action button behind a horizontal
 * scroll. This caps at --chip-max-w and ellipsises, and sets `title` from the label
 * automatically so truncation never hides information.
 *
 * `icon` takes an element from icons.tsx rather than a unicode glyph — unicode
 * inherits no stroke weight and sits on a different baseline in every font, which
 * is what made the old chips look unfinished.
 */
export function Chip({
  tone = "neutral",
  icon,
  title,
  className = "",
  style,
  children,
}: {
  tone?: ChipTone;
  icon?: ReactNode;
  /** Overrides the auto-title. Pass "" to suppress it (e.g. text already visible). */
  title?: string;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  const auto = typeof children === "string" || typeof children === "number"
    ? String(children)
    : undefined;
  return (
    <span
      className={`chip chip-${tone} ${className}`}
      title={title !== undefined ? title || undefined : auto}
      style={style}
    >
      {icon}
      <span className="chip-label">{children}</span>
    </span>
  );
}

export function Card({
  children,
  className = "",
  style,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  onClick?: () => void;
}) {
  return (
    <div
      onClick={onClick}
      className={className}
      style={{
        background: "var(--white)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-lg)",
        boxShadow: "var(--shadow-sm)",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

// DS .br-stat — big green numeric callout (green is the licensed emphasis use).
export function Stat({ value, label }: { value: ReactNode; label: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ fontWeight: 700, fontSize: 34, lineHeight: 1, color: "var(--brand-green-deep)", letterSpacing: "-0.01em" }}>{value}</div>
      <div style={{ fontSize: 15, color: "var(--ink-soft)" }}>{label}</div>
    </div>
  );
}

// DS .br-callout — soft tinted block. Title is INK (not the accent colour); body
// is ink-soft. Border/background per variant, value-for-value from the DS.
type CalloutVariant = "info" | "warn" | "brand" | "neutral";
const CALLOUT: Record<CalloutVariant, { bg: string; bd: string }> = {
  info: { bg: "var(--surface-info)", bd: "#BEE7F0" },
  warn: { bg: "var(--surface-warn)", bd: "#F2E5A8" },
  brand: { bg: "var(--surface-brand)", bd: "#A9DEC2" },
  neutral: { bg: "var(--surface-subtle)", bd: "var(--border-subtle)" },
};

export function Callout({
  variant = "info",
  title,
  children,
}: {
  variant?: CalloutVariant;
  title?: string;
  children?: ReactNode;
}) {
  const c = CALLOUT[variant];
  return (
    <div
      style={{
        display: "flex",
        gap: 14,
        alignItems: "flex-start",
        background: c.bg,
        border: `1px solid ${c.bd}`,
        borderRadius: "var(--radius-lg)",
        padding: "16px 18px",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {title && (
          <p style={{ fontSize: 15, fontWeight: 700, color: "var(--ink)", margin: 0 }}>{title}</p>
        )}
        {children && (
          <p style={{ fontSize: 14, lineHeight: 1.5, color: "var(--ink-soft)", margin: 0 }}>{children}</p>
        )}
      </div>
    </div>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  const { className = "", ...rest } = props;
  return <input className={`input ${className}`} {...rest} />;
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { className = "", ...rest } = props;
  return <textarea className={`textarea ${className}`} {...rest} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  const { className = "", ...rest } = props;
  return <select className={`select ${className}`} {...rest} />;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label style={{ display: "block" }}>
      <span style={{ display: "block", fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
        {label}
        {hint && <span style={{ fontWeight: 400, color: "var(--ink-soft)" }}> — {hint}</span>}
      </span>
      {children}
    </label>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange?: (v: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  const toggle = (
    <button
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange?.(!checked)}
      style={{
        width: 42,
        height: 24,
        flex: "0 0 auto",
        borderRadius: "var(--radius-pill)",
        border: "none",
        padding: 0,
        cursor: disabled ? "not-allowed" : "pointer",
        background: checked ? "var(--brand-green)" : "var(--neutral-300)",
        position: "relative",
        transition: "background var(--duration-base) var(--ease-standard)",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <span
        style={{
          position: "absolute",
          top: 3,
          left: checked ? 21 : 3,
          width: 18,
          height: 18,
          borderRadius: "50%",
          background: "#fff",
          boxShadow: "var(--shadow-sm)",
          transition: "left var(--duration-base) var(--ease-standard)",
        }}
      />
    </button>
  );
  if (!label) return toggle;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
      <span style={{ fontSize: 13, fontWeight: 700 }}>{label}</span>
      {toggle}
    </span>
  );
}

export function Tabs({
  tabs,
  active,
  onSelect,
}: {
  /** `hue` (2026-09-06): the tab's colour — a dot beside the label, and the active
   *  underline/count take it (app.css .tab-hue-*). Omit for the plain green tabs. */
  tabs: { key: string; label: string; count?: number; disabled?: boolean; tooltip?: string; hue?: string }[];
  active: string;
  onSelect: (key: string) => void;
}) {
  // DS .br-tabs: underlined tabs with a green active indicator (+ our count pill).
  return (
    <div role="tablist" style={{ display: "flex", gap: 4, borderBottom: "1px solid var(--border-subtle)", overflowX: "auto" }}>
      {tabs.map((t) => {
        const on = t.key === active;
        const disabled = !!t.disabled && !on;
        return (
          <button
            key={t.key}
            role="tab"
            aria-selected={on}
            aria-disabled={disabled || undefined}
            title={disabled ? t.tooltip : undefined}
            className={"tab-btn" + (on ? " is-on" : "") + (t.hue ? ` tab-hue-${t.hue}` : "")}
            onClick={() => { if (!disabled) onSelect(t.key); }}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "12px 16px",
              marginBottom: -1,
              border: "none",
              borderBottom: `3px solid ${on ? "var(--brand-green)" : "transparent"}`,
              background: "none",
              cursor: disabled ? "not-allowed" : "pointer",
              fontFamily: "var(--font-sans)",
              fontSize: 15,
              fontWeight: 700,
              color: disabled ? "var(--ink-muted)" : on ? "var(--brand-green-deep)" : "var(--ink-soft)",
              whiteSpace: "nowrap",
              transition: "color var(--duration-base) var(--ease-standard)",
            }}
          >
            {t.hue && !disabled && <span className="tab-dot" aria-hidden="true" />}
            {t.label}
            {t.count !== undefined && !disabled && (
              <span
                className="tab-count"
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  padding: "1px 7px",
                  borderRadius: 999,
                  background: on ? "var(--brand-green-tint)" : "var(--neutral-150)",
                  color: on ? "var(--brand-green-deep)" : "var(--ink-soft)",
                }}
              >
                {t.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

const IMPORTANCE_LABEL = ["", "Low", "Low–med", "Medium", "High", "Critical"];

/**
 * The 1–5 importance control (2026-09-06 rework, pilot feedback item 1).
 *
 * The old five bare bars read as a static indicator — nobody guessed they were
 * clickable, and they were always green whatever the row's kind. Now: a bordered
 * pill with an "Importance" caption, bars that PREVIEW the level under the pointer,
 * a label that follows the preview, and the row's kind colour via --kind-solid /
 * --kind-ink (set on the .req-card ancestor; brand green where there is none).
 * The aria-labels ("Importance N of 5") are unchanged.
 */
export function ImportanceMeter({
  value,
  onChange,
}: {
  value: number;
  onChange?: (v: number) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const shown = onChange && hover != null ? hover : value;
  return (
    <span
      className={"imp-meter" + (onChange ? " is-editable" : "")}
      role="group"
      aria-label={`Importance: ${IMPORTANCE_LABEL[value] || "not set"}`}
      title={onChange ? `Importance — ${IMPORTANCE_LABEL[value] || "not set"}. Click a bar to change it.` : undefined}
    >
      <span className="imp-caption" aria-hidden="true">Importance</span>
      <span className="imp-bars" onMouseLeave={() => setHover(null)}>
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            type="button"
            onClick={() => onChange?.(n)}
            onMouseEnter={() => onChange && setHover(n)}
            onFocus={() => onChange && setHover(n)}
            onBlur={() => setHover(null)}
            disabled={!onChange}
            aria-label={`Importance ${n} of 5`}
            aria-pressed={n === value}
            className={"imp-bar" + (n <= value ? " is-on" : "") + (n > value && n <= shown ? " is-preview" : "")}
          />
        ))}
      </span>
      <span className="imp-label">{IMPORTANCE_LABEL[shown] || ""}</span>
    </span>
  );
}

export function Stepper({ steps }: { steps: Step[] }) {
  return (
    <div style={{ display: "flex", alignItems: "center", overflowX: "auto", paddingBottom: 4 }}>
      {steps.map((st, i) => {
        const done = st.state === "done";
        const current = st.state === "current";
        return (
          <div key={st.key} style={{ display: "flex", alignItems: "center", flex: i < steps.length - 1 ? "1 1 auto" : "0 0 auto" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <span
                style={{
                  flex: "0 0 auto",
                  width: 26,
                  height: 26,
                  borderRadius: "50%",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 12,
                  fontWeight: 700,
                  background: done || current ? "var(--brand-green)" : "var(--neutral-200)",
                  color: done || current ? "#fff" : "var(--ink-soft)",
                }}
              >
                {done ? "✓" : i + 1}
              </span>
              <span
                style={{
                  fontSize: 13,
                  fontWeight: current ? 700 : 400,
                  color: current ? "var(--ink)" : "var(--ink-soft)",
                  whiteSpace: "nowrap",
                }}
              >
                {st.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <span
                style={{
                  flex: "1 1 auto",
                  minWidth: 20,
                  height: 2,
                  margin: "0 12px",
                  background: done ? "var(--brand-green)" : "var(--border-subtle)",
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

export function Spinner({ size = 44 }: { size?: number }) {
  return (
    <span
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        border: `3px solid var(--brand-green-tint)`,
        borderTopColor: "var(--brand-green)",
        display: "inline-block",
        animation: "brss-spin 0.8s linear infinite",
      }}
    />
  );
}

export function BackButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className="link-back"
      style={{
        border: 0,
        cursor: "pointer",
        fontFamily: "inherit",
        fontSize: 13,
        fontWeight: 700,
        color: "var(--ink-soft)",
        padding: "6px 0",
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        background: "none",
      }}
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6" /></svg>
      {label}
    </button>
  );
}

export function PageError({ message }: { message: string }) {
  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <Callout variant="warn" title="Something needs attention">
        {message}
      </Callout>
    </div>
  );
}

export function CenterLoading({ label = "Loading…" }: { label?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14, padding: "80px 0", color: "var(--ink-soft)" }}>
      <Spinner />
      <div style={{ fontSize: 14 }}>{label}</div>
    </div>
  );
}

// --- Skeletons (2026-09-05) -------------------------------------------------------
//
// A spinner says "wait"; a skeleton says "here is the shape of what is coming" and
// keeps the layout still, so the real content lands in place instead of jumping
// in from an empty page. Used for the first paint only — revisits are served from
// the SWR cache and never show either. Shimmer honours prefers-reduced-motion
// (app.css).

/** One grey bar the size of the text it stands in for. */
export function Skeleton({ width = "100%", height = 12, radius = 6, style }:
  { width?: number | string; height?: number; radius?: number; style?: CSSProperties }) {
  return (
    <span aria-hidden="true" className="skeleton"
          style={{ display: "inline-block", width, height, borderRadius: radius, ...style }} />
  );
}

/** A table of placeholder rows; `cols` widths are fractions of the row. */
export function TableSkeleton({ rows = 6, cols = [0.34, 0.14, 0.16, 0.12, 0.14] }:
  { rows?: number; cols?: number[] }) {
  return (
    <div role="status" aria-label="Loading" style={{ padding: "4px 0" }}>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} style={{ display: "flex", gap: 18, padding: "15px 12px",
                              borderBottom: "1px solid var(--neutral-150)", alignItems: "center" }}>
          {cols.map((w, c) => (
            <Skeleton key={c} width={`${Math.round(w * 100)}%`}
                      height={c === 0 ? 14 : 11} style={{ opacity: 1 - r * 0.09 }} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** The workspace header's shape while the requisition loads. */
export function HeaderSkeleton() {
  return (
    <div role="status" aria-label="Loading" style={{ padding: "18px 0 8px", display: "grid", gap: 12 }}>
      <Skeleton width={120} height={11} />
      <Skeleton width="42%" height={22} radius={8} />
      <Skeleton width="58%" height={12} />
      <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
        {[88, 96, 104, 80, 92].map((w, i) => <Skeleton key={i} width={w} height={30} radius={8} />)}
      </div>
    </div>
  );
}

// --- Tip / InfoTip (P51): the first real tooltip/popover primitive -------------------
//
// One engine for both shapes: plain-string content renders as a dark text tip
// (role=tooltip); element content renders as a small white card. `interactive`
// keeps a rich card open while the pointer is over it (the score-math popover).
// Opens on hover + focus, toggles on click/tap (touch), closes on Escape/blur.
// Fixed positioning dodges overflow clipping in table rows and drawers.

interface TipProps {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "bottom";
  maxWidth?: number;
  /** aria-label for icon-only anchors. */
  label?: string;
  interactive?: boolean;
}

interface TipPos {
  top: number; left: number; below: boolean;
  /** The anchor's edges, kept so the tip can re-place itself once measured. */
  anchorTop: number; anchorBottom: number;
  /** Set after the first paint measured the tip against the viewport. */
  measured: boolean;
}

export function Tip({ content, children, side = "top", maxWidth = 260, label, interactive = false }: TipProps) {
  const id = useId();
  const anchorRef = useRef<HTMLSpanElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<TipPos | null>(null);
  const closeTimer = useRef<number | null>(null);
  const rich = typeof content !== "string";

  // Pointer-openings anchor horizontally on the CURSOR (clamped to the anchor's
  // box) so a tip over a wide row appears at the mouse, not the row's middle;
  // keyboard/touch openings fall back to the anchor's center.
  const open = (cursorX?: number) => {
    if (closeTimer.current) { window.clearTimeout(closeTimer.current); closeTimer.current = null; }
    const rect = anchorRef.current?.getBoundingClientRect();
    if (!rect) return;
    const below = side === "bottom" || rect.top < 120; // first guess; measured below
    const anchorX = cursorX != null
      ? Math.min(Math.max(cursorX, rect.left), rect.right)
      : rect.left + rect.width / 2;
    setPos({
      top: below ? rect.bottom + 8 : rect.top - 8,
      left: Math.min(Math.max(anchorX, 16 + maxWidth / 2),
                     window.innerWidth - 16 - maxWidth / 2),
      below,
      anchorTop: rect.top,
      anchorBottom: rect.bottom,
      measured: false,
    });
  };

  // Place by MEASUREMENT, not by a fixed threshold (2026-09-06, pilot feedback item
  // 13): a tall card (the hiring-rules popover, the distance explainer) opened from a
  // row near the top of the page was cut off above the viewport, because "flip below
  // when the anchor is within 120px of the top" knew nothing about the tip's own
  // height. After the first paint the tip's real height decides: it goes above only
  // when it fits above, otherwise below when it fits below, otherwise wherever there
  // is more room — and it scrolls inside itself rather than overflowing the screen.
  useLayoutEffect(() => {
    if (!pos || pos.measured) return;
    const h = tipRef.current?.getBoundingClientRect().height ?? 0;
    const gap = 8;
    const spaceAbove = pos.anchorTop - gap - 8;
    const spaceBelow = window.innerHeight - pos.anchorBottom - gap - 8;
    let below = pos.below;
    if (h > 0) {
      const fitsAbove = h <= spaceAbove;
      const fitsBelow = h <= spaceBelow;
      if (side === "bottom") below = fitsBelow || spaceBelow >= spaceAbove;
      else below = !fitsAbove && (fitsBelow || spaceBelow > spaceAbove);
    }
    setPos({ ...pos, below, top: below ? pos.anchorBottom + gap : pos.anchorTop - gap, measured: true });
  }, [pos, side]);
  const close = () => {
    if (interactive) {
      // grace period so the pointer can travel into the card
      closeTimer.current = window.setTimeout(() => setPos(null), 140);
    } else setPos(null);
  };
  // Entering the card itself only cancels the pending close — never reposition.
  const hold = () => {
    if (closeTimer.current) { window.clearTimeout(closeTimer.current); closeTimer.current = null; }
  };

  useEffect(() => {
    if (!pos) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPos(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pos]);

  return (
    <>
      <span
        ref={anchorRef}
        aria-describedby={pos ? id : undefined}
        aria-label={label}
        tabIndex={label ? 0 : undefined}
        onMouseEnter={(e) => open(e.clientX)}
        onMouseLeave={close}
        onFocus={() => open()}
        onBlur={close}
        onClick={(e) => { e.stopPropagation(); pos ? setPos(null) : open(e.clientX); }}
        style={{ display: "inline-flex", alignItems: "center", cursor: "default" }}
      >
        {children}
      </span>
      {pos && createPortal(
        <div
          id={id}
          ref={tipRef}
          role={rich ? "dialog" : "tooltip"}
          aria-label={rich ? label : undefined}
          onMouseEnter={interactive ? hold : undefined}
          onMouseLeave={interactive ? close : undefined}
          style={{
            // Portaled to <body>: position:fixed inside a transformed ancestor
            // (.rise, the sliding drawer) would anchor to that ancestor and the
            // tip would land far from the cursor.
            position: "fixed",
            zIndex: 70,
            top: pos.top,
            left: pos.left,
            transform: `translate(-50%, ${pos.below ? "0" : "-100%"})`,
            visibility: pos.measured ? "visible" : "hidden", // no flash before it's placed
            maxWidth,
            maxHeight: pos.below
              ? `calc(100vh - ${Math.round(pos.anchorBottom) + 16}px)`
              : `${Math.max(80, Math.round(pos.anchorTop) - 16)}px`,
            overflowY: "auto",
            ...(rich
              ? { background: "var(--white)", color: "var(--ink)", border: "1px solid var(--border-subtle)",
                  borderRadius: 12, boxShadow: "0 8px 28px rgba(34,34,34,.18)", padding: "12px 14px",
                  fontSize: 12.5, lineHeight: 1.5 }
              : { background: "var(--ink)", color: "var(--white)", borderRadius: 8,
                  padding: "7px 11px", fontSize: 12.5, lineHeight: 1.45,
                  boxShadow: "0 4px 16px rgba(34,34,34,.25)" }),
          }}
        >
          {content}
        </div>,
        document.body,
      )}
    </>
  );
}

/** The tabbable ⓘ info button + Tip — the standard "explain this word" affordance. */
export function InfoTip({ content, label = "More about this", side }: {
  content: ReactNode; label?: string; side?: "top" | "bottom";
}) {
  return (
    <Tip content={content} label={label} side={side}>
      <span style={{ display: "inline-flex", color: "var(--ink-muted)", verticalAlign: "middle" }}>
        <IconInfo size={13} />
      </span>
    </Tip>
  );
}

/** The v3 design's circled-"i" marker (its `infoStyle`, verbatim) + a dark Tip.
 * The standard hover explainer on the Scoring model page and the score math. */
export function InfoDot({ content, label = "More about this", side }: {
  content: ReactNode; label?: string; side?: "top" | "bottom";
}) {
  return (
    <Tip content={content} label={label} side={side}>
      <span
        style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          width: 15, height: 15, borderRadius: "50%",
          border: "1px solid var(--border-strong)", color: "var(--ink-muted)",
          fontSize: 9.5, fontWeight: 700, cursor: "help", fontStyle: "normal",
          lineHeight: 1, flex: "0 0 auto",
        }}
      >
        i
      </span>
    </Tip>
  );
}

/** Joined pill segments (the design's Nice/Must/Gate control, ± direction
 * toggle, band-cap chips). `dark` renders the active segment ink-on-white
 * (the Hard-gate / must-cap variant). */
export function SegmentedPill({
  options, value, onChange, dark = false, disabled = false, size = "md",
}: {
  options: { key: string; label: string }[];
  value: string;
  onChange?: (key: string) => void;
  dark?: boolean;
  disabled?: boolean;
  size?: "sm" | "md";
}) {
  return (
    <span
      style={{
        display: "inline-flex", flex: "0 0 auto", whiteSpace: "nowrap",
        border: "1px solid var(--border-subtle)", borderRadius: 999,
        overflow: "hidden", fontSize: size === "sm" ? 10.5 : 11, fontWeight: 700,
        opacity: disabled ? 0.55 : 1,
      }}
    >
      {options.map((o) => {
        const on = o.key === value;
        return (
          <button
            key={o.key}
            onClick={() => !disabled && onChange?.(o.key)}
            aria-pressed={on}
            style={{
              padding: size === "sm" ? "5px 9px" : "6px 11px",
              cursor: disabled || !onChange ? "default" : "pointer",
              whiteSpace: "nowrap", border: 0, fontFamily: "inherit",
              fontSize: "inherit", fontWeight: 700,
              background: on ? (dark ? "var(--ink)" : "var(--brand-green)") : "transparent",
              color: on ? "#fff" : "var(--ink-muted)",
            }}
          >
            {o.label}
          </button>
        );
      })}
    </span>
  );
}

/** The design's − / value / ＋ stepper with the "was X" marker under a changed
 * value. Pure control — the row chrome (label + sub) lives with the caller. */
export function ValueStepper({
  label, was, onDec, onInc, minWidth = 56,
}: {
  label: string;
  /** "was 88" under the value while a draft differs from published. */
  was?: string | null;
  onDec: () => void;
  onInc: () => void;
  minWidth?: number;
}) {
  const btn: CSSProperties = {
    width: 26, height: 26, borderRadius: 8, border: "1px solid var(--border-subtle)",
    background: "var(--white)", color: "var(--ink-soft)", fontSize: 13,
    fontWeight: 700, cursor: "pointer", lineHeight: 1, fontFamily: "inherit",
  };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <button aria-label={`Decrease ${label}`} onClick={onDec} style={btn}>−</button>
      <span
        style={{
          position: "relative", fontSize: 12.5, fontWeight: 700,
          fontFamily: "monospace", minWidth, textAlign: "center",
          padding: "4px 6px", borderRadius: 8,
          ...(was ? { background: "var(--white)", boxShadow: "inset 0 0 0 2px var(--brand-green)", color: "var(--brand-green-deep)" } : {}),
        }}
      >
        {label}
        {was && (
          <span style={{ position: "absolute", top: "100%", left: "50%", transform: "translateX(-50%)", marginTop: 3, fontSize: 9, fontWeight: 700, color: "var(--ink-muted)", whiteSpace: "nowrap" }}>
            {was}
          </span>
        )}
      </span>
      <button aria-label={`Increase ${label}`} onClick={onInc} style={btn}>＋</button>
    </span>
  );
}
