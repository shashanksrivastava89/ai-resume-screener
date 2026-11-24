// P51: score explainability — the compact popover behind every score number
// (hover/click) and the drawer's full "How this score adds up" section. Plain
// recruiter language throughout; the technical words ride in InfoTips.
import { InfoTip, Tip } from "./ui";
import { IconOutdated } from "./icons";
import type { ReactNode } from "react";
import type { ScoreMath, ScoreMathBrief } from "../types";

const num = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));
const signed = (v: number) => `${v >= 0 ? "+" : ""}${num(v)}`;

function PopoverRow({ label, value, strong = false }: { label: ReactNode; value: string; strong?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 14, padding: "3px 0" }}>
      <span style={{ color: strong ? "var(--ink)" : "var(--ink-soft)", fontWeight: strong ? 700 : 400 }}>{label}</span>
      <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: strong ? 700 : 600, color: "var(--ink)" }}>{value}</span>
    </div>
  );
}

/** The compact math card inside the score popover. */
export function ScoreBrief({ math, stale }: { math: ScoreMathBrief; stale?: boolean }) {
  return (
    <div style={{ minWidth: 220 }}>
      <PopoverRow label="Checklist coverage" value={num(math.base)} />
      <PopoverRow
        label={math.suspended_count > 0
          ? `Evidence-backed extras (${math.suspended_count} set aside)`
          : "Evidence-backed extras"}
        value={signed(math.adjustments)} />
      {math.preferences !== 0 && <PopoverRow label="Preference nudge" value={signed(math.preferences)} />}
      {math.clamped && (
        <PopoverRow label={`Kept between 0 and 100 (${math.clamped === "high" ? "trimmed down" : "held at 0"})`}
          value={num(math.final - math.before_limits)} />
      )}
      <div style={{ borderTop: "1px solid var(--neutral-150)", marginTop: 4, paddingTop: 4 }}>
        <PopoverRow label="Final score" value={num(math.final)} strong />
        <div style={{ color: "var(--ink-soft)", marginTop: 2 }}>{math.band_label}</div>
      </div>
      {stale && (
        <div style={{ marginTop: 8, color: "var(--warn-ink)", display: "flex", gap: 6, alignItems: "center" }}>
          <IconOutdated size={13} /> Scored under previous rules — re-screen to refresh.
        </div>
      )}
      <div style={{ marginTop: 8, color: "var(--ink-muted)", fontSize: 11.5 }}>
        Open the candidate for the full breakdown.
      </div>
    </div>
  );
}

/** The score number wrapped in the popover — one component so the Review row
 *  and the drawer render scores identically (incl. the stale treatment). */
export function ScoreNumber({ score, math, stale, size = 24 }: {
  score: number; math?: ScoreMathBrief | null; stale?: boolean; size?: number;
}) {
  const body = (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      {stale && <span style={{ color: "var(--warn-ink)", display: "inline-flex" }}><IconOutdated size={Math.round(size * 0.58)} /></span>}
      <span style={{ fontSize: size, fontWeight: 700, lineHeight: 1, color: stale ? "var(--ink-soft)" : "var(--brand-green-deep)" }}>
        {score}
      </span>
    </span>
  );
  if (!math) {
    return stale ? (
      <Tip content="Scored under previous rules — re-screen to refresh.">{body}</Tip>
    ) : body;
  }
  return (
    <Tip content={<ScoreBrief math={math} stale={stale} />} label="How this score adds up" interactive>
      {body}
    </Tip>
  );
}

/** The simplified score summary for TA/HR (2026-07-19, from the user's design):
 *  hero number + band badge, one chip per contribution (checklist base, AI
 *  signals, preferences) and a thin meter with the band thresholds ticked.
 *  The full arithmetic stays one click away behind "Show the math". */
