// "My preferences" (v3 design): the user's plain-language hiring nudges. Every
// one waits for an HR Head's sign-off (2026-10-16, pen-test RS-01 — clean ones used
// to go live on their own, and a list of phrases cannot catch every disguise);
// critic-flagged ones carry the warning to the approver; protected-attribute ones
// commit as a visible BLOCKED record — nothing disappears quietly. The foot note
// quotes the governed scoring model live.
import { useState } from "react";
import type { CSSProperties } from "react";
import { Page } from "../layout/AppShell";
import { CenterLoading, PageError } from "../components/ui";
import { ConfirmModal, useToast, type ConfirmSpec } from "../components/overlays";
import { useFetch, useScoringConfig } from "../hooks";
import { useMeRefresh } from "../MeContext";
import { post, del } from "../api";
import type { Preference, TextCheck } from "../types";

const CHIP: Record<string, { label: string; bg: string; fg: string }> = {
  approved: { label: "Approved", bg: "var(--brand-green-tint)", fg: "var(--brand-green-deep)" },
  pending: { label: "Waiting for HR Head sign-off", bg: "var(--surface-warn)", fg: "var(--warn-ink)" },
  rereview: { label: "Re-review needed", bg: "var(--surface-warn)", fg: "var(--warn-ink)" },
  declined: { label: "Declined", bg: "var(--neutral-150)", fg: "var(--ink-soft)" },
  blocked: { label: "Blocked", bg: "var(--ink)", fg: "#fff" },
};

function fmtDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

function PrefCard({ p, capPoints, onWithdraw, onRemove }: {
  p: Preference; capPoints: number;
  onWithdraw: (id: string) => void; onRemove: (id: string) => void;
}) {
  const chip = CHIP[p.status] ?? CHIP.declined;
  const blocked = p.status === "blocked";
  const flaggedish = p.status === "pending" || p.status === "rereview";
  const capNote = p.status === "rereview"
    ? "Suspended until re-approved."
    : p.status === "approved"
      ? `Capped at +${capPoints} — warning on record.`
      : `Capped at +${capPoints} if approved.`;
  const linkStyle: CSSProperties = {
    border: 0, background: "none", padding: 0, fontFamily: "inherit", cursor: "pointer",
    fontSize: 11.5, fontWeight: 700, color: "var(--brand-green-deep)", whiteSpace: "nowrap",
  };
  return (
    <div
      style={{
        background: blocked ? "var(--neutral-100)" : "var(--white)",
        border: `1px solid ${flaggedish ? "var(--accent-yellow)" : "var(--border-subtle)"}`,
        borderRadius: 12, padding: "13px 16px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div
          style={{
            flex: "1 1 auto", minWidth: 0, fontSize: 13,
            border: `1px solid ${blocked ? "var(--border-subtle)" : "var(--border-strong)"}`,
            borderRadius: 9, padding: "8px 11px", background: "var(--white)",
            ...(blocked ? { color: "var(--ink-muted)", textDecoration: "line-through" } : {}),
          }}
        >
          {p.text}
        </div>
        <span style={{ display: "inline-flex", alignItems: "center", fontSize: 11.5, fontWeight: 700, padding: "3px 10px", borderRadius: 999, background: chip.bg, color: chip.fg, whiteSpace: "nowrap", flex: "0 0 auto" }}>
          {chip.label}
        </span>
        {p.status === "pending" && (
          <button onClick={() => onWithdraw(p.id)} style={linkStyle}>Withdraw request</button>
        )}
        {p.status !== "pending" && (
          <button onClick={() => onRemove(p.id)} title="Remove preference" aria-label={`Remove ${p.text}`}
            style={{ border: 0, background: "none", padding: 0, fontFamily: "inherit", cursor: "pointer", fontSize: 14, color: "var(--ink-muted)" }}>
            ✕
          </button>
        )}
      </div>
      {p.warning && !blocked && (
        <div style={{ display: "flex", gap: 8, fontSize: 12, lineHeight: 1.5, color: "var(--warn-ink)", background: "var(--surface-warn)", borderRadius: 9, padding: "8px 11px", marginTop: 10 }}>
          <span style={{ fontWeight: 700, flex: "0 0 auto" }}>⚠</span>
          <span>{p.warning} <strong>{capNote}</strong></span>
        </div>
      )}
      {(p.status === "approved" || p.status === "rereview") && p.review_due && (
        <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 8 }}>
          {p.approved_by ? `Approved by ${p.approved_by}` : "Approved"}
          {p.approved_at ? ` · ${fmtDate(p.approved_at)}` : ""} ·{" "}
          {p.status === "rereview" ? "Re-review overdue — approval expired" : `Re-review due ${fmtDate(p.review_due)}`}
        </div>
      )}
      {p.status === "pending" && !p.warning && (
        <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 8 }}>
          Added {fmtDate(p.ts)} · no conflicts found · applies from each requisition's next
          screen once an HR Head signs it off
        </div>
      )}
      {blocked && p.warning && (
        <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 8 }}>{p.warning}</div>
      )}
      {p.status === "declined" && p.decline_note && (
        <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 8 }}>Note: “{p.decline_note}”</div>
      )}
    </div>
  );
}

