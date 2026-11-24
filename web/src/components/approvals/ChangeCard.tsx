// One pending configuration change (v3 design's approval card, adapted to the
// single-second-leader rule): the diff table (old struck → new green), the
// leveled system warnings ("Acknowledge before approving:" on red), the
// proposer's reasoning verbatim, the inline how-scoring-works read, and the
// signature slot. The proposer sees withdraw instead of approve.
import { useState } from "react";
import type { CSSProperties } from "react";
import { Button, Callout, Textarea } from "../ui";
import { useScoringConfig } from "../../hooks";
import type { ChangeRequest, SystemWarning } from "../../types";
import { WHY_FOOT } from "../scoring/HowTab";

function kindChip(c: ChangeRequest): string {
  if (c.kind === "scoring_model") return `Scoring model v${c.base_version + 1} — ${c.summary.length} change${c.summary.length === 1 ? "" : "s"}`;
  return `${c.target.split(":")[1] ?? c.target} hiring rules — ${c.summary.length} change${c.summary.length === 1 ? "" : "s"}`;
}

const lightWarn = (level: SystemWarning["level"]): CSSProperties =>
  level === "red"
    ? { fontSize: 12, lineHeight: 1.55, fontWeight: 700, background: "rgba(195,69,69,0.08)", border: "1.5px solid var(--status-error, #C34545)", color: "var(--status-error, #B3261E)", borderRadius: 9, padding: "8px 11px" }
    : level === "info"
      ? { fontSize: 12, lineHeight: 1.55, color: "var(--ink-soft)", padding: "0 2px" }
      : { fontSize: 12, lineHeight: 1.55, color: "var(--warn-ink)", background: "var(--surface-warn)", borderRadius: 9, padding: "8px 11px" };

/** The approver's 60-second read — numbers quoted live from the scoring model. */
function ApproverExplainer() {
  const scoring = useScoringConfig();
  const partial = scoring?.coverage.match_value.partial ?? 0.5;
  const none = scoring?.coverage.match_value.none ?? 0;
  const prefCap = scoring?.preferences.per_preference_cap_points ?? 2;
  const rows = [
    { n: "1", head: "The checklist does most of the work.", body: `Each line has a weight; clear evidence pays the full weight, some evidence pays ${partial}×, none pays ${none}. Added up, that's the base — out of 100, re-checkable months later, line by line.` },
    { n: "2", head: "Judged signals nudge, never decide.", body: "Evidence-backed extras — quota attainment, regulated-environment exposure — move a few points at most; every one has a hard cap." },
    { n: "3", head: "The fairness critic reads every reason.", body: "Any adjustment leaning on a never-scored topic — gaps, age, salary, prestige… — is rejected by code. Still visible, worth 0." },
    { n: "4", head: "People decide.", body: `Nobody is auto-rejected. Personal preferences nudge ±${prefCap} at most, and every decision needs a human and a written reason.` },
  ];
  return (
    <div style={{ background: "var(--brand-green-tint)", borderRadius: 10, padding: "12px 14px", marginBottom: 12, display: "flex", flexDirection: "column", gap: 7 }}>
      {rows.map((w) => (
        <div key={w.n} style={{ display: "flex", gap: 9, fontSize: 12, lineHeight: 1.55 }}>
          <span style={{ fontWeight: 700, color: "var(--brand-green-deep)", flex: "0 0 auto" }}>{w.n}</span>
          <span><strong>{w.head}</strong> {w.body}</span>
        </div>
      ))}
      <div style={{ fontSize: 11.5, color: "var(--ink-soft)", lineHeight: 1.55 }}>{WHY_FOOT}</div>
    </div>
  );
}

