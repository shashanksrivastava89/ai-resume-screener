// The workspace's Overview tab (new in P31): the requisition's facts strip, the
// stage headline + the ONE primary CTA, the job-description card, the hiring-rules
// card (with Close), and the delete danger zone.
//
// 2026-09-06 (pilot feedback item 9): the location leads the facts strip, the law
// is something you can OPEN (what does DPDP ask of me?), "added by" is a person
// with an avatar rather than a UPN, the JD copies in one click, the distance card
// is gone (the distance is a Review filter now), and every blurb is one sentence.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Page } from "../../layout/AppShell";
import { Button, MagicButton, Callout, Field, Textarea } from "../../components/ui";
import { IconCheck, IconCopy, IconPin, IconPerson, IconX } from "../../components/icons";
import HiringRules from "../../components/HiringRules";
import { PeoplePicker, Person, usePeople, type PeopleIndex, type PickedPerson } from "../../people";
import { useMe } from "../../MeContext";
import LocationPicker, { EMPTY_LOCATION, blockingReason, packFor, type LocationChoice } from "../../components/LocationPicker";
import { del, post, put } from "../../api";
import { useFetch, useFeatureFlag, invalidate } from "../../hooks";
import { useWorkspace, tabPath } from "./context";
import { STAGE_CHIP } from "../../stages";
import type { JdPayload, LocationsFeed, RoleDetail } from "../../types";

/** Copy text to the clipboard and say whether it worked (the clipboard API is
 *  denied in insecure contexts and some corporate policies — a silent no-op is
 *  the one thing a copy button must never be). */
async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

function CopyButton({ text, label, size = 30 }: { text: string; label: string; size?: number }) {
  const [state, setState] = useState<"idle" | "yes" | "no">("idle");
  return (
    <button
      onClick={async () => { setState(await copyText(text) ? "yes" : "no"); window.setTimeout(() => setState("idle"), 1600); }}
      aria-label={label}
      title={state === "yes" ? "Copied" : state === "no" ? "Couldn't copy — select the text instead" : label}
      style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 5, height: size, minWidth: size, padding: size > 26 ? "0 8px" : 0, borderRadius: 8, border: "1px solid var(--border-subtle)", background: "var(--white)", cursor: "pointer", color: state === "yes" ? "var(--brand-green-deep)" : "var(--ink-soft)", fontFamily: "inherit", fontSize: 12, fontWeight: 700 }}
    >
      {state === "yes" ? <IconCheck size={14} /> : <IconCopy size={14} />}
      {size > 26 && <span>{state === "yes" ? "Copied" : state === "no" ? "Select to copy" : "Copy"}</span>}
    </button>
  );
}

