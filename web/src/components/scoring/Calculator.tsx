// The "Watch it work" rail (v3 design): the synthetic Treasury-Accountant
// sample scored live through the REAL engine (POST /api/scoring/preview) under
// the DRAFT values — final + band, the composition chart with a "was N"
// compare marker, the clamp note, and the full arithmetic breakdown whose
// section headers jump into the editable cards.
import { useState } from "react";
import type { CSSProperties } from "react";
import type { PreviewResponse, ScoringDoc } from "../../types";
import ScoreChart, { type ScoreParts } from "../ScoreChart";
import { InfoDot } from "../ui";
import {
  SAMPLE_ADJUSTMENTS, SAMPLE_DOWNGRADED, SAMPLE_EXTRACTION, SAMPLE_PREF,
  SAMPLE_RUBRIC, SAMPLE_SUSPENDED, SAMPLE_TITLE, fmtN, signalName,
} from "./model";

export function partsFromPreview(p: PreviewResponse): ScoreParts {
  let net = 0;
  let pref = 0;
  let clamped = false;
  for (const step of p.waterfall) {
    if (step.step === "adjustment") net += step.delta;
    if (step.step === "preference") pref += step.delta;
    if (step.step === "clamp") clamped = true;
  }
  return {
    base: p.base.total,
    net: Math.round(net * 100) / 100,
    pref: Math.round(pref * 100) / 100,
    final: p.result.final_score,
    band: p.result.band,
    clamped,
  };
}

const bandChip = (band: string): CSSProperties => ({
  display: "inline-flex", alignItems: "center", fontSize: 11.5, fontWeight: 700,
  padding: "3px 10px", borderRadius: 999, whiteSpace: "nowrap",
  background: band === "Strong" ? "var(--brand-green-tint)" : band === "Possible" ? "var(--accent-blue-tint)" : "var(--neutral-150)",
  color: band === "Strong" ? "var(--brand-green-deep)" : band === "Possible" ? "var(--hue-blue-ink)" : "var(--hue-slate-ink)",
});

const bandLabel = (band: string) =>
  band === "Strong" ? "Strong match" : band === "Possible" ? "Possible match" : "Weak match";

