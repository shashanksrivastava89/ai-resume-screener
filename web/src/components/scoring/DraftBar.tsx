// The sticky dark draft bar (v3 design): live warnings with Edit→ links (and
// Restore for removed topics), the staged count, the required reasoning, and
// "Submit for approval →" — one proposal for everything staged across the tabs.
import type { CSSProperties } from "react";
import type { ScoringDoc, SystemWarning } from "../../types";
import { sectionOfPath } from "./model";

const warnStyle = (level: SystemWarning["level"]): CSSProperties =>
  level === "red"
    ? { fontSize: 12, lineHeight: 1.5, fontWeight: 700, background: "rgba(216,64,64,0.22)", border: "1px solid rgba(255,130,130,0.55)", color: "#FFC9C9", borderRadius: 8, padding: "7px 10px" }
    : level === "info"
      ? { fontSize: 12, lineHeight: 1.5, color: "rgba(255,255,255,0.8)", padding: "0 2px" }
      : { fontSize: 12, lineHeight: 1.5, background: "rgba(255,255,255,0.12)", color: "#FFD98E", borderRadius: 8, padding: "7px 10px" };

const linkStyle: CSSProperties = {
  color: "#fff", fontWeight: 700, textDecoration: "underline", cursor: "pointer",
  marginLeft: 6, whiteSpace: "nowrap", border: 0, background: "none",
  fontFamily: "inherit", fontSize: 12, padding: 0,
};

export default function DraftBar({
  count, warnings, published, reason, onReason, onDiscard, onSubmit, onRestoreTopic, goSection, busy,
}: {
  count: number;
  warnings: SystemWarning[];
  published: ScoringDoc;
  reason: string;
  onReason: (v: string) => void;
  onDiscard: () => void;
  onSubmit: () => void;
  /** Re-add a removed topic from the published document (the ↺ Restore link). */
  onRestoreTopic: (topicId: string) => void;
  goSection: (tab: string, card: string | null) => void;
  busy: boolean;
}) {
  return (
    <div style={{ position: "sticky", bottom: 14, marginTop: 18, background: "var(--surface-inverted)", borderRadius: 14, padding: "14px 18px", boxShadow: "var(--shadow-lg)", color: "#fff", zIndex: 30 }}>
      {warnings.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, marginBottom: 10 }}>
          {warnings.map((w, i) => {
            const removedTopic = /^topics\.[^.]+$/.test(w.path) ? w.path.split(".")[1] : null;
            const section = sectionOfPath(w.path);
            return (
              <div key={`${w.path}-${i}`} style={warnStyle(w.level)}>
                {w.text}
                <button onClick={() => goSection(section.tab, section.card)} style={linkStyle}>Edit ›</button>
                {removedTopic && published.topics.some((t) => t.id === removedTopic) && (
                  <button onClick={() => onRestoreTopic(removedTopic)} style={{ ...linkStyle, marginLeft: 8 }}>↺ Restore</button>
                )}
              </div>
            );
          })}
        </div>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>
          {count} staged — nothing takes effect until a second leader approves.
        </span>
        <button onClick={onDiscard} style={{ fontSize: 12, fontWeight: 700, color: "#fff", opacity: 0.75, cursor: "pointer", marginLeft: "auto", border: 0, background: "none", fontFamily: "inherit" }}>
          Discard all
        </button>
      </div>
      <div style={{ display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
        <input
          value={reason}
          onChange={(e) => onReason(e.target.value)}
          placeholder="Why this change? Required — the approver and auditors read it verbatim…"
          style={{ flex: "1 1 320px", height: 38, border: "1px solid rgba(255,255,255,0.3)", borderRadius: 10, padding: "0 12px", fontSize: 13, background: "rgba(255,255,255,0.08)", color: "#fff", fontFamily: "inherit" }}
        />
        <button
          onClick={onSubmit}
          disabled={busy}
          style={{
            height: 38, padding: "0 18px", borderRadius: 999, border: 0, fontSize: 13,
            fontWeight: 700, cursor: "pointer", color: "#fff", fontFamily: "inherit",
            background: reason.trim() && !busy ? "var(--brand-green)" : "rgba(255,255,255,0.25)",
          }}
        >
          Submit for approval →
        </button>
      </div>
    </div>
  );
}
