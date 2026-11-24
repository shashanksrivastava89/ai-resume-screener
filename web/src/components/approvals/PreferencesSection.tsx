// The HR-Head preference queue (v3 design): green select boxes + bulk approve,
// each card carrying the owner, the amber critic warning with the cap note in
// bold, and decline-with-note. The cap/re-review copy quotes the governed
// scoring model live (never a literal).
import { useState } from "react";
import type { CSSProperties } from "react";
import { Button, Callout, Textarea, CenterLoading, PageError } from "../ui";
import { useFetch, useScoringConfig } from "../../hooks";
import { useMe, useMeRefresh } from "../../MeContext";
import { post } from "../../api";
import { useToast } from "../overlays";
import type { Preference } from "../../types";

function fmtDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

function SelectBox({ checked, onToggle, label }: { checked: boolean; onToggle: () => void; label: string }) {
  return (
    <button
      onClick={onToggle}
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      style={{
        flex: "0 0 auto", width: 19, height: 19, borderRadius: 6, display: "inline-flex",
        alignItems: "center", justifyContent: "center", cursor: "pointer", fontSize: 12,
        color: "#fff", fontFamily: "inherit",
        border: `1.5px solid ${checked ? "var(--brand-green)" : "var(--border-strong)"}`,
        background: checked ? "var(--brand-green)" : "var(--white)",
      }}
    >
      {checked ? "✓" : ""}
    </button>
  );
}

function Row({ p, capPoints, checked, onToggle, onDecline, own }: {
  p: Preference; capPoints: number; checked: boolean; onToggle: () => void;
  onDecline: (note: string) => void; own: boolean;
}) {
  const [declining, setDeclining] = useState(false);
  const [note, setNote] = useState("");
  const reReview = p.status === "rereview";
  const chipStyle: CSSProperties = {
    display: "inline-flex", alignItems: "center", fontSize: 11.5, fontWeight: 700,
    padding: "3px 10px", borderRadius: 999, whiteSpace: "nowrap", flex: "0 0 auto",
    background: "var(--surface-warn)", color: "var(--warn-ink)",
  };
  return (
    <div style={{ background: "var(--white)", border: "1px solid var(--accent-yellow)", borderRadius: 12, padding: "13px 16px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        {own ? (
          <span title="Another HR Head signs this one off" aria-label="Your own preference"
            style={{ flex: "0 0 auto", width: 19, height: 19, borderRadius: 6, border: "1.5px dashed var(--border-strong)" }} />
        ) : (
          <SelectBox checked={checked} onToggle={onToggle} label={`Select ${p.text}`} />
        )}
        <div style={{ flex: "1 1 auto", minWidth: 0, fontSize: 13, border: "1px solid var(--border-strong)", borderRadius: 9, padding: "8px 11px", background: "var(--white)" }}>
          {p.text}
        </div>
        <span style={{ fontSize: 11.5, color: "var(--ink-soft)", whiteSpace: "nowrap" }}>
          {p.owner_name || p.owner}
        </span>
        <span style={chipStyle}>{reReview ? "Re-review needed" : own ? "Yours — another HR Head signs it off" : "Waiting for your sign-off"}</span>
      </div>
      {!p.warning && !reReview && (
        <div style={{ fontSize: 12, lineHeight: 1.5, color: "var(--ink-soft)", background: "var(--neutral-100)", borderRadius: 9, padding: "8px 11px", marginTop: 10 }}>
          The automated checks found no conflicts — it needs a sign-off before it applies.{" "}
          <strong>Capped at +{capPoints} if approved.</strong>
        </div>
      )}
      {p.warning && (
        <div style={{ display: "flex", gap: 8, fontSize: 12, lineHeight: 1.5, color: "var(--warn-ink)", background: "var(--surface-warn)", borderRadius: 9, padding: "8px 11px", marginTop: 10 }}>
          <span style={{ fontWeight: 700, flex: "0 0 auto" }}>⚠</span>
          <span>
            {p.warning}{" "}
            <strong>{reReview ? "Suspended until re-approved." : `Capped at +${capPoints} if approved.`}</strong>
          </span>
        </div>
      )}
      {reReview && p.review_due && (
        <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 8 }}>
          Re-review was due {fmtDate(p.review_due)} — approval expired.
        </div>
      )}
      {declining ? (
        <div style={{ marginTop: 10 }}>
          <Textarea rows={2} placeholder="Reason (sent to the owner)…" value={note}
            onChange={(e) => setNote(e.target.value)} aria-label="Decline note" />
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 8 }}>
            <Button size="sm" variant="ghost" onClick={() => setDeclining(false)}>Cancel</Button>
            <Button size="sm" variant="secondary" onClick={() => onDecline(note)}>Confirm decline</Button>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
          <button onClick={() => setDeclining(true)} style={{ background: "none", border: 0, padding: 0, cursor: "pointer", font: "inherit", fontSize: 12, fontWeight: 700, color: "var(--ink-soft)" }}>
            Decline with note
          </button>
        </div>
      )}
    </div>
  );
}

export default function PreferencesSection() {
  const { data, error, loading, reload } = useFetch<{ pending: Preference[]; count: number }>("/api/preferences/pending");
  const scoring = useScoringConfig();
  const refreshMe = useMeRefresh();
  const toast = useToast();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const pending = data?.pending ?? [];
  const capPoints = scoring?.preferences.per_preference_cap_points ?? 2;
  const me = useMe();
  const isOwn = (p: Preference) => !!me?.user && (p.owner ?? "").toLowerCase() === me.user.toLowerCase();
  const selectable = pending.filter((p) => !isOwn(p));

  function toggle(id: string) {
    setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }
  function toggleAll() {
    setSelected((s) => (s.size === selectable.length ? new Set() : new Set(selectable.map((p) => p.id))));
  }

  async function run(fn: () => Promise<unknown>, outcome?: string) {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      setSelected(new Set());
      reload();
      refreshMe(); // clear/decrement the nav badge
      if (outcome) toast(outcome);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <CenterLoading label="Loading the approval queue…" />;
  if (error) return <PageError message={error} />;

  return (
    <section aria-label="Preferences">
      {err && <div style={{ marginBottom: 12 }}><Callout variant="warn" title="Couldn't complete that">{err}</Callout></div>}

      {pending.length === 0 ? (
        <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "26px 24px", textAlign: "center", fontSize: 13, color: "var(--ink-soft)" }}>
          Nothing waiting. Every new preference lands here for an HR Head’s sign-off.
        </div>
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <SelectBox
                checked={selected.size === selectable.length && selectable.length > 0}
                onToggle={toggleAll}
                label="Select all pending"
              />
              <span style={{ fontSize: 12, fontWeight: 700, color: "var(--ink-soft)" }}>Select all pending</span>
            </span>
            <button
              disabled={busy || selected.size === 0}
              onClick={() => run(
                () => post("/api/preferences/approve", { pref_ids: [...selected] }),
                `${selected.size} preference(s) approved — on record with any warnings`)}
              style={{ height: 32, padding: "0 15px", borderRadius: 999, border: 0, background: selected.size ? "var(--brand-green)" : "var(--neutral-300)", color: "#fff", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: selected.size ? "pointer" : "default" }}
            >
              Approve selected ({selected.size})
            </button>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {pending.map((p) => (
              <Row key={p.id} p={p} capPoints={capPoints} own={isOwn(p)} checked={selected.has(p.id)} onToggle={() => toggle(p.id)}
                onDecline={(note) => run(() => post(`/api/preferences/${p.id}/decline`, { note }),
                  "Declined — the owner sees your note")} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
