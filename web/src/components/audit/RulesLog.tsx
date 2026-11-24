// Rules & criteria log (P46): config-governance publishes + checklist publishes +
// preference approve/decline, newest first, each with its rationale quote.
import { Link } from "react-router-dom";
import { useFetch } from "../../hooks";
import { dualStamp } from "../../time";
import { CenterLoading, PageError } from "../ui";
import type { RulesResponse } from "../../types";

const ACTION_LABEL: Record<string, string> = {
  publish_registry: "Rules published",
  publish_pack: "Policy pack updated",
  publish_scoring_model: "Scoring model published",
  propose_change: "Change proposed",
  decline_change: "Change declined",
  withdraw_change: "Change withdrawn",
  review_signoff: "HR + Legal review recorded",
  review: "HR + Legal review recorded",
  checklist_publish: "Checklist published",
  checklist_approval_requested: "Checklist sent for approval",
  checklist_approved: "Checklist approved",
  pref_approve: "Preference approved",
  pref_decline: "Preference declined",
  data_delete: "Data deleted",
};

export default function RulesLog({ region }: { region: string | null }) {
  const qs = region ? `?region=${encodeURIComponent(region)}` : "";
  const { data, loading, error } = useFetch<RulesResponse>(`/api/audit/rules${qs}`);

  if (loading) return <CenterLoading />;
  if (error) return <PageError message={error} />;
  const entries = data?.entries ?? [];

  return (
    <>
    <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
      <Link to="/scoring" style={{ fontSize: 12, fontWeight: 700 }}>
        Open the scoring model — every constant, version and approval ›
      </Link>
    </div>
    <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, padding: "6px 20px" }}>
      {entries.length === 0 && <div style={{ padding: "18px 0", fontSize: 13, color: "var(--ink-soft)" }}>No rule or criteria changes recorded yet.</div>}
      {entries.map((e, i) => (
        <div key={i} style={{ padding: "13px 0", borderBottom: i < entries.length - 1 ? "1px solid var(--neutral-150)" : "none" }}>
          <div style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>
            {ACTION_LABEL[e.action] ?? e.action}
            {e.version ? <span style={{ color: "var(--ink-muted)", fontWeight: 400 }}>{` v${e.version}`}</span> : ""}
            {e.summary && e.summary !== e.action ? <span style={{ color: "var(--ink-soft)", fontWeight: 400 }}>{` — ${e.summary}`}</span> : ""}
          </div>
          {e.rationale && <div style={{ fontSize: 12.5, color: "var(--ink)", marginTop: 3, fontStyle: "italic" }}>“{e.rationale}”</div>}
          <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 3 }}>
            {e.actor ? `${e.actor} · ` : ""}
            {e.approved_by ? `approved by ${e.approved_by} · ` : ""}
            {e.ts ? dualStamp(e.ts) : ""}{e.region ? ` · ${e.region}` : ""}
          </div>
        </div>
      ))}
    </div>
    </>
  );
}
