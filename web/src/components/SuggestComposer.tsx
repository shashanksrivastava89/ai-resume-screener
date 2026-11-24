// The hiring manager's per-row edit composer (P42). Opens under a checklist
// requirement in suggest mode; the HM proposes new wording — and since
// 2026-07-19 also the KIND (Nice-to-have / Must-have / Hard gate) and the
// IMPORTANCE, defaulting to the row's CURRENT values (never silently
// "nice-to-have"). It "goes to the TA for review" — nothing here changes the
// checklist.
import { useState } from "react";
import { Button, ImportanceMeter, SegmentedPill } from "./ui";
import type { RequirementKind } from "../types";

const KIND_OPTIONS = [
  { key: "nice_to_have", label: "Nice-to-have" },
  { key: "must_have", label: "Must-have" },
  { key: "hard_gate", label: "Hard gate" },
];

export interface SuggestPayload {
  newText: string;
  note: string;
  newKind: RequirementKind;
  newImportance: number;
}

export default function SuggestComposer({
  requirement,
  busy = false,
  onSend,
  onCancel,
}: {
  requirement: { id?: string; text: string; kind: RequirementKind; importance?: number };
  busy?: boolean;
  onSend: (payload: SuggestPayload) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(requirement.text);
  const [note, setNote] = useState("");
  const [kind, setKind] = useState<RequirementKind>(requirement.kind);
  const [importance, setImportance] = useState(requirement.importance ?? 3);

  return (
    <div style={{ marginTop: 10, padding: "14px 16px", background: "var(--white)", border: "1.5px solid var(--accent-blue)", borderRadius: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--info-ink)", marginBottom: 8 }}>
        Suggest an edit — goes to the TA for review
      </div>
      <textarea
        className="input"
        aria-label="Suggested wording"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        style={{ width: "100%", resize: "vertical", marginBottom: 10, lineHeight: 1.5 }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 10 }}>
        <SegmentedPill options={KIND_OPTIONS} value={kind}
                       onChange={(k) => setKind(k as RequirementKind)} />
        {kind !== "hard_gate" ? (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11.5, color: "var(--ink-soft)", fontWeight: 700 }}>Importance</span>
            <ImportanceMeter value={importance} onChange={setImportance} />
          </span>
        ) : (
          <span style={{ fontSize: 11.5, color: "var(--ink-soft)" }}>gate — no weight</span>
        )}
      </div>
      <input
        className="input"
        aria-label="Note to the recruiter"
        placeholder="Why? (optional note to the recruiter)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        style={{ marginBottom: 10 }}
      />
      <div style={{ display: "flex", gap: 8 }}>
        <Button size="sm" disabled={busy || !text.trim()}
                onClick={() => onSend({ newText: text.trim(), note: note.trim(), newKind: kind, newImportance: importance })}>
          Send suggestion
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}
