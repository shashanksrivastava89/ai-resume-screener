import { useState } from "react";
import { Page } from "../layout/AppShell";
import { Button, Callout, CenterLoading, PageError } from "../components/ui";
import { ConfirmModal, useToast } from "../components/overlays";
import { IconCheck, IconPlus, IconX } from "../components/icons";
import { Avatar, PeoplePicker, type PickedPerson } from "../people";
import { useMe } from "../MeContext";
import { useFetch } from "../hooks";
import { del, post } from "../api";
import type { AccessGrants, AccessPerson, RbacResponse } from "../types";

export default function Access() {
  const me = useMe();
  const { data, error, loading } = useFetch<RbacResponse>("/api/rbac");
  if (loading) return <Page max={1240}><CenterLoading /></Page>;
  if (error || !data) return <Page max={1240}><PageError message={error || "Couldn't load."} /></Page>;

  const cols = `1.5fr repeat(${data.caps.length}, 1fr)`;

  return (
    <Page max={1240}>
      <h1 style={{ fontSize: 30, letterSpacing: "-0.01em", marginBottom: 6 }}>Access &amp; Roles</h1>
      <p style={{ fontSize: 15, color: "var(--ink-soft)", margin: "0 0 24px" }}>
        Who can do what, and which regions they see. Roles come from single sign-on
        (Microsoft Entra) and decide what a person is allowed to do; regions decide
        which requisitions those permissions reach. A person can hold more than one
        role — their access is the combination.
      </p>

      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, overflowX: "auto", marginBottom: 22 }}>
        <div style={{ minWidth: 320 + data.caps.length * 92 }}>
          <div style={{ display: "grid", gridTemplateColumns: cols, gap: 10, padding: "14px 20px", background: "var(--neutral-100)", borderBottom: "1px solid var(--border-subtle)" }}>
            <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink-soft)" }}>Role</span>
            {data.caps.map((c) => (
              <span key={c.key} style={{ fontSize: 11, fontWeight: 700, color: "var(--ink-soft)", textAlign: "center", lineHeight: 1.3 }}>{c.label}</span>
            ))}
          </div>
          {data.rows.map((row) => (
            <div key={row.name} style={{ display: "grid", gridTemplateColumns: cols, gap: 10, padding: "14px 20px", borderBottom: "1px solid var(--neutral-150)", alignItems: "center", background: row.name === me?.role ? "var(--brand-green-tint)" : "transparent" }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 700, color: "var(--ink)" }}>{row.name}</div>
                <div style={{ fontSize: 12, color: "var(--ink-soft)" }}>{row.entra}</div>
              </div>
              {data.caps.map((c) => (
                <div key={c.key} style={{ display: "flex", justifyContent: "center" }}>
                  {row.cells[c.key] ? (
                    <span style={{ width: 24, height: 24, borderRadius: "50%", background: "var(--brand-green-tint)", color: "var(--brand-green-deep)", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
                      <IconCheck size={14} />
                    </span>
                  ) : (
                    <span style={{ color: "var(--border-strong)" }}>—</span>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>

      <RegionGrants />
    </Page>
  );
}

// --- who sees which regions (the global rollout, 2026-09-12) ------------------------
//
// Region scoping was declared and unusable: nothing outside `screener db seed`
// could create a grant, so every real user resolved as unscoped and the lens
// filtered nothing. This is the surface that makes it a thing an administrator
// can actually do — and undo, which matters more, because the interesting
// mistake here is scoping someone too narrowly and stranding them.

function RegionGrants() {
  const me = useMe();
  const toast = useToast();
  const { data, error, loading, reload } = useFetch<AccessGrants>("/api/access/grants");
  const [adding, setAdding] = useState<PickedPerson | null>(null);
  const [confirm, setConfirm] = useState<null | {
    title: string; body: string; cta: string; run: () => Promise<void>;
  }>(null);
  const [busy, setBusy] = useState(false);

  const canEdit = (me?.capabilities ?? []).includes("access_edit");

  if (loading) return <CenterLoading />;
  if (error || !data) return <PageError message={error || "Couldn't load region access."} />;

  if (data.status !== "ok") {
    return (
      <Callout variant="neutral" title="Region access">{data.message}</Callout>
    );
  }

  const nameOf = (code: string) =>
    data.regions.find((r) => r.code === code)?.name ?? code;

  async function run(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await fn();
      reload();
      toast(done);
    } catch (e) {
      toast(e instanceof Error ? e.message : "That didn't go through.");
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  function give(person: AccessPerson | PickedPerson, region: string | null) {
    const who = person.name || person.email;
    const where = region ? nameOf(region) : "every region";
    const scoping = region && "unscoped" in person && person.unscoped;
    const act = () => run(
      () => post("/api/access/grants", { email: person.email, region }),
      `${who} now sees ${where}.`);
    if (!scoping) return act();
    // The one narrowing case: someone who could see everything will now see less.
    setConfirm({
      title: `Scope ${who} to ${where}?`,
      body: `Right now ${who} sees every region, because none is assigned. `
        + `After this they will see ${where} only — plus anything they created or `
        + `are named on. It takes effect the next time they load a page.`,
      cta: `Scope to ${where}`,
      run: act,
    });
  }

  function take(person: AccessPerson, region: string | null) {
    const who = person.name || person.email;
    const where = region ? nameOf(region) : "every region";
    const last = person.all_regions || person.regions.length <= 1;
    setConfirm({
      title: `Remove ${where} from ${who}?`,
      body: `${who} will no longer see ${where} requisitions unless they created `
        + `them or are named on them.`
        + (last ? ` With nothing assigned, ${who} goes back to seeing every region.` : ""),
      cta: "Remove",
      run: () => run(
        () => del(`/api/access/grants/${encodeURIComponent(person.email)}/${region ?? "all"}`),
        `${where} removed from ${who}.`),
    });
  }

  return (
    <>
      <h2 style={{ fontSize: 20, letterSpacing: "-0.01em", margin: "0 0 4px" }}>
        Who sees which regions
      </h2>
      <p style={{ fontSize: 14, color: "var(--ink-soft)", margin: "0 0 16px", maxWidth: 720 }}>
        A region decides which requisitions someone&rsquo;s permissions reach — it never
        grants a permission. Assigning one to a person who is not a recruiter gives
        them nothing. Someone with no region assigned sees every region, which is the
        deliberate default: nobody should need a second step before they can see their
        own work.
      </p>

      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, overflow: "hidden", marginBottom: 18 }}>
        {data.people.length === 0 && (
          <div style={{ padding: "18px 20px", fontSize: 13.5, color: "var(--ink-soft)" }}>
            Nobody has signed in or been assigned a region yet.
          </div>
        )}
        {data.people.map((person) => (
          <div key={person.email} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 20px", borderBottom: "1px solid var(--neutral-150)", flexWrap: "wrap" }}>
            <Avatar email={person.email} name={person.name} photo={person.photo} hue={person.hue} />
            <div style={{ minWidth: 180, flex: "0 0 auto" }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>{person.name || person.email}</div>
              <div style={{ fontSize: 12, color: "var(--ink-muted)" }}>{person.email}</div>
            </div>
            <div className="chip-row" style={{ flex: "1 1 auto" }}>
              {person.all_regions && <Chip label="All regions" onRemove={canEdit ? () => take(person, null) : undefined} />}
              {person.regions.map((code) => (
                <Chip key={code} label={nameOf(code)}
                      onRemove={canEdit ? () => take(person, code) : undefined} />
              ))}
              {person.unscoped && (
                <span style={{ fontSize: 12.5, color: "var(--ink-muted)" }}>
                  Not scoped — sees every region
                </span>
              )}
            </div>
            {canEdit && (
              <AddRegion
                regions={data.regions.filter((r) => !person.regions.includes(r.code))}
                allowAll={!person.all_regions}
                busy={busy}
                onPick={(code) => give(person, code)}
              />
            )}
          </div>
        ))}
      </div>

      {canEdit && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
          <span style={{ fontSize: 13.5, fontWeight: 700 }}>Assign someone not listed:</span>
          <div style={{ minWidth: 280 }}>
            <PeoplePicker value={adding} onChange={setAdding}
                          placeholder="Search by name or email…" ariaLabel="Person" manualLink />
          </div>
          {adding && (
            <AddRegion regions={data.regions} allowAll busy={busy}
                       onPick={(code) => { give(adding, code); setAdding(null); }} />
          )}
        </div>
      )}

      <p style={{ fontSize: 12.5, color: "var(--ink-muted)" }}>
        Changes are logged, and take effect the next time that person loads a page —
        within about half a minute.
      </p>

      {confirm && (
        <ConfirmModal
          spec={{
            title: confirm.title,
            question: confirm.body,
            cta: confirm.cta,
            canProceed: !busy,
            onProceed: () => { void confirm.run(); },
          }}
          onCancel={() => setConfirm(null)}
        />
      )}
    </>
  );
}

function Chip({ label, onRemove }: { label: string; onRemove?: () => void }) {
  return (
    <span className="chip chip-blue" title={label}>
      {label}
      {onRemove && (
        <button
          type="button"
          aria-label={`Remove ${label}`}
          onClick={onRemove}
          style={{ border: 0, background: "none", cursor: "pointer", padding: 0, marginLeft: 6, display: "inline-flex", color: "inherit" }}
        >
          <IconX size={11} />
        </button>
      )}
    </span>
  );
}

function AddRegion({ regions, allowAll, busy, onPick }: {
  regions: { code: string; name: string }[];
  allowAll: boolean;
  busy: boolean;
  onPick: (code: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!regions.length && !allowAll) return null;
  return (
    <div style={{ position: "relative", flex: "0 0 auto" }}>
      <Button variant="ghost" size="sm" disabled={busy} onClick={() => setOpen((v) => !v)}>
        <IconPlus size={13} /> Add region
      </Button>
      {open && (
        <div role="menu" style={{ position: "absolute", right: 0, top: "calc(100% + 4px)", zIndex: 40, background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 10, boxShadow: "var(--shadow-md, 0 8px 28px rgba(34,34,34,.14))", padding: 4, minWidth: 200 }}>
          {regions.map((r) => (
            <button key={r.code} role="menuitem"
                    onClick={() => { setOpen(false); onPick(r.code); }}
                    style={menuItem}>
              {r.name}
            </button>
          ))}
          {allowAll && (
            <button role="menuitem" onClick={() => { setOpen(false); onPick(null); }}
                    style={{ ...menuItem, borderTop: "1px solid var(--neutral-150)" }}>
              All regions
            </button>
          )}
        </div>
      )}
    </div>
  );
}

const menuItem: React.CSSProperties = {
  display: "block", width: "100%", background: "none", border: 0, borderRadius: 7,
  padding: "8px 10px", font: "inherit", fontSize: 13, color: "var(--ink)",
  cursor: "pointer", textAlign: "left",
};
