// The Checklist-tab activity rail card (P42, restyled to the v3 design), shown
// to both the recruiter and the assigned HM. Renders GET /activity newest-first
// with dual-timezone stamps. The governance note + publish actions live in the
// tab's own publish card, not here.
import { useFetch } from "../hooks";
import { dualStamp } from "../time";
import type { ActivityEntry } from "../types";

const KIND: Record<string, string> = {
  checklist_publish: "Checklist published",
  hm_assigned: "Hiring manager assigned",
  suggestion_created: "Suggested a checklist edit",
  suggestion_accepted: "Suggestion accepted",
  suggestion_declined: "Suggestion declined",
  hm_review_requested: "Review requested",
  hm_takes_sent: "Takes sent to the recruiter",
};

export default function ActivityRail({ reqId }: { reqId: string }) {
  const { data } = useFetch<{ entries: ActivityEntry[] }>(`/api/roles/${reqId}/activity`);
  const entries = data?.entries ?? [];

  return (
    <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "16px 18px" }}>
      <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink-soft)", marginBottom: 12 }}>
        Activity
      </div>

      {entries.length === 0 ? (
        <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>Nothing yet.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 11, fontSize: 12.5, lineHeight: 1.45 }}>
          {entries.map((e, i) => (
            <div key={i}>
              {e.summary || KIND[e.kind] || e.kind}
              {e.actor && <span style={{ color: "var(--ink-muted)" }}> · {e.actor}</span>}
              <div style={{ fontSize: 11, color: "var(--ink-muted)", marginTop: 1 }}>{dualStamp(e.ts, e.req_tz)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
