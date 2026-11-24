// The Approvals page (v3 design): tabbed queues — "Preferences · N" (HR Head)
// and "Scoring model · N" (any second leader) — with per-tab subtitles whose
// numbers quote the governed scoring model live. /preference-approvals
// redirects here.
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Page } from "../layout/AppShell";
import { Callout, CenterLoading, PageError } from "../components/ui";
import ChangeCard from "../components/approvals/ChangeCard";
import PreferencesSection from "../components/approvals/PreferencesSection";
import { useFetch, invalidate, useScoringConfig } from "../hooks";
import { useMe, useMeRefresh } from "../MeContext";
import { post } from "../api";
import { useToast } from "../components/overlays";
import type { ChangeRequest } from "../types";

function ChangesQueue() {
  const me = useMe();
  const refreshMe = useMeRefresh();
  const toast = useToast();
  const { data, error, loading, reload } = useFetch<{ changes: ChangeRequest[] }>(
    "/api/changes?status=pending");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function act(change: ChangeRequest, fn: () => Promise<unknown>, outcome: string) {
    setBusyId(change.change_id);
    setErr(null);
    try {
      await fn();
      toast(outcome);
      invalidate("/api/"); // scoring-config/model, jurisdictions, roles, changes all refetch
      reload();
      refreshMe(); // badge + review-pending banner
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  if (loading) return <CenterLoading label="Loading pending changes…" />;
  if (error) return <PageError message={error} />;
  const pending = data?.changes ?? [];

  return (
    <section aria-label="Configuration changes">
      {err && <div style={{ marginBottom: 12 }}><Callout variant="warn" title="Couldn't complete that">{err}</Callout></div>}
      {pending.length === 0 ? (
        <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "26px 24px", textAlign: "center", fontSize: 13, color: "var(--ink-soft)" }}>
          Nothing waiting. Scoring-model and hiring-rule changes land here for a second leader’s sign-off.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {pending.map((c) => (
            <ChangeCard key={c.change_id} change={c} viewer={me?.user ?? ""}
              busy={busyId === c.change_id}
              onApprove={() => act(c, () => post(`/api/changes/${c.change_id}/approve`, {}),
                `v${c.base_version + 1} is live — requisitions with no recorded decisions refresh automatically`)}
              onDecline={(note) => act(c, () => post(`/api/changes/${c.change_id}/decline`, { note }),
                `Returned to ${c.proposed_by} with your note — nothing changed`)}
              onWithdraw={() => act(c, () => post(`/api/changes/${c.change_id}/withdraw`, {}),
                "Withdrawn — nothing changed")} />
          ))}
        </div>
      )}
    </section>
  );
}

export default function Approvals() {
  const me = useMe();
  const scoring = useScoringConfig();
  const caps = me?.capabilities ?? [];
  const showPrefs = caps.includes("prefs_approve");
  const showModel = caps.includes("changes_approve");
  // A deep link (e.g. a scoring-model-approved notification) selects the tab.
  const [params] = useSearchParams();
  const requested = ((): "prefs" | "model" | null => {
    const t = params.get("tab");
    return t === "prefs" || t === "model" ? t : null;
  })();
  // me loads async — derive the default tab instead of freezing it in state.
  const [tabChoice, setTabChoice] = useState<"prefs" | "model" | null>(null);
  const prefN = me?.pref_pending ?? 0;
  const modelN = me?.changes_pending ?? 0;
  // Land on whichever visible tab HAS items (2026-07-20) — if one is empty and
  // the other isn't, don't strand the user on the empty one.
  const byCount: "prefs" | "model" =
    showPrefs && prefN > 0 ? "prefs"
      : showModel && modelN > 0 ? "model"
        : showPrefs ? "prefs" : "model";
  // Priority: an explicit click > the deep link > the count-based default.
  const want = tabChoice ?? requested ?? byCount;
  const active: "prefs" | "model" =
    want === "prefs" ? (showPrefs ? "prefs" : "model")
      : (showModel ? "model" : "prefs");

  const months = scoring?.preferences.review_months ?? 6;
  const cap = scoring?.preferences.per_preference_cap_points ?? 2;
  const sub = active === "prefs"
    ? `Every new preference waits here for an HR Head’s sign-off — nobody approves their own. Approving keeps any warning on record and sets a ${months}-month re-review. Each stays capped at +${cap} and never hides anyone.`
    : "Scoring-model and hiring-rules changes wait here for a second leader — the proposer can never approve their own change. Nothing takes effect until the approval lands. A scoring-model change applies everywhere; a hiring-rules change applies to its location only.";

  const tabs = [
    ...(showPrefs ? [{ key: "prefs" as const, label: `Preferences · ${me?.pref_pending ?? 0}` }] : []),
    ...(showModel ? [{ key: "model" as const, label: `Scoring model · ${me?.changes_pending ?? 0}` }] : []),
  ];

  return (
    <Page max={900}>
      <h1 style={{ fontSize: 24, letterSpacing: "-0.01em", margin: "0 0 4px" }}>Approvals</h1>
      <p style={{ fontSize: 13, color: "var(--ink-soft)", lineHeight: 1.55, marginTop: 0, marginBottom: 18 }}>{sub}</p>

      {tabs.length > 1 && (
        <div style={{ display: "flex", gap: 22, marginBottom: 16, fontSize: 13, fontWeight: 700, borderBottom: "1px solid var(--border-subtle)" }}>
          {tabs.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={active === t.key}
              onClick={() => setTabChoice(t.key)}
              style={{
                cursor: "pointer", paddingBottom: 9, border: 0, background: "none",
                fontFamily: "inherit", fontSize: 13, fontWeight: 700, marginBottom: -1,
                color: active === t.key ? "var(--brand-green-deep)" : "var(--ink-soft)",
                borderBottom: active === t.key ? "2.5px solid var(--brand-green)" : "2.5px solid transparent",
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      {active === "prefs" && showPrefs ? <PreferencesSection /> : showModel ? <ChangesQueue /> : null}
    </Page>
  );
}
