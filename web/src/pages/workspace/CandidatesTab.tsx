// The workspace's Candidates tab: checklist status + batch manager + the
// screen CTA, scoped to one requisition (moved from the pre-v2 Screen page).
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Page } from "../../layout/AppShell";
import { Button, MagicButton, Callout, Spinner, TableSkeleton } from "../../components/ui";
import { IconChecklist, IconFile, IconChevronRight, IconDownload, IconShield, IconX } from "../../components/icons";
import { get, post, del, uploadFilesInChunks } from "../../api";
import { bumpPolling, invalidate, useFeatureFlag } from "../../hooks";
import { Person, usePeople, usePersonName } from "../../people";
import { driftLines } from "../../lib/driftCopy";
import BatchQualityChip, { batchTip } from "../../components/BatchQualityChip";
import { useMe } from "../../MeContext";
import { useWorkspace, tabPath } from "./context";
import { ConfirmModal, OverlayRoot } from "../../components/overlays";
import LockControl from "../../components/LockControl";
import type {
  Batch, Preflight, PreflightFileStatus, RoleDetail, ScreenJob, ScreenRunEntry,
  ScreenRunsResponse,
} from "../../types";

// Above this many résumés a batch is "bulk" (a real case is a single iCIMS Bulk
// Print split into 500+): the file list gets its own scroll area so the Screen
// CTA below stays in view, and split groups become collapsible. Tunable.
const BULK_LIST_THRESHOLD = 30;

interface FileTag {
  text: string;
  bg: string;
  fg: string;
  /** The row-tint class (app.css .file-row.st-*) — the WHOLE row wears the status
   *  (2026-09-06, item 11); a chip alone was easy to miss in a 300-row list. */
  status: "not_screened" | "preparing" | "screening" | "screened" | "re_screen" | "problem" | "info";
}

// Per-file screening status (P35) — one four-term vocabulary everywhere:
// "not screened" (never scored) → "screening" (in the live run) → "screened"
// (result on file) → "re-screen" (an updated résumé, or a checklist / rules /
// preference change, makes the next run redo it). "Cached" never surfaces —
// it's an implementation detail that reads as either screened or re-screen
// depending on the scenario. Colors match the term: pending neutral, live
// blue, done green, redo amber.
const STATUS_TAG: Record<"not_screened" | "screening" | "screened" | "re_screen", FileTag> = {
  not_screened: { text: "not screened", bg: "var(--neutral-150)", fg: "var(--ink-soft)", status: "not_screened" },
  screening: { text: "screening…", bg: "var(--hue-blue-tint)", fg: "var(--hue-blue-ink)", status: "screening" },
  screened: { text: "screened", bg: "var(--hue-green-tint)", fg: "var(--hue-green-ink)", status: "screened" },
  re_screen: { text: "re-screen", bg: "var(--hue-amber-tint)", fg: "var(--hue-amber-ink)", status: "re_screen" },
};

// "Upload & start screening" was chosen, so a run is coming for this résumé the moment
// preparation ends. "not screened" is literally true and reads as a dead end — the
// recruiter is standing there watching for a sign that the next step is happening, and
// a row that says nothing is why they reach for the Screen button that is already
// spoken for (2026-09-11 pilot). Orange = activity, the same hue the workspace uses.
const PREPARING_TAG: FileTag = {
  text: "preparing to screen", bg: "var(--hue-orange-tint)", fg: "var(--hue-orange-ink)", status: "preparing",
};

// A derived slice that still holds several résumés (the split couldn't pin those
// names to their own pages). Screening refuses it, so flag it before the run.
const MULTI_RESUME_TAG: FileTag = {
  text: "several résumés", bg: "var(--hue-rose-tint)", fg: "var(--hue-rose-ink)", status: "problem",
};

// A résumé the splitter separated but could not put a name to (its printed name
// didn't match the bulk file's contents page). It screens normally — only the
// display name is missing — so this reads as a small task, not a failure.
const UNNAMED_TAG: FileTag = {
  text: "name not detected", bg: "var(--neutral-150)", fg: "var(--ink-soft)", status: "info",
};
const UNNAMED_MARKER = "name not detected";

// A plain-language summary of what config moved since the last run (P40 adds
// hiring preferences to the checklist/rules/location set preflight tracks).
function changedLabel(changed: Preflight["changed"]): string {
  const parts: string[] = [];
  if (changed.checklist) parts.push("Checklist");
  if (changed.rules) parts.push("rules");
  if (changed.location) parts.push("location");
  if (changed.scoring_model) parts.push("scoring model");
  if (changed.preferences) parts.push("hiring preferences");
  if (parts.length === 0) return "The screening setup";
  if (parts.length === 1) return parts[0][0].toUpperCase() + parts[0].slice(1);
  return parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];
}

