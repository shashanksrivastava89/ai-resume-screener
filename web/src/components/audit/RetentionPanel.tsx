// PII & retention (P46): the purge queue + a legal-hold control + the plain-
// language per-jurisdiction duty summaries. VISIBILITY ONLY — placing a hold
// only PAUSES a purge that never runs; nothing is ever deleted.
import { useState } from "react";
import { post } from "../../api";
import { useFetch } from "../../hooks";
import { Button, CenterLoading, Input, PageError } from "../ui";
import type { RetentionResponse, RetentionRow } from "../../types";

const STATUS_LABEL: Record<string, { text: string; bg: string; fg: string }> = {
  scheduled: { text: "Purge scheduled", bg: "var(--neutral-150)", fg: "var(--ink-soft)" },
  on_hold: { text: "On legal hold", bg: "var(--surface-warn)", fg: "var(--warn-ink)" },
  awaiting_close: { text: "Awaiting close", bg: "var(--neutral-100)", fg: "var(--ink-muted)" },
};

function Row({ row, onChanged }: { row: RetentionRow; onChanged: () => void }) {
  const [holding, setHolding] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const s = STATUS_LABEL[row.status] ?? STATUS_LABEL.scheduled;

  async function applyHold() {
    setBusy(true);
    setErr(null);
    try {
      await post(`/api/audit/retention/${row.req_id}/hold`, { reason });
      setHolding(false);
      setReason("");
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function release() {
    setBusy(true);
    setErr(null);
    try {
      await post(`/api/audit/retention/${row.req_id}/release`, {});
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--neutral-150)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 240px", minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--ink)" }}>{row.title}</div>
          <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginTop: 2 }}>
            {row.jurisdiction}{row.pack ? ` · ${row.pack} pack` : ""}
            {row.purge_due ? ` · PII purges ${row.purge_due}` : " · not closed yet"}
            {row.keep_audit_until ? ` · audit kept until ${row.keep_audit_until}` : ""}
          </div>
        </div>
        <span style={{ fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: 999, background: s.bg, color: s.fg }}>{s.text}</span>
        {row.status === "on_hold" ? (
          <Button size="sm" variant="ghost" onClick={release} disabled={busy}>Release hold</Button>
        ) : (
          <Button size="sm" variant="secondary" onClick={() => setHolding((v) => !v)} disabled={busy}>Apply legal hold</Button>
        )}
      </div>
      {row.hold && <div style={{ fontSize: 12, color: "var(--warn-ink)", marginTop: 6 }}>Hold by {row.hold.by}: “{row.hold.reason}”</div>}
      {holding && (
        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for the legal hold (recorded)" style={{ flex: "1 1 260px" }} />
          <Button size="sm" onClick={applyHold} disabled={busy || !reason.trim()}>Place hold</Button>
        </div>
      )}
      {err && <div style={{ fontSize: 12, color: "var(--warn-ink)", marginTop: 6 }}>{err}</div>}
    </div>
  );
}

export default function RetentionPanel({ region }: { region: string | null }) {
  const qs = region ? `?region=${encodeURIComponent(region)}` : "";
  const { data, loading, error, reload } = useFetch<RetentionResponse>(`/api/audit/retention${qs}`);

  if (loading) return <CenterLoading />;
  if (error) return <PageError message={error} />;
  const queue = data?.queue ?? [];

  return (
    <div>
      {/* the design's summary card first: duties by jurisdiction + the next purge */}
      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "18px 22px", marginBottom: 12 }}>
        <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 8 }}>Retention — by jurisdiction</div>
        {(data?.summaries ?? []).map((s, i) => (
          <div key={i} style={{ fontSize: 12.5, lineHeight: 1.6, color: "var(--ink)" }}>{s}</div>
        ))}
        <div style={{ borderTop: "1px solid var(--neutral-150)", marginTop: 12, paddingTop: 12, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          {(() => {
            const scheduled = queue.filter((r) => r.status === "scheduled" && r.purge_due);
            const next = scheduled.map((r) => r.purge_due!).sort()[0];
            return (
              <span style={{ fontSize: 12.5, fontWeight: 700 }}>
                {next ? `Next purge · ${next}` : "No purge scheduled"}
                {scheduled.length > 0 && (
                  <span style={{ fontWeight: 400, color: "var(--ink-soft)" }}> — {scheduled.length} requisition{scheduled.length === 1 ? "" : "s"}</span>
                )}
              </span>
            );
          })()}
          <span style={{ fontSize: 12, color: "var(--ink-muted)" }}>
            Nothing is deleted here — a legal hold pauses a purge that never runs on its own.
          </span>
        </div>
      </div>

      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, overflow: "hidden" }}>
        {queue.length === 0 && <div style={{ padding: "20px", fontSize: 13, color: "var(--ink-soft)" }}>No requisitions in this view.</div>}
        {queue.map((row) => <Row key={row.req_id} row={row} onChanged={reload} />)}
      </div>
    </div>
  );
}