export default function Calculator({
  doc, published, dirty, preview, publishedPreview, dirtySections, highlight, goSection, tips,
}: {
  doc: ScoringDoc;
  published: ScoringDoc;
  dirty: boolean;
  preview: PreviewResponse | null;
  publishedPreview: PreviewResponse | null;
  dirtySections: { credits: boolean; signals: boolean; prefs: boolean; conf: boolean };
  highlight: string | null;
  goSection: (tab: string, card: string | null) => void;
  tips: Record<string, string>;
}) {
  const [mathOpen, setMathOpen] = useState(true);
  if (!preview) return null;
  const parts = partsFromPreview(preview);
  const pubParts = publishedPreview ? partsFromPreview(publishedPreview) : null;

  const credit = (match: string) => doc.coverage.match_value[match] ?? 0;
  const glyph = (match: string) => (match === "full" ? "✓" : match === "partial" ? "~" : "✗");
  const glyphColor = (match: string) =>
    match === "full" ? "var(--brand-green-deep)" : match === "partial" ? "#4A4A4A" : "var(--warn-ink)";

  const stepByLabel = new Map(preview.waterfall.map((s) => [s.label, s]));
  const raw = Math.round((parts.base + parts.net + parts.pref) * 100) / 100;

  const header = (label: string, isDirty: boolean, color: string, tint: string, onClick: () => void): JSX.Element => (
    <button
      onClick={onClick}
      style={{
        display: "inline-flex", alignItems: "center", gap: 5, fontSize: 10, fontWeight: 700,
        letterSpacing: "0.04em", textTransform: "uppercase", cursor: "pointer",
        border: 0, background: "none", fontFamily: "inherit", color, padding: 0,
        ...(isDirty ? { background: tint, boxShadow: `inset 0 0 0 1.5px ${color}`, borderRadius: 6, padding: "2px 7px" } : {}),
      }}
    >
      {label}
    </button>
  );

  const deltaChanged = dirty && pubParts
    && (fmtN(pubParts.final) !== fmtN(parts.final) || pubParts.band !== parts.band);

  return (
    <div style={{ flex: "0 0 330px", position: "sticky", top: 0, alignSelf: "flex-start" }}>
      <div style={{ background: "var(--white)", border: "1.5px solid var(--brand-green)", borderRadius: 16, padding: "16px 18px", boxShadow: "var(--shadow-sm)" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 2 }}>
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--brand-green-deep)" }}>Watch it work</span>
          <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: "0.03em", textTransform: "uppercase", padding: "2px 7px", borderRadius: 6, background: "var(--neutral-150)", color: "var(--ink-soft)" }}>Synthetic — not a real person</span>
        </div>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>{SAMPLE_TITLE}</div>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink-soft)" }}>Final score</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 30, fontWeight: 700, fontFamily: "monospace", letterSpacing: "-0.02em" }}>{fmtN(parts.final)}</span>
            <span style={bandChip(parts.band)}>{bandLabel(parts.band)}</span>
          </span>
        </div>
        <ScoreChart
          parts={parts}
          bounds={{ strongAt: doc.bands.strong_at, possibleAt: doc.bands.possible_at, clampMin: doc.clamp.min, clampMax: doc.clamp.max }}
          compare={dirty ? pubParts : null}
          highlight={highlight}
        />
        {deltaChanged && pubParts && (
          <div style={{ marginTop: 9, fontSize: 12, fontWeight: 700, color: "var(--brand-green-deep)", background: "var(--brand-green-tint)", borderRadius: 8, padding: "7px 10px", lineHeight: 1.5 }}>
            v{published.version} says {fmtN(pubParts.final)} ({bandLabel(pubParts.band)}) — with your staged changes {fmtN(parts.final)} ({bandLabel(parts.band)})
          </div>
        )}
        {parts.clamped && (
          <div style={{ marginTop: 8, fontSize: 11.5, fontWeight: 700, color: "var(--warn-ink)", background: "var(--surface-warn)", borderRadius: 8, padding: "7px 10px", lineHeight: 1.5 }}>
            Raw total {fmtN(raw)} was held at {raw > doc.clamp.max ? `the ceiling ${fmtN(doc.clamp.max)}` : `the floor ${fmtN(doc.clamp.min)}`} — that’s the clamp doing its job.
          </div>
        )}
        <button
          onClick={() => setMathOpen((o) => !o)}
          style={{ display: "inline-block", border: 0, background: "none", fontFamily: "inherit", fontSize: 11.5, fontWeight: 700, cursor: "pointer", color: "var(--brand-green-deep)", marginTop: 11, padding: 0 }}
        >
          {mathOpen ? "Hide the arithmetic" : "Show the arithmetic"} ›
        </button>
        {mathOpen && (
          <div style={{ marginTop: 8 }}>
            <div style={{ marginBottom: 6 }}>
              {header("Checklist evidence × credits ›", dirtySections.credits, "var(--brand-green-deep)", "var(--brand-green-tint)", () => goSection("score", "credits"))}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 6 }}>
              {SAMPLE_RUBRIC.map((r) => (
                <div key={r.label} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 11.5 }}>
                  <span style={{ flex: "0 0 14px", textAlign: "center", fontSize: 11, fontWeight: 700, color: glyphColor(r.match) }}>{glyph(r.match)}</span>
                  <span title={r.evidence} style={{ flex: "1 1 auto", minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.label}</span>
                  <span style={{ fontFamily: "monospace", color: "var(--ink-soft)", whiteSpace: "nowrap", fontSize: 10.5 }}>
                    {r.weight} × {fmtN(credit(r.match))} = {fmtN(r.weight * credit(r.match))}
                  </span>
                </div>
              ))}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 12.5, fontWeight: 700, padding: "7px 0", borderTop: "1px solid var(--neutral-150)" }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                Checklist base — plain arithmetic <InfoDot content={tips.deterministic} />
              </span>
              <span style={{ fontFamily: "monospace" }}>{fmtN(parts.base)}</span>
            </div>
            <div style={{ margin: "6px 0" }}>
              {header("AI signals — capped, fairness-checked ›", dirtySections.signals, "var(--hue-blue-ink)", "var(--accent-blue-tint)", () => goSection("score", "signals"))}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              {SAMPLE_ADJUSTMENTS.map((a) => {
                const signal = doc.signals.find((s) => s.id === a.signalId);
                const name = signal ? signalName(signal) : a.signalId;
                const off = !signal || !signal.enabled_default || signal.retired;
                const step = off ? null : stepByLabel.get(name);
                const points = off ? 0 : (step?.delta ?? 0);
                const clipped = !off && Math.abs(points) < Math.abs(a.points);
                return (
                  <div key={a.signalId}>
                    <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 11.5 }}>
                      <span style={{ flex: "1 1 auto", minWidth: 0, fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", ...(off ? { textDecoration: "line-through", color: "var(--ink-muted)" } : {}) }}>
                        {name}
                      </span>
                      <span style={{
                        display: "inline-flex", fontSize: 9.5, fontWeight: 700, padding: "2px 7px", borderRadius: 999, flex: "0 0 auto",
                        ...(off ? { background: "var(--neutral-150)", color: "var(--hue-slate-ink)" }
                          : a.verdict === "pass" ? { background: "var(--brand-green-tint)", color: "var(--brand-green-deep)" }
                          : { background: "var(--surface-warn)", color: "var(--warn-ink)" }),
                      }}>
                        {off ? "retired" : a.verdict}
                      </span>
                      <span style={{ fontFamily: "monospace", fontWeight: 700, whiteSpace: "nowrap", ...(off ? { color: "var(--ink-muted)" } : {}) }}>
                        {points >= 0 ? "+" : ""}{fmtN(points)}
                      </span>
                    </div>
                    {clipped && (
                      <div style={{ fontSize: 10.5, color: "var(--ink-muted)", lineHeight: 1.45, margin: "-2px 0 2px 2px" }}>
                        clipped at the cap (evidence earned {a.points >= 0 ? "+" : ""}{fmtN(a.points)}, cap is {fmtN(signal!.cap_points)})
                      </div>
                    )}
                  </div>
                );
              })}
              {SAMPLE_SUSPENDED.map((a) => {
                const signal = doc.signals.find((s) => s.id === a.signalId);
                const name = signal ? signalName(signal) : a.signalId;
                return (
                  <div key={a.signalId}>
                    <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 11.5 }}>
                      <span style={{ flex: "1 1 auto", minWidth: 0, fontWeight: 700, textDecoration: "line-through", color: "var(--ink-muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{name}</span>
                      <span style={{ display: "inline-flex", fontSize: 9.5, fontWeight: 700, padding: "2px 7px", borderRadius: 999, background: "var(--surface-inverted)", color: "#fff", flex: "0 0 auto" }}>suspended</span>
                      <span style={{ fontFamily: "monospace", fontWeight: 700, color: "var(--ink-muted)" }}>0</span>
                    </div>
                    <div style={{ fontSize: 10.5, color: "var(--ink-muted)", lineHeight: 1.45, margin: "-2px 0 2px 2px" }}>{a.reason}</div>
                  </div>
                );
              })}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5, fontWeight: 700, padding: "7px 0", borderTop: "1px solid var(--neutral-150)", marginTop: 5 }}>
              <span>AI signals net</span>
              <span style={{ fontFamily: "monospace" }}>{parts.net >= 0 ? "+" : ""}{fmtN(parts.net)}</span>
            </div>
            <div style={{ margin: "6px 0" }}>
              {header("Preferences — capped, approved only ›", dirtySections.prefs, "var(--warn-ink)", "var(--surface-warn)", () => goSection("score", "prefs"))}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11.5, padding: "3px 0" }}>
              <span style={{ flex: "1 1 auto", minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{SAMPLE_PREF.label}</span>
              <span style={{ fontFamily: "monospace", fontWeight: 700 }}>+{fmtN(parts.pref)}</span>
            </div>
          </div>
        )}
        <div
          style={{
            display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "var(--ink-soft)",
            marginTop: 10, borderRadius: 8, padding: "8px 10px",
            border: `1.5px solid ${dirtySections.conf ? "var(--border-strong)" : "transparent"}`,
            background: dirtySections.conf ? "var(--neutral-100)" : "transparent",
          }}
        >
          <span style={{ flex: "1 1 auto", minWidth: 0 }}>Confidence read — display only, never the score</span>
          <span style={{ fontWeight: 700, whiteSpace: "nowrap" }}>
            {preview.result.confidence.charAt(0).toUpperCase() + preview.result.confidence.slice(1)} · extraction {SAMPLE_EXTRACTION} · {SAMPLE_DOWNGRADED} downgraded claim{SAMPLE_DOWNGRADED === 1 ? "" : "s"}
          </span>
        </div>
      </div>
      <div style={{ fontSize: 11, color: "var(--ink-muted)", lineHeight: 1.55, marginTop: 10 }}>
        Nudge any number on the left — this chart recomputes instantly. The same math powers every real score: click any score in Review to see its own version.
      </div>
    </div>
  );
}