// One résumé row in the batch list. Reused flat and, indented, under a split
// bulk file's group header. The row wears its status as a tint; download and
// remove sit apart (a gap and a divider) and remove SAYS what it does — the two
// icons used to touch, and the ✕ explained nothing on hover (item 11).
function FileRow({ name, tag, busy, onRemove, downloadUrl, indent = false }: {
  name: string; tag?: FileTag; busy: boolean; onRemove: () => void; downloadUrl?: string; indent?: boolean;
}) {
  const ext = name.toLowerCase().endsWith(".docx") ? "DOC" : "PDF";
  return (
    <div className={"file-row" + (tag ? ` st-${tag.status}` : "")}
         style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 14px 9px 18px", paddingLeft: indent ? 42 : 18, borderBottom: "1px solid var(--neutral-150)", fontSize: 13 }}>
      <span style={{ flex: "0 0 auto", width: 28, height: 28, borderRadius: 7, background: "var(--white)", border: "1px solid var(--border-subtle)", display: "inline-flex", alignItems: "center", justifyContent: "center", color: "var(--ink-soft)", fontSize: 10, fontWeight: 700 }}>{ext}</span>
      <span style={{ flex: "1 1 auto", wordBreak: "break-all" }}>{name}</span>
      {tag && (
        <span style={{ flex: "0 0 auto", fontSize: 11, fontWeight: 700, padding: "3px 9px", borderRadius: 999, background: tag.bg, color: tag.fg, whiteSpace: "nowrap" }}>
          {tag.text}
        </span>
      )}
      {downloadUrl && (
        <a href={downloadUrl} download title={`Download ${name}`} aria-label={`Download ${name}`}
           style={{ flex: "0 0 auto", display: "inline-flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: 7, color: "var(--ink-soft)" }}>
          <IconDownload size={16} />
        </a>
      )}
      <span aria-hidden="true" style={{ width: 1, height: 18, background: "var(--border-subtle)", margin: "0 4px" }} />
      <button onClick={onRemove} disabled={busy} aria-label={`Remove ${name} from this batch`}
              title="Remove from this batch (the file can be added again)"
              className="row-remove"
              style={{ flex: "0 0 auto", display: "inline-flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: 7, background: "none", border: 0, cursor: busy ? "default" : "pointer", color: "var(--hue-rose-ink)" }}>
        <IconX size={15} />
      </button>
    </div>
  );
}

// The per-file / whole-batch download URLs (served straight from the batch,
// pre-screening — see app.py download_batch_file / download_batch_zip).
const batchFileUrl = (reqId: string, name: string) =>
  `/api/roles/${reqId}/batch/file/${encodeURIComponent(name)}`;
const batchZipUrl = (reqId: string) => `/api/roles/${reqId}/batch/download`;

// "about N min left" from the run's own remaining-time estimate (null = unknown).
function etaLabel(seconds?: number | null): string | null {
  if (seconds == null || seconds <= 0) return null;
  if (seconds < 75) return "under a minute left";
  return `about ${Math.round(seconds / 60)} min left`;
}

function runWhen(ts: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

// One line of the run history. Completed runs render the outcome from their
// counts — the "met every must-have" number links to Review filtered to exactly
// those candidates, so the criterion is shown, not just told.
function RunLine({ entry, reqId }: { entry: ScreenRunEntry; reqId: string }) {
  const when = runWhen(entry.ts);
  const nameOf = usePersonName(); // a person, never a payroll UPN (item 11)
  if (entry.kind === "screen_completed" && entry.counts) {
    const c = entry.counts;
    const met = `${c.gate_passers} met every must-have`;
    return (
      <>
        Screened {c.scored} of {c.resumes_in} résumé{c.resumes_in === 1 ? "" : "s"}
        {entry.duration_seconds != null ? ` in ${entry.duration_seconds}s` : ""}
        {/* A tally of the AI's gate results — HR roles only (InfoSec, 2026-10-14). */}
        {c.gate_passers == null ? null : <>{" — "}{c.gate_passers > 0 ? (
          <Link to={`${tabPath(reqId, "review")}?show=full_look`} style={{ color: "var(--brand-green-deep)", fontWeight: 700 }}>
            {met}
          </Link>
        ) : (
          met
        )}</>}
        {" "}· {c.errors} error{c.errors === 1 ? "" : "s"}
        {entry.actor ? ` · by ${nameOf(entry.actor)}` : ""}{when ? ` · ${when}` : ""}
      </>
    );
  }
  // stopped / failed / interrupted (and legacy entries): the summary already
  // names who did what — just anchor it in time.
  return <>{entry.summary}{when ? ` · ${when}` : ""}</>;
}

export function CandidatesPanel({ role, reload }: { role: RoleDetail; reload: () => void }) {
  const reqId = role.req_id;
  const nav = useNavigate();
  const me = useMe();

  const people = usePeople();
  const nameOf = usePersonName();
  // Manual locks are behind a flag (item 13); an existing lock still shows.
  const locksOn = useFeatureFlag("manual_locks") ?? false;
  const [batch, setBatch] = useState<Batch | null>(null);
  const [batchLoading, setBatchLoading] = useState(true); // first fetch in flight
  const [pre, setPre] = useState<Preflight | null>(null);
  const [note, setNote] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<ScreenJob | null>(null);
  // Whether we have asked the server if a run is under way, for THIS requisition.
  const [knowIfRunning, setKnowIfRunning] = useState(false);
  const [runs, setRuns] = useState<ScreenRunsResponse | null>(null);
  const [showAllRuns, setShowAllRuns] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which split groups are collapsed (bulk only; default expanded).
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const fileInput = useRef<HTMLInputElement>(null);
  const poll = useRef<number | null>(null);

  // Preflight is advisory — when it can't load, fall back to the generic copy.
  const refreshPreflight = () =>
    get<Preflight>(`/api/roles/${reqId}/screen/preflight`).then(setPre).catch(() => setPre(null));

  // The persistent run log (who ran, when, outcome). Advisory like preflight.
  const refreshRuns = () =>
    get<ScreenRunsResponse>(`/api/roles/${reqId}/screen/runs`).then(setRuns).catch(() => setRuns(null));

  // Poll a job by id until it stops running, then refresh cached views. Shared
  // by startScreen and the mount re-attach so the two can't drift.
  function beginPolling(jobId: string) {
    if (poll.current) window.clearInterval(poll.current);
    poll.current = window.setInterval(async () => {
      try {
        const s = await get<ScreenJob>(`/api/screen/jobs/${jobId}`);
        setJob(s);
        if (s.status !== "running" && poll.current) {
          window.clearInterval(poll.current);
          poll.current = null;
          void refreshPreflight(); // the run just changed what's cached
          void refreshRuns();
          // the outcome event lands moments after the job flips — pick it up
          window.setTimeout(() => void refreshRuns(), 1500);
          invalidate(`/api/roles/${reqId}`); // screened → Review/Shortlist data changed
          reload();
        }
      } catch {
        /* keep polling */
      }
    }, 1200);
  }

  useEffect(() => {
    // Clear the previous req's cached views BEFORE refetching — otherwise a req
    // switch briefly shows the old requisition's run history / batch / job (and a
    // fresh req, which has no runs, would flash the prior req's history). Each
    // surface below is scoped to `reqId`; this just stops stale carry-over.
    setBatch(null);
    setBatchLoading(true);
    setPre(null);
    setRuns(null);
    setJob(null);
    setKnowIfRunning(false);   // a different requisition: we do not know yet
    setShowAllRuns(false);
    // The first load shows a skeleton, never "0 files" (item 5): the card used to
    // announce an empty batch while the real one was still on its way.
    get<Batch>(`/api/roles/${reqId}/batch`)
      .then((b) => { setBatch(b); setBatchLoading(false); })
      .catch(() => { setBatch(null); setBatchLoading(false); });
    void refreshPreflight();
    void refreshRuns();
    setNote("");
    setError(null);
    // Re-attach to a run already in flight (started here before we navigated
    // away, or from another window) — the screening never stopped server-side,
    // so returning to the tab shows its live progress, not the CTA. A run that
    // STOPPED or FAILED re-shows its panel too, so its state (and which résumés
    // it screened) survives a reload / user switch instead of dropping to a bare
    // CTA. A completed run is left to the preflight CTA + run history.
    get<{ job: ScreenJob | null; last?: ScreenJob | null }>(`/api/roles/${reqId}/screen/active`)
      .then((r) => {
        if (r.job && r.job.status === "running") {
          setJob(r.job);
          beginPolling(r.job.job_id);
        } else if (r.last && (r.last.status === "paused" || r.last.status === "stopped" || r.last.status === "error")) {
          // paused keeps its panel too — the batch is still locked by it.
          setJob(r.last);
        } else {
          setJob(null);
        }
      })
      .catch(() => setJob(null))
      .finally(() => setKnowIfRunning(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reqId]);

  useEffect(() => () => { if (poll.current) window.clearInterval(poll.current); }, []);

  // P51 auto-refresh (user decision 2026-07-18): when the only drift is FREE to
  // recompute (bands-style changes — every model stage cache-hits) and nothing
  // has been decided yet, fire the ordinary screen once so scores simply
  // refresh. Ref-guarded: one shot per mount, never while a run is in flight.
  const autoRescreened = useRef(false);
  useEffect(() => {
    // Never while résumés are still being prepared: the server would 409 and the
    // one-shot would be spent on it. The effect re-evaluates when prep finishes.
    if (autoRescreened.current || !pre || job || batch?.preparing?.active) return;
    if (pre.stale?.mode === "auto" && pre.will_run) {
      autoRescreened.current = true;
      void startScreen();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pre, job, batch?.preparing?.active]);

  // What the blocking overlay says. It used to fall back to the last upload note, so
  // clicking "Clear batch" announced "Uploading…" — the one moment a recruiter most
  // needs to trust what the screen says about their candidate list.
  const [busyLabel, setBusyLabel] = useState("Working…");

  async function refreshBatch(promise: Promise<Batch>, label = "Working…") {
    setBusyLabel(label);
    setBusy(true);
    setError(null);
    try {
      const b = await promise;
      setBatch(b);
      if (b.sentence) setNote(b.sentence);
      void refreshPreflight(); // every batch mutation can change what would run
      invalidate(`/api/roles/${reqId}`); // batch count changed
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // Chunked + sequential: a single request carrying a whole iCIMS Bulk Print batch
  // exceeded the gateway timeout and 504'd. Each group is now just a byte copy —
  // the server stages files instantly and prepares them in the background — so a
  // chunk's duration is upload bandwidth, nothing more.
  // Dropping files ASKS what to do next rather than assuming (2026-08-27). A bulk
  // drop is often the last thing a recruiter does before walking away, so "upload,
  // then screen it yourself" has to be offered at the moment of dropping — coming
  // back later just to click Screen is the thing they wanted to avoid.
  const [pending, setPending] = useState<File[] | null>(null);
  const onFiles = (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length) setPending(list);
  };

  // Did THIS tab just ask for a screening run? The server records the intent, but only
  // after the upload finishes — and between the click and that acknowledgement the
  // Screen button came back to life, offering to start a second run of exactly what is
  // already on its way (2026-09-11 pilot). The hold starts at the click.
  const [autoScreenAsked, setAutoScreenAsked] = useState(false);

  function startUpload(list: File[], autoScreen: boolean) {
    setPending(null);
    if (autoScreen) setAutoScreenAsked(true);
    // The intent rides on the URL so EVERY chunk carries it, and it is recorded
    // server-side — a client-side trigger would die with the tab, which is exactly
    // the case this feature exists for.
    const path = `/api/roles/${reqId}/batch/upload${autoScreen ? "?auto_screen=1" : ""}`;
    void refreshBatch(uploadFilesInChunks<Batch>(path, list, (done, total) => {
      const at = total > 1 ? ` group ${done + 1} of ${total}` : "";
      setBusyLabel(done < total ? `Uploading…${at}` : "Upload complete — preparing résumés…");
      if (total > 1) setNote(done < total
        ? `Uploading… group ${done + 1} of ${total}.`
        : `Upload complete — preparing résumés…`);
      // An upload that never landed asked the server for nothing, so the hold has to
      // come off with it — re-thrown so the error still surfaces the usual way.
    }).catch((e) => { if (autoScreen) setAutoScreenAsked(false); throw e; }), "Uploading…");
  }

  // Stop preparing: keeps the candidates already added, discards what is still
  // queued. Offered because a 500-résumé drop is otherwise a decision you cannot
  // take back.
  async function stopPreparing() {
    setStopAsk(false);
    await refreshBatch(post<Batch>(`/api/roles/${reqId}/batch/prepare/stop`, {}),
                       "Stopping…");
  }
  const [stopAsk, setStopAsk] = useState(false);

  // While the server prepares staged résumés (splitting bulk files, checking
  // duplicates) the batch grows on its own — poll it. Coming back to this tab
  // re-attaches to a preparation already in flight, same as screening runs.
  const prep = batch?.preparing;
  const preparing = !!prep?.active;
  const wasPreparing = useRef(false);
  useEffect(() => {
    if (!preparing) {
      if (wasPreparing.current) {
        // Just finished: surface the summary and refresh what depends on the batch.
        wasPreparing.current = false;
        if (prep?.sentence) setNote(prep.sentence);
        void refreshPreflight();
        invalidate(`/api/roles/${reqId}`);
        reload();
        // "Upload & start screening" hands the intent to the SERVER (?auto_screen=1)
        // so it survives the tab closing — which meant the run began with nothing
        // telling this tab about it, and the page went on offering a Screen button
        // for a requisition that was already screening (2026-09-10). Ask once, at
        // the one moment a run can have appeared without us starting it. One
        // request, at a transition, and it re-renders only the CTA block.
        // When the upload asked for it, hold the Screen call-to-action for the one
        // round trip this takes. Otherwise the CTA appears the instant preparation
        // ends and is replaced ~100 ms later by the progress — a flash offering an
        // action that is already under way.
        void get<{ job: ScreenJob | null }>(`/api/roles/${reqId}/screen/active`)
          .then((r) => {
            if (r.job && r.job.status === "running") {
              setJob(r.job);
              beginPolling(r.job.job_id);
              bumpPolling();   // the workspace banner and dashboard, now not in 20 s
            }
          })
          .catch(() => { /* the mount re-attach still covers a revisit */ })
          .finally(() => { setKnowIfRunning(true); setAutoScreenAsked(false); });
      }
      return;
    }
    wasPreparing.current = true;
    const timer = window.setInterval(() => {
      get<Batch>(`/api/roles/${reqId}/batch`).then((b) => {
        // Raise the hold in the SAME update that first reports preparation finished.
        // Done in an effect instead, it lands one render late — and that one render
        // is exactly the flash: a Screen button offered for a run already under way.
        if (!b.preparing?.active && b.preparing?.auto_screen) setKnowIfRunning(false);
        setBatch(b);
      }).catch(() => {});
    }, 2500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preparing, reqId]);

  // A hold that nothing releases is worse than the flash it prevents.
  //
  // The end-of-preparation transition above answers for a big drop, but it is not the
  // case that bit the pilot: a handful of résumés can be PREPARED before the upload's
  // own response comes back, so this tab never sees preparation at all — it sees a
  // finished batch, a run that has not started yet, and a live Screen button for work
  // already spoken for. Nothing arrives to say otherwise, because the batch poll only
  // runs while preparation does.
  //
  // So when the upload is done and nothing is preparing, ask. Give the worker a moment
  // first (it starts the run at the very end of preparation, and a probe that overtakes
  // it releases the hold just as the run appears), and cap it: three attempts, then the
  // button comes back. If the run really is not coming — the checklist gate closed
  // behind it, say — a recruiter who cannot press Screen is stuck.
  useEffect(() => {
    if (!autoScreenAsked || busy || preparing || job) return;
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      void get<{ job: ScreenJob | null }>(`/api/roles/${reqId}/screen/active`)
        .then((r) => {
          if (r.job && r.job.status === "running") {
            setJob(r.job);
            beginPolling(r.job.job_id);
            bumpPolling();   // the workspace banner and dashboard, now not in 20 s
            window.clearInterval(timer);
            setAutoScreenAsked(false);
            return;
          }
          if (attempts >= 3) { window.clearInterval(timer); setAutoScreenAsked(false); }
        })
        .catch(() => {
          // The next visit re-attaches, as it always did — don't strand the button.
          window.clearInterval(timer);
          setAutoScreenAsked(false);
        });
    }, 1500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoScreenAsked, busy, preparing, job, reqId]);

  async function startScreen() {
    setBusy(true);
    setError(null);
    try {
      const started = await post<ScreenJob>(`/api/roles/${reqId}/screen`, {});
      // The workspace banner and the dashboard poll slowly while nothing runs;
      // tell them something does, so the live pill appears now, not in 20 s.
      bumpPolling();
      setJob(started);
      beginPolling(started.job_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // Pause keeps the batch locked (the run intends to resume — the ordinary
  // screen click picks it up from cache); Cancel unlocks it for changes.
  async function pauseScreen() {
    if (!job) return;
    try {
      await post(`/api/screen/jobs/${job.job_id}/cancel`, { intent: "pause" });
      // the poll picks up the "paused" state; nothing else to do here
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function cancelScreen() {
    if (!job) return;
    const ok = window.confirm(
      "Cancel this run? The batch unlocks so résumés can be added or removed — "
      + "finished work stays cached, so the next screen only pays for new résumés.",
    );
    if (!ok) return;
    try {
      const res = await post<ScreenJob>(`/api/screen/jobs/${job.job_id}/cancel`, { intent: "cancel" });
      if (res.status && res.status !== "running") setJob(res); // a paused cancel finalizes inline
      void refreshRuns();
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // Live per-row status while a run is in flight.
  const running = job?.status === "running";
  const paused = job?.status === "paused";
  // One run per requisition (2026-07-19): live or paused = the batch is locked
  // for EVERYONE (no add/remove/clear/screen); only the run's initiator or a
  // Super Admin may pause/cancel/resume.
  // A MANUAL candidate lock (2026-07-20) also freezes the batch, alongside the
  // one-run-per-req screening lock. A read-only viewer (Legal/audit, 2026-07-21)
  // freezes everything too.
  const manualLock = role.locks?.candidates ?? null;
  const { readOnly, closed } = useWorkspace();
  const locked = running || paused || !!manualLock || readOnly || closed;
  const canControl = !job?.actor
    || job.actor.toLowerCase() === (me?.user ?? "").toLowerCase()
    || (me?.roles ?? []).includes("SuperAdmin");
  const lockNote = readOnly
    ? "Read-only view — the recruiting team manages résumés and screening."
    : closed
    ? "This requisition is closed — résumés and screening are frozen."
    : manualLock
    ? `The candidate list is locked (by ${manualLock.locked_by_name || nameOf(manualLock.locked_by)}). Unlock it to make changes.`
    : (running || paused)
      ? `Screening is ${paused ? "paused" : "in progress"}${job?.actor ? ` — started by ${nameOf(job.actor)}` : ""}. The batch is locked until the run finishes or is cancelled.`
      : undefined;
  const completedSet = useMemo(() => new Set(job?.completed ?? []), [job?.completed]);
  const plannedSet = useMemo(() => new Set(job?.planned ?? []), [job?.planned]);

  // The batch is FROZEN while a run is live or paused (2026-07-19) — the
  // server 409s too; cancel the run first. Removal needs no confirm otherwise.
  function removeFile(name: string) {
    if (locked) {
      setError(lockNote ?? "Screening is in progress — the batch is locked.");
      return;
    }
    if (prep?.live) {
      setError("Résumés are still being prepared — you can add more, but removing waits until that finishes.");
      return;
    }
    void refreshBatch(del<Batch>(`/api/roles/${reqId}/batch/${encodeURIComponent(name)}`),
                      "Removing that résumé…");
  }

  function clearBatch() {
    if (locked) {
      setError(lockNote ?? "Screening is in progress — the batch is locked.");
      return;
    }
    if (prep?.live) {
      setError("Résumés are still being prepared — clearing the batch waits until that finishes.");
      return;
    }
    void refreshBatch(del<Batch>(`/api/roles/${reqId}/batch`), "Clearing the batch…");
  }

  // checklist_ready folds in the P43 approval gate (a flagged checklist that
  // isn't signed off isn't ready). Résumés can be added anytime; only screening
  // is gated. screenBlocked is the plain-language reason for the tooltip.
  const hasBatch = (batch?.count ?? 0) > 0;
  // Screening needs BOTH a job description (the checklist is drafted from it) and
  // an approved checklist, plus at least one résumé — and no résumés still being
  // prepared (a run started now would silently miss them; the server 409s too).
  // "Upload & start screening" means a run may already be under way that this tab
  // has not heard about yet. Offering to start one in that gap is worse than a brief
  // quiet moment, so the call-to-action waits until we know — DERIVED, not set from
  // an effect, because an effect runs after the render that would have flashed it.
  // Bounded three ways: only when the upload actually asked for it, only until the
  // one check answers, and never once a job is in hand (2026-09-10).
  // Three holds, covering one uninterrupted stretch: `autoScreenAsked` from the click
  // until the server's answer, `preparing` while it works, `awaitingAutoScreen` for the
  // round trip that asks whether the run it promised has begun.
  const awaitingAutoScreen = !!prep?.auto_screen && !preparing && !job && !knowIfRunning;
  const autoScreenPending = autoScreenAsked || !!prep?.auto_screen;
  const canScreen = role.jd_present && role.checklist_ready && hasBatch
    && !preparing && !awaitingAutoScreen && !autoScreenAsked;
  // Could a run start once these files land? Deliberately EXCLUDES the batch and the
  // preparing state — the files being uploaded are what will satisfy those.
  const screenableAfterUpload = role.jd_present && role.checklist_ready;
  const screenBlockedReason = !role.jd_present
    ? "add a job description on the Overview tab first."
    : "the screening checklist needs to be approved first.";
  const screenBlocked = !role.jd_present
    ? "Add a job description on the Overview tab first."
    : !role.checklist_ready
      ? (role.checklist_block_reason
          ?? (role.rubric_version ? "This checklist is waiting on HR Head approval before it can screen."
                                  : "Publish a screening checklist first."))
      : !hasBatch
        ? "Add at least one résumé first."
        : preparing
          ? "Résumés are still being prepared — screening unlocks when they're ready."
          : autoScreenPending
            ? "Screening is already on its way for these résumés."
            : undefined;

  // Every row carries its status tag whenever the preflight loaded — the
  // headline summarizes, the rows show each file's fate.
  const fileStatus: Record<string, PreflightFileStatus> = {};
  for (const f of pre?.files ?? []) fileStatus[f.name] = f.status;

  // A run that processed this résumé — live now, or a just-ended (stopped/
  // failed) run whose completed set we still hold — is ground truth: it was
  // screened, its result cached. This matters after a partial stop: a stopped
  // run writes no run_meta, so the preflight can't know the 10-of-66 that
  // finished were screened; the completed set does. Otherwise the tag derives
  // from the preflight: never scored → not screened; updated résumé → re-screen;
  // already scored → screened, unless the setup changed and all redo.
  function tagFor(name: string): FileTag | undefined {
    if (completedSet.has(name)) return STATUS_TAG.screened;
    if (running) {
      if (plannedSet.size === 0 || plannedSet.has(name)) return STATUS_TAG.screening;
      return STATUS_TAG.not_screened; // added after this run started
    }
    const status = fileStatus[name];
    // A run is coming for this résumé without anyone clicking anything. Said BEFORE
    // the preflight check because the rows that matter most here are the ones the
    // preflight has not caught up with yet — a résumé that landed seconds ago, or a
    // slice the splitter has just cut out of a bulk file, which would otherwise sit
    // there with no tag at all while the recruiter waits for a sign of life.
    if (autoScreenPending && (!status || status === "new")) return PREPARING_TAG;
    if (!status) return undefined;
    if (status === "new") return STATUS_TAG.not_screened;
    if (status === "updated") return STATUS_TAG.re_screen;
    return pre?.reason === "config_changed" ? STATUS_TAG.re_screen : STATUS_TAG.screened;
  }

  // The design's split: the headline carries the ✨ ask ("Screen N résumés"),
  // the note carries the reason (what changed / what's cached / the no-op).
  // "Upload & start screening" has been chosen, so the answer to "what happens next?"
  // is not "you are not ready" — it is "nothing to click". Said here as well as on the
  // rows, because this is the block the eye goes to when the button will not respond.
  const headline = autoScreenPending
    ? "Screening starts on its own"
    : !canScreen
    ? "Not ready to screen yet"
    : pre?.reason === "nothing_changed"
      ? "Nothing changed since the last run"
      : `✨ Screen ${pre?.counts.total ?? batch?.count ?? 0} résumé${(pre?.counts.total ?? batch?.count ?? 0) === 1 ? "" : "s"}`;

  const subline = autoScreenPending
    ? (preparing
        ? "Preparing the résumés you added. The run starts by itself the moment they're ready — you can leave this page."
        : "These résumés were added with screening. The run starts by itself — you can leave this page.")
    : !canScreen
    ? (screenBlocked ?? "Approve a checklist and add at least one résumé first.")
    : pre?.reason === "nothing_changed"
      ? "Same checklist, rules and batch — screening again would be a no-op, so the shortlist is already current."
      : pre?.reason === "config_changed"
        ? `${changedLabel(pre.changed)} changed — all ${pre.counts.total} re-screen against the new version.`
        : pre?.reason === "files_changed"
          ? `${pre.counts.new + pre.counts.updated} file(s) new or updated will screen — ${pre.counts.cached} cached result${pre.counts.cached === 1 ? " is" : "s are"} reused free.` +
            (pre.counts.removed > 0
              ? ` ${pre.counts.removed} previously screened résumé${pre.counts.removed === 1 ? " is" : "s are"} no longer in the batch.`
              : "")
          : `Scores against the approved checklist and the ${role.location_display} hiring rules.`;

  // Bulk affordances (a 500-résumé batch shouldn't bury the Screen CTA under an
  // endless scroll, and its split groups should be collapsible).
  const total = batch?.count ?? 0;
  const bulk = total > BULK_LIST_THRESHOLD;
  const groups = batch?.groups ?? [];
  const hasGroups = groups.length > 0;
  const allCollapsed = hasGroups && groups.every((g) => collapsed[g.source]);
  const toggleGroup = (src: string) => setCollapsed((c) => ({ ...c, [src]: !c[src] }));
  const toggleAll = () => {
    const next = !allCollapsed;
    setCollapsed(Object.fromEntries(groups.map((g) => [g.source, next])));
  };

  const eta = etaLabel(job?.eta_seconds);
  const runEntries = runs?.runs ?? [];

  return (
    <>
      {error && <div style={{ marginBottom: 16 }}><Callout variant="warn" title="Heads up">{error}</Callout></div>}

      {/* checklist status — ALWAYS shown (P43): the ready state carries the
          version + count and the Edit-checklist shortcut; a blocked state
          carries the plain reason. Teal = the checklist's own hue; amber = waiting. */}
      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderLeft: `4px solid ${role.checklist_ready ? "var(--hue-teal-solid)" : "var(--hue-amber-solid)"}`, borderRadius: 14, padding: "12px 18px", marginBottom: 12, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ flex: "0 0 auto", width: 32, height: 32, borderRadius: 9, background: role.checklist_ready ? "var(--hue-teal-tint)" : "var(--hue-amber-tint)", color: role.checklist_ready ? "var(--hue-teal-ink)" : "var(--hue-amber-ink)", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
            <IconChecklist size={16} />
          </span>
          <div>
            <div style={{ fontSize: 13.5, fontWeight: 700 }}>
              {role.checklist_ready
                ? `Screening checklist v${role.rubric_version} · approved`
                : role.rubric_version ? "The checklist is waiting on approval" : "No approved checklist yet"}
            </div>
            <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
              {role.checklist_ready
                ? `${role.requirement_count} requirements to score against.`
                : (role.checklist_block_reason ?? "Draft and approve a checklist before screening.")}
            </div>
          </div>
        </div>
        <Button size="sm" variant="ghost" onClick={() => nav(tabPath(reqId, "checklist"))}>
          {role.checklist_ready ? "Edit checklist" : role.rubric_version ? "Open checklist" : "Create checklist"}
        </Button>
      </div>

      {/* One quiet line, not a callout: the fact matters, the box did not (item 8). */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--ink-soft)", margin: "0 4px 14px" }}>
        <span style={{ display: "inline-flex", color: "var(--hue-green-ink)" }}><IconShield size={15} /></span>
        Names, photos, contact details, age and gender are removed before anything is scored.
      </div>

      {/* batch manager */}
      <div
        onDragOver={(e) => { e.preventDefault(); if (!locked) setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); if (!locked && e.dataTransfer.files.length) onFiles(e.dataTransfer.files); }}
        style={{
          position: "relative",
          background: dragging ? "var(--brand-green-tint)" : "var(--white)",
          border: dragging ? "1.5px dashed var(--brand-green)" : "1px solid var(--border-subtle)",
          borderRadius: 16,
          overflow: "hidden",
          marginBottom: 18,
          transition: "background .14s var(--ease), border-color .14s var(--ease)",
        }}
      >
        {/* Busy overlay — covers only the UPLOAD itself now (a byte copy, seconds).
            The heavy work happens in the background and narrates itself through
            the preparing strip below, never through a blocking spinner. */}
        {busy && (
          <div style={{ position: "absolute", inset: 0, zIndex: 2, background: "rgba(255,255,255,0.72)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10 }}>
            <Spinner />
            <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink-soft)" }}>{busyLabel}</span>
          </div>
        )}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 20px", borderBottom: "1px solid var(--border-subtle)", flexWrap: "wrap", gap: 10 }}>
          <span style={{ fontSize: 13.5, fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 10 }}>
            <span style={{ display: "inline-flex", width: 30, height: 30, borderRadius: 9, alignItems: "center", justifyContent: "center", background: "var(--hue-orange-tint)", color: "var(--hue-orange-ink)" }}>
              <IconFile size={15} />
            </span>
            Candidate batch · {batchLoading ? "loading…" : `${batch?.count ?? 0} file${(batch?.count ?? 0) === 1 ? "" : "s"}`}
            {(busy || batchLoading) && <Spinner size={13} />}
          </span>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {!readOnly && (locksOn || manualLock) && <LockControl reqId={reqId} target="candidates" lock={manualLock} label="Candidates" onChange={reload} />}
            <input ref={fileInput} type="file" accept=".pdf,.docx" multiple style={{ display: "none" }} onChange={(e) => e.target.files && onFiles(e.target.files)} />
            {bulk && hasGroups && (
              <Button size="sm" variant="ghost" onClick={toggleAll} disabled={busy}>{allCollapsed ? "Expand all" : "Collapse all"}</Button>
            )}
            <button
              onClick={() => fileInput.current?.click()}
              disabled={busy || locked}
              title={lockNote}
              style={{ height: 30, padding: "0 13px", borderRadius: 999, border: "1.5px solid var(--brand-green)", background: "var(--white)", color: "var(--brand-green-deep)", fontSize: 12, fontWeight: 700, cursor: busy || locked ? "default" : "pointer", fontFamily: "inherit", whiteSpace: "nowrap", opacity: busy || locked ? 0.6 : 1 }}
            >
              ＋ Add résumés
            </button>
            {(batch?.count ?? 0) > 0 && (
              <a href={batchZipUrl(reqId)} download className="btn btn-secondary btn-sm"
                 style={{ display: "inline-flex", alignItems: "center", gap: 6, textDecoration: "none" }}>
                <IconDownload size={14} /> Download all (ZIP)
              </a>
            )}
            {(batch?.count ?? 0) > 0 && (
              <Button size="sm" variant="ghost" onClick={clearBatch} disabled={busy || locked || !!prep?.live} title={lockNote}>Clear batch</Button>
            )}
          </div>
        </div>

        {/* The background-preparation strip: files acknowledged, work visible,
            page leaveable. Candidates appear in the list below as they're ready. */}
        {prep && (preparing || prep.status === "error") && (
          <div role="status" style={{ display: "flex", gap: 12, alignItems: "center", padding: "12px 20px", borderBottom: "1px solid var(--border-subtle)", background: prep.status === "error" ? "var(--surface-warn)" : "var(--brand-green-tint)" }}>
            {prep.status !== "error" && <Spinner size={16} />}
            <div style={{ flex: "1 1 auto", minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: prep.status === "error" ? "var(--warn-ink)" : "var(--ink)" }}>
                {prep.status === "error"
                  ? "Preparing these résumés hit a problem"
                  : `Getting résumés ready — ${prep.processed} of ${prep.total} file${prep.total === 1 ? "" : "s"} done`}
              </div>
              <div style={{ fontSize: 12.5, color: "var(--ink-soft)", overflowWrap: "anywhere" }}>
                {prep.status === "error"
                  ? (prep.message ?? "Try again, or re-add the files.")
                  : <>
                      {(prep.added - prep.split_files + prep.split_resumes) > 0
                        && `${prep.added - prep.split_files + prep.split_resumes} candidate${(prep.added - prep.split_files + prep.split_resumes) === 1 ? "" : "s"} ready`}
                      {prep.duplicates > 0 && ` · ${prep.duplicates} duplicate${prep.duplicates === 1 ? "" : "s"} skipped`}
                      {prep.current && ` · working on ${prep.current}`}
                      {(prep.added > 0 || prep.current) ? " · " : ""}
                      {prep.auto_screen
                        ? "You can leave this page — screening starts on its own once these are ready."
                        : "You can leave this page — preparation keeps running on the server."}
                    </>}
              </div>
            </div>
            {prep.status === "error" ? (
              <Button size="sm" variant="secondary"
                onClick={() => refreshBatch(post<Batch>(`/api/roles/${reqId}/batch/prepare`, {}),
                                            "Starting preparation…")}>
                Try again
              </Button>
            ) : !readOnly && (
              <Button size="sm" variant="ghost" disabled={prep.stopping}
                onClick={() => setStopAsk(true)}>
                {prep.stopping ? "Stopping…" : "Stop"}
              </Button>
            )}
          </div>
        )}

        {batchLoading && (
          <div style={{ padding: "4px 20px 8px" }} aria-busy="true">
            <TableSkeleton rows={3} cols={[0.46, 0.14, 0.12]} />
          </div>
        )}
        {!batchLoading && batch && batch.count === 0 && !preparing && (
          <div style={{ padding: "26px 20px", textAlign: "center", fontSize: 13.5, color: "var(--ink-soft)" }}>
            Drag &amp; drop PDF/DOCX résumés here, or use “Add résumés”.
          </div>
        )}
        {/* A bulk batch scrolls WITHIN its own bounded area so the Screen CTA
            below (and the busy spinner) never slide off under a 500-row list. */}
        <div style={bulk ? { maxHeight: "min(56vh, 560px)", overflowY: "auto" } : undefined}>
          {/* Split bulk files (temporary iCIMS bridge): each source shows its
              derived résumés grouped under a provenance header. In bulk batches the
              header collapses its children (default expanded). */}
          {groups.map((g) => {
            const isCollapsed = bulk && collapsed[g.source];
            return (
              <div key={g.source}>
                <div
                  onClick={bulk ? () => toggleGroup(g.source) : undefined}
                  style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 20px", background: "var(--neutral-100, rgba(0,0,0,0.02))", borderBottom: "1px solid var(--neutral-150)", cursor: bulk ? "pointer" : "default" }}
                >
                  {bulk && (
                    <span aria-hidden="true" style={{ flex: "0 0 auto", display: "inline-flex", color: "var(--ink-soft)", transform: isCollapsed ? "none" : "rotate(90deg)", transition: "transform .14s var(--ease)" }}><IconChevronRight size={16} /></span>
                  )}
                  <span style={{ flex: "0 0 auto", width: 32, height: 32, borderRadius: 8, background: "var(--neutral-150)", display: "inline-flex", alignItems: "center", justifyContent: "center", color: "var(--ink-soft)" }}><IconFile size={16} /></span>
                  <span style={{ flex: "1 1 auto", fontSize: 13.5, fontWeight: 700, wordBreak: "break-all" }}>{g.source}</span>
                  <span style={{ flex: "0 0 auto", fontSize: 11, fontWeight: 700, padding: "3px 9px", borderRadius: 999, background: "var(--accent-blue-tint)", color: "var(--hue-blue-ink)", whiteSpace: "nowrap" }}>
                    {/* Counts PEOPLE PRESENT, not files written. Reporting the
                        written count against the table of contents read as a loss
                        ("split into 14 résumés of 15") when all 15 people were in
                        the batch and nothing had gone wrong — the 15th was simply
                        already here from another export. */}
                    split into {(g.present ?? g.count)} résumé{(g.present ?? g.count) !== 1 ? "s" : ""}
                    {g.expected && g.expected !== (g.present ?? g.count) ? ` of ${g.expected}` : ""}
                  </span>
                </div>
                {/* Account for every name the bulk file listed, so a smaller count
                    is never discovered later in Review. Two different reasons, kept
                    apart: a duplicate is expected and fine; an unseparated résumé
                    needs the recruiter to upload that candidate individually. */}
                {!!g.duplicates_dropped && (
                  <div role="status" style={{ display: "flex", gap: 8, padding: "8px 20px 8px 64px", fontSize: 12.5, color: "var(--ink-soft)", borderBottom: "1px solid var(--neutral-150)" }}>
                    <span aria-hidden="true">↺</span>
                    <span>
                      Includes {g.duplicates_dropped} candidate{g.duplicates_dropped === 1 ? "" : "s"} already
                      in this batch from another export — kept once, so nobody is counted twice.
                      {" "}Nobody from this file is missing.
                    </span>
                  </div>
                )}
                {!!g.shortfall && (
                  <div role="status" style={{ display: "flex", gap: 8, padding: "8px 20px 8px 64px", fontSize: 12.5, color: "var(--warn-ink)", background: "var(--surface-warn)", borderBottom: "1px solid var(--neutral-150)" }}>
                    <span aria-hidden="true">⚠</span>
                    <span>
                      <strong>{g.shortfall} of {g.expected} résumé{g.expected === 1 ? "" : "s"} couldn't be separated</strong>
                      {" "}from this file — their pages stay inside a neighbour's résumé, so they
                      won't appear as their own candidates.
                      {g.oversized?.length
                        ? ` ${g.oversized.length} file${g.oversized.length === 1 ? "" : "s"} below still hold${g.oversized.length === 1 ? "s" : ""} more than one résumé and won't be screened.`
                        : ""}
                      {" "}Upload those candidates individually to include them.
                    </span>
                  </div>
                )}
                {!isCollapsed && g.derived.map((name) => (
                  <FileRow key={name} name={name} indent busy={busy || locked || !!prep?.live}
                    tag={g.oversized?.includes(name) ? MULTI_RESUME_TAG
                         : name.includes(UNNAMED_MARKER) ? UNNAMED_TAG : tagFor(name)}
                    downloadUrl={batchFileUrl(reqId, name)}
                    onRemove={() => removeFile(name)} />
                ))}
              </div>
            );
          })}
          {/* Ordinary single-résumé files (not part of any split). */}
          {(batch?.files ?? [])
            .filter((f) => !groups.some((g) => g.derived.includes(f.name)))
            .map((f) => (
              <FileRow key={f.name} name={f.name} tag={tagFor(f.name)} busy={busy || locked || !!prep?.live}
                downloadUrl={batchFileUrl(reqId, f.name)}
                onRemove={() => removeFile(f.name)} />
            ))}
        </div>

        {/* "Import from folder" was REMOVED from the UI (2026-08-26, pilot feedback).
            It asks for a path on the SERVER's filesystem, which is meaningless to a
            recruiter typing a path from their own machine — so it could only ever
            appear broken. Drag-and-drop and the file picker cover the real need. The
            route stays for operator/CLI use; nothing here calls it. */}
        {note && <div style={{ padding: "0 20px 14px", fontSize: 12.5, color: "var(--brand-green-deep)" }}>{note}</div>}
      </div>

      {/* screen CTA / progress */}
      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, padding: 24 }}>
        {!job && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 20, flexWrap: "wrap" }}>
            <div style={{ minWidth: 0, flex: "1 1 380px" }}>
              <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 3 }}>{headline}</div>
              <div style={{ fontSize: 12.5, color: "var(--ink-soft)", lineHeight: 1.5 }}>{subline}</div>
              {/* P51: what exactly moved, with who changed it and why */}
              {pre?.reason === "config_changed" && (pre.details?.length ?? 0) > 0 && (
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 3 }}>
                  {pre.details.flatMap(driftLines).map((line, i) => (
                    <div key={i} style={{ fontSize: 12.5, color: line.startsWith("—") ? "var(--ink-muted)" : "var(--ink-soft)" }}>{line}</div>
                  ))}
                </div>
              )}
            </div>
            {/* ✨ only when the run would actually call the model (will_run);
                a no-op run offers the shortlist instead — never a fake POST */}
            {canScreen && pre?.reason === "nothing_changed" ? (
              <Button onClick={() => nav(tabPath(reqId, "review"))}>Proceed to review</Button>
            ) : pre === null || pre.will_run ? (
              <MagicButton onClick={startScreen} disabled={!canScreen || busy || locked} title={readOnly || closed ? lockNote : screenBlocked}>Screen résumés</MagicButton>
            ) : (
              <Button onClick={startScreen} disabled title={screenBlocked}>Screen résumés</Button>
            )}
          </div>
        )}

        {job && (
          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>
                {job.status === "running" && "Screening…"}
                {job.status === "done" && `Done — ${job.done} scored, cache reused where nothing changed.`}
                {job.status === "paused" && "Screening paused — the batch stays locked"}
                {job.status === "stopped" && "Screening cancelled"}
                {job.status === "error" && "Screening didn’t finish"}
              </div>
              {/* Only the run's initiator (or a Super Admin) controls it — the
                  server enforces the same rule with a 403. */}
              {job.status === "running" && (canControl ? (
                <span style={{ display: "inline-flex", gap: 8 }}>
                  <Button size="sm" variant="ghost" onClick={pauseScreen} title="Halt the run but keep the batch locked — resume picks up from cache.">Pause</Button>
                  <Button size="sm" variant="ghost" onClick={cancelScreen} title="End the run and unlock the batch — finished work stays cached.">Cancel run</Button>
                </span>
              ) : (
                <span style={{ fontSize: 12.5, color: "var(--ink-soft)", display: "inline-flex", alignItems: "center", gap: 5 }}>
                  Started by {job.actor ? <Person email={job.actor} people={people} avatar={false} /> : "another user"} — only they or a Super Admin can pause or cancel it.
                </span>
              ))}
              {job.status === "paused" && (canControl ? (
                <span style={{ display: "inline-flex", gap: 8 }}>
                  <MagicButton onClick={startScreen} disabled={busy} title="Picks up where it left off — finished résumés come from cache.">Resume screening</MagicButton>
                  <Button size="sm" variant="ghost" onClick={cancelScreen} title="End the run and unlock the batch — finished work stays cached.">Cancel run</Button>
                </span>
              ) : (
                <span style={{ fontSize: 12.5, color: "var(--ink-soft)", display: "inline-flex", alignItems: "center", gap: 5 }}>
                  Paused by {job.actor ? <Person email={job.actor} people={people} avatar={false} /> : "another user"} — only they or a Super Admin can resume or cancel it.
                </span>
              ))}
              {job.status === "done" && (
                <Button onClick={() => nav(tabPath(reqId, "review"))}>Review candidates ›</Button>
              )}
              {(job.status === "stopped" || job.status === "error") && (
                <MagicButton onClick={startScreen} disabled={busy}>Screen again</MagicButton>
              )}
            </div>
            {job.status !== "error" && (
              <div style={{ height: 10, borderRadius: 999, background: "var(--neutral-150)", overflow: "hidden", marginBottom: 8 }}>
                <div style={{ height: "100%", width: `${job.total ? Math.round((job.done / job.total) * 100) : (job.status === "done" ? 100 : 8)}%`, background: "var(--brand-green)", transition: "width .3s var(--ease)" }} />
              </div>
            )}
            <div style={{ fontSize: 13, color: job.status === "error" ? "var(--warn-ink)" : "var(--ink-soft)" }}>
              {job.status === "error" ? job.error : job.message}
              {job.status === "running" && eta ? ` · ${eta}` : ""}
            </div>
            {job.status === "running" && (
              <div style={{ fontSize: 12.5, color: "var(--ink-muted)", marginTop: 6 }}>
                Only new or changed items call the model — everything else is served from cache.
              </div>
            )}
          </div>
        )}
      </div>

      {/* batch quality — the whole-batch read, refreshed with every screen and
          with any change to the governed thresholds. Batch-level, not a
          candidate score (the tooltip says so), so it lives OUTSIDE the cards. */}
      {!job && role.batch_quality && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, padding: "13px 20px", marginTop: 18 }}>
          <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: ".04em", textTransform: "uppercase", color: "var(--ink-soft)" }}>Batch quality</span>
          <BatchQualityChip bq={role.batch_quality} />
          <span style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>{batchTip(role.batch_quality)}</span>
        </div>
      )}

      {/* run history — the persistent log of who screened, when and the outcome */}
      {runEntries.length > 0 && (
        <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, padding: "16px 20px", marginTop: 18 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: ".04em", textTransform: "uppercase", color: "var(--ink-soft)" }}>Screening history</span>
            {runEntries.length > 1 && (
              <Button size="sm" variant="ghost" onClick={() => setShowAllRuns((v) => !v)}>
                {showAllRuns ? "Hide earlier runs" : `Earlier runs (${runEntries.length - 1})`}
              </Button>
            )}
          </div>
          <div style={{ fontSize: 13.5 }}><RunLine entry={runEntries[0]} reqId={reqId} /></div>
          <div style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 4 }}>
            Everyone stays listed — gaps are shown on the card, never hidden.
          </div>
          {showAllRuns && runEntries.slice(1).map((entry, i) => (
            <div key={`${entry.ts}-${i}`} style={{ fontSize: 12.5, color: "var(--ink-soft)", marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--neutral-150)" }}>
              <RunLine entry={entry} reqId={reqId} />
            </div>
          ))}
          {runs?.note && <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 8 }}>{runs.note}</div>}
        </div>
      )}

      {/* What should dropping files do? Asked here because the answer changes what the
          recruiter does next: "and screen it" means they can walk away. */}
      {pending && (
        <OverlayRoot onDismiss={() => setPending(null)}>
          <div role="dialog" aria-label="Add résumés" className="rise"
               style={{ position: "relative", width: "min(520px, 92%)", background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, boxShadow: "var(--shadow-lg)", padding: "22px 26px" }}>
            <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>
              Add {pending.length} file{pending.length === 1 ? "" : "s"}
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.6, color: "var(--ink-soft)", marginBottom: 16 }}>
              Bulk files are split into individual résumés first. That runs on the server,
              so you can close this page either way.
              {!screenableAfterUpload && (
                <> Screening isn’t available yet — {screenBlockedReason}</>
              )}
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <MagicButton onClick={() => startUpload(pending, true)} disabled={!screenableAfterUpload}
                title={screenableAfterUpload ? undefined : screenBlockedReason}>
                Upload and screen when ready
              </MagicButton>
              <Button variant="secondary" onClick={() => startUpload(pending, false)}>
                Upload only
              </Button>
              <Button variant="ghost" onClick={() => setPending(null)}>Cancel</Button>
            </div>
          </div>
        </OverlayRoot>
      )}

      {stopAsk && (
        <ConfirmModal
          spec={{
            title: "Stop preparing résumés?",
            reason: `${prep?.processed ?? 0} of ${prep?.total ?? 0} file(s) have been prepared. `
              + "Those candidates stay in the batch; anything still queued is discarded.",
            question: "You can add the remaining files again at any time.",
            cta: "Stop preparing",
            canProceed: true,
            onProceed: () => void stopPreparing(),
          }}
          onCancel={() => setStopAsk(false)}
        />
      )}

    </>
  );
}

export default function CandidatesTab() {
  const { role, reload } = useWorkspace();
  return (
    <Page max={820}>
      <CandidatesPanel role={role} reload={reload} />
    </Page>
  );
}
