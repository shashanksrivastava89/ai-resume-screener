import { useState } from "react";
import { Page } from "../layout/AppShell";
import { Button, Callout, CenterLoading, Field, Input, Textarea, PageError } from "../components/ui";
import { IconChecklist } from "../components/icons";
import SignalDial, { type SignalOverrides } from "../components/SignalDial";
import { invalidate, useFetch } from "../hooks";
import { useMe, useMeRefresh } from "../MeContext";
import { post } from "../api";
import type {
  BlockedPattern,
  BlockedPatternDerivation,
  Jurisdiction,
  JurisdictionsResponse,
} from "../types";

function SeverityChip({ severity }: { severity: "blocked" | "warn" }) {
  const blocked = severity === "blocked";
  return (
    <span style={{
      fontSize: 10.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999,
      background: blocked ? "var(--danger-tint, #fdecec)" : "var(--warn-tint, #fdf4e3)",
      color: blocked ? "var(--danger, #b3261e)" : "var(--warn-deep, #8a5a00)",
    }}>{blocked ? "Blocked" : "Needs approval"}</span>
  );
}

function BlockedHere({ j }: { j: Jurisdiction }) {
  const universal = j.blocked_patterns.filter((p) => p.floor);
  const regional = j.own_blocked_patterns;
  return (
    <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--neutral-150)" }}>
      <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--ink)", marginBottom: 6 }}>Blocked here</div>
      <div style={{ fontSize: 12, color: "var(--ink-soft)", marginBottom: regional.length ? 8 : 0 }}>
        Universal fairness floor: {universal.length} protected-attribute rules (inherited everywhere).
      </div>
      {regional.map((p) => (
        <div key={p.id} style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 12.5, marginBottom: 4 }}>
          <SeverityChip severity={p.severity} />
          <span style={{ color: "var(--ink)" }}>{p.name}
            {p.source === "derived" && <span style={{ color: "var(--ink-soft)", fontSize: 11 }}> · AI-proposed</span>}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function Jurisdictions() {
  const me = useMe();
  const canEdit = me?.can_edit_rules ?? false;
  const refreshMe = useMeRefresh();
  const { data, error, loading, reload } = useFetch<JurisdictionsResponse>("/api/jurisdictions");
  const [editing, setEditing] = useState<string | null>(null);

  if (loading) return <Page max={1000}><CenterLoading /></Page>;
  if (error || !data) return <Page max={1000}><PageError message={error || "Couldn't load."} /></Page>;

  return (
    <Page max={1000}>
      <h1 style={{ fontSize: 30, letterSpacing: "-0.01em", marginBottom: 6 }}>Jurisdictions</h1>
      <p style={{ fontSize: 15, color: "var(--ink-soft)", margin: "0 0 24px" }}>
        A role's location sets the hiring rules that apply to its screening. Each pack layers legal obligations and blocked factors on top of the universal fairness floor — it can add duties, never remove them.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 18, alignItems: "start" }}>
        {data.jurisdictions.map((j) => (
          <div key={j.code} style={{ background: "var(--white)", border: `1px solid ${j.active ? "var(--brand-green)" : "var(--border-subtle)"}`, borderRadius: 16, padding: 22 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 4 }}>
              <h3 style={{ fontSize: 17, margin: 0 }}>{j.name}</h3>
              {j.badge && <span style={{ fontSize: 11, fontWeight: 700, padding: "4px 10px", borderRadius: 999, background: "var(--brand-green-tint)", color: "var(--brand-green-deep)" }}>{j.badge}</span>}
            </div>
            <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--brand-green-deep)", marginBottom: 14 }}>{j.law}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {j.obligations.map((o, i) => (
                <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 9, fontSize: 13, color: "var(--ink)" }}>
                  <span style={{ flex: "0 0 auto", marginTop: 2, color: "var(--ink-soft)" }}><IconChecklist size={15} /></span>
                  {o}
                </div>
              ))}
              {j.obligations.length === 0 && <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>Floor obligations only.</div>}
            </div>
            {(j.keep_at_least_days || j.purge_by_days || (j.monitoring_categories && j.monitoring_categories.length > 0)) && (
              <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--neutral-150)", fontSize: 12, color: "var(--ink-soft)" }}>
                {j.keep_at_least_days ? `Keep audit records ≥ ${j.keep_at_least_days} days. ` : ""}
                {j.purge_by_days ? `Purge candidate PII ${j.purge_by_days} days after close. ` : ""}
                {j.monitoring_categories && j.monitoring_categories.length > 0 ? `Monitoring: ${j.monitoring_categories.join(", ")}.` : ""}
              </div>
            )}
            <BlockedHere j={j} />
            {canEdit && editing !== j.code && (
              <div style={{ marginTop: 16 }}>
                <Button variant="ghost" size="sm" onClick={() => setEditing(j.code)}>Edit signals, obligations, retention &amp; blocked factors</Button>
              </div>
            )}
            {canEdit && editing === j.code && (
              <PackEditor pack={j} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); refreshMe(); }} />
            )}
          </div>
        ))}
      </div>

      <div style={{ marginTop: 24 }}>
        <Callout variant="info" title="Every location, one scoring model">
          A requisition&rsquo;s country decides which of these applies — the recruiter picks a
          country and the rules follow. Every location screens with the same company-wide
          scoring model; its pack turns the dial on top: which AI signals fire there, how far
          each may move a score, what candidates must be told and how long records are kept.
          The universal fairness floor (protected attributes) applies everywhere and can never
          be removed; a location can add blocked factors on top. Editing a pack is a proposal a
          second leader approves; both steps are logged.
        </Callout>
      </div>
    </Page>
  );
}

