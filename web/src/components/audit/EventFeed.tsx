// The merged audit feed (P46, the "All" tab). Server handles the kind chip and
// the region lens; the header search filters client-side over what's loaded so
// typing doesn't refetch. `audit_view` reads are excluded unless the Views chip
// is picked (the server enforces that; the chip just sets kind=Views).
import { useState } from "react";
import { useFetch } from "../../hooks";
import { dualStamp } from "../../time";
import { CenterLoading, PageError } from "../ui";
import type { FeedResponse } from "../../types";

const KIND_STYLE: Record<string, { bg: string; fg: string }> = {
  Decision: { bg: "var(--brand-green-tint)", fg: "var(--brand-green-deep)" },
  Preference: { bg: "var(--hue-blue-tint)", fg: "var(--hue-blue-ink)" },
  Rules: { bg: "var(--surface-warn)", fg: "var(--warn-ink)" },
  Close: { bg: "var(--neutral-150)", fg: "var(--ink-soft)" },
  Data: { bg: "var(--neutral-100)", fg: "var(--ink-soft)" },
  Views: { bg: "var(--neutral-100)", fg: "var(--ink-muted)" },
};

function KindChip({ kind }: { kind: string }) {
  const s = KIND_STYLE[kind] ?? KIND_STYLE.Data;
  return (
    <span style={{ flex: "0 0 auto", fontSize: 11, fontWeight: 700, padding: "2px 9px", borderRadius: 999, background: s.bg, color: s.fg }}>
      {kind}
    </span>
  );
}

export default function EventFeed({ q, region }: { q: string; region: string | null }) {
  const [kind, setKind] = useState<string | null>(null);
  const params = new URLSearchParams();
  if (kind) params.set("kind", kind);
  if (region) params.set("region", region);
  const qs = params.toString();
  const { data, loading, error } = useFetch<FeedResponse>(`/api/audit/feed${qs ? "?" + qs : ""}`);

  if (loading) return <CenterLoading />;
  if (error) return <PageError message={error} />;

  const needle = q.trim().toLowerCase();
  const rows = (data?.entries ?? []).filter((e) =>
    !needle || [e.summary, e.actor, e.candidate, e.req_id].some((v) => (v ?? "").toLowerCase().includes(needle))
  );
  const chips = ["All", ...(data?.kinds ?? [])];

  return (
    <div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
        {chips.map((c) => {
          const on = c === "All" ? kind === null : kind === c;
          return (
            <button
              key={c}
              onClick={() => setKind(c === "All" ? null : c)}
              aria-pressed={on}
              className={"pill" + (on ? " active" : "")}
            >
              {c}
            </button>
          );
        })}
      </div>

      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "6px 20px" }}>
        {rows.length === 0 && (
          <div style={{ padding: "18px 0", fontSize: 13, color: "var(--ink-soft)" }}>Nothing recorded yet for this view.</div>
        )}
        {rows.map((e, i) => (
          <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "13px 0", borderBottom: i < rows.length - 1 ? "1px solid var(--neutral-150)" : "none" }}>
            <KindChip kind={e.kind} />
            <div style={{ flex: "1 1 auto", minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>{e.summary}</div>
              <div style={{ fontSize: 11.5, color: "var(--ink-soft)", marginTop: 1 }}>
                {e.actor ? `${e.actor} · ` : ""}{e.ts ? dualStamp(e.ts) : ""}
                {e.region ? ` · ${e.region}` : ""}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