export function ScoreSummaryCard({ math, stale, onJump }: {
  math: ScoreMath; stale?: boolean;
  /** Chip click → open the math and land on that section ("base" | "signals" | "prefs"). */
  onJump?: (section: "base" | "signals" | "prefs") => void;
}) {
  const final = math.total.final;
  const strongAt = math.band.strong_at;
  const possibleAt = math.band.possible_at;
  const band = math.band.result;
  const badge = band === "Strong"
    ? { bg: "var(--brand-green-tint)", fg: "var(--brand-green-deep)", text: "Strong match", tick: true }
    : band === "Possible"
      ? { bg: "var(--accent-yellow-tint)", fg: "var(--status-warning)", text: "Possible match", tick: false }
      : { bg: "var(--neutral-150)", fg: "var(--ink-soft)", text: math.band.label || "Not a fit", tick: false };
  const chip = (bg: string, fg: string, text: string, section: "base" | "signals" | "prefs") => (
    <button
      onClick={onJump ? () => onJump(section) : undefined}
      title={onJump ? "See this part of the math" : undefined}
      style={{ fontSize: 11.5, padding: "4px 10px", borderRadius: 8, background: bg, color: fg,
               whiteSpace: "nowrap", fontWeight: 700, border: 0, fontFamily: "inherit",
               cursor: onJump ? "pointer" : "default" }}
    >
      {text}
    </button>
  );
  return (
    <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 12, padding: "16px 20px" }}>
      {/* One line: hero + badge left, ALL THREE chips right (nowrap — the drawer
          is wide enough by default now; tiny widths wrap the chip row whole). */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, flex: "0 0 auto" }}>
          <span style={{ fontSize: 40, fontWeight: 700, lineHeight: 1, color: stale ? "var(--ink-soft)" : "var(--ink)", fontVariantNumeric: "tabular-nums" }}>
            {num(final)}
          </span>
          <span style={{ fontSize: 12.5, color: "var(--ink-muted)" }}>/ 100</span>
        </div>
        <span style={{ fontSize: 11.5, fontWeight: 700, padding: "4px 11px", borderRadius: 999, background: badge.bg, color: badge.fg, display: "inline-flex", alignItems: "center", gap: 5, whiteSpace: "nowrap", flex: "0 0 auto" }}>
          {badge.tick && <span aria-hidden="true">✓</span>}{badge.text}
        </span>
        <div style={{ display: "flex", gap: 6, flexWrap: "nowrap", marginLeft: "auto", flex: "0 0 auto" }}>
          {chip("var(--brand-green-tint)", "var(--brand-green-deep)", `Checklist ${num(math.base.total)}`, "base")}
          {chip("var(--accent-blue-tint)", "var(--info-ink)", `AI signals ${signed(math.adjustments.net)}`, "signals")}
          {chip("var(--accent-yellow-tint)", "var(--warn-ink)", `Preferences ${signed(math.preferences.net)}`, "prefs")}
        </div>
      </div>
      <div style={{ position: "relative", marginTop: 18 }}>
        <div style={{ height: 8, borderRadius: 4, background: "var(--neutral-150)", overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${Math.max(0, Math.min(100, final))}%`, background: "var(--brand-green)", borderRadius: 4 }} />
        </div>
        <div style={{ position: "absolute", left: `${possibleAt}%`, top: -3, height: 14, width: 1.5, background: "var(--ink-muted)" }} />
        <div style={{ position: "absolute", left: `${strongAt}%`, top: -3, height: 14, width: 1.5, background: "var(--brand-green)" }} />
        <div style={{ display: "flex", fontSize: 11, color: "var(--ink-muted)", marginTop: 6 }}>
          <span style={{ width: `${possibleAt}%`, textAlign: "right", paddingRight: 4 }}>Possible {num(possibleAt)}</span>
          <span style={{ width: `${Math.max(0, strongAt - possibleAt)}%`, textAlign: "right", color: "var(--brand-green-deep)" }}>Strong {num(strongAt)}</span>
        </div>
      </div>
      {(math.band.gate_note || math.band.capped_by_must_have) && (
        <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 8 }}>
          {math.band.gate_note ?? math.band.cap_note ?? "Capped because a must-have is missing."}
        </div>
      )}
      {stale && (
        <div style={{ fontSize: 12, color: "var(--warn-ink)", marginTop: 8, display: "flex", gap: 6, alignItems: "center" }}>
          <IconOutdated size={13} /> Scored under previous rules — re-screen to refresh.
        </div>
      )}
    </div>
  );
}

/** One tinted section card — the color matches its summary chip so the eye can
 *  map "Checklist / AI signals / Preferences" between the two. */
function MathSection({ id, tint, ink, title, sub, children }: {
  id?: string; tint: string; ink: string; title: string; sub?: ReactNode; children: ReactNode;
}) {
  return (
    <div id={id} style={{ border: "1px solid var(--neutral-150)", borderLeft: `3px solid ${ink}`, borderRadius: 10, overflow: "hidden", marginBottom: 12, scrollMarginTop: 12 }}>
      <div style={{ background: tint, padding: "7px 12px" }}>
        <span style={{ fontSize: 11.5, fontWeight: 700, color: ink }}>{title}</span>
        {sub && <span style={{ fontSize: 11.5, color: "var(--ink-soft)", marginLeft: 8 }}>{sub}</span>}
      </div>
      <div style={{ padding: "4px 12px 8px" }}>{children}</div>
    </div>
  );
}

/** The drawer's full "How this score adds up" section. `idPrefix` makes the
 *  section anchors unique per surface (drawer vs the audit modal). */
export function ScoreMathSection({ math, idPrefix = "sm" }: { math: ScoreMath; idPrefix?: string }) {
  return (
    <section aria-label="How this score adds up" style={{ borderTop: "1px solid var(--neutral-150)", paddingTop: 16, marginTop: 16 }}>
      <h3 style={{ fontSize: 14.5, margin: "0 0 10px" }}>
        How this score adds up{" "}
        <InfoTip content={`Computed under scoring model v${math.as_of.scoring_model_version}${math.as_of.checklist ? ` and checklist ${math.as_of.checklist}` : ""} — the versions in force when this screen ran.${math.as_of.approximate ? " (Reconstructed with the closest available version.)" : ""}`} />
      </h3>

      {/* base — green, like the "Checklist" chip */}
      <MathSection id={`${idPrefix}-base`} tint="var(--brand-green-tint)" ink="var(--brand-green-deep)"
                   title={`Checklist · ${num(math.base.total)}`} sub={math.base.explainer}>
        {math.base.lines.map((l, i) => (
          <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, padding: "6px 0", fontSize: 12.5, borderBottom: i < math.base.lines.length - 1 ? "1px solid var(--neutral-150)" : "none" }}>
            <span style={{ color: "var(--ink)", minWidth: 0 }}>
              {l.label}
              <span style={{ color: "var(--ink-muted)" }}> · {l.weight_pct}% of the checklist · {l.credit === "full" ? "fully met" : l.credit === "partial" ? "partly met" : "not met"}</span>
            </span>
            <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 600, whiteSpace: "nowrap" }}>{signed(l.points)}</span>
          </div>
        ))}
        <div style={{ display: "flex", justifyContent: "space-between", padding: "7px 0 2px", fontSize: 12.5, fontWeight: 700, borderTop: "1px solid var(--neutral-150)" }}>
          <span>Starting score</span><span style={{ fontVariantNumeric: "tabular-nums" }}>{num(math.base.total)}</span>
        </div>
      </MathSection>

      {/* extras — blue, like the "AI signals" chip */}
      <MathSection id={`${idPrefix}-signals`} tint="var(--accent-blue-tint)" ink="var(--info-ink)"
                   title={`AI signals · ${signed(math.adjustments.net)}`}
                   sub={<>
                     {math.adjustments.cap_note}{" "}
                     <InfoTip content="The technical term is a cap: anything past the limit is trimmed back to it." />
                   </>}>
        {math.adjustments.lines.length === 0 && math.adjustments.suspended.length === 0 && (
          <div style={{ fontSize: 12.5, color: "var(--ink-muted)", padding: "4px 0" }}>
            No evidence-backed extras applied on this run.
          </div>
        )}
        {math.adjustments.lines.map((l, i) => (
          <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, padding: "4px 0", fontSize: 12.5 }}>
            <span style={{ color: "var(--ink)", minWidth: 0 }}>{l.label}{l.flagged && <span style={{ color: "var(--warn-ink)" }}> · flagged for a look</span>}</span>
            <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 600, whiteSpace: "nowrap" }}>{signed(l.points)}</span>
          </div>
        ))}
        {math.adjustments.suspended.map((l, i) => (
          <div key={`s${i}`} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, padding: "4px 0", fontSize: 12.5, color: "var(--ink-muted)" }}>
            <span style={{ textDecoration: "line-through", minWidth: 0 }}>{l.label} ({signed(l.points)})</span>
            <span style={{ textAlign: "right" }}>{l.note}</span>
          </div>
        ))}
        {math.adjustments.enforcement_notes.length > 0 && (
          <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 4 }}>
            {math.adjustments.enforcement_notes.join(" · ")}
          </div>
        )}
      </MathSection>

      {/* preferences — amber, like the "Preferences" chip */}
      <MathSection id={`${idPrefix}-prefs`} tint="var(--accent-yellow-tint)" ink="var(--warn-ink)"
                   title={`Preferences · ${signed(math.preferences.net)}`} sub={math.preferences.cap_note}>
        {math.preferences.lines.length === 0 && (
          <div style={{ fontSize: 12.5, color: "var(--ink-muted)", padding: "4px 0" }}>
            No personal preferences touched this score.
          </div>
        )}
        {math.preferences.lines.map((l, i) => (
          <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, padding: "4px 0", fontSize: 12.5 }}>
            <span style={{ color: "var(--ink)", minWidth: 0 }}>{l.label}</span>
            <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 600, whiteSpace: "nowrap" }}>{signed(l.points)}</span>
          </div>
        ))}
      </MathSection>

      {/* total + band */}
      <div style={{ border: "1px solid var(--neutral-150)", borderRadius: 10, padding: "8px 12px", fontSize: 12.5 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 700 }}>
          <span>
            Final score
            {math.total.clamped && (
              <span style={{ fontWeight: 400, color: "var(--ink-soft)" }}>
                {" "}(kept between 0 and 100 <InfoTip content="Technically 'clamped' — the math landed outside the range and was held at the boundary." />)
              </span>
            )}
          </span>
          <span style={{ fontVariantNumeric: "tabular-nums" }}>{num(math.total.final)}</span>
        </div>
        <div style={{ color: "var(--ink-soft)", marginTop: 4 }}>
          {math.band.gate_note
            ? math.band.gate_note
            : math.band.capped_by_must_have
              ? `${num(math.total.final)} would read ${math.band.capped_by_must_have === "Strong" ? "Strong" : math.band.capped_by_must_have} — ${math.band.cap_note ?? "capped because a must-have is missing."}`
              : `Strong match from ${num(math.band.strong_at)} · Possible match from ${num(math.band.possible_at)} → ${math.band.label}.`}
        </div>
        <div style={{ color: "var(--ink-muted)", marginTop: 4 }}>
          Confidence {math.confidence.level} — {math.confidence.explainer}
        </div>
      </div>
    </section>
  );
}
