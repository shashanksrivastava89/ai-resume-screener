// Requisition audit cards (P46, the "Requisitions" tab): each requisition with a
// mini event timeline. Honors the ?req= deep link from History's "View audit" —
// the server narrows to that requisition and the card is highlighted.
import { useFetch } from "../../hooks";
import { dualStamp } from "../../time";
import { CenterLoading, PageError } from "../ui";
import type { ReqAuditCard, ReqAuditResponse } from "../../types";

const STATUS_STYLE: Record<string, { bg: string; fg: string }> = {
  active: { bg: "var(--brand-green-tint)", fg: "var(--brand-green-deep)" },
  closed: { bg: "var(--neutral-150)", fg: "var(--ink-soft)" },
};

function Card({ c, highlight }: { c: ReqAuditCard; highlight: boolean }) {
  const s = STATUS_STYLE[c.status] ?? STATUS_STYLE.active;
  return (
    <div
      data-req={c.req_id}
      style={{ background: "var(--white)", border: `1px solid ${highlight ? "var(--brand-green)" : "var(--border-subtle)"}`, borderRadius: 14, padding: "18px 22px", boxShadow: highlight ? "0 0 0 3px var(--brand-green-tint)" : "none" }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 4 }}>
        <h3 style={{ fontSize: 14.5, margin: 0 }}>{c.title}</h3>
        <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 9px", borderRadius: 999, background: s.bg, color: s.fg }}>{c.status}</span>
      </div>
      <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 12 }}>
        <span style={{ fontFamily: "monospace", color: "var(--ink-muted)" }}>{c.req_code || "—"}</span>
        {" · "}{c.jurisdiction} · {c.region}{c.created_by ? ` · added by ${c.created_by}` : ""}
      </div>
      {c.events.length === 0 ? (
        <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>No recorded activity yet.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {c.events.map((e, i) => (
            <div key={i} style={{ display: "flex", gap: 10, fontSize: 12.5 }}>
              <span style={{ flex: "0 0 92px", color: "var(--ink-muted)" }}>{e.ts ? dualStamp(e.ts) : ""}</span>
              <span style={{ flex: "1 1 auto", color: "var(--ink)" }}>{e.summary}{e.actor ? <span style={{ color: "var(--ink-soft)" }}>{` · ${e.actor}`}</span> : ""}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ReqAuditCards({ selectedReq, region }: { selectedReq: string | null; region: string | null }) {
  const params = new URLSearchParams();
  if (selectedReq) params.set("req", selectedReq);
  if (region) params.set("region", region);
  const qs = params.toString();
  const { data, loading, error } = useFetch<ReqAuditResponse>(`/api/audit/requisitions${qs ? "?" + qs : ""}`);

  if (loading) return <CenterLoading />;
  if (error) return <PageError message={error} />;
  const cards = data?.requisitions ?? [];
  if (cards.length === 0) return <div style={{ padding: "30px 0", fontSize: 13, color: "var(--ink-soft)" }}>No requisitions in this view.</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {cards.map((c) => (
        <Card key={c.req_id} c={c} highlight={c.req_id === selectedReq} />
      ))}
    </div>
  );
}