export default function ChangeCard({ change, viewer, busy, onApprove, onDecline, onWithdraw }: {
  change: ChangeRequest;
  viewer: string;
  busy: boolean;
  onApprove: () => void;
  onDecline: (note: string) => void;
  onWithdraw: () => void;
}) {
  const [declining, setDeclining] = useState(false);
  const [note, setNote] = useState("");
  const [explOpen, setExplOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const mine = change.proposed_by.trim().toLowerCase() === viewer.trim().toLowerCase();
  const hasRed = change.system_warnings.some((w) => w.level === "red");
  const diff = change.diff?.length ? change.diff : change.summary.map((s) => ({ path: "", label: s, from: "", to: "" }));
  // There is ONE scoring model for the organisation. This said "applies to India
  // (IN) requisitions only — other regions keep their own model", which was never
  // true: an approver was told a global change was local (2026-09-12).
  const scope = change.kind === "scoring_model"
    ? "applies to every requisition, everywhere — there is one scoring model"
    : "applies to this location's requisitions only — the scoring model is unchanged";

  return (
    <div style={{ background: "var(--white)", border: "1.5px solid var(--accent-yellow)", borderRadius: 16, padding: "20px 24px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 4 }}>
        <span style={{ fontSize: 15, fontWeight: 700 }}>{kindChip(change)}</span>
        <span style={{ display: "inline-flex", alignItems: "center", fontSize: 11.5, fontWeight: 700, padding: "3px 10px", borderRadius: 999, background: "var(--surface-warn)", color: "var(--warn-ink)", whiteSpace: "nowrap" }}>
          Pending · awaiting a second leader
        </span>
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-soft)", marginBottom: 10 }}>
        Proposed by {change.proposed_by} · {change.proposed_at?.slice(0, 10)} · {scope}
      </div>
      <div style={{ fontSize: 12.5, lineHeight: 1.55, fontStyle: "italic", background: "var(--neutral-100)", borderRadius: 9, padding: "9px 12px", marginBottom: 12 }}>
        Reasoning: “{change.rationale}”
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 12 }}>
        {diff.map((d, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5, padding: "7px 11px", border: "1px solid var(--neutral-150)", borderRadius: 9, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 700, flex: "1 1 200px" }}>{d.label}</span>
            {(d.from || d.to) && (
              <>
                <span style={{ fontFamily: "monospace", color: "var(--ink-muted)", textDecoration: "line-through" }}>{d.from}</span>
                <span style={{ color: "var(--ink-muted)" }}>→</span>
                <span style={{ fontFamily: "monospace", fontWeight: 700, color: "var(--brand-green-deep)" }}>{d.to}</span>
              </>
            )}
          </div>
        ))}
      </div>

      {change.system_warnings.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, marginBottom: 12 }}>
          {change.system_warnings.map((w, i) => (
            <div key={i} style={lightWarn(w.level)}>
              {w.level === "red" ? "Acknowledge before approving: " : ""}{w.text}
            </div>
          ))}
        </div>
      )}

      <button
        onClick={() => setExplOpen((o) => !o)}
        style={{ display: "inline-block", border: 0, background: "none", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer", color: "var(--brand-green-deep)", marginBottom: 10, padding: 0, textAlign: "left" }}
      >
        {explOpen
          ? "Hide — how scoring works"
          : "How scoring works — the 60-second read (approvers: read once, it travels with your sign-off)"}
      </button>
      {explOpen && <ApproverExplainer />}

      {err && <div style={{ marginBottom: 10 }}><Callout variant="warn" title="Couldn't complete that">{err}</Callout></div>}

      <div style={{ borderTop: "1px solid var(--neutral-150)", paddingTop: 12 }}>
        {mine ? (
          <div style={{ fontSize: 12, color: "var(--ink-soft)" }}>
            You proposed this — you can’t sign it off yourself.{" "}
            <button onClick={onWithdraw} disabled={busy} style={{ border: 0, background: "none", padding: 0, fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer", color: "var(--brand-green-deep)" }}>
              Withdraw proposal
            </button>
          </div>
        ) : declining ? (
          <div>
            <Textarea rows={2} placeholder="Why not — sent to the proposer and kept in the audit log…"
              value={note} onChange={(e) => setNote(e.target.value)} aria-label="Decline note" />
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 8 }}>
              <Button size="sm" variant="ghost" onClick={() => setDeclining(false)} disabled={busy}>Cancel</Button>
              <Button size="sm" variant="secondary" disabled={busy}
                onClick={() => {
                  if (!note.trim()) { setErr("A note explaining the decline is required."); return; }
                  setErr(null);
                  onDecline(note.trim());
                }}>
                Confirm decline
              </Button>
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ display: "inline-flex", alignItems: "center", fontSize: 11.5, fontWeight: 700, padding: "3px 10px", borderRadius: 999, background: "var(--neutral-150)", color: "var(--hue-slate-ink)", whiteSpace: "nowrap" }}>
              Second leader · awaiting
            </span>
            <button
              onClick={() => setDeclining(true)}
              disabled={busy}
              style={{ border: 0, background: "none", padding: 0, fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer", color: "var(--ink-soft)" }}
            >
              Return to proposer with a note
            </button>
            <span style={{ flex: "1 1 auto" }} />
            <button
              onClick={onApprove}
              disabled={busy}
              style={{ height: 32, padding: "0 15px", borderRadius: 999, border: 0, background: "var(--brand-green)", color: "#fff", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap", flex: "0 0 auto" }}
            >
              {busy ? "Publishing…" : hasRed ? "Acknowledge exposure & approve" : "Approve & publish"}
            </button>
          </div>
        )}
        <div style={{ fontSize: 11, color: "var(--ink-muted)", lineHeight: 1.5, marginTop: 10 }}>
          {change.kind === "scoring_model"
            ? "One scoring model serves every location; a location's hiring-rules pack turns the dial on top of it."
            : "The scoring model is unchanged by this — a location's pack decides which signals fire there and how far each may move a score."}
        </div>
      </div>
    </div>
  );
}