// The job description + role details panel. Read mode collapses the JD to a
// preview; "Edit details" opens the same fields as the create wizard — title,
// job location and JD — with the iCIMS Req ID shown read-only (it's the mapping
// anchor and never changes). Saving a JD change resets the checklist (warned
// when one exists), because the checklist is drafted from the JD.
function DetailsPanel({ role, reload, closed }: { role: RoleDetail; reload: () => void; closed: boolean }) {
  const { readOnly } = useWorkspace();
  const reqId = role.req_id;
  const nav = useNavigate();
  const me = useMe();
  const people = usePeople();
  const { data, error } = useFetch<JdPayload>(`/api/roles/${reqId}/jd`);
  const { data: feedData } = useFetch<LocationsFeed>("/api/locations");
  const feed = feedData ?? undefined;
  // The HM portal flag gates COLLABORATION (invite/notify/takes) only — the HM
  // name is plain metadata, always shown + editable below.
  const hmPortalOn = useFeatureFlag("hm_portal") ?? false;
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  // The PLACE, not the pack (2026-09-12): editing a requisition asks the same
  // question creating one does. A requisition made before the country existed
  // restores an empty choice and keeps its pack until someone answers.
  const [place, setPlace] = useState<LocationChoice>(placeOf(role));
  const [jd, setJd] = useState("");
  const [hm, setHm] = useState<PickedPerson | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const hasChecklist = (role.requirement_count ?? 0) > 0;

  function startEdit() {
    setTitle(data?.title ?? role.title);
    setJd(data?.jd_text ?? "");
    setPlace(placeOf(role));
    setHm(role.hm?.email || role.hm?.name
      ? { email: role.hm.email ?? "", name: role.hm.name || role.hm.email || "" }
      : null);
    setConfirmReset(false);
    setErr(null);
    setEditing(true);
  }

  // Invite the recorded HM to collaborate (portal only) — flips status to
  // "invited" and notifies. Naming the HM above never triggers this.
  async function inviteHm() {
    if (!role.hm?.email) return;
    setErr(null);
    try {
      await put(`/api/roles/${reqId}/hm`, { email: role.hm.email, name: role.hm.name ?? "" });
      reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  const jdChanged = jd.trim() !== (data?.jd_text ?? "").trim();
  // The pack the current choice resolves to; the requisition's existing pack when
  // it has no country yet, so a title-only edit never moves its hiring rules.
  const pack = packFor(feed, place) ?? role.job_location;
  const placeBlocker = place.country ? blockingReason(feed, place) : null;

  async function save() {
    if (!title.trim()) return setErr("The job title can't be empty.");
    if (!jd.trim()) return setErr("The job description can't be empty.");
    if (placeBlocker) return setErr(placeBlocker);
    // Warn once before resetting a real checklist — but only when the JD actually
    // changed (a title/location-only edit leaves the checklist intact).
    if (jdChanged && hasChecklist && !confirmReset) {
      setConfirmReset(true);
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await put(`/api/roles/${reqId}/details`, {
        title: title.trim(), jd_text: jd.trim(), job_location: pack,
        country: place.country, us_state: place.us_state, nyc: place.nyc,
        hm_name: hm?.name.trim() ?? "", hm_email: hm?.email.trim() ?? "",
      });
      invalidate(`/api/roles/${reqId}`);
      reload();
      setEditing(false);
      // A JD change invalidates the checklist that was drafted from it — take the
      // recruiter to the Checklist tab to re-draft and re-publish it.
      if (jdChanged && hasChecklist) {
        nav(tabPath(reqId, "checklist"), { state: { redraft: true } });
      }
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, padding: "20px 24px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 10 }}>
        <div style={{ fontSize: 13.5, fontWeight: 700 }}>Job description</div>
        <div style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
          {/* One click copies the whole JD — the most-asked-for affordance on this tab. */}
          {!editing && data?.jd_text && <CopyButton text={data.jd_text} label="Copy job description" />}
          {!editing && !closed && !readOnly && (
            <Button size="sm" variant="secondary" onClick={startEdit}>Edit details</Button>
          )}
        </div>
      </div>

      {editing ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {err && <Callout variant="warn" title="Couldn't save">{err}</Callout>}
          <Field label="iCIMS Req ID" hint="the mapping anchor — can't be changed here">
            <input className="input" value={role.req_code ?? "—"} readOnly disabled
              style={{ height: 44, fontFamily: "monospace", background: "var(--neutral-100)", color: "var(--ink-muted)" }} />
          </Field>
          <Field label="Job title">
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} style={{ height: 44 }} />
          </Field>
          <LocationPicker feed={feed} value={place} onChange={setPlace} />
          {placeBlocker && place.country && (
            <div style={{ fontSize: 12, color: "var(--warn-ink)", fontWeight: 700 }}>{placeBlocker}</div>
          )}
          <Field label="Hiring manager" hint="optional — pick from the directory">
            <PeoplePicker value={hm} onChange={setHm} placeholder="Search by name or email…" ariaLabel="Hiring manager" manualLink />
          </Field>
          <Field label="Job description">
            <Textarea value={jd} onChange={(e) => setJd(e.target.value)} style={{ minHeight: 200 }} />
          </Field>

          {confirmReset && (
            <Callout variant="warn" title="This resets the screening checklist">
              The checklist was drafted from the old job description — saving replaces
              its {role.requirement_count} requirement{role.requirement_count === 1 ? "" : "s"} with a fresh draft for you to approve.
            </Callout>
          )}

          <div style={{ display: "flex", gap: 10 }}>
            <Button onClick={save} disabled={busy}>
              {confirmReset ? "Save & re-draft checklist" : "Save details"}
            </Button>
            <Button variant="ghost" onClick={() => setEditing(false)} disabled={busy}>Cancel</Button>
          </div>
        </div>
      ) : (
        <>
          {/* Hiring manager — always shown (metadata), regardless of the portal flag */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 14, fontSize: 13 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5, fontWeight: 700, color: "var(--ink-soft)" }}>
              <IconPerson size={14} /> Hiring manager
            </span>
            {role.hm?.name || role.hm?.email ? (
              <>
                <Person email={role.hm?.email || role.hm?.name} known={role.hm?.name} people={people} me={me?.user} size="sm" />
                {role.hm?.status && (
                  <span className="chip chip-violet" style={{ height: 22 }}>
                    <span className="chip-label">{role.hm.status.replace("_", " ")}</span>
                  </span>
                )}
                {hmPortalOn && role.hm?.email && !role.hm?.status && (
                  <button onClick={inviteHm} style={{ background: "none", border: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, color: "var(--brand-green-deep)", padding: 0 }}>
                    Invite to review
                  </button>
                )}
              </>
            ) : (
              <span style={{ color: "var(--ink-muted)" }}>Not assigned{!closed && !readOnly ? " — add one with “Edit details”" : ""}</span>
            )}
          </div>

          <SharedWith reqId={reqId} role={role} me={me?.user} caps={me?.capabilities}
                      people={people} readOnly={readOnly || closed} onChange={reload} />

          {error ? (
            <p style={{ fontSize: 13, color: "var(--ink-muted)", margin: 0 }}>Couldn't load the job description.</p>
          ) : !data ? (
            <p style={{ fontSize: 13.5, color: "var(--ink-soft)", margin: 0 }}>…</p>
          ) : (
            <>
              <div style={{ position: "relative", maxHeight: open ? "none" : 240, overflow: "hidden" }}>
                <p style={{ fontSize: 13, lineHeight: 1.65, color: "var(--ink)", margin: 0, whiteSpace: "pre-wrap" }}>
                  {data.jd_text || "—"}
                </p>
                {!open && (
                  <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 56, background: "linear-gradient(transparent, var(--white))" }} />
                )}
              </div>
              {(data.jd_text?.length ?? 0) > 320 && (
                <button
                  onClick={() => setOpen((o) => !o)}
                  style={{ background: "none", border: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, color: "var(--brand-green-deep)", padding: "8px 0 0" }}
                >
                  {open ? "Show less" : "Show full description"}
                </button>
              )}
              {/* the design's provenance footnote */}
              <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 8 }}>
                Maintained in iCIMS · auto-fetched via Req ID when the lifecycle APIs land.
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

// One sentence per stage — the headline says where you are, the line says what
// happens next. Anything longer was being skipped (pilot feedback item 8).
const STAGE_COPY: Record<string, { headline: string; blurb: string }> = {
  draft_checklist: {
    headline: "Draft the screening checklist",
    blurb: "The AI drafts must-haves and nice-to-haves from the JD; you edit and approve every line.",
  },
  checklist_approved: {
    headline: "Add résumés to screen",
    blurb: "The checklist is approved — add this role's candidates.",
  },
  candidates_added: {
    headline: "Ready to screen",
    blurb: "Score the candidates against the approved checklist and this location's hiring rules.",
  },
  screened: {
    headline: "Review the shortlist",
    blurb: "Ranked, with the reasoning — record a decision on each candidate.",
  },
  in_review: {
    headline: "Continue your review",
    blurb: "Keep going through the shortlist; nothing is sent anywhere on its own.",
  },
  complete: {
    headline: "Decisions recorded",
    blurb: "Everyone actionable is decided — open the shortlist any time.",
  },
};

// CTA `goto` values from the API map onto workspace tabs.
const GOTO_TAB: Record<string, string> = { checklist: "checklist", screen: "candidates", review: "review" };

function outcomeSummary(outcome: { kind: string; hired_count?: number } | null): string {
  if (!outcome) return "Closed";
  return outcome.kind === "hired" ? `Hired ${outcome.hired_count ?? 1}` : "No hire — cancelled";
}

/** The country choice a requisition already carries (empty when it predates the
 *  country picker — the app never knew, and pretending otherwise would put a
 *  guess on screen). */
function placeOf(role: RoleDetail): LocationChoice {
  if (!role.country) return EMPTY_LOCATION;
  return { country: role.country, us_state: role.us_state ?? null,
           nyc: role.job_location === "US-NYC" };
}

export default function OverviewTab() {
  const { role, reload, readOnly } = useWorkspace();
  const nav = useNavigate();
  const me = useMe();
  const people = usePeople();

  const closed = role.lifecycle.status === "closed";
  const copy = STAGE_COPY[role.stage] ?? STAGE_COPY.draft_checklist;
  const chip = STAGE_CHIP[role.stage] ?? STAGE_CHIP.draft_checklist;

  const [picking, setPicking] = useState(false);
  const [outcome, setOutcome] = useState<"hired" | "cancelled">("hired");
  const [hiredCount, setHiredCount] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [delBusy, setDelBusy] = useState(false);
  const [delError, setDelError] = useState<string | null>(null);

  async function confirmDelete() {
    setDelBusy(true);
    setDelError(null);
    try {
      await del(`/api/roles/${role.req_id}`, { confirm: true });
      nav("/reqs"); // the req is gone — back to the list
    } catch (e) {
      setDelError((e as Error).message);
      setDelBusy(false);
    }
  }

  async function confirmClose() {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/roles/${role.req_id}/close`,
        outcome === "hired" ? { outcome, hired_count: hiredCount } : { outcome });
      reload();
      nav("/reqs"); // back to the requisitions list — the req is now in History
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const added = role.lifecycle.created_at
    ? new Date(role.lifecycle.created_at).toLocaleDateString(undefined, { dateStyle: "medium" })
    : "";

  return (
    <Page max={900}>
      {/* The facts strip: WHERE (leads — it decides the rules), the rules themselves
          (openable), the iCIMS anchor (copyable), and WHO added it (a person). */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 16, padding: "12px 16px", background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14 }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span style={{ display: "inline-flex", width: 30, height: 30, borderRadius: 9, alignItems: "center", justifyContent: "center", background: "var(--hue-blue-tint)", color: "var(--hue-blue-ink)", flex: "0 0 auto" }}>
            <IconPin size={16} />
          </span>
          <span style={{ display: "flex", flexDirection: "column", lineHeight: 1.2, minWidth: 0 }}>
            <span style={{ fontSize: 15, fontWeight: 700, color: "var(--ink)" }}>{role.work_location?.place || role.location_display}</span>
            {role.work_location?.place && <span style={{ fontSize: 11.5, color: "var(--ink-soft)" }}>{role.location_display}</span>}
            {/* A city we cannot locate makes every candidate's distance read "unknown"
                in Review, which looks like a broken filter (2026-09-11, Budapest). The
                gap is ours, so it is named where the city is — quietly, because it
                changes nothing about the requisition or anyone's score. */}
            {role.work_location?.place_known === false && (
              <span style={{ fontSize: 11.5, color: "var(--hue-blue-ink)" }}
                    title="An administrator can add it to the location list.">
                Not in the location list — Review shows no distances
              </span>
            )}
          </span>
        </span>
        {role.law && <HiringRules code={role.job_location} law={role.law} />}
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--ink-muted)" }}>iCIMS</span>
          <span style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: "var(--ink)", background: "var(--neutral-100)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: "3px 9px" }}>
            {role.req_code || "—"}
          </span>
          {role.req_code && <CopyButton text={role.req_code} label="Copy iCIMS Req ID" size={26} />}
        </span>
        {role.created_by && (
          <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5, color: "var(--ink-soft)" }}>
            Added by <Person email={role.created_by} people={people} me={me?.user} you size="sm" />
            {added && <span style={{ color: "var(--ink-muted)" }}>· {added}</span>}
          </span>
        )}
      </div>

      {/* P51: decided scores produced under older published rules */}
      {role.staleness?.mode === "locked" && (
        <div style={{ marginBottom: 16 }}>
          <Callout variant="warn" title="Scored under previous rules">
            {role.staleness.summary} Re-screen from the Review tab before recording more
            decisions — the ones already made stay in the audit trail.
          </Callout>
        </div>
      )}

      {/* stage headline + the one primary CTA — the card wears the stage's hue */}
      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderLeft: `4px solid ${chip.fg}`, borderRadius: 16, padding: "20px 24px", marginBottom: 14, display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4, flexWrap: "wrap" }}>
            <span style={{ fontSize: 11, fontWeight: 700, padding: "3px 9px", borderRadius: 999, background: chip.bg, color: chip.fg }}>{chip.label}</span>
            <span style={{ fontSize: 16, fontWeight: 700 }}>{copy.headline}</span>
          </div>
          <p style={{ fontSize: 13.5, color: "var(--ink-soft)", margin: 0, lineHeight: 1.5 }}>{copy.blurb}</p>
        </div>
        {!closed && (
          role.cta.magic ? (
            <MagicButton onClick={() => nav(tabPath(role.req_id, GOTO_TAB[role.cta.goto] ?? "overview"))} style={{ height: 38, fontSize: 13 }}>{role.cta.label}</MagicButton>
          ) : (
            <Button onClick={() => nav(tabPath(role.req_id, GOTO_TAB[role.cta.goto] ?? "overview"))} style={{ height: 38, fontSize: 13 }}>{role.cta.label}</Button>
          )
        )}
      </div>

      {/* JD + details — read-only preview, or the create-like editor */}
      <DetailsPanel role={role} reload={reload} closed={closed} />

      {/* lifecycle — the design puts Close requisition here, a quiet ghost button;
          the outcome picker expands inside the card */}
      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, padding: "16px 22px", marginTop: 14 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 3 }}>Requisition status</div>
            <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
              {closed
                ? <>Closed — <strong style={{ color: "var(--ink)" }}>{outcomeSummary(role.lifecycle.outcome)}</strong>
                    {role.lifecycle.closed_at ? ` · ${new Date(role.lifecycle.closed_at).toLocaleDateString()}` : ""}
                    {role.lifecycle.closed_by ? <> by <Person email={role.lifecycle.closed_by} people={people} me={me?.user} you avatar={false} /></> : null}
                  </>
                : "Open. Closing moves it to History here — iCIMS is unchanged."}
            </div>
          </div>
          {!closed ? (
            !picking && !readOnly && (
              <button
                onClick={() => setPicking(true)}
                style={{ height: 32, padding: "0 14px", borderRadius: 999, border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", fontSize: 12, fontWeight: 700, cursor: "pointer", fontFamily: "inherit", flex: "0 0 auto" }}
              >
                Close requisition
              </button>
            )
          ) : !readOnly ? (
            <Button variant="secondary" disabled title="Coming with feature flags">Reopen</Button>
          ) : null}
        </div>

        {error && <div style={{ marginTop: 12 }}><Callout variant="warn" title="Couldn't close">{error}</Callout></div>}

        {!closed && picking && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--neutral-150)" }}>
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center" }}>
              <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13.5, fontWeight: 700, cursor: "pointer" }}>
                <input type="radio" name="close-outcome" checked={outcome === "hired"} onChange={() => setOutcome("hired")} />
                Hired
              </label>
              {outcome === "hired" && (
                <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--ink-soft)" }}>
                  How many?
                  <input
                    className="input"
                    type="number"
                    min={1}
                    value={hiredCount}
                    onChange={(e) => setHiredCount(Math.max(1, Number(e.target.value) || 1))}
                    style={{ width: 76, height: 34 }}
                    aria-label="Hired count"
                  />
                </label>
              )}
              <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13.5, fontWeight: 700, cursor: "pointer" }}>
                <input type="radio" name="close-outcome" checked={outcome === "cancelled"} onChange={() => setOutcome("cancelled")} />
                No hire — cancelled
              </label>
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <Button onClick={confirmClose} disabled={busy}>Confirm close</Button>
              <Button variant="ghost" onClick={() => setPicking(false)} disabled={busy}>Keep it open</Button>
            </div>
          </div>
        )}
      </div>

      {/* delete requisition — irreversible; states exactly what is lost. Hidden
          for a read-only (Legal/audit) viewer. */}
      {!readOnly && (
      <div style={{ background: "var(--white)", border: "1px solid var(--hue-rose-tint)", borderRadius: 16, padding: "16px 22px", marginTop: 16 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 3 }}>Delete requisition</div>
            <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
              Removes it and all its screening data from this workspace. Can't be undone; iCIMS is unchanged.
            </div>
          </div>
          {!confirmingDelete && (
            <Button variant="secondary" onClick={() => { setConfirmingDelete(true); setDelError(null); }}
                    style={{ borderColor: "var(--hue-rose-tint)", color: "var(--hue-rose-ink)" }}>
              Delete requisition…
            </Button>
          )}
        </div>

        {delError && <div style={{ marginTop: 12 }}><Callout variant="warn" title="Couldn't delete">{delError}</Callout></div>}

        {confirmingDelete && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 14 }}>
            <Callout variant="warn" title="This permanently deletes:">
              <ul style={{ margin: "4px 0 0", paddingLeft: 18, display: "flex", flexDirection: "column", gap: 3 }}>
                <li>{role.requirement_count} checklist requirement{role.requirement_count === 1 ? "" : "s"}</li>
                <li>{role.batch_count} candidate résumé{role.batch_count === 1 ? "" : "s"} ({role.candidate_count} screened)</li>
                <li>{role.reviewed_count} decision{role.reviewed_count === 1 ? "" : "s"} / shortlist entr{role.reviewed_count === 1 ? "y" : "ies"}</li>
              </ul>
            </Callout>
            <div style={{ display: "flex", gap: 10 }}>
              <Button
                variant="secondary"
                onClick={confirmDelete}
                disabled={delBusy}
                style={{ borderColor: "var(--hue-rose-solid)", color: "var(--hue-rose-ink)" }}
              >
                {delBusy ? "Deleting…" : "Permanently delete"}
              </Button>
              <Button variant="ghost" onClick={() => setConfirmingDelete(false)} disabled={delBusy}>Cancel</Button>
            </div>
          </div>
        )}
      </div>
      )}
    </Page>
  );
}


/** Who else can work on this requisition.
 *
 *  A requisition belongs to whoever created it, and an own-reqs-scoped recruiter
 *  cannot see anyone else's. That is right as a default and wrong as an absolute:
 *  cover, handovers and a second pair of eyes are ordinary recruiting, and the
 *  alternative was asking an HR Head to widen someone's whole ROLE (2026-09-10).
 *
 *  Only the owner (or an HR Head) sees the controls — the server enforces the same
 *  rule, so this only spares everyone else a button that would 403.
 */
function SharedWith({ reqId, role, me, caps, people, readOnly, onChange }: {
  reqId: string; role: RoleDetail; me?: string; caps?: string[];
  people: PeopleIndex; readOnly: boolean; onChange: () => void;
}) {
  const [picked, setPicked] = useState<PickedPerson | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const shared = role.lifecycle?.collaborators ?? [];
  const owner = (role.lifecycle?.created_by ?? role.created_by ?? "").toLowerCase();
  const mine = !!me && owner === me.toLowerCase();
  // The same rule the server enforces (_may_share_req): the owner, or someone who
  // already sees every requisition. Showing it to anyone else would only produce a
  // button that 403s. "No owner on record" must not read as "you own it", so only a
  // real match counts.
  const mayShare = (mine || (caps ?? []).includes("reqs_all")) && !readOnly;

  async function act(run: () => Promise<unknown>) {
    setBusy(true); setErr(null);
    try {
      await run();
      invalidate(`/api/roles/${reqId}`);
      onChange();
      setPicked(null);
      setAdding(false);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const add = () => {
    const email = picked?.email.trim();
    if (!email) { setErr("Pick someone, or type their work email."); return; }
    void act(() => post(`/api/roles/${reqId}/collaborators`,
                        { email, name: picked?.name?.trim() ?? "" }));
  };

  if (!mayShare && shared.length === 0) return null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 14, fontSize: 13 }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5, fontWeight: 700, color: "var(--ink-soft)" }}>
        <IconPerson size={14} /> Shared with
      </span>
      {shared.length === 0 && !adding && (
        <span style={{ color: "var(--ink-muted)" }}>No one else yet</span>
      )}
      {shared.map((c) => (
        <span key={c.email} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Person email={c.email} known={c.name} people={people} me={me} size="sm" />
          {mayShare && (
            <button onClick={() => void act(() => del(`/api/roles/${reqId}/collaborators/${encodeURIComponent(c.email)}`))}
                    disabled={busy}
                    title={`Remove ${c.name || c.email} from this requisition`}
                    aria-label={`Remove ${c.name || c.email} from this requisition`}
                    style={{ background: "none", border: 0, cursor: "pointer", color: "var(--ink-soft)", padding: 2, display: "inline-flex" }}>
              <IconX size={12} />
            </button>
          )}
        </span>
      ))}
      {mayShare && !adding && (
        <button onClick={() => setAdding(true)}
                style={{ background: "none", border: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, color: "var(--brand-green-deep)", padding: 0 }}>
          Share with someone
        </button>
      )}
      {mayShare && adding && (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ minWidth: 240 }}>
            <PeoplePicker value={picked} onChange={setPicked} autoFocus
                          placeholder="Search by name or email…"
                          ariaLabel="Share this requisition with" manualLink />
          </span>
          <Button onClick={add} disabled={busy || !picked?.email}>Share</Button>
          <button onClick={() => { setAdding(false); setPicked(null); setErr(null); }}
                  style={{ background: "none", border: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 12.5, color: "var(--ink-soft)", padding: 0 }}>
            Cancel
          </button>
        </span>
      )}
      {err && <span style={{ fontSize: 12.5, color: "var(--warn-ink)" }}>{err}</span>}
      {shared.length > 0 && (
        <span style={{ fontSize: 12, color: "var(--ink-muted)", flexBasis: "100%" }}>
          They can see and work on this requisition. What they may DO still comes from
          their own role.
        </span>
      )}
    </div>
  );
}