export default function MyPreferences() {
  const { data, error, loading, reload } = useFetch<{ preferences: Preference[] }>("/api/preferences");
  const scoring = useScoringConfig();
  const refreshMe = useMeRefresh();
  const toast = useToast();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null);

  const capPoints = scoring?.preferences.per_preference_cap_points ?? 2;
  const months = scoring?.preferences.review_months ?? 6;
  const version = scoring?.version;

  async function commit(text: string, outcome: string) {
    setBusy(true);
    try {
      await post("/api/preferences", { text });
      setDraft("");
      setConfirm(null);
      reload();
      refreshMe(); // bump the HR-Head approval badge when a warn pref lands
      toast(outcome);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onCheckAdd() {
    const text = draft.trim();
    if (!text) return;
    setBusy(true);
    try {
      const check = await post<TextCheck>("/api/preferences/check", { text });
      if (check.level === "ok") {
        setConfirm({
          title: "This needs HR Head sign-off",
          text,
          reason: "The automated checks found no conflicts. Every preference is reviewed by an HR Head before it applies.",
          question: "Send it for sign-off? It applies from each requisition's next screen once approved.",
          cta: "Send for sign-off",
          onProceed: () => commit(text, "Sent for sign-off — the HR Head was notified"),
        });
      } else if (check.level === "warn") {
        setConfirm({
          title: "This needs HR Head approval",
          text,
          reason: check.reason,
          question: "Approval from the HR Head is required because of this. Do you want to proceed? An approval notification will be sent.",
          cta: "Proceed — send for approval",
          onProceed: () => commit(text, "Sent for approval — the HR Head was notified"),
        });
      } else {
        // No workaround offered (2026-09-07): the old copy said "reword it without
        // the protected attribute", which tells someone how to get the same intent
        // past a fairness rule. It stops here, and the attempt is on the record.
        setConfirm({
          title: "This can't be a preference",
          text,
          reason: check.reason,
          question: "It won't be used in any screening. We keep a record that it was asked for, so nothing disappears quietly.",
          cta: "I understand",
          onProceed: () => commit(text, "Not used — recorded"),
        });
      }
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Page><CenterLoading label="Loading your preferences…" /></Page>;
  if (error) return <Page><PageError message={error} /></Page>;

  const prefs = (data?.preferences ?? []).filter((p) => p.status !== "withdrawn");

  return (
    <Page max={900}>
      {confirm && <ConfirmModal spec={confirm} onCancel={() => setConfirm(null)} />}
      <h1 style={{ fontSize: 24, letterSpacing: "-0.01em", margin: "0 0 4px" }}>My screening preferences</h1>
      <p style={{ fontSize: 13, color: "var(--ink-soft)", lineHeight: 1.55, marginTop: 0, marginBottom: 18 }}>
        {/* What a preference DOES, said first (2026-09-25): a pen tester added one, screened a
            résumé stating the opposite, saw no change and asked what he was missing. It only
            ever adds, only past the hard gates, only from the next screen — none of which
            this page said. */}
        Yours — applied to requisitions you created or are the hiring manager for, from their
        next screening run. A preference only adds: up to +{capPoints} for a candidate whose
        résumé clearly shows it, never a deduction — and only for candidates who meet every
        hard gate. Every preference waits for an HR Head's sign-off before it applies — the
        ones the bias critic flags carry its warning to them. Protected attributes → blocked.
        Approvals expire and need periodic re-review.
      </p>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 16 }}>
        {prefs.length === 0 ? (
          <div style={{ fontSize: 13, color: "var(--ink-soft)", padding: "14px 0" }}>
            No preferences yet — add one below.
          </div>
        ) : (
          prefs.map((p) => (
            <PrefCard
              key={p.id}
              p={p}
              capPoints={capPoints}
              onWithdraw={(id) => del(`/api/preferences/${id}`).then(() => {
                reload();
                refreshMe();
                toast("Approval request withdrawn — the HR Head was notified");
              })}
              onRemove={(id) => del(`/api/preferences/${id}`).then(() => {
                reload();
                toast(`Preference removed${p.status === "approved" || p.status === "rereview" ? " — logged in audit" : ""}`);
              })}
            />
          ))
        )}
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") onCheckAdd(); }}
          placeholder="Add a preference in plain language — checked against the jurisdiction packs and the bias critic…"
          aria-label="New preference"
          style={{ flex: "1 1 auto", height: 40, border: "1px solid var(--border-strong)", borderRadius: 10, padding: "0 12px", fontSize: 13, background: "var(--white)", color: "var(--ink)", fontFamily: "inherit" }}
        />
        <button
          onClick={onCheckAdd}
          disabled={busy || !draft.trim()}
          style={{ height: 36, padding: "0 16px", borderRadius: 999, border: "1.5px solid var(--brand-green)", background: "var(--white)", color: "var(--brand-green-deep)", fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}
        >
          Check &amp; add
        </button>
      </div>
      <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 10, lineHeight: 1.55 }}>
        Every preference needs an HR Head's sign-off, and nobody signs off their own; approved
        ones stay capped at +{capPoints} and expire after {months} months. Protected
        attributes — blocked, no approval path.
        {version != null && <> These numbers quote scoring model v{version} live — change the model and this copy follows.</>}
      </div>
    </Page>
  );
}
