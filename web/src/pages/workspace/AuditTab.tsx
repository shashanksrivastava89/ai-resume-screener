// The workspace's Audit tab (new in P31): renders GET /api/roles/{id}/trail —
// checklist versions, runs (with the hiring-rules pack), lifecycle events, and
// the decision summary with verbatim reason quotes. Recruiter language, no
// hashes. The search-first Audit Explorer is P45/P46; this is the per-req view.
import { Page } from "../../layout/AppShell";
import { CenterLoading, PageError } from "../../components/ui";
import { useFetch } from "../../hooks";
import { useWorkspace } from "./context";
import type { Trail } from "../../types";

const KIND_DOT: Record<string, string> = {
  created: "var(--border-strong)",
  checklist: "var(--brand-green)",
  run: "var(--brand-green)",
  screen_completed: "var(--brand-green)",
  req_close: "var(--warn-dot)",
  req_reopen: "var(--warn-dot)",
  req_snooze: "var(--border-strong)",
};

function when(ts: string): string {
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const ACTION_FG: Record<string, string> = {
  "Move forward": "var(--brand-green-deep)",
  Discuss: "var(--warn-ink)",
  "Not a fit": "#4A4A4A",
};

export default function AuditTab() {
  const { role } = useWorkspace();
  const { data: trail, error, loading } = useFetch<Trail>(`/api/roles/${role.req_id}/trail`);

  if (loading) return <Page max={900}><CenterLoading label="Loading the trail…" /></Page>;
  if (error || !trail) return <Page max={900}><PageError message={error || "Couldn't load the trail."} /></Page>;

  const ds = trail.decision_summary;
  const decidedTotal = ds.forward + ds.discuss + ds.not;

  return (
    <Page max={900}>
      {/* decisions with verbatim reasons */}
      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, overflow: "hidden" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "16px 22px", borderBottom: "1px solid var(--border-subtle)", flexWrap: "wrap" }}>
          <span style={{ fontSize: 15, fontWeight: 700 }}>Decisions</span>
          <span style={{ fontSize: 12.5, fontWeight: 700, padding: "4px 11px", borderRadius: 999, background: "var(--brand-green-tint)", color: "var(--brand-green-deep)" }}>Move forward {ds.forward}</span>
          <span style={{ fontSize: 12.5, fontWeight: 700, padding: "4px 11px", borderRadius: 999, background: "var(--surface-warn)", color: "var(--warn-ink)" }}>Discuss {ds.discuss}</span>
          <span style={{ fontSize: 12.5, fontWeight: 700, padding: "4px 11px", borderRadius: 999, background: "var(--neutral-150)", color: "var(--ink)" }}>Not a fit {ds.not}</span>
        </div>
        {decidedTotal === 0 && (
          <div style={{ padding: "20px 22px", fontSize: 13, color: "var(--ink-soft)" }}>
            No decisions recorded yet — they'll appear here with the reviewer's own words.
          </div>
        )}
        {ds.quotes.map((q, i) => (
          <div key={i} style={{ padding: "13px 22px", borderBottom: "1px solid var(--neutral-150)" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 14, fontWeight: 700 }}>{q.name}</span>
              <span style={{ fontSize: 12.5, fontWeight: 700, color: ACTION_FG[q.action] ?? "var(--ink-soft)" }}>{q.action}</span>
              <span style={{ fontSize: 12, color: "var(--ink-muted)", marginLeft: "auto" }}>{q.reviewer} · {when(q.ts)}</span>
            </div>
            {q.reason && (
              <div style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 4 }}>“{q.reason}”</div>
            )}
          </div>
        ))}
      </div>

      {/* timeline */}
      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, overflow: "hidden", marginTop: 16 }}>
        <div style={{ padding: "16px 22px", borderBottom: "1px solid var(--border-subtle)", fontSize: 15, fontWeight: 700 }}>Timeline</div>
        {trail.entries.length === 0 && (
          <div style={{ padding: "20px 22px", fontSize: 13, color: "var(--ink-soft)" }}>Nothing recorded yet.</div>
        )}
        {trail.entries.map((e, i) => (
          <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "13px 22px", borderBottom: "1px solid var(--neutral-150)" }}>
            <span style={{ flex: "0 0 auto", width: 9, height: 9, borderRadius: "50%", marginTop: 5, background: KIND_DOT[e.kind] ?? "var(--border-strong)" }} />
            <div style={{ flex: "1 1 auto", minWidth: 0 }}>
              <div style={{ fontSize: 13.5, color: "var(--ink)" }}>{e.summary}</div>
              <div style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 2 }}>
                {when(e.ts)}{e.actor ? ` · ${e.actor}` : ""}
              </div>
            </div>
          </div>
        ))}
      </div>

      <p style={{ fontSize: 12.5, color: "var(--ink-soft)", margin: "16px 2px 0", lineHeight: 1.6 }}>
        {trail.note} Full explorer: Audit &amp; History (HR Head / IT).
      </p>
    </Page>
  );
}
