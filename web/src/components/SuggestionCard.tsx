// The TA-side inline card for one open HM suggestion (P42). Accepting merges
// the (optionally edited) text into the checklist editor draft — publishing is
// still the normal Save & publish flow, so accepting alone changes nothing.
import { useState } from "react";
import { Button } from "./ui";
import { dualStamp } from "../time";
import type { Suggestion } from "../types";

function initials(name: string): string {
  const parts = name.replace(/[-_@.]/g, " ").split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts[0][0] + (parts[1]?.[0] ?? "")).toUpperCase();
}

const KIND_LABEL: Record<string, string> = {
  nice_to_have: "Nice-to-have", must_have: "Must-have", hard_gate: "Hard gate",
};

export function kindChanged(s: Suggestion): boolean {
  return !!s.new_kind && s.new_kind !== (s.old_kind ?? null);
}

export function importanceChanged(s: Suggestion): boolean {
  return s.new_importance != null && s.new_importance !== (s.old_importance ?? null);
}

export default function SuggestionCard({
  suggestion,
  reqTz,
  busy = false,
  onAccept,
  onDecline,
}: {
  suggestion: Suggestion;
  reqTz?: string;
  busy?: boolean;
  onAccept: (editedText?: string) => void;
  onDecline: (note: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [edited, setEdited] = useState(suggestion.new_text);
  const [declining, setDeclining] = useState(false);
  const [note, setNote] = useState("");
  const who = suggestion.actor_name || suggestion.actor;

  return (
    <div style={{ border: "1.5px solid var(--accent-blue)", background: "var(--white)", borderRadius: 12, padding: "14px 16px", marginTop: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <span style={{ flex: "0 0 auto", width: 24, height: 24, borderRadius: "50%", background: "var(--accent-blue-tint)", color: "var(--hue-blue-ink)", display: "inline-flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 10.5 }}>
          {initials(who)}
        </span>
        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--hue-blue-ink)" }}>{who} (HM) suggests an edit</span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--ink-muted)" }}>{dualStamp(suggestion.ts, reqTz)}</span>
      </div>

      {suggestion.old_text && (
        <div style={{ fontSize: 12.5, color: "var(--ink-muted)", textDecoration: "line-through" }}>{suggestion.old_text}</div>
      )}
      {/* 2026-07-19: kind/importance proposals ride along with the wording */}
      {(kindChanged(suggestion) || importanceChanged(suggestion)) && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "6px 0 2px" }}>
          {kindChanged(suggestion) && (
            <span style={{ fontSize: 11, fontWeight: 700, padding: "3px 9px", borderRadius: 999, background: "var(--accent-blue-tint)", color: "var(--info-ink)" }}>
              {KIND_LABEL[suggestion.old_kind ?? ""] ?? "—"} → {KIND_LABEL[suggestion.new_kind ?? ""] ?? "—"}
            </span>
          )}
          {importanceChanged(suggestion) && (
            <span style={{ fontSize: 11, fontWeight: 700, padding: "3px 9px", borderRadius: 999, background: "var(--accent-blue-tint)", color: "var(--info-ink)" }}>
              Importance {suggestion.old_importance ?? "—"} → {suggestion.new_importance}
            </span>
          )}
        </div>
      )}
      {editing ? (
        <textarea
          className="input"
          aria-label="Edited wording"
          value={edited}
          onChange={(e) => setEdited(e.target.value)}
          rows={2}
          style={{ width: "100%", resize: "vertical", margin: "4px 0 8px" }}
        />
      ) : (
        <div style={{ fontSize: 13.5, color: "var(--ink)", margin: "4px 0 8px" }}>{edited}</div>
      )}

      {suggestion.note && (
        <div style={{ fontSize: 12, color: "var(--ink-soft)", background: "var(--neutral-100)", borderRadius: 8, padding: "8px 11px", marginBottom: 10 }}>
          "{suggestion.note}"
        </div>
      )}

      {declining ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <input
            className="input"
            aria-label="Reason for declining"
            placeholder="Why decline? (optional note back to the HM)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => onDecline(note.trim())}>Decline suggestion</Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setDeclining(false)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8 }}>
          <Button size="sm" disabled={busy} onClick={() => onAccept(editing ? edited.trim() : undefined)}>Accept</Button>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => setEditing((v) => !v)}>
            {editing ? "Stop editing" : "Edit"}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setDeclining(true)}>Decline</Button>
        </div>
      )}
    </div>
  );
}