// A stable local id for a freshly-added row (namespaced by the server on derive).
function slugify(code: string, name: string): string {
  const base = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "pattern";
  const prefix = code.toLowerCase().replace(/-/g, "_");
  return base.startsWith(`${prefix}_`) ? base : `${prefix}_${base}`;
}

function PackEditor({ pack, onCancel, onSaved }: { pack: Jurisdiction; onCancel: () => void; onSaved: () => void }) {
  const [obligations, setObligations] = useState(pack.own_obligations.join("\n"));
  const [keep, setKeep] = useState(pack.own_keep_at_least_days != null ? String(pack.own_keep_at_least_days) : "");
  const [purge, setPurge] = useState(pack.own_purge_by_days != null ? String(pack.own_purge_by_days) : "");
  const [monitoring, setMonitoring] = useState((pack.own_monitoring_categories || []).join(", "));
  const [patterns, setPatterns] = useState<BlockedPattern[]>(pack.own_blocked_patterns.map((p) => ({ ...p })));
  // The regional DIAL — which of the model's signals fire here and how far each
  // may move a score. publish_pack has always accepted these; nothing sent them.
  const [signals, setSignals] = useState<SignalOverrides>(
    () => JSON.parse(JSON.stringify(pack.own_signal_overrides ?? {})) as SignalOverrides);
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [deriving, setDeriving] = useState(false);
  const [deriveNote, setDeriveNote] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<{ signal_id: string; note: string }[]>([]);
  const [err, setErr] = useState<string | null>(null);

  function days(value: string): number | null {
    return value.trim() === "" ? null : Number(value);
  }

  function patch(i: number, next: Partial<BlockedPattern>) {
    setPatterns((ps) => ps.map((p, idx) => (idx === i ? { ...p, ...next } : p)));
  }
  function addPattern() {
    setPatterns((ps) => [...ps, {
      id: "", name: "", reason: "", severity: "blocked", floor: false,
      stages: ["preferences", "checklist"], phrases: [], regexes: [], source: "manual",
    }]);
  }
  function removePattern(i: number) {
    setPatterns((ps) => ps.filter((_, idx) => idx !== i));
  }

  async function derive() {
    setDeriving(true);
    setErr(null);
    setDeriveNote(null);
    try {
      const out = await post<BlockedPatternDerivation>(`/api/jurisdictions/${pack.code}/derive`, {});
      const known = new Set(patterns.map((p) => p.id));
      const fresh = out.patterns.filter((p) => !known.has(p.id));
      setPatterns((ps) => [...ps, ...fresh]);
      setConflicts(out.conflicts);
      setDeriveNote(fresh.length ? `Added ${fresh.length} proposed pattern(s) — review, edit, then send for approval.`
        : (out.note || "The model proposed no new patterns."));
    } catch (e) {
      setDeriveNote((e as Error).message);
    } finally {
      setDeriving(false);
    }
  }

  function normalizedPatterns(): BlockedPattern[] {
    return patterns
      .filter((p) => p.name.trim() && p.reason.trim() && (p.phrases.length || p.regexes.length))
      .map((p) => ({
        ...p,
        id: (p.id || slugify(pack.code, p.name)).trim(),
        name: p.name.trim(),
        reason: p.reason.trim(),
        floor: false, // region patterns are never the universal floor
        stages: p.stages.length ? p.stages : ["preferences", "checklist"],
      }));
  }

  async function save() {
    const nextObl = obligations.split("\n").map((s) => s.trim()).filter(Boolean);
    const nextMon = monitoring.split(",").map((s) => s.trim()).filter(Boolean);
    const nextKeep = days(keep);
    const nextPurge = days(purge);
    for (const n of [nextKeep, nextPurge]) {
      if (n != null && (Number.isNaN(n) || n < 0)) {
        setErr("Retention must be a number of days.");
        return;
      }
    }
    const nextPatterns = normalizedPatterns();
    // Only send fields the admin actually changed (avoids empty version bumps).
    const payload: Record<string, unknown> = {};
    if (JSON.stringify(nextObl) !== JSON.stringify(pack.own_obligations)) payload.obligations = nextObl;
    if (nextKeep !== pack.own_keep_at_least_days) payload.keep_at_least_days = nextKeep;
    if (nextPurge !== pack.own_purge_by_days) payload.purge_by_days = nextPurge;
    if (JSON.stringify(nextMon) !== JSON.stringify(pack.own_monitoring_categories || [])) payload.monitoring_categories = nextMon;
    const cleanExisting = pack.own_blocked_patterns.map((p) => ({ ...p }));
    if (JSON.stringify(nextPatterns) !== JSON.stringify(cleanExisting)) payload.blocked_patterns = nextPatterns;
    if (JSON.stringify(signals) !== JSON.stringify(pack.own_signal_overrides ?? {})) {
      payload.signal_overrides = signals;
    }
    if (Object.keys(payload).length === 0) {
      setErr("Nothing changed.");
      return;
    }
    if (!rationale.trim()) {
      setErr("A reason for the change is required — it's recorded in the audit log.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      // Pack edits are change PROPOSALS — a second leader approves before anything applies.
      await post("/api/changes", {
        kind: "policy_pack", target: `pack:${pack.code}`,
        payload, rationale: rationale.trim(),
      });
      invalidate("/api/changes");
      onSaved();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginTop: 16, paddingTop: 16, borderTop: "1px solid var(--neutral-150)", display: "flex", flexDirection: "column", gap: 14 }}>
      <Field label="Extra obligations this jurisdiction adds" hint="one per line; the fairness floor is always included">
        <Textarea value={obligations} onChange={(e) => setObligations(e.target.value)} style={{ minHeight: 100 }} placeholder="e.g. Provide a DPDP notice at collection" />
      </Field>

      <SignalDial code={pack.code} value={signals} onChange={setSignals} />

      <div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
          <div style={{ fontSize: 13, fontWeight: 700 }}>Region-specific blocked factors</div>
          <Button variant="ghost" size="sm" onClick={derive} disabled={deriving || busy}>
            {deriving ? "Reading rules…" : "✨ Derive from rules"}
          </Button>
        </div>
        <p style={{ fontSize: 12, color: "var(--ink-soft)", margin: "0 0 8px" }}>
          The universal protected-attribute floor is inherited and can't be edited here. Add factors this jurisdiction's law blocks (e.g. salary history in California). Enforcement is exact-match; the model only proposes.
        </p>
        {deriveNote && <Callout variant="info" title="Derivation">{deriveNote}</Callout>}
        {conflicts.length > 0 && (
          <Callout variant="warn" title="Possible conflicts with the scoring model">
            {conflicts.map((c, i) => <div key={i} style={{ fontSize: 12.5 }}><b>{c.signal_id}</b>: {c.note}</div>)}
          </Callout>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8 }}>
          {patterns.map((p, i) => (
            <div key={i} style={{ border: "1px solid var(--border-subtle)", borderRadius: 10, padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <Input value={p.name} onChange={(e) => patch(i, { name: e.target.value })} placeholder="Factor name (e.g. Salary history)" style={{ flex: 1 }} />
                <select value={p.severity} onChange={(e) => patch(i, { severity: e.target.value as "blocked" | "warn" })}
                  style={{ fontSize: 13, padding: "6px 8px", borderRadius: 8, border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink)" }}>
                  <option value="blocked">Blocked</option>
                  <option value="warn">Needs approval</option>
                </select>
                <Button variant="ghost" size="sm" onClick={() => removePattern(i)}>Remove</Button>
              </div>
              <Input value={p.reason} onChange={(e) => patch(i, { reason: e.target.value })} placeholder="Plain-language reason (shown when it fires)" />
              <Field label="Trigger phrases" hint="one per line; exact word match, case-insensitive">
                <Textarea value={p.phrases.join("\n")} onChange={(e) => patch(i, { phrases: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })}
                  style={{ minHeight: 56 }} placeholder={"salary history\nprevious salary"} />
              </Field>
            </div>
          ))}
          <div><Button variant="ghost" size="sm" onClick={addPattern}>+ Add a blocked factor</Button></div>
        </div>
      </div>

      <Field label="Keep audit records for at least (days)" hint="a note for reviewers; nothing auto-deletes">
        <Input value={keep} onChange={(e) => setKeep(e.target.value)} inputMode="numeric" placeholder="e.g. 1095" />
      </Field>
      <Field label="Purge candidate PII within (days after close)" hint="a note for reviewers; nothing auto-deletes">
        <Input value={purge} onChange={(e) => setPurge(e.target.value)} inputMode="numeric" placeholder="e.g. 365" />
      </Field>
      <Field label="Monitoring categories" hint="comma-separated; voluntary and opt-in">
        <Input value={monitoring} onChange={(e) => setMonitoring(e.target.value)} placeholder="e.g. gender (voluntary), disability (voluntary)" />
      </Field>
      <Field label="Reason for this change" hint="recorded in the audit log">
        <Input value={rationale} onChange={(e) => setRationale(e.target.value)} placeholder="Why are you changing this pack?" />
      </Field>
      {err && <Callout variant="warn" title="Couldn't send this">{err}</Callout>}
      <div style={{ display: "flex", gap: 8 }}>
        <Button onClick={save} disabled={busy}>{busy ? "Sending…" : "Send for approval"}</Button>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
      </div>
      <p style={{ fontSize: 12, color: "var(--ink-soft)", margin: 0 }}>
        Nothing changes until another leader (HR Head, Legal or Super Admin) approves it.
      </p>
    </div>
  );
}
