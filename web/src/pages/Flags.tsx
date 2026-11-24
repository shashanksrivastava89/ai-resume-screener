// P37: Feature flags (Governance). Visible with flags_view; the switches are live
// only with flags_edit (a Super Admin). Deployment-controlled flags (control:env,
// e.g. the hiring-manager portal) render read-only — their value comes from an env
// var set on the container, never from this page.
import { useState } from "react";
import { Page } from "../layout/AppShell";
import { Switch, Callout, CenterLoading, PageError } from "../components/ui";
import { useMe } from "../MeContext";
import { useFetch } from "../hooks";
import { put } from "../api";
import type { FeatureFlag } from "../types";

export default function Flags() {
  const me = useMe();
  const { data, error, loading, reload } = useFetch<{ features: FeatureFlag[] }>("/api/features");
  const [busy, setBusy] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const canEdit = (me?.capabilities ?? []).includes("flags_edit");
  const regionCodes = me?.regions?.codes ?? [];

  if (loading) return <Page max={900}><CenterLoading /></Page>;
  if (error || !data) return <Page max={900}><PageError message={error || "Couldn't load."} /></Page>;

  async function setFlag(key: string, region: string | null, enabled: boolean) {
    setSaveError(null);
    setBusy(`${key}:${region ?? "_"}`);
    try {
      await put(`/api/flags/${key}`, { enabled, ...(region ? { region } : {}) });
      reload();
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Page max={900}>
      <h1 style={{ fontSize: 30, letterSpacing: "-0.01em", marginBottom: 6 }}>Feature flags</h1>
      <p style={{ fontSize: 15, color: "var(--ink-soft)", margin: "0 0 20px" }}>
        Turn optional capabilities on or off. {canEdit
          ? "Changes take effect immediately."
          : "You can see the current settings; a Super Admin changes them."}
      </p>

      {saveError && (
        <div style={{ marginBottom: 16 }}>
          <Callout variant="warn" title="Couldn't save">{saveError}</Callout>
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {data.features.map((f) => {
          const envControlled = f.control === "env";
          return (
            <div key={f.key} style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "18px 20px" }}>
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink)" }}>{f.name}</div>
                  <div style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 3, lineHeight: 1.5 }}>{f.description}</div>
                </div>
                <div style={{ flex: "0 0 auto", textAlign: "right" }}>
                  {envControlled ? (
                    <>
                      <span style={{ display: "inline-flex", fontSize: 12, fontWeight: 700, padding: "5px 11px", borderRadius: 999, background: f.enabled ? "var(--brand-green-tint)" : "var(--neutral-150)", color: f.enabled ? "var(--brand-green-deep)" : "var(--ink-soft)", whiteSpace: "nowrap" }}>
                        {f.enabled ? "On" : "Off"}
                      </span>
                      <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 6, maxWidth: 200 }}>
                        Controlled by deployment (env var)
                      </div>
                    </>
                  ) : (
                    <Switch
                      checked={f.default}
                      disabled={!canEdit || busy === `${f.key}:_`}
                      onChange={(v) => setFlag(f.key, null, v)}
                    />
                  )}
                </div>
              </div>

              {/* Per-region overrides — only for a flag that can actually differ
                  per region (2026-09-12). Every non-env flag used to get this row,
                  which promised something the code cannot keep: a per-region
                  override is only ever consulted where a call site has a
                  REQUISITION to take a region from. */}
              {!envControlled && f.scope !== "requisition" && (
                <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid var(--neutral-150)", fontSize: 12, color: "var(--ink-muted)" }}>
                  Applies everywhere — this one isn&rsquo;t set per region.
                </div>
              )}
              {!envControlled && f.scope === "requisition" && regionCodes.length > 0 && (
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--neutral-150)", display: "flex", flexWrap: "wrap", gap: 18 }}>
                  <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink-muted)", alignSelf: "center" }}>By region</span>
                  {regionCodes.map((code) => {
                    const on = code in f.regions ? f.regions[code] : f.default;
                    return (
                      <label key={code} style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--ink)" }}>
                        <input
                          type="checkbox"
                          checked={on}
                          disabled={!canEdit || busy === `${f.key}:${code}`}
                          onChange={(e) => setFlag(f.key, code, e.target.checked)}
                        />
                        {code}
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <p style={{ fontSize: 12.5, color: "var(--ink-muted)", marginTop: 18 }}>Changes are logged.</p>
    </Page>
  );
}
