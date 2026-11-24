// New requisition (P32, replaces NewRole): the iCIMS Req ID is REQUIRED — it is
// the mapping anchor back to the real requisition — and the location picker
// declares its consequences (pack + PII retention) up front via /api/locations.
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Page } from "../layout/AppShell";
import { Button, MagicButton, Callout, Field, Select, Textarea, Spinner, BackButton } from "../components/ui";
import ChecklistEditor, { KindLegend, type CheckVerdict } from "../components/ChecklistEditor";
import JdPane from "../components/JdPane";
import { HiringRulesBody } from "../components/HiringRules";
import LocationPicker, { EMPTY_LOCATION, blockingReason, packFor, type LocationChoice } from "../components/LocationPicker";
import { PeoplePicker, type PickedPerson } from "../people";
import { IconBriefcase, IconPin, IconFile, IconPerson } from "../components/icons";
import { ApiError, get, post, put, uploadFiles } from "../api";
import { useFetch, useFeatureFlag, invalidate } from "../hooks";
import { tabPath } from "./workspace/context";
import { loadWizardDraft, saveWizardDraft, clearWizardDraft } from "../drafts";
import type { CareersLookupResult, ClonePrefill, DuplicateReqDetail, JdParseResult, LocationOption, LocationsFeed, Requirement, RolesResponse, Rubric } from "../types";

const ICIMS_REQUIRED = "iCIMS Req ID is required — no mapping to the requisition is possible without it.";

type Stage = "details" | "drafting" | "checklist";

// The two retention duties this location carries (P46), stated up front in
// plain language — the design's one-line pack note. Nothing auto-deletes.
function packNote(loc: LocationOption | undefined): string {
  if (!loc) return "";
  const parts = [`${loc.law} pack applies`];
  if (loc.keep_at_least_days) parts.push(`audit records kept at least ${loc.keep_at_least_days} days`);
  if (loc.purge_by_days) parts.push(`candidate PII purged ${loc.purge_by_days} days after close`);
  return parts.join(" · ");
}

interface Snapshot { icims: string; title: string; jd: string; loc: string }

/** A coloured group heading inside the form — two of them turn one long grey form
 *  into two short, named parts (pilot feedback item 7: "form fear"). */
function GroupHead({ hue, icon, title, hint }: { hue: string; icon: React.ReactNode; title: string; hint: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, paddingBottom: 4, borderBottom: "1px solid var(--neutral-150)" }}>
      <span className={`avatar avatar-hue-${hue}`} aria-hidden="true" style={{ width: 26, height: 26, borderRadius: 8 }}>{icon}</span>
      <span style={{ fontSize: 13.5, fontWeight: 700 }}>{title}</span>
      <span style={{ fontSize: 12, color: "var(--ink-soft)" }}>{hint}</span>
    </div>
  );
}

export default function NewRequisition() {
  const nav = useNavigate();
  // Clone prefill (P37): the History "Clone" button lands here with the source
  // requisition's title / JD / location / checklist. The iCIMS Req ID stays BLANK —
  // a clone is a brand-new requisition and needs its own anchor.
  const prefill = (useLocation().state as { prefill?: ClonePrefill } | null)?.prefill;
  const { data: feedData } = useFetch<LocationsFeed>("/api/locations");
  const feed = feedData ?? undefined;
  const [stage, setStage] = useState<Stage>("details");
  const [icims, setIcims] = useState("");
  const [title, setTitle] = useState(prefill?.title ?? "");
  const [jd, setJd] = useState(prefill?.jd_text ?? "");
  // WHERE the job is (2026-09-12). The pack is derived from this, never picked:
  // a recruiter should not have to know that Hungary is governed by the EU pack,
  // and the old picker's India-first default is how they ended up with India's.
  const [place, setPlace] = useState<LocationChoice>(
    prefill?.country ? { country: prefill.country, us_state: prefill.us_state ?? null,
                         nyc: prefill.job_location === "US-NYC" }
                     : EMPTY_LOCATION);
  // The hiring manager comes from the people directory (2026-09-06, item 6): a
  // picked person carries name + address together, so the old name-without-email
  // validation has nothing left to catch. The manual path still exists for someone
  // the directory has never met.
  const [hm, setHm] = useState<PickedPerson | null>(null);
  const [showRules, setShowRules] = useState(false);
  const [reqId, setReqId] = useState("");
  const [reqs, setReqs] = useState<Requirement[]>([]);
  const [resetSignal, setResetSignal] = useState(0);
  const [clonedChecklist, setClonedChecklist] = useState<Requirement[]>(prefill?.checklist ?? []);
  // Clone-from-existing picker (pilot feedback 2026-07-19): the History-row
  // Clone flow, surfaced ON this screen — pick any requisition, the form
  // prefills, the iCIMS Req ID stays fresh. Same icims_clone flag gates it.
  // LAZY: the roles list loads only when the picker is opened.
  const cloneOn = useFeatureFlag("icims_clone");
  const [clonePickerOpen, setClonePickerOpen] = useState(false);
  const [cloneSources, setCloneSources] = useState<RolesResponse["roles"]>([]);
  const [cloneFrom, setCloneFrom] = useState("");
  const [cloneBusy, setCloneBusy] = useState(false);
  const [cloneNote, setCloneNote] = useState<{ tone: "brand" | "warn"; text: string } | null>(null);

  const [cloneLoading, setCloneLoading] = useState(false); // the list of sources is on its way
  async function openClonePicker() {
    setClonePickerOpen(true);
    setCloneLoading(true);
    try {
      const r = await get<RolesResponse>("/api/roles");
      setCloneSources(r.roles ?? []);
    } catch {
      setCloneNote({ tone: "warn", text: "Couldn't load the requisitions to clone from." });
    } finally {
      setCloneLoading(false);
    }
  }
  const [error, setError] = useState<string | null>(null);
  const [icimsError, setIcimsError] = useState<string | null>(null);
  const [icimsClash, setIcimsClash] = useState<DuplicateReqDetail | null>(null);
  const [redraftOffer, setRedraftOffer] = useState(false);
  const [busy, setBusy] = useState(false);
  // The server-side snapshot of what's saved: while null the req hasn't been
  // created; once set, coming Back and going forward diffs against it instead of
  // blindly re-POSTing (which would 409 on the iCIMS id it just created).
  const [saved, setSaved] = useState<Snapshot | null>(null);
  // An unfinished-wizard offer, shown once on mount when a draft exists.
  const [resumeOffer, setResumeOffer] = useState(() => (prefill ? null : loadWizardDraft()));
  // TEMPORARY iCIMS bridge (feature_flags.careers_autofill): on iCIMS-field blur we
  // fetch title/location/JD from the public careers site. undefined = flag still
  // loading; false hides it entirely. Removable with the flag + backend route.
  const careersOn = useFeatureFlag("careers_autofill");
  const [lookupBusy, setLookupBusy] = useState(false);
  const [lookupNote, setLookupNote] = useState<{ tone: "brand" | "warn"; text: string } | null>(null);
  const [lastLookedUp, setLastLookedUp] = useState("");
  // The CITY the job sits in (distinct from `loc`, which is a jurisdiction code).
  // Captured at intake because the stored JD body does not keep it: the careers
  // posting carries it in its structured location, and the boilerplate stripper
  // removes it from the description text.
  const [workPlace, setWorkPlace] = useState("");
  // JD upload (permanent): parse an uploaded JD file into title + JD text.
  const [jdUploadBusy, setJdUploadBusy] = useState(false);
  const [jdUploadNote, setJdUploadNote] = useState<{ tone: "brand" | "warn"; text: string } | null>(null);
  const jdFileInput = useRef<HTMLInputElement>(null);

  // The pack this choice resolves to — nothing is preselected, so it is "" until
  // the recruiter says where the job is.
  const selectedCode = packFor(feed, place) ?? "";
  const selected = feed?.packs.find((l) => l.code === selectedCode);
  const placeBlocker = blockingReason(feed, place);

  // Autosave the wizard once it has an iCIMS id typed (no key collisions, nothing
  // stored for an untouched form). Debounced; a stale "drafting" stage restores
  // as "details".
  useEffect(() => {
    if (resumeOffer || !icims.trim()) return;
    const id = window.setTimeout(() => {
      saveWizardDraft({
        savedAt: new Date().toISOString(), reqId,
        details: { icims, title, jd, loc: selectedCode, place }, rows: reqs,
        stage: stage === "checklist" ? "checklist" : "details",
      });
    }, 600);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [icims, title, jd, selectedCode, reqs, reqId, stage, resumeOffer]);

  function resume() {
    const d = resumeOffer;
    if (!d) return;
    setIcims(d.details.icims);
    setTitle(d.details.title);
    setJd(d.details.jd);
    setPlace(d.details.place ?? EMPTY_LOCATION);
    setReqId(d.reqId);
    setReqs(d.rows);
    setResetSignal((s) => s + 1);
    if (d.reqId) setSaved({ icims: d.details.icims.trim(), title: d.details.title.trim(), jd: d.details.jd.trim(), loc: d.details.loc ?? "" });
    // Always land back on the DETAILS step (pilot feedback 2026-07-19: resuming
    // straight into a prefilled checklist reads as "where did this come from?").
    // Any drafted rows are kept — "Create draft checklist"/"Save & continue"
    // moves forward exactly as it did the first time.
    setStage("details");
    setResumeOffer(null);
  }
  function startFresh() {
    clearWizardDraft();
    setResumeOffer(null);
  }

  // TEMPORARY iCIMS bridge: on iCIMS-field blur, pre-fill from the careers site.
  // Non-fatal — never blocks the form; never clobbers what the recruiter typed.
  async function runCareersLookup() {
    const id = icims.trim();
    if (!careersOn || !id || reqId) return;      // flag off, empty, or already created
    if (title.trim() && jd.trim()) return;       // don't overwrite hand-entered details
    if (id === lastLookedUp) return;             // already tried this exact id
    setLastLookedUp(id);
    setLookupBusy(true);
    setLookupNote(null);
    try {
      const r = await get<CareersLookupResult>(`/api/careers/lookup?req=${encodeURIComponent(id)}`);
      if (r.status === "ok") {
        if (r.title && !title.trim()) setTitle(r.title);
        if (r.jd_text && !jd.trim()) setJd(r.jd_text);
        if (r.country) setPlace({ country: r.country, us_state: r.us_state ?? null,
                                  nyc: r.job_location === "US-NYC" });
        if (r.work_place && !workPlace.trim()) setWorkPlace(r.work_place);
        setLookupNote({ tone: "brand",
          text: `Filled from the careers site${r.location_label ? ` · ${r.location_label}` : ""}. Review before continuing.` });
      } else if (r.status !== "disabled") {
        setLookupNote({ tone: "warn", text: r.note || "Couldn't fetch the details — enter them below." });
        // "error" = transient (site unreachable / rate-limited): let the next
        // blur of the same id try again. not_found/ambiguous are definitive.
        if (r.status === "error") setLastLookedUp("");
      }
    } catch (e) {
      setLookupNote({ tone: "warn", text: `Couldn't fetch the details (${(e as Error).message}). Enter them below.` });
      setLastLookedUp("");
    } finally {
      setLookupBusy(false);
    }
  }

  // JD upload (permanent): parse a PDF/DOCX into title + JD text and pre-fill.
  async function uploadJd(file: File) {
    setJdUploadBusy(true);
    setJdUploadNote(null);
    try {
      const r = await uploadFiles<JdParseResult>("/api/jd/parse", [file]);
      if (r.title) setTitle(r.title);
      if (r.jd_text) setJd(r.jd_text);
      if (r.country) setPlace({ country: r.country, us_state: r.us_state ?? null,
                                nyc: r.job_location === "US-NYC" });
      if (r.work_place && !workPlace.trim()) setWorkPlace(r.work_place);
      // A country we have no rules for is worth saying out loud here — the form
      // would otherwise just refuse to continue with no explanation of why.
      const placeNote = r.country_without_pack && r.country_name
        ? ` · no hiring rules for ${r.country_name} yet — ask HR and Legal`
        : r.country_name ? ` · ${r.country_name}` : r.location_label ? ` · ${r.location_label}` : "";
      setJdUploadNote({ tone: r.country_without_pack ? "warn" : "brand",
        text: `Read “${file.name}”${placeNote}. Review and edit before continuing.` });
    } catch (e) {
      setJdUploadNote({ tone: "warn", text: `Couldn't read that file (${(e as Error).message}). Paste the JD instead.` });
    } finally {
      setJdUploadBusy(false);
    }
  }

  async function cloneFromReq(id: string) {
    setCloneFrom(id);
    setCloneNote(null);
    if (!id || reqId) return; // nothing picked, or this req is already created
    setCloneBusy(true);
    try {
      const p = await post<ClonePrefill>(`/api/roles/${id}/clone`);
      setTitle(p.title);
      setJd(p.jd_text);
      if (p.country) setPlace({ country: p.country, us_state: p.us_state ?? null,
                                nyc: p.job_location === "US-NYC" });
      setClonedChecklist(p.checklist ?? []);
      const src = cloneSources.find((r) => r.req_id === id);
      const rows = p.checklist?.length ?? 0;
      setCloneNote({ tone: "brand",
        text: `Cloned from “${p.title}”${src?.req_code ? ` (${src.req_code})` : ""} — ${rows} checklist row${rows === 1 ? "" : "s"} reused, no AI draft needed. Enter a fresh iCIMS Req ID; review everything before continuing.` });
    } catch (e) {
      setCloneNote({ tone: "warn", text: `Couldn't clone (${(e as Error).message}).` });
      setCloneFrom("");
    } finally {
      setCloneBusy(false);
    }
  }

  function handleClash(e: unknown): boolean {
    if (e instanceof ApiError && e.status === 409 && e.detail && typeof e.detail === "object") {
      setIcimsClash(e.detail as DuplicateReqDetail);
      return true;
    }
    return false;
  }

  async function draftInto(id: string) {
    setStage("drafting");
    try {
      const rubric = await post<Rubric>(`/api/roles/${id}/rubric/draft`);
      setReqs(rubric.requirements);
    } catch (e) {
      // Azure unreachable etc. — let the recruiter hand-write the checklist.
      setError(`Couldn't auto-draft (${(e as Error).message}). Add the requirements yourself below.`);
      setReqs([{ text: "", kind: "must_have", importance: 4, type: "other" }]);
    }
    setResetSignal((s) => s + 1);
    setStage("checklist");
  }

  async function submitDetails() {
    setError(null);
    setIcimsError(null);
    setIcimsClash(null);
    if (!icims.trim()) return setIcimsError(ICIMS_REQUIRED);
    if (!title.trim()) return setError("A job title is required.");
    if (!jd.trim()) return setError("Paste the job description.");
    if (placeBlocker) return setError(placeBlocker);
    setBusy(true);
    try {
      const snap: Snapshot = { icims: icims.trim(), title: title.trim(), jd: jd.trim(), loc: selectedCode };
      if (!reqId) {
        // First pass — create the requisition.
        const created = await post<{ req_id: string }>("/api/roles", {
          title: snap.title, jd_text: snap.jd, job_location: snap.loc, icims_req_id: snap.icims,
          country: place.country, us_state: place.us_state, nyc: place.nyc,
          ...(workPlace.trim() ? { work_place: workPlace.trim() } : {}),
          ...(hm ? { hm_name: hm.name.trim(), hm_email: hm.email.trim() } : {}),
        });
        setReqId(created.req_id);
        setSaved(snap);
        // The Requisitions dashboard reads a cached /api/roles list; drop it so
        // returning there (or being redirected) shows the just-created req without
        // a manual page refresh (the codebase convention: mutations invalidate).
        invalidate("/api/roles");
        // A cloned checklist is reused verbatim — no AI draft (or model spend).
        if (clonedChecklist.length > 0) {
          setReqs(clonedChecklist);
          setResetSignal((s) => s + 1);
          setStage("checklist");
        } else {
          await draftInto(created.req_id);
        }
        return;
      }
      // Came Back to details on an already-created req — only PUT what changed,
      // and never re-POST /api/roles (that would clash on its own iCIMS id).
      const detailsDirty = saved
        ? (snap.title !== saved.title || snap.loc !== saved.loc || snap.icims !== saved.icims)
        : false;
      const jdDirty = saved ? snap.jd !== saved.jd : false;
      if (detailsDirty || jdDirty) {
        await put(`/api/roles/${reqId}/details`, {
          title: snap.title, jd_text: snap.jd, job_location: snap.loc, icims_req_id: snap.icims,
          country: place.country, us_state: place.us_state, nyc: place.nyc,
          ...(workPlace.trim() ? { work_place: workPlace.trim() } : {}),
        });
        setSaved(snap);
      }
      setStage("checklist");
      if (jdDirty) setRedraftOffer(true); // offer to re-draft from the new JD
    } catch (e) {
      if (!handleClash(e)) setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function redraft() {
    setRedraftOffer(false);
    setBusy(true);
    try {
      await draftInto(reqId);
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    setBusy(true);
    setError(null);
    try {
      const res = await put<Rubric>(`/api/roles/${reqId}/rubric`, {
        requirements: reqs.filter((r) => r.text.trim()),
      });
      clearWizardDraft(); // published — the in-flight wizard draft is spent
      // A flagged checklist needs HR-Head sign-off before it's usable — hand the
      // recruiter to the Checklist tab (its approval banner + Send-for-approval
      // machinery), never straight to Candidates with an unusable checklist.
      if (res.needs_approval) nav(tabPath(reqId, "checklist"));
      else nav(`/reqs/${reqId}/candidates`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <Page max={stage === "checklist" ? 1180 : 760}>
      <BackButton onClick={() => nav("/reqs")} label="Cancel" />
      {/* Once a title exists it joins the header — on the checklist step the
          recruiter must always see WHICH role they're building the list for. */}
      <h1 style={{ fontSize: 24, letterSpacing: "-0.01em", margin: "10px 0 18px" }}>
        New requisition{title.trim() ? <span style={{ color: "var(--ink-soft)", fontWeight: 600 }}> — {title.trim()}</span> : ""}
      </h1>
      {stage === "checklist" && (
        <p style={{ fontSize: 14, color: "var(--ink-soft)", margin: "-8px 0 24px" }}>
          Review and approve the screening checklist for
          {" "}<strong style={{ color: "var(--ink)" }}>{title.trim() || "this role"}</strong>
          {icims.trim() ? <span style={{ fontFamily: "monospace" }}> ({icims.trim()})</span> : null}
          {" "}— you decide every line.
        </p>
      )}

      {error && (
        <div style={{ marginBottom: 18 }}>
          <Callout variant="warn" title="Heads up">{error}</Callout>
        </div>
      )}

      {resumeOffer && (
        <div style={{ marginBottom: 18 }}>
          <Callout variant="info" title="Resume your unfinished requisition?">
            You have a saved draft{resumeOffer.details.title ? ` for “${resumeOffer.details.title}”` : ""} — pick up where you left off, or start fresh.
            <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
              <Button size="sm" onClick={resume}>Resume</Button>
              <Button size="sm" variant="ghost" onClick={startFresh}>Start fresh</Button>
            </div>
          </Callout>
        </div>
      )}

      {stage === "details" && (
        <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, padding: "22px 26px", display: "flex", flexDirection: "column", gap: 16 }}>
          {/* clone-from-existing — title + Req ID visible in the dropdown itself */}
          {cloneOn && !reqId && !clonePickerOpen && (
            <button
              onClick={() => void openClonePicker()}
              style={{ alignSelf: "flex-start", background: "none", border: 0, padding: 0, fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, color: "var(--brand-green-deep)", cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 2 }}
            >
              Start from an existing requisition…
            </button>
          )}
          {cloneOn && !reqId && clonePickerOpen && (
            <div style={{ background: "var(--neutral-100)", border: "1px solid var(--border-subtle)", borderRadius: 12, padding: "12px 14px" }}>
              <Field label="Start from an existing requisition" hint="optional — copies the title, JD, location and checklist; the iCIMS Req ID stays fresh">
                <Select value={cloneFrom} onChange={(e) => cloneFromReq(e.target.value)} disabled={cloneBusy || cloneLoading} aria-label="Clone from requisition" autoComplete="off">
                  <option value="">{cloneLoading ? "Loading your requisitions…" : "— start blank —"}</option>
                  {cloneSources.map((r) => (
                    <option key={r.req_id} value={r.req_id}>
                      {r.title} · {r.req_code || "no Req ID"} · {r.location_display}{r.lifecycle.status === "closed" ? " · closed" : ""}
                    </option>
                  ))}
                </Select>
              </Field>
              {/* The list used to arrive silently — the dropdown looked frozen (item 5). */}
              {cloneLoading && (
                <div role="status" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--ink-soft)", marginTop: 6 }}>
                  <Spinner size={13} /> Loading your requisitions…
                </div>
              )}
              {cloneBusy && (
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--ink-soft)", marginTop: 6 }}>
                  <Spinner size={13} /> Copying the requisition…
                </div>
              )}
              {cloneNote && (
                <div role="status" style={{ fontSize: 12.5, marginTop: 6,
                  color: cloneNote.tone === "warn" ? "var(--warn-ink)" : "var(--brand-green-deep)",
                  fontWeight: cloneNote.tone === "warn" ? 700 : 400 }}>
                  {cloneNote.text}
                </div>
              )}
            </div>
          )}
          {/* Group 1 — the requisition itself: three short fields. */}
          <GroupHead hue="slate" icon={<IconBriefcase size={14} />} title="The requisition" hint="Three fields, one of them from iCIMS." />
          <div>
            {/* the design's Required chip — the one field with no fallback */}
            <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 6, display: "flex", alignItems: "center", gap: 8 }}>
              iCIMS Req ID
              <span style={{ fontSize: 10.5, fontWeight: 700, padding: "2px 8px", borderRadius: 6, background: "var(--hue-amber-tint)", color: "var(--hue-amber-ink)" }}>Required</span>
            </div>
            <input
              className="input"
              aria-label="iCIMS Req ID"
              autoComplete="off"
              value={icims}
              onChange={(e) => { setIcims(e.target.value); if (icimsError) setIcimsError(null); if (icimsClash) setIcimsClash(null); if (lookupNote) setLookupNote(null); }}
              onBlur={runCareersLookup}
              placeholder="e.g. 2026-1042"
              style={{ height: 42, fontFamily: "monospace", ...(icimsError || icimsClash ? { borderColor: "var(--warn-dot)" } : {}) }}
              aria-invalid={!!icimsError || !!icimsClash}
            />
            {icimsError && (
              <div role="alert" style={{ fontSize: 12.5, color: "var(--warn-ink)", marginTop: 6, fontWeight: 700 }}>
                {icimsError}
              </div>
            )}
            {icimsClash && (
              <div role="alert" style={{ fontSize: 12.5, color: "var(--warn-ink)", marginTop: 6, fontWeight: 700 }}>
                {icimsClash.message}{" "}
                <Link to={`/reqs/${icimsClash.req_id}`} style={{ color: "var(--brand-green-deep)" }}>
                  Open {icimsClash.title} →
                </Link>
              </div>
            )}
            <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginTop: 6 }}>
              The anchor back to iCIMS.{careersOn ? " Tab out and the job details fill in from the careers site." : ""}
            </div>
            {careersOn && lookupBusy && (
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--ink-soft)", marginTop: 6 }}>
                <Spinner size={13} /> Looking up the requisition on the careers site…
              </div>
            )}
            {lookupNote && (
              <div role="status" style={{ fontSize: 12.5, marginTop: 6,
                color: lookupNote.tone === "warn" ? "var(--warn-ink)" : "var(--brand-green-deep)",
                fontWeight: lookupNote.tone === "warn" ? 700 : 400 }}>
                {lookupNote.text}
              </div>
            )}
          </div>

          <Field label="Job title">
            <input className="input" autoComplete="off" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Senior Treasury Accountant" style={{ height: 44 }} />
          </Field>

          <div>
            <LocationPicker feed={feed} value={place} onChange={setPlace} />
            {placeBlocker && place.country && (
              <div style={{ fontSize: 12, color: "var(--warn-ink)", marginTop: 6, fontWeight: 700 }}>
                {placeBlocker}
              </div>
            )}
            {/* the consequences of this location, declared up front (design: one quiet line)
                — with the pack's own duties one click away (item 9) */}
            {selected && (
              <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 6, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span style={{ display: "inline-flex", color: "var(--hue-blue-ink)" }}><IconPin size={12} /></span>
                <span>{packNote(selected)}</span>
                <button type="button" className="link-quiet" onClick={() => setShowRules((v) => !v)}>
                  {showRules ? "Hide the rules" : "What are these rules?"}
                </button>
              </div>
            )}
            {selected && showRules && (
              <div style={{ marginTop: 8, padding: "10px 12px", background: "var(--hue-blue-tint)", borderRadius: 10 }}>
                <HiringRulesBody loc={selected} law={selected.law} />
              </div>
            )}
          </div>

          {/* Group 2 — the job: the JD (paste or upload) and who is hiring. */}
          <GroupHead hue="teal" icon={<IconFile size={14} />} title="The job" hint="Paste or upload the JD — the checklist is drafted from it." />
          <div>
            <Field label="Job description">
              <Textarea value={jd} onChange={(e) => setJd(e.target.value)} autoComplete="off" placeholder="Paste the full JD, or upload a PDF/DOCX below." style={{ minHeight: 170 }} />
            </Field>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
              <input
                ref={jdFileInput}
                type="file"
                accept=".pdf,.docx"
                style={{ display: "none" }}
                onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadJd(f); e.currentTarget.value = ""; }}
              />
              <Button size="sm" variant="secondary" onClick={() => jdFileInput.current?.click()} disabled={jdUploadBusy}>
                {jdUploadBusy ? <><Spinner size={13} /> Reading…</> : "Upload JD (PDF/DOCX)"}
              </Button>
              {jdUploadNote && (
                <span role="status" style={{ fontSize: 12.5,
                  color: jdUploadNote.tone === "warn" ? "var(--warn-ink)" : "var(--brand-green-deep)",
                  fontWeight: jdUploadNote.tone === "warn" ? 700 : 400 }}>
                  {jdUploadNote.text}
                </span>
              )}
            </div>
          </div>

          <div>
            <Field label="Hiring manager" hint="optional — from the directory">
              <PeoplePicker value={hm} onChange={setHm} placeholder="Search by name or email…" ariaLabel="Hiring manager" manualLink />
            </Field>
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ink-soft)", marginTop: 6 }}>
              <IconPerson size={12} /> Named for reference; inviting them to review is a separate step later.
            </div>
          </div>

          <div style={{ display: "flex", gap: 10, alignItems: "center", paddingTop: 4, flexWrap: "wrap" }}>
            {/* ✨ only for the AI draft on first create; the Back-then-forward path
                (req already exists) is a plain save — no model call. */}
            {reqId ? (
              <Button onClick={submitDetails} disabled={busy}>Save &amp; continue to checklist</Button>
            ) : (
              <MagicButton onClick={submitDetails} disabled={busy}>Create draft checklist</MagicButton>
            )}
            <span style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>Drafts the checklist from the JD — you approve every line.</span>
          </div>
        </div>
      )}

      {stage === "drafting" && (
        <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, padding: "60px 28px", display: "flex", flexDirection: "column", alignItems: "center", gap: 18, textAlign: "center" }}>
          <Spinner />
          <div style={{ fontSize: 16, fontWeight: 700 }}>Reading the job description…</div>
          <p style={{ fontSize: 13.5, color: "var(--ink-soft)", maxWidth: 420, margin: 0 }}>
            Drafting a screening checklist of must-haves and nice-to-haves. You'll edit and approve every line before anything is scored.
          </p>
        </div>
      )}

      {stage === "checklist" && (
        <>
          {redraftOffer && (
            <div style={{ marginBottom: 18 }}>
              <Callout variant="info" title="The job description changed">
                Re-draft the checklist from the updated JD? This replaces your current rows.
                <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
                  <MagicButton onClick={redraft} disabled={busy}>Re-draft checklist</MagicButton>
                  <Button variant="ghost" onClick={() => setRedraftOffer(false)}>Keep my rows</Button>
                </div>
              </Callout>
            </div>
          )}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
            <span style={{ fontSize: 13, color: "var(--ink-soft)" }}>
              Edit the wording, set the kind and how much each line counts — résumés are scored against exactly what you approve.
            </span>
            <KindLegend />
          </div>
          <div className="checklist-layout">
            <div className="checklist-main">
              <ChecklistEditor
                requirements={reqs}
                onChange={setReqs}
                resetSignal={resetSignal}
                onCheckText={reqId
                  ? (text) => post<CheckVerdict>(`/api/roles/${reqId}/checklist/check`, { text })
                  : undefined}
              />
              <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
                <Button onClick={approve} disabled={busy || !reqs.some((r) => r.text.trim())}>
                  Approve checklist &amp; add résumés
                </Button>
                <Button variant="ghost" onClick={() => setStage("details")}>Back to details</Button>
              </div>
            </div>
            <JdPane jd={jd} />
          </div>
        </>
      )}
    </Page>
  );
}
